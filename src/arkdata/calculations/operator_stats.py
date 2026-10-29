#File Documentation
'''
File: operator_stats.py
Description: Calculates an operator's current stats, basic attack damage
    type, and damage per second (DPS) at a given level, phase (elite level),
    potential, and trust, using game data pulled from the HellaAPI.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-29
Last Modified by: Claude
'''

# Imports

import logging

from arkdata.calculations.damage import DamageType
from arkdata.sources.hella_api import fetch_operator_data

logger = logging.getLogger(__name__)

# Constants

# Maps the HellaAPI's potentialRanks[*].buff.attributes.attributeModifiers[*]
# "attributeType" values to the matching key in a phase's attribute data.
ATTRIBUTE_TYPE_TO_STAT_KEY = {
    "MAX_HP": "maxHp",
    "ATK": "atk",
    "DEF": "def",
    "MAGIC_RESISTANCE": "magicResistance",
    "COST": "cost",
    "RESPAWN_TIME": "respawnTime",
    "BLOCK_CNT": "blockCnt",
    "MOVE_SPEED": "moveSpeed",
    "ATTACK_SPEED": "attackSpeed",
    "BASE_ATTACK_TIME": "baseAttackTime",
}


# The HellaAPI's profession value for Medics, whose basic attacks heal.
PROFESSION_MEDIC = "MEDIC"

# Basic attack damage type for each sub profession (archetype). Only
# archetypes whose damage type is certain are listed; any other archetype
# falls back to reading the operator's trait description, with a warning so
# it can be added here.
SUB_PROFESSION_DAMAGE_TYPES = {
    # Vanguards
    "pioneer": DamageType.PHYSICAL,
    "charger": DamageType.PHYSICAL,
    "tactician": DamageType.PHYSICAL,
    "bearer": DamageType.PHYSICAL,
    "agent": DamageType.PHYSICAL,
    # Guards
    "fighter": DamageType.PHYSICAL,
    "lord": DamageType.PHYSICAL,
    "sword": DamageType.PHYSICAL,
    "musha": DamageType.PHYSICAL,
    "fearless": DamageType.PHYSICAL,
    "reaper": DamageType.PHYSICAL,
    "centurion": DamageType.PHYSICAL,
    "instructor": DamageType.PHYSICAL,
    "librator": DamageType.PHYSICAL,
    "artsfghter": DamageType.ARTS,  # "artsfghter" is spelled this way in the game data
    # Defenders
    "protector": DamageType.PHYSICAL,
    "guardian": DamageType.PHYSICAL,
    "unyield": DamageType.PHYSICAL,
    "duelist": DamageType.PHYSICAL,
    "fortress": DamageType.PHYSICAL,
    "artsprotector": DamageType.ARTS,
    # Snipers
    "fastshot": DamageType.PHYSICAL,
    "closerange": DamageType.PHYSICAL,
    "aoesniper": DamageType.PHYSICAL,
    "longrange": DamageType.PHYSICAL,
    "reaperrange": DamageType.PHYSICAL,
    "siegesniper": DamageType.PHYSICAL,
    "bombarder": DamageType.PHYSICAL,
    "hunter": DamageType.PHYSICAL,
    "loopshooter": DamageType.PHYSICAL,
    # Casters
    "corecaster": DamageType.ARTS,
    "splashcaster": DamageType.ARTS,
    "blastcaster": DamageType.ARTS,
    "funnel": DamageType.ARTS,
    "phalanx": DamageType.ARTS,
    "chain": DamageType.ARTS,
    "mystic": DamageType.ARTS,
    # Medics
    "physician": DamageType.HEAL,
    "ringhealer": DamageType.HEAL,
    "healer": DamageType.HEAL,
    "wandermedic": DamageType.HEAL,
    "chainhealer": DamageType.HEAL,
    # Supporters
    "slower": DamageType.ARTS,
    "underminer": DamageType.ARTS,
    "summoner": DamageType.ARTS,
    "ritualist": DamageType.ARTS,
    "bard": DamageType.HEAL,  # Bards don't attack; they heal allies in range.
    # Specialists
    "executor": DamageType.PHYSICAL,
    "pusher": DamageType.PHYSICAL,
    "stalker": DamageType.PHYSICAL,
    "hookmaster": DamageType.PHYSICAL,
}

