import pytest

import arkdata.calculations.enemy_stats as enemy_stats_module
from arkdata.calculations.enemy_stats import ENEMY_DAMAGE_TYPES, EnemyStats, _defined_values, _fix_mojibake, _min_block
from factories import defined, load_enemy_fixture, make_enemy_data, undefined

# helpers

def test_fix_mojibake_repairs_latin1_decoded_utf8():
    assert _fix_mojibake('Î±') == 'α'


def test_fix_mojibake_leaves_plain_text_alone():
    assert _fix_mojibake('Frostfang') == 'Frostfang'


def test_fix_mojibake_leaves_unencodable_text_alone():
    assert _fix_mojibake('α') == 'α'


def test_fix_mojibake_leaves_invalid_utf8_alone():
    assert _fix_mojibake('é') == 'é'


def test_defined_values_keeps_only_defined_fields():
    fields = {'atk': defined(100), 'def': undefined(50), 'bad': 'not a dict', 'off': {'m_value': 3}}
    assert _defined_values(fields) == {'atk': 100}


@pytest.mark.parametrize('fields', [None, {}])
def test_defined_values_empty_input(fields):
    assert _defined_values(fields) == {}


def test_enemy_damage_type_map():
    assert ENEMY_DAMAGE_TYPES == {'PHYSIC': 'PHYSICAL', 'MAGIC': 'ARTS', 'HEAL': 'HEAL'}


# EnemyStats

def test_level_zero_stats(enemy_data):
    enemy = EnemyStats('enemy_test', enemy_data=enemy_data)
    assert enemy.enemy_id == 'enemy_test'
    assert enemy.name == 'Test Enemy'
    assert enemy.enemy_tier == 'NORMAL'
    assert enemy.level == 0
    assert enemy.stats['maxHp'] == 2000
    assert enemy.apply_way == 'MELEE'
    assert enemy.motion == 'WALK'
    assert enemy.damage_type == 'PHYSICAL'


def test_fetches_enemy_data_when_not_given(monkeypatch, enemy_data):
    calls = []
    monkeypatch.setattr(enemy_stats_module, 'fetch_enemy_data', lambda key: calls.append(key) or enemy_data)
    EnemyStats('enemy_test')
    assert calls == ['enemy_test']


def test_higher_level_overrides_defined_fields_and_falls_back_to_level_zero():
    data = make_enemy_data(level_overrides={1: {'atk': 300, 'maxHp': 3000}})
    enemy = EnemyStats('enemy_test', level=1, enemy_data=data)
    assert enemy.stats['atk'] == 300
    assert enemy.stats['maxHp'] == 3000
    assert enemy.stats['def'] == 100
    assert enemy.apply_way == 'MELEE'
    assert enemy.motion == 'WALK'


def test_missing_level_raises(enemy_data):
    with pytest.raises(ValueError, match=r'has levels \[0\], got level 2'):
        EnemyStats('enemy_test', level=2, enemy_data=enemy_data)


def test_overwritten_data_applied_last():
    data = make_enemy_data(level_overrides={1: {'atk': 300}})
    overwritten = {'attributes': {'atk': defined(999), 'def': undefined(1)}, 'applyWay': defined('RANGED')}
    enemy = EnemyStats('enemy_test', level=1, overwritten_data=overwritten, enemy_data=data)
    assert enemy.stats['atk'] == 999
    assert enemy.stats['def'] == 100
    assert enemy.apply_way == 'RANGED'
    assert enemy.motion == 'WALK'


def test_name_mojibake_fixed():
    enemy = EnemyStats('enemy_test', enemy_data=make_enemy_data(name='Î±'))
    assert enemy.name == 'α'


@pytest.mark.parametrize('damage_type, expected', [
    (['MAGIC'], 'ARTS'), (['HEAL'], 'HEAL'), (['PHYSIC', 'MAGIC'], 'PHYSICAL'), (['OTHER'], 'OTHER'),
    ([], 'NONE'), (None, 'NONE'),
])
def test_damage_type(damage_type, expected):
    enemy = EnemyStats('enemy_test', enemy_data=make_enemy_data(damage_type=damage_type))
    assert enemy.damage_type == expected


def test_attack_interval_and_dps(enemy_data):
    enemy = EnemyStats('enemy_test', enemy_data=enemy_data)
    assert enemy.attack_interval == pytest.approx(2.0)
    assert enemy.dps == pytest.approx(100)


def test_attack_speed_shortens_interval():
    enemy = EnemyStats('enemy_test', enemy_data=make_enemy_data(base_attributes={'attackSpeed': 200}))
    assert enemy.attack_interval == pytest.approx(1.0)
    assert enemy.dps == pytest.approx(200)


