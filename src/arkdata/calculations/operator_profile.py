#File Documentation
'''
File: operator_profile.py
Description: Puts an operator's stats, talents and skills together into an
    OperatorProfile: its stats and damage (or healing) per second with no
    skill, while each skill is active, and averaged over each skill's
    rotation, plus a summary (min/average/max DPS, DEF, RES, HP and
    healing). Summons are profiled the same way at their owner's level.
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from arkdata.calculations.attack_targets import (
    NO_BASIC_ATTACK_SUB_PROFESSIONS,
    Targets,
    basic_attack_strikes,
    basic_attack_targets,
)
from arkdata.calculations.blackboard import BlackboardValue
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_stats import OperatorStats
from arkdata.calculations.skill_rotation import SkillRotation, SkillTrigger, SpType, hit_interval
from arkdata.calculations.skills import (
    MAX_SKILL_LEVEL,
    PENETRATION_KEYS,
    DamagePart,
    Penetration,
    Skill,
    StatModifiers,
    parse_skill,
    unlocked_skills,
)
from arkdata.calculations.talents import STACK_WORDS, Talent, build_talents, member_groups, unlocked_candidate

logger = logging.getLogger(__name__)

# Constants

# Stacking talents are averaged over one rotation of the skill being used. With no rotation (no skill, or a skill
# that stays on), they're averaged over this many seconds instead.
DEFAULT_STACK_WINDOW = 60.0

# The game keeps every unit's ASPD (operators' and enemies') between these limits, whatever buffs and debuffs add up
# to: "the final ASPD value can never go below 20 or above 600" (https://arknights.wiki.gg/wiki/Attack_interval,
# checked 2026-11-05).
MIN_ATTACK_SPEED = 20.0
MAX_ATTACK_SPEED = 600.0

# Skills that can only be used once per battle ("This skill can only be used once per battle") have no rotation, so
# they're left out of average and max DPS, like deployment skills.
ONCE_PER_BATTLE = re.compile(r'once per battle|only be used once', re.IGNORECASE)

# Statuses (of skills and talents) that make up an overall status.
FULL, PARTIAL, NONE = 'full', 'partial', 'none'


@dataclass(frozen=True)
class UnitStats:
    '''
    A unit's stats with talent (and skill) changes applied. attack_speed is
    ASPD (100 = normal) and attack_interval the seconds between attacks.
    '''
    atk: float
    defense: float
    max_hp: float
    magic_resistance: float
    attack_speed: float
    attack_interval: float
    block: float


@dataclass(frozen=True)
class Damage:
    '''
    Damage (or healing, for HEAL) of one type, on each enemy hit and over
    every enemy hit. Depending on where it's used, it's per second or one
    strike's amount.
    '''
    damage_type: str
    per_target: float
    total: float


@dataclass(frozen=True)
class Hit:
    '''
    Hits of one kind, kept so the enemy's DEF or RES can be taken off each
    one (in matchups).

    damage is one hit's damage on the main target before DEF/RES: ATK ×
    the hit's ATK multiplier × talent ATK multipliers. amplifier is the
    talent damage multipliers, counted after DEF/RES (1 for healing).
    count is how many of these hits land on each enemy per attack (or per
    second, or per strike), and interval the seconds each count takes (the
    attack interval; 1 for damage every second and for one strike).
    targets says which enemies are hit and how much of the damage each
    takes (Targets.target_scales).
    '''
    damage_type: str
    damage: float
    amplifier: float
    count: float
    interval: float
    targets: Targets

    @property
    def per_target(self) -> float:
        '''
        Damage per second (or per strike) on the main target, before DEF/RES.
        '''
        return self.damage * self.amplifier * self.count / self.interval if self.interval > 0 else 0.0

    @property
    def total(self) -> float:
        '''
        Damage per second (or per strike) over every enemy hit, before DEF/RES.
        '''
        return self.per_target * self.targets.damage_share


@dataclass
class SkillProfile:
    '''
    How an operator does with one of its skills.

    stats and damage are while the skill is active (damage per second);
    for instant skills damage is the normal attack, and burst is the extra
    damage each activation adds. rotation is the skill's cycle, and the
    averages are over that cycle (None when it can't be known). For
    skills that gain SP when hit, the full_block averages assume every
    enemy the operator can block attacks it. talents are the operator's
    talents averaged over this skill's rotation (window seconds).

    For matchups, the damage is also kept as hits: base_hits with no skill
    (with this skill's talents), hits while active (the normal attack for
    instant skills), and for instant skills strike_hits (one activation's
    strike) and replaced_hits (the normal attack it replaces, if any).
    penetration is the DEF/RES ignored while the skill is active (the
    skill's and the applied talents'), and enemy_effects the skill's and
    applied talents' effects on enemies (DEF/RES ignore left out).
    '''
    skill: Skill
    stats: UnitStats
    damage: list[Damage]
    burst: list[Damage]
    rotation: SkillRotation
    window: float
    talents: list[Talent]
    average_dps: float | None = None
    average_dps_total: float | None = None
    average_hps: float | None = None
    full_block_average_dps: float | None = None
    full_block_average_dps_total: float | None = None
    base_hits: list[Hit] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    strike_hits: list[Hit] = field(default_factory=list)
    replaced_hits: list[Hit] = field(default_factory=list)
    penetration: list[Penetration] = field(default_factory=list)
    enemy_effects: list[BlackboardValue] = field(default_factory=list)

    @property
    def dps(self) -> float:
        '''
        Damage per second on each enemy hit while the skill is active.
        '''
        return damage_sum(self.damage)[0]

    @property
    def dps_total(self) -> float:
        '''
        Damage per second over every enemy hit while the skill is active.
        '''
        return damage_sum(self.damage)[1]

    @property
    def hps(self) -> float:
        '''
        Healing per second on each ally healed while the skill is active.
        '''
        return healing_sum(self.damage)[0]

    @property
    def counts_for_max(self) -> bool:
        '''
        Whether the skill lasts long enough to count towards max DPS, DEF,
        RES and HP: it has a duration (timed, until retreat, ammo, always
        on, or a timed deployment skill), isn't started by something
        unknown, and can be used more than once per battle.
        '''
        return (not self.rotation.instant and self.skill.trigger != SkillTrigger.OTHER
                and not self.once_per_battle)

    @property
    def once_per_battle(self) -> bool:
        '''
        Whether the skill can only be used once per battle.
        '''
        return bool(ONCE_PER_BATTLE.search(self.skill.description))


@dataclass(frozen=True)
class ProfileSummary:
    '''
    An operator's figures in one row. DPS figures come per target and in
    total (over every enemy hit), each naming the skill it comes from (""
    for no skill). Healing (hps) is only given for HEAL operators. status
    is "full" when every skill and talent was fully modelled, "none" when
    none could be, and "partial" otherwise.
    '''
    min_dps: float
    min_dps_total: float
    avg_dps: float
    avg_dps_skill: str
    avg_dps_total: float
    avg_dps_total_skill: str
    max_dps: float
    max_dps_skill: str
    max_dps_total: float
    max_dps_total_skill: str
    min_def: float
    max_def: float
    min_res: float
    max_res: float
    min_hp: float
    max_hp: float
    min_hps: float | None
    avg_hps: float | None
    max_hps: float | None
    status: str


@dataclass
class OperatorProfile:
    '''
    An operator (or summon) at its phase, level, potential, trust, skill
    level and masteries.

    base_stats and base_damage (per second) are with no skill active, with
    the talents that are applied (ALWAYS and LIKELY groups) averaged over
    DEFAULT_STACK_WINDOW. skills holds one SkillProfile per unlocked skill.
    summons lists the ids of the operator's summons; owner_id is set on a
    summon's own profile. member_of holds the groups it belongs to (class,
    position, rarity, factions; see talents.member_groups), for talents
    that buff a group. base_hits, penetration and enemy_effects are as in
    SkillProfile, with no skill.
    '''
    operator_id: str
    name: str
    profession: str
    sub_profession_id: str
    damage_type: str
    phase: int
    level: int
    potential: int
    trust: int
    base_stats: UnitStats
    base_damage: list[Damage]
    talents: list[Talent]
    skills: list[SkillProfile] = field(default_factory=list)
    summons: list[str] = field(default_factory=list)
    owner_id: str | None = None
    member_of: frozenset[str] = frozenset()
    notes: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    base_hits: list[Hit] = field(default_factory=list)
    penetration: list[Penetration] = field(default_factory=list)
    enemy_effects: list[BlackboardValue] = field(default_factory=list)

    def summary(self) -> ProfileSummary:
        '''
        Returns the operator's figures in one row (see ProfileSummary).
        '''
        min_dps, min_total = damage_sum(self.base_damage)
        lasting = [skill for skill in self.skills if skill.counts_for_max]

        max_dps, max_skill = max([(min_dps, '')] + [(skill.dps, skill.skill.name) for skill in lasting], key=_value)
        max_total, max_total_skill = max([(min_total, '')] + [(skill.dps_total, skill.skill.name) for skill in lasting],
                                         key=_value)
        # No skill comes first, so a skill only names the average when it raises it (deployment skills, left out of
        # the average, don't).
        avg_dps, avg_skill = max([(min_dps, '')] + [(skill.average_dps, skill.skill.name) for skill in self.skills
                                                    if skill.average_dps is not None], key=_value)
        avg_total, avg_total_skill = max([(min_total, '')] + [(skill.average_dps_total, skill.skill.name)
                                                              for skill in self.skills
                                                              if skill.average_dps_total is not None], key=_value)

        stats = [self.base_stats] + [skill.stats for skill in lasting]
        min_hps = avg_hps = max_hps = None
        if self.damage_type == DamageType.HEAL:
            min_hps = healing_sum(self.base_damage)[0]
            max_hps = max([min_hps] + [skill.hps for skill in lasting])
            avg_hps = max([min_hps] + [skill.average_hps for skill in self.skills if skill.average_hps is not None])

        return ProfileSummary(
            min_dps=min_dps, min_dps_total=min_total,
            avg_dps=avg_dps, avg_dps_skill=avg_skill, avg_dps_total=avg_total, avg_dps_total_skill=avg_total_skill,
            max_dps=max_dps, max_dps_skill=max_skill, max_dps_total=max_total, max_dps_total_skill=max_total_skill,
            min_def=self.base_stats.defense, max_def=max(stat.defense for stat in stats),
            min_res=self.base_stats.magic_resistance, max_res=max(stat.magic_resistance for stat in stats),
            min_hp=self.base_stats.max_hp, max_hp=max(stat.max_hp for stat in stats),
            min_hps=min_hps, avg_hps=avg_hps, max_hps=max_hps,
            status=overall_status([skill.skill.status for skill in self.skills] + [talent.status for talent in self.talents]),
        )


def _value(pair: tuple[float, str]) -> float:
    '''
    The number in a (figure, skill name) pair, for max().
    '''
    return pair[0]


def damage_sum(damage: Iterable[Damage]) -> tuple[float, float]:
    '''
    Returns (per target, total) of every damage type except healing.
    '''
    hits = [part for part in damage if part.damage_type != DamageType.HEAL]
    return sum(part.per_target for part in hits), sum(part.total for part in hits)


def healing_sum(damage: Iterable[Damage]) -> tuple[float, float]:
    '''
    Returns (per target, total) of the healing.
    '''
    heals = [part for part in damage if part.damage_type == DamageType.HEAL]
    return sum(part.per_target for part in heals), sum(part.total for part in heals)


def overall_status(statuses: Iterable[str]) -> str:
    '''
    Returns "full" when every status is full (or there are none), "none"
    when every one is none, and "partial" otherwise.

    Args:
        statuses: Skill and talent statuses ("full", "partial", "none").
    '''
    statuses = [str(status) for status in statuses]
    if all(status == FULL for status in statuses):
        return FULL
    if all(status == NONE for status in statuses):
        return NONE
    return PARTIAL


def apply_modifiers(stats: dict, modifiers: StatModifiers) -> UnitStats:
    '''
    Returns a unit's stats with stat changes applied.

    > ATK = (ATK + flat ATK) × (1 + ATK %), and the same for DEF, HP and RES
    > attack interval = (base attack time + change) / (ASPD / 100), with
    > ASPD kept between MIN_ATTACK_SPEED and MAX_ATTACK_SPEED

    Args:
        stats: The unit's stats (OperatorStats.stats: atk, def, maxHp,
            magicResistance, attackSpeed, baseAttackTime, blockCnt).
        modifiers: The changes to apply (from talents and/or a skill).

    Returns:
        The changed stats. None of them goes below 0.
    '''
    attack_speed = min(max(stats['attackSpeed'] + modifiers.attack_speed, MIN_ATTACK_SPEED), MAX_ATTACK_SPEED)
    base_attack_time = max(stats['baseAttackTime'] + modifiers.attack_interval_change, 0.0)
    return UnitStats(
        atk=max((stats['atk'] + modifiers.atk_flat) * (1 + modifiers.atk_percent), 0.0),
        defense=max((stats['def'] + modifiers.def_flat) * (1 + modifiers.def_percent), 0.0),
        max_hp=max((stats['maxHp'] + modifiers.hp_flat) * (1 + modifiers.hp_percent), 0.0),
        magic_resistance=max((stats['magicResistance'] + modifiers.res_flat) * (1 + modifiers.res_percent), 0.0),
        attack_speed=attack_speed,
        attack_interval=base_attack_time / (attack_speed / 100),
        block=max(stats.get('blockCnt', 0) + modifiers.block, 0.0),
    )


def attack_hits(stats: UnitStats, parts: Iterable[DamagePart], atk_multiplier: float = 1.0,
                damage_multiplier: float = 1.0, strikes: tuple[float, ...] = (1.0,), per_strike: bool = False) -> list[Hit]:
    '''
    Returns the hits of attacks made of parts (see Hit).

    > one hit = ATK × ATK multiplier × strike × talent ATK multiplier
    > per target = one hit × talent damage multiplier × hits / attack interval

    Parts with 1 hit use strikes (the archetype's strikes,
    basic_attack_strikes: a Swordmaster's (1, 1), a Flinger's (1, 0.5)), for
    normal attacks and skills that change them, since only skills that
    change the number of hits say so; a skill's own one-off strike passes
    (1,). Equal strikes are counted together. Parts dealt every second
    (per_second), and every part when per_strike is True (one strike of an
    instant skill), aren't divided by the attack interval. Healing isn't
    multiplied by talent multipliers.

    Args:
        stats: The unit's stats while attacking.
        parts: The attack's damage parts.
        atk_multiplier: Talent ATK multipliers, multiplied together.
        damage_multiplier: Talent damage multipliers, multiplied together.
        strikes: The archetype's strikes per attack, as fractions of ATK.
        per_strike: True for one strike rather than per second.

    Returns:
        The hits, in part order.
    '''
    hits = []
    for part in parts:
        heal = part.damage_type == DamageType.HEAL
        atk_scale, amplifier = (1.0, 1.0) if heal else (atk_multiplier, damage_multiplier)
        interval = 1.0 if per_strike or part.per_second else stats.attack_interval
        counts: dict[float, float] = {}
        if part.hits != 1:
            counts[1.0] = part.hits
        else:
            for strike in strikes:
                counts[strike] = counts.get(strike, 0.0) + 1.0
        for strike, count in counts.items():
            hits.append(Hit(part.damage_type, stats.atk * part.atk_scale * strike * atk_scale, amplifier, count, interval,
                            part.targets))
    return hits


def hit_damage(hits: Iterable[Hit]) -> list[Damage]:
    '''
    Returns one Damage (per target and in total, before DEF/RES) per hit.
    '''
    return [Damage(hit.damage_type, hit.per_target, hit.total) for hit in hits]


def damage_per_second(stats: UnitStats, parts: Iterable[DamagePart], damage_multiplier: float = 1.0,
                      basic_hits: float = 1.0) -> list[Damage]:
    '''
    Returns the damage per second of attacks made of parts.

    > per target = ATK × ATK multiplier × hits × damage multiplier / attack interval
    > total = per target × Targets.damage_share

    Parts dealt every second (per_second) aren't divided by the attack
    interval. Parts with 1 hit use basic_hits (the archetype's hits, a
    Swordmaster's 2, for normal attacks and skills that change them), since
    only skills that change the number of hits say so; a skill's own
    one-off strike passes 1.
    Healing isn't multiplied by damage_multiplier. (attack_hits keeps
    each strike apart and the two kinds of talent multiplier apart.)

    Args:
        stats: The unit's stats while attacking.
        parts: The attack's damage parts.
        damage_multiplier: Talent damage multipliers, multiplied together.
        basic_hits: The archetype's hits per attack (basic_attack_hits).

    Returns:
        One Damage per part.
    '''
    return hit_damage(attack_hits(stats, parts, 1.0, damage_multiplier, (basic_hits,)))


def strike_damage(stats: UnitStats, parts: Iterable[DamagePart], damage_multiplier: float = 1.0,
                  basic_hits: float = 1.0) -> list[Damage]:
    '''
    Returns the damage of one strike made of parts (an instant skill, or
    one normal attack), per target and in total. See damage_per_second.
    '''
    return hit_damage(attack_hits(stats, parts, 1.0, damage_multiplier, (basic_hits,), per_strike=True))


def subtract(strike: list[Damage], normal: list[Damage]) -> list[Damage]:
    '''
    Returns strike minus normal, by damage type (an instant skill that
    replaces an attack only adds the difference). Types left below 0 are
    dropped.
    '''
    result: dict[str, list[float]] = {}
    for part in strike:
        result.setdefault(part.damage_type, [0.0, 0.0])
        result[part.damage_type][0] += part.per_target
        result[part.damage_type][1] += part.total
    for part in normal:
        result.setdefault(part.damage_type, [0.0, 0.0])
        result[part.damage_type][0] -= part.per_target
        result[part.damage_type][1] -= part.total
    return [Damage(damage_type, per_target, total) for damage_type, (per_target, total) in result.items()
            if per_target > 0 or total > 0]


def trait_values(data: dict, phase: int, level: int, potential: int) -> dict:
    '''
    Returns {name: value} from the operator's unlocked trait version (used
    for Chain Caster jumps, Earthshaker splash and Flinger shockwaves).
    '''
    candidate = unlocked_candidate(data.get('trait') or {}, phase, level, potential) or {}
    return {entry['key']: entry['value'] for entry in candidate.get('blackboard') or [] if entry.get('value') is not None}


def fit_skill_level(skill_level: int, mastery: int, level_count: int) -> tuple[int, int, bool]:
    '''
    Returns (skill level, mastery, was it changed) that exist for a skill
    with level_count levels: a summon's skill with 1 level uses that level
    whatever its owner's skill level, and a skill without masteries uses
    level 7.

    Args:
        skill_level: The wanted skill level (1-7).
        mastery: The wanted mastery (0-3; only counts at skill level 7).
        level_count: How many levels the skill has.
    '''
    wanted = skill_level - 1 + (mastery if skill_level == MAX_SKILL_LEVEL else 0)
    index = max(min(wanted, level_count - 1), 0)
    if index < MAX_SKILL_LEVEL:
        return index + 1, 0, index != wanted
    return MAX_SKILL_LEVEL, index - MAX_SKILL_LEVEL + 1, index != wanted


def summon_ids(operator_value: dict) -> dict[str, int | None]:
    '''
    Returns the operator's summons: {deployable id: the number of the skill
    that deploys it, or None when it isn't tied to a skill (a talent, or
    any skill)}.

    Summons are listed in data.displayTokenDict and in each skill's
    overrideTokenKey (some are only in one of the two).
    '''
    data = operator_value.get('data') or operator_value
    summons: dict[str, int | None] = dict.fromkeys(data.get('displayTokenDict') or {})
    for number, skill in enumerate(data.get('skills') or [], start=1):
        token = skill.get('overrideTokenKey')
        if token and summons.get(token) is None:
            summons[token] = number
    return summons


def has_potential_buffs(data: dict) -> bool:
    '''
    Whether any potential rank changes stats (no summon's does, checked
    2026-11-04).
    '''
    return any(((rank.get('buff') or {}).get('attributes') or {}).get('attributeModifiers')
               for rank in data.get('potentialRanks') or [])


def has_trust_buffs(data: dict) -> bool:
    '''
    Whether trust gives any stat bonus (every summon's is 0 or missing,
    checked 2026-11-04).
    '''
    frames = data.get('favorKeyFrames') or []
    if not frames:
        return False
    return any(not isinstance(value, bool) and value for value in (frames[-1].get('data') or {}).values())


def build_operator_profile(
    operator_value: dict, phase: int, level: int, potential: int, trust: int, skill_level: int,
    masteries: Iterable[int] = (), operator_id: str | None = None,
) -> OperatorProfile:
    '''
    Builds an operator's profile.

    Args:
        operator_value: The operator's whole HellaAPI value (with "data"
            and "skills"), or a deployable's.
        phase: The operator's phase (elite level).
        level: The operator's level within that phase.
        potential: The operator's potential, from 0.
        trust: The operator's trust percentage.
        skill_level: The operator's skill level (1-7).
        masteries: The mastery of each skill (0-3), in skill order; missing
            ones are 0.
        operator_id: The operator's id (defaults to the value's "id").

    Returns:
        The OperatorProfile.

    Raises:
        ValueError: If the phase, level, potential, trust or skill level is
            out of range.
    '''
    data = operator_value['data']
    operator_id = operator_id or operator_value.get('id') or data.get('name', '')
    operator = OperatorStats(operator_id, level, phase, potential, trust, operator_data=data)
    masteries = list(masteries)
    traits = trait_values(data, phase, level, potential)
    sub_profession = data.get('subProfessionId')
    strikes = basic_attack_strikes(sub_profession, traits)

    # No skill: talents averaged over the default window. They're built twice: first to find the attack interval
    # (which their ASPD changes), then with it, for stacks gained per attack.
    # Buffs to a group the operator belongs to (its class, a faction...) apply to it too.
    member_of = member_groups(operator_value)
    talents = build_talents(data, phase, level, potential, operator_id, None, DEFAULT_STACK_WINDOW, member_of=member_of)
    talents = build_talents(data, phase, level, potential, operator_id,
                            apply_modifiers(operator.stats, _talent_modifiers(talents)).attack_interval,
                            DEFAULT_STACK_WINDOW, member_of=member_of)
    base_stats = apply_modifiers(operator.stats, _talent_modifiers(talents))
    basic_targets = basic_attack_targets(sub_profession, int(base_stats.block), traits)
    # The normal attack, used during skills; outside them, Phalanx Casters and Liberators don't attack.
    attack_parts = [DamagePart(operator.damage_type, 1.0, 1.0, basic_targets)]
    base_parts = [] if sub_profession in NO_BASIC_ATTACK_SUB_PROFESSIONS else attack_parts
    base_hits = attack_hits(base_stats, base_parts, *_talent_multipliers(talents), strikes)
    profile = OperatorProfile(
        operator_id=operator_id, name=operator.name, profession=operator.profession,
        sub_profession_id=sub_profession or '', damage_type=operator.damage_type, phase=phase, level=level,
        potential=potential, trust=trust, base_stats=base_stats, base_damage=hit_damage(base_hits),
        talents=talents, summons=list(summon_ids(operator_value)), member_of=member_of, base_hits=base_hits,
        penetration=_talent_penetration(talents), enemy_effects=_talent_enemy_effects(talents),
    )
    if any(STACK_WORDS.search(talent.description) for talent in talents):
        profile.assumed.append(f'stacking talents averaged over {DEFAULT_STACK_WINDOW:g} s with no skill')

    for number, entry in unlocked_skills(operator_value, phase, level):
        mastery = masteries[number - 1] if number - 1 < len(masteries) else 0
        fitted_level, fitted_mastery, changed = fit_skill_level(skill_level, mastery, len(entry['excel']['levels']))
        skill = parse_skill(entry, number, fitted_level, fitted_mastery, operator.damage_type, basic_targets)
        if changed:
            skill.assumed.append(f'skill level {skill_level} mastery {mastery} not available; '
                                 f'used level {fitted_level} mastery {fitted_mastery}')
        profile.skills.append(_skill_profile(skill, data, operator, profile, (base_parts, attack_parts), strikes))
    return profile


def _talent_modifiers(talents: Iterable[Talent]) -> StatModifiers:
    '''
    Returns the stat changes of the talents that are applied.
    '''
    total = StatModifiers()
    for talent in talents:
        if talent.applied:
            total = total + talent.modifiers
    return total


def _talent_multipliers(talents: Iterable[Talent]) -> tuple[float, float]:
    '''
    Returns (ATK multipliers, damage multipliers) of the talents that are
    applied, each multiplied together.
    '''
    atk_multiplier = damage_multiplier = 1.0
    for talent in talents:
        if talent.applied:
            atk_multiplier *= talent.atk_multiplier
            damage_multiplier *= talent.damage_multiplier
    return atk_multiplier, damage_multiplier


def _talent_penetration(talents: Iterable[Talent]) -> list[Penetration]:
    '''
    Returns the DEF/RES ignored by the talents that are applied.
    '''
    return [penetration for talent in talents if talent.applied for penetration in talent.penetration]


def _talent_enemy_effects(talents: Iterable[Talent]) -> list[BlackboardValue]:
    '''
    Returns the effects on enemies of the talents that are applied, without
    DEF/RES ignore (see _talent_penetration).
    '''
    return [value for talent in talents if talent.applied for value in talent.enemy_effects
            if value.key not in PENETRATION_KEYS]


def _skill_profile(skill: Skill, data: dict, operator: OperatorStats, profile: OperatorProfile,
                   parts: tuple[list[DamagePart], list[DamagePart]], strikes: tuple[float, ...]) -> SkillProfile:
    '''
    Works out one skill's stats, damage, burst and rotation.

    The talents are first averaged over the default window to find the
    rotation, then again over that rotation (its cycle time), and the
    skill is worked out with the second set. parts is (the attack with no
    skill, empty for archetypes that don't attack outside their skills;
    the normal attack used during skills), and strikes the archetype's
    (basic_attack_strikes).
    '''
    base_parts = parts[0]
    first = _skill_state(skill, operator, profile.talents, parts, strikes)
    window = first.rotation.cycle_time or 0.0
    if window <= 0:
        window = DEFAULT_STACK_WINDOW
    talents = build_talents(data, operator.phase, operator.level, operator.potential, profile.operator_id,
                            first.stats.attack_interval, window, member_of=profile.member_of)
    state = _skill_state(skill, operator, talents, parts, strikes)
    state.window = window

    base_stats = apply_modifiers(operator.stats, _talent_modifiers(talents))
    state.base_hits = attack_hits(base_stats, base_parts, *_talent_multipliers(talents), strikes)
    base_damage = hit_damage(state.base_hits)
    base_dps, base_total = damage_sum(base_damage)
    active_dps, active_total = damage_sum(state.damage)
    burst_dps, burst_total = damage_sum(state.burst)
    if state.once_per_battle:
        skill.assumed.append('can only be used once per battle, so left out of average and max DPS')
        return state
    state.average_dps = state.rotation.average_dps(base_dps, active_dps, burst_dps)
    state.average_dps_total = state.rotation.average_dps(base_total, active_total, burst_total)
    if operator.damage_type == DamageType.HEAL:
        state.average_hps = state.rotation.average_dps(healing_sum(base_damage)[0], state.hps,
                                                       healing_sum(state.burst)[0])
    if str(skill.sp_type) == SpType.WHEN_HIT and skill.trigger == SkillTrigger.SP:
        full_block = SkillRotation(skill.sp_type, skill.sp_cost, state.rotation.duration, state.rotation.attack_interval,
                                   skill.trigger, skill.replaces_attack, hit_interval(max(base_stats.block, 1)))
        state.full_block_average_dps = full_block.average_dps(base_dps, active_dps, burst_dps)
        state.full_block_average_dps_total = full_block.average_dps(base_total, active_total, burst_total)
    return state


def _skill_state(skill: Skill, operator: OperatorStats, talents: list[Talent],
                 parts: tuple[list[DamagePart], list[DamagePart]], strikes: tuple[float, ...]) -> SkillProfile:
    '''
    Returns a SkillProfile (without averages) for the skill with the given
    talents: its stats and damage (and hits) while active, its burst, and
    its rotation. parts and strikes are as in _skill_profile.
    '''
    talent_modifiers = _talent_modifiers(talents)
    multipliers = _talent_multipliers(talents)
    base_stats = apply_modifiers(operator.stats, talent_modifiers)
    stats = apply_modifiers(operator.stats, talent_modifiers + skill.modifiers)
    base_parts, attack_parts = parts
    skill_parts = _skill_parts(skill, operator.damage_type, attack_parts)

    burst: list[Damage] = []
    strike_hits: list[Hit] = []
    replaced_hits: list[Hit] = []
    if skill.instant:
        # The normal attack keeps going, and each activation adds a strike: the skill's own damage (an extra strike,
        # or an attack it replaces, counting only what it adds), or a boosted next attack. An instant skill with no
        # damage that doesn't change the next attack (e.g. "Blocks the next source of damage") adds nothing.
        # Archetype strikes (a Swordmaster's 2) are part of normal attacks only: a skill's own strike hits as often as
        # the skill says (Ch'en's Sheathed Strike hits once), while a boosted next attack is still a normal attack.
        hits = attack_hits(base_stats, base_parts, *multipliers, strikes)
        skill_strikes = (1.0,) if skill.damage_parts else strikes
        if skill.replaces_attack:
            strike_hits = attack_hits(stats, skill_parts, *multipliers, skill_strikes, per_strike=True)
            replaced_hits = attack_hits(base_stats, base_parts, *multipliers, strikes, per_strike=True)
            burst = subtract(hit_damage(strike_hits), hit_damage(replaced_hits))
        elif skill.damage_parts:
            strike_hits = attack_hits(stats, skill.damage_parts, *multipliers, skill_strikes, per_strike=True)
            burst = hit_damage(strike_hits)
    elif skill.stops_attacking:
        hits = attack_hits(stats, [part for part in skill.damage_parts if part.per_second], *multipliers, strikes)
    else:
        # A skill with only damage every second deals it on top of the normal attack.
        attacks = [part for part in skill_parts if not part.per_second] or attack_parts
        per_second = [part for part in skill_parts if part.per_second]
        hits = attack_hits(stats, attacks + per_second, *multipliers, strikes)
    damage = hit_damage(hits)

    duration = skill.duration
    if skill.ammo is not None:
        # Ammo skills last as long as their attacks take.
        duration = skill.ammo * stats.attack_interval
    rotation = SkillRotation(skill.sp_type, skill.sp_cost, duration, base_stats.attack_interval, skill.trigger,
                             skill.replaces_attack, hit_interval())
    return SkillProfile(skill=skill, stats=stats, damage=damage, burst=burst, rotation=rotation,
                        window=DEFAULT_STACK_WINDOW, talents=talents, hits=hits, strike_hits=strike_hits,
                        replaced_hits=replaced_hits, penetration=skill.penetration + _talent_penetration(talents),
                        enemy_effects=skill.enemy_effects + _talent_enemy_effects(talents))


def _skill_parts(skill: Skill, damage_type: str, attack_parts: list[DamagePart]) -> list[DamagePart]:
    '''
    Returns the damage parts of the skill's attacks: its own parts, a
    HEAL part for a healer's skill with a heal multiplier, or the basic
    attack's parts.
    '''
    if skill.damage_parts:
        return list(skill.damage_parts)
    if damage_type == DamageType.HEAL and skill.heal_scale is not None:
        return [DamagePart(DamageType.HEAL, skill.heal_scale, 1.0, attack_parts[0].targets)]
    return attack_parts


def build_summon_profile(
    deployable_value: dict, owner_phase: int, owner_level: int, owner_potential: int, owner_trust: int,
    skill_level: int, masteries: Iterable[int] = (), deployable_id: str | None = None, owner_id: str | None = None,
) -> OperatorProfile:
    '''
    Builds a summon's profile at its owner's phase and level.

    The summon gets its owner's potential and trust only if its own data
    lists stat bonuses for them (none do, checked 2026-11-04); otherwise it
    uses 0, recorded as an assumption. A summon's skills line up with its
    owner's by slot (Skadi's Seaborn has one skill for each of Skadi's;
    Nearl the Radiant Knight's sword only has skill 3, for her S3), so its
    skill N uses the owner's skill level and skill N mastery.

    Args:
        deployable_value: The summon's whole HellaAPI value.
        owner_phase: The owner's phase.
        owner_level: The owner's level within that phase.
        owner_potential: The owner's potential.
        owner_trust: The owner's trust.
        skill_level: The owner's skill level.
        masteries: The owner's mastery of each skill, in skill order.
        deployable_id: The summon's id (defaults to the value's "id").
        owner_id: The owner's id.

    Returns:
        The summon's OperatorProfile, with owner_id set.
    '''
    data = deployable_value['data']
    assumed = []
    phase = min(owner_phase, len(data['phases']) - 1)
    level = min(owner_level, data['phases'][phase]['maxLevel'])
    if (phase, level) != (owner_phase, owner_level):
        assumed.append(f"owner's phase {owner_phase} level {owner_level} not available; used phase {phase} level {level}")
    potential = min(owner_potential, len(data.get('potentialRanks') or [])) if has_potential_buffs(data) else 0
    trust = owner_trust if has_trust_buffs(data) else 0
    if not has_potential_buffs(data) and owner_potential:
        assumed.append("no potential bonuses listed, so the owner's potential isn't used")
    if not has_trust_buffs(data) and owner_trust:
        assumed.append("no trust bonuses listed, so the owner's trust isn't used")

    profile = build_operator_profile(deployable_value, phase, level, potential, trust, skill_level, masteries,
                                     deployable_id)
    profile.owner_id = owner_id
    if profile.skills:
        assumed.append("its skills are counted whenever they'd be active on their own; in game, skill N is only used "
                       "while the owner's skill N is")
    profile.assumed[:0] = assumed
    return profile


def build_summon_profiles(
    operator_value: dict, deployables: Mapping[str, dict], phase: int, level: int, potential: int, trust: int,
    skill_level: int, masteries: Iterable[int] = (), operator_id: str | None = None,
) -> list[OperatorProfile]:
    '''
    Builds the profiles of every summon an operator has. A summon missing
    from deployables is logged and skipped.

    Args:
        operator_value: The owner's whole HellaAPI value.
        deployables: {deployable id: its whole HellaAPI value}.
        phase, level, potential, trust, skill_level: The owner's.
        masteries: The owner's mastery of each skill, in skill order.
        operator_id: The owner's id (defaults to the value's "id").

    Returns:
        One OperatorProfile per summon found, in summon_ids order.
    '''
    owner_id = operator_id or operator_value.get('id')
    masteries = list(masteries)
    profiles = []
    for summon_id in summon_ids(operator_value):
        if summon_id not in deployables:
            logger.warning('Summon %s of %s is not in the HellaAPI data; skipped', summon_id, owner_id)
            continue
        profiles.append(build_summon_profile(deployables[summon_id], phase, level, potential, trust, skill_level,
                                             masteries, summon_id, owner_id))
    return profiles
