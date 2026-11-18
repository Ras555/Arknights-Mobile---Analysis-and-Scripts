import logging

import pandas as pd
import pytest

from arkdata.sources.hella_api import HellaAPIError
from arknights_calc.flows.matchups import (
    ELITE_2_PHASE,
    ENEMY_TABLE_COLUMNS,
    StageMatchupProcessor,
    choose_stage_variant,
    print_matchup_tables,
)
from arknights_calc.storage import Storage
from factories import make_enemy_data, make_enemy_stats, make_operator_value, make_talent_candidate


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

ROSTER_COLUMNS = ['id', 'potential', 'level', 'phase', 'trust', 'skill_level', 'skill_1_mastery', 'skill_2_mastery',
                  'skill_3_mastery', 'selected_skill']


def _storage(tmp_path):
    return Storage(tmp_path / 'ref', tmp_path / 'io')


def _write_roster(tmp_path, rows=None):
    # Each row: (id, phase); every operator is at level 90 (or its phase's maximum), potential 0, trust 0.
    rows = rows if rows is not None else [('char_a', 2)]
    levels = {0: 50, 1: 70, 2: 90}
    roster_file = _storage(tmp_path).roster_file
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([[operator_id, 0, levels[phase], phase, 0, 1, 0, None, None, 1] for operator_id, phase in rows],
                 columns=ROSTER_COLUMNS).to_csv(roster_file, index=False)


def _stage(enemy_refs, stage_id='main_06-09'):
    return {'excel': {'code': '6-9', 'name': 'Test', 'difficulty': 'NORMAL', 'stageId': stage_id},
            'levels': {'enemyDbRefs': enemy_refs, 'waves': [
                {'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 2}]}]}]}}


def _operator(operator_id):
    # ATK 640, attack interval 1.0 s at Elite 2 level 90 (make_operator_value).
    return make_operator_value(name=f'Op {operator_id}', operator_id=operator_id)


def _processor(tmp_path, stages, inputs=(), fetch_operator=_operator, fetch_deployable=None):
    fetched = []

    def fake_fetch(stage_id):
        fetched.append(stage_id)
        return stages

    processor = StageMatchupProcessor(
        _storage(tmp_path), fetch_stage=fake_fetch, make_enemy=lambda enemy_id, level, overwritten: make_enemy_stats(),
        input_func=_inputs(*inputs), fetch_operator=fetch_operator, fetch_deployable=fetch_deployable or _operator,
    )
    return processor, fetched


def test_processor_run_saves_outputs_and_prints_tables(tmp_path, capsys):
    _write_roster(tmp_path)
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])

    enemy_df, matchups = processor.run('main_06-09')

    assert fetched == ['main_06-09']
    assert enemy_df['spawn_count'].tolist() == [2]
    # 640 ATK against the enemy's 100 DEF.
    assert matchups['avg_dps'].tolist() == [pytest.approx(540)]
    saved_enemies = pd.read_csv(tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv')
    saved_matchups = pd.read_csv(tmp_path / 'io' / 'stage_main_06-09_matchups.csv')
    assert saved_enemies['name'].tolist() == ['Test Enemy']
    assert saved_matchups['operator_id'].tolist() == ['char_a']
    out = capsys.readouterr().out
    assert 'Enemy stats:' in out
    assert 'Average DPS to each enemy (best skill rotation against it):' in out
    assert 'Enemy DPS to each operator (with no skill):' in out


def test_processor_asks_for_stage_id_when_not_given(tmp_path):
    _write_roster(tmp_path)
    stages = [_stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')]
    processor, fetched = _processor(tmp_path, stages, inputs=[' main_06-09 '])
    processor.run()
    assert processor.input_func.prompts == ['Enter a stage id (e.g. main_01-07): ']
    assert fetched == ['main_06-09']
    # Output files are named after the chosen variant's stageId.
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_compares_only_elite_2_operators(tmp_path, caplog):
    _write_roster(tmp_path, [('char_e0', 0), ('char_e2', 2), ('char_e1', 1)])
    fetched = []
    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])],
                              fetch_operator=lambda operator_id: fetched.append(operator_id) or _operator(operator_id))
    with caplog.at_level(logging.INFO, logger='arknights_calc.flows.matchups'):
        _, matchups = processor.run('main_06-09')
    assert matchups['operator_id'].tolist() == ['char_e2']
    assert fetched == ['char_e2']
    assert ELITE_2_PHASE == 2
    assert 'Comparing 1 Elite 2 operators' in caplog.text


