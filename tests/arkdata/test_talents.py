import pytest

from arkdata.calculations import talents as talents_module
from arkdata.calculations.skills import Penetration, StatModifiers
from arkdata.calculations.talents import (
    ATK_MULTIPLIER,
    DAMAGE_MULTIPLIER,
    UNKNOWN_STACK_FRACTION,
    Talent,
    TalentCondition,
    TalentGroup,
    TalentOverride,
    average_stacks,
    build_talent,
    build_talents,
    clause_condition,
    clause_group,
    find_value,
    group_name,
    load_talent_overrides,
    member_groups,
    multiplier_kind,
    parse_condition,
    parse_talent_overrides,
    shared_group,
    talent_key,
    unlocked_candidate,
)
from factories import load_talent_fixture, make_talent_candidate

NOT_RECOGNISED = 'condition not recognised, so not applied (review)'


def _talent(description, blackboard=None, number=1, overrides=None, **kwargs):
    candidate = make_talent_candidate(description, blackboard)
    return build_talent(number, candidate, overrides={} if overrides is None else overrides, **kwargs)


def _real(name, phase=2, level=1, potential=5, **kwargs):
    candidate = unlocked_candidate(load_talent_fixture(name), phase, level, potential)
    return build_talent(1, candidate, overrides={}, **kwargs)


# talent_key

@pytest.mark.parametrize('raw_key, expected', [
    ('atk', 'atk'),
    ('attack@prob', 'prob'),
    ('talent@damage_scale', 'damage_scale'),
    ('skadi2_t_2[atk][1].atk', 'atk'),
    ('halo2_t_1[weak].attack@damage_scale', 'damage_scale'),
])
def test_talent_key(raw_key, expected):
    assert talent_key(raw_key) == expected


# unlocked_candidate

def _versions():
    return {'candidates': [
        make_talent_candidate('one', phase='PHASE_1', level=1, potential=0, name='E1'),
        make_talent_candidate('two', phase='PHASE_1', level=1, potential=4, name='E1 P5'),
        make_talent_candidate('three', phase='PHASE_2', level=1, potential=0, name='E2'),
        make_talent_candidate('four', phase='PHASE_2', level=1, potential=4, name='E2 P5'),
    ]}


@pytest.mark.parametrize('phase, level, potential, expected', [
    (0, 50, 5, None),
    (1, 1, 0, 'E1'),
    (1, 80, 4, 'E1 P5'),
    (2, 1, 3, 'E2'),
    (2, 90, 5, 'E2 P5'),
])
def test_unlocked_candidate_follows_phase_level_and_potential(phase, level, potential, expected):
    candidate = unlocked_candidate(_versions(), phase, level, potential)
    assert (candidate['name'] if candidate else None) == expected


def test_unlocked_candidate_needs_the_level_within_the_phase():
    talent = {'candidates': [make_talent_candidate(phase='PHASE_1', level=30)]}
    assert unlocked_candidate(talent, 1, 29, 0) is None
    assert unlocked_candidate(talent, 1, 30, 0) is not None


def test_unlocked_candidate_without_conditions_or_candidates():
    assert unlocked_candidate({'candidates': [{'name': 'Free'}]}, 0, 1, 0) == {'name': 'Free'}
    assert unlocked_candidate({'candidates': None}, 2, 90, 5) is None
    assert unlocked_candidate({}, 2, 90, 5) is None


# find_value

@pytest.mark.parametrize('text, value, key, expected', [
    ('ATK +8%', 0.08, 'atk', (True, 5)),
    ('ATK +18%, DEF +8%', 0.08, 'def', (True, 15)),
    ('ASPD +15', 15, 'attack_speed', (False, 6)),
    ('lasts 1.5 seconds, ASPD +15', 15, 'attack_speed', (False, 25)),
    ('ASPD +15%', 15, 'attack_speed', (None, None)),
    ('deal 160% damage', 1.6, 'atk_scale', (True, 5)),
    ('gain 15% Fragile', 1.15, 'damage_scale', (True, 5)),
    ('RES -44%', -0.44, 'magic_resistance', (True, 5)),
    ('nothing here', 0.3, 'atk', (None, None)),
])
def test_find_value(text, value, key, expected):
    assert find_value(text, value, key) == expected


def test_find_value_only_adds_the_bonus_form_for_multipliers():
    assert find_value('ATK +15%', 1.15, 'atk') == (None, None)
    assert find_value('ATK +15%', 1.15, 'atk_scale') == (True, 5)


# clause_group

@pytest.mark.parametrize('clause, expected', [
    ('ATK +8%', (TalentGroup.ALWAYS, None)),
    ('Attacks ignore 23% DEF', (TalentGroup.ALWAYS, None)),
    ('Each attack gives ATK +2%', (TalentGroup.LIKELY, None)),
    ('When attacking, has a 20% chance to deal 160% damage', (TalentGroup.LIKELY, None)),
    ('After being deployed, ATK +10% for 20 seconds', (TalentGroup.LIKELY, None)),
    ('While the skill is active, DEF +20%', (TalentGroup.LIKELY, None)),
    ('When HP is below 50%, ATK +20%', (TalentGroup.LIKELY, None)),
    ('Increases Max HP by 50 after defeating an enemy, stacking up to 30 times', (TalentGroup.LIKELY, None)),
    ('When inflicting Slow on an enemy, +1 ASPD', (TalentGroup.LIKELY, None)),
    ('For every 20 seconds Saria is deployed, ATK +6%', (TalentGroup.LIKELY, None)),
    ('Gains +4 ASPD for every 15 seconds this unit remains on the battlefield', (TalentGroup.LIKELY, None)),
    ('When attacking aerial units, ATK +20%', (TalentGroup.PER_ENEMY, 'aerial')),
    ('Increases ATK to 123% when attacking unblocked enemies', (TalentGroup.PER_ENEMY, 'unblocked')),
    ('DEF +10% for each blocked enemy', (TalentGroup.PER_ENEMY, 'blocked')),
    ('Deals 120% damage to Elite and Leader enemies', (TalentGroup.PER_ENEMY, 'elite')),
    ('When attacking Sarkaz enemies, increase ATK to 150%', (TalentGroup.PER_ENEMY, 'enemy type')),
    ('Increases ATK to 150% when attacking [Infected Creatures]', (TalentGroup.PER_ENEMY, 'enemy type')),
    ('When there are 3 [Abyssal Hunter] Operators, ATK +10%', (TalentGroup.UNLIKELY, None)),
    ('Allies on adjacent tiles gain +10% ATK', (TalentGroup.UNLIKELY, None)),
    ('When not taking damage, every 7s restores HP', (None, None)),
    ('If the target is stunned, ATK +40%', (None, None)),
    ('Each attack will randomly trigger one of the following effects: that attack deals 150% damage', (None, None)),
    ('Prioritize attacking drones; ATK +43% when attacking drones', (TalentGroup.PER_ENEMY, 'aerial')),
    ('Increases ATK to 125% when hitting an enemy with full HP', (TalentGroup.PER_ENEMY, 'enemy HP')),
    ('When attacking enemies with 80% or more HP, deals 25% increased damage', (TalentGroup.PER_ENEMY, 'enemy HP')),
    ('When attacking enemies with 3 or less weight, increase ATK to 136%', (TalentGroup.PER_ENEMY, 'weight')),
    ('Deals 20% more damage to Machine enemies', (TalentGroup.PER_ENEMY, 'enemy type')),
    ('Deals 22% more Physical damage to ground enemies', (TalentGroup.PER_ENEMY, 'enemy type')),
    ('Deals 150% damage to all enemies', (TalentGroup.ALWAYS, None)),
    ('Deals 150% damage to nearby enemies', (TalentGroup.ALWAYS, None)),
    ('Immediately deals 380% ATK as Elemental Damage to that enemy', (TalentGroup.ALWAYS, None)),
    ('Click and her Drones gain +12 ASPD', (TalentGroup.ALWAYS, None)),
])
def test_clause_group(clause, expected):
    assert clause_group(clause) == expected


