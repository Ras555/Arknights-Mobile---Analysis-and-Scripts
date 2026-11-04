import pytest

from arkdata.calculations.attack_targets import Targets
from arkdata.calculations.blackboard import BlackboardValue, Target
from arkdata.calculations.damage import DamageType
from arkdata.calculations.skill_rotation import SkillTrigger
from arkdata.calculations.skills import (
    MAX_MASTERY,
    MAX_SKILL_LEVEL,
    DamagePart,
    SkillStatus,
    SkillType,
    StatEffects,
    StatModifiers,
    apply_stat_value,
    apply_stat_values,
    is_stat_value,
    level_index,
    parse_skill,
    unlocked_skills,
)
from factories import load_skill_fixture, make_skill_entry, make_skill_level

ONE = Targets()
AOE = Targets(3, 3.0, True, 'AoE (no target limit)')


def _parse(*levels, damage_type=DamageType.PHYSICAL, basic=ONE, skill_level=1, mastery=0):
    return parse_skill(make_skill_entry(*levels), 1, skill_level, mastery, damage_type, basic)


def _real(name, skill_level=7, mastery=3, damage_type=DamageType.PHYSICAL, basic=ONE, number=1):
    return parse_skill(load_skill_fixture(name), number, skill_level, mastery, damage_type, basic)


# level_index

def test_skill_level_constants():
    assert (MAX_SKILL_LEVEL, MAX_MASTERY) == (7, 3)


@pytest.mark.parametrize('skill_level, mastery, expected', [(1, 0, 0), (4, 0, 3), (7, 0, 6), (7, 1, 7), (7, 3, 9)])
def test_level_index(skill_level, mastery, expected):
    assert level_index(skill_level, mastery, 10) == expected


