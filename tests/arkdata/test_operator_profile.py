import logging

import pytest

from arkdata.calculations.attack_targets import Targets
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_profile import (
    DEFAULT_STACK_WINDOW,
    MAX_ATTACK_SPEED,
    MIN_ATTACK_SPEED,
    Damage,
    Hit,
    UnitStats,
    apply_modifiers,
    attack_hits,
    build_operator_profile,
    build_summon_profile,
    build_summon_profiles,
    damage_per_second,
    damage_sum,
    fit_skill_level,
    has_potential_buffs,
    has_trust_buffs,
    healing_sum,
    hit_damage,
    overall_status,
    strike_damage,
    subtract,
    summon_ids,
    trait_values,
)
from arkdata.calculations.skills import DamagePart, Penetration, StatModifiers
from factories import (
    load_operator_fixture,
    make_operator_value,
    make_skill_entry,
    make_skill_level,
    make_talent_candidate,
)

STATS = {'atk': 600, 'def': 100, 'maxHp': 2000, 'magicResistance': 10, 'attackSpeed': 100, 'baseAttackTime': 1.5,
         'blockCnt': 2}
ONE = Targets()
AOE = Targets(3, 3.0, True, 'AoE (no target limit)')


def _unit(atk=600.0, attack_interval=1.0):
    return UnitStats(atk, 100.0, 2000.0, 10.0, 100.0, attack_interval, 1.0)


def _skill(description, blackboard=None, **kwargs):
    # One skill with a single level, used at skill level 1.
    return make_skill_entry(make_skill_level(description, blackboard, **kwargs), skill_id=f'sk_{len(description)}')


def _profile(*skills, talents=(), skill_level=1, masteries=(), **kwargs):
    # make_operator_value at phase 2 level 90, potential 0, trust 0: ATK 640, DEF 100, HP 1000, attack interval 1.0 s.
    value = make_operator_value(skills=skills, talents=talents, **kwargs)
    return build_operator_profile(value, 2, 90, 0, 0, skill_level, masteries)


def _set_block(value, block):
    for frame in value['data']['phases'][2]['attributesKeyFrames']:
        frame['data']['blockCnt'] = block


# apply_modifiers

def test_apply_modifiers_without_changes():
    assert apply_modifiers(STATS, StatModifiers()) == UnitStats(600, 100, 2000, 10, 100, 1.5, 2)


def test_apply_modifiers_adds_flat_then_multiplies_percent():
    stats = apply_modifiers(STATS, StatModifiers(atk_percent=0.5, atk_flat=100, def_percent=1.0, def_flat=50,
                                                 hp_percent=0.1, hp_flat=500, res_percent=0.5, res_flat=10, block=1))
    # ATK (600 + 100) × 1.5, DEF (100 + 50) × 2, HP (2000 + 500) × 1.1, RES (10 + 10) × 1.5
    assert (stats.atk, stats.defense, stats.max_hp, stats.magic_resistance, stats.block) == (1050, 300, 2750, 30, 3)


def test_apply_modifiers_attack_interval():
    # (1.5 - 0.3) / (150 / 100)
    stats = apply_modifiers(STATS, StatModifiers(attack_speed=50, attack_interval_change=-0.3))
    assert stats.attack_speed == 150
    assert stats.attack_interval == pytest.approx(0.8)


@pytest.mark.parametrize('attack_speed, expected', [(-200, MIN_ATTACK_SPEED), (1000, MAX_ATTACK_SPEED)])
def test_apply_modifiers_keeps_aspd_in_limits(attack_speed, expected):
    stats = apply_modifiers(STATS, StatModifiers(attack_speed=attack_speed))
    assert stats.attack_speed == expected
    assert stats.attack_interval == pytest.approx(1.5 / (expected / 100))


def test_apply_modifiers_never_goes_below_zero():
    stats = apply_modifiers(STATS, StatModifiers(atk_percent=-2, def_flat=-500, hp_percent=-1.5, res_flat=-50, block=-5,
                                                 attack_interval_change=-3))
    assert (stats.atk, stats.defense, stats.max_hp, stats.magic_resistance, stats.block, stats.attack_interval) == (
        0, 0, 0, 0, 0, 0)


# damage_per_second, strike_damage and sums

def test_damage_per_second_divides_by_the_interval_and_shares_over_targets():
    [damage] = damage_per_second(_unit(attack_interval=1.5), [DamagePart(DamageType.ARTS, 2.0, 1.0, AOE)])
    assert damage == Damage(DamageType.ARTS, pytest.approx(800), pytest.approx(2400))


def test_damage_per_second_per_second_parts_ignore_the_interval():
    [damage] = damage_per_second(_unit(attack_interval=2.0), [DamagePart(DamageType.ARTS, 0.5, 1.0, ONE, True)])
    assert damage.per_target == 300


def test_damage_per_second_hits_and_multiplier():
    parts = [DamagePart(DamageType.PHYSICAL, 1.0, 1.0, ONE), DamagePart(DamageType.PHYSICAL, 1.0, 3.0, ONE)]
    # One hit uses the archetype's 2 hits; 3 hits stay 3. Both × the 1.1 talent multiplier.
    damage = damage_per_second(_unit(), parts, damage_multiplier=1.1, basic_hits=2)
    assert [part.per_target for part in damage] == [pytest.approx(1320), pytest.approx(1980)]


def test_damage_per_second_healing_isnt_multiplied():
    [heal] = damage_per_second(_unit(), [DamagePart(DamageType.HEAL, 1.0, 1.0, ONE)], damage_multiplier=1.5)
    assert heal.per_target == 600


def test_damage_per_second_without_an_attack_interval():
    assert damage_per_second(_unit(attack_interval=0), [DamagePart(DamageType.ARTS)])[0].per_target == 0