def test_per_enemy_conditions_win_over_likely_ones():
    # "When attacking" alone is LIKELY, but the enemy it needs decides.
    assert clause_group('When attacking blocked enemies, ATK +10%') == (TalentGroup.PER_ENEMY, 'blocked')


# average_stacks

@pytest.mark.parametrize('max_stacks, seconds_per_stack, window, expected', [
    # Reaches 10 stacks at 10 s, then holds them: (10 * 5 + 10 * 10) / 20.
    (10, 1.0, 20, 7.5),
    # Still building up after 4 s: 4 stacks reached, 2 on average.
    (10, 1.0, 4, 2.0),
    # Exactly full at the end of the window.
    (5, 2.0, 10, 2.5),
    (10, None, 20, None),
    (10, 1.0, None, None),
    (10, 1.0, 0, None),
    (10, 0.0, 20, None),
])
def test_average_stacks(max_stacks, seconds_per_stack, window, expected):
    assert average_stacks(max_stacks, seconds_per_stack, window) == (None if expected is None else pytest.approx(expected))


# build_talent: groups and targets

def test_always_on_stats_are_applied():
    talent = _talent('ATK +8%; DEF +60', {'atk': 0.08, 'def': 60})
    assert talent.group == TalentGroup.ALWAYS and talent.applied
    assert (talent.modifiers.atk_percent, talent.modifiers.def_flat) == (0.08, 60)
    assert talent.factor == 1.0 and talent.status == 'full'


def test_identity_is_kept():
    talent = build_talent(2, make_talent_candidate('<@ba.kw>ATK</> +8%', {'atk': 0.08}, name='Sharp'), overrides={})
    assert (talent.number, talent.name, talent.description) == (2, 'Sharp', 'ATK +8%')


def test_the_least_likely_clause_decides_the_group():
    talent = _talent('ATK +10%; when attacking aerial units, ASPD +20', {'atk': 0.1, 'attack_speed': 20})
    assert (talent.group, talent.condition) == (TalentGroup.PER_ENEMY, TalentCondition('aerial'))
    assert not talent.applied
    # Worked out anyway, for matchups.
    assert talent.modifiers.attack_speed == 20


def test_unrecognised_conditions_are_unlikely_and_flagged():
    talent = _talent('If the target is stunned, ATK +40%', {'atk': 0.4})
    assert talent.group == TalentGroup.UNLIKELY and not talent.applied
    assert NOT_RECOGNISED in talent.assumed
    assert talent.status == 'partial'


def test_talent_without_effects_takes_its_group_from_the_whole_text():
    talent = _talent('Restores 2 extra Skill Points every 5 seconds', {'sp': 2, 'interval': 5})
    assert talent.group == TalentGroup.LIKELY
    assert talent.notes == ['SP +2', 'every 5 s']


def test_enemy_only_talent_takes_its_group_from_the_enemy_effects():
    talent = _talent('Enemies within attack range gain 12% Fragile, increasing to 16% after being in attack range for '
                     'more than 7 seconds', {'damage_scale': 1.12, 'damage_scale_max': 1.16})
    assert talent.group == TalentGroup.LIKELY
    assert [value.key for value in talent.enemy_effects] == ['damage_scale']
    assert talent.multiplier == 1.0
    assert talent.unmodelled == ['damage_scale_max']


def test_condition_on_an_enemy_before_the_comma_doesnt_make_the_value_an_enemys():
    talent = _talent('Before defeating an enemy for the first time, gains +10 ASPD', {'attack_speed': 10})
    assert talent.modifiers.attack_speed == 10
    assert talent.group == TalentGroup.LIKELY


@pytest.mark.parametrize('description, blackboard', [
    ('RES of enemies within range -44%', {'magic_resistance': -0.44}),
    ('Attacks inflict 14% [Arts Fragility] to the target for 5 seconds', {'damage_scale': 1.14}),
    ('When dealing damage, inflict Cinder: ATK -22%, 32% [Arts Fragility]', {'atk': -0.22, 'damage_scale': 1.32}),
])
def test_enemy_effects_are_recorded_not_applied(description, blackboard):
    talent = _talent(description, blackboard)
    assert [value.key for value in talent.enemy_effects] == list(blackboard)
    assert talent.modifiers.is_empty() and talent.multiplier == 1.0


@pytest.mark.parametrize('description', ['[Abyssal Hunter] Operators gain ATK +10%', 'Ave Mujica members gain ATK +10%'])
def test_ally_buffs_are_notes(description):
    talent = _talent(description, {'atk': 0.1})
    assert talent.notes == ['buffs allies: atk +0.1']
    assert talent.modifiers.is_empty()


def test_penetration_is_an_enemy_effect():
    talent = _talent('Attacks ignore 175 DEF', {'def_penetrate_fixed': 175})
    assert [value.key for value in talent.enemy_effects] == ['def_penetrate_fixed']
    assert talent.status == 'full'