def test_dps_zero_when_apply_way_none():
    enemy = EnemyStats('enemy_test', enemy_data=make_enemy_data(apply_way='NONE'))
    assert enemy.dps == 0.0


@pytest.mark.parametrize('damage_type', [['HEAL'], []])
def test_dps_zero_for_heal_or_no_damage_type(damage_type):
    enemy = EnemyStats('enemy_test', enemy_data=make_enemy_data(damage_type=damage_type))
    assert enemy.dps == 0.0


def test_to_dict(enemy_data):
    enemy = EnemyStats('enemy_test', enemy_data=enemy_data)
    result = enemy.to_dict()
    assert result['enemy_id'] == 'enemy_test'
    assert result['name'] == 'Test Enemy'
    assert result['enemy_tier'] == 'NORMAL'
    assert result['level'] == 0
    assert result['damage_type'] == 'PHYSICAL'
    assert result['apply_way'] == 'MELEE'
    assert result['motion'] == 'WALK'
    assert (result['level_type'], result['tags'], result['weight'], result['min_block']) == ('NORMAL', '', 0, 1)
    assert result['atk'] == 200
    assert result['attack_interval'] == pytest.approx(2.0)
    assert result['dps'] == pytest.approx(100)


def test_apply_way_and_motion_are_none_when_never_defined():
    data = make_enemy_data()
    data['levels']['Value'][0]['enemyData']['applyWay'] = undefined('MELEE')
    del data['levels']['Value'][0]['enemyData']['motion']
    enemy = EnemyStats('enemy_test', enemy_data=data)
    assert enemy.apply_way is None
    assert enemy.motion is None


def test_enemy_damage_types_are_damage_type_members():
    from arkdata.calculations.damage import DamageType
    assert all(isinstance(value, DamageType) for value in ENEMY_DAMAGE_TYPES.values())
    assert EnemyStats('enemy_test', enemy_data=make_enemy_data(damage_type=[])).damage_type is DamageType.NONE


def test_apply_way_none_constant():
    from arkdata.calculations.enemy_stats import APPLY_WAY_NONE
    assert APPLY_WAY_NONE == 'NONE'


# Tags, level type, weight and blocking (real enemies in tests/fixtures/enemies)

@pytest.mark.parametrize('abilities, expected', [
    ([], 1),
    (['Flying unit.'], 1),
    (['Unblockable; can be attacked by enemies', 'Drops gear'], None),
    (['Cannot be blocked.'], None),
    (['Can only be blocked by Operators with 3 or more Block'], 3),
    (["Can only be blocked by units with at least 2 Block, and deals Physical damage"], 2),
    # Only unblockable for a while: still blockable.
    (['Flees when HP is under 50%! Gains bonus Movement Speed and cannot be blocked for a period of time.'], 1),
    (['Cannot be blocked while on Nethersea Brand'], 1),
    (['Stops attacking and cannot be blocked when Invisible'], 1),
    (['Flees for a period of time', 'Unblockable'], None),
])
def test_min_block(abilities, expected):
    assert _min_block(abilities) == expected


def test_real_drone():
    enemy = load_enemy_fixture('monster_mk_ii')
    assert (enemy.flying, enemy.elite, enemy.tags, enemy.weight, enemy.level_type) == (True, False, ['drone'], 0, 'NORMAL')
    # Aerial enemies can't be blocked (by operators that haven't Taken Off).
    assert not enemy.blockable_by(3)
    # Level 1 keeps level 0's tags.
    assert load_enemy_fixture('monster_mk_ii', level=1).tags == ['drone']


def test_real_heavy_elite_sarkaz():
    enemy = load_enemy_fixture('sarkaz_lancer')
    assert (enemy.elite, enemy.tags, enemy.weight, enemy.min_block) == (True, ['sarkaz'], 3, 1)
    assert enemy.blockable_by(1)


def test_real_blocking_rules():
    assert load_enemy_fixture('freight_car').min_block is None
    assert not load_enemy_fixture('freight_car').blockable_by(5)
    seller = load_enemy_fixture('carnevale_snack_seller')
    assert (seller.min_block, seller.blockable_by(2), seller.blockable_by(3), seller.tags) == (3, False, True, [])
    assert load_enemy_fixture('nethersea_predator').blockable_by(1)


def test_level_type_from_the_level_data():
    data = make_enemy_data()
    data['levels']['Value'][0]['enemyData']['levelType'] = defined('BOSS')
    assert EnemyStats('enemy_test', enemy_data=data).elite

