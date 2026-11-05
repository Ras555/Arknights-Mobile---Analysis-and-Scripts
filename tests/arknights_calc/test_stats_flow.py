import logging

import pandas as pd
import pytest

from arkdata.sources.hella_api import HellaAPIError
from arknights_calc.flows.stats import OperatorStatsProcessor, _whole
from arknights_calc.reports import OPERATOR_STATS_VIEW_COLUMNS
from arknights_calc.storage import Storage
from factories import make_operator_value, make_skill_entry, make_skill_level, make_talent_candidate


def _storage(tmp_path):
    return Storage(tmp_path / 'ref', tmp_path / 'io')


ROSTER_BASE_COLUMNS = ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']


def _write_roster(tmp_path, rows, with_skill_columns=True):
    roster_file = _storage(tmp_path).roster_file
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=ROSTER_BASE_COLUMNS)
    if with_skill_columns:
        df = df.assign(skill_1_mastery=0, skill_2_mastery=None, skill_3_mastery=None, selected_skill=1)
    df.to_csv(roster_file, index=False)


def test_run_calculates_and_saves_to_ref_and_io(tmp_path):
    _write_roster(tmp_path, [['char_a', 1, 30, 0, 50.0, 7], ['char_b', 0, 1, 2, 0.0, 1]])
    fetched = []
    caster = make_operator_value(name='Caster', profession='CASTER', description='Deals Arts damage',
                                 sub_profession='testsub')

    def fake_fetch(operator_id):
        fetched.append(operator_id)
        return caster if operator_id == 'char_b' else make_operator_value(name='Guard')

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
        return make_operator_value()

    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=fake_fetch).run()
    assert result['operator_id'].tolist() == ['char_ok']
    assert 'Error calculating stats for operator char_bad' in caplog.text


def test_run_skips_operators_with_invalid_levels(tmp_path, caplog):
    _write_roster(tmp_path, [['char_a', 0, 99, 0, 0.0, 1]])
    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run()
    assert result.empty
    assert 'char_a' in caplog.text


def test_run_overwrites_previous_results(tmp_path):
    _write_roster(tmp_path, [['char_a', 0, 1, 0, 0.0, 1]])
    processor = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value())
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


def test_run_without_roster_logs_error_and_saves_nothing(tmp_path, caplog):
    fetched = []
    with caplog.at_level(logging.ERROR):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=fetched.append).run()
    assert result is None
    assert fetched == []
    roster_file = tmp_path / 'ref' / 'roster.csv'
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, f'No roster found at {roster_file}. Run `arknights-calc roster` first.')]
    assert not (tmp_path / 'ref' / 'operator_stats_output.csv').exists()


def test_run_calculates_operators_at_every_phase(tmp_path):
    _write_roster(tmp_path, [['char_e0', 0, 30, 0, 0.0, 1], ['char_e1', 0, 1, 1, 0.0, 1], ['char_e2', 0, 1, 2, 0.0, 1]])
    result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run()
    assert result['operator_id'].tolist() == ['char_e0', 'char_e1', 'char_e2']
    assert result['phase'].tolist() == [0, 1, 2]


def test_run_ignores_old_elite2_roster_file(tmp_path, caplog):
    old_file = tmp_path / 'ref' / 'elite2_operators.csv'
    old_file.parent.mkdir(parents=True)
    old_file.write_text('id,potential,level,phase,trust,skill_level\nchar_a,0,1,2,0.0,1\n')
    with caplog.at_level(logging.ERROR):
        assert OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run() is None
    assert 'Run `arknights-calc roster` first.' in caplog.text


def test_run_with_skill_columns_does_not_warn(tmp_path, caplog):
    _write_roster(tmp_path, [['char_a', 0, 1, 0, 0.0, 1]])
    with caplog.at_level(logging.WARNING, logger='arknights_calc.flows.stats'):
        OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run()
    assert [r for r in caplog.records if r.name == 'arknights_calc.flows.stats'] == []


def test_run_warns_when_roster_has_no_skill_columns_and_still_calculates(tmp_path, caplog):
    _write_roster(tmp_path, [['char_a', 0, 1, 0, 0.0, 1]], with_skill_columns=False)
    with caplog.at_level(logging.WARNING):
        result = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run()
    assert result['operator_id'].tolist() == ['char_a']
    roster_file = tmp_path / 'ref' / 'roster.csv'
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == 'arknights_calc.flows.stats'] == [(
        logging.WARNING,
        f'{roster_file} has no skill mastery columns (skill_1_mastery, skill_2_mastery, skill_3_mastery, selected_skill). '
        'Run `arknights-calc roster` to add them.',
    )]


def test_run_lists_only_the_missing_skill_columns(tmp_path, caplog):
    _write_roster(tmp_path, [['char_a', 0, 1, 0, 0.0, 1]], with_skill_columns=False)
    roster_file = tmp_path / 'ref' / 'roster.csv'
    pd.read_csv(roster_file).assign(skill_1_mastery=0, selected_skill=1).to_csv(roster_file, index=False)
    with caplog.at_level(logging.WARNING):
        OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: make_operator_value()).run()
    assert '(skill_2_mastery, skill_3_mastery)' in caplog.text


# Profiles: summary columns, skills, talents, summons and pages

def _levels():
    # 10 skill levels whose ATK bonus shows which level was used (10% per level index).
    return [make_skill_level('ATK +{atk:0%}', {'atk': index / 10}, sp_cost=20, duration=20, name='Boost')
            for index in range(10)]