# Per-operator exceptions to SUB_PROFESSION_DAMAGE_TYPES, keyed by the
# operator's coded id (e.g. "char_002_amiya"). Checked first.
OPERATOR_DAMAGE_TYPE_OVERRIDES: dict[str, DamageType] = {}


def parse_phase(phase) -> int:
    '''
    Converts a HellaAPI phase value ("PHASE_2" or 2) into an int (2).
    '''
    return int(str(phase).replace("PHASE_", ""))


def _damage_type_from_description(operator_data: dict, phase: int) -> DamageType:
    '''
    Guesses the damage type from the operator's trait description. Trait
    candidates may override the base description once a later phase is
    reached. ("overrideDescripton" is misspelled in the game data itself.)
    '''
    description = operator_data.get("description") or ""

    for candidate in (operator_data.get("trait") or {}).get("candidates") or []:
        required_phase = parse_phase(candidate["unlockCondition"]["phase"])
        if candidate.get("overrideDescripton") and required_phase <= phase:
            description = candidate["overrideDescripton"]

    if "Arts damage" in description:
        return DamageType.ARTS
    if operator_data.get("profession") == PROFESSION_MEDIC or "Restores the HP" in description:
        return DamageType.HEAL
    return DamageType.PHYSICAL


def determine_damage_type(operator_data: dict, phase: int, operator_id: str | None = None) -> DamageType:
    '''
    Returns PHYSICAL, ARTS, or HEAL for an operator's basic attack at the
    given phase.

    Checks OPERATOR_DAMAGE_TYPE_OVERRIDES, then SUB_PROFESSION_DAMAGE_TYPES.
    If the operator's archetype isn't listed, logs a warning and falls back
    to guessing from the trait description.
    '''
    if operator_id in OPERATOR_DAMAGE_TYPE_OVERRIDES:
        return OPERATOR_DAMAGE_TYPE_OVERRIDES[operator_id]

    sub_profession = operator_data.get("subProfessionId")
    if sub_profession in SUB_PROFESSION_DAMAGE_TYPES:
        return SUB_PROFESSION_DAMAGE_TYPES[sub_profession]

    damage_type = _damage_type_from_description(operator_data, phase)
    logger.warning(
        'Sub profession %s of %s is not in SUB_PROFESSION_DAMAGE_TYPES; guessed %s from its trait description',
        sub_profession, operator_data.get("name", operator_id), damage_type,
    )
    return damage_type