def test_strike_damage_is_one_attack():
    [strike] = strike_damage(_unit(attack_interval=2.0), [DamagePart(DamageType.PHYSICAL, 3.0, 1.0, AOE)])
    assert (strike.per_target, strike.total) == (1800, 5400)



# attack_hits and Hit

PHYSICAL_PART = DamagePart(DamageType.PHYSICAL, 2.0, 1.0, ONE)


def test_attack_hits_keep_strikes_and_multipliers_apart():
    # A Flinger-like (1, 0.5) attack at 200%: each strike's damage before DEF has the ATK multiplier (1.5); the damage
    # multiplier (1.2) comes after.
    hits = attack_hits(_unit(), [PHYSICAL_PART], atk_multiplier=1.5, damage_multiplier=1.2, strikes=(1.0, 0.5))
    assert hits == [Hit(DamageType.PHYSICAL, 600 * 2 * 1.5, 1.2, 1.0, 1.0, ONE),
                    Hit(DamageType.PHYSICAL, 600 * 2 * 0.5 * 1.5, 1.2, 1.0, 1.0, ONE)]
    # 1800 × 1.2 + 900 × 1.2 = 3240, the same as 600 × 2 × 1.5 hits × 1.5 × 1.2.
    assert sum(hit.per_target for hit in hits) == pytest.approx(3240)


def test_attack_hits_count_equal_strikes_together():
    [hit] = attack_hits(_unit(), [PHYSICAL_PART], strikes=(1.0, 1.0))
    assert (hit.damage, hit.count) == (1200, 2)


def test_attack_hits_parts_with_their_own_hits_ignore_strikes():
    [hit] = attack_hits(_unit(), [DamagePart(DamageType.PHYSICAL, 1.1, 5, ONE)], strikes=(1.0, 1.0))
    assert (hit.damage, hit.count) == (pytest.approx(660), 5)


def test_attack_hits_intervals():
    unit = _unit(attack_interval=2.0)
    assert attack_hits(unit, [PHYSICAL_PART])[0].interval == 2.0
    assert attack_hits(unit, [PHYSICAL_PART], per_strike=True)[0].interval == 1.0
    assert attack_hits(unit, [DamagePart(DamageType.ARTS, 1.4, 1.0, AOE, per_second=True)])[0].interval == 1.0


def test_attack_hits_healing_isnt_multiplied():
    [heal] = attack_hits(_unit(), [DamagePart(DamageType.HEAL, 1.0, 1.0, ONE)], 1.5, 1.2)
    assert (heal.damage, heal.amplifier) == (600, 1.0)


def test_hit_figures():
    hit = Hit(DamageType.ARTS, 500.0, 1.2, 2.0, 1.5, AOE)
    # 500 × 1.2 × 2 / 1.5 = 800 per target, on 3 enemies.
    assert (hit.per_target, hit.total) == (pytest.approx(800), pytest.approx(2400))
    assert Hit(DamageType.ARTS, 500.0, 1.0, 1.0, 0.0, ONE).per_target == 0
    assert hit_damage([hit]) == [Damage(DamageType.ARTS, hit.per_target, hit.total)]


def test_subtract_by_type():
    strike = [Damage(DamageType.PHYSICAL, 1800, 1800), Damage(DamageType.ARTS, 300, 900)]
    normal = [Damage(DamageType.PHYSICAL, 600, 600), Damage(DamageType.TRUE, 100, 100)]
    assert subtract(strike, normal) == [Damage(DamageType.PHYSICAL, 1200, 1200), Damage(DamageType.ARTS, 300, 900)]


def test_damage_and_healing_sums():
    damage = [Damage(DamageType.PHYSICAL, 100, 300), Damage(DamageType.ARTS, 50, 50), Damage(DamageType.HEAL, 20, 40)]
    assert damage_sum(damage) == (150, 350)
    assert healing_sum(damage) == (20, 40)


@pytest.mark.parametrize('statuses, expected', [
    ([], 'full'), (['full', 'full'], 'full'), (['none', 'none'], 'none'), (['full', 'none'], 'partial'),
    (['partial'], 'partial'),
])
def test_overall_status(statuses, expected):
    assert overall_status(statuses) == expected


# Helpers for skills, traits and summons

@pytest.mark.parametrize('skill_level, mastery, level_count, expected', [
    (7, 3, 10, (7, 3, False)),
    (4, 0, 10, (4, 0, False)),
    (7, 2, 7, (7, 0, True)),
    (7, 3, 1, (1, 0, True)),
    (3, 0, 1, (1, 0, True)),
    (1, 0, 1, (1, 0, False)),
    # Masteries only count at skill level 7.
    (5, 2, 10, (5, 0, False)),
])
def test_fit_skill_level(skill_level, mastery, level_count, expected):
    assert fit_skill_level(skill_level, mastery, level_count) == expected


def test_trait_values_use_the_unlocked_version():
    data = {'trait': {'candidates': [
        make_talent_candidate(blackboard={'max_target': 3}, phase='PHASE_0'),
        make_talent_candidate(blackboard={'max_target': 4}, phase='PHASE_2'),
    ]}}
    assert trait_values(data, 1, 1, 0) == {'max_target': 3}
    assert trait_values(data, 2, 1, 0) == {'max_target': 4}
    assert trait_values({'trait': None}, 2, 1, 0) == {}


def test_summon_ids_from_tokens_and_skills():
    value = make_operator_value(skills=[_skill('a'), _skill('bb'), _skill('ccc')], tokens=['tok_a', 'tok_c'],
                                override_tokens={2: 'tok_b', 3: 'tok_a'})
    assert summon_ids(value) == {'tok_a': 3, 'tok_c': None, 'tok_b': 2}
    assert summon_ids({'data': {}}) == {}


