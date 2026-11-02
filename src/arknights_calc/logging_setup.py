#File Documentation
'''
File: logging_setup.py
Description: Sets up logging to the console and to a rotating log file.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import logging
import logging.handlers
import sys
from pathlib import Path

# Constants

LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
# Once the log file reaches 10 MB it's renamed (arknights.log.1, .2, ...) and
# a new one is started. Only 5 old files are kept, so logs never pass ~60 MB.
LOG_FILE_MAX_BYTES = 10_000_000
LOG_FILE_BACKUP_COUNT = 5


def setup_logging(log_file: Path) -> list:
    '''
    Sends INFO and above to stdout and to log_file (rotating), creating the
    log folder if needed. Returns [console_handler, file_handler].

    Args:
        log_file: The log file's path.

    Returns:
        The two handlers added, so tests can remove them again.
    '''
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Handlers go on the root logger, so every module's
    # logging.getLogger(__name__) logger sends its messages through them.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT)

    # stdout rather than stderr, so log messages appear in order with the
    # tables the program prints.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # utf-8 so names like "α" can always be written, whatever the system's encoding.
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUP_COUNT, encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    return [console_handler, file_handler]
