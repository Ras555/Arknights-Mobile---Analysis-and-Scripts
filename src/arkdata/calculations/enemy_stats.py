#File Documentation
'''
File: enemy_stats.py
Description: Resolves an enemy's stats at a given level, using game data
    pulled from the HellaAPI, and calculates its damage per second (DPS).
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import re

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
# motion value for aerial (flying) enemies.
MOTION_FLY = "FLY"
# Enemy tiers (excel.enemyLevel / levelType) that count as elite or leader for talents.
ELITE_LEVEL_TYPES = ("ELITE", "BOSS")

# Whether an enemy can be blocked is only in its ability text (blockCnt is 0 for every enemy but one, checked
# 2026-11-09): "Unblockable" / "Cannot be blocked" means never, "Can only be blocked by Operators with 3 or more
# Block" (or "by units with at least 3 Block") needs that much block. Unblockable only for a while ("cannot be blocked
# for a period of time" when fleeing, "cannot be blocked when Invisible") still counts as blockable.
NEVER_BLOCKED = re.compile(r"\bunblockable\b|\bcannot be blocked\b", re.IGNORECASE)
SOMETIMES_UNBLOCKABLE = re.compile(r"cannot be blocked (?:for|when|while)|period of time", re.IGNORECASE)
MINIMUM_BLOCK = re.compile(r"only be blocked by [^.;]*?(\d+) or more block|only be blocked by [^.;]*?at least (\d+) block",
                           re.IGNORECASE)


def _fix_mojibake(text: str) -> str:
    '''
    Repairs a name that was UTF-8 decoded as Latin-1 (e.g. "Î±" back to "α").

    Args:
        text: The name as the HellaAPI returned it.

    Returns:
        The repaired name, or the original text if it wasn't mangled.
    '''
    # Re-encoding as Latin-1 recovers the original UTF-8 bytes. Names that
    # were never mangled fail one of the two steps and are returned unchanged.
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _defined_values(fields: dict | None) -> dict:
    '''
    Returns {key: m_value} for every {m_defined, m_value} field that is defined.

    The HellaAPI wraps each enemy field as {"m_defined": bool, "m_value": ...}.
    m_defined = false means "this level doesn't change the field", so those
    fields are left out rather than treated as real values.

    Args:
        fields: A dictionary of wrapped fields. None or empty gives {}.

    Returns:
        The unwrapped values of only the defined fields.
    '''
    if not fields:
        return {}
    return {
        key: field["m_value"]
        for key, field in fields.items()
        # Skips anything that isn't a wrapped field, as well as undefined ones.
        if isinstance(field, dict) and field.get("m_defined")
    }


def _min_block(abilities: list[str]) -> float | None:
    '''
    Returns the block an operator needs to block the enemy, from its
    ability texts: None when it can never be blocked, the number asked for
    ("Can only be blocked by Operators with 3 or more Block"), or 1.

    Args:
        abilities: The texts of the enemy's abilityList.
    '''
    for text in abilities:
        minimum = MINIMUM_BLOCK.search(text)
        if minimum:
            return float(minimum.group(1) or minimum.group(2))
        if NEVER_BLOCKED.search(text) and not SOMETIMES_UNBLOCKABLE.search(text):
            return None
    return 1.0


class EnemyStats:
    '''
    Resolves and stores an enemy's stats and DPS at a given level.

    In the HellaAPI, level 0 holds an enemy's full data and higher levels
    only define the fields that change, so undefined fields fall back to
    level 0. Stage-specific overwrittenData (from enemyDbRefs) is applied
    last, in the same {m_defined, m_value} format.

    For matchups it also keeps tags (enemyTags, e.g. "sarkaz"), level_type
    (NORMAL, ELITE or BOSS) and min_block, the block an operator needs to
    block it, from its ability text (None when it can't be blocked).
    '''

    def __init__(self, enemy_id: str, level: int = 0, overwritten_data: dict | None = None, enemy_data: dict | None = None):
        '''
        Args:
            enemy_id: The enemy's key, e.g. "enemy_1007_slime".
            level: The enemy level used by the stage (0 is the base level).
            overwritten_data: The stage's changes to this enemy, if any.
            enemy_data: The enemy's HellaAPI document. When None, it's
                fetched with fetch_enemy_data (tests pass it in directly).

        Raises:
            ValueError: If the enemy has no data for the requested level.
        '''
        self.enemy_id = enemy_id
        self.level = level

        self._data = enemy_data if enemy_data is not None else fetch_enemy_data(enemy_id)
        # "excel" holds the enemy's handbook info, which doesn't change by level.
        excel = self._data["excel"]

        self.name = _fix_mojibake(excel["name"])
        self.enemy_tier = excel.get("enemyLevel")
        self.min_block = _min_block([ability.get("text") or "" for ability in excel.get("abilityList") or []])
        # Unrecognised damage types are kept as-is rather than dropped.
        self.damage_types = [ENEMY_DAMAGE_TYPES.get(t, t) for t in (excel.get("damageType") or [])]

        levels = {entry["level"]: entry["enemyData"] for entry in self._data["levels"]["Value"]}
        if level not in levels:
            raise ValueError(f"{self.name} has levels {sorted(levels)}, got level {level}")

        # Stats are built up in layers, each one overriding the last:
        # level 0 (the full data), then the requested level's changes, then
        # the stage's changes. Level 0 is only added once when level is 0.
        layers = [levels[0]] if level != 0 else []
        layers.append(levels[level])
        if overwritten_data:
            layers.append(overwritten_data)

        self.stats = {}
        self.apply_way = None
        self.motion = None
        self.level_type = self.enemy_tier
        self.tags: list[str] = []
        for layer in layers:
            self.stats.update(_defined_values(layer.get("attributes")))
            # applyWay, motion, levelType and enemyTags are single wrapped fields, not dictionaries
            # of them, so each is wrapped in a one-key dictionary to reuse
            # _defined_values. An undefined field keeps the previous layer's value.
            self.apply_way = _defined_values({"v": layer.get("applyWay")}).get("v", self.apply_way)
            self.motion = _defined_values({"v": layer.get("motion")}).get("v", self.motion)
            self.level_type = _defined_values({"v": layer.get("levelType")}).get("v", self.level_type)
            self.tags = list(_defined_values({"v": layer.get("enemyTags")}).get("v", self.tags) or [])

        self.attack_interval = self._calculate_attack_interval()
        self.dps = self._calculate_dps()

    @property
    def weight(self) -> float:
        '''
        The enemy's weight (massLevel; 0 when not given).
        '''
        return self.stats.get("massLevel", 0)

    @property
    def flying(self) -> bool:
        '''
        Whether the enemy is aerial (motion FLY).
        '''
        return self.motion == MOTION_FLY

    @property
    def elite(self) -> bool:
        '''
        Whether the enemy is an elite or leader (ELITE or BOSS tier).
        '''
        return self.level_type in ELITE_LEVEL_TYPES

    def blockable_by(self, block: float) -> bool:
        '''
        Whether an operator with this much block can block the enemy: it
        walks, can be blocked at all, and the operator has the block its
        ability text asks for (min_block).

        Args:
            block: The operator's block count.
        '''
        return not self.flying and self.min_block is not None and block >= self.min_block

    @property
    def damage_type(self) -> str:
        '''
        The enemy's primary damage type: PHYSICAL, ARTS, HEAL, or NONE.

        Only the first listed damage type is used; enemies with none listed
        are treated as not attacking.
        '''
        return self.damage_types[0] if self.damage_types else DamageType.NONE

    def _calculate_attack_interval(self) -> float:
        '''
        Returns the seconds between the enemy's attacks.

        Returns:
            baseAttackTime adjusted by attackSpeed.
        '''
        # attackSpeed is a percentage of the base attack rate (100 = normal speed).
        return self.stats["baseAttackTime"] / (self.stats["attackSpeed"] / 100)

    def _calculate_dps(self) -> float:
        '''
        Returns the enemy's raw DPS, before any operator's DEF or RES.

        Returns:
            ATK / attack interval, or 0 for enemies that don't deal damage.
        '''
        # Enemies that never attack, or whose attacks heal, deal no damage.
        if self.apply_way == APPLY_WAY_NONE or self.damage_type in (DamageType.NONE, DamageType.HEAL):
            return 0.0
        return self.stats["atk"] / self.attack_interval

    def to_dict(self) -> dict:
        '''
        Returns the enemy's identity, stats, and DPS as a dictionary.

        Returns:
            One flat dictionary (one table row), with every resolved stat
            included as its own key.
        '''
        return {
            "enemy_id": self.enemy_id,
            "name": self.name,
            "enemy_tier": self.enemy_tier,
            "level": self.level,
            "damage_type": self.damage_type,
            "apply_way": self.apply_way,
            "motion": self.motion,
            "level_type": self.level_type,
            "tags": ", ".join(self.tags),
            "weight": self.weight,
            "min_block": self.min_block,
            **self.stats,
            "attack_interval": self.attack_interval,
            "dps": self.dps,
        }