def test_buff_checks():
    assert has_potential_buffs(make_operator_value()['data'])
    assert has_trust_buffs(make_operator_value()['data'])
    assert not has_potential_buffs({'potentialRanks': [{'buff': None}]})
    assert not has_potential_buffs({})
    assert not has_trust_buffs({'favorKeyFrames': None})
    assert not has_trust_buffs({'favorKeyFrames': [{'level': 50, 'data': {'atk': 0, 'stunImmune': True}}]})


# build_operator_profile: no skill

def test_base_profile():
    profile = _profile()
    assert (profile.operator_id, profile.name, profile.damage_type) == ('char_test', 'Test Operator', DamageType.PHYSICAL)
    assert profile.base_stats == UnitStats(640, 100, 1000, 0, 100, 1.0, 1)
    assert profile.base_damage == [Damage(DamageType.PHYSICAL, 640, 640)]
    summary = profile.summary()
    assert (summary.min_dps, summary.avg_dps, summary.max_dps) == (640, 640, 640)
    assert (summary.avg_dps_skill, summary.max_dps_skill, summary.max_dps_total_skill) == ('', '', '')
    assert (summary.min_def, summary.max_def, summary.min_hp, summary.max_hp) == (100, 100, 1000, 1000)
    assert summary.min_hps is None and summary.status == 'full'
    assert profile.skills == [] and profile.summons == [] and profile.assumed == []


def test_archetypes_without_a_basic_attack_only_attack_during_skills():
    profile = _profile(_skill('ATK +{atk:0%}', {'atk': 0.6}, sp_cost=20, duration=20), sub_profession='phalanx')
    summary = profile.summary()
    assert summary.min_dps == 0
    # 640 × 1.6 = 1024 Arts damage per second while the skill is active, half the time.
    assert profile.skills[0].damage == [Damage(DamageType.ARTS, 1024, 1024)]
    assert summary.avg_dps == pytest.approx(512)


def test_aoe_archetype_total():
    profile = _profile(sub_profession='splashcaster')
    assert profile.summary().min_dps_total == 640 * 3


def test_profile_uses_potential_and_trust():
    # Potential 5 adds ATK +20 (rank 2) and trust 100 adds ATK +50 (make_operator_data).
    profile = build_operator_profile(make_operator_value(), 2, 90, 5, 100, 1)
    assert profile.base_stats.atk == 710


def test_profile_rejects_bad_levels():
    with pytest.raises(ValueError):
        build_operator_profile(make_operator_value(), 2, 91, 0, 0, 1)


# build_operator_profile: talents

def test_applied_talents_change_stats_and_damage():
    profile = _profile(talents=[
        [make_talent_candidate('ATK +10%', {'atk': 0.1})],
        [make_talent_candidate('When attacking aerial units, ATK +20%', {'atk': 0.2})],
        [make_talent_candidate('When attacking, has a 20% chance to deal 150% damage', {'prob': 0.2, 'atk_scale': 1.5})],
    ])
    # ATK 640 × 1.1 = 704 (the aerial talent isn't applied), × 1.1 average damage = 774.4.
    assert profile.base_stats.atk == pytest.approx(704)
    assert profile.base_damage[0].per_target == pytest.approx(774.4)
    assert len(profile.talents) == 3


def test_stacking_talents_use_the_default_window_then_each_rotation():
    stacking = [make_talent_candidate('Each attack gives ATK +1%, stacking up to 10 times', {'atk': 0.01,
                                                                                              'max_stack_cnt': 10})]
    timed = _skill('ATK +{atk:0%}', {'atk': 0.0}, sp_cost=10, duration=10)
    profile = _profile(timed, talents=[stacking])
    # 1 stack per 1 s attack, full at 10 s: over 60 s, (10 × 5 + 50 × 10) / 60 stacks.
    assert profile.talents[0].factor == pytest.approx(550 / 60)
    assert profile.assumed == [f'stacking talents averaged over {DEFAULT_STACK_WINDOW:g} s with no skill']
    # Over the skill's 20 s cycle: (10 × 5 + 10 × 10) / 20 = 7.5 stacks.
    [skill] = profile.skills
    assert skill.window == 20
    assert skill.talents[0].factor == pytest.approx(7.5)
    assert skill.stats.atk == pytest.approx(640 * 1.075)


# build_operator_profile: skills

def test_timed_skill():
    profile = _profile(_skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=40, duration=20, name='Boost'))
    [skill] = profile.skills
    assert skill.dps == 960 and skill.rotation.cycle_time == 60
    # (960 × 20 + 640 × 40) / 60
    assert skill.average_dps == pytest.approx(746.6667)
    assert skill.counts_for_max and skill.window == 60
    summary = profile.summary()
    assert (summary.max_dps, summary.max_dps_skill, summary.avg_dps_skill) == (960, 'Boost', 'Boost')


def test_unlimited_skill_beats_a_timed_one():
    profile = _profile(_skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=40, duration=20, name='Boost'),
                       _skill('ATK +{atk:0%}\nUnlimited duration', {'atk': 1.0}, sp_cost=40, duration=-1, name='Forever'))
    assert profile.skills[1].average_dps == 1280
    assert profile.skills[1].window == DEFAULT_STACK_WINDOW
    summary = profile.summary()
    assert (summary.avg_dps, summary.avg_dps_skill, summary.max_dps, summary.max_dps_skill) == (1280, 'Forever', 1280,
                                                                                                'Forever')


