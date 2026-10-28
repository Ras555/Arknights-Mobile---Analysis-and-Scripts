import os
import sys

# Only the project root goes on sys.path, so tests import modules by their
# full names (e.g. lib.damage, app.runner), matching the names mutmut uses.
TEST_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import copy

import pytest


# Stats shared by every keyframe. Values that change between level 1 and
# max level are overridden per keyframe in make_operator_data.
BASE_OPERATOR_STATS = {
    'maxHp': 1000, 'atk': 300, 'def': 100, 'magicResistance': 0.0, 'cost': 10, 'blockCnt': 1,
    'moveSpeed': 1.0, 'attackSpeed': 100.0, 'baseAttackTime': 1.0, 'respawnTime': 70,
    'hpRecoveryPerSec': 0.0, 'spRecoveryPerSec': 1.0, 'maxDeployCount': 1, 'stunImmune': False,
}

ZERO_FAVOR_STATS = {
    'maxHp': 0, 'atk': 0, 'def': 0, 'magicResistance': 0.0, 'attackSpeed': 0.0, 'stunImmune': False,
}


def make_operator_data(
    name='Test Operator', profession='GUARD', description='Blocks 1 enemy', trait_candidates=None,
    phase_ranges=None, potential_ranks=None, favor_end=None,
):
    '''
    Builds a minimal HellaAPI operator "data" dict.

    phase_ranges is a list (one per phase) of (max_level, start_overrides,
    end_overrides) used to build each phase's level 1 and max level keyframes.
    '''
    if phase_ranges is None:
        phase_ranges = [
            (50, {'atk': 276, 'maxHp': 1000}, {'atk': 390, 'maxHp': 1490}),
            (70, {'atk': 400}, {'atk': 538}),
            (90, {'atk': 550}, {'atk': 640}),
        ]
    phases = []
    for max_level, start_overrides, end_overrides in phase_ranges:
        phases.append({
            'maxLevel': max_level,
            'attributesKeyFrames': [
                {'level': 1, 'data': {**BASE_OPERATOR_STATS, **start_overrides}},
                {'level': max_level, 'data': {**BASE_OPERATOR_STATS, **end_overrides}},
            ],
        })
    if potential_ranks is None:
        potential_ranks = [
            {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'COST', 'formulaItem': 'ADDITION', 'value': -1}]}}},
            {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'ATK', 'formulaItem': 'ADDITION', 'value': 20}]}}},
            {'buff': None},
            {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'MAX_HP', 'formulaItem': 'ADDITION', 'value': 100}]}}},
            {'buff': {'attributes': {'attributeModifiers': [{'attributeType': 'DEF', 'formulaItem': 'ADDITION', 'value': 10}]}}},
        ]
    if favor_end is None:
        favor_end = {'maxHp': 200, 'atk': 50, 'def': 20, 'magicResistance': 0.0, 'attackSpeed': 0.0, 'stunImmune': False}
    return {
        'name': name,
        'profession': profession,
        'subProfessionId': 'testsub',
        'rarity': 'TIER_5',
        'description': description,
        'trait': {'candidates': trait_candidates or []},
        'phases': phases,
        'potentialRanks': potential_ranks,
        'favorKeyFrames': [
            {'level': 0, 'data': copy.deepcopy(ZERO_FAVOR_STATS)},
            {'level': 50, 'data': favor_end},
        ],
    }


def defined(value):
    return {'m_defined': True, 'm_value': value}


def undefined(value=None):
    return {'m_defined': False, 'm_value': value}


def make_enemy_data(name='Test Enemy', damage_type=('PHYSIC',), level_overrides=None, apply_way='MELEE', base_attributes=None):
    '''
    Builds a minimal HellaAPI enemy dict with a full level 0 and, for each
    {level: attributes} in level_overrides, a sparse higher level.
    '''
    base = {
        'maxHp': 2000, 'atk': 200, 'def': 100, 'magicResistance': 20, 'moveSpeed': 1.0,
        'attackSpeed': 100, 'baseAttackTime': 2.0,
    }
    base.update(base_attributes or {})
    levels = [{
        'level': 0,
        'enemyData': {
            'attributes': {key: defined(value) for key, value in base.items()},
            'applyWay': defined(apply_way),
            'motion': defined('WALK'),
        },
    }]
    for level, attributes in (level_overrides or {}).items():
        levels.append({
            'level': level,
            'enemyData': {
                'attributes': {**{key: undefined(0) for key in base}, **{key: defined(value) for key, value in attributes.items()}},
                'applyWay': undefined(),
                'motion': undefined(),
            },
        })
    return {
        'excel': {'name': name, 'enemyLevel': 'NORMAL', 'damageType': list(damage_type) if damage_type is not None else None},
        'levels': {'Value': levels},
    }


@pytest.fixture
def operator_data():
    return make_operator_data()


@pytest.fixture
def enemy_data():
    return make_enemy_data()