@pytest.mark.parametrize('skill_level, mastery, message', [
    (0, 0, 'Skill level must be 1-7, got 0'),
    (8, 0, 'Skill level must be 1-7, got 8'),
    (7, 4, 'Mastery must be 0-3, got 4'),
    (7, -1, 'Mastery must be 0-3, got -1'),
    (6, 1, 'Skills can only be mastered at skill level 7, got level 6 mastery 1'),
    (7, 1, "This skill has 7 levels, so skill level 7 mastery 1 doesn't exist"),
])
def test_level_index_rejects_levels_that_dont_exist(skill_level, mastery, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        level_index(skill_level, mastery, 7)


def test_level_index_last_level_of_a_short_skill():
    assert level_index(7, 0, 7) == 6


# unlocked_skills

def _operator(*conditions):
    data_skills, entries = [], []
    for i, condition in enumerate(conditions, start=1):
        data_skills.append({'skillId': f'sk_{i}', 'unlockCond': condition})
        entries.append(make_skill_entry(skill_id=f'sk_{i}'))
    return {'data': {'skills': data_skills}, 'skills': entries}


def test_unlocked_skills_follow_phase_and_level():
    operator = _operator({'phase': 'PHASE_0', 'level': 1}, {'phase': 'PHASE_1', 'level': 1}, {'phase': 'PHASE_2', 'level': 1})
    assert [number for number, _ in unlocked_skills(operator, 0, 50)] == [1]
    assert [number for number, _ in unlocked_skills(operator, 1, 1)] == [1, 2]
    assert [number for number, _ in unlocked_skills(operator, 2, 1)] == [1, 2, 3]
    assert unlocked_skills(operator, 2, 1)[2][1]['excel']['skillId'] == 'sk_3'


def test_unlock_level_within_a_phase():
    operator = _operator({'phase': 1, 'level': 30})
    assert unlocked_skills(operator, 1, 29) == []
    assert len(unlocked_skills(operator, 1, 30)) == 1


def test_skills_are_matched_by_id_and_need_levels():
    operator = _operator({'phase': 'PHASE_0', 'level': 1}, {'phase': 'PHASE_0', 'level': 1})
    operator['skills'].reverse()
    assert [entry['excel']['skillId'] for _, entry in unlocked_skills(operator, 0, 1)] == ['sk_1', 'sk_2']
    operator['skills'][0]['excel']['levels'] = []
    assert [number for number, _ in unlocked_skills(operator, 0, 1)] == [1]


def test_operators_without_skills():
    assert unlocked_skills({'data': {}, 'skills': []}, 2, 90) == []
    assert unlocked_skills({'data': {'skills': [{'skillId': 'x'}]}}, 0, 1) == []


def test_missing_unlock_condition_means_unlocked_from_the_start():
    operator = _operator({})
    assert len(unlocked_skills(operator, 0, 1)) == 1


# parse_skill: identity, SP and duration

def test_parse_skill_reads_identity_and_sp():
    skill = parse_skill(make_skill_entry(make_skill_level('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=40, initial_sp=10,
                                                         name='Strike')), 2, 1, 0, DamageType.PHYSICAL, ONE)
    assert (skill.skill_id, skill.number, skill.name, skill.level, skill.mastery) == ('skchr_test_1', 2, 'Strike', 1, 0)
    assert (skill.skill_type, skill.sp_type, skill.sp_cost, skill.initial_sp, skill.duration) == (
        'MANUAL', 'INCREASE_WITH_TIME', 40, 10, 20)
    assert skill.description == 'ATK +50%'
    assert not skill.instant and not skill.passive


def test_parse_skill_picks_the_mastery_level():
    levels = [make_skill_level('ATK +{atk:0%}', {'atk': i / 10}) for i in range(10)]
    assert _parse(*levels, skill_level=7, mastery=2).modifiers.atk_percent == pytest.approx(0.8)


@pytest.mark.parametrize('duration, description, expected_duration, instant', [
    (20, 'ATK +{atk:0%}', 20, False),
    (0, 'Deals {atk_scale:0%}', 0, True),
    (-1, 'ATK +{atk:0%}\nUnlimited duration', None, False),
    (0, 'Can switch between the original state and the following state: DEF +{atk:0%}', None, False),
    (-1, 'Immediately launches a grenade dealing {atk_scale:0%}', 0, True),
    (-1, 'Deals {atk_scale:0%} in the next attack', 0, True),
    (20, 'The next attack deals {atk_scale:0%}', 0, True),
])
def test_duration_rules(duration, description, expected_duration, instant):
    skill = _parse(make_skill_level(description, {'atk': 0.5, 'atk_scale': 2.0}, duration=duration))
    assert (skill.duration, skill.instant) == (expected_duration, instant)


def test_sp_skills_have_the_sp_trigger():
    assert _parse(make_skill_level('ATK +{atk:0%}', {'atk': 0.5})).trigger == SkillTrigger.SP


def _passive(description, duration, blackboard=None):
    return _parse(make_skill_level(description, blackboard, skill_type='PASSIVE', sp_type=8, sp_cost=0, duration=duration))


@pytest.mark.parametrize('description, duration, blackboard, trigger, expected_duration, instant', [
    # Always on: no deployment wording and no duration (Hoshiguma's Thorns, Verdant).
    ('DEF +{def:0%}', 0, {'def': 0.3}, SkillTrigger.ALWAYS, None, False),
    ('Max HP +{max_hp:0%}, RES +{magic_resistance}', -1, {'max_hp': 0.2, 'magic_resistance': 10}, SkillTrigger.ALWAYS, None,
     False),
    # After deployment for a set time (Projekt Red S1), or the time from the text when the data says -1 (Kafka S1).
    ('After deployment, ATK +{atk:0%}', 10, {'atk': 0.8}, SkillTrigger.DEPLOYMENT, 10, False),
    ('After deployment, stops attacking for {duration} seconds', -1, {'duration': 5}, SkillTrigger.DEPLOYMENT, 5, False),
    ('ATK +{atk:0%} for {duration} seconds after Skadi is deployed', -1, {'atk': 0.5, 'duration': 30},
     SkillTrigger.DEPLOYMENT, 30, False),
    ('Upon deployment, gains a buff', -1, {}, SkillTrigger.DEPLOYMENT, None, False),
    # A one-off hit after deployment (Projekt Red S2, Texas the Omertosa S2), even with a timed effect.
    ('After deployment, immediately deals {atk_scale:0%} ATK', 0, {'atk_scale': 2.5}, SkillTrigger.DEPLOYMENT, 0, True),
    ('After deployment, immediately deals {atk_scale:0%} ATK and binds them', 10, {'atk_scale': 2.5},
     SkillTrigger.DEPLOYMENT, 0, True),
    # Started by something else (Fang the Fire-Sharpened, Swire the Elegant Wit, Iana).
    ('ATK +{atk:0%}, DEF +{def:0%}', 20, {'atk': 0.5, 'def': 0.5}, SkillTrigger.OTHER, 20, False),
    ('Spends 1 Coin: the next attack restores HP', -1, {}, SkillTrigger.OTHER, 0, True),
    ('When switching to a Substitute, immediately deals {atk_scale:0%}', -1, {'atk_scale': 2}, SkillTrigger.OTHER, 0, True),
])
def test_passive_triggers(description, duration, blackboard, trigger, expected_duration, instant):
    skill = _passive(description, duration, blackboard)
    assert skill.passive
    assert (skill.trigger, skill.duration, skill.instant) == (trigger, expected_duration, instant)


def test_passive_started_by_something_else_is_partial():
    skill = _passive('ATK +{atk:0%}', 20, {'atk': 0.5})
    assert skill.assumed == ['started by something other than SP or deployment, so its rotation is unknown']
    assert skill.status == SkillStatus.PARTIAL


def test_passive_with_sp_type_8_but_no_passive_type():
    skill = _parse(make_skill_level('DEF +{def:0%}', {'def': 0.3}, sp_type=8, duration=0))
    assert skill.passive and skill.trigger == SkillTrigger.ALWAYS


def test_ammo_skills_keep_zero_duration_and_count_ammo():
    skill = _parse(make_skill_level('Fires {attack@trigger_time} arrows', {'attack@trigger_time': 10},
                                    duration=-1, duration_type='AMMO'))
    assert (skill.ammo, skill.duration, skill.instant) == (10, 0, False)


def test_ammo_skill_without_an_ammo_count_is_partial():
    skill = _parse(make_skill_level('ATK +{atk:0%}', {'atk': 0.5}, duration_type='AMMO'))
    assert skill.ammo is None
    assert skill.unmodelled == ['ammo count']
    assert skill.status == SkillStatus.PARTIAL


# parse_skill: stat changes

def test_stat_values_use_percent_or_flat_from_the_template():
    skill = _parse(make_skill_level('ATK +{atk:0%}, DEF +{def}, Max HP +{max_hp:0%}, RES +{magic_resistance}, '
                                    'ASPD +{attack_speed}, Block +{block_cnt}',
                                    {'atk': 0.7, 'def': 75, 'max_hp': 0.5, 'magic_resistance': 15, 'attack_speed': 60,
                                     'base_attack_time': -0.2, 'block_cnt': 1}))
    assert skill.modifiers == StatModifiers(atk_percent=0.7, def_flat=75, hp_percent=0.5, res_flat=15, attack_speed=60,
                                            attack_interval_change=-0.2, block=1)
    assert skill.assumed == []


@pytest.mark.parametrize('key, value, field_name', [
    ('atk', 1.4, 'atk_percent'), ('atk', 300, 'atk_flat'), ('def', 3.0, 'def_percent'), ('def', 75, 'def_flat'),
    ('max_hp', 0.75, 'hp_percent'), ('max_hp', 5000, 'hp_flat'), ('magic_resistance', 0.5, 'res_percent'),
    ('magic_resistance', 20, 'res_flat'),
])
def test_stat_values_missing_from_the_text_are_guessed_by_size(key, value, field_name):
    skill = _parse(make_skill_level('Something happens', {key: value}))
    assert getattr(skill.modifiers, field_name) == value
    kind = 'a percentage' if field_name.endswith('percent') else 'flat'
    assert skill.assumed == [f'{key} {value:+g} assumed to be {kind}']
    assert skill.status == SkillStatus.PARTIAL


def test_enemy_and_ally_values_are_not_applied_to_the_unit():
    skill = _parse(make_skill_level("Targets' DEF -{-def}; allies gain ASPD +{attack_speed}; enemies' ASPD -{-cost}",
                                    {'def': -200, 'attack_speed': 30}))
    assert skill.modifiers.is_empty()
    assert [(effect.key, effect.value, effect.target) for effect in skill.enemy_effects] == [('def', -200, Target.ENEMY)]
    assert skill.notes == ['buffs allies: attack_speed +30']


def test_enemy_and_ally_attack_values_are_recorded_too():
    skill = _parse(make_skill_level("Enemies' ASPD {attack_speed}; Allies' Block +{block_cnt}",
                                    {'attack_speed': -30, 'block_cnt': 1}))
    assert skill.modifiers.is_empty()
    assert [effect.key for effect in skill.enemy_effects] == ['attack_speed']
    assert skill.notes == ['buffs allies: block_cnt +1']


def test_ally_stat_value_is_a_note():
    skill = _parse(make_skill_level('Allies gain ATK +{atk:0%}', {'atk': 0.3}))
    assert skill.notes == ['buffs allies: atk +0.3']
    assert skill.modifiers.is_empty()


def test_stat_modifiers_add_and_report_empty():
    total = StatModifiers(atk_percent=0.5, attack_speed=10) + StatModifiers(atk_percent=0.25, block=1)
    assert total == StatModifiers(atk_percent=0.75, attack_speed=10, block=1)
    assert StatModifiers().is_empty()
    assert not total.is_empty()


# apply_stat_value(s): shared with talents

def _value(key, value, is_percent=None, target=Target.SELF):
    return BlackboardValue(key, key, value, is_percent, target)


@pytest.mark.parametrize('key, expected', [
    ('atk', True), ('magic_resistance', True), ('attack_speed', True), ('block_cnt', True),
    ('atk_scale', False), ('stun', False),
])
def test_is_stat_value(key, expected):
    assert is_stat_value(_value(key, 1)) == expected


def test_apply_stat_values_combines_and_skips_other_values():
    effects = apply_stat_values([
        _value('atk', 0.1, True), _value('atk', 20, False), _value('attack_speed', 8), _value('atk_scale', 1.5),
        _value('def', -0.2, True, Target.ENEMY), _value('max_hp', 0.1, True, Target.ALLY),
    ])
    assert effects.modifiers == StatModifiers(atk_percent=0.1, atk_flat=20, attack_speed=8)
    assert [value.key for value in effects.enemy_effects] == ['def']
    assert effects.notes == ['buffs allies: max_hp +0.1']
    assert effects.assumed == []


def test_apply_stat_values_scales_own_values_only():
    effects = apply_stat_values([_value('attack_speed', 2), _value('def', 0.05), _value('atk', -0.2, True, Target.ENEMY)],
                                scale=2.5)
    assert effects.modifiers == StatModifiers(attack_speed=5, def_percent=0.125)
    assert effects.enemy_effects[0].value == -0.2
    assert effects.assumed == ['def +0.05 assumed to be a percentage']


def test_apply_stat_values_of_nothing():
    assert apply_stat_values([]) == StatEffects()


def test_apply_stat_value_adds_to_any_collector():
    skill = _parse(make_skill_level('ATK +{atk:0%}', {'atk': 0.5}))
    apply_stat_value(skill, _value('atk', 0.25, True))
    assert skill.modifiers.atk_percent == 0.75


# parse_skill: notes and unmodelled values

def test_note_values_are_understood():
    skill = _parse(make_skill_level('Stuns for {stun} s', {'stun': 1.5, 'cost': 6, 'range_id': 1}))
    assert skill.notes == ['stuns for 1.5 s', 'DP +6', 'changes attack range']
    assert skill.unmodelled == []
    assert skill.status == SkillStatus.FULL


def test_unknown_values_are_listed():
    skill = _parse(make_skill_level('ATK +{atk:0%}', {'atk': 0.5, 'mystery': 3, 'attack@prob': 0.2}))
    assert skill.unmodelled == ['mystery', 'attack@prob']
    assert skill.status == SkillStatus.PARTIAL


def test_skill_with_nothing_modelled():
    assert _parse(make_skill_level('Summons a drone', {'mystery': 3})).status == SkillStatus.NONE
    assert _parse(make_skill_level('Range expands', {})).status == SkillStatus.NONE


def test_charges_and_heal_scale():
    skill = _parse(make_skill_level('Heals {heal_scale:0%}; Can store {ct} charges', {'heal_scale': 1.5, 'ct': 3}))
    assert (skill.heal_scale, skill.charges) == (1.5, 3)
    assert skill.notes == ['stores up to 3 charges']
    assert skill.status == SkillStatus.FULL


def test_stops_attacking():
    skill = _parse(make_skill_level('DEF +{def:0%}, stops attacking', {'def': 0.5}))
    assert skill.stops_attacking
    assert _parse(make_skill_level('Cannot attack while active', {})).stops_attacking
    assert _parse(make_skill_level('Unable to attack', {})).status == SkillStatus.FULL


def test_enemy_effect_alone_counts_as_modelled():
    assert _parse(make_skill_level("Targets' DEF -{-def}", {'def': -100})).status == SkillStatus.FULL


# parse_skill: damage

def test_damage_types_follow_each_multiplier():
    skill = _parse(make_skill_level('Deals {atk_scale:0%} of ATK Physical damage and {atk_scale:0%} of ATK Arts damage '
                                    'to at most {max_target} enemies', {'atk_scale': 4.1, 'max_target': 6}))
    six = Targets(6, 6.0, False, 'hits up to 6 enemies')
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 4.1, 1.0, six), DamagePart(DamageType.ARTS, 4.1, 1.0, six)]