@pytest.mark.parametrize('key, value', [('atk', 1), ('attack_speed', 10), ('damage_scale', 1.5)])
def test_effects_missing_from_the_text_are_unmodelled(key, value):
    # Suzuran's talent 2 has an "atk" of 1 that isn't in its description.
    talent = _talent('Enemies in range that are Slowed are inflicted with 23% Fragile', {'atk_scale': 1.23, key: value})
    assert talent.modifiers.is_empty() and talent.multiplier == 1.0
    assert [value.key for value in talent.enemy_effects] == ['atk_scale']
    assert talent.unmodelled == [f'{key} (not in the description)']
    assert talent.group == TalentGroup.ALWAYS


# build_talent: duplicates, notes and unknown values

def test_duplicate_values_with_the_same_value_are_ignored_silently():
    talent = _talent('ATK +10%', {'atk': 0.1, 'talent@atk': 0.1})
    assert talent.modifiers.atk_percent == 0.1 and talent.assumed == []


def test_duplicate_values_with_a_different_value_are_flagged():
    talent = _talent('ATK +10%', {'atk': 0.1, 'map[x].atk': 0.2})
    assert talent.modifiers.atk_percent == 0.1
    assert talent.assumed == ['map[x].atk ignored (another version of atk)']


def test_values_without_a_number_are_skipped():
    candidate = make_talent_candidate('ATK +10%', {'atk': 0.1})
    candidate['blackboard'].append({'key': 'mystery', 'value': None, 'valueStr': 'text'})
    talent = build_talent(1, candidate, overrides={})
    assert talent.unmodelled == [] and talent.status == 'full'


def test_unknown_values_are_unmodelled():
    talent = _talent('ATK +10%; does something else', {'atk': 0.1, 'mystery': 3})
    assert talent.unmodelled == ['mystery']
    assert talent.status == 'partial'


def test_talent_with_nothing_modelled():
    talent = _talent('Can use a Seaborn', {'mystery': 3})
    assert talent.status == 'none'


# build_talent: chances

def test_dodge_chance_is_a_note():
    talent = _talent('ATK +6%; Physical Dodge +13%', {'atk': 0.06, 'prob': 0.13})
    assert talent.notes == ['13% dodge chance']
    assert talent.factor == 1.0 and talent.modifiers.atk_percent == 0.06


@pytest.mark.parametrize('wording', ['and strike an additional target', 'and strikes an extra time',
                                     'and hits another target'])
def test_boosted_attack_that_strikes_again_deals_the_multiplier_twice(wording):
    # Bagpipe's talent 1: 75% of attacks deal 100%, 25% deal two strikes of 130%: 0.75 + 0.25 × 2.6 = 1.4.
    talent = _talent(f'When attacking, has a 25% chance to increase ATK to 130% {wording}',
                     {'prob': 0.25, 'atk_scale': 1.3})
    assert talent.multiplier == pytest.approx(1.4)
    assert talent.notes == ['the boosted attack strikes twice']


def test_chance_of_a_damage_multiplier_is_its_expected_value():
    talent = _talent('When attacking, has a 20% chance to deal 160% damage', {'prob': 0.2, 'atk_scale': 1.6})
    assert talent.factor == 0.2
    assert talent.multiplier == pytest.approx(1.12)


def test_chance_of_a_stat_scales_the_stat():
    talent = _talent('When attacking, 25% chance to gain ATK +40%', {'prob': 0.25, 'atk': 0.4})
    assert talent.modifiers.atk_percent == pytest.approx(0.1)


def test_chance_only_scales_the_effects_in_its_part_of_the_clause():
    # GreyThroat: the ASPD is always on, only the ATK multiplier has the chance.
    talent = _talent('ASPD +6, 15% chance to increase ATK to 150% when attacking', {'attack_speed': 6, 'prob': 0.15,
                                                                                   'atk_scale': 1.5})
    assert talent.modifiers.attack_speed == 6
    assert talent.multiplier == pytest.approx(1.075)
    assert talent.factor == 0.15


def test_chance_mentioned_elsewhere_in_the_clause_scales_every_effect():
    talent = _talent('When attacking, has a 20% chance, gaining ATK +40% and ASPD +10', {'prob': 0.2, 'atk': 0.4,
                                                                                       'attack_speed': 10})
    assert talent.modifiers.atk_percent == pytest.approx(0.08)
    assert talent.modifiers.attack_speed == pytest.approx(2)


def test_numbered_chances_use_the_first():
    # Vigna: prob1 when attacking, prob2 while the skill is active (not modelled).
    talent = _talent('When attacking, has a 10% chance to increase the current damage of that attack by 110%. The '
                     'chance increases to 30% when the skill is active.', {'atk': 1.1, 'prob1': 0.1, 'prob2': 0.3})
    assert talent.modifiers.atk_percent == pytest.approx(0.11)
    assert talent.unmodelled == ['prob2']


def test_chance_of_something_unmodelled_is_a_note():
    talent = _talent('28% chance to resist damage', {'prob': 0.28})
    assert talent.notes == ['28% chance of an effect']
    assert talent.factor == 1.0


# build_talent: damage multipliers

@pytest.mark.parametrize('description, blackboard', [
    # Not on attacks.
    ('When deploying, deals 200% of ATK as Arts damage to nearby enemies', {'atk_scale': 2.0}),
    # A minimum, not a bonus.
    ('Deals at least 33% of ATK damage every attack', {'atk_scale': 0.33}),
])
def test_atk_scale_that_isnt_an_attack_multiplier(description, blackboard):
    talent = _talent(description, blackboard)
    assert talent.multiplier == 1.0
    assert talent.unmodelled == ['atk_scale (not an attack multiplier)']


def test_splash_only_multiplier_is_unmodelled():
    talent = _talent('Physical damage caused by Trait splash effect increases by 24%', {'damage_scale': 1.24})
    assert talent.multiplier == 1.0
    assert talent.unmodelled == ['damage_scale (splash damage only)']


def test_damage_scale_is_a_multiplier():
    talent = _talent('Increases damage dealt by 15%', {'damage_scale': 1.15})
    assert talent.multiplier == pytest.approx(1.15)


# build_talent: stacks

def test_stacks_per_attack_are_averaged_over_the_window():
    talent = _talent('Each attack gives ATK +2%, stacking up to 10 times', {'atk': 0.02, 'max_stack_cnt': 10},
                     attack_interval=1.0, window=20)
    assert talent.factor == pytest.approx(7.5)
    assert talent.modifiers.atk_percent == pytest.approx(0.15)
    assert talent.assumed == []


