#File Documentation
'''
File: profiles.py
Description: Builds the profile of every operator in the saved roster, and
    of its summons, from the HellaAPI data, for the stats and matchups
    steps. Operators whose data is missing or unexpected are logged and
    skipped.
Created: 2026-11-09
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pandas as pd

from arkdata.calculations.matchups import ProfileBuilder
from arkdata.calculations.operator_profile import (
    OperatorProfile,
    build_operator_profile,
    build_summon_profile,
    build_summon_profiles,
    summon_ids,
)
from arkdata.calculations.operator_stats import OperatorStats
from arkdata.sources.account import MASTERY_COLUMNS
from arkdata.sources.hella_api import HellaAPIError

logger = logging.getLogger(__name__)

# Constants

# The message logged when an operator can't be built (the operator's id and the error).
DEFAULT_ERROR_MESSAGE = 'Error building the profile of operator %s: %s'


def whole(value, default: int) -> int:
    '''
    Returns a roster cell as a whole number, or default when it's blank
    (e.g. the mastery of a skill the operator doesn't have).
    '''
    return default if pd.isna(value) else int(value)


@dataclass
class RosterOperator:
    '''
    One roster operator: its roster row, HellaAPI value, stats, profile
    (at its skill level and masteries) and its summons' profiles, with the
    summons' HellaAPI values (deployables) by id.
    '''
    row: pd.Series
    value: dict
    operator: OperatorStats
    profile: OperatorProfile
    summons: list[OperatorProfile]
    deployables: dict[str, dict]
    skill_level: int
    masteries: list[int]

    def builder(self) -> ProfileBuilder:
        '''
        Returns a ProfileBuilder for the operator (for matchups), starting
        with its profile.
        '''
        row = self.row

        def build(extra: frozenset[tuple[int, str]]) -> OperatorProfile:
            return build_operator_profile(self.value, row['phase'], row['level'], row['potential'], row['trust'],
                                          self.skill_level, self.masteries, row['id'], extra)

        return ProfileBuilder(build, profile=self.profile)

    def summon_builders(self) -> list[ProfileBuilder]:
        '''
        Returns a ProfileBuilder for each summon, starting with its profile,
        with the operator's name as the owner.
        '''
        return [self._summon_builder(summon) for summon in self.summons]

    def _summon_builder(self, summon: OperatorProfile) -> ProfileBuilder:
        '''
        Returns the ProfileBuilder of one summon.
        '''
        row = self.row
        deployable = self.deployables[summon.operator_id]

        def build(extra: frozenset[tuple[int, str]]) -> OperatorProfile:
            return build_summon_profile(deployable, row['phase'], row['level'], row['potential'], row['trust'],
                                        self.skill_level, self.masteries, summon.operator_id, row['id'], extra)

        return ProfileBuilder(build, owner=self.profile.name, profile=summon)


def deployables_of(operator_value: dict, fetch_deployable: Callable[[str], dict]) -> dict[str, dict]:
    '''
    Returns {summon id: its HellaAPI value} for the operator's summons that
    can be fetched. Missing ones are left out (and logged by
    build_summon_profiles).
    '''
    deployables = {}
    for summon_id in summon_ids(operator_value):
        try:
            deployables[summon_id] = fetch_deployable(summon_id)
        except HellaAPIError:
            continue
    return deployables


def roster_operators(roster: pd.DataFrame, fetch_operator: Callable[[str], dict],
                     fetch_deployable: Callable[[str], dict],
                     error_message: str = DEFAULT_ERROR_MESSAGE) -> Iterator[RosterOperator]:
    '''
    Yields a RosterOperator for each roster row, built at the row's phase,
    level, potential, trust, skill level and masteries (a blank mastery
    counts as 0). An operator with missing or unexpected data is logged
    (with its traceback, error_message filled with its id and the error)
    and skipped, so it doesn't stop the rest.

    Args:
        roster: The saved roster (Storage.load_roster).
        fetch_operator: Returns an operator's whole HellaAPI value.
        fetch_deployable: Returns a summon's whole HellaAPI value.
        error_message: The message logged for an operator that fails.
    '''
    for _, row in roster.iterrows():
        skill_level = whole(row.get('skill_level'), 1)
        masteries = [whole(row.get(column), 0) for column in MASTERY_COLUMNS]
        try:
            value = fetch_operator(row['id'])
            operator = OperatorStats(row['id'], row['level'], row['phase'], row['potential'], row['trust'],
                                     operator_data=value['data'])
            profile = build_operator_profile(value, row['phase'], row['level'], row['potential'], row['trust'],
                                             skill_level, masteries, row['id'])
            deployables = deployables_of(value, fetch_deployable)
            summons = build_summon_profiles(value, deployables, row['phase'], row['level'], row['potential'],
                                            row['trust'], skill_level, masteries, row['id'])
        except (HellaAPIError, ValueError, KeyError) as exc:
            logger.exception(error_message, row['id'], exc)
            continue
        yield RosterOperator(row, value, operator, profile, summons, deployables, skill_level, masteries)