def test_processor_without_elite_2_operators_warns_before_asking_for_a_stage(tmp_path, caplog):
    _write_roster(tmp_path, [('char_a', 0), ('char_b', 1)])
    processor, fetched = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    with caplog.at_level(logging.WARNING):
        assert processor.run() is None
    assert processor.input_func.prompts == []
    assert fetched == []
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(
        logging.WARNING,
        f'No Elite 2 operators in {_storage(tmp_path).roster_file}. Matchups only compare Elite 2 operators.',
    )]
    assert not (tmp_path / 'io').exists()


def test_processor_without_roster_logs_error(tmp_path, caplog):
    processor, fetched = _processor(tmp_path, [])
    with caplog.at_level(logging.ERROR):
        assert processor.run('main_06-09') is None
    assert fetched == []
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, f'No roster found at {_storage(tmp_path).roster_file}. Run `arknights-calc roster` first.')]


def test_processor_no_enemies_returns_none(tmp_path, caplog):
    _write_roster(tmp_path)
    processor, _ = _processor(tmp_path, [_stage([{'useDb': False, 'id': 'custom'}])])
    with caplog.at_level(logging.WARNING):
        assert processor.run('main_06-09') is None
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, 'Skipping custom: stage-defined enemies (useDb = false) are not supported.'),
        (logging.WARNING, 'No enemies found for stage main_06-09'),
    ]
    assert not (tmp_path / 'io').exists()


def test_processor_includes_summons_with_their_owner(tmp_path):
    _write_roster(tmp_path)
    owner = make_operator_value(name='Owner', operator_id='char_a', tokens=['token_drone'])
    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])], fetch_operator=lambda _: owner,
                              fetch_deployable=lambda summon_id: make_operator_value(name='Drone', operator_id=summon_id))
    _, matchups = processor.run('main_06-09')
    assert matchups[['operator_id', 'operator_name', 'owner']].values.tolist() == [
        ['char_a', 'Owner', ''], ['token_drone', 'Drone', 'Owner']]


def test_processor_rebuilds_operators_and_summons_for_talents_that_fit(tmp_path):
    _write_roster(tmp_path)
    talents = [[make_talent_candidate('When attacking aerial units, ATK +20%', {'atk': 0.2})]]
    owner = make_operator_value(name='Owner', operator_id='char_a', tokens=['token_drone'], talents=talents)
    summon = make_operator_value(name='Drone', operator_id='token_drone', talents=talents)
    flying = make_enemy_data(base_attributes={'def': 0})
    flying['levels']['Value'][0]['enemyData']['motion'] = {'m_defined': True, 'm_value': 'FLY'}
    processor = StageMatchupProcessor(
        _storage(tmp_path), fetch_stage=lambda stage_id: [_stage([{'useDb': True, 'id': 'e1'}])],
        fetch_enemy=lambda enemy_id: flying, input_func=_inputs(), fetch_operator=lambda _: owner,
        fetch_deployable=lambda _: summon,
    )
    _, matchups = processor.run('main_06-09')
    # Both get ATK +20% against the flying enemy: 640 × 1.2.
    assert matchups['min_dps'].tolist() == [pytest.approx(768), pytest.approx(768)]


def test_processor_logs_and_skips_operators_that_fail(tmp_path, caplog):
    _write_roster(tmp_path, [('char_bad', 2), ('char_a', 2)])

    def fetch(operator_id):
        if operator_id == 'char_bad':
            raise HellaAPIError('down')
        return _operator(operator_id)

    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])], fetch_operator=fetch)
    with caplog.at_level(logging.ERROR):
        _, matchups = processor.run('main_06-09')
    assert matchups['operator_id'].tolist() == ['char_a']
    assert [r.getMessage() for r in caplog.records] == [
        'Error building the profile of operator char_bad for matchups: down']


def _matchup(unit, enemy, owner='', avg=1.0, enemy_dps=2.0):
    return {'operator_name': unit, 'owner': owner, 'enemy_name': enemy, 'enemy_level': 0, 'avg_dps': avg,
            'enemy_dps_vs_min_def': enemy_dps}


def _enemy_row(name, **values):
    row = dict.fromkeys(ENEMY_TABLE_COLUMNS, 0)
    row.update(name=name, damage_type='PHYSICAL', apply_way='MELEE', motion='WALK', level_type='NORMAL', **values)
    return row