def test_instant_skill_replacing_an_attack_adds_the_difference():
    profile = _profile(_skill('The next attack deals {atk_scale:0%} ATK', {'atk_scale': 3.0},
                              sp_type='INCREASE_WHEN_ATTACK', sp_cost=4, duration=0))
    [skill] = profile.skills
    # Strike 640 × 3 = 1920, minus the 640 attack it replaces. Cycle: 4 attacks to charge plus the strike = 5 s.
    assert skill.burst == [Damage(DamageType.PHYSICAL, 1280, 1280)]
    assert skill.average_dps == pytest.approx(640 + 1280 / 5)
    assert skill.dps == 640 and not skill.counts_for_max
    assert profile.summary().max_dps == 640


def test_instant_skill_boosting_the_next_attack():
    profile = _profile(_skill('The next attack has ATK +{atk:0%}', {'atk': 1.0}, sp_cost=10, duration=0))
    assert profile.skills[0].burst == [Damage(DamageType.PHYSICAL, 640, 640)]


def test_instant_extra_strike_on_nearby_enemies():
    profile = _profile(_skill('Immediately deals {atk_scale:0%} ATK to nearby enemies', {'atk_scale': 3.0}, sp_cost=10,
                              duration=0))
    [skill] = profile.skills
    assert skill.burst == [Damage(DamageType.PHYSICAL, 1920, 5760)]
    assert skill.average_dps == pytest.approx(640 + 192)
    assert skill.average_dps_total == pytest.approx(640 + 576)


def test_swordmaster_strike_skills_hit_once():
    # Ch'en's Sheathed Strike: the trait's double hit is for normal attacks only.
    profile = _profile(_skill('On the next attack, strike the enemy using the sheath, dealing {atk_scale:0%} ATK as '
                              'Physical damage', {'atk_scale': 2.6}, sp_type='INCREASE_WHEN_ATTACK', sp_cost=5, duration=0),
                       _skill('Deals {atk_scale:0%} ATK as Physical damage to up to {max_target} enemies',
                              {'atk_scale': 4.4, 'max_target': 6}, sp_type='INCREASE_WHEN_ATTACK', sp_cost=10,
                              duration=0),
                       sub_profession='sword')
    s1, s2 = profile.skills
    # Normal attacks hit twice: 1280 DPS. S1's strike is one 260% hit (1664), replacing a 1280 attack: +384.
    assert profile.base_damage[0].per_target == 1280
    assert s1.burst[0].per_target == pytest.approx(640 * 2.6 - 1280)
    # S2's extra strike: one 440% hit on each of up to 6 enemies.
    assert (s2.burst[0].per_target, s2.burst[0].total) == (pytest.approx(640 * 4.4), pytest.approx(640 * 4.4 * 6))


def test_swordmaster_boosted_next_attack_and_timed_skills_keep_the_double_hit():
    profile = _profile(_skill('The next attack has ATK +{atk:0%}', {'atk': 1.0}, sp_cost=10, duration=0),
                       _skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=20, duration=20), sub_profession='sword')
    boosted, timed = profile.skills
    # A boosted next attack is still a normal attack (2 hits of 1280 instead of 640): +1280.
    assert boosted.burst[0].per_target == 1280
    assert timed.dps == 960 * 2


def test_instant_skill_without_damage_adds_nothing():
    profile = _profile(_skill('Blocks the next source of damage; DEF +{def:0%}', {'def': 1.0},
                              sp_type='INCREASE_WHEN_TAKEN_DAMAGE', sp_cost=10, duration=0))
    [skill] = profile.skills
    assert skill.burst == []
    assert skill.average_dps == 640 and skill.full_block_average_dps == 640


def test_skill_that_stops_attacking_keeps_its_damage_every_second():
    profile = _profile(_skill('Stops attacking; deals {atk_scale:0%} ATK as Arts damage to nearby enemies every second',
                              {'atk_scale': 0.5}, sp_cost=10, duration=10))
    [skill] = profile.skills
    assert skill.damage == [Damage(DamageType.ARTS, 320, 960)]


def test_damage_every_second_adds_to_the_attack():
    profile = _profile(_skill('ATK +{atk:0%}; deals {atk_scale:0%} ATK as Arts damage every second',
                              {'atk': 0.5, 'atk_scale': 0.5}, sp_cost=10, duration=10))
    assert profile.skills[0].damage == [Damage(DamageType.PHYSICAL, 960, 960), Damage(DamageType.ARTS, 480, 480)]
    assert profile.skills[0].dps == 1440


def test_ammo_skill_lasts_its_attacks():
    profile = _profile(_skill('ATK +{atk:0%}', {'atk': 1.0, 'trigger_time': 8}, sp_cost=20, duration=0,
                              duration_type='AMMO'))
    [skill] = profile.skills
    assert skill.rotation.duration == 8
    # (1280 × 8 + 640 × 20) / 28
    assert skill.average_dps == pytest.approx(822.857, rel=1e-4)
    assert skill.counts_for_max


def test_deployment_skill_counts_for_max_not_average():
    profile = _profile(_skill('After deployment, ATK +{atk:0%}', {'atk': 1.0}, skill_type='PASSIVE', sp_type='8',
                              sp_cost=0, duration=10, name='Deploy'))
    summary = profile.summary()
    assert (summary.max_dps, summary.max_dps_skill) == (1280, 'Deploy')
    assert (summary.avg_dps, summary.avg_dps_skill) == (640, '')


def test_skill_started_by_something_else_has_no_average():
    profile = _profile(_skill('Spends Coins: ATK +{atk:0%}', {'atk': 1.0}, skill_type='PASSIVE', sp_type='8', sp_cost=0,
                              duration=10))
    [skill] = profile.skills
    assert skill.average_dps is None and not skill.counts_for_max
    assert profile.summary().max_dps == 640


