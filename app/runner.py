#File Documentation
'''
File: runner.py
Description: The file to run the project. Runs one step of the program
    (roster, stats, or matchups), or all of them in order.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
from enum import StrEnum

# The project root goes on sys.path so the lib and app packages can be imported
# by their full names (e.g. lib.damage, app.config). Modules are only imported
# this way so each one is loaded once, under one name.
APP_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import config
from app.operator_stats_flow import OperatorStatsProcessor
from app.roster_flow import RosterProcessor
from app.stage_matchup_flow import StageMatchupProcessor

logger = logging.getLogger(__name__)

# Constants

class Step(StrEnum):
    '''
    The steps runner.py can run. ALL runs every other step in order.
    '''
    ROSTER = 'roster'
    STATS = 'stats'
    MATCHUPS = 'matchups'
    ALL = 'all'


STEPS = [step.value for step in Step]


def setup_logging(log_dir=None, log_file=None):
    log_dir = log_dir or config.LOG_DIR
    log_file = log_file or config.LOG_FILE
    os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    return [console_handler, file_handler]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Arknights damage and stat calculations.')
    parser.add_argument('step', choices=STEPS,
                        help='roster: download your Elite 2 roster. stats: calculate operator stats. '
                             'matchups: compare operators against a stage\'s enemies. all: run every step in order.')
    parser.add_argument('--stage', default=None,
                        help='Stage id for the matchups step (e.g. main_06-09). Asked for if not given.')
    return parser.parse_args(argv)


def run_steps(step, stage_id=None):
    '''
    Runs the given step, or every step in order for 'all'.
    '''
    if step in (Step.ROSTER, Step.ALL):
        logger.info('Running roster step')
        asyncio.run(RosterProcessor().run())
    if step in (Step.STATS, Step.ALL):
        logger.info('Running stats step')
        OperatorStatsProcessor().run()
    if step in (Step.MATCHUPS, Step.ALL):
        logger.info('Running matchups step')
        StageMatchupProcessor().run(stage_id)


def main(argv=None):
    # Enemy names can contain characters (e.g. "α") the Windows console codepage can't print.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    args = parse_args(argv)
    setup_logging()
    logger.info('Starting Arknights Damage and Stat Calculation: %s', args.step)
    run_steps(args.step, args.stage)
    logger.info('Finished %s', args.step)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logger.exception('Arknights Damage and Stat Calculation crashed')
        sys.exit(1)
