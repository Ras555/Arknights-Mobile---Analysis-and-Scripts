import logging

import pandas as pd
import pytest

from arknights_calc.flows.matchups import StageMatchupProcessor, choose_stage_variant, print_matchup_tables
from arknights_calc.storage import Storage

from factories import make_enemy_stats, operator_stats_row


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


# StageMatchupProcessor

def _stats_file(tmp_path):
    return tmp_path / 'ref' / 'operator_stats_output.csv'


def _write_operator_stats(tmp_path, include_damage_type=True):
    row = operator_stats_row(phase=2, profession='GUARD')
    if not include_damage_type:
        row.pop('damage_type')
    _stats_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(_stats_file(tmp_path), index=False)


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
        Storage(tmp_path / 'ref', tmp_path / 'io'),
        fetch_stage=fake_fetch, make_enemy=lambda enemy_id, level, overwritten: make_enemy_stats(),
        input_func=_inputs(*inputs),
    )
    return processor, fetched


def test_processor_run_saves_outputs_and_prints_tables(tmp_path, capsys):
    _write_operator_stats(tmp_path)
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
    _write_operator_stats(tmp_path)
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')], inputs=[' main_06-09 '])
    processor.run()
    assert processor.input_func.prompts == ['Enter a stage id (e.g. main_01-07): ']
    assert fetched == ['main_06-09']
    # Output files are named after the chosen variant's stageId.
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_requires_damage_type_column(tmp_path, caplog):
    _write_operator_stats(tmp_path, include_damage_type=False)
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    with caplog.at_level(logging.ERROR):
        assert processor.run('main_06-09') is None
    assert fetched == []
    assert "missing columns ['damage_type']" in caplog.text


def test_processor_no_enemies_returns_none(tmp_path, caplog):
    _write_operator_stats(tmp_path)
    processor, _ = _processor(tmp_path, [_stage([{'useDb': False, 'id': 'custom'}])])
    with caplog.at_level(logging.WARNING):
        assert processor.run('main_06-09') is None
    assert 'No enemies found for stage main_06-09' in caplog.text
    assert not (tmp_path / 'io').exists()


def test_print_matchup_tables_formatting(capsys):
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
    _write_operator_stats(tmp_path)
    stages = [_stage([{'useDb': True, 'id': 'e1'}]), _stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')]
    processor, _ = _processor(tmp_path, stages, inputs=['1'])
    processor.run('main_06-09')
    assert "Stage 'main_06-09' has 2 variants" in capsys.readouterr().out
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_names_files_after_stage_id_when_variant_has_none(tmp_path):
    _write_operator_stats(tmp_path)
    stage = _stage([{'useDb': True, 'id': 'e1'}])
    del stage['excel']['stageId']
    processor, _ = _processor(tmp_path, [stage])
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').exists()


def test_processor_saves_csvs_without_index_and_overwrites(tmp_path):
    _write_operator_stats(tmp_path)
    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    processor.run('main_06-09')
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').read_text().startswith('enemy_id,')
    assert (tmp_path / 'io' / 'stage_main_06-09_matchups.csv').read_text().startswith('operator_id,')


def test_processor_missing_damage_type_error_names_file(tmp_path, caplog):
    _write_operator_stats(tmp_path, include_damage_type=False)
    processor, _ = _processor(tmp_path, [])
    with caplog.at_level(logging.ERROR):
        processor.run('main_06-09')
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].getMessage() == f"{_stats_file(tmp_path)} is missing columns ['damage_type']. Rerun the stats step first."


def test_skipped_enemy_and_no_enemies_logged_as_warnings(tmp_path, caplog):
    _write_operator_stats(tmp_path)
    processor, _ = _processor(tmp_path, [_stage([{'useDb': False, 'id': 'custom'}])])
    with caplog.at_level(logging.WARNING):
        processor.run('main_06-09')
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, 'Skipping custom: stage-defined enemies (useDb = false) are not supported.'),
        (logging.WARNING, 'No enemies found for stage main_06-09'),
    ]


def test_print_matchup_tables_pivots_by_enemy_and_keeps_operator_order(capsys):
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


def test_processor_lists_every_missing_column(tmp_path, caplog):
    _stats_file(tmp_path).parent.mkdir(parents=True)
    pd.DataFrame([{'operator_id': 'a', 'name': 'A', 'atk': 1}]).to_csv(_stats_file(tmp_path), index=False)
    processor, fetched = _processor(tmp_path, [])
    with caplog.at_level(logging.ERROR):
        assert processor.run('main_06-09') is None
    assert fetched == []
    assert "missing columns ['damage_type', 'attack_interval', 'def', 'magicResistance', 'maxHp']" in caplog.text
