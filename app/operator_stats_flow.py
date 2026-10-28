#File Documentation
'''
File: operator_stats_flow.py
Description: Implements the stats step: for each operator in the saved
    Elite 2 roster, retrieve its game data from the HellaAPI and calculate
    its stats, damage type, and DPS at its current phase, level, potential,
    and trust.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import logging
import os

import pandas as pd

from lib.hella_api_client import fetch_operator_data, HellaAPIError
from lib.operator_stats import OperatorStats

from app import config

logger = logging.getLogger(__name__)


class OperatorStatsProcessor:
    '''
    Calculates the stats of every operator in the roster file (CLAUDE.md
    Program Flow, stats step) and saves them to every file in output_files.

    fetch_operator is a function taking an operator id and returning its
    HellaAPI data. It is injectable so tests can avoid network calls.
    '''

    def __init__(self, operators_file=None, output_files=None, fetch_operator=fetch_operator_data):
        self.operators_file = operators_file or config.ELITE2_OPERATORS_FILE
        self.output_files = output_files or [config.OPERATOR_STATS_FILE, config.OPERATOR_STATS_VIEW_FILE]
        self.fetch_operator = fetch_operator

    def run(self) -> pd.DataFrame:
        target_operators = pd.read_csv(self.operators_file)
        logger.info('Calculating stats for %d operators from %s', len(target_operators), self.operators_file)

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
        for output_file in self.output_files:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            stats_df.to_csv(output_file, index=False)
            logger.info('Saved operator stats to %s', output_file)
        return stats_df
