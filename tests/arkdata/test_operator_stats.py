import logging

import pytest

import arkdata.calculations.operator_stats as operator_stats_module
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_stats import OperatorStats, determine_damage_type, parse_phase, ATTRIBUTE_TYPE_TO_STAT_KEY, SUB_PROFESSION_DAMAGE_TYPES

from factories import make_operator_data


# determine_damage_type

def test_damage_type_defaults_to_physical():
    assert determine_damage_type({'description': 'Blocks 1 enemy', 'profession': 'GUARD'}, 2) == 'PHYSICAL'


def test_damage_type_arts_from_description():
    assert determine_damage_type({'description': 'Deals Arts damage', 'profession': 'CASTER'}, 0) == 'ARTS'


def test_damage_type_heal_for_medic_profession():
    assert determine_damage_type({'description': 'Heals allies', 'profession': 'MEDIC'}, 0) == 'HEAL'


def test_damage_type_heal_from_restores_hp_description():
    assert determine_damage_type({'description': 'Restores the HP of allies', 'profession': 'SUPPORT'}, 0) == 'HEAL'


def test_damage_type_handles_missing_description_and_trait():
    assert determine_damage_type({}, 2) == 'PHYSICAL'
    assert determine_damage_type({'description': None, 'trait': None}, 2) == 'PHYSICAL'


def _trait(phase, override):
    return {'unlockCondition': {'phase': phase, 'level': 1}, 'overrideDescripton': override}


def test_damage_type_trait_override_applies_once_phase_reached():
    data = {'description': 'Blocks 1 enemy', 'profession': 'GUARD', 'trait': {'candidates': [_trait('PHASE_2', 'Deals Arts damage')]}}
    assert determine_damage_type(data, 2) == 'ARTS'


def test_damage_type_trait_override_ignored_before_phase():
    data = {'description': 'Blocks 1 enemy', 'profession': 'GUARD', 'trait': {'candidates': [_trait('PHASE_2', 'Deals Arts damage')]}}
    assert determine_damage_type(data, 1) == 'PHYSICAL'


def test_damage_type_trait_override_accepts_integer_phase():
    data = {'description': 'Blocks 1 enemy', 'trait': {'candidates': [_trait(1, 'Deals Arts damage')]}}
    assert determine_damage_type(data, 1) == 'ARTS'


def test_damage_type_later_trait_candidate_wins():
    data = {'description': 'Deals Arts damage', 'trait': {'candidates': [
        _trait('PHASE_0', 'Deals Arts damage'), _trait('PHASE_1', 'Blocks 2 enemies'),
    ]}}
    assert determine_damage_type(data, 2) == 'PHYSICAL'


def test_damage_type_empty_override_ignored():
    data = {'description': 'Deals Arts damage', 'trait': {'candidates': [_trait('PHASE_0', None)]}}
    assert determine_damage_type(data, 2) == 'ARTS'


# OperatorStats: identity and validation

def test_identity_fields(operator_data):
    op = OperatorStats('char_test', 1, 0, 0, operator_data=operator_data)
    assert (op.operator_id, op.name, op.profession, op.sub_profession_id, op.rarity) == (
        'char_test', 'Test Operator', 'GUARD', 'testsub', 'TIER_5')
    assert op.damage_type == 'PHYSICAL'


def test_fetches_operator_data_when_not_given(monkeypatch, operator_data):
    calls = []
    monkeypatch.setattr(operator_stats_module, 'fetch_operator_data', lambda key: calls.append(key) or operator_data)
    op = OperatorStats('char_test', 1, 0, 0)
    assert calls == ['char_test']
    assert op.name == 'Test Operator'


@pytest.mark.parametrize('phase', [-1, 3])
def test_invalid_phase_raises(operator_data, phase):
    with pytest.raises(ValueError, match='phases 0-2'):
        OperatorStats('char_test', 1, phase, 0, operator_data=operator_data)


@pytest.mark.parametrize('level', [0, 51])
def test_invalid_level_raises(operator_data, level):
    with pytest.raises(ValueError, match='levels 1-50'):
        OperatorStats('char_test', level, 0, 0, operator_data=operator_data)


@pytest.mark.parametrize('level', [1, 50])
def test_level_boundaries_allowed(operator_data, level):
    OperatorStats('char_test', level, 0, 0, operator_data=operator_data)