def test_once_per_battle_skill_is_left_out():
    profile = _profile(_skill('ATK +{atk:0%}. This skill can only be used once per battle', {'atk': 1.0}, sp_cost=10,
                              duration=10))
    [skill] = profile.skills
    assert skill.once_per_battle and not skill.counts_for_max
    assert skill.average_dps is None
    assert 'can only be used once per battle, so left out of average and max DPS' in skill.skill.assumed
    assert profile.summary().max_dps == 640


def test_sp_when_hit_full_block_average():
    value = make_operator_value(skills=[_skill('ATK +{atk:0%}', {'atk': 1.0}, sp_type='INCREASE_WHEN_TAKEN_DAMAGE',
                                               sp_cost=10, duration=10)])
    _set_block(value, 3)
    [skill] = build_operator_profile(value, 2, 90, 0, 0, 1).skills
    # 1 attacker every 3 s: 30 s to charge. A full block (3 attackers): 10 s.
    assert skill.average_dps == pytest.approx((1280 * 10 + 640 * 30) / 40)
    assert skill.full_block_average_dps == pytest.approx((1280 * 10 + 640 * 10) / 20)
    assert skill.full_block_average_dps_total == skill.full_block_average_dps


def test_healer_profile():
    profile = _profile(_skill('Heals allies with {heal_scale:0%} ATK', {'heal_scale': 1.5}, sp_cost=20, duration=10),
                       profession='MEDIC', sub_profession='physician')
    assert profile.damage_type == DamageType.HEAL
    summary = profile.summary()
    assert (summary.min_dps, summary.max_dps) == (0, 0)
    # 640 HPS normally, 960 during the skill: (960 × 10 + 640 × 20) / 30 on average.
    assert (summary.min_hps, summary.max_hps) == (640, 960)
    assert summary.avg_hps == pytest.approx(746.6667)


def test_skill_levels_and_masteries_by_skill():
    levels = [make_skill_level('ATK +{atk:0%}', {'atk': index / 10}, sp_cost=40, duration=20) for index in range(10)]
    profile = _profile(make_skill_entry(*levels, skill_id='sk_1'), make_skill_entry(*levels, skill_id='sk_2'),
                       skill_level=7, masteries=(0, 2))
    assert [skill.skill.modifiers.atk_percent for skill in profile.skills] == [0.6, 0.8]
    assert [skill.skill.mastery for skill in profile.skills] == [0, 2]


def test_skill_without_the_wanted_level_uses_its_last():
    profile = _profile(_skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=40, duration=20), skill_level=7, masteries=(3,))
    [skill] = profile.skills
    assert (skill.skill.level, skill.skill.mastery) == (1, 0)
    assert skill.skill.assumed == ['skill level 7 mastery 3 not available; used level 1 mastery 0']


def test_summary_stats_and_status():
    profile = _profile(_skill('DEF +{def:0%}, RES +{magic_resistance}, Max HP +{max_hp:0%}',
                              {'def': 1.0, 'magic_resistance': 20, 'max_hp': 0.5, 'mystery': 1}, sp_cost=10, duration=10))
    summary = profile.summary()
    assert (summary.max_def, summary.max_res, summary.max_hp) == (200, 20, 1500)
    assert summary.status == 'partial'


# Summons

def _summon(**data_kwargs):
    value = make_operator_value(operator_id='token_test', profession='TOKEN', sub_profession='notchar1', **data_kwargs)
    value['data']['potentialRanks'] = []
    value['data']['favorKeyFrames'] = None
    return value


def test_summon_without_bonuses_ignores_the_owners_potential_and_trust():
    profile = build_summon_profile(_summon(), 2, 90, 5, 100, 7, owner_id='char_owner')
    assert (profile.potential, profile.trust, profile.owner_id) == (0, 0, 'char_owner')
    assert profile.base_stats.atk == 640
    assert profile.assumed == ["no potential bonuses listed, so the owner's potential isn't used",
                               "no trust bonuses listed, so the owner's trust isn't used"]


def test_summon_with_bonuses_uses_the_owners_potential_and_trust():
    profile = build_summon_profile(make_operator_value(operator_id='token_test'), 2, 90, 5, 100, 7)
    # ATK +20 at potential 5 and +50 at trust 100.
    assert (profile.potential, profile.trust, profile.base_stats.atk) == (5, 100, 710)
    assert profile.assumed == []


def test_summon_at_a_phase_or_level_it_doesnt_have():
    summon = _summon(phase_ranges=[(50, {}, {'atk': 390}), (70, {'atk': 400}, {'atk': 538})])
    profile = build_summon_profile(summon, 2, 90, 0, 0, 7)
    assert (profile.phase, profile.level, profile.base_stats.atk) == (1, 70, 538)
    assert profile.assumed == ["owner's phase 2 level 90 not available; used phase 1 level 70"]


def test_summon_skills_use_the_owners_masteries_by_slot():
    levels = [make_skill_level('ATK +{atk:0%}', {'atk': index / 10}, sp_cost=40, duration=20) for index in range(10)]
    summon = _summon(skills=[make_skill_entry(*levels, skill_id='sktok_1'), make_skill_entry(*levels, skill_id='sktok_2')])
    profile = build_summon_profile(summon, 2, 90, 0, 0, 7, masteries=(1, 3))
    assert [skill.skill.mastery for skill in profile.skills] == [1, 3]
    assert profile.assumed[-1].startswith('its skills are counted whenever')


def test_summon_with_null_skills():
    summon = _summon()
    summon['skills'] = [None, None, None]
    assert build_summon_profile(summon, 2, 90, 0, 0, 7).skills == []


