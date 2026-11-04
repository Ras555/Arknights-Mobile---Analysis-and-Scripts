#File Documentation
'''
File: skill_rotation.py
Description: Models one skill cycle (charging SP, then the skill being
    active) to work out how long a cycle takes, how much of it the skill
    is active for, and the average damage per second over the cycle.
    Covers skills that gain SP over time, per attack, or when hit (with an
    assumed rate of incoming attacks), always-on passives, and skills that
    start on deployment.
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

# Skills that gain SP when hit gain 1 SP per hit taken, so their rotation depends on the enemies. Without a stage,
# one enemy is assumed to attack every 3.0 s: the median attack interval of the 1,313 attacking enemies in the
# HellaAPI data (2026-11-04; Normal 2.5 s, Elite 3.5 s, Boss 4.0 s).
ASSUMED_ENEMY_ATTACK_INTERVAL = 3.0
ASSUMED_ATTACKERS = 1


class SpType(StrEnum):
    '''
    How a skill gains SP. The HellaAPI gives passive skills the number 8
    instead of a name, so PASSIVE is "8".
    '''
    OVER_TIME = "INCREASE_WITH_TIME"
    PER_ATTACK = "INCREASE_WHEN_ATTACK"
    WHEN_HIT = "INCREASE_WHEN_TAKEN_DAMAGE"
    PASSIVE = "8"


class SkillTrigger(StrEnum):
    '''
    What starts a skill. SP skills charge SP and repeat; ALWAYS skills
    (always-on passives) never stop; DEPLOYMENT skills ("After deployment")
    happen once each time the operator is deployed; OTHER skills are
    started by something else (e.g. spending Coins), so their rotation
    isn't known.
    '''
    SP = "SP"
    ALWAYS = "ALWAYS"
    DEPLOYMENT = "DEPLOYMENT"
    OTHER = "OTHER"


def hit_interval(attackers: float = ASSUMED_ATTACKERS, enemy_attack_interval: float = ASSUMED_ENEMY_ATTACK_INTERVAL) -> float:
    '''
    Returns the seconds between hits an operator takes, for skills that gain
    SP when hit.

    Args:
        attackers: How many enemies attack the operator (e.g. 1, or its
            block count for a full block).
        enemy_attack_interval: Seconds between each enemy's attacks.

    Returns:
        enemy_attack_interval / attackers (with at least 1 attacker).
    '''
    return enemy_attack_interval / max(attackers, 1)


@dataclass(frozen=True)
class SkillRotation:
    '''
    One skill cycle: charge SP, then the skill is active, then repeat.
    SP doesn't charge while a skill is active.

    duration is how long the skill is active, in seconds: 0 for an instant
    skill (e.g. "the next attack deals 250%"), None for a skill that stays
    on once activated. For ammo skills, pass ammo * attack interval.
    attack_interval is the seconds between attacks while charging, used for
    skills that gain SP per attack. hit_interval is the seconds between hits
    the operator takes, used for skills that gain SP when hit (see
    hit_interval()); without it their cycle isn't known. replaces_attack is
    True when an instant skill turns an attack into the skill (e.g. "the
    next attack"), which then gains no SP, and False when it's an extra
    strike on activation.

    trigger (a SkillTrigger) decides whether there's a cycle at all:
    ALWAYS skills are always active; DEPLOYMENT skills happen once per
    deployment, so in a long fight they're left out of the average (it's
    the base DPS), unless they last until the operator retreats; OTHER
    skills have no known cycle.
    '''
    sp_type: str
    sp_cost: float
    duration: float | None
    attack_interval: float
    trigger: str = SkillTrigger.SP
    replaces_attack: bool = True
    hit_interval: float | None = None

    @property
    def unlimited(self) -> bool:
        '''
        Whether the skill is always active once it starts: always-on
        passives, and SP or deployment skills with no end (duration None).
        '''
        if self.trigger == SkillTrigger.ALWAYS:
            return True
        return self.trigger in (SkillTrigger.SP, SkillTrigger.DEPLOYMENT) and self.duration is None

    @property
    def instant(self) -> bool:
        '''
        Whether the skill acts once and ends (duration 0).
        '''
        return not self.unlimited and self.duration == 0

    @property
    def charge_time(self) -> float | None:
        '''
        Seconds to charge the skill's SP: 0 for always-on skills, and None
        when there's no SP cycle (deployment and other skills) or it can't
        be known (SP gained when hit, without a hit_interval).
        '''
        if self.trigger == SkillTrigger.ALWAYS:
            return 0.0
        if self.trigger != SkillTrigger.SP:
            return None
        if str(self.sp_type) == SpType.OVER_TIME:
            return self.sp_cost / SP_PER_SECOND
        if str(self.sp_type) == SpType.PER_ATTACK:
            return self.sp_cost * self.attack_interval
        if str(self.sp_type) == SpType.WHEN_HIT and self.hit_interval is not None:
            return self.sp_cost * self.hit_interval
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
            # cycle. Skills that gain SP over time or when hit keep charging during that attack.
            if self.replaces_attack and str(self.sp_type) == SpType.PER_ATTACK:
                return charge_time + self.attack_interval
            return charge_time
        return charge_time + (self.duration or 0.0)

    @property
    def uptime(self) -> float | None:
        '''
        The fraction of the time the skill is active: 1 for skills that
        stay on, 0 for instant skills and for deployment skills (once per
        deployment, so none of a long fight), and None when it can't be
        known.
        '''
        if self.unlimited:
            return 1.0
        if self.trigger == SkillTrigger.DEPLOYMENT:
            return 0.0
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
        if self.trigger == SkillTrigger.DEPLOYMENT:
            # Happens once per deployment, so it's left out of a long fight's average.
            return base_dps
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
