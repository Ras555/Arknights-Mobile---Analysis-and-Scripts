import math

import pytest

from arkdata.calculations.attack_targets import Targets
from arkdata.calculations.blackboard import BlackboardValue, Target
from arkdata.calculations.damage import DamageType
from arkdata.calculations.enemy_stats import EnemyStats
from arkdata.calculations.matchups import (
    MATCHUP_COLUMNS,
    Defenses,
    ProfileBuilder,
    Scenario,
    calculate_matchups,
    condition_met,
    damage_against,
    enemy_damage,
    enemy_defenses,
    extra_effects,
    hit_after_defenses,
    hp_segments,
    kill_time,
    matchup_row,
    over_segments,
)
from arkdata.calculations.operator_profile import EnemyEffect, Hit, UnitStats, build_operator_profile
from arkdata.calculations.skills import Penetration
from arkdata.calculations.talents import TalentCondition, TalentEffect, TalentGroup
from factories import (
    defined,
    load_enemy_fixture,
    load_operator_fixture,
    make_enemy_data,
    make_operator_value,
    make_skill_entry,
    make_skill_level,
    make_talent_candidate,
)

# make_operator_value at phase 2 level 90, potential 0, trust 0: ATK 640, DEF 100, HP 1000, RES 0, block 1,
# attack interval 1.0 s, Physical damage.
LORD_TRAIT = [{'unlockCondition': {'phase': 'PHASE_0', 'level': 1}, 'requiredPotentialRank': 0,
               'blackboard': [{'key': 'atk_scale', 'value': 0.8}]}]


def _unit(*skills, talents=(), position='RANGED', sub_profession='fearless', block=None, trait=None, owner=''):
    value = make_operator_value(skills=skills, talents=[[candidate] for candidate in talents],
                                sub_profession=sub_profession, trait_candidates=trait)
    value['data']['position'] = position
    if block is not None:
        for phase in value['data']['phases']:
            for frame in phase['attributesKeyFrames']:
                frame['data']['blockCnt'] = block
    return ProfileBuilder(lambda extra: build_operator_profile(value, 2, 90, 0, 0, 1, extra_effects=extra), owner=owner)


def _talent(description, blackboard):
    return make_talent_candidate(description, blackboard)


def _skill(description, blackboard=None, name='Test Skill', **kwargs):
    return make_skill_entry(make_skill_level(description, blackboard, name=name, **kwargs), skill_id=f'sk_{name}')


def _enemy(motion='WALK', apply_way='MELEE', tags=None, level_type='NORMAL', abilities=(), damage_type=('PHYSIC',),
           **attributes):
    # make_enemy_data: HP 2000, ATK 200, DEF 100, RES 20, attack interval 2 s, unless given.
    data = make_enemy_data(damage_type=damage_type, apply_way=apply_way, base_attributes=attributes)
    enemy_data = data['levels']['Value'][0]['enemyData']
    enemy_data['motion'] = defined(motion)
    if tags is not None:
        enemy_data['enemyTags'] = defined(list(tags))
    data['excel']['enemyLevel'] = level_type
    data['excel']['abilityList'] = [{'text': text} for text in abilities]
    return EnemyStats('enemy_test', enemy_data=data)


FLYING = dict(motion='FLY', apply_way='RANGED')


# Damage per hit

def test_physical_damage_after_def():
    row = matchup_row(_unit(), _enemy(**{'def': 300, 'maxHp': 3400}), 2)
    # 640 - 300 = 340 per second; 3400 HP in 10 s.
    assert (row['min_dps'], row['avg_dps'], row['max_dps'], row['min_dps_total']) == (340, 340, 340, 340)
    assert (row['avg_dps_skill'], row['max_dps_skill']) == ('', '')
    assert (row['min_kill_time'], row['enemy_spawn_count'], row['can_attack']) == (10, 2, True)


def test_minimum_damage_is_five_percent():
    assert matchup_row(_unit(), _enemy(**{'def': 1000}))['min_dps'] == pytest.approx(32)