def test_build_summon_profiles_skips_missing_summons(caplog):
    owner = make_operator_value(skills=[_skill('a'), _skill('bb')], tokens=['token_found', 'token_missing'],
                                override_tokens={2: 'token_found'})
    with caplog.at_level(logging.WARNING, logger='arkdata.calculations.operator_profile'):
        [profile] = build_summon_profiles(owner, {'token_found': _summon()}, 2, 90, 0, 0, 7, (0, 2))
    assert (profile.operator_id, profile.owner_id) == ('token_found', 'char_test')
    assert 'Summon token_missing of char_test is not in the HellaAPI data; skipped' in caplog.text


def test_owner_profile_lists_its_summons():
    profile = _profile(tokens=['token_a'])
    assert profile.summons == ['token_a']


# Real operators (trimmed HellaAPI data in tests/fixtures/operators), at max level, potential 6, trust 100, M3.

def _real(name, level=90):
    return build_operator_profile(load_operator_fixture(name), 2, level, 5, 100, 7, (3, 3, 3))


def test_real_exusiai():
    profile = _real('exusiai')
    # ATK 540 + 27 (potential) + 90 (trust) = 657, × 1.08 (talent) = 709.56.
    # ASPD 100 + 15 (talent): attack interval 1 / 1.15 = 0.8696 s, so 709.56 / 0.8696 = 815.99 DPS.
    assert profile.base_stats.atk == pytest.approx(709.56)
    assert profile.base_stats.attack_interval == pytest.approx(1 / 1.15)
    assert profile.summary().min_dps == pytest.approx(815.994)
    s1, s2, s3 = profile.skills
    # S1 M3: next attack shoots 3 × 145%: 3086.6 - 709.56 = 2377.0 extra, every 4 attacks + 1 = 4.348 s.
    # 815.99 + 2377.0 / 4.348 = 1362.7.
    assert s1.burst[0].per_target == pytest.approx(709.56 * 1.45 * 3 - 709.56)
    assert s1.average_dps == pytest.approx(1362.71, rel=1e-5)
    # S2 M3: 4 × 125% per attack = 4079.97 DPS for 15 s of a 50 s cycle: (4079.97 × 15 + 815.99 × 35) / 50 = 1795.19.
    assert s2.dps == pytest.approx(4079.97, rel=1e-5)
    assert s2.average_dps == pytest.approx(1795.19, rel=1e-5)
    # S3 M3: 5 × 110%, interval (1 - 0.11) / 1.15 = 0.7739 s: 709.56 × 5.5 / 0.7739 = 5042.66 DPS,
    # 15 s of a 45 s cycle: (5042.66 × 15 + 815.99 × 30) / 45 = 2224.88.
    assert s3.dps == pytest.approx(5042.66, rel=1e-5)
    assert s3.average_dps == pytest.approx(2224.88, rel=1e-5)
    summary = profile.summary()
    assert (summary.max_dps_skill, summary.avg_dps_skill, summary.status) == ('Overloading Mode', 'Overloading Mode',
                                                                              'full')


def test_real_blaze():
    profile = _real('blaze')
    # ATK 765 + 28 + 60 = 853, interval 1.2 s: 710.83 DPS on each of the 3 enemies she blocks (Centurion).
    assert profile.summary().min_dps == pytest.approx(853 / 1.2)
    assert profile.summary().min_dps_total == pytest.approx(853 / 1.2 * 3)
    s1, s2, s3 = profile.skills
    # S1 M3: next attack at 290%, adding 1620.7 every 2 attacks + 1 = 3.6 s: 710.83 + 1620.7 / 3.6 = 1161.03.
    assert s1.average_dps == pytest.approx(853 / 1.2 + 853 * 1.9 / 3.6)
    # S2 M3: ATK +100%, unlimited: 1421.67 DPS, the best average and max.
    assert s2.dps == pytest.approx(1706 / 1.2) and s2.average_dps == s2.dps
    # S3 M3: ATK +80% on 3 enemies (AoE), 10 s of a 45 s cycle: (1279.5 × 10 + 710.83 × 35) / 45 = 837.2.
    assert s3.dps_total == pytest.approx(853 * 1.8 / 1.2 * 3)
    assert s3.average_dps == pytest.approx((853 * 1.8 / 1.2 * 10 + 853 / 1.2 * 35) / 45)
    summary = profile.summary()
    # DEF 370 + 45 (trust) = 415; S3's DEF +80% gives the max, 747.
    assert (summary.max_dps_skill, summary.min_def, summary.max_def) == ('Chainsaw Extension Module', 415, 747)


def test_real_projekt_red():
    profile = _real('projekt_red', level=80)
    # ATK 530 + 20 + 75 = 625, interval 0.93 s: 672.04 DPS. Her talent's "at least 33%" isn't a damage bonus.
    assert profile.summary().min_dps == pytest.approx(625 / 0.93)
    s1, s2 = profile.skills
    # S1: ATK +80% for 10 s after deployment: counts for max (1209.68), not for the average.
    assert s1.dps == pytest.approx(1125 / 0.93)
    # S2: 250% ATK to nearby enemies once after deployment.
    assert s2.burst[0].per_target == pytest.approx(1562.5)
    summary = profile.summary()
    assert (summary.max_dps_skill, summary.avg_dps, summary.avg_dps_skill) == ('Execution Mode', summary.min_dps, '')