@pytest.mark.parametrize('description, attack_interval, expected', [
    # One stack every 2 attacks of 1 s: full at 10 s of a 20 s window.
    ('Every 2 attacks, ATK +2%, stacking up to 5 times', 1.0, (5 * 10 / 2 + 5 * 10) / 20),
    ('ATK +2% every 4 seconds, stacking up to 5 times', None, (5 * 20 / 2) / 20),
    ('ATK +2% every second, stacking up to 5 times', None, (5 * 5 / 2 + 5 * 15) / 20),
])
def test_stack_rates(description, attack_interval, expected):
    talent = _talent(description, {'atk': 0.02}, attack_interval=attack_interval, window=20)
    assert talent.factor == pytest.approx(expected)


def test_stacks_with_unknown_rate_assume_half():
    talent = _talent('Increases Max HP by 50 after defeating an enemy, stacking up to 30 times',
                     {'max_hp': 50, 'max_stack_cnt': 30}, attack_interval=1.0, window=60)
    assert talent.factor == 30 * UNKNOWN_STACK_FRACTION
    assert talent.modifiers.hp_flat == 750
    assert talent.assumed == ['stack rate unknown; assumed half of 30 stacks']


def test_stacks_per_attack_without_an_attack_interval_assume_half():
    talent = _talent('Each attack gives ATK +2%, stacking up to 10 times', {'atk': 0.02, 'max_stack_cnt': 10}, window=20)
    assert talent.factor == 5


def test_stacks_without_a_maximum_count_once():
    talent = _talent('Each attack gives ATK +2%, stacks', {'atk': 0.02}, attack_interval=1.0, window=20)
    assert talent.factor == 1.0
    assert talent.assumed == ['stacks with no maximum given; counted once']


# build_talent: overrides

def test_override_sets_the_group_factor_and_note():
    overrides = {('char_x', 1): TalentOverride(TalentGroup.ALWAYS, 0.5, 'tested in game')}
    talent = _talent('If the target is stunned, ATK +40%', {'atk': 0.4}, operator_id='char_x', overrides=overrides)
    assert talent.group == TalentGroup.ALWAYS and talent.overridden and talent.applied
    assert talent.factor == 0.5 and talent.modifiers.atk_percent == pytest.approx(0.2)
    assert 'overridden: tested in game' in talent.notes


def test_override_without_factor_or_note_keeps_the_factor():
    overrides = {('char_x', 2): TalentOverride(TalentGroup.UNLIKELY)}
    talent = _talent('ATK +10%', {'atk': 0.1}, number=2, operator_id='char_x', overrides=overrides)
    assert talent.group == TalentGroup.UNLIKELY and talent.factor == 1.0
    assert 'overridden' in talent.notes


def test_override_only_matches_its_operator_and_talent():
    overrides = {('char_x', 1): TalentOverride(TalentGroup.UNLIKELY)}
    assert not _talent('ATK +10%', {'atk': 0.1}, operator_id='char_y', overrides=overrides).overridden
    assert not _talent('ATK +10%', {'atk': 0.1}, number=2, operator_id='char_x', overrides=overrides).overridden


def test_build_talent_loads_the_override_file_by_default(monkeypatch):
    monkeypatch.setattr(talents_module, 'load_talent_overrides',
                        lambda: {('char_x', 1): TalentOverride(TalentGroup.UNLIKELY)})
    talent = build_talent(1, make_talent_candidate('ATK +10%', {'atk': 0.1}), operator_id='char_x')
    assert talent.overridden


# Talent

def test_talent_applied_by_group():
    assert [Talent(1, '', '', group).applied for group in TalentGroup] == [True, True, False, False]


# Overrides file

def test_parse_talent_overrides():
    text = ('operator_id,talent,group,factor,note\n'
            'char_1, 2 ,likely,0.5,checked\n'
            ',,,,\n'
            'char_2,1,PER_ENEMY,,\n')
    assert parse_talent_overrides(text) == {
        ('char_1', 2): TalentOverride(TalentGroup.LIKELY, 0.5, 'checked'),
        ('char_2', 1): TalentOverride(TalentGroup.PER_ENEMY, None, ''),
    }


def test_parse_talent_overrides_rejects_unknown_groups():
    with pytest.raises(ValueError, match="char_1 has unknown group 'sometimes'"):
        parse_talent_overrides('operator_id,talent,group,factor,note\nchar_1,1,sometimes,,\n')


def test_parse_talent_overrides_rejects_bad_numbers():
    with pytest.raises(ValueError):
        parse_talent_overrides('operator_id,talent,group,factor,note\nchar_1,one,ALWAYS,,\n')


def test_shipped_override_file_loads():
    load_talent_overrides.cache_clear()
    overrides = load_talent_overrides()
    assert isinstance(overrides, dict)
    assert all(isinstance(override, TalentOverride) for override in overrides.values())


# build_talents

def test_build_talents_numbers_and_skips():
    operator = {'talents': [
        {'candidates': [make_talent_candidate('ATK +10%', {'atk': 0.1}, name='First')]},
        {'candidates': [make_talent_candidate('Hidden', {'atk': 0.1}, hidden=True)]},
        {'candidates': [make_talent_candidate('', {'atk': 0.1})]},
        {'candidates': [make_talent_candidate('Later', {'atk': 0.1}, phase='PHASE_2')]},
        {'candidates': [make_talent_candidate('DEF +10%', {'def': 0.1}, name='Fifth')]},
    ]}
    talents = build_talents(operator, 1, 1, 0, overrides={})
    assert [(talent.number, talent.name) for talent in talents] == [(1, 'First'), (5, 'Fifth')]


def test_build_talents_passes_on_the_operator_and_stack_settings():
    operator = {'talents': [{'candidates': [make_talent_candidate(
        'Each attack gives ATK +2%, stacking up to 10 times', {'atk': 0.02, 'max_stack_cnt': 10})]}]}
    overrides = {('char_x', 1): TalentOverride(TalentGroup.UNLIKELY)}
    [talent] = build_talents(operator, 0, 1, 0, operator_id='char_x', attack_interval=1.0, window=20, overrides=overrides)
    assert talent.factor == pytest.approx(7.5) and talent.overridden


def test_build_talents_without_talents():
    assert build_talents({'talents': None}, 2, 90, 5, overrides={}) == []
    assert build_talents({}, 2, 90, 5, overrides={}) == []


# Real talents (trimmed HellaAPI data in tests/fixtures/talents)