@pytest.mark.parametrize('damage_type, penetration, expected', [
    # (500 - 100 flat) × (1 - 0.5) = 200 DEF used: 640 - 200.
    (DamageType.PHYSICAL, [Penetration(def_flat=100, def_ratio=0.5)], 440),
    # 20% of the time all DEF is ignored: 0.2 × 640 + 0.8 × (640 - 500).
    (DamageType.PHYSICAL, [Penetration(def_ratio=1.0, chance=0.2)], 0.2 * 640 + 0.8 * 140),
    (DamageType.PHYSICAL, [], 140),
    # RES 50: (50 - 10) × 0.5 = 20 used, 640 × 0.8.
    (DamageType.ARTS, [Penetration(res_flat=10, res_ratio=0.5)], 512),
    (DamageType.ARTS, [], 320),
    (DamageType.TRUE, [], 640),
])
def test_hit_after_defenses(damage_type, penetration, expected):
    assert hit_after_defenses(640, damage_type, Defenses(500, 50), penetration) == pytest.approx(expected)


def test_hit_after_defenses_limits():
    # RES above 100 is used as 100, leaving the 5% minimum; DEF ignore can't make DEF negative.
    assert hit_after_defenses(640, DamageType.ARTS, Defenses(0, 150), []) == pytest.approx(32)
    assert hit_after_defenses(640, DamageType.PHYSICAL, Defenses(100, 0), [Penetration(def_flat=400)]) == 640


def _effect(key, value, is_percent=None, scale=1.0, affects='ALL'):
    return EnemyEffect(BlackboardValue(key, key, value, is_percent, Target.ENEMY), scale, affects)


def test_enemy_defenses():
    enemy = _enemy(**{'def': 300, 'magicResistance': 50})
    defenses = enemy_defenses(enemy, [_effect('def', -100, False), _effect('def', -0.2, True),
                                      _effect('magic_resistance', -20, False), _effect('magic_resistance', 0.5, True),
                                      _effect('damage_scale', 1.12), _effect('damage_scale', 1.2, affects='ARTS'),
                                      _effect('damage_scale', 1.1), _effect('damage_scale', 1.3, affects='NONE'),
                                      _effect('atk', -0.5, True)])
    # DEF (300 - 100) × (1 - 0.2) = 160; RES (50 - 20) × 1.5 = 45.
    assert (defenses.defense, defenses.resistance) == (pytest.approx(160), pytest.approx(45))
    # Only the highest Fragile of a kind counts (1.12, not 1.1); Elemental isn't counted.
    assert defenses.fragile_for(DamageType.PHYSICAL) == pytest.approx(1.12)
    assert defenses.fragile_for(DamageType.ARTS) == pytest.approx(1.12 * 1.2)


def test_enemy_defenses_scale_and_floor():
    enemy = _enemy(**{'def': 100})
    # A 50% chance of 20% Fragile averages 10%; DEF can't go below 0.
    defenses = enemy_defenses(enemy, [_effect('damage_scale', 1.2, scale=0.5), _effect('def', -500, False)])
    assert (defenses.defense, defenses.fragile_for(DamageType.TRUE)) == (0, pytest.approx(1.1))


def test_damage_against_shares_over_targets():
    hit = Hit(DamageType.PHYSICAL, 640, 1.0, 1.0, 1.0, Targets(3, 2.0, True, '', scales=(1.0, 0.5, 0.5)))
    # 640 - 100 on the target, 320 - 100 on each of the other two.
    assert damage_against([hit], Defenses(100, 0), []) == (540, 540 + 2 * 220)
    heal = Hit(DamageType.HEAL, 640, 1.0, 1.0, 1.0, Targets())
    assert damage_against([heal], Defenses(100, 0), []) == (0, 0)


# Who can attack whom

def test_aerial_talent_applies_to_flying_enemies():
    talent = _talent('Increases ATK to 140% when attacking aerial targets', {'atk_scale': 1.4})
    flying = matchup_row(_unit(talents=[talent]), _enemy(**FLYING, **{'def': 0}))
    assert flying['min_dps'] == pytest.approx(896)
    assert flying['talents_applied'] == 'Test Talent (aerial)'
    ground = matchup_row(_unit(talents=[talent]), _enemy(**{'def': 0}))
    assert (ground['min_dps'], ground['talents_applied']) == (640, '')


