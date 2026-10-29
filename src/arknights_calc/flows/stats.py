#File Documentation
'''
File: stats.py
Description: Implements the stats step: for each operator in the saved
    Elite 2 roster, retrieve its game data from the HellaAPI and calculate
    its stats, damage type, and DPS at its current phase, level, potential,
    and trust.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-29
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.calculations.operator_stats import OperatorStats
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
        self.storage = storage
        self.fetch_operator = fetch_operator

    def run(self) -> pd.DataFrame:
        target_operators = self.storage.load_elite2_operators()
        logger.info('Calculating stats for %d operators from %s', len(target_operators), self.storage.elite2_operators_file)

        operator_stats = []
        for _, row in target_operators.iterrows():
            try:
                operator = OperatorStats(
                    row['id'], row['level'], row['phase'], row['potential'], row['trust'],
                    operator_data=self.fetch_operator(row['id']),
                )
            except (HellaAPIError, ValueError, KeyError) as exc:
                logger.exception('Error calculating stats for operator %s: %s', row['id'], exc)
                continue
            operator_stats.append(operator.to_dict())

        stats_df = pd.DataFrame(operator_stats)
        for output_file in self.storage.save_operator_stats(stats_df):
            logger.info('Saved operator stats to %s', output_file)
        return stats_df
