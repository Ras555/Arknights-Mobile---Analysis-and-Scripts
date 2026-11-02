#File Documentation
'''
File: damage.py
Description: Damage types and damage calculations between an attacker
    and a defender, accounting for physical, arts, and true damage.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-02
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
    HEAL = "HEAL"   # Healers' attacks restore allies' HP instead of damaging enemies.
    NONE = "NONE"   # Units that don't attack at all.


def damage_per_hit(atk: float, damage_type: str, defense: float, magic_resistance: float) -> float:
    '''
    Returns the damage one hit deals after the defender's DEF or RES.

    Physical damage is reduced by DEF as a flat amount, arts damage by RES
    as a percentage, and true damage is not reduced. Any other damage type
    (e.g. HEAL or NONE) deals no damage.

    Args:
        atk: The attacker's ATK.
        damage_type: A DamageType, or its plain string value (e.g. "ARTS").
        defense: The defender's DEF.
        magic_resistance: The defender's RES, as a percentage (0-100).

    Returns:
        The damage dealt by one hit.
    '''
    if damage_type == DamageType.PHYSICAL:
        # DEF is subtracted from ATK, but never below the 5% minimum.
        return max(atk - defense, atk * MINIMUM_DAMAGE_RATIO)
    if damage_type == DamageType.ARTS:
        # RES is a percentage reduction. It's limited to 0-100 so unusual
        # values (e.g. negative RES from debuffs) can't raise or invert damage.
        resistance = min(max(magic_resistance, 0), 100)
        return max(atk * (1 - resistance / 100), atk * MINIMUM_DAMAGE_RATIO)
    if damage_type == DamageType.TRUE:
        # True damage ignores both DEF and RES.
        return atk
    # HEAL, NONE and anything unrecognised deal no damage.
    return 0.0


def damage_per_second(atk: float, attack_interval: float, damage_type: str, defense: float, magic_resistance: float) -> float:
    '''
    Returns DPS = (damage per hit after DEF/RES) / attack interval.

    Args:
        atk: The attacker's ATK.
        attack_interval: Seconds between the attacker's hits (see
            CLAUDE.md, Damage Per Second).
        damage_type: A DamageType, or its plain string value.
        defense: The defender's DEF.
        magic_resistance: The defender's RES, as a percentage (0-100).

    Returns:
        The damage dealt per second against this defender.
    '''
    return damage_per_hit(atk, damage_type, defense, magic_resistance) / attack_interval
