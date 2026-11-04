#File Documentation
'''
File: skills.py
Description: Turns an operator's HellaAPI skill data into Skill objects:
    which skills are unlocked, the right skill level and mastery, the stat
    changes and damage each skill applies, and how fully the skill could
    be modelled.
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-04
Last Modified by: Claude
'''

# Imports

import re
from dataclasses import dataclass, field, fields
from enum import StrEnum

from arkdata.calculations.attack_targets import Targets, skill_targets
from arkdata.calculations.blackboard import (
    PLACEHOLDER,
    BlackboardValue,
    Target,
    read_blackboard,
    render_description,
    strip_tags,
)
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_stats import parse_phase
from arkdata.calculations.skill_rotation import SpType

# Constants

MAX_SKILL_LEVEL = 7
MAX_MASTERY = 3

# Stat values applied to the unit itself, mapped to the StatModifiers fields for (a percentage, a flat amount).
# Whether a value is a percentage comes from the description template ({atk:0%} or {def}).
STAT_KEYS = {
    "atk": ("atk_percent", "atk_flat"),
    "def": ("def_percent", "def_flat"),
    "max_hp": ("hp_percent", "hp_flat"),
    "magic_resistance": ("res_percent", "res_flat"),
}
# For STAT_KEYS values not shown in the description, values up to this size are assumed to be percentages
# (atk 1.4 = +140%) and larger ones flat amounts (def 75). RES uses 1, since flat RES is usually 10 or more.
PERCENT_LIMIT = {"atk": 3.0, "def": 3.0, "max_hp": 3.0, "magic_resistance": 1.0}
# Attack values applied to the unit itself, mapped to their StatModifiers field: flat ASPD, seconds added to
# the attack interval, and extra block.
ATTACK_KEYS = {"attack_speed": "attack_speed", "base_attack_time": "attack_interval_change", "block_cnt": "block"}
# Values that describe the skill's damage, healing, ammo or charges. They're read separately below.
DAMAGE_KEYS = {"atk_scale", "times", "max_target", "heal_scale", "trigger_time", "ct"}
# Values that are understood but don't change DPS or defensive stats, recorded in the notes.
NOTE_KEYS = {
    "stun": "stuns for {value:g} s",
    "sluggish": "slows for {value:g} s",
    "cost": "DP {value:+g}",
    "force": "shifts enemies (force {value:g})",
    "taunt_level": "taunt level {value:+g}",
    "move_speed": "movement speed {value:+g}",
    "duration": "effect lasts {value:g} s",
    "hp_recovery_per_sec": "regenerates {value:g} HP per second",
    "hp_recovery_per_sec_by_max_hp_ratio": "regenerates {value:.1%} of max HP per second",
    "ability_range_forward_extend": "range extends by {value:g} tiles",
    "projectile_range": "projectile range {value:g}",
    "projectile_delay_time": "projectile delay {value:g} s",
    "projectile_life_time": "lingering effect lasts {value:g} s",
    "silence": "silences for {value:g} s",
    "range_id": "changes attack range",
}

TYPED_DAMAGE = re.compile(r'\b(Physical|Arts|True) damage', re.IGNORECASE)
# The whole attack changes damage type, e.g. Amiya's Chimera: "damage type changes to True".
DAMAGE_TYPE_CHANGE = re.compile(
    r'damage type (?:changes|is changed|becomes|switches) to (Physical|Arts|True)'
    r'|(?:normal )?attacks (?:now |instead )?deal (Physical|Arts|True) damage',
    re.IGNORECASE,
)
# A multiplier only dealt to enemies that attack the unit (e.g. Hoshiguma's Thorns), not on its own attacks.
COUNTER_DAMAGE = re.compile(r'that attacks?\b|when (?:attacked|hit|taking damage)|attackers?\b', re.IGNORECASE)
NEXT_ATTACK = re.compile(r'\bnext attack', re.IGNORECASE)
# Skills that stay on once activated: "Unlimited duration", or a toggle ("Can switch between the default state and
# the following state"). Other skills with a negative duration are one-off effects ("Immediately launches...").
STAYS_ON = re.compile(r'unlimited duration|indefinite|switch between', re.IGNORECASE)
# Damage dealt on a timer rather than with each attack (e.g. Ifrit's Scorched Earth, "every second").
PER_SECOND = re.compile(r'every second|per second|each second', re.IGNORECASE)
STOPS_ATTACKING = re.compile(r'stops? attacking|(?:cannot|can not|can\'t|does not|unable to) attack', re.IGNORECASE)
DAMAGE_TYPE_NAMES = {"physical": DamageType.PHYSICAL, "arts": DamageType.ARTS, "true": DamageType.TRUE}


