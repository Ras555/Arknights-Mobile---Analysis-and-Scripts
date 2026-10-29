#File Documentation
'''
File: enemy_stats.py
Description: Resolves an enemy's stats at a given level, using game data
    pulled from the HellaAPI, and calculates its damage per second (DPS).
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

from arkdata.calculations.damage import DamageType
from arkdata.sources.hella_api import fetch_enemy_data

# Constants

# The HellaAPI's enemy damageType values mapped to the damage types used here.
ENEMY_DAMAGE_TYPES = {
    "PHYSIC": DamageType.PHYSICAL,
    "MAGIC": DamageType.ARTS,
    "HEAL": DamageType.HEAL,
}

# applyWay value for enemies that don't attack.
APPLY_WAY_NONE = "NONE"


def _fix_mojibake(text: str) -> str:
    # Some HellaAPI names are UTF-8 decoded as Latin-1 (e.g. "Î±" instead of "α").
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _defined_values(fields: dict | None) -> dict:
    '''
    Returns {key: m_value} for every {m_defined, m_value} field that is defined.
    '''
    if not fields:
        return {}
    return {
        key: field["m_value"]
        for key, field in fields.items()
        if isinstance(field, dict) and field.get("m_defined")
    }


class EnemyStats:
    '''
    Resolves and stores an enemy's stats and DPS at a given level.

    In the HellaAPI, level 0 holds an enemy's full data and higher levels
    only define the fields that change, so undefined fields fall back to
    level 0. Stage-specific overwrittenData (from enemyDbRefs) is applied
    last, in the same {m_defined, m_value} format.
    '''

    def __init__(self, enemy_id: str, level: int = 0, overwritten_data: dict | None = None, enemy_data: dict | None = None):
        self.enemy_id = enemy_id
        self.level = level

        self._data = enemy_data if enemy_data is not None else fetch_enemy_data(enemy_id)
        excel = self._data["excel"]

        self.name = _fix_mojibake(excel["name"])
        self.enemy_tier = excel.get("enemyLevel")
        self.damage_types = [ENEMY_DAMAGE_TYPES.get(t, t) for t in (excel.get("damageType") or [])]

        levels = {entry["level"]: entry["enemyData"] for entry in self._data["levels"]["Value"]}
        if level not in levels:
            raise ValueError(f"{self.name} has levels {sorted(levels)}, got level {level}")

        layers = [levels[0]] if level != 0 else []
        layers.append(levels[level])
        if overwritten_data:
            layers.append(overwritten_data)

        self.stats = {}
        self.apply_way = None
        self.motion = None
        for layer in layers:
            self.stats.update(_defined_values(layer.get("attributes")))
            self.apply_way = _defined_values({"v": layer.get("applyWay")}).get("v", self.apply_way)
            self.motion = _defined_values({"v": layer.get("motion")}).get("v", self.motion)

        self.attack_interval = self._calculate_attack_interval()
        self.dps = self._calculate_dps()

    @property
    def damage_type(self) -> str:
        '''
        The enemy's primary damage type: PHYSICAL, ARTS, HEAL, or NONE.
        '''
        return self.damage_types[0] if self.damage_types else DamageType.NONE

    def _calculate_attack_interval(self) -> float:
        # attackSpeed is a percentage of the base attack rate (100 = normal speed).
        return self.stats["baseAttackTime"] / (self.stats["attackSpeed"] / 100)

    def _calculate_dps(self) -> float:
        if self.apply_way == APPLY_WAY_NONE or self.damage_type in (DamageType.NONE, DamageType.HEAL):
            return 0.0
        return self.stats["atk"] / self.attack_interval

    def to_dict(self) -> dict:
        '''
        Returns the enemy's identity, stats, and DPS as a dictionary.
        '''
        return {
            "enemy_id": self.enemy_id,
            "name": self.name,
            "enemy_tier": self.enemy_tier,
            "level": self.level,
            "damage_type": self.damage_type,
            "apply_way": self.apply_way,
            "motion": self.motion,
            **self.stats,
            "attack_interval": self.attack_interval,
            "dps": self.dps,
        }