def test_untyped_multiplier_uses_the_operators_damage_type():
    skill = _parse(make_skill_level('Shoots {times} times, dealing {atk_scale:0%} of ATK damage', {'atk_scale': 1.2, 'times': 3}),
                   damage_type=DamageType.ARTS)
    assert skill.damage_parts == [DamagePart(DamageType.ARTS, 1.2, 3, ONE)]


def test_multiplier_missing_from_the_text_is_one_part():
    skill = _parse(make_skill_level('Shoots {attack@times} times', {'attack@atk_scale': 1.1, 'attack@times': 5}))
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.1, 5, ONE)]


def test_damage_type_named_after_a_later_clause_is_not_used():
    skill = _parse(make_skill_level('Deals {atk_scale:0%} damage; deals True damage to self', {'atk_scale': 2.0}))
    assert skill.damage_parts[0].damage_type == DamageType.PHYSICAL


def test_per_second_damage():
    skill = _parse(make_skill_level('Deals {atk_scale:0%} ATK as Arts damage to enemies every second, and RES -{-r}',
                                    {'atk_scale': 1.4}))
    assert skill.damage_parts[0].per_second
    assert not _parse(make_skill_level('Deals {atk_scale:0%}', {'atk_scale': 1.4})).damage_parts[0].per_second


