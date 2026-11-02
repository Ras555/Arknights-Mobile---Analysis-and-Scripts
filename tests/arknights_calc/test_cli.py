import logging
from datetime import timedelta

import pytest

import arknights_calc.cli as cli
from arkdata.sources.hella_api import HellaAPIError
from arknights_calc.settings import Settings


def _settings(tmp_path, email='me@example.com'):
    return Settings(project_root=tmp_path, user_email=email, ref_dir=tmp_path / 'ref', io_dir=tmp_path / 'io',
                    log_dir=tmp_path / 'logs')


# parse_args

@pytest.mark.parametrize('step', ['roster', 'stats', 'matchups', 'all'])
def test_parse_args_accepts_each_step(step):
    args = cli.parse_args([step])
    assert args.step == step
    assert args.stage is None


def test_parse_args_stage_option():
    assert cli.parse_args(['matchups', '--stage', 'main_06-09']).stage == 'main_06-09'


@pytest.mark.parametrize('argv', [[], ['unknown']])
def test_parse_args_rejects_missing_or_unknown_step(argv):
    with pytest.raises(SystemExit):
        cli.parse_args(argv)


def test_parse_args_uses_command_name_in_usage(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(['--help'])
    assert capsys.readouterr().out.startswith('usage: arknights-calc ')


def test_step_enum_matches_steps_list():
    assert cli.STEPS == ['roster', 'stats', 'matchups', 'all']
    assert cli.Step.ALL == 'all'
    assert cli.parse_args(['stats']).step == cli.Step.STATS


# run_steps

class FakeHella:
    def __init__(self, calls, error=None):
        self.calls = calls
        self.error = error

    def sync(self, refresh):
        self.calls.append(('sync', refresh))
        if self.error:
            raise self.error

    def fetch_operator_data(self, key):
        return {'operator': key}

    def fetch_stage_data(self, key):
        return [{'stage': key}]

    def fetch_enemy_data(self, key):
        return {'enemy': key}


@pytest.fixture
def fake_processors(monkeypatch):
    calls = []
    hella = FakeHella(calls)

    class FakeRoster:
        def __init__(self, email, storage):
            calls.append(('roster init', email, storage.ref_dir))

        async def run(self):
            calls.append('roster')

    class FakeStats:
        def __init__(self, storage, fetch_operator):
            calls.append(('stats init', storage.ref_dir, fetch_operator('char_a')))

        def run(self):
            calls.append('stats')

    class FakeMatchups:
        def __init__(self, storage, fetch_stage, fetch_enemy):
            calls.append(('matchups init', storage.io_dir, fetch_stage('s1'), fetch_enemy('e1')))

        def run(self, stage_id):
            calls.append(('matchups', stage_id))

    monkeypatch.setattr(cli, 'RosterProcessor', FakeRoster)
    monkeypatch.setattr(cli, 'OperatorStatsProcessor', FakeStats)
    monkeypatch.setattr(cli, 'StageMatchupProcessor', FakeMatchups)
    monkeypatch.setattr(cli, 'build_hella_cache', lambda settings: calls.append(('build cache', settings.cache_dir)) or hella)
    monkeypatch.setattr(cli, 'check_roster_age', lambda storage, max_age: calls.append(('roster age', max_age, storage.ref_dir)))
    return calls


def _step_calls(calls):
    return [call for call in calls if isinstance(call, str) or call[0] == 'matchups']


@pytest.mark.parametrize('step, expected', [
    ('roster', ['roster']),
    ('stats', ['stats']),
    ('matchups', [('matchups', 'main_06-09')]),
    ('all', ['roster', 'stats', ('matchups', 'main_06-09')]),
])
def test_run_steps_runs_selected_steps_in_order(tmp_path, fake_processors, step, expected):
    cli.run_steps(step, _settings(tmp_path), 'main_06-09')
    assert _step_calls(fake_processors) == expected


def test_run_steps_passes_settings_email_storage_and_cache(tmp_path, fake_processors):
    cli.run_steps('all', _settings(tmp_path), None)
    assert ('roster init', 'me@example.com', tmp_path / 'ref') in fake_processors
    assert ('stats init', tmp_path / 'ref', {'operator': 'char_a'}) in fake_processors
    assert ('matchups init', tmp_path / 'io', [{'stage': 's1'}], {'enemy': 'e1'}) in fake_processors
    assert ('build cache', tmp_path / 'ref' / 'cache') in fake_processors


def test_roster_step_alone_never_touches_the_hella_cache(tmp_path, fake_processors):
    cli.run_steps('roster', _settings(tmp_path))
    assert not [call for call in fake_processors if call[0] in ('build cache', 'sync', 'roster age')]


@pytest.mark.parametrize('step', ['stats', 'matchups'])
def test_stats_and_matchups_check_roster_age_then_sync_once_before_running(tmp_path, fake_processors, step):
    cli.run_steps(step, _settings(tmp_path), refresh=True)
    names = [call if isinstance(call, str) else call[0] for call in fake_processors]
    assert names[:3] == ['roster age', 'build cache', 'sync']
    assert names.count('sync') == 1
    assert ('sync', True) in fake_processors
    assert ('roster age', timedelta(days=7), tmp_path / 'ref') in fake_processors


def test_all_skips_roster_age_check_and_syncs_once(tmp_path, fake_processors):
    cli.run_steps('all', _settings(tmp_path))
    names = [call if isinstance(call, str) else call[0] for call in fake_processors]
    assert 'roster age' not in names
    assert names.count('sync') == 1
    assert names.index('roster') < names.index('sync') < names.index('stats')
    assert ('sync', False) in fake_processors


def test_build_hella_cache_uses_settings(tmp_path):
    settings = Settings(project_root=tmp_path, user_email=None, ref_dir=tmp_path / 'ref', io_dir=tmp_path / 'io',
                        log_dir=tmp_path / 'logs', hella_api_url='http://localhost/api/', request_timeout=3, bulk_timeout=30,
                        request_retries=5, retry_delay=0.5, cache_max_age=timedelta(days=2), cache_bulk_threshold=9)
    hella = cli.build_hella_cache(settings)
    assert hella.cache_dir == tmp_path / 'ref' / 'cache'
    assert (hella.max_age, hella.bulk_threshold) == (timedelta(days=2), 9)
    client = hella.client
    assert (client.base_url, client.timeout, client.bulk_timeout, client.retries, client.retry_delay) == (
        'http://localhost/api', 3, 30, 5, 0.5)


def test_sync_hella_cache_warns_and_continues_when_hellaapi_unreachable(caplog):
    calls = []
    with caplog.at_level(logging.WARNING):
        cli.sync_hella_cache(FakeHella(calls, error=HellaAPIError('down')), refresh=False)
    assert calls == [('sync', False)]
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, 'Could not check the HellaAPI for updates (down); using cached data where available.')]


