#File Documentation
'''
File: skill_rotation.py
Description: Models one skill cycle (charging SP, then the skill being
    active) to work out how long a cycle takes, how much of it the skill
    is active for, and the average damage per second over the cycle.
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-04
Last Modified by: Claude
'''

# Imports

from dataclasses import dataclass
from enum import StrEnum

# Constants

# Skills that gain SP over time gain 1 SP per second (before any SP boosts from talents or other operators).
SP_PER_SECOND = 1.0


class SpType(StrEnum):
    '''
    How a skill gains SP. The HellaAPI gives passive skills the number 8
    instead of a name, so PASSIVE is "8".
    '''
    OVER_TIME = "INCREASE_WITH_TIME"
    PER_ATTACK = "INCREASE_WHEN_ATTACK"
    WHEN_HIT = "INCREASE_WHEN_TAKEN_DAMAGE"
    PASSIVE = "8"


@dataclass(frozen=True)
class SkillRotation:
    '''
    One skill cycle: charge SP, then the skill is active, then repeat.
    SP doesn't charge while a skill is active.

    duration is how long the skill is active, in seconds: 0 for an instant
    skill (e.g. "the next attack deals 250%"), None for a skill that stays
    on once activated. For ammo skills, pass ammo * attack interval.
    attack_interval is the seconds between attacks while charging, used for
    skills that gain SP per attack. replaces_attack is True when an instant
    skill turns an attack into the skill (e.g. "the next attack"), which
    then gains no SP, and False when it's an extra strike on activation.
    '''
    sp_type: str
    sp_cost: float
    duration: float | None
    attack_interval: float
    passive: bool = False
    replaces_attack: bool = True

    @property
    def unlimited(self) -> bool:
        '''
        Whether the skill stays active once activated (or is passive).
        '''
        return self.passive or self.duration is None

    @property
    def instant(self) -> bool:
        '''
        Whether the skill acts once and ends (duration 0).
        '''
        return not self.unlimited and self.duration == 0

    @property
    def charge_time(self) -> float | None:
        '''
        Seconds to charge the skill's SP, or None when it can't be known
        (SP gained when hit depends on the enemies). 0 for passive skills.
        '''
        if self.passive:
            return 0.0
        if str(self.sp_type) == SpType.OVER_TIME:
            return self.sp_cost / SP_PER_SECOND
        if str(self.sp_type) == SpType.PER_ATTACK:
            return self.sp_cost * self.attack_interval
        return None

    @property
    def cycle_time(self) -> float | None:
        '''
        Seconds from one activation to the next, or None for skills that
        stay on (no cycle) or whose charge time can't be known.
        '''
        charge_time = self.charge_time
        if self.unlimited or charge_time is None:
            return None
        if self.instant:
            # An attack that becomes the skill gains no SP, so a per-attack skill takes one extra attack per
            # cycle. Skills that gain SP over time keep charging during that attack.
            if self.replaces_attack and str(self.sp_type) == SpType.PER_ATTACK:
                return charge_time + self.attack_interval
            return charge_time
        return charge_time + (self.duration or 0.0)

    @property
    def uptime(self) -> float | None:
        '''
        The fraction of the time the skill is active: 1 for skills that
        stay on, 0 for instant skills, and None when it can't be known.
        '''
        if self.unlimited:
            return 1.0
        cycle_time = self.cycle_time
        if cycle_time is None:
            return None
        if self.instant or cycle_time == 0:
            return 0.0
        return (self.duration or 0.0) / cycle_time

    def average_dps(self, base_dps: float, active_dps: float | None = None, burst_damage: float = 0.0) -> float | None:
        '''
        Returns the average damage per second over one cycle.

        Args:
            base_dps: DPS while the skill is charging.
            active_dps: DPS while the skill is active (not used for
                instant skills). None counts as base_dps.
            burst_damage: For instant skills, the damage the skill adds on
                top of a normal attack (the whole strike for an extra
                strike, or the boosted attack minus a normal attack).

        Returns:
            The average DPS, or None when the cycle can't be known.
        '''
        active_dps = base_dps if active_dps is None else active_dps
        if self.unlimited:
            # Once activated the skill never ends, so in a long fight its DPS is the average.
            return active_dps
        cycle_time = self.cycle_time
        charge_time = self.charge_time
        if cycle_time is None or charge_time is None:
            return None
        if cycle_time == 0:
            return active_dps
        if self.instant:
            return base_dps + burst_damage / cycle_time
        duration = self.duration or 0.0
        return (active_dps * duration + base_dps * charge_time) / cycle_time