class SkillType(StrEnum):
    '''
    How a skill is activated: by the player, automatically once charged,
    or always active.
    '''
    MANUAL = "MANUAL"
    AUTO = "AUTO"
    PASSIVE = "PASSIVE"


class SkillStatus(StrEnum):
    '''
    How fully a skill could be modelled: everything understood, some of it,
    or none of it.
    '''
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


@dataclass
class StatModifiers:
    '''
    Changes to a unit's stats. Percentages are fractions (0.7 = +70%) and
    add together; flat amounts are added to the stat before percentages
    are applied. attack_speed is flat ASPD (+60), attack_interval_change
    is seconds added to the base attack time (negative is faster).
    '''
    atk_percent: float = 0.0
    atk_flat: float = 0.0
    def_percent: float = 0.0
    def_flat: float = 0.0
    hp_percent: float = 0.0
    hp_flat: float = 0.0
    res_percent: float = 0.0
    res_flat: float = 0.0
    attack_speed: float = 0.0
    attack_interval_change: float = 0.0
    block: float = 0.0

    def __add__(self, other: 'StatModifiers') -> 'StatModifiers':
        '''
        Returns the combined changes of two StatModifiers (for stacking a
        skill's changes on top of talents').
        '''
        return StatModifiers(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    def is_empty(self) -> bool:
        '''
        Whether no stat is changed.
        '''
        return all(getattr(self, f.name) == 0 for f in fields(self))


@dataclass(frozen=True)
class DamagePart:
    '''
    One part of an attack's damage: its type, the ATK multiplier per hit,
    the number of hits on each enemy, and the enemies hit. per_second is
    True for damage dealt once per second instead of with each attack.
    '''
    damage_type: str
    atk_scale: float = 1.0
    hits: float = 1.0
    targets: Targets = field(default_factory=Targets)
    per_second: bool = False


@dataclass
class Skill:
    '''
    One of an operator's skills at a given skill level and mastery.

    While the skill is active, modifiers apply to the unit's own stats and
    its attacks deal damage_parts (each attack, or once for an instant
    skill). duration is in seconds: 0 for instant skills, None for skills
    that stay on once activated. Ammo skills keep a duration of 0 and set
    ammo instead, since how long the ammo lasts depends on the attack
    interval. enemy_effects are values that change
    enemies (e.g. lowering their DEF), kept for the matchup calculations.
    unmodelled lists value names the model doesn't understand; status is
    FULL only when there are none and nothing had to be assumed.
    '''
    skill_id: str
    number: int
    name: str
    level: int
    mastery: int
    skill_type: str
    sp_type: str
    sp_cost: float
    initial_sp: float
    duration: float | None
    description: str
    instant: bool = False
    replaces_attack: bool = False
    ammo: int | None = None
    charges: int | None = None
    modifiers: StatModifiers = field(default_factory=StatModifiers)
    damage_parts: list[DamagePart] = field(default_factory=list)
    damage_type: str | None = None
    heal_scale: float | None = None
    stops_attacking: bool = False
    enemy_effects: list[BlackboardValue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)

    @property
    def passive(self) -> bool:
        '''
        Whether the skill is always active.
        '''
        return self.skill_type == SkillType.PASSIVE or str(self.sp_type) == SpType.PASSIVE

    @property
    def status(self) -> SkillStatus:
        '''
        FULL when every value was understood, NONE when the skill does
        something but nothing could be modelled, and PARTIAL otherwise.
        '''
        modelled = (not self.modifiers.is_empty() or self.damage_parts or self.damage_type is not None
                    or self.heal_scale is not None or self.stops_attacking or self.ammo is not None
                    or self.enemy_effects)
        if not modelled:
            # A skill whose only effects are notes (e.g. a stun) is fully understood; one with no values at
            # all, or only values the model doesn't know, can't be modelled.
            return SkillStatus.FULL if self.notes and not self.unmodelled else SkillStatus.NONE
        if self.unmodelled or self.assumed:
            return SkillStatus.PARTIAL
        return SkillStatus.FULL


def level_index(skill_level: int, mastery: int, level_count: int) -> int:
    '''
    Returns the index into a skill's levels list for a skill level and
    mastery: levels 1-7 are indexes 0-6, and masteries 1-3 follow them.

    Args:
        skill_level: The operator's skill level, 1 to 7.
        mastery: The skill's mastery, 0 to 3 (only at skill level 7).
        level_count: How many levels the skill has (10, or 7 for skills
            that can't be mastered).

    Returns:
        The index.

    Raises:
        ValueError: If the skill level or mastery is out of range, or the
            skill has no such level.
    '''
    if not 1 <= skill_level <= MAX_SKILL_LEVEL:
        raise ValueError(f"Skill level must be 1-{MAX_SKILL_LEVEL}, got {skill_level}")
    if not 0 <= mastery <= MAX_MASTERY:
        raise ValueError(f"Mastery must be 0-{MAX_MASTERY}, got {mastery}")
    if mastery and skill_level != MAX_SKILL_LEVEL:
        raise ValueError(
            f"Skills can only be mastered at skill level {MAX_SKILL_LEVEL}, got level {skill_level} mastery {mastery}"
        )
    index = skill_level - 1 + mastery
    if index >= level_count:
        raise ValueError(f"This skill has {level_count} levels, so skill level {skill_level} mastery {mastery} doesn't exist")
    return index


def unlocked_skills(operator_value: dict, phase: int, level: int) -> list[tuple[int, dict]]:
    '''
    Returns the skills an operator has unlocked at its phase and level.

    Unlock conditions are in data.skills[i].unlockCond, while each skill's
    levels are in skills[i] beside data; the two are matched by skill id.

    Args:
        operator_value: The operator's whole HellaAPI value (with "data"
            and "skills").
        phase: The operator's phase (elite level).
        level: The operator's level within that phase.

    Returns:
        (skill number from 1, skill entry with "excel") for each unlocked
        skill, in order. Skills without level data are left out.
    '''
    entries = {(entry.get('excel') or {}).get('skillId'): entry for entry in operator_value.get('skills') or []}
    unlocked = []
    for number, skill in enumerate(operator_value['data'].get('skills') or [], start=1):
        condition = skill.get('unlockCond') or {}
        required = (parse_phase(condition.get('phase', 0)), condition.get('level', 1))
        entry = entries.get(skill.get('skillId'))
        if (phase, level) >= required and entry and entry['excel'].get('levels'):
            unlocked.append((number, entry))
    return unlocked


def _percent(value: BlackboardValue) -> tuple[bool, bool]:
    '''
    Returns (is the value a percentage, was that assumed).

    Args:
        value: A STAT_KEYS value.

    Returns:
        From the description when the value is shown there; otherwise
        guessed from its size (PERCENT_LIMIT), and marked as assumed.
    '''
    if value.is_percent is not None:
        return value.is_percent, False
    return abs(value.value) <= PERCENT_LIMIT[value.key], True


def _damage_parts(text: str, values: dict[str, BlackboardValue], damage_type: str, targets: Targets) -> list[DamagePart]:
    '''
    Returns the damage parts of a skill that has an ATK multiplier.

    Each time the multiplier appears in the text it's one part, typed by
    the damage named after it ("410% of ATK Physical damage and 410% of
    ATK Arts damage" is two parts); otherwise the operator's damage type
    is used.

    Args:
        text: The description without formatting tags.
        values: The skill's values (must include atk_scale).
        damage_type: The operator's (or the skill's) damage type.
        targets: The enemies each part hits.

    Returns:
        The damage parts.
    '''
    scale = values['atk_scale']
    hits = values['times'].value if 'times' in values else 1.0
    placeholders = list(PLACEHOLDER.finditer(text))
    parts = []
    for i, match in enumerate(placeholders):
        if match.group(2).lower() != scale.raw_key.lower():
            continue
        # The damage type named between this multiplier and the next value or the end of the clause.
        end = placeholders[i + 1].start() if i + 1 < len(placeholders) else len(text)
        segment = re.split(r'[;\n]', text[match.end():end])[0]
        typed = TYPED_DAMAGE.search(segment)
        part_type = DAMAGE_TYPE_NAMES[typed.group(1).lower()] if typed else damage_type
        parts.append(DamagePart(part_type, scale.value, hits, targets, per_second=bool(PER_SECOND.search(segment))))
    return parts or [DamagePart(damage_type, scale.value, hits, targets)]


def parse_skill(
    skill_entry: dict, number: int, skill_level: int, mastery: int, damage_type: str, basic_targets: Targets,
) -> Skill:
    '''
    Builds a Skill from one skill's HellaAPI data at a skill level and mastery.

    Args:
        skill_entry: One entry of the operator's value["skills"] (with "excel").
        number: The skill's number (1-3).
        skill_level: The operator's skill level (1-7).
        mastery: This skill's mastery (0-3).
        damage_type: The operator's basic attack damage type.
        basic_targets: The enemies the operator's basic attack hits.

    Returns:
        The Skill.

    Raises:
        ValueError: If the skill level or mastery doesn't exist for this skill.
    '''
    excel = skill_entry['excel']
    levels = excel['levels']
    data = levels[level_index(skill_level, mastery, len(levels))]
    sp_data = data.get('spData') or {}
    text = strip_tags(data.get('description'))
    values = read_blackboard(data)

    skill = Skill(
        skill_id=excel.get('skillId', ''), number=number, name=data.get('name', ''), level=skill_level, mastery=mastery,
        skill_type=data.get('skillType', ''), sp_type=str(sp_data.get('spType', '')), sp_cost=sp_data.get('spCost', 0),
        initial_sp=sp_data.get('initSp', 0), duration=data.get('duration', 0), description=render_description(data),
    )
    is_ammo = data.get('durationType') == 'AMMO'
    # "the next attack" makes the skill instant even when its duration says otherwise (e.g. Ifrit's Pyroclasm,
    # which stores charges and is marked as never ending).
    skill.replaces_attack = bool(NEXT_ATTACK.search(text))
    stays_on = skill.passive or bool(STAYS_ON.search(text))
    if is_ammo:
        skill.duration = 0
    elif stays_on:
        skill.duration = None
    elif skill.replaces_attack or (skill.duration or 0) <= 0:
        # A one-off effect: "the next attack", or a duration of 0 or less without "Unlimited duration".
        skill.instant = True
        skill.duration = 0

    for value in values.values():
        if value.key in STAT_KEYS:
            _apply_stat(skill, value)
        elif value.key in ATTACK_KEYS:
            _apply_attack_value(skill, value)
        elif value.key in NOTE_KEYS:
            skill.notes.append(NOTE_KEYS[value.key].format(value=value.value))
        elif value.key not in DAMAGE_KEYS:
            skill.unmodelled.append(value.raw_key)

    if is_ammo:
        if 'trigger_time' in values:
            skill.ammo = int(values['trigger_time'].value)
        else:
            skill.unmodelled.append('ammo count')
    if 'ct' in values:
        skill.charges = int(values['ct'].value)
        skill.notes.append(f'stores up to {skill.charges} charges')
    if 'heal_scale' in values:
        skill.heal_scale = values['heal_scale'].value

    changed_type = DAMAGE_TYPE_CHANGE.search(text)
    if changed_type:
        skill.damage_type = DAMAGE_TYPE_NAMES[(changed_type.group(1) or changed_type.group(2)).lower()]
    skill.stops_attacking = bool(STOPS_ATTACKING.search(text))

    max_target = values['max_target'].value if 'max_target' in values else None
    attack_type = skill.damage_type or damage_type
    if 'atk_scale' in values:
        # AoE wording only counts in the multiplier's own clause: Toddifons' arrows hit one enemy, and only
        # their later explosion ("to the target and nearby enemies") is AoE.
        clause = _clause_of(text, values['atk_scale'].raw_key) or text
        targets = skill_targets(max_target, clause, basic_targets)
        if COUNTER_DAMAGE.search(clause):
            skill.unmodelled.append(f'{values["atk_scale"].raw_key} (counter damage)')
        else:
            skill.damage_parts = _damage_parts(text, values, attack_type, targets)
        return skill
    targets = skill_targets(max_target, text, basic_targets)
    if 'times' in values or 'max_target' in values or skill.damage_type is not None or targets.aoe:
        # No multiplier, but the attacks change: more hits, more targets, or a new damage type.
        hits = values['times'].value if 'times' in values else 1.0
        skill.damage_parts = [DamagePart(attack_type, 1.0, hits, targets)]
    return skill


def _clause_of(text: str, raw_key: str) -> str:
    '''
    Returns the clause of text containing the first placeholder for raw_key.

    Args:
        text: The description without formatting tags.
        raw_key: The value's name as written in the template.

    Returns:
        The clause, or "" if the value isn't in the text.
    '''
    for clause in re.split(r'[;\n]|\.\s', text):
        if any(match.group(2).lower() == raw_key.lower() for match in PLACEHOLDER.finditer(clause)):
            return clause
    return ''


def _apply_stat(skill: Skill, value: BlackboardValue) -> None:
    '''
    Adds a STAT_KEYS value to the skill's own modifiers, or records it as an
    enemy effect or an ally buff.

    Args:
        skill: The skill being built (changed in place).
        value: An atk, def, max_hp or magic_resistance value.
    '''
    if value.target == Target.ENEMY:
        skill.enemy_effects.append(value)
        return
    if value.target == Target.ALLY:
        skill.notes.append(f'buffs allies: {value.raw_key} {value.value:+g}')
        return
    is_percent, assumed = _percent(value)
    if assumed:
        skill.assumed.append(f'{value.raw_key} {value.value:+g} assumed to be {"a percentage" if is_percent else "flat"}')
    percent_field, flat_field = STAT_KEYS[value.key]
    name = percent_field if is_percent else flat_field
    setattr(skill.modifiers, name, getattr(skill.modifiers, name) + value.value)


def _apply_attack_value(skill: Skill, value: BlackboardValue) -> None:
    '''
    Adds an ATTACK_KEYS value (ASPD, attack interval, block) to the skill's
    own modifiers, or records it as an enemy effect or an ally buff.

    These values are almost always the unit's own, so values not shown in
    the description are applied too.

    Args:
        skill: The skill being built (changed in place).
        value: An attack_speed, base_attack_time or block_cnt value.
    '''
    if value.target == Target.ENEMY:
        skill.enemy_effects.append(value)
        return
    if value.target == Target.ALLY:
        skill.notes.append(f'buffs allies: {value.raw_key} {value.value:+g}')
        return
    name = ATTACK_KEYS[value.key]
    setattr(skill.modifiers, name, getattr(skill.modifiers, name) + value.value)
