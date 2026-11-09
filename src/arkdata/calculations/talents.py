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
from arkdata.calculations.skills import ATTACK_KEYS, NOTE_KEYS, STAT_KEYS, StatModifiers, apply_stat_value, is_stat_value

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
# Values that make attacks ignore part of the enemy's DEF or RES ("Attacks ignore 23% DEF"), kept for matchups.
PENETRATION_KEYS = {'def_penetrate', 'def_penetrate_fixed', 'magic_resist_penetrate', 'magic_resist_penetrate_fixed'}
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


@dataclass
class Talent:
    '''
    One of an operator's talents, at the version it has unlocked.

    factor is what the talent's values were multiplied by before being
    applied: a chance (0.2 for "20% chance") times the average number of
    stacks, or 1. Values outside the part of the description with the
    chance ("ASPD +6" in "ASPD +6, 15% chance to...") only get the stacks.
    modifiers (stat changes) and damage_multiplier (1.12 = 12% more damage)
    are already multiplied. They're worked out for every group, but
    only applied to the operator's figures when applied is True. condition
    names the enemy a PER_ENEMY talent needs (e.g. "aerial").
    '''
    number: int
    name: str
    description: str
    group: str = TalentGroup.ALWAYS
    factor: float = 1.0
    condition: str | None = None
    modifiers: StatModifiers = field(default_factory=StatModifiers)
    damage_multiplier: float = 1.0
    enemy_effects: list[BlackboardValue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    overridden: bool = False

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
        modelled = (not self.modifiers.is_empty() or self.damage_multiplier != 1.0 or self.enemy_effects or self.notes)
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


def find_value(text: str, value: float, key: str = '') -> tuple[bool | None, int | None]:
    '''
    Finds where a value is written in a description that has its numbers
    filled in (as talent descriptions do): 0.08 as "8%", 15 as "15", and a
    damage multiplier of 1.15 as "115%" or "15%".

    Args:
        text: The description without formatting tags.
        value: The value from the blackboard.
        key: The value's plain name (multipliers get the extra form).

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
        match = re.search(r'(?<![\d.])' + re.escape(written) + ending, text)
        if match:
            return is_percent, match.start()
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
        is_percent, position = find_value(text, entry['value'], key)
        if position is None:
            values[key] = BlackboardValue(key, entry['key'], entry['value'], None, Target.UNKNOWN)
            clauses[key], pieces[key] = text, ''
        else:
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

    # The group comes from the clauses holding the talent's own effects; the least likely one decides. Effects that
    # aren't in the description (it shows every number) aren't plain bonuses, so they're left out (and unmodelled).
    effect_keys = [key for key, value in values.items()
                   if (is_stat_value(value) or key in DAMAGE_KEYS) and value.target == Target.SELF]
    # A talent that only weakens enemies ("Enemies within attack range gain 12% Fragile") takes its group from those.
    group_keys = effect_keys or [key for key, value in values.items()
                                 if (is_stat_value(value) or key in DAMAGE_KEYS or key in PENETRATION_KEYS)
                                 and value.target == Target.ENEMY]
    groups = [clause_group(_group_clause(clauses[key], shared.get(key))) for key in group_keys] or [clause_group(text)]
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
        if value.target == Target.UNKNOWN and (is_stat_value(value) or key in DAMAGE_KEYS):
            talent.unmodelled.append(f'{value.raw_key} (not in the description)')
        elif is_stat_value(value):
            apply_stat_value(talent, value, scales[key])
        elif key in DAMAGE_KEYS:
            _apply_damage_value(talent, value, clauses[key], scales[key])
        elif key in PENETRATION_KEYS:
            talent.enemy_effects.append(value)
        elif key in TALENT_NOTE_KEYS:
            talent.notes.append(TALENT_NOTE_KEYS[key].format(value=value.value))
        else:
            talent.unmodelled.append(value.raw_key)
    return talent


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


def _apply_damage_value(talent: Talent, value: BlackboardValue, clause: str, scale: float) -> None:
    '''
    Adds a damage multiplier (damage_scale or atk_scale) to the talent, or
    records it as an enemy effect (e.g. Arts Fragility) or as unmodelled
    (an ally's, one not on attacks, one below 1, or one for splash damage
    only).

    A multiplier m with scale f (its chance and stacks) becomes
    1 + (m - 1) * f: a 20% chance to deal 160% damage averages
    1 + 0.6 * 0.2 = 1.12. When the boosted attack also strikes again
    (EXTRA_STRIKE), it deals m twice: 1 + (2m - 1) * f, so a 25% chance of
    two 130% strikes averages 1 + 1.6 * 0.25 = 1.4.
    '''
    if value.target == Target.ENEMY:
        talent.enemy_effects.append(value)
        return
    not_on_attacks = value.key == 'atk_scale' and (not ATTACK_WORDS.search(clause) or DEPLOY_WORDS.search(clause))
    # Below 1 isn't a bonus but something else, e.g. a minimum ("Deals at least 33% of ATK damage every attack").
    if value.target == Target.ALLY or not_on_attacks or value.value < 1:
        talent.unmodelled.append(f'{value.raw_key} (not an attack multiplier)')
        return
    if SPLASH_WORDS.search(clause):
        talent.unmodelled.append(f'{value.raw_key} (splash damage only)')
        return
    strikes = 1
    if EXTRA_STRIKE.search(clause):
        strikes = 2
        talent.notes.append('the boosted attack strikes twice')
    talent.damage_multiplier *= 1 + (value.value * strikes - 1) * scale


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