@pytest.mark.parametrize('potential', [-1, 6])
def test_invalid_potential_raises(operator_data, potential):
    with pytest.raises(ValueError, match='potential 0-5'):
        OperatorStats('char_test', 1, 0, potential, operator_data=operator_data)


@pytest.mark.parametrize('potential', [0, 5])
def test_potential_boundaries_allowed(operator_data, potential):
    OperatorStats('char_test', 1, 0, potential, operator_data=operator_data)


def test_negative_trust_raises(operator_data):
    with pytest.raises(ValueError, match='Trust cannot be negative'):
        OperatorStats('char_test', 1, 0, 0, trust=-1, operator_data=operator_data)


# OperatorStats: stat calculation

def test_level_one_uses_start_keyframe(operator_data):
    op = OperatorStats('char_test', 1, 0, 0, operator_data=operator_data)
    assert op.stats['atk'] == 276
    assert op.stats['maxHp'] == 1000


def test_max_level_uses_end_keyframe(operator_data):
    op = OperatorStats('char_test', 50, 0, 0, operator_data=operator_data)
    assert op.stats['atk'] == pytest.approx(390)
    assert op.stats['maxHp'] == pytest.approx(1490)


def test_intermediate_level_interpolates_over_level_ups(operator_data):
    # Matches the Amiya example in CLAUDE.md: (390 - 276) / 49 * 29 + 276.
    op = OperatorStats('char_test', 30, 0, 0, operator_data=operator_data)
    assert op.stats['atk'] == pytest.approx(343.469, abs=1e-3)
    assert op.stats['maxHp'] == pytest.approx(1290)


def test_unchanged_and_bool_stats_kept(operator_data):
    op = OperatorStats('char_test', 30, 0, 0, operator_data=operator_data)
    assert op.stats['def'] == 100
    assert op.stats['stunImmune'] is False


def test_uses_selected_phase_keyframes(operator_data):
    op = OperatorStats('char_test', 1, 2, 0, operator_data=operator_data)
    assert op.stats['atk'] == 550


def test_legacy_attribute_key_frames_name_supported():
    data = make_operator_data()
    for phase in data['phases']:
        phase['attributeKeyFrames'] = phase.pop('attributesKeyFrames')
    op = OperatorStats('char_test', 50, 0, 0, operator_data=data)
    assert op.stats['atk'] == pytest.approx(390)


def test_single_keyframe_phase_keeps_start_values():
    data = make_operator_data(phase_ranges=[(1, {'atk': 100}, {'atk': 100})])
    data['phases'][0]['attributesKeyFrames'] = data['phases'][0]['attributesKeyFrames'][:1]
    op = OperatorStats('char_test', 1, 0, 0, operator_data=data)
    assert op.stats['atk'] == 100


def test_potential_bonuses_applied_up_to_potential(operator_data):
    base = OperatorStats('char_test', 1, 0, 0, operator_data=operator_data)
    op = OperatorStats('char_test', 1, 0, 2, operator_data=operator_data)
    assert op.stats['cost'] == base.stats['cost'] - 1
    assert op.stats['atk'] == base.stats['atk'] + 20
    assert op.stats['maxHp'] == base.stats['maxHp']


def test_all_potential_bonuses_applied_and_null_buff_skipped(operator_data):
    base = OperatorStats('char_test', 1, 0, 0, operator_data=operator_data)
    op = OperatorStats('char_test', 1, 0, 5, operator_data=operator_data)
    assert op.stats['maxHp'] == base.stats['maxHp'] + 100
    assert op.stats['def'] == base.stats['def'] + 10


def test_non_addition_and_unknown_potential_modifiers_ignored():
    data = make_operator_data(potential_ranks=[
        {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'ATK', 'formulaItem': 'MULTIPLIER', 'value': 0.5}]}}},
        {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'UNKNOWN', 'formulaItem': 'ADDITION', 'value': 99}]}}},
        {'buff': {'attributes': None}},
    ])
    op = OperatorStats('char_test', 1, 0, 3, operator_data=data)
    assert op.stats['atk'] == 276
    assert 'UNKNOWN' not in op.stats


