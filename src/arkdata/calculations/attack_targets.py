#File Documentation
'''
File: attack_targets.py
Description: Works out how many enemies an attack hits and how much of its
    damage each one takes, from a skill's own values and text or, failing
    that, from the operator's archetype (sub profession).
Created: 2026-11-04
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-09
Last Modified by: Claude
'''

# Imports

import re
from dataclasses import dataclass, field

# Constants

# Attacks that hit every enemy in an area have no target limit, so the number of enemies hit is assumed.
AOE_TARGETS = 3

# Archetypes whose basic attacks are AoE, from their trait text (checked 2026-11-04):
#   aoesniper (Artilleryman): "Deals AOE Physical damage"
#   blastcaster: "Deals AOE Arts damage in a long line"
#   splashcaster: "Deals AOE Arts damage"
#   bombarder (Flinger): two instances of Physical damage "to ground enemies in a small area"
#   reaper: "Attacks deal AoE damage"
#   reaperrange (Spreadshooter): "Attacks all enemies within range"
#   stalker (Ambusher): "Deals Damage to all targets within range"
#   fortress: "When not blocking enemies, prioritizes dealing ranged AoE Physical damage"
#   hammer (Earthshaker): splash damage "to enemies around the target" (see HAMMER below)
AOE_SUB_PROFESSIONS = frozenset({
    "aoesniper", "blastcaster", "splashcaster", "bombarder", "reaper", "reaperrange", "stalker", "fortress", "hammer",
})
# Archetypes that attack every enemy they block ("Attacks all blocked enemies"), so they hit as many enemies as
# their block count: centurion, crusher, and pusher ("Can attack all blocked enemies").
BLOCK_TARGET_SUB_PROFESSIONS = frozenset({"centurion", "crusher", "pusher"})
# Archetypes that don't attack outside their skills: phalanx ("Normally does not attack") and librator (Liberator,
# "Normally does not attack and has 0 Block").
NO_BASIC_ATTACK_SUB_PROFESSIONS = frozenset({"phalanx", "librator"})

# Chain Casters' attacks "jump between {max_target} enemies. Each jump deals 15% less damage".
CHAIN_SUB_PROFESSION = "chain"
CHAIN_JUMP_DAMAGE = 0.85
DEFAULT_CHAIN_TARGETS = 4
# Earthshakers hit their target for full damage and the enemies around it for atk_scale_2 (50%) of ATK.
HAMMER_SUB_PROFESSION = "hammer"
DEFAULT_SPLASH_SCALE = 0.5
# Flingers add "a shockwave that has half the normal ATK" (append_atk_scale) to every attack.
BOMBARDER_SUB_PROFESSION = "bombarder"
DEFAULT_SHOCKWAVE_SCALE = 0.5
# Swordmasters' "Normal attacks deal damage twice": two full strikes per attack.
SUB_PROFESSION_STRIKES = {"sword": (1.0, 1.0)}

# Skill text that means the skill hits every enemy in an area rather than a set number.
AOE_WORDS = re.compile(
    r'\bAO[Ee]\b|all (nearby |surrounding |blocked )?enemies|surrounding enemies|nearby enemies|enemies (around|nearby)'
    r'|all targets|every enemy',
    re.IGNORECASE,
)
# Skill text for a hit that also splashes half its damage around the target ("Deals half damage to enemies around
# the target", Eyjafjalla's Ignition). It can be in a different clause from the multiplier.
HALF_SPLASH = re.compile(
    r'half (?:the )?damage to (?:the )?(?:enemies|targets) (?:around|near|surrounding)|half damage to (?:nearby|surrounding) '
    r'enemies',
    re.IGNORECASE,
)
HALF_SPLASH_SCALE = 0.5


@dataclass(frozen=True)
class Targets:
    '''
    How many enemies one attack hits and how its damage is shared out.

    count is the number of enemies hit. damage_share is the sum, over
    those enemies, of the fraction of the attack's damage each one takes:
    count for an even AoE, 1 + 2 * 0.5 = 2.0 for an Earthshaker's splash
    on 3 enemies. aoe is True when there is no target limit (count is then
    AOE_TARGETS, an assumption). note explains anything unusual.

    scales holds the fraction each enemy takes, the main target first,
    when they differ ((1, 0.5, 0.5) for an Earthshaker's splash, (1, 0.85,
    0.7225, ...) for a Chain Caster); it's empty when every enemy takes the
    full damage. It's left out of comparisons, since damage_share already
    sums it.
    '''
    count: int = 1
    damage_share: float = 1.0
    aoe: bool = False
    note: str = ''
    scales: tuple[float, ...] = field(default=(), compare=False)

    @property
    def target_scales(self) -> tuple[float, ...]:
        '''
        The fraction of the damage each enemy hit takes, the main target
        first: scales, or 1 for each of count enemies.
        '''
        return self.scales or (1.0,) * self.count


