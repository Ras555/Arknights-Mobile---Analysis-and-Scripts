#File Documentation
'''
File: matchups.py
Description: Calculates how operators (and their summons) match up against
    a stage's enemies: the damage per second each operator deals to each
    enemy with no skill, averaged over its best skill rotation and with its
    best skill active (each hit reduced by the enemy's DEF or RES, after the
    operator's own debuffs, DEF/RES ignore and Fragile, and with the
    talents that fit the enemy), the time it takes to defeat the enemy, and
    the damage the enemy deals back.
Created: 2026-09-30
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import partial
from itertools import product

import pandas as pd

from arkdata.calculations.attack_targets import MELEE_POSITION
from arkdata.calculations.damage import MINIMUM_DAMAGE_RATIO, DamageType, damage_per_second
from arkdata.calculations.enemy_stats import APPLY_WAY_NONE, EnemyStats
from arkdata.calculations.operator_profile import (
    MAX_ATTACK_SPEED,
    MIN_ATTACK_SPEED,
    EnemyEffect,
    Hit,
    OperatorProfile,
    SkillProfile,
    UnitStats,
)
from arkdata.calculations.skills import Penetration, is_percent_value
from arkdata.calculations.talents import (
    ENEMY_TYPE_TAGS,
    FRAGILE_ALL,
    FRAGILE_NONE,
    TalentCondition,
    TalentEffect,
    TalentGroup,
)

# Constants

# "Ranged enemies" in talents: applyWay RANGED or ALL (decided by the owner 2026-11-09).
RANGED_APPLY_WAYS = ("RANGED", "ALL")
# Melee enemies only hit operators that block them.
MELEE_APPLY_WAY = "MELEE"
# Enemy-type tags that aren't HellaAPI enemyTags (see talents.ENEMY_TYPE_TAGS).
RANGED_TAG, GROUND_TAG = "ranged", "ground"
# Enemy HP conditions with this minimum ("full HP") only cover the first hit.
FULL_HP = 1.0
# Damage types that hurt enemies (healing and NONE don't).
DAMAGING_TYPES = (DamageType.PHYSICAL, DamageType.ARTS, DamageType.TRUE)
# A figure is (per target, total).
NO_DAMAGE = (0.0, 0.0)

MATCHUP_COLUMNS = [
    "operator_id", "operator_name", "owner", "operator_damage_type", "enemy_id", "enemy_name", "enemy_level",
    "enemy_damage_type", "enemy_apply_way", "enemy_motion", "enemy_spawn_count", "can_attack",
    "min_dps", "avg_dps", "avg_dps_skill", "max_dps", "max_dps_skill",
    "min_dps_total", "avg_dps_total", "avg_dps_total_skill", "max_dps_total", "max_dps_total_skill",
    "min_kill_time", "avg_kill_time", "max_kill_time",
    "enemy_dps_vs_min_def", "enemy_dps_vs_max_def", "max_def_skill", "enemy_kill_time_vs_min_def",
    "enemy_kill_time_vs_max_def", "talents_applied", "notes",
]


class ProfileBuilder:
    '''
    Builds one operator's (or summon's) profile with a set of extra talent
    effects (the PER_ENEMY effects that fit an enemy), keeping each one so
    a profile is only built once per set.

    build takes a frozenset of (talent number, value name) and returns the
    profile (e.g. build_operator_profile with extra_effects). owner is the
    owner's name for a summon ("" for an operator).
    '''

    def __init__(self, build: Callable[[frozenset[tuple[int, str]]], OperatorProfile], owner: str = '',
                 profile: OperatorProfile | None = None):
        '''
        Args:
            build: Builds the profile with a set of extra effects.
            owner: The owner's name, for a summon.
            profile: The profile without extra effects, if already built.
        '''
        self._build = build
        self._profiles: dict[frozenset[tuple[int, str]], OperatorProfile] = {}
        if profile is not None:
            self._profiles[frozenset()] = profile
        self.owner = owner

    def __call__(self, extra: frozenset[tuple[int, str]] = frozenset()) -> OperatorProfile:
        '''
        Returns the profile with the extra effects (built the first time).
        '''
        if extra not in self._profiles:
            self._profiles[extra] = self._build(extra)
        return self._profiles[extra]


@dataclass(frozen=True)
class Scenario:
    '''
    Whether the operator is blocking the enemy. None means either can be
    true: ranged operators count as both blocking and not blocking (decided
    by the owner 2026-11-09).
    '''
    blocking: bool | None = None


@dataclass
class Defenses:
    '''
    An enemy's DEF and RES after an operator's debuffs, and the Fragile
    multipliers on it, by the damage they raise (FRAGILE_ALL or a
    DamageType).
    '''
    defense: float
    resistance: float
    fragile: dict[str, float] = field(default_factory=dict)

    def fragile_for(self, damage_type: str) -> float:
        '''
        The Fragile multiplier on damage of this type (all-damage Fragile
        times the type's own, e.g. Arts Fragility).
        '''
        return self.fragile.get(FRAGILE_ALL, 1.0) * self.fragile.get(str(damage_type), 1.0)


@dataclass
class Figures:
    '''
    An operator's damage against one enemy, each as (per target, total):
    with no skill (base), while each lasting skill is active (active, by
    skill number), and averaged over each skill's rotation (average, by
    skill number). names holds the skill names, and first_hit_seconds the
    attack interval with no skill (for first-hit bonuses).
    '''
    base: tuple[float, float]
    active: dict[int, tuple[float, float]] = field(default_factory=dict)
    average: dict[int, tuple[float, float]] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    first_hit_seconds: float = 0.0


def is_melee(profile: OperatorProfile) -> bool:
    '''
    Whether the operator (or summon) is a melee unit.
    '''
    return profile.position == MELEE_POSITION


def can_block(profile: OperatorProfile, enemy: EnemyStats) -> bool:
    '''
    Whether the operator can block the enemy: it's melee, and the enemy can
    be blocked with its block count (EnemyStats.blockable_by).
    '''
    return is_melee(profile) and enemy.blockable_by(profile.base_stats.block)


def scenarios(profile: OperatorProfile, enemy: EnemyStats) -> list[Scenario]:
    '''
    Returns the ways the matchup can go: ranged operators count as both
    blocking and not; melee operators can be blocking or not when they can
    block the enemy, and otherwise aren't blocking it.
    '''
    if not is_melee(profile):
        return [Scenario(None)]
    if can_block(profile, enemy):
        return [Scenario(True), Scenario(False)]
    return [Scenario(False)]


def _within(value: float, minimum: float | None, maximum: float | None) -> bool:
    '''
    Whether value is between the limits (None means no limit).
    '''
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def condition_met(condition: TalentCondition, enemy: EnemyStats, profile: OperatorProfile, scenario: Scenario,
                  hp_fraction: float | None = None) -> bool:
    '''
    Whether a PER_ENEMY talent condition holds against an enemy.

    aerial: the enemy flies. elite: it's ELITE or BOSS (negated: it isn't).
    weight: within the limits. enemy type: its tag is in the enemy's tags,
    or "ranged" (applyWay RANGED or ALL) or "ground" (it walks); other
    types (e.g. "sleeping") don't count. blocked and unblocked follow the
    scenario (a ranged operator counts as both), and "2 or more blocked"
    needs that much block. enemy HP holds within its limits at
    hp_fraction (the enemy's remaining HP, 0-1; None: never). Enemy status
    conditions never count, since they depend on other operators.

    Args:
        condition: The effect's condition.
        enemy: The enemy.
        profile: The operator (its block count).
        scenario: Whether the operator is blocking the enemy.
        hp_fraction: The enemy's remaining HP, for enemy HP conditions.
    '''
    kind = condition.kind
    if kind == 'aerial':
        return enemy.flying
    if kind == 'elite':
        return enemy.elite != condition.negated
    if kind == 'weight':
        return _within(enemy.weight, condition.minimum, condition.maximum)
    if kind == 'enemy type':
        if condition.tag == RANGED_TAG:
            return enemy.apply_way in RANGED_APPLY_WAYS
        if condition.tag == GROUND_TAG:
            return not enemy.flying
        return condition.tag is not None and condition.tag in enemy.tags
    if kind in ('blocked', 'unblocked'):
        if scenario.blocking is None:
            return True
        if kind == 'unblocked' or condition.negated:
            return not scenario.blocking
        return scenario.blocking and (condition.minimum is None or profile.base_stats.block >= condition.minimum)
    if kind == 'enemy HP' and hp_fraction is not None:
        return (condition.minimum is None or hp_fraction >= condition.minimum) and (
            condition.maximum is None or hp_fraction < condition.maximum)
    return False


def enemy_effects(profile: OperatorProfile) -> list[tuple[int, TalentEffect]]:
    '''
    Returns (talent number, effect) for the operator's PER_ENEMY talent
    effects (each with a condition), which apply in matchups when the enemy
    fits. Effects of talents that are already applied, or that were
    overridden to another group, are left out.
    '''
    return [(talent.number, effect) for talent in profile.talents if not talent.applied for effect in talent.effects
            if effect.group == TalentGroup.PER_ENEMY and effect.condition is not None]


def _is_first_hit(condition: TalentCondition) -> bool:
    '''
    Whether an enemy HP condition only covers the first hit ("full HP").
    '''
    return condition.kind == 'enemy HP' and condition.minimum is not None and condition.minimum >= FULL_HP


def hp_segments(effects: Iterable[tuple[int, TalentEffect]]) -> list[tuple[float, float]]:
    '''
    Returns (share of the enemy's HP, remaining HP in the middle of it) for
    each stretch of the fight between the enemy HP limits of the effects
    ("less than 60% HP" splits it at 0.6): [(0.4, 0.8), (0.6, 0.3)]. With
    no limits, the whole fight is one stretch.
    '''
    points = {1.0, 0.0}
    for _, effect in effects:
        condition = effect.condition
        if condition is not None and condition.kind == 'enemy HP' and not _is_first_hit(condition):
            points.update(limit for limit in (condition.minimum, condition.maximum)
                          if limit is not None and 0 < limit < 1)
    ordered = sorted(points, reverse=True)
    return [(high - low, (high + low) / 2) for high, low in zip(ordered, ordered[1:], strict=False)]


def extra_effects(effects: Iterable[tuple[int, TalentEffect]], enemy: EnemyStats, profile: OperatorProfile,
                  scenario: Scenario, hp_fraction: float | None = None,
                  first_hit: bool = False) -> frozenset[tuple[int, str]]:
    '''
    Returns (talent number, value name) of the effects whose conditions
    hold (condition_met). First-hit ("full HP") effects only count when
    first_hit is True.
    '''
    chosen = set()
    for number, effect in effects:
        condition = effect.condition
        if condition is None:
            continue
        if _is_first_hit(condition):
            if first_hit:
                chosen.add((number, effect.key))
        elif condition_met(condition, enemy, profile, scenario, hp_fraction):
            chosen.add((number, effect.key))
    return frozenset(chosen)


def enemy_defenses(enemy: EnemyStats, effects: Iterable[EnemyEffect]) -> Defenses:
    '''
    Returns the enemy's DEF and RES after an operator's effects on it, and
    the Fragile on it.

    > DEF = (DEF + flat changes) × (1 + increases) × product of (1 - decreases), at least 0

    and the same for RES (Arknights Terra Wiki, Damage/Physical and Arts
    damage). Percentages and flat amounts come from each value
    (skills.is_percent_value); a talent's chance and stacks (scale)
    multiply its amount. A Fragile (damage_scale) of m with scale f raises
    damage taken by 1 + (m - 1) × f; only the highest of each kind counts.

    Args:
        enemy: The enemy.
        effects: The operator's effects on enemies.
    '''
    changes = {'def': [0.0, 0.0, 1.0], 'magic_resistance': [0.0, 0.0, 1.0]}
    fragile: dict[str, float] = {}
    for effect in effects:
        value = effect.value
        if value.key == 'damage_scale':
            if effect.affects != FRAGILE_NONE:
                multiplier = 1 + (value.value - 1) * effect.scale
                fragile[effect.affects] = max(fragile.get(effect.affects, 1.0), multiplier)
            continue
        if value.key not in changes:
            continue
        amount = value.value * effect.scale
        flat, increase, decrease = changes[value.key]
        if not is_percent_value(value):
            flat += amount
        elif amount >= 0:
            increase += amount
        else:
            decrease *= 1 + amount
        changes[value.key] = [flat, increase, decrease]

    def changed(stat: float, key: str) -> float:
        flat, increase, decrease = changes[key]
        return max((stat + flat) * (1 + increase) * decrease, 0.0)

    return Defenses(changed(enemy.stats.get('def', 0), 'def'),
                    changed(enemy.stats.get('magicResistance', 0), 'magic_resistance'), fragile)


def _after_defenses(raw: float, damage_type: str, defenses: Defenses, penetration: Iterable[Penetration]) -> float:
    '''
    Returns one hit's damage after DEF or RES, with the given DEF/RES
    ignore all applying.

    > DEF used = (DEF - flat ignore) × product of (1 - % ignore), at least 0
    > Physical = max(hit - DEF used, 5% of hit); Arts = max(hit × (1 - RES used / 100), 5% of hit)

    RES is kept within 0-100 before the ignore. True damage isn't reduced.
    '''
    penetration = list(penetration)
    if damage_type == DamageType.TRUE:
        return raw
    if damage_type == DamageType.PHYSICAL:
        defense = max(defenses.defense - sum(item.def_flat for item in penetration), 0.0)
        defense *= math.prod(1 - item.def_ratio for item in penetration)
        return max(raw - defense, raw * MINIMUM_DAMAGE_RATIO)
    resistance = min(max(defenses.resistance, 0.0), 100.0)
    resistance = max(resistance - sum(item.res_flat for item in penetration), 0.0)
    resistance *= math.prod(1 - item.res_ratio for item in penetration)
    return max(raw * (1 - resistance / 100), raw * MINIMUM_DAMAGE_RATIO)


def hit_after_defenses(raw: float, damage_type: str, defenses: Defenses, penetration: Iterable[Penetration]) -> float:
    '''
    Returns one hit's expected damage after DEF or RES: DEF/RES ignore with
    a chance below 1 is averaged over whether it applies (each combination
    weighted by its chance).

    Args:
        raw: The hit's damage before DEF/RES.
        damage_type: PHYSICAL, ARTS or TRUE.
        defenses: The enemy's DEF and RES (enemy_defenses).
        penetration: The DEF/RES ignore of the attack.
    '''
    penetration = list(penetration)
    always = [item for item in penetration if item.chance >= 1]
    sometimes = [item for item in penetration if item.chance < 1]
    expected = 0.0
    for applies in product((True, False), repeat=len(sometimes)):
        weight = math.prod(item.chance if on else 1 - item.chance for item, on in zip(sometimes, applies, strict=True))
        active = always + [item for item, on in zip(sometimes, applies, strict=True) if on]
        expected += weight * _after_defenses(raw, damage_type, defenses, active)
    return expected


def damage_against(hits: Iterable[Hit], defenses: Defenses, penetration: Iterable[Penetration],
                   scale: float = 1.0) -> tuple[float, float]:
    '''
    Returns (per target, total) of hits against an enemy: each hit's damage
    (times scale, e.g. a Lord's 80% ranged attacks on aerial enemies) on
    each enemy it hits (Targets.target_scales) after DEF/RES, times its
    damage multiplier, the Fragile on the enemy, and hits per second (or per
    strike). The total assumes every enemy hit is this one. Healing counts
    as 0.

    Args:
        hits: The attack's hits.
        defenses: The enemy's DEF, RES and Fragile (enemy_defenses).
        penetration: The DEF/RES ignore of the attack.
        scale: What to multiply each hit's damage by.
    '''
    penetration = list(penetration)
    per_target = total = 0.0
    for hit in hits:
        if hit.damage_type not in DAMAGING_TYPES or hit.interval <= 0:
            continue
        rate = hit.amplifier * defenses.fragile_for(hit.damage_type) * hit.count / hit.interval
        amounts = [hit_after_defenses(hit.damage * share * scale, hit.damage_type, defenses, penetration) * rate
                   for share in hit.targets.target_scales]
        per_target += amounts[0]
        total += sum(amounts)
    return per_target, total


def attack_scale(profile: OperatorProfile, enemy: EnemyStats, skill: SkillProfile | None = None) -> float:
    '''
    Returns the share of its damage an operator's attacks deal to an enemy
    (0 when it can't attack it). Aerial enemies: profile.aerial_scale (a
    Lord's ranged attacks 0.8, None for other melee units), or 1 while a
    skill that can hit them (Skill.hits_aerial) is active. Ground enemies: 1,
    except units whose normal attacks only hit aerial enemies (Skybreakers),
    which hit them only during skills.

    Args:
        profile: The operator.
        enemy: The enemy.
        skill: The skill whose attacks are being made, or None for normal
            attacks.
    '''
    if enemy.flying:
        if skill is not None and skill.skill.hits_aerial:
            return 1.0
        return profile.aerial_scale or 0.0
    return 1.0 if profile.attacks_ground or skill is not None else 0.0


def figures_against(profile: OperatorProfile, enemy: EnemyStats) -> Figures:
    '''
    Returns an operator's damage against one enemy (see Figures).

    With no skill, its talents' effects on enemies and DEF/RES ignore
    apply. While a timed skill is active, the skill's apply too; an instant
    skill's only apply to its strike (decided by the owner 2026-11-09): its
    burst is the strike minus the attack it replaces, at least 0. Averages
    use each skill's rotation (SkillRotation.average_dps) with these
    figures; skills without an average (e.g. once per battle) have none,
    and only skills that count for max (SkillProfile.counts_for_max) have
    an active figure.

    Args:
        profile: The operator, with the talents that fit the enemy.
        enemy: The enemy.
    '''
    base_defenses = enemy_defenses(enemy, profile.enemy_effects)
    figures = Figures(base=damage_against(profile.base_hits, base_defenses, profile.penetration,
                                          attack_scale(profile, enemy)),
                      first_hit_seconds=profile.base_stats.attack_interval)
    for skill in profile.skills:
        number = skill.skill.number
        figures.names[number] = skill.skill.name
        talent_defenses = enemy_defenses(enemy, skill.base_enemy_effects)
        skill_defenses = enemy_defenses(enemy, skill.enemy_effects)
        base = damage_against(skill.base_hits, talent_defenses, skill.base_penetration, attack_scale(profile, enemy))
        burst = 0.0, 0.0
        if skill.rotation.instant:
            active = damage_against(skill.hits, talent_defenses, skill.base_penetration, attack_scale(profile, enemy))
            strike = damage_against(skill.strike_hits, skill_defenses, skill.penetration,
                                    attack_scale(profile, enemy, skill))
            replaced = damage_against(skill.replaced_hits, talent_defenses, skill.base_penetration,
                                      attack_scale(profile, enemy))
            burst = max(strike[0] - replaced[0], 0.0), max(strike[1] - replaced[1], 0.0)
        else:
            active = damage_against(skill.hits, skill_defenses, skill.penetration, attack_scale(profile, enemy, skill))
        if skill.counts_for_max:
            figures.active[number] = active
        if skill.average_dps is not None:
            average = (skill.rotation.average_dps(base[0], active[0], burst[0]),
                       skill.rotation.average_dps(base[1], active[1], burst[1]))
            if average[0] is not None and average[1] is not None:
                figures.average[number] = (average[0], average[1])
    return figures


def over_segments(values: Iterable[tuple[float, float]]) -> float:
    '''
    Returns the DPS over a whole fight from (share of HP, DPS) stretches:
    1 / sum(share / DPS), so enemy HP / DPS is the total kill time; 0 if
    any stretch can't be finished.
    '''
    values = [(share, dps) for share, dps in values if share > 0]
    if not values or any(dps <= 0 for _, dps in values):
        return 0.0
    return 1 / sum(share / dps for share, dps in values)


def kill_time(hp: float, dps: float) -> float:
    '''
    Returns the seconds to deal hp damage at dps (infinity at 0 DPS).
    '''
    return hp / dps if dps > 0 else math.inf


def _first_hit_dps(hp: float, dps: float, bonus: float) -> float:
    '''
    Returns the DPS over a fight whose first hit deals bonus more damage:
    hp / ((hp - bonus) / dps).
    '''
    if dps <= 0:
        return 0.0
    remaining = max(hp - bonus, 0.0)
    return hp / (remaining / dps) if remaining > 0 else math.inf


def _enemy_attack(enemy: EnemyStats, effects: Iterable[EnemyEffect]) -> tuple[float, float]:
    '''
    Returns the enemy's (ATK, attack interval) after an operator's effects
    on it (ATK and ASPD changes; percentages as in enemy_defenses), with ASPD
    kept between MIN_ATTACK_SPEED and MAX_ATTACK_SPEED.
    '''
    flat = increase = 0.0
    decrease = 1.0
    attack_speed = enemy.stats.get('attackSpeed', 100)
    for effect in effects:
        value = effect.value
        amount = value.value * effect.scale
        if value.key == 'attack_speed':
            attack_speed += amount
        elif value.key == 'atk':
            if not is_percent_value(value):
                flat += amount
            elif amount >= 0:
                increase += amount
            else:
                decrease *= 1 + amount
    atk = max((enemy.stats.get('atk', 0) + flat) * (1 + increase) * decrease, 0.0)
    attack_speed = min(max(attack_speed, MIN_ATTACK_SPEED), MAX_ATTACK_SPEED)
    return atk, enemy.stats.get('baseAttackTime', 0) / (attack_speed / 100)


def enemy_reaches(enemy: EnemyStats, profile: OperatorProfile, scenario: Scenario) -> bool:
    '''
    Whether the enemy can attack the operator: it attacks at all, and a
    melee enemy only hits an operator that's blocking it (so never a ranged
    operator, and an unblockable melee enemy hits no one).
    '''
    if enemy.dps == 0 or enemy.apply_way == APPLY_WAY_NONE:
        return False
    if enemy.apply_way == MELEE_APPLY_WAY:
        return is_melee(profile) and scenario.blocking is True and can_block(profile, enemy)
    return True


def enemy_damage(enemy: EnemyStats, stats: UnitStats, effects: Iterable[EnemyEffect]) -> float:
    '''
    Returns the enemy's DPS against an operator's stats, after the
    operator's effects on the enemy's ATK and ASPD.
    '''
    atk, interval = _enemy_attack(enemy, effects)
    if interval <= 0:
        return 0.0
    return damage_per_second(atk, interval, enemy.damage_type, stats.defense, stats.magic_resistance)


def _best(options: dict[str, float]) -> tuple[float, str]:
    '''
    Returns the highest (figure, skill name), no skill ("") first on ties.
    '''
    return max(((value, name) for name, value in options.items()), key=lambda pair: pair[0])


def matchup_row(builder: ProfileBuilder, enemy: EnemyStats, spawn_count: int = 0) -> dict:
    '''
    Returns one operator's matchup against one enemy (one MATCHUP_COLUMNS
    row).

    Every way the matchup can go (scenarios) and every stretch of the
    enemy's HP its talents care about (hp_segments) gets a profile with the
    talents that fit (extra_effects). Each figure takes the best way for
    the operator (decided by the owner 2026-11-09): damage figures the
    highest over the scenarios, and the best skill against this enemy;
    over the stretches, DPS = enemy HP / kill time. A first-hit bonus
    ("full HP") adds one attack's extra damage to the per-target figures.

    The enemy's damage is against the operator's min DEF/RES (no skill) and
    max DEF/RES (whichever lasting skill, or none, it hurts least, with
    that skill's ATK/ASPD debuffs on the enemy), in the scenario where the
    enemy can reach it (a melee enemy only when blocked), the one it hurts
    least otherwise.

    Args:
        builder: Builds the operator's profile with extra effects.
        enemy: The enemy.
        spawn_count: How many of the enemy the stage spawns.
    '''
    profile = builder()
    effects = enemy_effects(profile)
    segments = hp_segments(effects)
    has_first_hit = any(effect.condition is not None and _is_first_hit(effect.condition) for _, effect in effects)
    hp = enemy.stats.get('maxHp', 0)

    applied: set[tuple[int, str]] = set()
    best: dict[str, tuple[float, str]] = {}
    for scenario in scenarios(profile, enemy):
        per_segment = []
        for share, middle in segments:
            extra = extra_effects(effects, enemy, profile, scenario, middle)
            applied |= extra
            per_segment.append((share, figures_against(builder(extra), enemy)))
        first = None
        if has_first_hit:
            extra = extra_effects(effects, enemy, profile, scenario, segments[0][1], first_hit=True)
            applied |= extra
            first = figures_against(builder(extra), enemy)
        options = _options(per_segment, first, hp)
        for key, (value, name) in options.items():
            if key not in best or value > best[key][0]:
                best[key] = (value, name)

    min_def, max_def, max_def_skill = _enemy_side(builder, enemy, effects)
    notes = _notes(profile, enemy, effects)
    conditions = {(number, effect.key): effect.condition for number, effect in effects}
    talents_applied = sorted({f'{_talent_name(profile, number)} ({condition.describe()})'
                              for (number, key), condition in conditions.items()
                              if (number, key) in applied and condition is not None})
    return {
        'operator_id': profile.operator_id, 'operator_name': profile.name, 'owner': builder.owner,
        'operator_damage_type': profile.damage_type, 'enemy_id': enemy.enemy_id, 'enemy_name': enemy.name,
        'enemy_level': enemy.level, 'enemy_damage_type': enemy.damage_type, 'enemy_apply_way': enemy.apply_way,
        'enemy_motion': enemy.motion, 'enemy_spawn_count': spawn_count, 'can_attack': best['max_dps'][0] > 0,
        'min_dps': best['min_dps'][0], 'avg_dps': best['avg_dps'][0], 'avg_dps_skill': best['avg_dps'][1],
        'max_dps': best['max_dps'][0], 'max_dps_skill': best['max_dps'][1],
        'min_dps_total': best['min_dps_total'][0], 'avg_dps_total': best['avg_dps_total'][0],
        'avg_dps_total_skill': best['avg_dps_total'][1], 'max_dps_total': best['max_dps_total'][0],
        'max_dps_total_skill': best['max_dps_total'][1],
        'min_kill_time': kill_time(hp, best['min_dps'][0]), 'avg_kill_time': kill_time(hp, best['avg_dps'][0]),
        'max_kill_time': kill_time(hp, best['max_dps'][0]),
        'enemy_dps_vs_min_def': min_def[0], 'enemy_dps_vs_max_def': max_def[0], 'max_def_skill': max_def_skill,
        'enemy_kill_time_vs_min_def': kill_time(min_def[1], min_def[0]),
        'enemy_kill_time_vs_max_def': kill_time(max_def[1], max_def[0]),
        'talents_applied': '; '.join(talents_applied), 'notes': '; '.join(notes),
    }


def _options(per_segment: list[tuple[float, Figures]], first: Figures | None, hp: float) -> dict[str, tuple[float, str]]:
    '''
    Returns the best (figure, skill name) of each kind (min/avg/max DPS, per
    target and total) for one scenario, over the HP stretches (see
    matchup_row). first is the figures with first-hit bonuses, if any.
    '''
    top = per_segment[0][1]
    result = {}
    for index, suffix in ((0, ''), (1, '_total')):
        def whole_fight(pick: Callable[[Figures], tuple[float, float] | None], index: int = index) -> float | None:
            # The figure over every stretch (None when the skill has none), with a first-hit bonus per target.
            picked = [(share, pick(segment)) for share, segment in per_segment]
            if any(figure is None for _, figure in picked):
                return None
            dps = over_segments((share, figure[index]) for share, figure in picked if figure is not None)
            with_bonus, without = (pick(first), pick(top)) if first is not None and index == 0 else (None, None)
            if with_bonus is not None and without is not None and hp > 0:
                dps = _first_hit_dps(hp, dps, max(with_bonus[0] - without[0], 0.0) * top.first_hit_seconds)
            return dps

        minimum = whole_fight(lambda figures: figures.base) or 0.0
        averages, maxima = {'': minimum}, {'': minimum}
        for number, name in top.names.items():
            average = whole_fight(partial(_skill_figure, 'average', number))
            if average is not None:
                averages[name] = max(average, averages.get(name, 0.0))
            active = whole_fight(partial(_skill_figure, 'active', number))
            if active is not None:
                maxima[name] = max(active, maxima.get(name, 0.0))
        result[f'min_dps{suffix}'] = (minimum, '')
        result[f'avg_dps{suffix}'] = _best(averages)
        result[f'max_dps{suffix}'] = _best(maxima)
    return result


def _skill_figure(kind: str, number: int, figures: Figures) -> tuple[float, float] | None:
    '''
    Returns a skill's figure of one kind ("average" or "active"), or None
    when it has none.
    '''
    return (figures.average if kind == 'average' else figures.active).get(number)


def _enemy_side(builder: ProfileBuilder, enemy: EnemyStats,
                effects: list[tuple[int, TalentEffect]]) -> tuple[tuple[float, float], tuple[float, float], str]:
    '''
    Returns ((enemy DPS, operator HP) against min DEF/RES, the same against
    max DEF/RES, the skill used for max) in the scenario where the enemy
    reaches the operator and hurts it least (see matchup_row).
    '''
    profile = builder()
    reachable = [scenario for scenario in scenarios(profile, enemy) if enemy_reaches(enemy, profile, scenario)]
    if not reachable:
        return (0.0, profile.base_stats.max_hp), (0.0, profile.base_stats.max_hp), ''
    minimum = maximum = None
    max_skill = ''
    for scenario in reachable:
        unit = builder(extra_effects(effects, enemy, profile, scenario))
        base = (enemy_damage(enemy, unit.base_stats, unit.enemy_effects), unit.base_stats.max_hp)
        if minimum is None or base[0] < minimum[0]:
            minimum = base
        if maximum is None or base[0] < maximum[0]:
            maximum, max_skill = base, ''
        for skill in unit.skills:
            if not skill.counts_for_max:
                continue
            during = (enemy_damage(enemy, skill.stats, skill.enemy_effects), skill.stats.max_hp)
            if during[0] < maximum[0]:
                maximum, max_skill = during, skill.skill.name
    assert minimum is not None and maximum is not None
    return minimum, maximum, max_skill


def _talent_name(profile: OperatorProfile, number: int) -> str:
    '''
    Returns a talent's name by number.
    '''
    return next((talent.name for talent in profile.talents if talent.number == number), f'talent {number}')


def _notes(profile: OperatorProfile, enemy: EnemyStats, effects: list[tuple[int, TalentEffect]]) -> list[str]:
    '''
    Returns notes on a matchup: attacking aerial enemies, blocking, the
    enemy's reach, first-hit bonuses and conditions that can't be judged.
    '''
    notes = []
    if enemy.flying:
        aerial_skills = [skill.skill.name for skill in profile.skills if skill.skill.hits_aerial]
        if profile.aerial_scale is None:
            text = "can't attack aerial enemies"
            notes.append(f"{text} except during {', '.join(aerial_skills)}" if aerial_skills else text)
        elif profile.aerial_scale != 1:
            notes.append(f'attacks aerial enemies with ranged attacks at {profile.aerial_scale:.0%} damage')
    elif not profile.attacks_ground:
        notes.append('normal attacks only hit aerial enemies; ground enemies only during skills')
    if is_melee(profile) and not enemy.flying and not can_block(profile, enemy):
        if enemy.min_block is None:
            notes.append("this enemy can't be blocked")
        elif profile.base_stats.block < 1:
            notes.append("doesn't block enemies")
        else:
            notes.append(f"can't block this enemy (needs {enemy.min_block:g} block, has {profile.base_stats.block:g})")
    if enemy.apply_way == MELEE_APPLY_WAY and enemy.dps > 0 and not is_melee(profile):
        notes.append("melee enemy: can't reach a ranged operator")
    if any(effect.condition is not None and _is_first_hit(effect.condition) for _, effect in effects):
        notes.append('full-HP bonus counted on the first hit only')
    unjudged = sorted({_talent_name(profile, number) for number, effect in effects
                       if effect.condition is not None and not _judged(effect.condition)})
    if unjudged:
        notes.append(f"not applied (depends on the fight): {', '.join(unjudged)}")
    return notes


def _judged(condition: TalentCondition) -> bool:
    '''
    Whether a condition can be judged from the enemy's data: not an enemy
    status, nor an enemy type the data doesn't have (e.g. "sleeping").
    '''
    if condition.kind == 'enemy status':
        return False
    return condition.kind != 'enemy type' or condition.tag in ENEMY_TYPE_TAGS.values()


def calculate_matchups(units: Iterable[ProfileBuilder], enemies: list) -> pd.DataFrame:
    '''
    Returns one row per (operator or summon, enemy) pair (MATCHUP_COLUMNS,
    see matchup_row).

    Args:
        units: A ProfileBuilder for each operator and summon.
        enemies: (EnemyStats, spawn count) pairs, as returned by
            load_stage_enemies.
    '''
    rows = [matchup_row(unit, enemy, spawn_count) for unit in units for enemy, spawn_count in enemies]
    return pd.DataFrame(rows, columns=MATCHUP_COLUMNS)