def test_print_matchup_tables_formatting(capsys):
    enemy_df = pd.DataFrame([_enemy_row('Wolf', spawn_count=2, attack_interval=1.23456)])
    matchups = pd.DataFrame([_matchup('Zeta', 'Wolf', avg=12.345, enemy_dps=6.789), _matchup('Alpha', 'Wolf')])
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


def test_print_matchup_tables_pivots_by_enemy_and_names_summons_with_their_owner(capsys):
    enemy_df = pd.DataFrame([_enemy_row(name) for name in ('Wolf', 'Yeti')])
    matchups = pd.DataFrame([_matchup(unit, enemy, owner) for unit, owner in (('Zeta', ''), ('Drone', 'Alpha'))
                             for enemy in ('Wolf', 'Yeti')])
    print_matchup_tables(enemy_df, matchups)
    out = capsys.readouterr().out
    operator_section = out.split('Average DPS to each enemy')[1].split('Enemy DPS to each operator')[0]
    enemy_section = out.split('Enemy DPS to each operator')[1]
    for section in (operator_section, enemy_section):
        assert 'Wolf' in section and 'Yeti' in section
        assert section.index('Zeta') < section.index('Drone (Alpha)')


def test_processor_choose_variant_uses_stage_id_and_input(tmp_path, capsys):
    _write_roster(tmp_path)
    stages = [_stage([{'useDb': True, 'id': 'e1'}]), _stage([{'useDb': True, 'id': 'e1'}], stage_id='tough_06-09')]
    processor, _ = _processor(tmp_path, stages, inputs=['1'])
    processor.run('main_06-09')
    assert "Stage 'main_06-09' has 2 variants" in capsys.readouterr().out
    assert (tmp_path / 'io' / 'stage_tough_06-09_matchups.csv').exists()


def test_processor_names_files_after_stage_id_when_variant_has_none(tmp_path):
    _write_roster(tmp_path)
    stage = _stage([{'useDb': True, 'id': 'e1'}])
    del stage['excel']['stageId']
    processor, _ = _processor(tmp_path, [stage])
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').exists()


def test_processor_saves_csvs_without_index_and_overwrites(tmp_path):
    _write_roster(tmp_path)
    processor, _ = _processor(tmp_path, [_stage([{'useDb': True, 'id': 'e1'}])])
    processor.run('main_06-09')
    processor.run('main_06-09')
    assert (tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv').read_text().startswith('enemy_id,')
    assert (tmp_path / 'io' / 'stage_main_06-09_matchups.csv').read_text().startswith('operator_id,')


def test_default_make_enemy_builds_enemy_stats_from_fetch_enemy(tmp_path):
    _write_roster(tmp_path)
    fetched = []

    def fake_fetch_enemy(enemy_id):
        fetched.append(enemy_id)
        return make_enemy_data(name='Fetched Enemy')

    stage = _stage([{'useDb': True, 'id': 'e1', 'level': 0}])
    processor = StageMatchupProcessor(
        _storage(tmp_path), fetch_stage=lambda stage_id: [stage], fetch_enemy=fake_fetch_enemy, input_func=_inputs(),
        fetch_operator=_operator, fetch_deployable=_operator,
    )
    enemy_df, _ = processor.run('main_06-09')
    assert fetched == ['e1']
    assert enemy_df['name'].tolist() == ['Fetched Enemy']
    assert enemy_df['enemy_id'].tolist() == ['e1']


def test_processor_defaults_to_single_attempt_fetch_functions(tmp_path):
    from arkdata.sources import hella_api
    processor = StageMatchupProcessor(_storage(tmp_path))
    assert processor.fetch_stage is hella_api.fetch_stage_data
    assert processor.fetch_enemy is hella_api.fetch_enemy_data
    assert processor.fetch_operator is hella_api.fetch_operator_value
    assert processor.fetch_deployable is hella_api.fetch_deployable_data
    assert processor.make_enemy == processor._make_enemy


def test_default_make_enemy_applies_stage_overwritten_data(tmp_path):
    _write_roster(tmp_path)
    stage = _stage([{'useDb': True, 'id': 'e1', 'level': 0,
                     'overwrittenData': {'attributes': {'atk': {'m_defined': True, 'm_value': 999}}}}])
    processor = StageMatchupProcessor(
        _storage(tmp_path), fetch_stage=lambda stage_id: [stage], fetch_enemy=lambda enemy_id: make_enemy_data(),
        input_func=_inputs(), fetch_operator=_operator, fetch_deployable=_operator,
    )
    enemy_df, _ = processor.run('main_06-09')
    assert enemy_df['atk'].tolist() == [999]
