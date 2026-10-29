import logging
from pathlib import Path

import pytest

import arknights_calc.cli as cli
from arknights_calc.settings import Settings


def _settings(tmp_path, email='me@example.com'):
    return Settings(project_root=tmp_path, user_email=email, ref_dir=tmp_path / 'ref', io_dir=tmp_path / 'io', log_dir=tmp_path / 'logs')


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

@pytest.fixture
def fake_processors(monkeypatch):
    calls = []

    class FakeRoster:
        def __init__(self, email, storage):
            calls.append(('roster init', email, storage.ref_dir))

        async def run(self):
            calls.append('roster')

    class FakeStats:
        def __init__(self, storage):
            calls.append(('stats init', storage.ref_dir))

        def run(self):
            calls.append('stats')

    class FakeMatchups:
        def __init__(self, storage):
            calls.append(('matchups init', storage.io_dir))

        def run(self, stage_id):
            calls.append(('matchups', stage_id))

    monkeypatch.setattr(cli, 'RosterProcessor', FakeRoster)
    monkeypatch.setattr(cli, 'OperatorStatsProcessor', FakeStats)
    monkeypatch.setattr(cli, 'StageMatchupProcessor', FakeMatchups)
    return calls


@pytest.mark.parametrize('step, expected', [
    ('roster', ['roster']),
    ('stats', ['stats']),
    ('matchups', [('matchups', 'main_06-09')]),
    ('all', ['roster', 'stats', ('matchups', 'main_06-09')]),
])
def test_run_steps_runs_selected_steps_in_order(tmp_path, fake_processors, step, expected):
    cli.run_steps(step, _settings(tmp_path), 'main_06-09')
    assert [call for call in fake_processors if 'init' not in call[0]] == expected


def test_run_steps_passes_settings_email_and_storage(tmp_path, fake_processors):
    cli.run_steps('all', _settings(tmp_path), None)
    assert ('roster init', 'me@example.com', tmp_path / 'ref') in fake_processors
    assert ('stats init', tmp_path / 'ref') in fake_processors
    assert ('matchups init', tmp_path / 'io') in fake_processors


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
    assert fake_main_setup == [('reconfigure', {'encoding': 'utf-8', 'errors': 'replace'}), ('logging', tmp_path / 'logs' / 'arknights.log')]
    assert ('matchups', 'main_01-07') in fake_processors


def test_main_logs_crash_and_returns_1(monkeypatch, fake_main_setup, caplog):
    def crash(step, settings, stage_id):
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
