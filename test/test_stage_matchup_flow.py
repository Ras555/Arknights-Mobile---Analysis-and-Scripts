import logging
import math

import pandas as pd
import pytest

from app import config
from lib.enemy_stats import EnemyStats
from app.stage_matchup_flow import (
    StageMatchupProcessor, calculate_matchups, choose_stage_variant, count_enemy_spawns, load_stage_enemies,
)

from conftest import make_enemy_data


def _variant(stage_id, difficulty='NORMAL'):
    return {'excel': {'code': '6-9', 'name': 'Test', 'difficulty': difficulty, 'stageId': stage_id}}


def _inputs(*answers):
    answers = list(answers)
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return answers.pop(0)

    fake_input.prompts = prompts
    return fake_input


# choose_stage_variant

def test_single_variant_returned_without_asking():
    fake_input = _inputs()
    assert choose_stage_variant('s', [_variant('a')], fake_input) == _variant('a')
    assert fake_input.prompts == []


def test_multiple_variants_asks_until_valid(capsys):
    variants = [_variant('main_06-09'), _variant('tough_06-09', 'FOUR_STAR')]
    fake_input = _inputs('x', '2', '-1', ' 1 ')
    assert choose_stage_variant('main_06-09', variants, fake_input) is variants[1]
    assert len(fake_input.prompts) == 4
    assert 'Choose a variant [0-1]' in fake_input.prompts[0]
    out = capsys.readouterr().out
    assert "has 2 variants" in out
    assert out.splitlines().count('Invalid choice.') == 3
    assert '[1] 6-9 Test (FOUR_STAR, tough_06-09)' in out


def test_first_variant_selectable():
    variants = [_variant('a'), _variant('b')]
    assert choose_stage_variant('s', variants, _inputs('0')) is variants[0]


# count_enemy_spawns