def test_counter_damage_is_not_an_attack_multiplier():
    skill = _parse(make_skill_level('DEF +{def:0%}\nDeals {atk_scale:0%} of ATK to targets that attack this unit',
                                    {'atk_scale': 0.8, 'def': 0.2}))
    assert skill.damage_parts == []
    assert skill.unmodelled == ['atk_scale (counter damage)']


def test_aoe_wording_only_counts_in_the_multipliers_clause():
    skill = _parse(make_skill_level('Fires arrows that deal {atk_scale:0%} ATK. They explode, hitting nearby enemies',
                                    {'atk_scale': 2.4}))
    assert skill.damage_parts[0].targets == ONE
    skill = _parse(make_skill_level('Deals {atk_scale:0%} ATK to all enemies hit', {'atk_scale': 3.5}))
    assert skill.damage_parts[0].targets == AOE


def test_attacks_change_without_a_multiplier():
    skill = _parse(make_skill_level('Shoots {times} times', {'times': 4}))
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.0, 4, ONE)]
    targets = _parse(make_skill_level('Attacks {max_target} targets', {'max_target': 5})).damage_parts[0].targets
    assert targets.count == 5
    skill = _parse(make_skill_level('Cuts all targets in front', {}))
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.0, 1.0, AOE)]
    assert _parse(make_skill_level('ATK +{atk:0%}', {'atk': 0.5})).damage_parts == []


