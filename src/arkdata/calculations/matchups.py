#File Documentation
'''
File: matchups.py
Description: Calculates how operators and enemies match up: the damage
    per second each deals to the other and how long each takes to defeat
    the other.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import pandas as pd

from arkdata.calculations.damage import damage_per_second

# Constants

# Operator stats columns calculate_matchups reads. OperatorStats.to_dict()
# produces all of them; test_matchups.py checks the two stay in sync.
REQUIRED_OPERATOR_COLUMNS = ['operator_id', 'name', 'damage_type', 'atk', 'attack_interval', 'def', 'magicResistance', 'maxHp']


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
