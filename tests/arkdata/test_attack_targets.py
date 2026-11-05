import pytest

from arkdata.calculations.attack_targets import (
    AOE_SUB_PROFESSIONS,
    AOE_TARGETS,
    BLOCK_TARGET_SUB_PROFESSIONS,
    NO_BASIC_ATTACK_SUB_PROFESSIONS,
    Targets,
    basic_attack_hits,
    basic_attack_targets,
    skill_targets,
)


def test_constants():
    assert AOE_TARGETS == 3
    assert {'aoesniper', 'blastcaster', 'splashcaster', 'bombarder', 'reaper', 'reaperrange', 'stalker',
            'fortress', 'hammer'} == AOE_SUB_PROFESSIONS
    assert {'centurion', 'crusher', 'pusher'} == BLOCK_TARGET_SUB_PROFESSIONS
    assert {'phalanx', 'librator'} == NO_BASIC_ATTACK_SUB_PROFESSIONS


# basic_attack_targets

def test_single_target_archetypes_hit_one_enemy():
    assert basic_attack_targets('pioneer', 2) == Targets(1, 1.0, False, '')
    assert basic_attack_targets(None, 1) == Targets()


@pytest.mark.parametrize('sub_profession', ['aoesniper', 'splashcaster', 'blastcaster', 'reaper', 'stalker'])
def test_aoe_archetypes_assume_three_enemies(sub_profession):
    assert basic_attack_targets(sub_profession, 1) == Targets(3, 3.0, True, 'AoE (no target limit)')


def test_fortress_aoe_only_while_not_blocking():
    assert basic_attack_targets('fortress', 3).note == 'AoE (no target limit), only while not blocking'


@pytest.mark.parametrize('block, expected', [(3, 3), (2, 2), (0, 1)])
def test_block_count_archetypes_hit_every_blocked_enemy(block, expected):
    targets = basic_attack_targets('centurion', block)
    assert (targets.count, targets.damage_share, targets.aoe) == (expected, float(expected), False)
    assert targets.note == f'attacks all blocked enemies (block {expected})'


def test_chain_caster_jumps_lose_15_percent_each():
    # 4 enemies: 1 + 0.85 + 0.7225 + 0.614125 = 3.186625
    targets = basic_attack_targets('chain', 1, {'attack@max_target': 4})
    assert targets.count == 4
    assert targets.damage_share == pytest.approx(3.186625)
    assert targets.note == 'jumps between 4 enemies, 15% less damage per jump'


def test_chain_caster_defaults_to_four_jumps():
    assert basic_attack_targets('chain', 1).count == 4


def test_earthshaker_splash_uses_trait_scale():
    # Main target 100% plus 2 others at 50%: 1 + 2 * 0.5 = 2.0
    targets = basic_attack_targets('hammer', 1, {'attack@atk_scale_2': 0.5, 'attack@ability_range_radius': 1})
    assert (targets.count, targets.damage_share, targets.aoe) == (3, 2.0, True)
    assert targets.note == 'AoE (no target limit): 50% ATK splash to enemies around the target'
    assert basic_attack_targets('hammer', 1, {'atk_scale_2': 0.6}).damage_share == pytest.approx(2.2)
    assert basic_attack_targets('hammer', 1).damage_share == 2.0


# basic_attack_hits

def test_basic_attack_hits():
    assert basic_attack_hits('sword') == 2
    assert basic_attack_hits('bombarder', {'attack@append_atk_scale': 0.5}) == 1.5
    assert basic_attack_hits('bombarder', {'append_atk_scale': 0.4}) == 1.4
    assert basic_attack_hits('bombarder') == 1.5
    assert basic_attack_hits('pioneer') == 1
    assert basic_attack_hits(None) == 1


# skill_targets

def test_skill_target_limit_comes_first():
    assert skill_targets(5, 'all enemies', Targets(3, 3.0, True)) == Targets(5, 5.0, False, 'hits up to 5 enemies')
    assert skill_targets(0, '', Targets()).count == 1


@pytest.mark.parametrize('description', [
    'dealing 350% ATK to all enemies hit', 'Deals AOE Physical damage', 'to surrounding enemies',
    'damages nearby enemies', 'to enemies around it', 'Cuts all targets in the frontal tile',
])
def test_aoe_wording_assumes_three_enemies(description):
    assert skill_targets(None, description, Targets()) == Targets(3, 3.0, True, 'AoE (no target limit)')


def test_otherwise_the_basic_attack_targets_are_kept():
    basic = Targets(2, 2.0, False, 'attacks all blocked enemies (block 2)')
    assert skill_targets(None, 'ATK +100%', basic) is basic