def test_real_liskarm():
    profile = _real('liskarm', level=80)
    # ATK 425 + 45 = 470, interval 1.2 s: 391.67 DPS. DEF 710 + 27 + 45 = 782, RES 13 (talent).
    assert profile.summary().min_dps == pytest.approx(470 / 1.2)
    assert (profile.base_stats.defense, profile.base_stats.magic_resistance) == (782, 13)
    s1, s2 = profile.skills
    # S1 blocks a hit and raises DEF: no damage of its own.
    assert s1.burst == [] and s1.average_dps == pytest.approx(470 / 1.2)
    # S2 M3: ATK +200%, interval 1.2 + 0.7 = 1.9 s, Arts on up to 4 enemies: 742.1 DPS each, 2968.4 in total.
    assert s2.damage[0].damage_type == DamageType.ARTS
    assert (s2.dps, s2.dps_total) == (pytest.approx(1410 / 1.9), pytest.approx(1410 / 1.9 * 4))
    # 34 SP when hit, 1 attacker every 3 s: 102 s + 20 s active. A full block of 3: 34 s + 20 s.
    assert s2.average_dps == pytest.approx((1410 / 1.9 * 20 + 470 / 1.2 * 102) / 122)
    assert s2.full_block_average_dps == pytest.approx((1410 / 1.9 * 20 + 470 / 1.2 * 34) / 54)


def test_real_kaltsit_and_mon3tr():
    kaltsit = load_operator_fixture('kaltsit')
    profile = build_operator_profile(kaltsit, 2, 90, 5, 100, 7, (3, 3, 3))
    # A healer: ATK 490 + 25 = 515 healing every 2.85 s = 180.70 HPS, and no damage.
    summary = profile.summary()
    assert (summary.min_dps, summary.min_hps) == (0, pytest.approx(515 / 2.85))
    assert profile.summons == ['token_10002_kalts_mon3tr']
    # Mon3tr at Kal'tsit's level: ATK 1402 every 2.0 s = 701 DPS on one enemy. It lists no potential or trust
    # bonuses, so they're 0.
    [mon3tr] = build_summon_profiles(kaltsit, {'token_10002_kalts_mon3tr': load_operator_fixture('mon3tr')}, 2, 90, 5,
                                     100, 7, (3, 3, 3))
    assert (mon3tr.name, mon3tr.owner_id, mon3tr.potential, mon3tr.trust) == ('Mon3tr', 'char_003_kalts', 0, 0)
    assert mon3tr.summary().min_dps == pytest.approx(701)
    assert mon3tr.skills == []


def test_real_bagpipe():
    profile = _real('bagpipe')
    # ATK 586 + 25 (potential) + 85 (trust) = 696, attack interval 1.0 s.
    # Talent 1 (potential 6): 28% chance of two 130% strikes: 0.72 + 0.28 × 2.6 = 1.448 per hit.
    [talent] = [talent for talent in profile.talents if talent.number == 1]
    assert talent.multiplier == pytest.approx(1.448)
    assert profile.summary().min_dps == pytest.approx(696 * 1.448)
    s1, s2, s3 = profile.skills
    # S1 M3: ATK +45%, ASPD +45: 1009.2 × 1.448 / (1 / 1.45) = 2118.92, 35 s of a 70 s cycle.
    assert s1.dps == pytest.approx(1009.2 * 1.448 * 1.45)
    assert s1.average_dps == pytest.approx((1009.2 * 1.448 * 1.45 + 696 * 1.448) / 2)
    # S2 M3: next attack 200% and strikes again: 2 × 2 × 696 × 1.448 = 4031.23, minus the 1007.81 attack it replaces,
    # every 4 s (4 SP over time): 1007.81 + 3023.42 / 4 = 1763.66.
    assert s2.burst[0].per_target == pytest.approx(696 * 1.448 * 3)
    assert s2.average_dps == pytest.approx(696 * 1.448 + 696 * 1.448 * 3 / 4)
    # S3 M3: ATK +120% = 1531.2, 3 hits per attack (each can trigger the talent), interval 1 + 0.7 = 1.7 s:
    # 1531.2 × 3 × 1.448 / 1.7 = 3912.67 DPS, 20 s of a 60 s cycle.
    assert s3.dps == pytest.approx(1531.2 * 3 * 1.448 / 1.7)
    assert s3.average_dps == pytest.approx((1531.2 * 3 * 1.448 / 1.7 * 20 + 696 * 1.448 * 40) / 60)
    summary = profile.summary()
    assert (summary.max_dps_skill, summary.avg_dps_skill) == ('Locked Breech Burst', 'Locked Breech Burst')


def test_real_siege_gets_her_own_vanguard_buff():
    profile = _real('siege')
    assert {'vanguard', 'melee', 'glasgow', 'victoria'} <= profile.member_of
    # Talent 1 ("All Vanguard Operators' ATK and DEF +10%") includes Siege herself.
    # ATK (515 + 25 potential + 60 trust) × 1.1 = 660, DEF (384 + 25) × 1.1 = 449.9; 660 / 1.05 s = 628.57 DPS.
    assert (profile.base_stats.atk, profile.base_stats.defense) == (pytest.approx(660), pytest.approx(449.9))
    assert profile.summary().min_dps == pytest.approx(660 / 1.05)
    _, s2, s3 = profile.skills
    # S2 M3: next attack deals 340% to nearby enemies: 2244 - 660 = 1584 more, every 10 s: 628.57 + 158.4 = 786.97.
    assert s2.burst[0].per_target == pytest.approx(1584)
    assert s2.average_dps == pytest.approx(660 / 1.05 + 158.4)
    # S3 M3: ATK to 380%, attack interval 1.05 + 1 = 2.05 s: 1223.41 DPS for 25 s of a 55 s cycle.
    assert s3.dps == pytest.approx(660 * 3.8 / 2.05)
    assert s3.average_dps == pytest.approx((660 * 3.8 / 2.05 * 25 + 660 / 1.05 * 30) / 55)


