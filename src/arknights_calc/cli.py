#File Documentation
'''
File: cli.py
Description: The program's command line entry point. Runs one step of the
    program (roster, stats, or matchups), or all of them in order. Installed
    as the `arknights-calc` command.
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
import sys
from enum import StrEnum

from arknights_calc.flows.matchups import StageMatchupProcessor
from arknights_calc.flows.roster import RosterProcessor
from arknights_calc.flows.stats import OperatorStatsProcessor
from arknights_calc.logging_setup import setup_logging
from arknights_calc.settings import load_settings
from arknights_calc.storage import Storage

logger = logging.getLogger(__name__)

# Constants

class Step(StrEnum):
    '''
    The steps the program can run. ALL runs every other step in order.
    '''
    ROSTER = 'roster'
    STATS = 'stats'
    MATCHUPS = 'matchups'
    ALL = 'all'


STEPS = [step.value for step in Step]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='arknights-calc', description='Arknights damage and stat calculations.')
    parser.add_argument('step', choices=STEPS,
                        help='roster: download your Elite 2 roster. stats: calculate operator stats. '
                             'matchups: compare operators against a stage\'s enemies. all: run every step in order.')
    parser.add_argument('--stage', default=None,
                        help='Stage id for the matchups step (e.g. main_06-09). Asked for if not given.')
    return parser.parse_args(argv)


def run_steps(step, settings, stage_id=None):
    '''
    Runs the given step, or every step in order for 'all'.
    '''
    storage = Storage.from_settings(settings)
    if step in (Step.ROSTER, Step.ALL):
        logger.info('Running roster step')
        asyncio.run(RosterProcessor(settings.user_email, storage).run())
    if step in (Step.STATS, Step.ALL):
        logger.info('Running stats step')
        OperatorStatsProcessor(storage).run()
    if step in (Step.MATCHUPS, Step.ALL):
        logger.info('Running matchups step')
        StageMatchupProcessor(storage).run(stage_id)


def main(argv=None) -> int:
    '''
    Runs the program and returns its exit code: 0 on success, 1 if it
    crashed (the error is logged).
    '''
    # Enemy names can contain characters (e.g. "α") the Windows console codepage can't print.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    args = parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_file)
    logger.info('Starting Arknights Damage and Stat Calculation: %s', args.step)
    try:
        run_steps(args.step, settings, args.stage)
    except Exception:
        logger.exception('Arknights Damage and Stat Calculation crashed')
        return 1
    logger.info('Finished %s', args.step)
    return 0