def test_potential_modifier_for_missing_stat_starts_from_zero():
    data = make_operator_data(potential_ranks=[
        {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'ATK', 'formulaItem': 'ADDITION', 'value': 5}]}}},
    ])
    for phase in data['phases']:
        for frame in phase['attributesKeyFrames']:
            del frame['data']['atk']
    data['favorKeyFrames'][0]['data'].pop('atk')
    data['favorKeyFrames'][1]['data'].pop('atk')
    op = OperatorStats.__new__(OperatorStats)
    op._data, op.phase, op.level, op.potential = data, 0, 1, 1
    op.stats = op._calculate_base_stats()
    op._apply_potential_bonuses()
    assert op.stats['atk'] == 5


def test_attribute_type_map_covers_potential_stats():
    assert ATTRIBUTE_TYPE_TO_STAT_KEY['ATK'] == 'atk'
    assert ATTRIBUTE_TYPE_TO_STAT_KEY['MAX_HP'] == 'maxHp'
    assert ATTRIBUTE_TYPE_TO_STAT_KEY['COST'] == 'cost'
    assert ATTRIBUTE_TYPE_TO_STAT_KEY['RESPAWN_TIME'] == 'respawnTime'


@pytest.mark.parametrize('trust, expected_atk_bonus', [(0, 0), (50, 25), (100, 50), (200, 50)])
def test_trust_bonus_scales_and_caps_at_100(operator_data, trust, expected_atk_bonus):
    op = OperatorStats('char_test', 1, 0, 0, trust=trust, operator_data=operator_data)
    assert op.stats['atk'] == pytest.approx(276 + expected_atk_bonus)
    assert op.stats['stunImmune'] is False


def test_trust_bonus_for_stat_missing_from_phase_starts_from_zero():
    data = make_operator_data(favor_end={'maxHp': 0, 'atk': 0, 'def': 0, 'magicResistance': 0.0,
                                         'attackSpeed': 0.0, 'stunImmune': False, 'newStat': 10})
    data['favorKeyFrames'][0]['data']['newStat'] = 0
    op = OperatorStats('char_test', 1, 0, 0, trust=100, operator_data=data)
    assert op.stats['newStat'] == 10


def test_attack_interval_and_dps():
    data = make_operator_data(phase_ranges=[(10, {'atk': 400, 'baseAttackTime': 1.6, 'attackSpeed': 100.0},
                                                 {'atk': 400, 'baseAttackTime': 1.6, 'attackSpeed': 100.0})])
    op = OperatorStats('char_test', 1, 0, 0, operator_data=data)
    assert op.attack_interval == pytest.approx(1.6)
    assert op.dps == pytest.approx(250)


def test_attack_speed_shortens_interval():
    data = make_operator_data(phase_ranges=[(10, {'baseAttackTime': 2.0, 'attackSpeed': 200.0},
                                                 {'baseAttackTime': 2.0, 'attackSpeed': 200.0})])
    op = OperatorStats('char_test', 1, 0, 0, operator_data=data)
    assert op.attack_interval == pytest.approx(1.0)


def test_to_dict_contains_identity_stats_damage_type_and_dps(operator_data):
    op = OperatorStats('char_test', 30, 2, 1, trust=50, operator_data=operator_data)
    result = op.to_dict()
    assert result['operator_id'] == 'char_test'
    assert result['name'] == 'Test Operator'
    assert result['profession'] == 'GUARD'
    assert result['sub_profession_id'] == 'testsub'
    assert result['rarity'] == 'TIER_5'
    assert (result['level'], result['phase'], result['potential'], result['trust']) == (30, 2, 1, 50)
    assert result['atk'] == op.stats['atk']
    assert result['damage_type'] == 'PHYSICAL'
    assert result['attack_interval'] == op.attack_interval
    assert result['dps'] == op.dps
    assert list(result)[-3:] == ['damage_type', 'attack_interval', 'dps']


def test_bool_stats_keep_level_one_value_even_if_they_change():
    data = make_operator_data(phase_ranges=[(10, {'stunImmune': False}, {'stunImmune': True})])
    op = OperatorStats('char_test', 5, 0, 0, operator_data=data)
    assert op.stats['stunImmune'] is False


def test_skipped_potential_modifier_does_not_stop_later_modifiers_in_same_rank():
    data = make_operator_data(potential_ranks=[
        {'buff': {'attributes': {'attributeModifiers': [
            {'attributeType': 'ATK', 'formulaItem': 'MULTIPLIER', 'value': 0.5},
            {'attributeType': 'UNKNOWN', 'formulaItem': 'ADDITION', 'value': 99},
            {'attributeType': 'DEF', 'formulaItem': 'ADDITION', 'value': 15},
        ]}}},
    ])
    op = OperatorStats('char_test', 1, 0, 1, operator_data=data)
    assert op.stats['def'] == 115
    assert op.stats['atk'] == 276