def test_melee_operators_cant_attack_flying_enemies():
    row = matchup_row(_unit(position='MELEE'), _enemy(**FLYING))
    assert (row['can_attack'], row['min_dps'], row['max_dps'], row['min_kill_time']) == (False, 0, 0, math.inf)
    assert "can't attack aerial enemies" in row['notes']


def test_lords_hit_flying_enemies_at_80_percent():
    row = matchup_row(_unit(position='MELEE', sub_profession='lord', trait=LORD_TRAIT), _enemy(**FLYING, **{'def': 0}))
    assert row['min_dps'] == pytest.approx(512)
    assert 'ranged attacks at 80% damage' in row['notes']


def test_skills_that_hit_flying_enemies():
    row = matchup_row(_unit(_skill('Takes Off; ATK +{atk:0%}', {'atk': 0.5}, sp_cost=20, duration=20), position='MELEE'),
                      _enemy(**FLYING, **{'def': 0}))
    # Only during the skill: 960 for 20 s of a 40 s cycle.
    assert (row['min_dps'], row['max_dps'], row['max_dps_skill']) == (0, 960, 'Test Skill')
    assert row['avg_dps'] == pytest.approx(480)
    assert "can't attack aerial enemies except during Test Skill" in row['notes']


def test_skybreakers_hit_ground_enemies_only_during_skills():
    unit = _unit(_skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=20, duration=20), sub_profession='skybreaker')
    ground = matchup_row(unit, _enemy(**{'def': 0}))
    assert (ground['min_dps'], ground['max_dps']) == (0, 960)
    assert 'normal attacks only hit aerial enemies' in ground['notes']
    assert matchup_row(unit, _enemy(**FLYING, **{'def': 0}))['min_dps'] == 640


def test_melee_enemies_only_hit_operators_that_block_them():
    ranged = matchup_row(_unit(), _enemy())
    assert (ranged['enemy_dps_vs_min_def'], ranged['enemy_kill_time_vs_min_def']) == (0, math.inf)
    assert "melee enemy: can't reach a ranged operator" in ranged['notes']
    # Blocked by a melee operator: (200 - 100) / 2 s = 50 DPS, 1000 HP in 20 s.
    melee = matchup_row(_unit(position='MELEE'), _enemy())
    assert (melee['enemy_dps_vs_min_def'], melee['enemy_kill_time_vs_min_def']) == (50, 20)


def test_unblockable_melee_enemies_hit_no_one():
    row = matchup_row(_unit(position='MELEE'), _enemy(abilities=['Unblockable']))
    assert row['enemy_dps_vs_min_def'] == 0
    assert "this enemy can't be blocked" in row['notes']


def test_enemies_needing_more_block():
    enemy = load_enemy_fixture('carnevale_snack_seller')
    row = matchup_row(_unit(position='MELEE'), enemy)
    assert row['enemy_dps_vs_min_def'] == 0
    assert "can't block this enemy (needs 3 block, has 1)" in row['notes']
    # With 3 block it's blocked: 300 ATK Arts damage every 4 s against 0 RES.
    assert matchup_row(_unit(position='MELEE', block=3), enemy)['enemy_dps_vs_min_def'] == 75


def test_melee_units_without_block():
    # Ambushers and some summons are melee with 0 block.
    row = matchup_row(_unit(position='MELEE', block=0), _enemy())
    assert row['enemy_dps_vs_min_def'] == 0
    assert "doesn't block enemies" in row['notes']


def test_enemies_that_dont_attack():
    row = matchup_row(_unit(position='MELEE'), _enemy(apply_way='NONE'))
    assert row['enemy_dps_vs_min_def'] == 0
    assert matchup_row(_unit(), _enemy(damage_type=None, apply_way='RANGED'))['enemy_dps_vs_min_def'] == 0


# Talent conditions

