#File Documentation
'''
File: cli.py
Description: The program's command line entry point. Runs one step of the
    program (roster, stats, or matchups), or all of them in order. Installed
    as the `arknights-calc` command.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import argparse
import asyncio
import io
import logging
import sys
from enum import StrEnum

from arkdata.sources.hella_api import HellaAPIClient, HellaAPIError
from arkdata.sources.hella_cache import HellaCache
from arknights_calc.flows.matchups import StageMatchupProcessor
from arknights_calc.flows.roster import RosterProcessor, check_roster_age
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


# The step names as plain strings, for argparse's choices.
STEPS = [step.value for step in Step]


def parse_args(argv=None):
    '''
    Reads the command line options.

    Args:
        argv: The arguments to parse (without the program name). None reads
            them from the command line (sys.argv); tests pass a list.

    Returns:
        An argparse.Namespace with step, refresh and stage.
    '''
    parser = argparse.ArgumentParser(prog='arknights-calc', description='Arknights damage and stat calculations.')
    parser.add_argument('step', choices=STEPS,
                        help='roster: download your full roster. stats: calculate every operator\'s stats. '
                             'matchups: compare your Elite 2 operators against a stage\'s enemies. all: run every step in order.')
    parser.add_argument('--refresh', action='store_true',
                        help='Download all HellaAPI data again instead of only what changed (e.g. after a game update).')
    parser.add_argument('--stage', default=None,
                        help='Stage id for the matchups step (e.g. main_06-09). Asked for if not given.')
    return parser.parse_args(argv)


def build_hella_cache(settings) -> HellaCache:
    '''
    Returns a HellaAPI client with the settings' URL, timeouts and retries,
    behind a disk cache in the settings' cache folder.

    Args:
        settings: The program's Settings, from load_settings.

    Returns:
        The HellaCache the stats and matchups steps fetch game data through.
    '''
    client = HellaAPIClient(
        settings.hella_api_url, timeout=settings.request_timeout, bulk_timeout=settings.bulk_timeout,
        retries=settings.request_retries, retry_delay=settings.retry_delay,
    )
    return HellaCache(client, settings.cache_dir, max_age=settings.cache_max_age, bulk_threshold=settings.cache_bulk_threshold)


def sync_hella_cache(hella: HellaCache, refresh: bool = False) -> None:
    '''
    Brings the HellaAPI cache up to date. If the HellaAPI can't be reached,
    logs a warning and carries on with whatever is already cached.

    Args:
        hella: The cache to bring up to date.
        refresh: True to download all HellaAPI data again.
    '''
    # Being offline shouldn't stop a run when the data it needs is already
    # cached; anything that isn't cached will fail when it's fetched instead.
    try:
        hella.sync(refresh)
    except HellaAPIError as exc:
        logger.warning('Could not check the HellaAPI for updates (%s); using cached data where available.', exc)


def run_steps(step, settings, stage_id=None, refresh=False):
    '''
    Runs the given step, or every step in order for 'all'. The stats and
    matchups steps first suggest refreshing an old roster (unless the roster
    step just ran) and bring the HellaAPI cache up to date.

    Args:
        step: A Step, or its plain string value.
        settings: The program's Settings, from load_settings.
        stage_id: The stage for the matchups step; None asks for it.
        refresh: True to download all HellaAPI data again first.
    '''
    storage = Storage.from_settings(settings)
    if step in (Step.ROSTER, Step.ALL):
        logger.info('Running roster step')
        asyncio.run(RosterProcessor(settings.user_email, storage).run())
    # Only the roster step was asked for, so the HellaAPI isn't needed.
    if step not in (Step.STATS, Step.MATCHUPS, Step.ALL):
        return

    # With 'all', the roster was just downloaded, so it can't be old.
    if step != Step.ALL:
        check_roster_age(storage, settings.roster_max_age)
    # Built and synced once, so 'all' shares one cache between both steps.
    hella = build_hella_cache(settings)
    sync_hella_cache(hella, refresh)
    # The processors get the cache's fetch functions, so they read game data
    # from disk where they can and only download what's missing.
    if step in (Step.STATS, Step.ALL):
        logger.info('Running stats step')
        OperatorStatsProcessor(storage, fetch_operator=hella.fetch_operator_data).run()
    if step in (Step.MATCHUPS, Step.ALL):
        logger.info('Running matchups step')
        StageMatchupProcessor(storage, fetch_stage=hella.fetch_stage_data, fetch_enemy=hella.fetch_enemy_data).run(stage_id)


def main(argv=None) -> int:
    '''
    Runs the program and returns its exit code: 0 on success, 1 if it
    crashed (the error is logged).

    Args:
        argv: The command line arguments; None reads them from sys.argv.

    Returns:
        The exit code.
    '''
    # Enemy names can contain characters (e.g. "α") the Windows console codepage can't print.
    # sys.stdout is only a TextIOWrapper (which has reconfigure) when it's a real console or file.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    args = parse_args(argv)
    settings = load_settings()
    # Logging is set up as early as possible, so later failures are logged.
    setup_logging(settings.log_file)
    logger.info('Starting Arknights Damage and Stat Calculation: %s', args.step)
    try:
        run_steps(args.step, settings, args.stage, args.refresh)
    # Any crash is logged with its full traceback (to the console and the
    # log file) and turned into exit code 1, rather than a raw traceback.
    except Exception:
        logger.exception('Arknights Damage and Stat Calculation crashed')
        return 1
    logger.info('Finished %s', args.step)
    return 0