def test_real_chen_t2_always_on_with_a_dodge_chance():
    talent = _real('chen_t2')
    assert talent.group == TalentGroup.ALWAYS
    assert (talent.modifiers.atk_percent, talent.modifiers.def_percent) == (0.06, 0.06)
    assert talent.notes == ['13% dodge chance']
    assert talent.status == 'full'
    # Before potential 5 (rank 4), the weaker version.
    assert _real('chen_t2', potential=3).modifiers.atk_percent == 0.05


def test_real_flamebringer_t1_per_kill_stacks():
    talent = _real('flamebringer_t1', attack_interval=1.25, window=60)
    assert talent.group == TalentGroup.LIKELY
    assert talent.modifiers.hp_flat == 50 * 15
    assert talent.assumed == ['stack rate unknown; assumed half of 30 stacks']
    assert _real('flamebringer_t1', phase=1, level=50, potential=0).modifiers.hp_flat == 20 * 15


def test_real_astgenne_alter_t1_per_slow_stacks():
    talent = _real('astgenne_alter_t1', attack_interval=1.6, window=40)
    assert talent.group == TalentGroup.LIKELY
    assert talent.modifiers.attack_speed == 9
    assert talent.assumed == ['stack rate unknown; assumed half of 18 stacks']


def test_real_toddifons_t1_per_enemy_type():
    talent = _real('toddifons_t1')
    assert (talent.group, talent.condition) == (TalentGroup.PER_ENEMY, TalentCondition('enemy type', tag='sarkaz'))
    assert not talent.applied
    assert talent.multiplier == 1.5


def test_real_kroos_alter_t1_chance_multiplier():
    talent = _real('kroos_alter_t1')
    assert talent.group == TalentGroup.LIKELY
    assert talent.multiplier == pytest.approx(1.12)
    assert talent.notes == ['stuns for 0.2 s']
    assert talent.status == 'full'


def test_real_reed_alter_t1_enemy_debuffs():
    talent = _real('reed_alter_t1')
    assert talent.group == TalentGroup.LIKELY
    assert [(value.key, value.value) for value in talent.enemy_effects] == [('atk', -0.22), ('damage_scale', 1.32)]
    assert talent.multiplier == 1.0 and talent.modifiers.is_empty()
    assert talent.notes == ['30% chance of an effect', 'effect lasts 6 s']
    # Potential 3 (rank 2) unlocks the stronger version.
    assert _real('reed_alter_t1', potential=1).enemy_effects[0].value == -0.2


# Buffs to a group the operator belongs to

VANGUARD = frozenset({'vanguard', 'melee', 'six-star', 'victoria', 'glasgow'})
HUNTER = frozenset({'guard', 'melee', 'six-star', 'egir', 'abyssal hunter', 'abyssal'})


@pytest.mark.parametrize('name, expected', [
    ('Vanguard', 'vanguard'), ('[Abyssal Hunters]', 'abyssal hunter'), ('Abyssal Hunter', 'abyssal hunter'),
    ("'Minoan'", 'mino'), ('Minos', 'mino'), ("Laios's Party", 'laioss party'), ('  Ave   Mujica ', 'ave mujica'),
    ('Ursus Student Self-government Group', 'ursus student self-government group'),
])
def test_group_name(name, expected):
    assert group_name(name) == expected


def test_member_groups():
    value = {'data': {'profession': 'PIONEER', 'position': 'MELEE', 'rarity': 'TIER_6'}, 'factions': [
        {'nationPower': {'powerId': 'egir', 'powerName': 'Ægir', 'powerCode': 'Ægir'},
         'groupPower': {'powerId': 'abyssal', 'powerName': 'Abyssal Hunters', 'powerCode': 'Abyssal Hunters'},
         'teamPower': None},
    ]}
    assert member_groups(value) == {'vanguard', 'melee', 'six-star', 'egir', 'ægir', 'abyssal', 'abyssal hunter'}
    assert member_groups({}) == frozenset()


@pytest.mark.parametrize('words_before, member_of, expected', [
    ("All Vanguard Operators' ATK and DEF +", VANGUARD, "All Vanguard Operators'"),
    ('When deployed, allied Vanguard Operators gain +', VANGUARD, ' allied Vanguard Operators'),
    ('When deployed, all [Vanguard] Operators gain +', VANGUARD, ' all [Vanguard] Operators'),
    ('When this unit is in the squad, all [Abyssal Hunter] Operators gain +', HUNTER,
     ' all [Abyssal Hunter] Operators'),
    ("All allies' ASPD +", VANGUARD, "All allies'"),
    ('All allied units on the battlefield gain +', VANGUARD, 'All allied units'),
    ('This unit and allied Operators in the surrounding 8 tiles gain +', VANGUARD, 'This unit and allied Operators'),
    ('Increases the ATK and DEF of all [Melee] allies by ', VANGUARD, 'Increases the ATK and DEF of all [Melee] allies'),
    # Not the operator's group, or limited by position or a condition.
    ("All Guard Operators' ATK +", VANGUARD, None),
    ('When deployed, all three-star Operators gain +', VANGUARD, None),
    ('DEF of allies within range +', VANGUARD, None),
    ('ATK of Melee allies in the 8 surrounding tiles +', VANGUARD, None),
    ('When deployed, Operators with ammo-type skills gain ATK +', VANGUARD, None),
    ('Within the surrounding 8 tiles of the banner, allied units have ASPD +', VANGUARD, None),
    ('When deployed, adjacent allies gain +', VANGUARD, None),
    ("All Vanguard Operators' ATK +", frozenset(), None),
    ('ATK +', VANGUARD, None),
])
def test_shared_group(words_before, member_of, expected):
    assert shared_group(words_before, member_of) == expected


def test_buff_to_the_operators_own_class_applies_to_it():
    # Siege's talent 1.
    talent = _talent("All Vanguard Operators' ATK and DEF +10%", {'atk': 0.1, 'def': 0.1}, member_of=VANGUARD)
    assert (talent.group, talent.applied) == (TalentGroup.ALWAYS, True)
    assert (talent.modifiers.atk_percent, talent.modifiers.def_percent) == (0.1, 0.1)
    assert talent.notes == ['also buffs All Vanguard Operators']
    assert talent.status == 'full'


def test_buff_to_another_class_stays_an_ally_buff():
    talent = _talent("All Guard Operators' ATK +10%", {'atk': 0.1}, member_of=VANGUARD)
    assert talent.group == TalentGroup.UNLIKELY and talent.modifiers.is_empty()
    assert talent.notes == ['buffs allies: atk +0.1']