class OperatorStats:
    '''
    Calculates and stores an operator's stats and DPS for a given
    level, phase, potential, and trust.

    operator_id may be the operator's coded id (e.g. "char_002_amiya") or
    a version of the operator's printed name (e.g. "Amiya"). If
    operator_data isn't given, it is fetched from the HellaAPI.
    '''

    def __init__(
        self, operator_id: str, level: int, phase: int, potential: int, trust: int = 0, operator_data: dict | None = None,
    ):
        self.operator_id = operator_id
        self.level = level
        self.phase = phase
        self.potential = potential
        self.trust = trust

        self._data = operator_data if operator_data is not None else fetch_operator_data(operator_id)

        self.name = self._data["name"]
        self.profession = self._data["profession"]
        self.sub_profession_id = self._data["subProfessionId"]
        self.rarity = self._data["rarity"]

        self._validate_phase()
        self._validate_level()
        self._validate_potential()
        self._validate_trust()

        self.damage_type = determine_damage_type(self._data, self.phase, self.operator_id)

        self.stats = self._calculate_base_stats()
        self._apply_potential_bonuses()
        self._apply_trust_bonus()

        self.attack_interval = self._calculate_attack_interval()
        self.dps = self._calculate_dps()

    def _phase_data(self, phase: int) -> dict:
        return self._data["phases"][phase]

    @staticmethod
    def _keyframes(phase_data: dict) -> list:
        # HellaAPI returns "attributesKeyFrames"; "attributeKeyFrames" is kept as a fallback.
        return phase_data.get("attributesKeyFrames") or phase_data["attributeKeyFrames"]

    def _validate_phase(self):
        num_phases = len(self._data["phases"])
        if not (0 <= self.phase < num_phases):
            raise ValueError(f"{self.name} only has phases 0-{num_phases - 1}, got phase {self.phase}")

    def _validate_level(self):
        max_level = self._phase_data(self.phase)["maxLevel"]
        if not (1 <= self.level <= max_level):
            raise ValueError(f"{self.name} phase {self.phase} allows levels 1-{max_level}, got level {self.level}")

    def _validate_potential(self):
        # Potential is 0-indexed to match arkprts' potential_rank (0 = base, no upgrades applied).
        max_potential = len(self._data["potentialRanks"])
        if not (0 <= self.potential <= max_potential):
            raise ValueError(f"{self.name} allows potential 0-{max_potential}, got potential {self.potential}")

    def _validate_trust(self):
        if self.trust < 0:
            raise ValueError(f"Trust cannot be negative, got trust {self.trust}")

    def _calculate_base_stats(self) -> dict:
        keyframes = self._keyframes(self._phase_data(self.phase))
        start_frame, end_frame = keyframes[0], keyframes[-1]
        start_level, end_level = start_frame["level"], end_frame["level"]
        start_data, end_data = start_frame["data"], end_frame["data"]

        stats = {}
        for key, start_value in start_data.items():
            end_value = end_data[key]
            if isinstance(start_value, bool) or start_value == end_value or end_level == start_level:
                stats[key] = start_value
            else:
                rate_per_level = (end_value - start_value) / (end_level - start_level)
                stats[key] = start_value + rate_per_level * (self.level - start_level)
        return stats

    def _apply_potential_bonuses(self):
        ranks_to_apply = self._data["potentialRanks"][: self.potential]
        for rank in ranks_to_apply:
            modifiers = ((rank.get("buff") or {}).get("attributes") or {}).get("attributeModifiers") or []
            for modifier in modifiers:
                if modifier.get("formulaItem") != "ADDITION":
                    continue
                stat_key = ATTRIBUTE_TYPE_TO_STAT_KEY.get(modifier["attributeType"])
                if stat_key is None:
                    continue
                self.stats[stat_key] = self.stats.get(stat_key, 0) + modifier["value"]

    def _apply_trust_bonus(self):
        # favorKeyFrames gives the stat bonus at 0 trust (level 0, always zero) and
        # 100 trust (level 50); trust scales linearly between them and caps at 100.
        start_frame, end_frame = self._data["favorKeyFrames"][0], self._data["favorKeyFrames"][-1]
        start_data, end_data = start_frame["data"], end_frame["data"]
        trust_ratio = min(self.trust, 100) / 100

        for key, start_value in start_data.items():
            if isinstance(start_value, bool):
                continue
            end_value = end_data[key]
            bonus = start_value + (end_value - start_value) * trust_ratio
            self.stats[key] = self.stats.get(key, 0) + bonus

    def _calculate_attack_interval(self) -> float:
        # attackSpeed is a percentage of the base attack rate (100 = normal speed).
        return self.stats["baseAttackTime"] / (self.stats["attackSpeed"] / 100)

    def _calculate_dps(self) -> float:
        return self.stats["atk"] / self.attack_interval

    def to_dict(self) -> dict:
        '''
        Returns the operator's identity, configuration, stats, damage type,
        and DPS as a dictionary.
        '''
        return {
            "operator_id": self.operator_id,
            "name": self.name,
            "profession": self.profession,
            "sub_profession_id": self.sub_profession_id,
            "rarity": self.rarity,
            "level": self.level,
            "phase": self.phase,
            "potential": self.potential,
            "trust": self.trust,
            **self.stats,
            "damage_type": self.damage_type,
            "attack_interval": self.attack_interval,
            "dps": self.dps,
        }
