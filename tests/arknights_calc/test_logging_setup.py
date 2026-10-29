import logging
import logging.handlers
import sys

import pytest

from arknights_calc.logging_setup import setup_logging


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
    log_file = tmp_path / 'logs' / 'test.log'
    console_handler, file_handler = setup_logging(log_file)

    assert console_handler.stream is sys.stdout
    assert console_handler.formatter._fmt == '%(asctime)s %(levelname)s %(name)s: %(message)s'
    assert file_handler.formatter is console_handler.formatter
    assert isinstance(file_handler, logging.handlers.RotatingFileHandler)
    assert file_handler.maxBytes == 10_000_000
    assert file_handler.backupCount == 5
    assert file_handler.encoding == 'utf-8'
    assert logging.getLogger().level == logging.INFO
    assert all(h in logging.getLogger().handlers for h in (console_handler, file_handler))

    logging.getLogger('test').info('hello α')
    file_handler.flush()
    assert 'INFO test: hello α' in log_file.read_text(encoding='utf-8')


def test_setup_logging_with_existing_log_folder(tmp_path, restore_root_logger):
    (tmp_path / 'logs').mkdir()
    setup_logging(str(tmp_path / 'logs' / 'test.log'))
    assert (tmp_path / 'logs' / 'test.log').exists()


def test_setup_logging_creates_missing_parent_folders(tmp_path, restore_root_logger):
    setup_logging(tmp_path / 'a' / 'b' / 'logs' / 'test.log')
    assert (tmp_path / 'a' / 'b' / 'logs' / 'test.log').exists()