@pytest.mark.parametrize('description, expected', [
    ('When deployed, all [Vanguard] Operators gain +10% ATK', TalentGroup.LIKELY),
    ('When this unit is in the squad, all Vanguard Operators gain +10% ATK', TalentGroup.ALWAYS),
    ('When skill is activated, Vanguard Operators gain +10% ATK for the duration', TalentGroup.LIKELY),
    ('This unit and allied Operators in the surrounding 8 tiles gain +10% ATK', TalentGroup.ALWAYS),
    ('When this unit and at least one more Vanguard Operator are on the battlefield, all Vanguard Operators gain '
     '+10% ATK', TalentGroup.UNLIKELY),
    ('When there are 4 [Glasgow] Operators in the squad, all [Glasgow] Operators gain +10% ATK', TalentGroup.UNLIKELY),
])
def test_shared_buff_conditions(description, expected):
    talent = _talent(description, {'atk': 0.1}, member_of=VANGUARD)
    assert talent.group == expected
    assert talent.modifiers.atk_percent == 0.1


def test_shared_buff_named_twice_in_the_clause():
    # Amiya (Medic): "all allied units" twice, so both are left out when judging the condition.
    talent = _talent('When deployed, all allied units have max HP +8%, and while skill is active, all allied units '
                     'recover 2.5% of max HP per second', {'max_hp': 0.08, 'hp_recovery_per_sec_by_max_hp_ratio': 0.025},
                     member_of=VANGUARD)
    assert talent.group == TalentGroup.LIKELY and talent.modifiers.hp_percent == 0.08


def test_faction_buffs_and_aliases():
    minos = frozenset({'guard', 'mino'})
    talent = _talent("When deployed, all 'Minoan' Operators gain Vigor effect of +25% ATK when HP is above 80%",
                     {'atk': 0.25}, member_of=minos)
    assert talent.modifiers.atk_percent == 0.25 and talent.notes == ["also buffs all 'Minoan' Operators"]
    hunter = _talent('When this unit is in the squad, all [Abyssal Hunters] Operators gain +14 ASPD', {'attack_speed': 14},
                     member_of=HUNTER)
    assert (hunter.group, hunter.modifiers.attack_speed) == (TalentGroup.ALWAYS, 14)


def test_shared_damage_multiplier():
    talent = _talent('When deployed, Ave Mujica members deal 15% more damage', {'damage_scale': 1.15},
                     member_of=frozenset({'ave mujica'}))
    assert talent.multiplier == pytest.approx(1.15)
    assert talent.notes == ['also buffs Ave Mujica members']


def test_shared_timed_buff_is_flagged():
    # Castle-3: counted as always on, though it only lasts 20 s.
    talent = _talent('Increases the ATK and DEF of all [Melee] allies by 20% for 20 seconds after deployment',
                     {'atk': 0.2, 'def': 0.2, 'duration': 20}, member_of=VANGUARD)
    assert talent.notes == ['also buffs all [Melee] allies', 'effect lasts 20 s']
    assert talent.assumed == ['the shared buff lasts 20 s; counted as always on']


def test_only_stat_and_damage_changes_are_shared():
    talent = _talent('Restores the HP of all allies by 25 per second', {'hp_recovery_per_sec': 25}, member_of=VANGUARD)
    assert talent.notes == ['regenerates 25 HP per second']


def test_build_talents_passes_the_groups_on():
    operator = {'talents': [{'candidates': [make_talent_candidate("All Vanguard Operators' ATK +10%", {'atk': 0.1})]}]}
    [talent] = build_talents(operator, 0, 1, 0, overrides={}, member_of=VANGUARD)
    assert talent.applied and talent.modifiers.atk_percent == 0.1


def test_enemy_status_condition():
    talent = _talent('When deployed, deals 15% more damage to Slowed or Feared enemies', {'damage_scale': 1.15})
    assert (talent.group, talent.condition) == (TalentGroup.PER_ENEMY, TalentCondition('enemy status', tag='slowed'))


# ATK or damage multiplier, from the wording

@pytest.mark.parametrize('description, blackboard, expected', [
    ('Increases ATK to 140% (+5%) when attacking aerial targets', {'atk_scale': 1.4}, ATK_MULTIPLIER),
    ('Prioritize attacking drones; ATK +43% (+3%) when attacking drones', {'atk_scale': 1.43}, ATK_MULTIPLIER),
    ('The next attack deals 133% (+3%) ATK as Physical damage', {'atk_scale': 1.33}, ATK_MULTIPLIER),
    ('Attacks have a 30% chance to deal 160% (+10%) of ATK as Physical damage', {'prob': 0.3, 'atk_scale': 1.6},
     ATK_MULTIPLIER),
    ('Has a 20% chance to increase the ATK of the current attack to 190% (+10%) when attacking',
     {'prob': 0.2, 'atk_scale': 1.9}, ATK_MULTIPLIER),
    # Necrass: a damage_scale, but the text says ATK.
    ('When Necrass or her summons attack enemies with less than 60% (+10%) HP, increases ATK to 145% (+5%)',
     {'hp_ratio': 0.6, 'damage_scale': 1.45}, ATK_MULTIPLIER),
    # Harmonie: an atk_scale, but the text says damage.
    ('Deal 20% (+3%) more damage when attacking blocked enemies', {'atk_scale': 1.2}, DAMAGE_MULTIPLIER),
    ('Deals +25% (+5%) damage when attacking enemies that are not blocked by this unit', {'damage_scale': 1.25},
     DAMAGE_MULTIPLIER),
    ('When attacking, has a 20% chance to increase the current damage of that attack to 160% (+10%)',
     {'prob': 0.2, 'atk_scale': 1.6}, DAMAGE_MULTIPLIER),
    # Mountain: only the words before "and" count.
    ("20% chance for attacks to deal 165% (+5%) damage and reduce the target's ATK by 15% for 3 seconds",
     {'prob': 0.2, 'atk_scale': 1.65}, DAMAGE_MULTIPLIER),
    ('Damage when skill is active increases by 16% (+1%) from second skill activation onward', {'damage_scale': 1.16},
     DAMAGE_MULTIPLIER),
])
def test_multiplier_kind_comes_from_the_wording(description, blackboard, expected):
    talent = _talent(description, blackboard)
    raised, unchanged = ((talent.atk_multiplier, talent.damage_multiplier) if expected == ATK_MULTIPLIER
                         else (talent.damage_multiplier, talent.atk_multiplier))
    assert raised > 1 and unchanged == 1
    assert talent.multiplier == raised
    assert not any('ATK or damage' in note for note in talent.assumed)