def _blackboard_value(trait_blackboard: dict | None, key: str, default: float) -> float:
    '''
    Returns a value from a trait's blackboard, ignoring the "attack@" prefix.

    Args:
        trait_blackboard: {name: value} from the trait, or None.
        key: The name without the prefix.
        default: The value to use if it's missing.

    Returns:
        The value, or default.
    '''
    for name, value in (trait_blackboard or {}).items():
        if name.removeprefix('attack@') == key:
            return value
    return default


def splash_targets(splash: float, note: str) -> Targets:
    '''
    Returns the Targets of an attack that hits its target in full and
    splashes a fraction of its damage on the enemies around it (assumed to
    be AOE_TARGETS enemies in all, as for other AoE).

    Args:
        splash: The fraction of the damage the others take (0.5 for half).
        note: The note to show with the targets.
    '''
    return Targets(AOE_TARGETS, 1 + (AOE_TARGETS - 1) * splash, aoe=True, note=note,
                   scales=(1.0,) + (splash,) * (AOE_TARGETS - 1))


def basic_attack_targets(sub_profession: str | None, block_count: int, trait_blackboard: dict | None = None) -> Targets:
    '''
    Returns the enemies an operator's basic attack hits, from its archetype.

    Args:
        sub_profession: The operator's subProfessionId.
        block_count: The operator's current block count (blockCnt).
        trait_blackboard: {name: value} from the operator's trait, used for
            the Chain Caster jump count and Earthshaker splash.

    Returns:
        The Targets of one basic attack.
    '''
    if sub_profession in BLOCK_TARGET_SUB_PROFESSIONS:
        # At least 1, so an operator whose block is reduced to 0 still hits its target.
        count = max(int(block_count), 1)
        return Targets(count, float(count), note=f'attacks all blocked enemies (block {count})')
    if sub_profession == CHAIN_SUB_PROFESSION:
        count = int(_blackboard_value(trait_blackboard, 'max_target', DEFAULT_CHAIN_TARGETS))
        # Each jump deals 15% less than the one before: 1 + 0.85 + 0.85^2 + ...
        scales = tuple(CHAIN_JUMP_DAMAGE ** jump for jump in range(count))
        return Targets(count, sum(scales), note=f'jumps between {count} enemies, 15% less damage per jump', scales=scales)
    if sub_profession == HAMMER_SUB_PROFESSION:
        splash = _blackboard_value(trait_blackboard, 'atk_scale_2', DEFAULT_SPLASH_SCALE)
        return splash_targets(splash, f'AoE (no target limit): {splash:.0%} ATK splash to enemies around the target')
    if sub_profession in AOE_SUB_PROFESSIONS:
        note = 'AoE (no target limit)'
        if sub_profession == 'fortress':
            note += ', only while not blocking'
        return Targets(AOE_TARGETS, float(AOE_TARGETS), aoe=True, note=note)
    return Targets()


def basic_attack_strikes(sub_profession: str | None, trait_blackboard: dict | None = None) -> tuple[float, ...]:
    '''
    Returns the strikes one basic attack makes on each enemy it hits, as
    fractions of ATK: (1, 1) for Swordmasters, (1, 0.5) for Flingers (a
    full hit plus a half-ATK shockwave), and (1,) otherwise. Each strike is
    reduced by DEF or RES on its own, so a weak shockwave loses more to DEF
    than its share of a whole attack would.

    Args:
        sub_profession: The operator's subProfessionId.
        trait_blackboard: {name: value} from the operator's trait.

    Returns:
        The fraction of ATK of each strike.
    '''
    if sub_profession == BOMBARDER_SUB_PROFESSION:
        return (1.0, _blackboard_value(trait_blackboard, 'append_atk_scale', DEFAULT_SHOCKWAVE_SCALE))
    return SUB_PROFESSION_STRIKES.get(sub_profession or '', (1.0,))


def basic_attack_hits(sub_profession: str | None, trait_blackboard: dict | None = None) -> float:
    '''
    Returns how many times ATK one basic attack deals to each enemy it hits,
    before any skill: the sum of its strikes (basic_attack_strikes), so 2
    for Swordmasters, 1.5 for Flingers, and 1 otherwise.

    Args:
        sub_profession: The operator's subProfessionId.
        trait_blackboard: {name: value} from the operator's trait.

    Returns:
        The ATK multiple per enemy hit.
    '''
    return sum(basic_attack_strikes(sub_profession, trait_blackboard))


def skill_targets(max_target: float | None, description: str, basic: Targets) -> Targets:
    '''
    Returns the enemies an attack hits while a skill is active.

    The order is: the skill's own target limit (max_target), then AoE
    wording in the skill text (assumed AOE_TARGETS enemies), then the
    archetype's basic attack targets.

    Args:
        max_target: The skill's max_target value, or None.
        description: The skill's description without formatting tags.
        basic: The archetype's basic attack Targets.

    Returns:
        The Targets while the skill is active.
    '''
    if max_target is not None:
        count = max(int(max_target), 1)
        return Targets(count, float(count), note=f'hits up to {count} enemies')
    if AOE_WORDS.search(description):
        return Targets(AOE_TARGETS, float(AOE_TARGETS), aoe=True, note='AoE (no target limit)')
    return basic
