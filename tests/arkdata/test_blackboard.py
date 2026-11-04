import pytest

from arkdata.calculations.blackboard import (
    BlackboardValue,
    Target,
    read_blackboard,
    render_description,
    strip_tags,
)
from factories import load_skill_fixture, make_skill_level


def _level(description, **blackboard):
    return make_skill_level(description, blackboard)


# strip_tags

def test_strip_tags_removes_formatting_but_keeps_angle_bracket_words():
    text = 'ATK <@ba.vup>+70%</> and <$ba.stun>Stuns</>; Can use <Support Devices>'
    assert strip_tags(text) == 'ATK +70% and Stuns; Can use <Support Devices>'


def test_strip_tags_of_none_is_empty():
    assert strip_tags(None) == ''


# read_blackboard: percentages and flat values

def test_percent_and_flat_values_come_from_the_template():
    values = read_blackboard(_level('ATK +{atk:0%}, DEF +{def}', atk=0.7, **{'def': 75}))
    assert values['atk'] == BlackboardValue('atk', 'atk', 0.7, True, Target.SELF)
    assert values['def'] == BlackboardValue('def', 'def', 75, False, Target.SELF)
    assert values['atk'].in_text


def test_value_missing_from_the_text_is_unknown():
    values = read_blackboard(_level('Shoots more', base_attack_time=-0.08))
    assert values['base_attack_time'] == BlackboardValue('base_attack_time', 'base_attack_time', -0.08, None, Target.UNKNOWN)
    assert not values['base_attack_time'].in_text


def test_attack_prefix_is_removed_from_the_key_but_matched_in_the_template():
    values = read_blackboard(_level('Shoots {attack@times} times', **{'attack@times': 5}))
    assert values['times'].raw_key == 'attack@times'
    assert values['times'].is_percent is False


def test_first_of_duplicate_names_is_kept():
    level = make_skill_level('ATK +{atk:0%}', None)
    level['blackboard'] = [{'key': 'atk', 'value': 0.5}, {'key': 'attack@atk', 'value': 0.9}]
    assert read_blackboard(level)['atk'].value == 0.5


def test_values_without_a_number_are_skipped():
    level = make_skill_level('x', None)
    level['blackboard'] = [{'key': 'cnt', 'value': None, 'valueStr': 'token'}]
    assert read_blackboard(level) == {}


def test_names_in_the_template_are_matched_ignoring_case():
    values = read_blackboard(_level('Restores {HP_RATIO:0.0%} of Max HP', hp_ratio=0.06))
    assert values['hp_ratio'].is_percent is True


def test_level_without_description_or_blackboard():
    assert read_blackboard({}) == {}


# read_blackboard: who a value affects

@pytest.mark.parametrize('description, expected', [
    ('DEF -{-def:0%}; ATK +{atk:0%}', Target.SELF),
    ("Targets' DEF -{-def}", Target.ENEMY),
    ('Reduces the DEF of enemies within range by {def}', Target.ENEMY),
    ("Attacks reduce the target's DEF by {def}", Target.ENEMY),
    ('Allies within range gain DEF +{def}', Target.ALLY),
    ('Other operators gain DEF +{def}', Target.ALLY),
    ('Enemies are slowed; DEF +{def}', Target.SELF),
    ('Enemies are slowed. DEF +{def}', Target.SELF),
    ('Enemies are slowed\nDEF +{def}', Target.SELF),
])
def test_target_comes_from_the_words_before_the_value_in_its_clause(description, expected):
    assert read_blackboard(_level(description, **{'def': -1}))['def'].target == expected


# render_description

def test_render_fills_percentages_flat_and_negated_values():
    level = _level("ATK +{atk:0%}; Targets' DEF -{-def}; lasts {duration} s; heals {heal:0.0%}",
                   atk=0.7, duration=3.0, heal=0.065, **{'def': -200})
    assert render_description(level) == "ATK +70%; Targets' DEF -200; lasts 3 s; heals 6.5%"


def test_render_keeps_unknown_placeholders_and_removes_tags():
    level = _level('<@ba.vup>{atk:0%}</> and {missing}', atk=1.25)
    assert render_description(level) == '125% and {missing}'


def test_render_matches_names_ignoring_case():
    assert render_description(_level('{HP_RATIO:0.0%}', hp_ratio=0.06)) == '6.0%'


def test_render_real_skill_ifrit_s2_mastery_3():
    level = load_skill_fixture('ifrit_s2')['excel']['levels'][9]
    assert render_description(level) == (
        "Deals 250% of ATK Arts damage in the next attack; Targets' DEF -300 for 3 seconds; "
        'Causes them to take Immolation Damage\nCan store 3 charge(s)'
    )


def test_real_skill_silverash_s3_reads_own_def_reduction_as_self():
    values = read_blackboard(load_skill_fixture('silverash_s3')['excel']['levels'][9])
    assert values['def'] == BlackboardValue('def', 'def', -0.7, True, Target.SELF)
    assert values['max_target'].value == 6