def test_skill_keeps_basic_targets_when_it_doesnt_change_them():
    blocked = Targets(3, 3.0, False, 'attacks all blocked enemies (block 3)')
    skill = _parse(make_skill_level('Increases the ATK of next attack to {atk_scale:0%}', {'atk_scale': 2.9}), basic=blocked)
    assert skill.damage_parts[0].targets == blocked


@pytest.mark.parametrize('description, expected', [
    ('damage type changes to True', DamageType.TRUE),
    ('Normal attacks deal Arts damage', DamageType.ARTS),
    ('Attacks now deal Physical damage', DamageType.PHYSICAL),
])
def test_whole_attack_damage_type_change(description, expected):
    skill = _parse(make_skill_level(description, {}), damage_type=DamageType.ARTS)
    assert skill.damage_type == expected
    assert skill.damage_parts == [DamagePart(expected, 1.0, 1.0, ONE)]


# real skills (tests/fixtures/skills), all at skill level 7 mastery 3 unless noted

def test_real_blaze_s2_stays_on():
    skill = _real('blaze_s2', basic=Targets(3, 3.0))
    assert (skill.name, skill.duration, skill.sp_cost) == ('Chainsaw Extension Module', None, 70)
    assert skill.skill_type == SkillType.AUTO
    assert skill.modifiers == StatModifiers(atk_percent=1.0, def_percent=0.35)
    assert skill.damage_parts == []
    assert skill.status == SkillStatus.FULL


