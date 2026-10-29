import logging

import pandas as pd
import pytest

from arkdata.sources.hella_api import HellaAPIError
from arknights_calc.flows.stats import OperatorStatsProcessor
from arknights_calc.storage import Storage

from factories import make_operator_data


def _storage(tmp_path):
    return Storage(tmp_path / 'ref', tmp_path / 'io')


def _write_roster(tmp_path, rows):
    roster_file = _storage(tmp_path).elite2_operators_file
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=['id', 'potential', 'level', 'phase', 'trust', 'skill_level']).to_csv(roster_file, index=False)


def test_run_calculates_and_saves_to_ref_and_io(tmp_path):
    _write_roster(tmp_path, [['char_a', 1, 30, 0, 50.0, 7], ['char_b', 0, 1, 2, 0.0, 1]])
    fetched = []
    caster = make_operator_data(name='Caster', profession='CASTER', description='Deals Arts damage')

    def fake_fetch(operator_id):
        fetched.append(operator_id)
        return caster if operator_id == 'char_b' else make_operator_data(name='Guard')

    result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=fake_fetch).run()

    assert fetched == ['char_a', 'char_b']
    assert result['name'].tolist() == ['Guard', 'Caster']
    assert result['damage_type'].tolist() == ['PHYSICAL', 'ARTS']
    assert result.loc[0, 'level'] == 30
    assert result.loc[0, 'trust'] == 50.0
    for output in (tmp_path / 'ref' / 'operator_stats_output.csv', tmp_path / 'io' / 'operator_stats_output.csv'):
        saved = pd.read_csv(output)
        assert saved['operator_id'].tolist() == ['char_a', 'char_b']
        assert 'damage_type' in saved.columns


@pytest.mark.parametrize('error', [HellaAPIError('down'), KeyError('name')])
def test_run_logs_and_skips_operators_that_fail(tmp_path, caplog, error):
    _write_roster(tmp_path, [['char_bad', 0, 1, 0, 0.0, 1], ['char_ok', 0, 1, 0, 0.0, 1]])

    def fake_fetch(operator_id):
        if operator_id == 'char_bad':
            raise error
        return make_operator_data()

    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=fake_fetch).run()
    assert result['operator_id'].tolist() == ['char_ok']
    assert 'Error calculating stats for operator char_bad' in caplog.text


def test_run_skips_operators_with_invalid_levels(tmp_path, caplog):
    _write_roster(tmp_path, [['char_a', 0, 99, 0, 0.0, 1]])
    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_data()).run()
    assert result.empty
    assert 'char_a' in caplog.text


def test_run_overwrites_previous_results(tmp_path):
    _write_roster(tmp_path, [['char_a', 0, 1, 0, 0.0, 1]])
    processor = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_data())
    processor.run()
    processor.run()
    assert len(pd.read_csv(tmp_path / 'ref' / 'operator_stats_output.csv')) == 1


def test_run_logs_failed_operator_id_as_error(tmp_path, caplog):
    _write_roster(tmp_path, [['char_bad', 0, 1, 0, 0.0, 1]])

    def fake_fetch(operator_id):
        raise HellaAPIError('down')

    with caplog.at_level(logging.ERROR):
        OperatorStatsProcessor(_storage(tmp_path), fetch_operator=fake_fetch).run()
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].getMessage() == 'Error calculating stats for operator char_bad: down'
