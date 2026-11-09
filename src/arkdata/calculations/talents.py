#File Documentation
'''
File: talents.py
Description: Turns an operator's HellaAPI talent data into Talent objects:
    which version of each talent is unlocked, what condition it needs (and
    so whether it's applied), the stat changes and damage multiplier it
    gives, and how fully it could be modelled.
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import csv
import io
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from importlib import resources

from arkdata.calculations.blackboard import (
    ALLY_WORDS,
    CLAUSE_BREAK,
    ENEMY_WORDS,
    BlackboardValue,
    Target,
    strip_tags,
    value_name,
)
from arkdata.calculations.operator_stats import parse_phase
from arkdata.calculations.skills import (
    ATTACK_KEYS,
    NOTE_KEYS,
    PENETRATION_KEYS,
    STAT_KEYS,
    Penetration,
    StatEffects,
    StatModifiers,
    apply_stat_value,
    is_stat_value,
)

# Constants

# The editable file (in src/arkdata/data) that overrides the automatic group (and factor) of a talent.
TALENT_OVERRIDES_PACKAGE = 'arkdata.data'
TALENT_OVERRIDES_FILE = 'talent_conditions.csv'
TALENT_OVERRIDE_COLUMNS = ['operator_id', 'talent', 'group', 'factor', 'note']

# Stacking effects whose stack rate isn't known (e.g. per kill) assume half their maximum stacks.
UNKNOWN_STACK_FRACTION = 0.5

# Condition wording, checked in this order on each clause that holds one of the talent's effects.
# Conditions on the enemy: applied in matchups when the enemy fits, not in the general figures.
PER_ENEMY_CONDITIONS = {
    # Enemy drones only: "her Drones" are an operator's own summons.
    'aerial': re.compile(r'\baerial\b|\bflying\b|attacking drones|enemy drones', re.IGNORECASE),
    'unblocked': re.compile(r'\bunblocked\b', re.IGNORECASE),
    'blocked': re.compile(r'\bblocked\b|\bblocking\b', re.IGNORECASE),
    'elite': re.compile(r'\belite\b|\bleader\b|\bboss\b', re.IGNORECASE),
    # "attacking enemies with 80% or more HP", "hitting an enemy with full HP".
    'enemy HP': re.compile(r'enem(?:y|ies) with [^.;,]*\bhp\b|with full hp', re.IGNORECASE),
    'weight': re.compile(r'\bweight\b', re.IGNORECASE),
    # "more damage to Slowed or Feared enemies", "Stunned targets".
    'enemy status': re.compile(r'\bto (?:slowed|stunned|frozen|feared|bound)\b'
                               r'|\b(?:slowed|stunned|frozen|feared|bound) (?:or \w+ )?(?:enem|target)', re.IGNORECASE),
    # "[Infected Creatures] enemies", "when attacking [Infected Creatures]", "when attacking Sarkaz enemies",
    # "more damage to Machine enemies" (but not "to all/nearby/surrounding... enemies").
    'enemy type': re.compile(
        r'\[[^\]]+\] enem|attacking \[[^\]]+\]'
        r'|(?:to|against|attacking) (?!(?:the|all|nearby|surrounding|multiple|random|other|more|up|adjacent|those|these'
        r'|target|its|their|that|this|each|every|any)\b)[a-z]+ enemies\b',
        re.IGNORECASE,
    ),
}
# Details of a PER_ENEMY condition, for matchups (see parse_condition).
# "not blocking any enemies", "enemies that are not blocked by this unit", "enemies that are not Elites or Leaders".
NOT_WORDS = re.compile(r"\bnot (?:\w+ )?(?:block|elite)", re.IGNORECASE)
# "when two or more enemies are blocked", "when blocking 2 or more enemies".
COUNT_OR_MORE = re.compile(r"\b(\d+|two|three|four|five) or more\b", re.IGNORECASE)
NUMBER_WORDS = {'two': 2, 'three': 3, 'four': 4, 'five': 5}
# "enemies with 3 weight or higher", "weight greater than or equal to 3"; "enemies with 3 or less weight".
WEIGHT_AT_LEAST = re.compile(r"(\d+) weight or (?:higher|more|above|greater)"
                             r"|weight (?:greater than or equal to|of at least|at least) (\d+)", re.IGNORECASE)
WEIGHT_AT_MOST = re.compile(r"(\d+) or (?:less|lower|fewer) weight"
                            r"|weight (?:less than or equal to|of at most|at most) (\d+)", re.IGNORECASE)
# "enemies with less than 60% (+10%) HP"; "enemies with 80% or more HP"; "an enemy with full HP".
HP_BELOW = re.compile(r"(?:less than|below|under|lower than) (\d+(?:\.\d+)?)%(?: \([^)]*\))? (?:of (?:its |their )?)?(?:max )?HP",
                      re.IGNORECASE)
HP_AT_LEAST = re.compile(r"(\d+(?:\.\d+)?)%(?: \([^)]*\))? or (?:more|higher|above) (?:of (?:its |their )?)?(?:max )?HP",
                         re.IGNORECASE)
FULL_HP = re.compile(r"\bfull HP\b", re.IGNORECASE)
# The enemy type a condition names: "[Infected Creatures]", "[Sarkaz] enemies", "Sarkaz enemies", "ranged enemies".
ENEMY_TYPE_NAME = re.compile(r"\[([^\]]+)\](?= enem)|attacking \[([^\]]+)\]|\b(\w+) enemies\b", re.IGNORECASE)
# Enemy type names as written, mapped to the HellaAPI enemyTags (enemyData.enemyTags), or to "ranged" (applyWay
# RANGED) and "ground" (motion WALK). Names not listed (e.g. "sleeping", "stationary") are kept as written.
ENEMY_TYPE_TAGS = {'infected creature': 'infection', 'sarkaz': 'sarkaz', 'machine': 'machine', 'sea monster': 'seamonster',
                   'drone': 'drone', 'ranged': 'ranged', 'ground': 'ground'}
ENEMY_STATUS_NAME = re.compile(r"\b(slowed|stunned|frozen|feared|bound)\b", re.IGNORECASE)

# Buffs to a group of operators that the operator itself belongs to ("All Vanguard Operators' ATK +10%" for Siege,
# "all [Abyssal Hunter] Operators" for Skadi, "All allies' ASPD +8", "This unit and allied Operators..."): the group is
# the words before one of these nouns, in the value's part of the clause.
GROUP_NOUN = re.compile(r"\b(?:operators?|allies|(?:allied )?units|members)\b'?", re.IGNORECASE)
# Buffs to everyone ("All allies", "all allied units", "all Operators").
EVERYONE = re.compile(r"\ball (?:allied |friendly )?$", re.IGNORECASE)
INCLUDES_THIS_UNIT = re.compile(r'\bthis unit and\b', re.IGNORECASE)
# Group buffs limited by position ("allies within range", "Melee allies in the 8 surrounding tiles") or by a condition
# ("Operators with ammo-type skills"): whether the operator itself counts varies, so they stay ally buffs.
GROUP_LIMIT = re.compile(r"^\W*(?:within|in the|in \d|surrounding|adjacent|around|with|that|who)\b", re.IGNORECASE)
# Range wording removed (with the group) from a "This unit and..." buff's clause before its condition is judged.
RANGE_TEXT = re.compile(r'\b(?:in|within) (?:the )?(?:\d+ )?(?:surrounding |adjacent )?(?:\d+ )?(?:tiles?|attack range|range)'
                        r'(?: around (?:her|him|it|them))?', re.IGNORECASE)
# Where the group's own words start, for notes ("Restores the HP of all allies" -> "all allies").
GROUP_START = re.compile(r"\b(?:this unit and|all|allied|friendly|deployed)\b.*$|[\['].*$", re.IGNORECASE)
# Always true for the operator itself, so removed before a shared buff's condition is judged.
SELF_PRESENT = re.compile(r'when this unit is in the squad,?', re.IGNORECASE)
# Class names as written in talents, by the HellaAPI profession.
CLASS_NAMES = {'PIONEER': 'vanguard', 'WARRIOR': 'guard', 'TANK': 'defender', 'SNIPER': 'sniper', 'CASTER': 'caster',
               'MEDIC': 'medic', 'SUPPORT': 'supporter', 'SPECIAL': 'specialist'}
RARITY_NAMES = {'TIER_1': 'one-star', 'TIER_2': 'two-star', 'TIER_3': 'three-star', 'TIER_4': 'four-star',
                'TIER_5': 'five-star', 'TIER_6': 'six-star'}
# Faction names written differently in talents than in the faction data ("'Minoan' Operators" for Minos).
FACTION_ALIASES = {'minoan': 'minos'}
# Words that aren't group names, ignored when matching the words before a group noun.
GROUP_FILLER = {'all', 'allied', 'friendly', 'the', 'of', 'and', 'deployed', 'this', 'unit'}

# Effects picked at random ("randomly trigger one of the following effects") can't be told apart: flagged for review.
RANDOM_WORDS = re.compile(r'\brandom', re.IGNORECASE)
# Conditions that depend on other operators, the map or the team: not applied.
UNLIKELY_WORDS = re.compile(
    r"\ball(?:y|ies)\b|\ballied\b|\bfriendly\b|other operators|\boperators\b|\bsquad\b|\bterrain\b|\btiles?\b|\bteam\b"
    r"|when there (?:is|are)|at least one more",
    re.IGNORECASE,
)
# Conditions the operator meets by itself in most fights: applied.
LIKELY_WORDS = re.compile(
    r"after (?:being )?deploy|when deployed|after each deployment|on the field|seconds? after"
    r"|while (?:the |a |this unit's )?skill|during (?:the )?(?:duration|skill)|skill is activ(?:e|ated)"
    r"|\bhp\b[^.;]*?\b(?:above|below|beneath|under|less than|more than)\b"
    r"|when attacking|when dealing damage|when hitting|\battacks? (?:have|has|inflict)\b"
    r"|(?:each|every|per) (?:\d+ )?(?:attacks?|hits?)\b|when this unit attacks"
    r"|\bis deployed|remain(?:s|ing)? deployed|every \d+(?:\.\d+)? seconds?"
    r"|defeat(?:ing|s)? an enemy|\bkill|when inflicting|on the battlefield|in [^.;]*attack range for",
    re.IGNORECASE,
)
# Any of these words means there's a condition; without them a talent is always active.
CONDITION_WORDS = re.compile(
    r'\bwhen\b|\bwhile\b|\bafter\b|\bif\b|\bupon\b|\bbefore\b|\bevery\b|\beach\b|\bduring\b|\bfor \d|\bchance\b|\bstack',
    re.IGNORECASE,
)
# Who an effect applies to, beyond the skill wording in blackboard.py: inflicting something targets enemies, and
# "[Abyssal Hunter] Operators gain..." targets allies.
TALENT_ENEMY_WORDS = re.compile(r'\binflict|\bfragility\b', re.IGNORECASE)
TALENT_ALLY_WORDS = re.compile(r'\boperators\b|\bmembers\b', re.IGNORECASE)
# A value followed by Fragile/Fragility in its part of the clause ("ATK -22%, 32% [Arts Fragility]") is on enemies.
FRAGILE_AFTER = re.compile(r'^[^,]*?\bfragil', re.IGNORECASE)
# A damage multiplier for splash damage only ("damage caused by Trait splash effect increases by 24%").
SPLASH_WORDS = re.compile(r'\bsplash', re.IGNORECASE)
# A boosted attack that also strikes again ("increase ATK to 130% and strike an additional target"): the extra strike
# deals the same boosted damage, to the same enemy unless the first defeats it (Bagpipe's talent 1).
EXTRA_STRIKE = re.compile(r'\b(?:strikes?|attacks?|hits?) (?:an? |one )?(?:additional|extra|another) (?:target|time)',
                          re.IGNORECASE)

DODGE_WORDS = re.compile(r'\bdodge\b', re.IGNORECASE)
CHANCE_WORDS = re.compile(r'\bchance\b', re.IGNORECASE)
# Chance values: "prob", or numbered ones ("prob1", "prob2") when a talent has several; the first one is used.
CHANCE_KEY = re.compile(r'prob\d*')
STACK_WORDS = re.compile(r'\bstack', re.IGNORECASE)
MAX_STACKS_TEXT = re.compile(r'up to (\d+) (?:times|stacks)', re.IGNORECASE)
EVERY_N_ATTACKS = re.compile(r'every (\d+) attacks', re.IGNORECASE)
PER_ATTACK = re.compile(r'(?:each|every|per) (?:attack|hit)\b', re.IGNORECASE)
EVERY_N_SECONDS = re.compile(r'every (\d+(?:\.\d+)?) seconds?', re.IGNORECASE)
PER_SECOND = re.compile(r'(?:each|every|per) second', re.IGNORECASE)
# An atk_scale in a talent only multiplies attacks when its clause is about attacking (not "When deploying, deals...").
ATTACK_WORDS = re.compile(r'\battack|\bhit', re.IGNORECASE)
DEPLOY_WORDS = re.compile(r'\bdeploy', re.IGNORECASE)

# Talent values that multiply the operator's damage.
DAMAGE_KEYS = {'damage_scale', 'atk_scale'}
# Whether a multiplier raises ATK (before DEF/RES) or the damage dealt (after), from the words next to its number,
# whatever the value's name: "Increases ATK to 140%", "ATK +43%", "deals 133% ATK as Physical damage" and "160% of
# ATK" raise ATK; "20% more damage", "attacks deal 117% damage" and "increase the current damage of that attack to
# 160%" raise damage. The words after the number are checked first; before it, the nearest "ATK" or "damage" up to
# five words back, ending in "to", "by" or "+" ("increase the ATK of the current attack to 190%", "Damage when skill
# is active increases by 16%").
ATK_AFTER = re.compile(r"^\s*(?:\([^)]*\)\s*)?(?:of\s+(?:[\w'’.-]+\s+){0,2}?)?ATK\b", re.IGNORECASE)
DAMAGE_AFTER = re.compile(r"^\s*(?:\([^)]*\)\s*)?(?:(?:more|increased|additional|extra|bonus)\s+)?"
                          r"(?:(?:Physical|Arts|True)\s+)?damage\b", re.IGNORECASE)
MULTIPLIER_BEFORE = re.compile(r"\b(ATK|damage)\b(?:\s+(?!(?:ATK|damage)\b)[\w'’]+){0,5}?\s*(?:to|by|\+)\s*$",
                               re.IGNORECASE)
# The number itself, at the start of the words after it.
LEADING_NUMBER = re.compile(r'^[\d.]+%?')
ATK_MULTIPLIER, DAMAGE_MULTIPLIER = 'atk', 'damage'
# Values that are understood but don't change DPS or defensive stats, recorded in the notes.
TALENT_NOTE_KEYS = {
    **NOTE_KEYS,
    'sp': 'SP {value:+g}',
    'interval': 'every {value:g} s',
    'cnt': 'count {value:g}',
    'respawn_time': 'redeploy time {value:+g} s',
}


class TalentGroup(StrEnum):
    '''
    How likely a talent's condition is to be met, which decides whether it's
    applied: ALWAYS and LIKELY are applied, PER_ENEMY only against enemies
    that fit (in matchups), and UNLIKELY not at all.
    '''
    ALWAYS = "ALWAYS"
    LIKELY = "LIKELY"
    PER_ENEMY = "PER_ENEMY"
    UNLIKELY = "UNLIKELY"


# When a talent's effects sit in clauses of different groups, the least likely one decides.
GROUP_ORDER = [TalentGroup.ALWAYS, TalentGroup.LIKELY, TalentGroup.PER_ENEMY, TalentGroup.UNLIKELY]


@dataclass(frozen=True)
class TalentOverride:
    '''
    One row of the talent override file: the group to use, and optionally
    the factor (None keeps the calculated one).
    '''
    group: str
    factor: float | None = None
    note: str = ''


@dataclass(frozen=True)
class TalentCondition:
    '''
    What a PER_ENEMY talent needs from the enemy (or the fight), for
    matchups.

    kind is the condition's name (a PER_ENEMY_CONDITIONS key: "aerial",
    "blocked", "unblocked", "elite", "enemy HP", "weight", "enemy status"
    or "enemy type"). negated is True for "not blocking", "not blocked by
    this unit" and "not Elites". minimum and maximum are the limits the
    text gives: weight ("3 weight or higher" is minimum 3), the enemy's HP
    as a fraction ("less than 60% HP" is maximum 0.6, "full HP" minimum 1)
    or the number of enemies blocked ("2 or more" is minimum 2). tag is the
    enemy type (an enemyTags name such as "sarkaz" or "infection", or
    "ranged", "ground", or the words as written, e.g. "sleeping") or the
    enemy status ("stunned").
    '''
    kind: str
    negated: bool = False
    minimum: float | None = None
    maximum: float | None = None
    tag: str | None = None

    def describe(self) -> str:
        '''
        Returns the condition in a few words, e.g. "weight 3 or more",
        "not blocked", "enemy HP below 60%", "enemy type: sarkaz".
        '''
        text = f'not {self.kind}' if self.negated else self.kind
        if self.tag:
            text += f': {self.tag}'
        if self.kind == 'enemy HP':
            if self.minimum is not None:
                text += ' full' if self.minimum >= 1 else f' {self.minimum:.0%} or more'
            if self.maximum is not None:
                text += f' below {self.maximum:.0%}'
            return text
        if self.minimum is not None:
            text += f' {self.minimum:g} or more'
        if self.maximum is not None:
            text += f' {self.maximum:g} or less'
        return text


@dataclass
class TalentEffect:
    '''
    One effect of a talent (one of its values) with the condition of the
    part of the description it's in, so talents whose effects need
    different things ("ATK +14% when not blocking an enemy, DEF +14% when
    blocking an enemy") can be judged one effect at a time in matchups.

    group and condition are the effect's own (the talent's group is the
    least likely of its effects'). scale is what the value was multiplied
    by (its chance and average stacks). modifiers, atk_multiplier and
    damage_multiplier are already scaled; enemy_effect is the value as
    written (e.g. a Fragile on enemies), and penetration the DEF/RES its
    attacks ignore (with its chance).
    '''
    key: str
    group: str
    condition: TalentCondition | None = None
    scale: float = 1.0
    modifiers: StatModifiers = field(default_factory=StatModifiers)
    atk_multiplier: float = 1.0
    damage_multiplier: float = 1.0
    enemy_effect: BlackboardValue | None = None
    penetration: Penetration | None = None


@dataclass
class Talent:
    '''
    One of an operator's talents, at the version it has unlocked.

    factor is what the talent's values were multiplied by before being
    applied: a chance (0.2 for "20% chance") times the average number of
    stacks, or 1. Values outside the part of the description with the
    chance ("ASPD +6" in "ASPD +6, 15% chance to...") only get the stacks.
    modifiers (stat changes), atk_multiplier ("Increases ATK to 140%",
    counted before the enemy's DEF/RES) and damage_multiplier ("20% more
    damage", counted after it) are already multiplied; multiplier is the
    two together. They're worked out for every group, but only applied to
    the operator's figures when applied is True. condition is what a
    PER_ENEMY talent needs (e.g. aerial). penetration is the DEF/RES its
    attacks ignore, and effects lists each effect with its own condition.
    '''
    number: int
    name: str
    description: str
    group: str = TalentGroup.ALWAYS
    factor: float = 1.0
    condition: TalentCondition | None = None
    modifiers: StatModifiers = field(default_factory=StatModifiers)
    atk_multiplier: float = 1.0
    damage_multiplier: float = 1.0
    enemy_effects: list[BlackboardValue] = field(default_factory=list)
    penetration: list[Penetration] = field(default_factory=list)
    effects: list[TalentEffect] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    overridden: bool = False

    @property
    def multiplier(self) -> float:
        '''
        The talent's ATK and damage multipliers together (1.12 = 12% more
        damage before the enemy's DEF/RES).
        '''
        return self.atk_multiplier * self.damage_multiplier

    @property
    def applied(self) -> bool:
        '''
        Whether the talent counts towards the operator's general figures.
        '''
        return self.group in (TalentGroup.ALWAYS, TalentGroup.LIKELY)

    @property
    def status(self) -> str:
        '''
        "full" when every value was understood and nothing was assumed,
        "none" when nothing could be modelled, and "partial" otherwise.
        '''
        modelled = (not self.modifiers.is_empty() or self.multiplier != 1.0 or self.enemy_effects or self.notes)
        if not modelled:
            return 'none'
        return 'partial' if self.unmodelled or self.assumed else 'full'


def talent_key(raw_key: str) -> str:
    '''
    Returns a talent value's name without prefixes and variants:
    "attack@prob" -> "prob", "skadi2_t_2[atk][1].atk" -> "atk". The same
    as blackboard.value_name, which skills use too.

    Args:
        raw_key: The name as the HellaAPI gives it.

    Returns:
        The plain name.
    '''
    return value_name(raw_key)


def unlocked_candidate(talent: dict, phase: int, level: int, potential: int) -> dict | None:
    '''
    Returns the version (candidate) of a talent an operator has unlocked.

    Candidates are listed in unlock order, so the last one the operator has
    reached (by phase and level, and potential) replaces the earlier ones.

    Args:
        talent: One entry of data.talents (with "candidates").
        phase: The operator's phase (elite level).
        level: The operator's level within that phase.
        potential: The operator's potential, from 0.

    Returns:
        The candidate, or None if no version is unlocked yet.
    '''
    unlocked = None
    for candidate in talent.get('candidates') or []:
        condition = candidate.get('unlockCondition') or {}
        required = (parse_phase(condition.get('phase', 0)), condition.get('level', 1))
        if (phase, level) >= required and potential >= (candidate.get('requiredPotentialRank') or 0):
            unlocked = candidate
    return unlocked


def find_value(text: str, value: float, key: str = '', used: Collection[int] = ()) -> tuple[bool | None, int | None]:
    '''
    Finds where a value is written in a description that has its numbers
    filled in (as talent descriptions do): 0.08 as "8%", 15 as "15", and a
    damage multiplier of 1.15 as "115%" or "15%".

    When the number is written more than once, the first place not in used
    (where earlier values were found) is taken, so in "ATK +14% when not
    blocking an enemy, DEF +14% when blocking an enemy" the second 14% is
    DEF's. When every place is used, the first is shared ("ATK and DEF
    +14%").

    Args:
        text: The description without formatting tags.
        value: The value from the blackboard.
        key: The value's plain name (multipliers get the extra form).
        used: Positions already taken by other values.

    Returns:
        (is it written as a percentage, its position), or (None, None) when
        it isn't in the text.
    '''
    size = abs(value)
    forms = [(f'{size * 100:g}%', True)]
    if key in DAMAGE_KEYS and size > 1:
        forms.append((f'{(size - 1) * 100:g}%', True))
    forms.append((f'{size:g}', False))
    for written, is_percent in forms:
        # Not part of a longer number: "8%" mustn't match "18%", and "15" mustn't match "1.5" or "15%".
        ending = '' if is_percent else r'(?![\d.%])'
        positions = [match.start() for match in re.finditer(r'(?<![\d.])' + re.escape(written) + ending, text)]
        if positions:
            return is_percent, next((position for position in positions if position not in used), positions[0])
    return None, None


def _clause_at(text: str, position: int) -> tuple[str, str]:
    '''
    Returns (the whole clause around position, the part before position).

    Args:
        text: The description without formatting tags.
        position: Where a value is written.

    Returns:
        The clause and its words before the value.
    '''
    breaks = [match.end() for match in CLAUSE_BREAK.finditer(text, 0, position)]
    start = breaks[-1] if breaks else 0
    end_match = CLAUSE_BREAK.search(text, position)
    end = end_match.start() if end_match else len(text)
    return text[start:end], text[start:position]


def _target_of(words_before: str, words_after: str = '') -> Target:
    '''
    Decides who a talent value affects from the words before it in its
    clause, after the last comma: in "Before defeating an enemy for the
    first time, gains +10 ASPD" the enemy is part of the condition, and
    only "gains +" says who gets the ASPD. A value followed by Fragile or
    Fragility (before the next comma) is on enemies.
    '''
    words_before = words_before.split(',')[-1]
    if ENEMY_WORDS.search(words_before) or TALENT_ENEMY_WORDS.search(words_before) or FRAGILE_AFTER.search(words_after):
        return Target.ENEMY
    if ALLY_WORDS.search(words_before) or TALENT_ALLY_WORDS.search(words_before):
        return Target.ALLY
    return Target.SELF


def group_name(name: str) -> str:
    '''
    Returns a group name (class, position, rarity or faction) in a form that
    matches however it's written: lowercase, without brackets or quotes,
    aliases resolved (FACTION_ALIASES), and without a final "s" ("[Abyssal
    Hunter]" and "Abyssal Hunters" are both "abyssal hunter").
    '''
    name = re.sub(r"[\[\]'\"’]", '', name.lower())
    name = re.sub(r'\s+', ' ', name).strip()
    name = FACTION_ALIASES.get(name, name)
    return name[:-1] if len(name) > 3 and name.endswith('s') else name


def member_groups(operator_value: dict) -> frozenset[str]:
    '''
    Returns the groups an operator belongs to, as group_name()s: its class
    ("vanguard"), position ("melee"), rarity ("five-star") and factions
    (the name, code and id of each nation, group and team in "factions",
    e.g. "abyssal hunter", "egir").

    Args:
        operator_value: The operator's whole HellaAPI value (with "data" and
            "factions").
    '''
    data = operator_value.get('data') or {}
    names = [CLASS_NAMES.get(data.get('profession') or ''), data.get('position'), RARITY_NAMES.get(data.get('rarity') or '')]
    for faction in operator_value.get('factions') or []:
        for power in (faction or {}).values():
            if isinstance(power, dict):
                names += [power.get('powerName'), power.get('powerCode'), power.get('powerId')]
    return frozenset(group_name(name) for name in names if name)


def shared_group(words_before: str, member_of: frozenset[str]) -> str | None:
    '''
    Returns the words naming the group an ally buff is for ("All Vanguard
    Operators'"), when the operator belongs to that group, or None.

    The group is the words before the last GROUP_NOUN in the value's part
    of the clause (after the last comma). The operator belongs when they
    say "this unit and", or "all" with no other name ("All allies"), or
    name one of its groups (its class, position, rarity or a faction).
    Buffs limited by position or a condition (GROUP_LIMIT) don't count,
    unless they say "this unit and".

    Args:
        words_before: The words before the value in its clause.
        member_of: The operator's groups (member_groups).
    '''
    segment = words_before.split(',')[-1]
    nouns = list(GROUP_NOUN.finditer(segment))
    if not nouns or not member_of:
        return None
    noun = nouns[-1]
    qualifier = segment[:noun.start()]
    phrase = segment[:noun.end()]
    if INCLUDES_THIS_UNIT.search(qualifier):
        return phrase
    if GROUP_LIMIT.search(segment[noun.end():]):
        return None
    if EVERYONE.search(qualifier):
        return phrase
    names = [name for pair in re.findall(r"\[([^\]]+)\]|'([^']+)'", qualifier) for name in pair if name]
    words = [word for word in re.findall(r"[\w-]+", re.sub(r"\[[^\]]+\]|'[^']+'", ' ', qualifier))
             if word.lower() not in GROUP_FILLER]
    candidates = [*names, *words, ' '.join(words)]
    return phrase if any(candidate and group_name(candidate) in member_of for candidate in candidates) else None


def clause_group(clause: str) -> tuple[str | None, str | None]:
    '''
    Returns (group, enemy condition) for one clause, or (None, None) when it
    has condition words that no rule recognises, or effects picked at random.

    Args:
        clause: A clause of a talent description.

    Returns:
        The TalentGroup and, for PER_ENEMY, the condition's name.
    '''
    if RANDOM_WORDS.search(clause):
        return None, None
    for condition, pattern in PER_ENEMY_CONDITIONS.items():
        if pattern.search(clause):
            return TalentGroup.PER_ENEMY, condition
    if UNLIKELY_WORDS.search(clause):
        return TalentGroup.UNLIKELY, None
    if LIKELY_WORDS.search(clause):
        return TalentGroup.LIKELY, None
    if not CONDITION_WORDS.search(clause):
        return TalentGroup.ALWAYS, None
    return None, None


def parse_condition(kind: str, clause: str) -> TalentCondition:
    '''
    Returns a PER_ENEMY condition with the details its clause gives (see
    TalentCondition).

    Args:
        kind: The condition's name, from clause_group.
        clause: The clause it was found in.
    '''
    negated = kind in ('blocked', 'elite') and bool(NOT_WORDS.search(clause))
    minimum = maximum = None
    tag = None
    if kind == 'blocked':
        count = COUNT_OR_MORE.search(clause)
        if count:
            word = count.group(1).lower()
            minimum = float(NUMBER_WORDS.get(word) or word)
    elif kind == 'weight':
        at_least, at_most = WEIGHT_AT_LEAST.search(clause), WEIGHT_AT_MOST.search(clause)
        if at_least:
            minimum = float(at_least.group(1) or at_least.group(2))
        if at_most:
            maximum = float(at_most.group(1) or at_most.group(2))
    elif kind == 'enemy HP':
        below, at_least = HP_BELOW.search(clause), HP_AT_LEAST.search(clause)
        if FULL_HP.search(clause):
            minimum = 1.0
        elif at_least:
            minimum = float(at_least.group(1)) / 100
        if below:
            maximum = float(below.group(1)) / 100
    elif kind == 'enemy type':
        named = ENEMY_TYPE_NAME.search(clause)
        if named:
            name = group_name(named.group(1) or named.group(2) or named.group(3))
            tag = ENEMY_TYPE_TAGS.get(name, name)
    elif kind == 'enemy status':
        status = ENEMY_STATUS_NAME.search(clause)
        tag = status.group(1).lower() if status else None
    return TalentCondition(kind, negated, minimum, maximum, tag)


def clause_condition(clause: str) -> tuple[str | None, TalentCondition | None]:
    '''
    Returns (group, condition) for one clause: clause_group's group, with
    the PER_ENEMY condition's details (parse_condition).
    '''
    group, kind = clause_group(clause)
    return group, (parse_condition(kind, clause) if kind else None)


def average_stacks(max_stacks: float, seconds_per_stack: float | None, window: float | None) -> float | None:
    '''
    Returns the average number of stacks over a time window that starts
    with none, gaining one every seconds_per_stack up to max_stacks.

    Args:
        max_stacks: The most stacks the effect can have.
        seconds_per_stack: Seconds between stacks, or None if not known.
        window: The time to average over (e.g. one skill rotation), or None.

    Returns:
        The average stacks, or None if the rate or window isn't known.
    '''
    if not seconds_per_stack or not window or window <= 0:
        return None
    full_time = max_stacks * seconds_per_stack
    if window <= full_time:
        # Still building up at the end of the window: the average is half the stacks reached.
        return window / seconds_per_stack / 2
    # Building up (averaging half the maximum), then at the maximum for the rest of the window.
    return (full_time * max_stacks / 2 + (window - full_time) * max_stacks) / window


def _seconds_per_stack(clause: str, attack_interval: float | None) -> float | None:
    '''
    Returns the seconds between stacks from the clause's wording, or None
    if the rate isn't per attack or per time (e.g. per kill).
    '''
    every_attacks = EVERY_N_ATTACKS.search(clause)
    if every_attacks:
        return int(every_attacks.group(1)) * attack_interval if attack_interval else None
    if PER_ATTACK.search(clause):
        return attack_interval
    every_seconds = EVERY_N_SECONDS.search(clause)
    if every_seconds:
        return float(every_seconds.group(1))
    if PER_SECOND.search(clause):
        return 1.0
    return None


def build_talent(
    number: int, candidate: dict, operator_id: str | None = None, attack_interval: float | None = None,
    window: float | None = None, overrides: dict | None = None, member_of: frozenset[str] = frozenset(),
) -> Talent:
    '''
    Builds a Talent from its unlocked candidate.

    Args:
        number: The talent's number (from 1).
        candidate: The unlocked candidate (see unlocked_candidate).
        operator_id: The operator's id, to look up overrides.
        attack_interval: The operator's seconds between attacks, for stacks
            gained per attack.
        window: The time to average stacks over (one skill rotation).
        overrides: {(operator_id, talent number): TalentOverride}; None
            loads the override file.
        member_of: The operator's groups (member_groups), so buffs to a
            group it belongs to ("All Vanguard Operators") apply to it too.

    Returns:
        The Talent.
    '''
    text = strip_tags(candidate.get('description'))
    talent = Talent(number=number, name=candidate.get('name') or '', description=text)

    # Read each value (the first of any duplicate names), finding where it's written to judge its clause, and its
    # piece of the clause (between commas) to tell whether a chance applies to it.
    values: dict[str, BlackboardValue] = {}
    clauses: dict[str, str] = {}
    pieces: dict[str, str] = {}
    # The words before and after each value's number in its clause, to tell what a multiplier multiplies.
    around: dict[str, tuple[str, str]] = {}
    # Where values were found, so a number written twice is matched to each place in turn.
    used: set[int] = set()
    # Buffs to a group the operator belongs to: {value name: the words naming the group}.
    shared: dict[str, str] = {}
    for entry in candidate.get('blackboard') or []:
        if entry.get('value') is None:
            continue
        key = talent_key(entry['key'])
        if key in values:
            # A copy with the same value (e.g. "talent@atk" next to "atk") changes nothing; a different one is a guess.
            if entry['value'] != values[key].value:
                talent.assumed.append(f'{entry["key"]} ignored (another version of {key})')
            continue
        is_percent, position = find_value(text, entry['value'], key, used)
        if position is None:
            values[key] = BlackboardValue(key, entry['key'], entry['value'], None, Target.UNKNOWN)
            clauses[key], pieces[key], around[key] = text, '', ('', '')
        else:
            used.add(position)
            clause, words_before = _clause_at(text, position)
            words_after = clause[len(words_before):]
            target = _target_of(words_before, words_after)
            # Only stat and damage changes are applied to the operator; other ally effects stay as they were.
            effect = key in STAT_KEYS or key in ATTACK_KEYS or key in DAMAGE_KEYS
            group = shared_group(words_before, member_of) if target == Target.ALLY and effect else None
            if group is not None:
                target, shared[key] = Target.SELF, group
            values[key] = BlackboardValue(key, entry['key'], entry['value'], is_percent, target)
            clauses[key] = clause
            pieces[key] = words_before.split(',')[-1] + words_after.split(',')[0]
            around[key] = (words_before, LEADING_NUMBER.sub('', words_after))

    # The group comes from the clauses holding the talent's own effects; the least likely one decides. Effects that
    # aren't in the description (it shows every number) aren't plain bonuses, so they're left out (and unmodelled).
    effect_keys = [key for key, value in values.items()
                   if (is_stat_value(value) or key in DAMAGE_KEYS) and value.target == Target.SELF]
    # A talent that only weakens enemies ("Enemies within attack range gain 12% Fragile") takes its group from those.
    group_keys = effect_keys or [key for key, value in values.items()
                                 if (is_stat_value(value) or key in DAMAGE_KEYS or key in PENETRATION_KEYS)
                                 and value.target == Target.ENEMY]
    groups = [clause_condition(_group_clause(clauses[key], shared.get(key))) for key in group_keys] or [clause_condition(text)]
    if any(group is None for group, _ in groups):
        talent.group = TalentGroup.UNLIKELY
        talent.assumed.append('condition not recognised, so not applied (review)')
    else:
        known = [(TalentGroup(group), condition) for group, condition in groups if group is not None]
        talent.group, talent.condition = max(known, key=lambda pair: GROUP_ORDER.index(pair[0]))

    for group in dict.fromkeys(shared.values()):
        named = GROUP_START.search(group)
        talent.notes.append(f"also buffs {(named.group(0) if named else group).strip().rstrip(chr(39)).strip()}")
    if shared and 'duration' in values:
        talent.assumed.append(f"the shared buff lasts {values['duration'].value:g} s; counted as always on")

    chance_key = 'prob' if 'prob' in values else next((key for key in values if CHANCE_KEY.fullmatch(key)), None)
    chance, chance_keys, stacks = _factor(talent, values, clauses, pieces, effect_keys, chance_key, attack_interval,
                                          window)
    talent.factor = chance * stacks
    scales = {key: (chance if key in chance_keys else 1.0) * stacks for key in values}
    override = (load_talent_overrides() if overrides is None else overrides).get((operator_id or '', number))
    if override is not None:
        talent.group, talent.overridden = override.group, True
        if override.factor is not None:
            talent.factor = override.factor
            scales = dict.fromkeys(values, override.factor)
        talent.notes.append(f'overridden: {override.note}' if override.note else 'overridden')

    for key, value in values.items():
        if key in (chance_key, 'max_stack_cnt'):
            continue
        group, condition = _effect_condition(clauses[key], pieces[key], shared.get(key))
        talent_effect = TalentEffect(key, talent.group if override is not None else group, condition, scales[key])
        if value.target == Target.UNKNOWN and (is_stat_value(value) or key in DAMAGE_KEYS):
            talent.unmodelled.append(f'{value.raw_key} (not in the description)')
        elif is_stat_value(value):
            collected = StatEffects()
            apply_stat_value(collected, value, scales[key])
            talent.modifiers = talent.modifiers + collected.modifiers
            talent.enemy_effects += collected.enemy_effects
            talent.notes += collected.notes
            talent.assumed += collected.assumed
            if not collected.modifiers.is_empty() or collected.enemy_effects:
                talent_effect.modifiers = collected.modifiers
                talent_effect.enemy_effect = collected.enemy_effects[0] if collected.enemy_effects else None
                talent.effects.append(talent_effect)
        elif key in DAMAGE_KEYS:
            if _apply_damage_value(talent, talent_effect, value, clauses[key], around[key]):
                talent.effects.append(talent_effect)
        elif key in PENETRATION_KEYS:
            talent.enemy_effects.append(value)
            # A chance in the value's clause ("50% chance to ignore 50% DEF") is how often it applies; stacks
            # scale the amount.
            chance_value = values[chance_key].value if chance_key is not None else 1.0
            applies = CHANCE_WORDS.search(clauses[key]) and not DODGE_WORDS.search(clauses[key])
            talent_effect.penetration = Penetration.from_value(value, stacks, chance_value if applies else 1.0)
            talent.penetration.append(talent_effect.penetration)
            talent.effects.append(talent_effect)
        elif key in TALENT_NOTE_KEYS:
            talent.notes.append(TALENT_NOTE_KEYS[key].format(value=value.value))
        else:
            talent.unmodelled.append(value.raw_key)
    return talent


def _effect_condition(clause: str, piece: str, group: str | None) -> tuple[str, TalentCondition | None]:
    '''
    Returns (group, condition) of one effect: from its own piece of the
    clause (between commas) when that names an enemy condition ("DEF +14%
    when blocking an enemy"), otherwise from the whole clause ("When
    attacking aerial targets, increases ATK to 123%"). Wording no rule
    recognises gives UNLIKELY.

    Args:
        clause: The value's clause.
        piece: The value's piece of the clause.
        group: The words naming the group, for a buff to a group the
            operator belongs to (see _group_clause), or None.
    '''
    own = clause_condition(_group_clause(piece, group))
    found = own if own[0] == TalentGroup.PER_ENEMY else clause_condition(_group_clause(clause, group))
    return (found[0] or TalentGroup.UNLIKELY), found[1]


def _group_clause(clause: str, group: str | None) -> str:
    '''
    Returns the clause to judge a value's condition from: for a buff to a
    group the operator belongs to, without the group's words (which would
    otherwise make it depend on other operators), "when this unit is in
    the squad", and, for "This unit and..." buffs, range wording.
    '''
    if group is None:
        return clause
    clause = SELF_PRESENT.sub(' ', clause.replace(group.strip(), ' '))
    return RANGE_TEXT.sub(' ', clause) if INCLUDES_THIS_UNIT.search(group) else clause


def _factor(talent: Talent, values: dict[str, BlackboardValue], clauses: dict[str, str], pieces: dict[str, str],
            effect_keys: list[str], chance_key: str | None, attack_interval: float | None,
            window: float | None) -> tuple[float, set[str], float]:
    '''
    Returns (the chance, the effects it applies to, the average stacks):
    what the talent's values are multiplied by. The chance applies to the
    effects whose piece of the clause mentions it ("ASPD +6, 15% chance to
    increase ATK to 150%" only to the ATK), or to all of them when only the
    whole clause does. Also records dodge chances and other chances as
    notes, and stack assumptions.
    '''
    effect_text = ' '.join(clauses[key] for key in effect_keys)
    chance, chance_keys, stacks = 1.0, set(), 1.0
    if chance_key is not None:
        prob = values[chance_key]
        chance_keys = {key for key in effect_keys if CHANCE_WORDS.search(pieces[key])}
        if not chance_keys and CHANCE_WORDS.search(effect_text):
            chance_keys = set(effect_keys)
        if DODGE_WORDS.search(clauses[chance_key]):
            talent.notes.append(f'{prob.value:.0%} dodge chance')
            chance_keys = set()
        elif chance_keys:
            chance = prob.value
        else:
            talent.notes.append(f'{prob.value:.0%} chance of an effect')
    if effect_keys and STACK_WORDS.search(effect_text):
        max_text = MAX_STACKS_TEXT.search(effect_text)
        max_stacks = values['max_stack_cnt'].value if 'max_stack_cnt' in values else None
        if max_stacks is None and max_text:
            max_stacks = int(max_text.group(1))
        if max_stacks is None:
            talent.assumed.append('stacks with no maximum given; counted once')
        else:
            average = average_stacks(max_stacks, _seconds_per_stack(effect_text, attack_interval), window)
            if average is None:
                average = max_stacks * UNKNOWN_STACK_FRACTION
                talent.assumed.append(f'stack rate unknown; assumed half of {max_stacks:g} stacks')
            stacks = average
    return chance, chance_keys, stacks


def multiplier_kind(words_before: str, words_after: str) -> str | None:
    '''
    Returns whether a talent multiplier raises ATK (ATK_MULTIPLIER, counted
    before the enemy's DEF/RES) or the damage dealt (DAMAGE_MULTIPLIER,
    counted after), from the words next to its number: the words after it
    first ("20% more damage", "133% ATK as Physical damage"), then the words
    before it ("Increases ATK to 140%", "the current damage of that attack
    to 160%"; the nearest of the two words counts). None when neither says.

    Args:
        words_before: The clause's words before the number.
        words_after: The clause's words after the number.
    '''
    if ATK_AFTER.search(words_after):
        return ATK_MULTIPLIER
    if DAMAGE_AFTER.search(words_after):
        return DAMAGE_MULTIPLIER
    before = MULTIPLIER_BEFORE.search(words_before)
    if before:
        return ATK_MULTIPLIER if before.group(1).upper() == 'ATK' else DAMAGE_MULTIPLIER
    return None


def _apply_damage_value(talent: Talent, effect: TalentEffect, value: BlackboardValue, clause: str,
                        around: tuple[str, str]) -> bool:
    '''
    Adds an ATK or damage multiplier (damage_scale or atk_scale) to the
    talent and the effect, or records it as an enemy effect (e.g. Arts
    Fragility) or as unmodelled (an ally's, one not on attacks, one below
    1, or one for splash damage only). Returns whether the effect has
    anything in it.

    Whether it raises ATK or damage comes from its wording
    (multiplier_kind); when the wording doesn't say, from its name
    (atk_scale raises ATK), recorded as an assumption.

    A multiplier m with scale f (its chance and stacks) becomes
    1 + (m - 1) * f: a 20% chance to deal 160% damage averages
    1 + 0.6 * 0.2 = 1.12. When the boosted attack also strikes again
    (EXTRA_STRIKE), it deals m twice: 1 + (2m - 1) * f, so a 25% chance of
    two 130% strikes averages 1 + 1.6 * 0.25 = 1.4.
    '''
    if value.target == Target.ENEMY:
        talent.enemy_effects.append(value)
        effect.enemy_effect = value
        return True
    not_on_attacks = value.key == 'atk_scale' and (not ATTACK_WORDS.search(clause) or DEPLOY_WORDS.search(clause))
    # Below 1 isn't a bonus but something else, e.g. a minimum ("Deals at least 33% of ATK damage every attack").
    if value.target == Target.ALLY or not_on_attacks or value.value < 1:
        talent.unmodelled.append(f'{value.raw_key} (not an attack multiplier)')
        return False
    if SPLASH_WORDS.search(clause):
        talent.unmodelled.append(f'{value.raw_key} (splash damage only)')
        return False
    strikes = 1
    if EXTRA_STRIKE.search(clause):
        strikes = 2
        talent.notes.append('the boosted attack strikes twice')
    multiplier = 1 + (value.value * strikes - 1) * effect.scale
    kind = multiplier_kind(*around)
    if kind is None:
        kind = ATK_MULTIPLIER if value.key == 'atk_scale' else DAMAGE_MULTIPLIER
        talent.assumed.append(f"{value.raw_key}: the text doesn't say whether it raises ATK or damage; "
                              f"counted as {kind} by its name (review)")
    if kind == ATK_MULTIPLIER:
        talent.atk_multiplier *= multiplier
        effect.atk_multiplier = multiplier
    else:
        talent.damage_multiplier *= multiplier
        effect.damage_multiplier = multiplier
    return True


def parse_talent_overrides(text: str) -> dict[tuple[str, int], TalentOverride]:
    '''
    Reads the talent override file's text.

    Args:
        text: CSV with the TALENT_OVERRIDE_COLUMNS header. Blank rows are
            skipped; factor and note may be left empty.

    Returns:
        {(operator id, talent number): TalentOverride}.

    Raises:
        ValueError: If a row's group isn't a TalentGroup, or its talent or
            factor isn't a number.
    '''
    overrides = {}
    for row in csv.DictReader(io.StringIO(text)):
        if not (row.get('operator_id') or '').strip():
            continue
        group = (row.get('group') or '').strip().upper()
        if group not in TalentGroup.__members__:
            raise ValueError(f"Talent override for {row['operator_id']} has unknown group {row.get('group')!r}")
        factor = (row.get('factor') or '').strip()
        overrides[(row['operator_id'].strip(), int(row['talent']))] = TalentOverride(
            TalentGroup[group], float(factor) if factor else None, (row.get('note') or '').strip())
    return overrides


@lru_cache(maxsize=1)
def load_talent_overrides() -> dict[tuple[str, int], TalentOverride]:
    '''
    Returns the overrides from src/arkdata/data/talent_conditions.csv
    (read once, then reused).
    '''
    text = resources.files(TALENT_OVERRIDES_PACKAGE).joinpath(TALENT_OVERRIDES_FILE).read_text(encoding='utf-8')
    return parse_talent_overrides(text)


def build_talents(
    operator_data: dict, phase: int, level: int, potential: int, operator_id: str | None = None,
    attack_interval: float | None = None, window: float | None = None, overrides: dict | None = None,
    member_of: frozenset[str] = frozenset(),
) -> list[Talent]:
    '''
    Returns an operator's unlocked talents. Hidden talents and ones with no
    description are skipped.

    Args:
        operator_data: The operator's (or summon's) HellaAPI data, with
            "talents".
        phase: The operator's phase (elite level).
        level: The operator's level within that phase.
        potential: The operator's potential, from 0.
        operator_id: The operator's id, for overrides.
        attack_interval: Seconds between attacks, for stacks gained per attack.
        window: The time to average stacks over (one skill rotation).
        overrides: Overrides to use instead of the override file.
        member_of: The operator's groups (member_groups).

    Returns:
        One Talent per unlocked talent, numbered from 1 in data order.
    '''
    talents = []
    for number, talent in enumerate(operator_data.get('talents') or [], start=1):
        candidate = unlocked_candidate(talent, phase, level, potential)
        if candidate is None or candidate.get('isHideTalent') or not strip_tags(candidate.get('description')).strip():
            continue
        talents.append(build_talent(number, candidate, operator_id, attack_interval, window, overrides, member_of))
    return talents