def test_blocking_best_case_for_each_figure():
    # Firewhistle's T1: ATK when not blocking, DEF when blocking.
    talent = _talent('ATK +14% when not blocking an enemy, DEF +14% when blocking an enemy', {'atk': 0.14, 'def': 0.14})
    row = matchup_row(_unit(talents=[talent], position='MELEE'), _enemy(**{'def': 0, 'atk': 300}))
    # Damage: not blocking, 640 × 1.14. The melee enemy only hits it while blocked: DEF 114, (300 - 114) / 2.
    assert row['min_dps'] == pytest.approx(729.6)
    assert row['enemy_dps_vs_min_def'] == pytest.approx(93)
    assert row['talents_applied'] == 'Test Talent (blocked); Test Talent (not blocked)'


def test_ranged_enemies_hit_the_best_blocking_case():
    talent = _talent('ATK +14% when not blocking an enemy, DEF +14% when blocking an enemy', {'atk': 0.14, 'def': 0.14})
    row = matchup_row(_unit(talents=[talent], position='MELEE'), _enemy(apply_way='RANGED', atk=300))
    assert row['enemy_dps_vs_min_def'] == pytest.approx((300 - 114) / 2)


def test_ranged_operators_count_as_blocking_and_not():
    talents = [_talent('Deal 20% more damage when attacking blocked enemies', {'atk_scale': 1.2}),
               _talent('Increases ATK to 123% when attacking unblocked enemies', {'atk_scale': 1.23})]
    assert matchup_row(_unit(talents=talents), _enemy(**{'def': 0}))['min_dps'] == pytest.approx(640 * 1.23 * 1.2)


def test_blocking_several_enemies_needs_the_block():
    talent = _talent('Increases ATK and DEF by 14% when blocking 2 or more enemies', {'atk': 0.14, 'def': 0.14})
    assert matchup_row(_unit(talents=[talent], position='MELEE'), _enemy(**{'def': 0}))['min_dps'] == 640
    row = matchup_row(_unit(talents=[talent], position='MELEE', block=2), _enemy(**{'def': 0}))
    assert row['min_dps'] == pytest.approx(729.6)


def test_weight_def_ignore():
    # Rosa's T1 against Sarkaz Lancer (DEF 150, weight 3): 640 - 150 × 0.4.
    talent = _talent('When attacking a heavy enemy, ignores 60% of its DEF (enemies with 3 weight or higher count for '
                     'this effect)', {'value': 3, 'def_penetrate': 0.6})
    assert matchup_row(_unit(talents=[talent]), load_enemy_fixture('sarkaz_lancer'))['min_dps'] == pytest.approx(580)
    assert matchup_row(_unit(talents=[talent]), _enemy(**{'def': 150}))['min_dps'] == pytest.approx(490)


def test_enemy_type_and_elite_talents():
    lancer = load_enemy_fixture('sarkaz_lancer')
    sarkaz = _talent('When attacking Sarkaz enemies, increase ATK to 150%', {'atk_scale': 1.5})
    assert matchup_row(_unit(talents=[sarkaz]), lancer)['min_dps'] == 810
    elite = _talent('Deals 20% more damage when attacking elite enemies', {'damage_scale': 1.2})
    assert matchup_row(_unit(talents=[elite]), lancer)['min_dps'] == pytest.approx(490 * 1.2)
    assert matchup_row(_unit(talents=[elite]), _enemy(**{'def': 150}))['min_dps'] == pytest.approx(490)


def test_ranged_enemies_include_all():
    talent = _talent('ATK increases to 145% when attacking ranged enemies', {'atk_scale': 1.45})
    assert matchup_row(_unit(talents=[talent]), _enemy(apply_way='ALL', **{'def': 0}))['min_dps'] == 928
    assert matchup_row(_unit(talents=[talent]), _enemy(**{'def': 0}))['min_dps'] == 640


def test_enemy_hp_threshold_by_share_of_hp():
    # Necrass's T2: ATK 150% below 60% HP. 40% of 10,000 HP at 640, 60% at 960: 6.25 + 6.25 = 12.5 s, 800 DPS.
    talent = _talent('When attacking enemies with less than 60% HP, increases ATK to 150%', {'damage_scale': 1.5})
    row = matchup_row(_unit(talents=[talent]), _enemy(**{'def': 0, 'maxHp': 10000}))
    assert (row['min_dps'], row['min_kill_time']) == (pytest.approx(800), pytest.approx(12.5))
    assert row['talents_applied'] == 'Test Talent (enemy HP below 60%)'


