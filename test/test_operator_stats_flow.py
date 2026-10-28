import logging

import pandas as pd
import pytest

from app import config
from lib.hella_api_client import HellaAPIError
from app.operator_stats_flow import OperatorStatsProcessor

from conftest import make_operator_data


def _write_roster(path, rows):
    pd.DataFrame(rows, columns=['id', 'potential', 'level', 'phase', 'trust', 'skill_level']).to_csv(path, index=False)


def test_defaults_come_from_config():
    processor = OperatorStatsProcessor()
    assert processor.operators_file == config.ELITE2_OPERATORS_FILE
    assert processor.output_files == [config.OPERATOR_STATS_FILE, config.OPERATOR_STATS_VIEW_FILE]


def test_config_writes_stats_to_ref_and_io():
    assert config.OPERATOR_STATS_FILE.startswith(config.REF_DIR)
    assert config.OPERATOR_STATS_VIEW_FILE.startswith(config.IO_DIR)


def test_run_calculates_and_saves_to_every_output_file(tmp_path):
    roster = tmp_path / 'roster.csv'
    _write_roster(roster, [['char_a', 1, 30, 0, 50.0, 7], ['char_b', 0, 1, 2, 0.0, 1]])
    fetched = []
    caster = make_operator_data(name='Caster', profession='CASTER', description='Deals Arts damage')

    def fake_fetch(operator_id):
        fetched.append(operator_id)
        return caster if operator_id == 'char_b' else make_operator_data(name='Guard')

    outputs = [str(tmp_path / 'ref' / 'stats.csv'), str(tmp_path / 'io' / 'stats.csv')]
    result = OperatorStatsProcessor(str(roster), outputs, fetch_operator=fake_fetch).run()

    assert fetched == ['char_a', 'char_b']
    assert result['name'].tolist() == ['Guard', 'Caster']
    assert result['damage_type'].tolist() == ['PHYSICAL', 'ARTS']
    assert result.loc[0, 'level'] == 30
    assert result.loc[0, 'trust'] == 50.0
    for output in outputs:
        saved = pd.read_csv(output)
        assert saved['operator_id'].tolist() == ['char_a', 'char_b']
        assert 'damage_type' in saved.columns


@pytest.mark.parametrize('error', [HellaAPIError('down'), KeyError('name')])
def test_run_logs_and_skips_operators_that_fail(tmp_path, caplog, error):
    roster = tmp_path / 'roster.csv'
    _write_roster(roster, [['char_bad', 0, 1, 0, 0.0, 1], ['char_ok', 0, 1, 0, 0.0, 1]])

    def fake_fetch(operator_id):
        if operator_id == 'char_bad':
            raise error
        return make_operator_data()

    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(str(roster), [str(tmp_path / 'out.csv')], fetch_operator=fake_fetch).run()
    assert result['operator_id'].tolist() == ['char_ok']
    assert 'Error calculating stats for operator char_bad' in caplog.text


def test_run_skips_operators_with_invalid_levels(tmp_path, caplog):
    roster = tmp_path / 'roster.csv'
    _write_roster(roster, [['char_a', 0, 99, 0, 0.0, 1]])
    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(str(roster), [str(tmp_path / 'out.csv')], fetch_operator=lambda _: make_operator_data()).run()
    assert result.empty
    assert 'char_a' in caplog.text


def test_run_saves_csv_without_index_column_and_overwrites(tmp_path):
    roster = tmp_path / 'roster.csv'
    _write_roster(roster, [['char_a', 0, 1, 0, 0.0, 1]])
    output = tmp_path / 'out' / 'stats.csv'
    processor = OperatorStatsProcessor(str(roster), [str(output)], fetch_operator=lambda _: make_operator_data())
    processor.run()
    processor.run()
    assert output.read_text().splitlines()[0].startswith('operator_id,name,')
    assert len(pd.read_csv(output)) == 1


def test_run_logs_failed_operator_id_as_error(tmp_path, caplog):
    roster = tmp_path / 'roster.csv'
    _write_roster(roster, [['char_bad', 0, 1, 0, 0.0, 1]])

    def fake_fetch(operator_id):
        raise HellaAPIError('down')

    with caplog.at_level(logging.ERROR):
        OperatorStatsProcessor(str(roster), [str(tmp_path / 'out.csv')], fetch_operator=fake_fetch).run()
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].getMessage() == 'Error calculating stats for operator char_bad: down'
