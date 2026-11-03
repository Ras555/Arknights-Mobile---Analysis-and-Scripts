#File Documentation
'''
File: matchups.py
Description: Implements the matchups step: retrieve the stats of each
    enemy in a stage from the HellaAPI and compare them against the saved
    stats of the Elite 2 operators to calculate the damage per second each operator deals
    to each enemy, and vice versa.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.calculations.enemy_stats import EnemyStats
from arkdata.calculations.matchups import REQUIRED_OPERATOR_COLUMNS, calculate_matchups
from arkdata.calculations.stage import load_stage_enemies
from arkdata.sources.account import filter_operators
from arkdata.sources.hella_api import fetch_enemy_data, fetch_stage_data
from arknights_calc.storage import MissingColumnsError

logger = logging.getLogger(__name__)

# Constants

# Only Elite 2 operators are compared, so the matchup tables stay a readable size.
ELITE_2_PHASE = 2
# The operator stats columns this step needs: the ones calculate_matchups
# reads, plus the phase used to keep only Elite 2 operators.
MATCHUP_OPERATOR_COLUMNS = [*REQUIRED_OPERATOR_COLUMNS, 'phase']


def choose_stage_variant(stage_id: str, variants: list, input_func=input) -> dict:
    '''
    Returns the stage variant to use, asking the user when there is more than one.

    Args:
        stage_id: The stage id, for the prompt.
        variants: The stage's variants, from fetch_stage_data.
        input_func: The function used to ask the user (tests pass a fake).

    Returns:
        The chosen variant.
    '''
    # Nothing to choose between, so don't ask.
    if len(variants) == 1:
        return variants[0]

    print(f"Stage '{stage_id}' has {len(variants)} variants:")
    for i, variant in enumerate(variants):
        excel = variant["excel"]
        print(f"  [{i}] {excel.get('code')} {excel.get('name')} ({excel.get('difficulty')}, {excel.get('stageId')})")
    # Keep asking until the answer is one of the listed numbers.
    while True:
        choice = input_func(f"Choose a variant [0-{len(variants) - 1}]: ").strip()
        if choice.isdigit() and int(choice) < len(variants):
            return variants[int(choice)]
        print("Invalid choice.")


def print_matchup_tables(enemy_df: pd.DataFrame, matchups: pd.DataFrame):
    '''
    Prints the stage's enemy stats and the operator/enemy DPS pivot tables.

    Args:
        enemy_df: The stage's enemy stats, with spawn counts.
        matchups: The matchups, from calculate_matchups.
    '''
    # Show every row and column (pandas normally cuts large tables short),
    # only for these prints; the global display settings aren't changed.
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 250):
        print("\nEnemy stats:")
        print(enemy_df[["name", "level", "spawn_count", "maxHp", "atk", "def", "magicResistance",
                        "damage_type", "apply_way", "attack_interval", "dps"]].round(2).to_string(index=False))
        # One row per operator and one column per enemy. Columns include the
        # level, since a stage can use the same enemy at different levels.
        # sort=False keeps the stage's and roster's order instead of sorting by name.
        print("\nOperator DPS to each enemy:")
        print(matchups.pivot_table(index="operator_name", columns=["enemy_name", "enemy_level"],
                                   values="operator_dps_to_enemy", sort=False).round(1))
        print("\nEnemy DPS to each operator:")
        print(matchups.pivot_table(index="operator_name", columns=["enemy_name", "enemy_level"],
                                   values="enemy_dps_to_operator", sort=False).round(1))


class StageMatchupProcessor:
    '''
    Calculates operator vs. enemy matchups for one stage (CLAUDE.md Program
    Flow, matchups step) and saves the enemy stats and matchups through
    storage. Only Elite 2 operators are compared.

    fetch_stage and fetch_enemy take a stage or enemy id and return its
    HellaAPI data. make_enemy takes (enemy_id, level, overwritten_data) and
    returns an EnemyStats; by default it builds one from fetch_enemy's data.
    All of them, and input_func, are injectable so tests can avoid network
    calls and user input.
    '''

    def __init__(self, storage, fetch_stage=fetch_stage_data, fetch_enemy=fetch_enemy_data, make_enemy=None, input_func=input):
        '''
        Args:
            storage: The Storage the operator stats are read from and the
                results saved through.
            fetch_stage: The function used to get a stage's data.
            fetch_enemy: The function used to get an enemy's data.
            make_enemy: The function used to build each EnemyStats; None
                uses _make_enemy.
            input_func: The function used to ask the user for input.
        '''
        self.storage = storage
        self.fetch_stage = fetch_stage
        self.fetch_enemy = fetch_enemy
        # The default needs self.fetch_enemy, so it can't be set in the signature.
        self.make_enemy = make_enemy or self._make_enemy
        self.input_func = input_func

    def _make_enemy(self, enemy_id, level, overwritten_data):
        '''
        Returns an EnemyStats built from fetch_enemy's data, so enemies are
        fetched the same way as the stage (e.g. through the cache).
        '''
        return EnemyStats(enemy_id, level, overwritten_data, enemy_data=self.fetch_enemy(enemy_id))

    def run(self, stage_id=None):
        '''
        Runs the matchups step for stage_id (asking for one if not given).
        Returns (enemy_df, matchups), or None if the step couldn't run.

        Args:
            stage_id: The stage to compare against, e.g. "main_06-09".

        Returns:
            The enemy stats and matchups DataFrames, or None.
        '''
        # The stats step's output is needed first. Stop with a hint if it's
        # missing or was saved by an older version without every column.
        try:
            operators = self.storage.load_operator_stats(MATCHUP_OPERATOR_COLUMNS)
        except FileNotFoundError:
            logger.error('No operator stats found at %s. Run `arknights-calc stats` first.', self.storage.operator_stats_file)
            return None
        except MissingColumnsError as exc:
            logger.error('%s is missing columns %s. Rerun the stats step first.', exc.file_path, exc.missing_columns)
            return None

        # Checked before asking for a stage, so there's nothing to answer if
        # there are no operators to compare.
        operators = filter_operators(operators, min_phase=ELITE_2_PHASE, max_phase=ELITE_2_PHASE)
        if operators.empty:
            logger.warning('No Elite 2 operators in %s. Matchups only compare Elite 2 operators.',
                           self.storage.operator_stats_file)
            return None
        logger.info('Comparing %d Elite 2 operators', len(operators))

        if not stage_id:
            stage_id = self.input_func("Enter a stage id (e.g. main_01-07): ").strip()
        stage = choose_stage_variant(stage_id, self.fetch_stage(stage_id), self.input_func)
        excel = stage["excel"]
        logger.info('Calculating matchups for stage %s - %s (%s)', excel.get('code'), excel.get('name'), excel.get('difficulty'))

        enemies = load_stage_enemies(stage, self.make_enemy)
        if not enemies:
            logger.warning('No enemies found for stage %s', stage_id)
            return None

        # Add each enemy's spawn count to its stats, for the enemy table.
        enemy_df = pd.DataFrame([{**enemy.to_dict(), "spawn_count": count} for enemy, count in enemies])
        matchups = calculate_matchups(operators, enemies)

        # Name the files by the chosen variant's stageId (e.g. a challenge mode
        # has its own id), falling back to the id that was asked for.
        enemy_file, matchup_file = self.storage.save_stage_results(excel.get("stageId", stage_id), enemy_df, matchups)

        print_matchup_tables(enemy_df, matchups)
        logger.info('Saved enemy stats to %s', enemy_file)
        logger.info('Saved matchups to %s', matchup_file)
        return enemy_df, matchups