def test_full_hp_bonus_on_the_first_hit():
    # Puzzle's T1: the first attack at 150% deals 320 more, so 6400 HP takes (6400 - 320) / 640 = 9.5 s.
    talent = _talent('ATK increased to 150% when hitting an enemy with full HP', {'atk_scale': 1.5})
    row = matchup_row(_unit(talents=[talent]), _enemy(**{'def': 0, 'maxHp': 6400}))
    assert row['min_kill_time'] == pytest.approx(9.5)
    assert 'full-HP bonus counted on the first hit only' in row['notes']


def test_enemy_status_talents_arent_applied():
    talent = _talent('Increase ATK to 165% when attacking Stunned enemies', {'atk_scale': 1.65})
    row = matchup_row(_unit(talents=[talent]), _enemy(**{'def': 0}))
    assert row['min_dps'] == 640
    assert 'not applied (depends on the fight): Test Talent' in row['notes']


def test_talent_debuffs_and_def_ignore():
    fragile = _talent('Attacks inflict 10% Fragile', {'damage_scale': 1.1})
    assert matchup_row(_unit(talents=[fragile]), _enemy())['min_dps'] == pytest.approx(540 * 1.1)
    arts = _talent('Attacks inflict 10% [Arts Fragility]', {'damage_scale': 1.1})
    assert matchup_row(_unit(talents=[arts]), _enemy())['min_dps'] == 540
    ignore = _talent('Attacks ignore 175 DEF', {'def_penetrate_fixed': 175})
    assert matchup_row(_unit(talents=[ignore]), _enemy(**{'def': 150}))['min_dps'] == 640


# Skills

def test_timed_skill_against_def():
    row = matchup_row(_unit(_skill('ATK +{atk:0%}', {'atk': 1.0}, sp_cost=30, duration=20)), _enemy(**{'def': 140}))
    # 500 with no skill, 1140 for 20 s of a 50 s cycle: (1140 × 20 + 500 × 30) / 50 = 756.
    assert (row['min_dps'], row['max_dps'], row['avg_dps']) == (500, 1140, pytest.approx(756))
    assert (row['max_dps_skill'], row['avg_dps_skill']) == ('Test Skill', 'Test Skill')


def test_skill_debuffs_count_while_it_is_active():
    skill = _skill("ATK +{atk:0%}; Targets' DEF -{-def:0%}", {'atk': 0.5, 'def': -0.5}, sp_cost=20, duration=20)
    row = matchup_row(_unit(skill), _enemy(**{'def': 400}))
    # 640 - 400 = 240; during the skill 960 - 200 = 760; average (760 + 240) / 2.
    assert (row['min_dps'], row['max_dps'], row['avg_dps']) == (240, 760, pytest.approx(500))


def test_instant_skill_debuffs_only_on_its_strike():
    skill = _skill("The next attack deals {atk_scale:0%} ATK; Targets' DEF -{-def}", {'atk_scale': 3.0, 'def': -300},
                   sp_type='INCREASE_WHEN_ATTACK', sp_cost=4, duration=0)
    row = matchup_row(_unit(skill), _enemy(**{'def': 400}))
    # Strike 1920 - 100 = 1820 instead of a 240 attack: +1580 every 5 attacks (5 s).
    assert (row['min_dps'], row['max_dps'], row['avg_dps']) == (240, 240, pytest.approx(240 + 1580 / 5))


def test_enemy_damage_against_the_best_defensive_skill():
    unit = _unit(_skill('DEF +{def:0%}', {'def': 1.0}, name='Shield', duration=20),
                 _skill("Targets' ATK -{-atk:0%}", {'atk': -0.5}, name='Weaken', duration=20))
    row = matchup_row(unit, _enemy(apply_way='RANGED', atk=500))
    # No skill: (500 - 100) / 2 = 200. Shield: (500 - 200) / 2 = 150. Weaken: (250 - 100) / 2 = 75.
    assert (row['enemy_dps_vs_min_def'], row['enemy_dps_vs_max_def'], row['max_def_skill']) == (200, 75, 'Weaken')
    assert (row['enemy_kill_time_vs_min_def'], row['enemy_kill_time_vs_max_def']) == (5, pytest.approx(1000 / 75))