def test_trust_bonus_interpolates_from_nonzero_start_frame():
    data = make_operator_data()
    data['favorKeyFrames'][0]['data']['atk'] = 10
    op = OperatorStats('char_test', 1, 0, 0, trust=50, operator_data=data)
    # Halfway between 10 and 50.
    assert op.stats['atk'] == pytest.approx(276 + 30)


@pytest.mark.parametrize('phase, expected', [(1, 'PHYSICAL'), (2, 'ARTS')])
def test_damage_type_uses_operators_phase_for_trait_overrides(phase, expected):
    data = make_operator_data(trait_candidates=[_trait('PHASE_2', 'Deals Arts damage')])
    assert OperatorStats('char_test', 1, phase, 0, operator_data=data).damage_type == expected


# Sub profession lookup


@pytest.mark.parametrize('phase, expected', [('PHASE_0', 0), ('PHASE_2', 2), (1, 1), ('2', 2)])
def test_parse_phase(phase, expected):
    assert parse_phase(phase) == expected


def test_sub_profession_table_only_holds_damage_types():
    assert SUB_PROFESSION_DAMAGE_TYPES
    assert all(isinstance(value, DamageType) for value in SUB_PROFESSION_DAMAGE_TYPES.values())


@pytest.mark.parametrize('sub_profession, expected', [
    ('corecaster', 'ARTS'), ('artsfghter', 'ARTS'), ('summoner', 'ARTS'), ('slower', 'ARTS'),
    ('physician', 'HEAL'), ('ringhealer', 'HEAL'), ('bard', 'HEAL'),
    ('lord', 'PHYSICAL'), ('guardian', 'PHYSICAL'), ('aoesniper', 'PHYSICAL'), ('stalker', 'PHYSICAL'),
])
def test_sub_profession_table_entries(sub_profession, expected):
    assert SUB_PROFESSION_DAMAGE_TYPES[sub_profession] == expected


def test_sub_profession_takes_priority_over_description(caplog):
    data = {'subProfessionId': 'corecaster', 'description': 'Blocks 1 enemy', 'profession': 'CASTER'}
    with caplog.at_level(logging.WARNING):
        assert determine_damage_type(data, 2) is DamageType.ARTS
    assert caplog.records == []


def test_operator_override_takes_priority_over_sub_profession(monkeypatch):
    monkeypatch.setitem(operator_stats_module.OPERATOR_DAMAGE_TYPE_OVERRIDES, 'char_test', DamageType.TRUE)
    data = {'subProfessionId': 'corecaster'}
    assert determine_damage_type(data, 2, 'char_test') is DamageType.TRUE
    assert determine_damage_type(data, 2, 'char_other') is DamageType.ARTS


def test_unknown_sub_profession_warns_and_falls_back_to_description(caplog):
    data = {'name': 'Mystery', 'subProfessionId': 'newarchetype', 'description': 'Deals Arts damage'}
    with caplog.at_level(logging.WARNING):
        assert determine_damage_type(data, 2) is DamageType.ARTS
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert caplog.records[0].getMessage() == (
        'Sub profession newarchetype of Mystery is not in SUB_PROFESSION_DAMAGE_TYPES; guessed ARTS from its trait description')


def test_unknown_sub_profession_warning_uses_operator_id_without_name(caplog):
    with caplog.at_level(logging.WARNING):
        determine_damage_type({}, 0, 'char_test')
    assert caplog.records[0].getMessage() == (
        'Sub profession None of char_test is not in SUB_PROFESSION_DAMAGE_TYPES; guessed PHYSICAL from its trait description')


def test_operator_stats_damage_type_uses_sub_profession_and_operator_id(monkeypatch):
    data = make_operator_data(description='Blocks 1 enemy')
    data['subProfessionId'] = 'splashcaster'
    assert OperatorStats('char_test', 1, 0, 0, operator_data=data).damage_type is DamageType.ARTS
    monkeypatch.setitem(operator_stats_module.OPERATOR_DAMAGE_TYPE_OVERRIDES, 'char_test', DamageType.PHYSICAL)
    assert OperatorStats('char_test', 1, 0, 0, operator_data=data).damage_type is DamageType.PHYSICAL