@pytest.mark.parametrize('before, after, expected', [
    ('', ' bonus', None),
    # The nearest word before the number counts.
    ('Deals damage, ATK +', '', ATK_MULTIPLIER),
    ('ATK and damage +', '', DAMAGE_MULTIPLIER),
    # Too far from the number.
    ('ATK is one of many things that rise to', '', None),
])
def test_multiplier_kind(before, after, expected):
    assert multiplier_kind(before, after) == expected


def test_multiplier_kind_falls_back_to_the_name():
    talent = _talent('When attacking, gains a 20% bonus', {'damage_scale': 1.2})
    assert (talent.atk_multiplier, talent.damage_multiplier) == (1.0, 1.2)
    assert talent.assumed == ["damage_scale: the text doesn't say whether it raises ATK or damage; counted as damage by "
                              "its name (review)"]
    assert _talent('When attacking, gains a 20% bonus', {'atk_scale': 1.2}).atk_multiplier == 1.2


def test_atk_and_damage_multipliers_together():
    talent = _talent('When attacking, ATK increased to 150% and deals 20% more damage',
                     {'atk_scale': 1.5, 'damage_scale': 1.2})
    assert (talent.atk_multiplier, talent.damage_multiplier) == (1.5, 1.2)
    assert talent.multiplier == pytest.approx(1.8)


# parse_condition and TalentCondition

@pytest.mark.parametrize('kind, clause, expected', [
    ('aerial', 'Increases ATK to 140% when attacking aerial targets', TalentCondition('aerial')),
    ('blocked', 'DEF +19% (+3%) when two or more enemies are blocked', TalentCondition('blocked', minimum=2)),
    ('blocked', 'Increases ATK and DEF by 14% (+2%) when blocking 2 or more enemies', TalentCondition('blocked', minimum=2)),
    ('blocked', 'ATK and DEF +14% (+2%) when Cliffheart is not blocking any enemies',
     TalentCondition('blocked', negated=True)),
    ('blocked', 'Deals 45% (+5%) more damage to enemies that are not blocked by this unit',
     TalentCondition('blocked', negated=True)),
    ('elite', 'Applies 18% (+2%) Fragile effect to enemies that are not Elites or Leaders within range',
     TalentCondition('elite', negated=True)),
    ('weight', 'When attacking a heavy enemy, ignores 60% of its DEF (enemies with 3 weight or higher count for this '
               'effect)', TalentCondition('weight', minimum=3)),
    ('weight', 'Ignores 200 (+20) DEF when attacking enemies with weight greater than or equal to 3',
     TalentCondition('weight', minimum=3)),
    ('weight', 'When attacking enemies with 3 or less weight, increase ATK to 136% (+6%)',
     TalentCondition('weight', maximum=3)),
    ('enemy HP', 'When Necrass or her summons attack enemies with less than 60% (+10%) HP, increases ATK to 145% (+5%)',
     TalentCondition('enemy HP', maximum=0.6)),
    ('enemy HP', 'When attacking enemies with 80% or more HP, Passenger will deal 25% (+5%) increased damage',
     TalentCondition('enemy HP', minimum=0.8)),
    ('enemy HP', 'ATK increased to 125% (+5%) when hitting an enemy with full HP', TalentCondition('enemy HP', minimum=1)),
    ('enemy HP', 'ATK increased to 112% when attacking enemies with an HP percentage equal to or lower than their own',
     TalentCondition('enemy HP')),
    ('enemy type', 'Increases ATK to 150% (+5%) when attacking [Infected Creatures]',
     TalentCondition('enemy type', tag='infection')),
    ('enemy type', 'When attacking Sarkaz enemies, increase ATK to 150% (+5%)', TalentCondition('enemy type', tag='sarkaz')),
    ('enemy type', 'ATK increases to 145% (+5%) when attacking ranged enemies',
     TalentCondition('enemy type', tag='ranged')),
    ('enemy type', 'Deals 22% (+2) more Physical damage to ground enemies that have not yet dealt damage to her',
     TalentCondition('enemy type', tag='ground')),
    ('enemy type', 'Receive 25% less Physical and Arts damage from [Sea Monster] enemies',
     TalentCondition('enemy type', tag='seamonster')),
    ('enemy type', 'Deal 20% more damage to Machine enemies', TalentCondition('enemy type', tag='machine')),
    # Delphine: the first bracket names a game mode, not the enemies.
    ('enemy type', 'In [The Whirlpool that is Passion], ATK is increased to 120% against [Sarkaz] enemies',
     TalentCondition('enemy type', tag='sarkaz')),
    # Not an enemyTags type: kept as written.
    ('enemy type', 'Increase ATK to 144% (+4%) when attacking Sleeping enemies',
     TalentCondition('enemy type', tag='sleeping')),
    ('enemy type', 'Deals more damage', TalentCondition('enemy type')),
    ('enemy status', 'Increase ATK to 165% (+5%) when attacking Stunned enemies',
     TalentCondition('enemy status', tag='stunned')),
    ('enemy status', 'Deals more damage', TalentCondition('enemy status')),
])
def test_parse_condition(kind, clause, expected):
    assert parse_condition(kind, clause) == expected


@pytest.mark.parametrize('condition, expected', [
    (TalentCondition('aerial'), 'aerial'),
    (TalentCondition('blocked', negated=True), 'not blocked'),
    (TalentCondition('blocked', minimum=2), 'blocked 2 or more'),
    (TalentCondition('weight', minimum=3), 'weight 3 or more'),
    (TalentCondition('weight', maximum=3), 'weight 3 or less'),
    (TalentCondition('enemy HP', maximum=0.6), 'enemy HP below 60%'),
    (TalentCondition('enemy HP', minimum=0.8), 'enemy HP 80% or more'),
    (TalentCondition('enemy HP', minimum=1), 'enemy HP full'),
    (TalentCondition('enemy type', tag='sarkaz'), 'enemy type: sarkaz'),
])
def test_condition_describe(condition, expected):
    assert condition.describe() == expected


def test_clause_condition():
    assert clause_condition('ATK +10%') == (TalentGroup.ALWAYS, None)
    assert clause_condition('When attacking aerial targets, ATK +10%') == (TalentGroup.PER_ENEMY, TalentCondition('aerial'))


# find_value with places already used

