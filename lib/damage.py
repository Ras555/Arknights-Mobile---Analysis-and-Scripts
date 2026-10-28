#File Documentation
'''
File: damage.py
Description: Damage types and damage calculations between an attacker
    and a defender, accounting for physical, arts, and true damage.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

from enum import StrEnum

# Constants

# Arknights guarantees every physical or arts hit deals at least 5% of the
# attacker's ATK, no matter how high the defender's DEF or RES is.
MINIMUM_DAMAGE_RATIO = 0.05


class DamageType(StrEnum):
    '''
    The type of damage a unit's basic attack deals. HEAL and NONE deal no
    damage. Being a StrEnum, members compare equal to their plain string
    values (e.g. DamageType.ARTS == "ARTS"), so values read back from CSV
    files still match.
    '''
    PHYSICAL = "PHYSICAL"
    ARTS = "ARTS"
    TRUE = "TRUE"
    HEAL = "HEAL"
    NONE = "NONE"


def damage_per_hit(atk: float, damage_type: str, defense: float, magic_resistance: float) -> float:
    '''
    Returns the damage one hit deals after the defender's DEF or RES.

    Physical damage is reduced by DEF as a flat amount, arts damage by RES
    as a percentage, and true damage is not reduced. Any other damage type
    (e.g. HEAL or NONE) deals no damage.
    '''
    if damage_type == DamageType.PHYSICAL:
        return max(atk - defense, atk * MINIMUM_DAMAGE_RATIO)
    if damage_type == DamageType.ARTS:
        resistance = min(max(magic_resistance, 0), 100)
        return max(atk * (1 - resistance / 100), atk * MINIMUM_DAMAGE_RATIO)
    if damage_type == DamageType.TRUE:
        return atk
    return 0.0


def damage_per_second(atk: float, attack_interval: float, damage_type: str, defense: float, magic_resistance: float) -> float:
    '''
    Returns DPS = (damage per hit after DEF/RES) / attack interval.
    '''
    return damage_per_hit(atk, damage_type, defense, magic_resistance) / attack_interval