def test_count_enemy_spawns():
    waves = [
        {'fragments': [{'actions': [
            {'actionType': 'SPAWN', 'key': 'e1', 'count': 3},
            {'actionType': 'SPAWN', 'key': 'e2'},
            {'actionType': 'STORY', 'key': 'e1', 'count': 9},
        ]}]},
        {'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 2}]}, {'actions': None}]},
        {'fragments': None},
    ]
    assert count_enemy_spawns(waves) == {'e1': 5, 'e2': 1}


@pytest.mark.parametrize('waves', [None, []])
def test_count_enemy_spawns_empty(waves):
    assert count_enemy_spawns(waves) == {}


# load_stage_enemies

def test_load_stage_enemies_skips_non_db_enemies(caplog):
    stage = {'levels': {
        'waves': [{'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 4}]}]}],
        'enemyDbRefs': [
            {'useDb': True, 'id': 'e1', 'level': 1, 'overwrittenData': {'x': 1}},
            {'useDb': False, 'id': 'custom'},
            {'useDb': True, 'id': 'e2'},
        ],
    }}
    made = []

    def fake_make(enemy_id, level, overwritten):
        made.append((enemy_id, level, overwritten))
        return enemy_id

    with caplog.at_level(logging.WARNING):
        result = load_stage_enemies(stage, fake_make)
    assert made == [('e1', 1, {'x': 1}), ('e2', 0, None)]
    assert result == [('e1', 4), ('e2', 0)]
    assert 'Skipping custom' in caplog.text


def test_load_stage_enemies_no_refs():
    assert load_stage_enemies({'levels': {}}, lambda *a: None) == []


# calculate_matchups

def _operator_row(**overrides):
    row = {'operator_id': 'char_a', 'name': 'Guard', 'damage_type': 'PHYSICAL', 'atk': 600, 'attack_interval': 1.0,
           'def': 150, 'magicResistance': 10, 'maxHp': 3000}
    row.update(overrides)
    return row


def _enemy(**kwargs):
    return EnemyStats('enemy_test', enemy_data=make_enemy_data(**kwargs))


def test_calculate_matchups_physical_vs_physical():
    operators = pd.DataFrame([_operator_row()])
    enemy = _enemy()  # atk 200, def 100, res 20, hp 2000, interval 2.0
    result = calculate_matchups(operators, [(enemy, 3)])
    row = result.iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(500)
    assert row['enemy_dps_to_operator'] == pytest.approx(25)
    assert row['seconds_for_operator_to_kill_enemy'] == pytest.approx(4)
    assert row['seconds_for_enemy_to_kill_operator'] == pytest.approx(120)
    assert row['enemy_spawn_count'] == 3
    assert row['operator_name'] == 'Guard'
    assert row['operator_damage_type'] == 'PHYSICAL'
    assert row['enemy_name'] == 'Test Enemy'
    assert row['enemy_level'] == 0
    assert row['enemy_damage_type'] == 'PHYSICAL'
    assert row['enemy_apply_way'] == 'MELEE'


def test_calculate_matchups_arts_both_ways():
    operators = pd.DataFrame([_operator_row(damage_type='ARTS', magicResistance=50)])
    enemy = _enemy(damage_type=['MAGIC'])
    row = calculate_matchups(operators, [(enemy, 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(480)
    assert row['enemy_dps_to_operator'] == pytest.approx(50)


def test_calculate_matchups_healer_and_harmless_enemy_never_kill():
    operators = pd.DataFrame([_operator_row(damage_type='HEAL')])
    enemy = _enemy(apply_way='NONE')
    row = calculate_matchups(operators, [(enemy, 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == 0
    assert row['enemy_dps_to_operator'] == 0
    assert math.isinf(row['seconds_for_operator_to_kill_enemy'])
    assert math.isinf(row['seconds_for_enemy_to_kill_operator'])


def test_calculate_matchups_one_row_per_pair():
    operators = pd.DataFrame([_operator_row(operator_id='a'), _operator_row(operator_id='b')])
    result = calculate_matchups(operators, [(_enemy(), 1), (_enemy(name='Other'), 2)])
    assert list(zip(result['operator_id'], result['enemy_name'])) == [
        ('a', 'Test Enemy'), ('a', 'Other'), ('b', 'Test Enemy'), ('b', 'Other')]


def test_calculate_matchups_empty():
    assert calculate_matchups(pd.DataFrame([_operator_row()]), []).empty


# StageMatchupProcessor

def _write_operator_stats(path, include_damage_type=True):
    row = _operator_row(phase=2, profession='GUARD')
    if not include_damage_type:
        row.pop('damage_type')
    pd.DataFrame([row]).to_csv(path, index=False)


def _stage(enemy_refs, stage_id='main_06-09'):
    return {'excel': {'code': '6-9', 'name': 'Test', 'difficulty': 'NORMAL', 'stageId': stage_id},
            'levels': {'enemyDbRefs': enemy_refs, 'waves': [
                {'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 2}]}]}]}}


def _processor(tmp_path, stages, inputs=()):
    fetched = []

    def fake_fetch(stage_id):
        fetched.append(stage_id)
        return stages

    processor = StageMatchupProcessor(
        operator_stats_file=str(tmp_path / 'stats.csv'), output_dir=str(tmp_path / 'io'),
        fetch_stage=fake_fetch, make_enemy=lambda enemy_id, level, overwritten: _enemy(),
        input_func=_inputs(*inputs),
    )
    return processor, fetched


def test_processor_defaults_come_from_config():
    processor = StageMatchupProcessor()
    assert processor.operator_stats_file == config.OPERATOR_STATS_FILE
    assert processor.output_dir == config.IO_DIR


def test_processor_run_saves_outputs_and_prints_tables(tmp_path, capsys):
    _write_operator_stats(tmp_path / 'stats.csv')
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])

    enemy_df, matchups = processor.run('main_06-09')

    assert fetched == ['main_06-09']
    assert enemy_df['spawn_count'].tolist() == [2]
    assert matchups['operator_dps_to_enemy'].tolist() == [pytest.approx(500)]
    saved_enemies = pd.read_csv(tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv')
    saved_matchups = pd.read_csv(tmp_path / 'io' / 'stage_main_06-09_matchups.csv')
    assert saved_enemies['name'].tolist() == ['Test Enemy']
    assert len(saved_matchups) == 1
    out = capsys.readouterr().out
    assert 'Enemy stats:' in out
    assert 'Operator DPS to each enemy:' in out
    assert 'Enemy DPS to each operator:' in out


def test_processor_asks_for_stage_id_when_not_given(tmp_path):
    _write_operator_stats(tmp_path / 'stats.csv')
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')], inputs=[' main_06-09 '])
    processor.run()
    assert processor.input_func.prompts == ['Enter a stage id (e.g. main_01-07): ']
    assert fetched == ['main_06-09']
    # Output files are named after the chosen variant's stageId.
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_requires_damage_type_column(tmp_path, caplog):
    _write_operator_stats(tmp_path / 'stats.csv', include_damage_type=False)
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    with caplog.at_level(logging.ERROR):
        assert processor.run('main_06-09') is None
    assert fetched == []
    assert "missing columns ['damage_type']" in caplog.text


def test_processor_no_enemies_returns_none(tmp_path, caplog):
    _write_operator_stats(tmp_path / 'stats.csv')
    processor, _ = _processor(tmp_path, [_stage([{'useDb': False, 'id': 'custom'}])])
    with caplog.at_level(logging.WARNING):
        assert processor.run('main_06-09') is None
    assert 'No enemies found for stage main_06-09' in caplog.text
    assert not (tmp_path / 'io').exists()


def test_calculate_matchups_includes_enemy_id():
    row = calculate_matchups(pd.DataFrame([_operator_row()]), [(_enemy(), 1)]).iloc[0]
    assert row['enemy_id'] == 'enemy_test'
    assert row['operator_id'] == 'char_a'


def test_calculate_matchups_kill_time_with_dps_below_one():
    # 100 ATK vs 100 DEF hits for the 5% floor (5) every 10 seconds: 0.5 DPS.
    operators = pd.DataFrame([_operator_row(atk=100, attack_interval=10.0)])
    row = calculate_matchups(operators, [(_enemy(), 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(0.5)
    assert row['seconds_for_operator_to_kill_enemy'] == pytest.approx(4000)


def test_calculate_matchups_enemy_kill_time_with_dps_below_one():
    # 20 ATK vs 150 DEF hits for the 5% floor (1) every 2 seconds: 0.5 DPS.
    operators = pd.DataFrame([_operator_row()])
    row = calculate_matchups(operators, [(_enemy(base_attributes={'atk': 20}), 1)]).iloc[0]
    assert row['enemy_dps_to_operator'] == pytest.approx(0.5)
    assert row['seconds_for_enemy_to_kill_operator'] == pytest.approx(6000)


def test_print_matchup_tables_formatting(capsys):
    from app.stage_matchup_flow import print_matchup_tables
    enemy_df = pd.DataFrame([{'name': 'Wolf', 'level': 0, 'spawn_count': 2, 'maxHp': 100, 'atk': 10, 'def': 0,
                              'magicResistance': 0, 'damage_type': 'PHYSICAL', 'apply_way': 'MELEE',
                              'attack_interval': 1.23456, 'dps': 8.1}])
    matchups = pd.DataFrame([
        {'operator_name': 'Zeta', 'enemy_name': 'Wolf', 'enemy_level': 0, 'operator_dps_to_enemy': 12.345, 'enemy_dps_to_operator': 6.789},
        {'operator_name': 'Alpha', 'enemy_name': 'Wolf', 'enemy_level': 0, 'operator_dps_to_enemy': 1.0, 'enemy_dps_to_operator': 2.0},
    ])
    print_matchup_tables(enemy_df, matchups)
    out = capsys.readouterr().out
    enemy_lines = out.split('Enemy stats:\n')[1].split('\n\n')[0].splitlines()
    assert enemy_lines[0].split()[0] == 'name'
    assert enemy_lines[1].split()[0] == 'Wolf'
    assert '1.23' in enemy_lines[1] and '1.235' not in enemy_lines[1]
    assert '12.3' in out and '12.34' not in out and '12.35' not in out
    assert '6.8' in out and '6.79' not in out
    # Operators keep their input order rather than being sorted.
    assert out.index('Zeta') < out.index('Alpha')


def test_processor_choose_variant_uses_stage_id_and_input(tmp_path, capsys):
    _write_operator_stats(tmp_path / 'stats.csv')
    stages = [_stage([{'useDb': True, 'id': 'e1'}]), _stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')]
    processor, _ = _processor(tmp_path, stages, inputs=['1'])
    processor.run('main_06-09')
    assert "Stage 'main_06-09' has 2 variants" in capsys.readouterr().out
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_names_files_after_stage_id_when_variant_has_none(tmp_path):
    _write_operator_stats(tmp_path / 'stats.csv')
    stage = _stage([{'useDb': True, 'id': 'e1'}])
    del stage['excel']['stageId']
    processor, _ = _processor(tmp_path, [stage])
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').exists()


def test_processor_saves_csvs_without_index_and_overwrites(tmp_path):
    _write_operator_stats(tmp_path / 'stats.csv')
    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    processor.run('main_06-09')
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').read_text().startswith('enemy_id,')
    assert (tmp_path / 'io' / 'stage_main_06-09_matchups.csv').read_text().startswith('operator_id,')


def test_processor_missing_damage_type_error_names_file(tmp_path, caplog):
    _write_operator_stats(tmp_path / 'stats.csv', include_damage_type=False)
    processor, _ = _processor(tmp_path, [])
    with caplog.at_level(logging.ERROR):
        processor.run('main_06-09')
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].getMessage() == f"{tmp_path / 'stats.csv'} is missing columns ['damage_type']. Rerun the stats step first."


def test_skipped_enemy_and_no_enemies_logged_as_warnings(tmp_path, caplog):
    _write_operator_stats(tmp_path / 'stats.csv')
    processor, _ = _processor(tmp_path, [_stage([{'useDb': False, 'id': 'custom'}])])
    with caplog.at_level(logging.WARNING):
        processor.run('main_06-09')
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, 'Skipping custom: stage-defined enemies (useDb = false) are not supported.'),
        (logging.WARNING, 'No enemies found for stage main_06-09'),
    ]


def test_print_matchup_tables_pivots_by_enemy_and_keeps_operator_order(capsys):
    from app.stage_matchup_flow import print_matchup_tables
    enemy_df = pd.DataFrame([{'name': name, 'level': 0, 'spawn_count': 1, 'maxHp': 1, 'atk': 1, 'def': 0, 'magicResistance': 0,
                              'damage_type': 'PHYSICAL', 'apply_way': 'MELEE', 'attack_interval': 1, 'dps': 1}
                             for name in ('Wolf', 'Yeti')])
    matchups = pd.DataFrame([
        {'operator_name': op, 'enemy_name': enemy, 'enemy_level': 0, 'operator_dps_to_enemy': 1.0, 'enemy_dps_to_operator': 2.0}
        for op in ('Zeta', 'Alpha') for enemy in ('Wolf', 'Yeti')
    ])
    print_matchup_tables(enemy_df, matchups)
    out = capsys.readouterr().out
    operator_section = out.split('Operator DPS to each enemy:')[1].split('Enemy DPS to each operator:')[0]
    enemy_section = out.split('Enemy DPS to each operator:')[1]
    for section in (operator_section, enemy_section):
        assert 'Wolf' in section and 'Yeti' in section
        assert section.index('Zeta') < section.index('Alpha')


def test_required_operator_columns_are_produced_by_operator_stats():
    from app.stage_matchup_flow import REQUIRED_OPERATOR_COLUMNS
    from lib.operator_stats import OperatorStats
    from conftest import make_operator_data
    produced = OperatorStats('char_test', 1, 0, 0, operator_data=make_operator_data()).to_dict()
    assert [column for column in REQUIRED_OPERATOR_COLUMNS if column not in produced] == []


def test_processor_lists_every_missing_column(tmp_path, caplog):
    pd.DataFrame([{'operator_id': 'a', 'name': 'A', 'atk': 1}]).to_csv(tmp_path / 'stats.csv', index=False)
    processor, fetched = _processor(tmp_path, [])
    with caplog.at_level(logging.ERROR):
        assert processor.run('main_06-09') is None
    assert fetched == []
    assert "missing columns ['damage_type', 'attack_interval', 'def', 'magicResistance', 'maxHp']" in caplog.text