def test_find_value_takes_the_next_unused_place_of_a_repeated_number():
    text = 'ATK +14% when not blocking, DEF +14% when blocking'
    first = text.index('14%')
    second = text.index('14%', first + 1)
    assert find_value(text, 0.14) == (True, first)
    assert find_value(text, 0.14, used={first}) == (True, second)
    # Every place used: the first is shared ("ATK and DEF +14%").
    assert find_value(text, 0.14, used={first, second}) == (True, first)


def test_same_number_for_the_chance_and_an_enemy_debuff():
    # Shalem's T2: the RES debuff's 25% is the second one, so it's on the target, not on Shalem.
    talent = _talent("Attacks have 25% (+5%) chance to reduce the target's RES by 25% for 3 seconds",
                     {'prob': 0.25, 'magic_resistance': -0.25, 'duration': 3})
    assert talent.modifiers.is_empty()
    assert [value.key for value in talent.enemy_effects] == ['magic_resistance']


# TalentEffect: each effect with its own condition

def test_effects_judged_one_at_a_time():
    # Firewhistle's T1: both 14%, one when not blocking and one when blocking.
    talent = _talent('ATK +14% (+2%) when not blocking an enemy, DEF +14% (+2%) when blocking an enemy',
                     {'atk': 0.14, 'def': 0.14})
    assert talent.modifiers == StatModifiers(atk_percent=0.14, def_percent=0.14)
    atk, defense = talent.effects
    assert (atk.key, atk.condition, atk.modifiers) == ('atk', TalentCondition('blocked', negated=True),
                                                        StatModifiers(atk_percent=0.14))
    assert (defense.key, defense.condition, defense.modifiers) == ('def', TalentCondition('blocked'),
                                                                    StatModifiers(def_percent=0.14))
    assert atk.group == defense.group == TalentGroup.PER_ENEMY


def test_effect_condition_from_the_whole_clause():
    talent = _talent('When attacking aerial targets, increases ATK to 123% (+3%)', {'atk_scale': 1.23})
    [effect] = talent.effects
    assert (effect.group, effect.condition, effect.atk_multiplier) == (TalentGroup.PER_ENEMY, TalentCondition('aerial'),
                                                                       1.23)


def test_effect_without_a_condition():
    [effect] = _talent('ATK +10%', {'atk': 0.1}).effects
    assert (effect.group, effect.condition, effect.scale) == (TalentGroup.ALWAYS, None, 1.0)


def test_effect_with_unrecognised_wording_is_unlikely():
    [effect] = _talent('ATK +10% if it rains', {'atk': 0.1}).effects
    assert effect.group == TalentGroup.UNLIKELY


def test_effect_scale_and_multiplier():
    [effect] = _talent('When attacking, has a 20% chance to deal 150% damage', {'prob': 0.2, 'atk_scale': 1.5}).effects
    assert (effect.key, effect.scale) == ('atk_scale', 0.2)
    assert (effect.atk_multiplier, effect.damage_multiplier) == (1.0, pytest.approx(1.1))


def test_effects_on_enemies():
    [fragile] = _talent('Enemies within attack range gain 12% Fragile', {'damage_scale': 1.12}).effects
    assert fragile.enemy_effect.key == 'damage_scale'
    [debuff] = _talent("Attacks reduce the target's DEF by 10%", {'def': -0.1}).effects
    assert (debuff.enemy_effect.key, debuff.modifiers.is_empty()) == ('def', True)


def test_ally_buffs_have_no_effect():
    talent = _talent('Allies within range gain ATK +10%', {'atk': 0.1})
    assert talent.effects == [] and talent.notes


def test_def_ignore_talents():
    talent = _talent('Attacks ignore 175 (+15) DEF', {'def_penetrate_fixed': 175})
    assert talent.penetration == [Penetration(def_flat=175)]
    # Still listed with the effects on enemies, for the reports.
    assert [value.key for value in talent.enemy_effects] == ['def_penetrate_fixed']
    assert talent.effects[0].penetration == Penetration(def_flat=175)


def test_def_ignore_with_a_chance():
    # Irene's T1: the chance is how often it applies, not how much is ignored.
    talent = _talent('Physical damage has a 50% chance to ignore 50% DEF', {'prob': 0.5, 'def_penetrate': 0.5})
    assert talent.penetration == [Penetration(def_ratio=0.5, chance=0.5)]


def test_overridden_effects_take_the_talents_group():
    overrides = {('', 1): TalentOverride(TalentGroup.UNLIKELY)}
    [effect] = _talent('ATK +10%', {'atk': 0.1}, overrides=overrides).effects
    assert effect.group == TalentGroup.UNLIKELY


# Real talents (tests/fixtures/talents) for matchups, at Elite 2, potential 6

def test_real_meteor_t1_aerial_atk_multiplier():
    talent = _real('meteor_t1')
    # "Increases ATK to 140% (+5%) when attacking aerial targets"
    assert (talent.group, talent.condition, talent.applied) == (TalentGroup.PER_ENEMY, TalentCondition('aerial'), False)
    assert (talent.atk_multiplier, talent.damage_multiplier) == (1.4, 1.0)
    assert [(effect.key, effect.condition) for effect in talent.effects] == [('atk_scale', TalentCondition('aerial'))]


def test_real_necrass_t2_low_hp_atk_multiplier():
    talent = _real('necrass_t2')
    # "...attack enemies with less than 60% (+10%) HP, increases ATK to 145% (+5%)": a damage_scale worded as ATK.
    assert talent.condition == TalentCondition('enemy HP', maximum=0.6)
    assert (talent.atk_multiplier, talent.damage_multiplier) == (1.45, 1.0)


def test_real_rosa_t1_heavy_enemies_def_ignore():
    talent = _real('rosa_t1')
    # "When attacking a heavy enemy, ignores 60% of its DEF (enemies with 3 weight or higher count for this effect)"
    assert talent.condition == TalentCondition('weight', minimum=3)
    assert talent.penetration == [Penetration(def_ratio=0.6)]
    assert talent.effects[0].condition == TalentCondition('weight', minimum=3)


def test_real_firewhistle_t1_each_effect_its_own_condition():
    talent = _real('firewhistle_t1')
    # "ATK +14% (+2%) when not blocking an enemy, DEF +14% (+2%) when blocking an enemy"
    assert talent.modifiers == StatModifiers(atk_percent=0.14, def_percent=0.14)
    assert [(effect.key, effect.condition.describe()) for effect in talent.effects] == [('atk', 'not blocked'),
                                                                                        ('def', 'blocked')]

