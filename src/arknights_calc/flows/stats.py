#File Documentation
'''
File: stats.py
Description: Implements the stats step: for each operator in the saved
    roster, retrieve its game data from the HellaAPI and calculate
    its stats, damage type, and DPS at its current phase, level, potential,
    and trust.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.calculations.operator_stats import OperatorStats
from arkdata.sources.account import SKILL_COLUMNS
from arkdata.sources.hella_api import HellaAPIError, fetch_operator_data

logger = logging.getLogger(__name__)


class OperatorStatsProcessor:
    '''
    Calculates the stats of every operator in the saved roster (CLAUDE.md
    Program Flow, stats step) and saves them through storage.

    fetch_operator is a function taking an operator id and returning its
    HellaAPI data. It is injectable so tests can avoid network calls.
    '''

    def __init__(self, storage, fetch_operator=fetch_operator_data):
        '''
        Args:
            storage: The Storage the roster is read from and the stats saved through.
            fetch_operator: The function used to get each operator's data.
        '''
        self.storage = storage
        self.fetch_operator = fetch_operator

    def run(self) -> pd.DataFrame | None:
        '''
        Calculates and saves the operator stats. Returns them, or None if
        there is no saved roster yet.

        Returns:
            One row per operator (see OperatorStats.to_dict), or None.
        '''
        # Without a roster there's nothing to calculate, so stop with a hint
        # instead of crashing on a missing file.
        if not self.storage.roster_file.exists():
            logger.error('No roster found at %s. Run `arknights-calc roster` first.', self.storage.roster_file)
            return None
        target_operators = self.storage.load_roster()
        logger.info('Calculating stats for %d operators from %s', len(target_operators), self.storage.roster_file)
        # Rosters saved before the skill columns were added still work, but
        # the skill calculations will need them, so suggest saving it again.
        missing_columns = [column for column in SKILL_COLUMNS if column not in target_operators.columns]
        if missing_columns:
            logger.warning('%s has no skill mastery columns (%s). Run `arknights-calc roster` to add them.',
                           self.storage.roster_file, ', '.join(missing_columns))

        operator_stats = []
        for _, row in target_operators.iterrows():
            try:
                operator = OperatorStats(
                    row['id'], row['level'], row['phase'], row['potential'], row['trust'],
                    operator_data=self.fetch_operator(row['id']),
                )
            # One operator with missing or unexpected data shouldn't stop the
            # rest, so it's logged (with its traceback) and skipped.
            except (HellaAPIError, ValueError, KeyError) as exc:
                logger.exception('Error calculating stats for operator %s: %s', row['id'], exc)
                continue
            operator_stats.append(operator.to_dict())

        stats_df = pd.DataFrame(operator_stats)
        for output_file in self.storage.save_operator_stats(stats_df):
            logger.info('Saved operator stats to %s', output_file)
        return stats_df
