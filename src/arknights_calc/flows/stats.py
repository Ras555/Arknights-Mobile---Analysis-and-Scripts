#File Documentation
'''
File: stats.py
Description: Implements the stats step: for each operator in the saved
    roster, retrieve its game data from the HellaAPI and calculate
    its stats, damage type, and DPS at its current phase, level, potential,
    and trust, and its profile (skills, talents and summons) at its skill
    level and masteries. Saves the stats table and, for viewing, the
    skills, talents and summon tables and a page per operator.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-05
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.calculations.operator_profile import build_operator_profile, build_summon_profiles, summon_ids
from arkdata.calculations.operator_stats import OperatorStats
from arkdata.sources.account import MASTERY_COLUMNS, SKILL_COLUMNS
from arkdata.sources.hella_api import HellaAPIError, fetch_deployable_data, fetch_operator_value
from arknights_calc.reports import (
    OPERATOR_STATS_VIEW_COLUMNS,
    operator_page,
    skill_rows,
    summary_row,
    summon_stats_row,
    talent_rows,
)

logger = logging.getLogger(__name__)


class OperatorStatsProcessor:
    '''
    Calculates the stats of every operator in the saved roster (CLAUDE.md
    Program Flow, stats step) and saves them through storage.

    fetch_operator is a function taking an operator id and returning its
    whole HellaAPI value ("data" and "skills"); fetch_deployable does the
    same for a summon. They're injectable so tests can avoid network calls.
    '''

    def __init__(self, storage, fetch_operator=fetch_operator_value, fetch_deployable=fetch_deployable_data):
        '''
        Args:
            storage: The Storage the roster is read from and the results saved through.
            fetch_operator: The function used to get each operator's value.
            fetch_deployable: The function used to get each summon's value.
        '''
        self.storage = storage
        self.fetch_operator = fetch_operator
        self.fetch_deployable = fetch_deployable

    def run(self) -> pd.DataFrame | None:
        '''
        Calculates and saves the operator stats, and the skills, talents,
        summon tables and operator pages. Returns the stats, or None if
        there is no saved roster yet.

        Returns:
            One row per operator (OperatorStats.to_dict plus the profile's
            summary columns), or None.
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

        operator_stats, skills, talents, summon_stats, summon_skills = [], [], [], [], []
        pages = {}
        for _, row in target_operators.iterrows():
            skill_level = _whole(row.get('skill_level'), 1)
            masteries = [_whole(row.get(column), 0) for column in MASTERY_COLUMNS]
            try:
                value = self.fetch_operator(row['id'])
                operator = OperatorStats(row['id'], row['level'], row['phase'], row['potential'], row['trust'],
                                         operator_data=value['data'])
                profile = build_operator_profile(value, row['phase'], row['level'], row['potential'], row['trust'],
                                                 skill_level, masteries, row['id'])
                summons = build_summon_profiles(value, self._deployables(value), row['phase'], row['level'],
                                                row['potential'], row['trust'], skill_level, masteries, row['id'])
            # One operator with missing or unexpected data shouldn't stop the
            # rest, so it's logged (with its traceback) and skipped.
            except (HellaAPIError, ValueError, KeyError) as exc:
                logger.exception('Error calculating stats for operator %s: %s', row['id'], exc)
                continue
            operator_stats.append({**operator.to_dict(), **summary_row(profile, [summon.name for summon in summons])})
            skills += skill_rows(profile)
            talents += talent_rows(profile, value['data'])
            for summon in summons:
                summon_stats.append(summon_stats_row(summon, profile, value))
                summon_skills += skill_rows(summon, owner=profile)
            pages[profile.name] = operator_page(profile, value['data'], summons, skill_level)

        stats_df = pd.DataFrame(operator_stats)
        for output_file in self.storage.save_operator_stats(stats_df, OPERATOR_STATS_VIEW_COLUMNS):
            logger.info('Saved operator stats to %s', output_file)
        tables = [pd.DataFrame(rows) for rows in (skills, talents, summon_stats, summon_skills)]
        for output_file in self.storage.save_profile_tables(*tables):
            logger.info('Saved %s', output_file)
        self.storage.save_operator_pages(pages)
        logger.info('Saved %d operator pages to %s', len(pages), self.storage.operator_pages_dir)
        return stats_df

    def _deployables(self, operator_value: dict) -> dict:
        '''
        Returns {summon id: its HellaAPI value} for the operator's summons
        that can be fetched. Missing ones are left out (and logged by
        build_summon_profiles).
        '''
        deployables = {}
        for summon_id in summon_ids(operator_value):
            try:
                deployables[summon_id] = self.fetch_deployable(summon_id)
            except HellaAPIError:
                continue
        return deployables


def _whole(value, default: int) -> int:
    '''
    Returns a roster cell as a whole number, or default when it's blank
    (e.g. the mastery of a skill the operator doesn't have).
    '''
    return default if pd.isna(value) else int(value)
