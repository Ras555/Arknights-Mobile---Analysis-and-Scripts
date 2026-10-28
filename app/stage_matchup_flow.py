#File Documentation
'''
File: stage_matchup_flow.py
Description: Implements the matchups step: retrieve the stats of each
    enemy in a stage from the HellaAPI and compare them against the saved
    operator stats to calculate the damage per second each operator deals
    to each enemy, and vice versa.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import logging
import os
from collections import Counter

import pandas as pd

from lib.damage import damage_per_second
from lib.enemy_stats import EnemyStats
from lib.hella_api_client import fetch_stage_data

from app import config

logger = logging.getLogger(__name__)

# Constants

# Operator stats columns calculate_matchups reads. Files saved by the stats
# step (OperatorStats.to_dict) have all of them; test_stage_matchup_flow.py
# checks the two stay in sync.
REQUIRED_OPERATOR_COLUMNS = ['operator_id', 'name', 'damage_type', 'atk', 'attack_interval', 'def', 'magicResistance', 'maxHp']


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


def count_enemy_spawns(waves: list) -> Counter:
    '''
    Counts how many of each enemy key the stage's waves spawn.
    '''
    counts = Counter()
    for wave in waves or []:
        for fragment in wave.get("fragments") or []:
            for action in fragment.get("actions") or []:
                if action.get("actionType") == "SPAWN":
                    counts[action["key"]] += action.get("count", 1)
    return counts


def load_stage_enemies(stage: dict, make_enemy=EnemyStats) -> list:
    '''
    Returns (EnemyStats, spawn count) for each enemy in the stage's enemyDbRefs.

    make_enemy takes (enemy_id, level, overwritten_data) and returns an
    EnemyStats. It is injectable so tests can avoid network calls.
    '''
    levels = stage["levels"]
    spawn_counts = count_enemy_spawns(levels.get("waves"))

    enemies = []
    for ref in levels.get("enemyDbRefs") or []:
        if not ref.get("useDb"):
            logger.warning('Skipping %s: stage-defined enemies (useDb = false) are not supported.', ref['id'])
            continue
        enemy = make_enemy(ref["id"], ref.get("level", 0), ref.get("overwrittenData"))
        enemies.append((enemy, spawn_counts.get(ref["id"], 0)))
    return enemies


def calculate_matchups(operators: pd.DataFrame, enemies: list) -> pd.DataFrame:
    '''
    Returns one row per (operator, enemy) pair with the DPS each deals to the other.
    '''
    rows = []
    for _, op in operators.iterrows():
        for enemy, spawn_count in enemies:
            e = enemy.stats
            op_dps = damage_per_second(op["atk"], op["attack_interval"], op["damage_type"], e["def"], e["magicResistance"])
            enemy_dps = (
                0.0 if enemy.dps == 0
                else damage_per_second(e["atk"], enemy.attack_interval, enemy.damage_type, op["def"], op["magicResistance"])
            )
            rows.append({
                "operator_id": op["operator_id"],
                "operator_name": op["name"],
                "operator_damage_type": op["damage_type"],
                "enemy_id": enemy.enemy_id,
                "enemy_name": enemy.name,
                "enemy_level": enemy.level,
                "enemy_damage_type": enemy.damage_type,
                "enemy_apply_way": enemy.apply_way,
                "enemy_spawn_count": spawn_count,
                "operator_dps_to_enemy": op_dps,
                "enemy_dps_to_operator": enemy_dps,
                "seconds_for_operator_to_kill_enemy": e["maxHp"] / op_dps if op_dps > 0 else float("inf"),
                "seconds_for_enemy_to_kill_operator": op["maxHp"] / enemy_dps if enemy_dps > 0 else float("inf"),
            })
    return pd.DataFrame(rows)


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
    Flow, matchups step) and saves the enemy stats and matchups to output_dir.

    fetch_stage, make_enemy, and input_func are injectable so tests can
    avoid network calls and user input.
    '''

    def __init__(self, operator_stats_file=None, output_dir=None, fetch_stage=fetch_stage_data, make_enemy=EnemyStats, input_func=input):
        self.operator_stats_file = operator_stats_file or config.OPERATOR_STATS_FILE
        self.output_dir = output_dir or config.IO_DIR
        self.fetch_stage = fetch_stage
        self.make_enemy = make_enemy
        self.input_func = input_func

    def run(self, stage_id=None):
        '''
        Runs the matchups step for stage_id (asking for one if not given).
        Returns (enemy_df, matchups), or None if the step couldn't run.
        '''
        operators = pd.read_csv(self.operator_stats_file)
        missing_columns = [column for column in REQUIRED_OPERATOR_COLUMNS if column not in operators.columns]
        if missing_columns:
            logger.error('%s is missing columns %s. Rerun the stats step first.', self.operator_stats_file, missing_columns)
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

        file_stage_id = excel.get("stageId", stage_id)
        enemy_file = os.path.join(self.output_dir, f"stage_{file_stage_id}_enemy_stats.csv")
        matchup_file = os.path.join(self.output_dir, f"stage_{file_stage_id}_matchups.csv")
        os.makedirs(self.output_dir, exist_ok=True)
        enemy_df.to_csv(enemy_file, index=False)
        matchups.to_csv(matchup_file, index=False)

        print_matchup_tables(enemy_df, matchups)
        logger.info('Saved enemy stats to %s', enemy_file)
        logger.info('Saved matchups to %s', matchup_file)
        return enemy_df, matchups
