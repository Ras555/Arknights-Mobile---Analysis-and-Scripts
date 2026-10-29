#File Documentation
'''
File: matchups.py
Description: Implements the matchups step: retrieve the stats of each
    enemy in a stage from the HellaAPI and compare them against the saved
    operator stats to calculate the damage per second each operator deals
    to each enemy, and vice versa.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-29
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.calculations.enemy_stats import EnemyStats
from arkdata.calculations.matchups import REQUIRED_OPERATOR_COLUMNS, calculate_matchups
from arkdata.calculations.stage import load_stage_enemies
from arkdata.sources.hella_api import fetch_stage_data
from arknights_calc.storage import MissingColumnsError

logger = logging.getLogger(__name__)


def choose_stage_variant(stage_id: str, variants: list, input_func=input) -> dict:
    '''
    Returns the stage variant to use, asking the user when there is more than one.
    '''
    if len(variants) == 1:
        return variants[0]

    print(f"Stage '{stage_id}' has {len(variants)} variants:")
    for i, variant in enumerate(variants):
        excel = variant["excel"]
        print(f"  [{i}] {excel.get('code')} {excel.get('name')} ({excel.get('difficulty')}, {excel.get('stageId')})")
    while True:
        choice = input_func(f"Choose a variant [0-{len(variants) - 1}]: ").strip()
        if choice.isdigit() and int(choice) < len(variants):
            return variants[int(choice)]
        print("Invalid choice.")


def print_matchup_tables(enemy_df: pd.DataFrame, matchups: pd.DataFrame):
    '''
    Prints the stage's enemy stats and the operator/enemy DPS pivot tables.
    '''
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 250):
        print("\nEnemy stats:")
        print(enemy_df[["name", "level", "spawn_count", "maxHp", "atk", "def", "magicResistance",
                        "damage_type", "apply_way", "attack_interval", "dps"]].round(2).to_string(index=False))
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
    storage.

    fetch_stage, make_enemy, and input_func are injectable so tests can
    avoid network calls and user input.
    '''

    def __init__(self, storage, fetch_stage=fetch_stage_data, make_enemy=EnemyStats, input_func=input):
        self.storage = storage
        self.fetch_stage = fetch_stage
        self.make_enemy = make_enemy
        self.input_func = input_func

    def run(self, stage_id=None):
        '''
        Runs the matchups step for stage_id (asking for one if not given).
        Returns (enemy_df, matchups), or None if the step couldn't run.
        '''
        try:
            operators = self.storage.load_operator_stats(REQUIRED_OPERATOR_COLUMNS)
        except MissingColumnsError as exc:
            logger.error('%s is missing columns %s. Rerun the stats step first.', exc.file_path, exc.missing_columns)
            return None

        if not stage_id:
            stage_id = self.input_func("Enter a stage id (e.g. main_01-07): ").strip()
        stage = choose_stage_variant(stage_id, self.fetch_stage(stage_id), self.input_func)
        excel = stage["excel"]
        logger.info('Calculating matchups for stage %s - %s (%s)', excel.get('code'), excel.get('name'), excel.get('difficulty'))

        enemies = load_stage_enemies(stage, self.make_enemy)
        if not enemies:
            logger.warning('No enemies found for stage %s', stage_id)
            return None

        enemy_df = pd.DataFrame([{**enemy.to_dict(), "spawn_count": count} for enemy, count in enemies])
        matchups = calculate_matchups(operators, enemies)

        enemy_file, matchup_file = self.storage.save_stage_results(excel.get("stageId", stage_id), enemy_df, matchups)

        print_matchup_tables(enemy_df, matchups)
        logger.info('Saved enemy stats to %s', enemy_file)
        logger.info('Saved matchups to %s', matchup_file)
        return enemy_df, matchups