def test_enemy_damage_keeps_aspd_within_limits():
    stats = UnitStats(0, 0, 1000, 0, 100, 1, 1)
    enemy = _enemy(atk=200)
    # ASPD 100 - 500 is kept at 20: 2 s / 0.2 = 10 s between attacks.
    assert enemy_damage(enemy, stats, [_effect('attack_speed', -500, False)]) == pytest.approx(20)
    assert enemy_damage(enemy, stats, [_effect('atk', 100, False), _effect('atk', 0.5, True)]) == pytest.approx(225)
    enemy.stats['baseAttackTime'] = 0
    assert enemy_damage(enemy, stats, []) == 0


# Building blocks

@pytest.mark.parametrize('condition, enemy, scenario, hp, expected', [
    (TalentCondition('aerial'), _enemy(**FLYING), Scenario(), None, True),
    (TalentCondition('aerial'), _enemy(), Scenario(), None, False),
    (TalentCondition('elite'), _enemy(level_type='BOSS'), Scenario(), None, True),
    (TalentCondition('elite', negated=True), _enemy(level_type='BOSS'), Scenario(), None, False),
    (TalentCondition('weight', minimum=3), _enemy(massLevel=3), Scenario(), None, True),
    (TalentCondition('weight', maximum=3), _enemy(massLevel=4), Scenario(), None, False),
    (TalentCondition('enemy type', tag='ground'), _enemy(), Scenario(), None, True),
    (TalentCondition('enemy type', tag='machine'), _enemy(tags=['machine']), Scenario(), None, True),
    (TalentCondition('enemy type', tag='sleeping'), _enemy(), Scenario(), None, False),
    (TalentCondition('enemy type'), _enemy(), Scenario(), None, False),
    (TalentCondition('enemy status', tag='stunned'), _enemy(), Scenario(), None, False),
    (TalentCondition('blocked'), _enemy(), Scenario(True), None, True),
    (TalentCondition('blocked', negated=True), _enemy(), Scenario(True), None, False),
    (TalentCondition('unblocked'), _enemy(), Scenario(False), None, True),
    (TalentCondition('unblocked'), _enemy(), Scenario(None), None, True),
    (TalentCondition('enemy HP', maximum=0.6), _enemy(), Scenario(), 0.3, True),
    (TalentCondition('enemy HP', maximum=0.6), _enemy(), Scenario(), 0.8, False),
    (TalentCondition('enemy HP', minimum=0.8), _enemy(), Scenario(), 0.9, True),
    (TalentCondition('enemy HP', minimum=0.8), _enemy(), Scenario(), None, False),
])
def test_condition_met(condition, enemy, scenario, hp, expected):
    profile = _unit()()
    assert condition_met(condition, enemy, profile, scenario, hp) == expected


def _hp_effect(condition):
    return (1, TalentEffect('atk', TalentGroup.PER_ENEMY, condition))


def test_hp_segments():
    assert hp_segments([]) == [(1.0, 0.5)]
    assert hp_segments([_hp_effect(TalentCondition('enemy HP', maximum=0.6))]) == [(pytest.approx(0.4), 0.8),
                                                                                   (0.6, 0.3)]
    # Full HP is only the first hit, so it doesn't split the fight.
    assert hp_segments([_hp_effect(TalentCondition('enemy HP', minimum=1.0))]) == [(1.0, 0.5)]


def test_over_segments_and_kill_time():
    assert over_segments([(0.4, 640), (0.6, 960)]) == pytest.approx(800)
    assert over_segments([(0.5, 100), (0.5, 0)]) == 0
    assert over_segments([]) == 0
    assert (kill_time(1000, 50), kill_time(1000, 0)) == (20, math.inf)


def test_profile_builder_builds_each_set_once():
    built = []
    builder = ProfileBuilder(lambda extra: built.append(extra) or len(built), owner='Owner')
    assert (builder(), builder(), builder(frozenset({(1, 'atk')}))) == (1, 1, 2)
    assert builder.owner == 'Owner'
    seeded = ProfileBuilder(lambda extra: 'built', profile='given')
    assert (seeded(), seeded(frozenset({(1, 'atk')}))) == ('given', 'built')


