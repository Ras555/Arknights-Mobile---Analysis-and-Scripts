#File Documentation
'''
File: blackboard.py
Description: Reads the named values ("blackboard") of a skill or talent
    level from its HellaAPI data, working out from the description text
    whether each value is a percentage or a flat amount and who it affects,
    and fills the description template in to give readable text.
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import re
from dataclasses import dataclass
from enum import StrEnum

# Constants

# Placeholders in description templates: {atk}, {atk:0%}, {-def}, {attack@times}, {burn.atk_scale:0.0%}.
# A leading "-" means the value is shown negated (e.g. "DEF -{-def}" with def = -200 reads "DEF -200").
PLACEHOLDER = re.compile(r'\{(-?)([^{}:]+)(?::([^{}]*))?\}')
# Formatting tags, e.g. <@ba.vup>+70%</> or <$ba.stun>Stunning</>. Plain angle-bracket text such as
# <Support Devices> is part of the description and is kept.
FORMATTING_TAG = re.compile(r'<[@$][^>]*>|</>')
# Descriptions are split into clauses at these, so each value is judged by the words near it.
CLAUSE_BREAK = re.compile(r'[;\n]|\.\s')

# Words before a value (in the same clause) that mean it affects enemies or allies rather than the
# unit itself. Checked in this order; with neither, the value affects the unit itself.
ENEMY_WORDS = re.compile(r"\b(enem(y|ies)|targets?'?s?|target's|foes?)\b", re.IGNORECASE)
ALLY_WORDS = re.compile(r'\b(all(y|ies)|allied|friendly|other operators|operators within)\b', re.IGNORECASE)

# Prefix the HellaAPI puts on some value names (e.g. attack@atk_scale). It doesn't change the meaning.
ATTACK_PREFIX = 'attack@'
# All the prefixes that don't change a value's meaning.
KEY_PREFIXES = (ATTACK_PREFIX, 'talent@')
# Bracket tags in a variant name: the "[b]" in "amgoat_s_1[b].atk" (Eyjafjalla's S1 from its second use).
VARIANT_TAG = re.compile(r'\[[^\]]*\]')


class Target(StrEnum):
    '''
    Who a value affects. UNKNOWN is used for values that don't appear in
    the description text, so there are no words to judge by.
    '''
    SELF = "SELF"
    ENEMY = "ENEMY"
    ALLY = "ALLY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BlackboardValue:
    '''
    One named value of a skill or talent level.

    key is the name without the "attack@" prefix and raw_key the name as
    the HellaAPI gives it. is_percent is True when the description shows
    the value as a percentage (e.g. {atk:0%}), False when it shows it as a
    plain number, and None when the value isn't in the description.
    '''
    key: str
    raw_key: str
    value: float
    is_percent: bool | None
    target: Target

    @property
    def in_text(self) -> bool:
        '''
        Whether the value appears in the description text.
        '''
        return self.is_percent is not None


def strip_tags(text: str | None) -> str:
    '''
    Removes the formatting tags from a description, keeping the words.

    Args:
        text: A HellaAPI description, or None.

    Returns:
        The description without tags ("" for None).
    '''
    return FORMATTING_TAG.sub('', text or '')


def _clause_before(text: str, position: int) -> str:
    '''
    Returns the part of text's clause that comes before position.

    Args:
        text: A description without tags.
        position: Where a placeholder starts.

    Returns:
        The words from the start of the clause up to position.
    '''
    breaks = [match.end() for match in CLAUSE_BREAK.finditer(text, 0, position)]
    return text[breaks[-1] if breaks else 0:position]


def _target_of(text: str, position: int) -> Target:
    '''
    Decides who a value affects from the words before it in its clause.

    For example, "Targets' DEF -{-def}" affects enemies, while
    "DEF -{-def:0%}; ATK +{atk:0%}" affects the unit itself.

    Args:
        text: A description without tags.
        position: Where the value's placeholder starts.

    Returns:
        ENEMY, ALLY, or SELF.
    '''
    words = _clause_before(text, position)
    if ENEMY_WORDS.search(words):
        return Target.ENEMY
    if ALLY_WORDS.search(words):
        return Target.ALLY
    return Target.SELF


def value_name(raw_key: str) -> str:
    '''
    Returns a value's plain name, without prefixes (KEY_PREFIXES) and
    variants (everything up to the last "."): "attack@prob" -> "prob",
    "amgoat_s_1[b].atk" -> "atk", "skadi2_t_2[atk][1].atk" -> "atk".

    Args:
        raw_key: The name as the HellaAPI gives it.

    Returns:
        The plain name.
    '''
    key = raw_key.split('.')[-1]
    for prefix in KEY_PREFIXES:
        key = key.removeprefix(prefix)
    return key


def _variant_prefix(raw_key: str) -> str:
    '''
    Returns the part of a variant name before its last ".", without
    prefixes: "attack@amgoat_s_1[b].atk" -> "amgoat_s_1[b]".
    '''
    prefix = raw_key.rsplit('.', 1)[0]
    for key_prefix in KEY_PREFIXES:
        prefix = prefix.removeprefix(key_prefix)
    return prefix


def _value_key(raw_key: str, plain_names: set[str], versions: dict[str, set[str]]) -> str:
    '''
    Returns the key a skill value is read under (see read_blackboard).

    Args:
        raw_key: The name as the HellaAPI gives it.
        plain_names: The plain names of the level's values without a ".".
        versions: {variant base (prefix without tags): its prefixes}.
    '''
    if '.' not in raw_key:
        return value_name(raw_key)
    prefix = _variant_prefix(raw_key)
    if value_name(raw_key) not in plain_names and len(versions[VARIANT_TAG.sub('', prefix)]) > 1:
        return value_name(raw_key)
    # A separate effect with its own name ("burn.atk_scale", "extrabomb.atk_scale"): kept whole.
    return raw_key.removeprefix(ATTACK_PREFIX)


def read_blackboard(level: dict, ignored: list[str] | None = None) -> dict[str, BlackboardValue]:
    '''
    Reads every named value of a skill or talent level.

    Values are keyed by their name without the "attack@" prefix. A variant
    name ("prefix.name") is read as a version of the plain value when
    its prefix has versions with different bracket tags and there's no
    plain value of that name: Eyjafjalla's S1 has "amgoat_s_1[a]" (first
    use) and "amgoat_s_1[b]" (from the second use), so its
    "amgoat_s_1[b].atk" is read as "atk". Other variants are separate
    effects ("burn.atk_scale", "extrabomb.atk_scale",
    "chen3_s2[respawn_buff].atk") and keep their whole name, so they're
    listed as unmodelled. When a key appears more than once (with and
    without a prefix, or as versions), the first one listed is kept.

    Args:
        level: One level of a skill (excel.levels[i]) or a talent candidate,
            with "description" and "blackboard".
        ignored: If given, a message is added to it for each later version
            whose number differs from the one kept.

    Returns:
        {key: BlackboardValue}.
    '''
    text = strip_tags(level.get('description'))
    # The first placeholder for each name decides its format and target. Names are matched ignoring case,
    # since a few templates write them in capitals (e.g. {HP_RECOVERY_PER_SEC_BY_MAX_HP_RATIO:0.0%}).
    placeholders: dict[str, re.Match[str]] = {}
    for match in PLACEHOLDER.finditer(text):
        placeholders.setdefault(match.group(2).lower(), match)

    entries = [entry for entry in level.get('blackboard') or [] if entry.get('value') is not None]
    plain_names = {value_name(entry['key']) for entry in entries if '.' not in entry['key']}
    versions: dict[str, set[str]] = {}
    for entry in entries:
        if '.' in entry['key']:
            prefix = _variant_prefix(entry['key'])
            versions.setdefault(VARIANT_TAG.sub('', prefix), set()).add(prefix)

    values: dict[str, BlackboardValue] = {}
    for entry in entries:
        raw_key = entry['key']
        key = _value_key(raw_key, plain_names, versions)
        if key in values:
            if ignored is not None and entry['value'] != values[key].value:
                ignored.append(f'{raw_key} ignored (another version of {key} is used)')
            continue
        found = placeholders.get(raw_key.lower())
        if found is None:
            values[key] = BlackboardValue(key, raw_key, entry['value'], None, Target.UNKNOWN)
        else:
            is_percent = '%' in (found.group(3) or '')
            values[key] = BlackboardValue(key, raw_key, entry['value'], is_percent, _target_of(text, found.start()))
    return values


def _format_value(value: float, negate: bool, number_format: str | None) -> str:
    '''
    Formats one value the way the game's template asks for.

    Args:
        value: The value from the blackboard.
        negate: True for a "{-key}" placeholder, which shows the value negated.
        number_format: The text after ":" in the placeholder (e.g. "0%" or
            "0.0%"), or None.

    Returns:
        e.g. "70%" for (0.7, False, "0%"), "200" for (-200, True, None),
        "1.5" for (1.5, False, None).
    '''
    if negate:
        value = -value
    if number_format and '%' in number_format:
        # "0%" shows whole percentages, "0.0%" one decimal place, and so on.
        decimals = len(number_format.split('.')[1].rstrip('%')) if '.' in number_format else 0
        return f'{value * 100:.{decimals}f}%'
    # Plain numbers drop a trailing ".0" (3.0 reads "3") but keep real decimals.
    return f'{value:g}'


def render_description(level: dict) -> str:
    '''
    Returns a skill or talent level's description with its values filled in
    and the formatting tags removed, as the game shows it.

    Placeholders for names that aren't in the blackboard are left as they
    are, so nothing is silently dropped.

    Args:
        level: One level of a skill or a talent candidate, with
            "description" and "blackboard".

    Returns:
        The readable description.
    '''
    raw_values = {
        entry['key'].lower(): entry['value'] for entry in level.get('blackboard') or [] if entry.get('value') is not None
    }

    def fill(match: re.Match) -> str:
        negate, raw_key, number_format = match.group(1) == '-', match.group(2).lower(), match.group(3)
        if raw_key not in raw_values:
            return match.group(0)
        return _format_value(raw_values[raw_key], negate, number_format)

    return PLACEHOLDER.sub(fill, strip_tags(level.get('description')))