def test_real_eyjafjalla_s1_versions_and_s2_ignition():
    profile = _real('eyjafjalla')
    # Talent 1 ("All [Caster] Operators' ATK +16%") includes Eyjafjalla.
    # ATK (645 + 27 potential + 90 trust) × 1.16 = 883.92; arts, interval 1.6 s: 552.45 DPS.
    assert profile.base_stats.atk == pytest.approx(883.92)
    assert profile.summary().min_dps == pytest.approx(552.45)
    s1, s2, _ = profile.skills
    # S1 M3: ATK +60% (counted from the first use) and ASPD +60: 762 × (1 + 0.16 + 0.6) = 1341.12 ATK,
    # interval 1.6 / 1.6 = 1 s: 1341.12 DPS for 25 s of a 60 s cycle: (1341.12 × 25 + 552.45 × 35) / 60 = 881.06.
    assert s1.stats.attack_interval == pytest.approx(1.0)
    assert s1.dps == pytest.approx(1341.12)
    assert s1.average_dps == pytest.approx((1341.12 * 25 + 552.45 * 35) / 60)
    # S2 M3: the next attack deals 370% (fk) instead of 100%: 883.92 × 2.7 = 2386.58 more on the target.
    # Half damage splashes on 2 others: 883.92 × 3.7 × 2.0 - 883.92 = 5657.09 more in total.
    # SP 5 over time: 552.45 + 2386.58 / 5 = 1029.77.
    assert s2.burst[0].per_target == pytest.approx(883.92 * 2.7)
    assert s2.burst[0].total == pytest.approx(883.92 * 3.7 * 2 - 883.92)
    assert s2.average_dps == pytest.approx(552.45 + 883.92 * 2.7 / 5)


# Hits, DEF/RES ignore and enemy effects kept for matchups

def test_flinger_hits_keep_the_shockwave_apart():
    profile = _profile(sub_profession='bombarder')
    assert [(hit.damage, hit.count) for hit in profile.base_hits] == [(640, 1), (320, 1)]
    assert profile.base_damage[0].per_target + profile.base_damage[1].per_target == 960


def test_talent_atk_and_damage_multipliers_in_hits():
    profile = _profile(talents=[[make_talent_candidate('ATK increased to 150% when attacking', {'atk_scale': 1.5})],
                                [make_talent_candidate('Deals 20% more damage when attacking', {'damage_scale': 1.2})]])
    [hit] = profile.base_hits
    # 640 × 1.5 = 960 before DEF, then × 1.2 after it: 1152 per second.
    assert (hit.damage, hit.amplifier) == (960, 1.2)
    assert profile.base_damage[0].per_target == pytest.approx(1152)


def test_instant_skill_hits():
    profile = _profile(_skill('The next attack deals {atk_scale:0%} ATK', {'atk_scale': 3.0},
                              sp_type='INCREASE_WHEN_ATTACK', sp_cost=4, duration=0),
                       _skill('Immediately deals {atk_scale:0%} ATK to nearby enemies', {'atk_scale': 3.0}, sp_cost=10,
                              duration=0))
    replacing, extra = profile.skills
    assert [(hit.damage, hit.interval) for hit in replacing.strike_hits] == [(1920, 1.0)]
    assert [(hit.damage, hit.interval) for hit in replacing.replaced_hits] == [(640, 1.0)]
    assert [hit.damage for hit in replacing.hits] == [hit.damage for hit in replacing.base_hits] == [640]
    assert [(hit.damage, hit.targets.count) for hit in extra.strike_hits] == [(1920, 3)]
    assert extra.replaced_hits == []


def test_def_ignore_and_enemy_effects():
    profile = _profile(
        _skill("ATK +{atk:0%} and attacks ignore {def_penetrate_fixed} DEF; Targets' DEF -{-def:0%}",
               {'atk': 0.5, 'def_penetrate_fixed': 400, 'def': -0.2}),
        talents=[[make_talent_candidate('Attacks ignore 20 RES', {'magic_resist_penetrate_fixed': 20})],
                 [make_talent_candidate('Attacks inflict 10% Fragile', {'damage_scale': 1.1})],
                 [make_talent_candidate('Ignores 200 DEF when attacking enemies with weight greater than or equal to 3',
                                        {'def_penetrate_fixed': 200})]])
    # The weight talent only applies to some enemies (PER_ENEMY), so it's left out here.
    assert profile.penetration == [Penetration(res_flat=20)]
    assert [value.key for value in profile.enemy_effects] == ['damage_scale']
    [skill] = profile.skills
    assert skill.penetration == [Penetration(def_flat=400), Penetration(res_flat=20)]
    assert [value.key for value in skill.enemy_effects] == ['def', 'damage_scale']


def _sums(damage):
    # (per target, total) of every type, healing included.
    return sum(part.per_target for part in damage), sum(part.total for part in damage)


@pytest.mark.parametrize('name, level', [('exusiai', 90), ('blaze', 90), ('eyjafjalla', 90), ('bagpipe', 80),
                                         ('siege', 90), ('liskarm', 80), ('kaltsit', 90), ('projekt_red', 80)])
def test_real_hits_add_up_to_the_damage(name, level):
    profile = _real(name, level)
    assert _sums(hit_damage(profile.base_hits)) == pytest.approx(_sums(profile.base_damage))
    for skill in profile.skills:
        assert _sums(hit_damage(skill.hits)) == pytest.approx(_sums(skill.damage))
        if skill.burst:
            strike, replaced = _sums(hit_damage(skill.strike_hits)), _sums(hit_damage(skill.replaced_hits))
            assert _sums(skill.burst)[0] == pytest.approx(strike[0] - replaced[0])


def test_real_eyjafjalla_ignition_hits():
    _, s2, _ = _real('eyjafjalla').skills
    [strike] = s2.strike_hits
    # 370% of 883.92 on the target, half on the two enemies around it.
    assert (strike.damage, strike.targets.target_scales) == (pytest.approx(883.92 * 3.7), (1.0, 0.5, 0.5))
    assert [hit.damage for hit in s2.replaced_hits] == [pytest.approx(883.92)]

