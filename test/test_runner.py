import logging
import logging.handlers
import sys

import pytest

from app import config
from app import runner


# parse_args

@pytest.mark.parametrize('step', ['roster', 'stats', 'matchups', 'all'])
def test_parse_args_accepts_each_step(step):
    args = runner.parse_args([step])
    assert args.step == step
    assert args.stage is None


def test_parse_args_stage_option():
    assert runner.parse_args(['matchups', '--stage', 'main_06-09']).stage == 'main_06-09'


@pytest.mark.parametrize('argv', [[], ['unknown']])
def test_parse_args_rejects_missing_or_unknown_step(argv):
    with pytest.raises(SystemExit):
        runner.parse_args(argv)


# run_steps

@pytest.fixture
def fake_processors(monkeypatch):
    calls = []

    class FakeRoster:
        async def run(self):
            calls.append('roster')

    class FakeStats:
        def run(self):
            calls.append('stats')

    class FakeMatchups:
        def run(self, stage_id):
            calls.append(('matchups', stage_id))

    monkeypatch.setattr(runner, 'RosterProcessor', FakeRoster)
    monkeypatch.setattr(runner, 'OperatorStatsProcessor', FakeStats)
    monkeypatch.setattr(runner, 'StageMatchupProcessor', FakeMatchups)
    return calls


@pytest.mark.parametrize('step, expected', [
    ('roster', ['roster']),
    ('stats', ['stats']),
    ('matchups', [('matchups', 'main_06-09')]),
    ('all', ['roster', 'stats', ('matchups', 'main_06-09')]),
])
def test_run_steps_runs_selected_steps_in_order(fake_processors, step, expected):
    runner.run_steps(step, 'main_06-09')
    assert fake_processors == expected


# setup_logging and main

@pytest.fixture
def restore_root_logger():
    root_logger = logging.getLogger()
    handlers, level = list(root_logger.handlers), root_logger.level
    yield
    for handler in root_logger.handlers:
        if handler not in handlers:
            handler.close()
    root_logger.handlers = handlers
    root_logger.setLevel(level)


def test_setup_logging_adds_console_and_rotating_file_handlers(tmp_path, restore_root_logger):
    log_dir = tmp_path / 'logs'
    handlers = runner.setup_logging(str(log_dir), str(log_dir / 'test.log'))

    console_handler, file_handler = handlers
    assert console_handler.stream is sys.stdout
    assert console_handler.formatter._fmt == '%(asctime)s %(levelname)s %(name)s: %(message)s'
    assert file_handler.formatter is console_handler.formatter
    assert isinstance(file_handler, logging.handlers.RotatingFileHandler)
    assert file_handler.maxBytes == 10_000_000
    assert file_handler.backupCount == 5
    assert logging.getLogger().level == logging.INFO
    assert all(h in logging.getLogger().handlers for h in handlers)

    logging.getLogger('test').info('hello')
    file_handler.flush()
    assert 'INFO test: hello' in (log_dir / 'test.log').read_text()


def test_setup_logging_defaults_to_config_paths(tmp_path, monkeypatch, restore_root_logger):
    monkeypatch.setattr(config, 'LOG_DIR', str(tmp_path / 'default_logs'))
    monkeypatch.setattr(config, 'LOG_FILE', str(tmp_path / 'default_logs' / 'arknights.log'))
    runner.setup_logging()
    assert (tmp_path / 'default_logs' / 'arknights.log').exists()


def test_main_sets_up_logging_and_runs_step(monkeypatch, fake_processors):
    logging_calls = []
    monkeypatch.setattr(runner, 'setup_logging', lambda: logging_calls.append(True))
    runner.main(['matchups', '--stage', 'main_01-07'])
    assert logging_calls == [True]
    assert fake_processors == [('matchups', 'main_01-07')]


def test_config_paths_are_under_project_root():
    for path in (config.RAW_DATA_FILE, config.ELITE2_OPERATORS_FILE, config.OPERATOR_STATS_FILE):
        assert path.startswith(config.REF_DIR)
    assert config.OPERATOR_STATS_VIEW_FILE.startswith(config.IO_DIR)
    assert config.LOG_FILE.startswith(config.LOG_DIR)
    for directory in (config.REF_DIR, config.IO_DIR, config.LOG_DIR, config.LIB_DIR):
        assert directory.startswith(config.PROJECT_ROOT)


def test_setup_logging_with_existing_dir_and_utf8_file(tmp_path, restore_root_logger):
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    _, file_handler = runner.setup_logging(str(log_dir), str(log_dir / 'test.log'))
    assert file_handler.encoding == 'utf-8'


def test_main_reconfigures_stdout_for_utf8(monkeypatch, fake_processors):
    calls = []

    class FakeStdout:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(runner.sys, 'stdout', FakeStdout())
    monkeypatch.setattr(runner, 'setup_logging', lambda: None)
    runner.main(['stats'])
    assert calls == [{'encoding': 'utf-8', 'errors': 'replace'}]


def test_modules_are_only_imported_by_their_package_names():
    # Importing a module under two names (e.g. damage and lib.damage) loads it
    # twice, which breaks mutmut's test mapping and duplicates classes such as
    # HellaAPIError.
    import sys as system
    bare_names = ['damage', 'operator_stats', 'enemy_stats', 'hella_api_client', 'arknights_account',
                  'config', 'runner', 'roster_flow', 'operator_stats_flow', 'stage_matchup_flow']
    assert [name for name in bare_names if name in system.modules] == []


def test_step_enum_matches_steps_list():
    assert runner.STEPS == ['roster', 'stats', 'matchups', 'all']
    assert runner.Step.ALL == 'all'
    assert runner.parse_args(['stats']).step == runner.Step.STATS