def test_sync_hella_cache_lets_unexpected_errors_through():
    with pytest.raises(RuntimeError):
        cli.sync_hella_cache(FakeHella([], error=RuntimeError('bug')))


# main

@pytest.fixture
def fake_main_setup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, 'load_settings', lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, 'setup_logging', lambda log_file: calls.append(('logging', log_file)))
    monkeypatch.setattr(cli.sys.stdout, 'reconfigure', lambda **kwargs: calls.append(('reconfigure', kwargs)))
    return calls


def test_main_sets_up_and_runs_step(tmp_path, fake_processors, fake_main_setup):
    assert cli.main(['matchups', '--stage', 'main_01-07']) == 0
    assert fake_main_setup == [
        ('reconfigure', {'encoding': 'utf-8', 'errors': 'replace'}),
        ('logging', tmp_path / 'logs' / 'arknights.log'),
    ]
    assert ('matchups', 'main_01-07') in fake_processors
    assert ('sync', False) in fake_processors


def test_main_passes_refresh_flag(tmp_path, fake_processors, fake_main_setup):
    assert cli.main(['stats', '--refresh']) == 0
    assert ('sync', True) in fake_processors


def test_parse_args_refresh_defaults_to_false():
    assert cli.parse_args(['stats']).refresh is False
    assert cli.parse_args(['stats', '--refresh']).refresh is True


def test_main_logs_crash_and_returns_1(monkeypatch, fake_main_setup, caplog):
    def crash(step, settings, stage_id, refresh):
        raise RuntimeError('boom')

    monkeypatch.setattr(cli, 'run_steps', crash)
    with caplog.at_level(logging.ERROR):
        assert cli.main(['stats']) == 1
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].getMessage() == 'Arknights Damage and Stat Calculation crashed'
    assert 'RuntimeError: boom' in caplog.text


def test_dunder_main_exits_with_main_return_code(monkeypatch):
    import runpy
    monkeypatch.setattr(cli, 'main', lambda: 3)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module('arknights_calc', run_name='__main__')
    assert exc.value.code == 3


def test_main_skips_reconfigure_when_stdout_is_not_a_text_wrapper(tmp_path, monkeypatch, fake_processors):
    import io
    monkeypatch.setattr(cli, 'load_settings', lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, 'setup_logging', lambda log_file: None)
    monkeypatch.setattr(cli.sys, 'stdout', io.StringIO())
    assert cli.main(['stats']) == 0
    assert 'stats' in fake_processors


def test_sync_hella_cache_defaults_to_no_refresh():
    calls = []
    cli.sync_hella_cache(FakeHella(calls))
    assert calls == [('sync', False)]