def test_real_blaze_s2_skill_level_7_without_mastery():
    assert _real('blaze_s2', mastery=0).modifiers == StatModifiers(atk_percent=0.7, def_percent=0.2)


def test_real_chen_s2_is_an_extra_strike_of_two_damage_types():
    skill = _real('chen_s2')
    assert (skill.instant, skill.replaces_attack, skill.sp_type, skill.sp_cost, skill.initial_sp) == (
        True, False, 'INCREASE_WHEN_ATTACK', 20, 20)
    seven = Targets(7, 7.0, False, 'hits up to 7 enemies')
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 5, 1.0, seven), DamagePart(DamageType.ARTS, 5, 1.0, seven)]
    assert skill.status == SkillStatus.FULL


def test_real_chen_s3_ten_slashes():
    skill = _real('chen_s3')
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 3.2, 10, ONE)]
    assert skill.notes == ['stuns for 4 s']


def test_real_exusiai_s3_values_outside_the_text():
    skill = _real('exusiai_s3')
    assert (skill.duration, skill.sp_cost, skill.initial_sp) == (15, 30, 20)
    assert skill.modifiers == StatModifiers(attack_interval_change=-0.11)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.1, 5, ONE)]
    assert skill.status == SkillStatus.FULL


def test_real_ifrit_s2_charges_and_enemy_def():
    skill = _real('ifrit_s2', damage_type=DamageType.ARTS, basic=AOE)
    assert (skill.instant, skill.replaces_attack, skill.duration, skill.charges) == (True, True, 0, 3)
    assert skill.damage_parts == [DamagePart(DamageType.ARTS, 2.5, 1.0, AOE)]
    assert [(effect.key, effect.value) for effect in skill.enemy_effects] == [('def', -300)]
    assert skill.unmodelled == ['burn.atk_scale']
    assert skill.status == SkillStatus.PARTIAL