def test_calculate_matchups():
    units = [_unit(), _unit(owner='Owner')]
    df = calculate_matchups(units, [(_enemy(), 1), (_enemy(**FLYING), 2)])
    assert list(df.columns) == MATCHUP_COLUMNS
    assert len(df) == 4
    assert df['owner'].tolist() == ['', '', 'Owner', 'Owner']
    assert calculate_matchups(units, []).empty


# Real operators (tests/fixtures/operators) against real enemies (tests/fixtures/enemies), at max level, potential 6,
# trust 100, M3

def _real(name, level=90):
    value = load_operator_fixture(name)
    return ProfileBuilder(lambda extra: build_operator_profile(value, 2, level, 5, 100, 7, (3, 3, 3), extra_effects=extra))


def test_real_eyjafjalla_against_sarkaz_lancer():
    row = matchup_row(_real('eyjafjalla'), load_enemy_fixture('sarkaz_lancer'))
    # ATK 883.92 Arts against 40 RES: 530.35 per attack, 331.47 DPS.
    assert row['min_dps'] == pytest.approx(883.92 * 0.6 / 1.6)
    # Volcano: 1874.52 ATK every 0.5 s on up to 6 enemies.
    assert (row['max_dps'], row['max_dps_skill']) == (pytest.approx(1874.52 * 0.6 / 0.5), 'Volcano')
    assert row['max_dps_total'] == pytest.approx(1874.52 * 0.6 / 0.5 * 6)
    # Ignition: its RES -25% makes 30 RES for the 370% strike: 3270.50 × 0.7 = 2289.35, instead of a 530.35 attack,
    # every 5 s: 331.47 + 1759.00 / 5 = 683.27, better than Volcano's average.
    strike, replaced = 883.92 * 3.7 * 0.7, 883.92 * 0.6
    assert (row['avg_dps'], row['avg_dps_skill']) == (pytest.approx(883.92 * 0.6 / 1.6 + (strike - replaced) / 5),
                                                      'Ignition')
    # The Lancer is a melee enemy, so it can't reach her.
    assert row['enemy_dps_vs_min_def'] == 0


def test_real_exusiai_and_siege_against_a_drone():
    drone = load_enemy_fixture('monster_mk_ii')
    exusiai = _real('exusiai')
    row = matchup_row(exusiai, drone)
    # ATK 709.56 - 50 DEF, 1.15 attacks per second.
    assert row['min_dps'] == pytest.approx((709.56 - 50) * 1.15)
    # The drone's 220 ATK every 3 s against her DEF.
    defense = exusiai().base_stats.defense
    assert row['enemy_dps_vs_min_def'] == pytest.approx(max(220 - defense, 220 * 0.05) / 3)
    siege = matchup_row(_real('siege'), drone)
    assert (siege['can_attack'], siege['max_dps']) == (False, 0)
    assert siege['enemy_dps_vs_min_def'] > 0


def test_extra_effects_choose_the_effects_that_fit():
    aerial = (1, TalentEffect('atk', TalentGroup.PER_ENEMY, TalentCondition('aerial')))
    first_hit = (2, TalentEffect('atk_scale', TalentGroup.PER_ENEMY, TalentCondition('enemy HP', minimum=1.0)))
    unconditional = (3, TalentEffect('def', TalentGroup.PER_ENEMY))
    profile, flying = _unit()(), _enemy(**FLYING)
    effects = [aerial, first_hit, unconditional]
    assert extra_effects(effects, flying, profile, Scenario()) == {(1, 'atk')}
    assert extra_effects(effects, flying, profile, Scenario(), first_hit=True) == {(1, 'atk'), (2, 'atk_scale')}
    assert extra_effects(effects, _enemy(), profile, Scenario()) == frozenset()


def test_first_hit_bonus_with_no_damage():
    from arkdata.calculations.matchups import _first_hit_dps
    assert _first_hit_dps(1000, 0, 100) == 0
    # A first hit that finishes the enemy.
    assert _first_hit_dps(1000, 100, 1000) == math.inf