def _summoner():
    return make_operator_value(
        name='Summoner', skills=[make_skill_entry(*_levels(), skill_id='sk_1')], tokens=['token_a'],
        talents=[[make_talent_candidate('ATK +10%', {'atk': 0.1}, name='Sharp')],
                 [make_talent_candidate('DEF +10%', {'def': 0.1}, phase='PHASE_2', potential=4, name='Tough')]],
    )


def _drone():
    return make_operator_value(operator_id='token_a', name='Drone', profession='TOKEN', sub_profession='notchar1',
                               skills=[make_skill_entry(*_levels(), skill_id='sktok_1')])


def _run_summoner(tmp_path, fetch_deployable=None):
    roster_file = _storage(tmp_path).roster_file
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    # Phase 2 level 2: ATK 550 + 90 / 89 = 551.01, × 1.1 (talent) = 606.11 DPS at 1 attack per second.
    pd.DataFrame([['char_s', 0, 2, 2, 0.0, 7, 2, None, None, 1]], columns=[
        *ROSTER_BASE_COLUMNS, 'skill_1_mastery', 'skill_2_mastery', 'skill_3_mastery', 'selected_skill',
    ]).to_csv(roster_file, index=False)
    processor = OperatorStatsProcessor(_storage(tmp_path), fetch_operator=lambda _: _summoner(),
                                       fetch_deployable=fetch_deployable or (lambda _: _drone()))
    return processor.run()


def test_run_adds_profile_summary_columns(tmp_path):
    result = _run_summoner(tmp_path)
    row = result.iloc[0]
    assert row['min_dps'] == pytest.approx((550 + 90 / 89) * 1.1)
    # Skill level 7 mastery 2 is the 9th level: ATK +80%.
    assert row['max_dps'] == pytest.approx((550 + 90 / 89) * 1.9)
    assert (row['max_dps_skill'], row['summons'], row['modelled_status']) == ('Boost', 'Drone', 'full')
    assert row['dps'] == pytest.approx(550 + 90 / 89)


def test_run_shows_fewer_columns_in_the_io_file(tmp_path):
    _run_summoner(tmp_path)
    ref = pd.read_csv(tmp_path / 'ref' / 'operator_stats_output.csv')
    view = pd.read_csv(tmp_path / 'io' / 'operator_stats_output.csv')
    # The fake data has no tauntLevel, so it's skipped; every other view column is there, in order.
    assert list(view.columns) == [column for column in OPERATOR_STATS_VIEW_COLUMNS if column != 'tauntLevel']
    assert {'spRecoveryPerSec', 'maxDeployCount', 'moveSpeed', 'stunImmune'} <= set(ref.columns) - set(view.columns)
    assert set(view.columns) <= set(ref.columns)


def test_run_rounds_only_the_io_files(tmp_path):
    _run_summoner(tmp_path)
    ref = pd.read_csv(tmp_path / 'ref' / 'operator_stats_output.csv')
    view = pd.read_csv(tmp_path / 'io' / 'operator_stats_output.csv')
    assert ref.loc[0, 'min_dps'] == pytest.approx((550 + 90 / 89) * 1.1, abs=1e-9)
    assert view.loc[0, 'min_dps'] == round((550 + 90 / 89) * 1.1, 2)
    assert view.loc[0, 'min_dps'] != ref.loc[0, 'min_dps']


def test_run_saves_skills_talents_and_summons(tmp_path):
    _run_summoner(tmp_path)
    io_dir = tmp_path / 'io'
    skills = pd.read_csv(io_dir / 'operator_skills.csv')
    assert skills[['operator_id', 'skill_name', 'skill_level', 'mastery']].values.tolist() == [['char_s', 'Boost', 7, 2]]
    talents = pd.read_csv(io_dir / 'operator_talents.csv')
    assert talents[['talent_name', 'unlocked', 'unlocks_at']].values.tolist() == [
        ['Sharp', True, 'Elite 0 level 1'], ['Tough', False, 'Elite 2 level 1, potential 5']]
    summon_stats = pd.read_csv(io_dir / 'summon_stats.csv')
    assert summon_stats[['summon_id', 'name', 'owner_name', 'deployed_by']].values.tolist() == [
        ['token_a', 'Drone', 'Summoner', 'talent or any skill']]
    summon_skills = pd.read_csv(io_dir / 'summon_skills.csv')
    assert summon_skills[['operator_id', 'owner_id', 'mastery']].values.tolist() == [['token_a', 'char_s', 2]]
    page = (io_dir / 'operators' / 'Summoner.md').read_text(encoding='utf-8')
    assert page.startswith('# Summoner\n') and '### Drone' in page


def test_run_skips_summons_that_cant_be_fetched(tmp_path, caplog):
    def missing(summon_id):
        raise HellaAPIError(f'{summon_id} not found')

    with caplog.at_level(logging.WARNING):
        result = _run_summoner(tmp_path, fetch_deployable=missing)
    assert result.loc[0, 'summons'] == ''
    assert 'Summon token_a of char_s is not in the HellaAPI data; skipped' in caplog.text
    assert (tmp_path / 'io' / 'summon_stats.csv').read_text().strip() == ''


@pytest.mark.parametrize('value, default, expected', [(3, 0, 3), (2.0, 0, 2), (None, 0, 0), (float('nan'), 1, 1),
                                                      (pd.NA, 1, 1)])
def test_whole(value, default, expected):
    assert _whole(value, default) == expected