def test_real_ifrit_s3_damage_every_second_and_enemy_res():
    skill = _real('ifrit_s3', damage_type=DamageType.ARTS, basic=AOE)
    assert skill.damage_parts == [DamagePart(DamageType.ARTS, 1.4, 1.0, AOE, per_second=True)]
    assert [(effect.key, effect.value, effect.is_percent) for effect in skill.enemy_effects] == [('magic_resistance', -20, False)]
    assert skill.unmodelled == ['hp_ratio']


def test_real_amiya_s3_changes_to_true_damage():
    skill = _real('amiya_s3', damage_type=DamageType.ARTS)
    assert skill.damage_type == DamageType.TRUE
    assert skill.modifiers == StatModifiers(atk_percent=2.3, hp_percent=1.0)
    assert skill.damage_parts == [DamagePart(DamageType.TRUE, 1.0, 1.0, ONE)]


def test_real_hoshiguma_s2_passive_with_counter_damage():
    skill = _real('hoshiguma_s2')
    assert skill.passive and skill.duration is None
    assert skill.trigger == SkillTrigger.ALWAYS
    assert skill.modifiers == StatModifiers(def_percent=0.3)
    assert skill.unmodelled == ['atk_scale (counter damage)']


def test_real_toddifons_s2_ammo():
    skill = _real('toddifons_s2')
    assert (skill.ammo, skill.duration, skill.instant) == (10, 0, False)
    assert skill.modifiers == StatModifiers(attack_interval_change=0.3)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 2.4, 1.0, ONE)]
    assert skill.unmodelled == ['attack@splash_atk_scale', 'attack@splash_projectile_delay_time']


def test_real_silverash_s2_toggle_and_capitalised_placeholder():
    skill = _real('silverash_s2')
    assert skill.duration is None and not skill.instant
    assert skill.modifiers == StatModifiers(def_percent=1.0)
    assert 'Restores HP by 6.0% of Max HP per second' in skill.description


def test_real_silverash_s3_lowers_own_def_and_hits_six():
    skill = _real('silverash_s3')
    assert (skill.duration, skill.initial_sp) == (30, 75)
    assert skill.modifiers == StatModifiers(atk_percent=2.0, def_percent=-0.7)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.0, 1.0, Targets(6, 6.0, False, 'hits up to 6 enemies'))]


def test_real_w_s1_immediate_grenade_is_instant():
    skill = _real('w_s1')
    assert (skill.instant, skill.replaces_attack, skill.duration) == (True, False, 0)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 3.5, 1.0, AOE)]


def test_real_kroos_s1_has_no_masteries():
    skill = _real('kroos_s1', mastery=0)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 1.4, 2, ONE)]
    with pytest.raises(ValueError, match="This skill has 7 levels"):
        _real('kroos_s1', mastery=1)


def test_real_projekt_red_s1_lasts_10_seconds_after_deployment():
    skill = _real('projekt_red_s1')
    assert (skill.trigger, skill.duration, skill.instant) == (SkillTrigger.DEPLOYMENT, 10, False)
    assert skill.modifiers == StatModifiers(atk_percent=0.8)
    # The 50% dodge chance isn't modelled yet.
    assert skill.unmodelled == ['prob']


def test_real_projekt_red_s2_one_off_hit_on_deployment():
    skill = _real('projekt_red_s2')
    assert (skill.trigger, skill.duration, skill.instant, skill.replaces_attack) == (SkillTrigger.DEPLOYMENT, 0, True, False)
    assert skill.damage_parts == [DamagePart(DamageType.PHYSICAL, 2.5, 1.0, AOE)]
    assert skill.notes == ['stuns for 3 s']


def test_real_liskarm_s2_gains_sp_when_hit():
    skill = _real('liskarm_s2')
    assert (skill.trigger, skill.sp_type, skill.sp_cost, skill.duration) == (
        SkillTrigger.SP, 'INCREASE_WHEN_TAKEN_DAMAGE', 34, 20)
    assert skill.modifiers == StatModifiers(atk_percent=2.0, attack_interval_change=0.7)
