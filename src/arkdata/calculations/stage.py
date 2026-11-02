#File Documentation
'''
File: stage.py
Description: Reads the enemies a stage contains from its HellaAPI stage
    data: which enemies, at which levels, and how many of each spawn.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import logging
from collections import Counter

from arkdata.calculations.enemy_stats import EnemyStats

logger = logging.getLogger(__name__)


def count_enemy_spawns(waves: list) -> Counter[str]:
    '''
    Counts how many of each enemy key the stage's waves spawn.

    A stage's waves are split into fragments, and each fragment is a list of
    actions. Only SPAWN actions add enemies; other actions (e.g. story or
    camera events) are ignored.

    Args:
        waves: The stage's levels["waves"] list. None is treated as empty.

    Returns:
        A Counter of {enemy key: number spawned}.
    '''
    counts: Counter[str] = Counter()
    # Missing waves, fragments or actions are all treated as empty lists.
    for wave in waves or []:
        for fragment in wave.get("fragments") or []:
            for action in fragment.get("actions") or []:
                if action.get("actionType") == "SPAWN":
                    # A spawn action without a count spawns one enemy.
                    counts[action["key"]] += action.get("count", 1)
    return counts


def load_stage_enemies(stage: dict, make_enemy=EnemyStats) -> list:
    '''
    Returns (EnemyStats, spawn count) for each enemy in the stage's enemyDbRefs.

    make_enemy takes (enemy_id, level, overwritten_data) and returns an
    EnemyStats. It is injectable so tests can avoid network calls.

    Args:
        stage: One stage variant from the HellaAPI (with "levels" holding
            "enemyDbRefs" and "waves").
        make_enemy: Builds the EnemyStats for one enemy reference.

    Returns:
        A list of (EnemyStats, spawn count) pairs, in enemyDbRefs order.
    '''
    levels = stage["levels"]
    spawn_counts = count_enemy_spawns(levels.get("waves"))

    enemies = []
    for ref in levels.get("enemyDbRefs") or []:
        # useDb = false means the enemy is defined inside the stage itself
        # rather than in the enemy database, so there's no enemy data to fetch.
        if not ref.get("useDb"):
            logger.warning('Skipping %s: stage-defined enemies (useDb = false) are not supported.', ref['id'])
            continue
        # overwrittenData holds this stage's changes to the enemy's normal stats.
        enemy = make_enemy(ref["id"], ref.get("level", 0), ref.get("overwrittenData"))
        enemies.append((enemy, spawn_counts.get(ref["id"], 0)))
    return enemies
