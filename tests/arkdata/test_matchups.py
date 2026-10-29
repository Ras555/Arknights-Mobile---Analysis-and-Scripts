import math

import pandas as pd
import pytest

from arkdata.calculations.matchups import REQUIRED_OPERATOR_COLUMNS, calculate_matchups
from arkdata.calculations.operator_stats import OperatorStats

from factories import make_enemy_stats, make_operator_data, operator_stats_row


def test_calculate_matchups_physical_vs_physical():
    operators = pd.DataFrame([operator_stats_row()])
    enemy = make_enemy_stats()  # atk 200, def 100, res 20, hp 2000, interval 2.0
    result = calculate_matchups(operators, [(enemy, 3)])
    row = result.iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(500)
    assert row['enemy_dps_to_operator'] == pytest.approx(25)
    assert row['seconds_for_operator_to_kill_enemy'] == pytest.approx(4)
    assert row['seconds_for_enemy_to_kill_operator'] == pytest.approx(120)
    assert row['enemy_spawn_count'] == 3
    assert row['operator_name'] == 'Guard'
    assert row['operator_damage_type'] == 'PHYSICAL'
    assert row['enemy_name'] == 'Test Enemy'
    assert row['enemy_level'] == 0
    assert row['enemy_damage_type'] == 'PHYSICAL'
    assert row['enemy_apply_way'] == 'MELEE'


def test_calculate_matchups_arts_both_ways():
    operators = pd.DataFrame([operator_stats_row(damage_type='ARTS', magicResistance=50)])
    enemy = make_enemy_stats(damage_type=['MAGIC'])
    row = calculate_matchups(operators, [(enemy, 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(480)
    assert row['enemy_dps_to_operator'] == pytest.approx(50)


def test_calculate_matchups_healer_and_harmless_enemy_never_kill():
    operators = pd.DataFrame([operator_stats_row(damage_type='HEAL')])
    enemy = make_enemy_stats(apply_way='NONE')
    row = calculate_matchups(operators, [(enemy, 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == 0
    assert row['enemy_dps_to_operator'] == 0
    assert math.isinf(row['seconds_for_operator_to_kill_enemy'])
    assert math.isinf(row['seconds_for_enemy_to_kill_operator'])


def test_calculate_matchups_one_row_per_pair():
    operators = pd.DataFrame([operator_stats_row(operator_id='a'), operator_stats_row(operator_id='b')])
    result = calculate_matchups(operators, [(make_enemy_stats(), 1), (make_enemy_stats(name='Other'), 2)])
    assert list(zip(result['operator_id'], result['enemy_name'])) == [
        ('a', 'Test Enemy'), ('a', 'Other'), ('b', 'Test Enemy'), ('b', 'Other')]


def test_calculate_matchups_empty():
    assert calculate_matchups(pd.DataFrame([operator_stats_row()]), []).empty


def test_calculate_matchups_includes_enemy_id():
    row = calculate_matchups(pd.DataFrame([operator_stats_row()]), [(make_enemy_stats(), 1)]).iloc[0]
    assert row['enemy_id'] == 'enemy_test'
    assert row['operator_id'] == 'char_a'


def test_calculate_matchups_kill_time_with_dps_below_one():
    # 100 ATK vs 100 DEF hits for the 5% floor (5) every 10 seconds: 0.5 DPS.
    operators = pd.DataFrame([operator_stats_row(atk=100, attack_interval=10.0)])
    row = calculate_matchups(operators, [(make_enemy_stats(), 1)]).iloc[0]
    assert row['operator_dps_to_enemy'] == pytest.approx(0.5)
    assert row['seconds_for_operator_to_kill_enemy'] == pytest.approx(4000)


def test_calculate_matchups_enemy_kill_time_with_dps_below_one():
    # 20 ATK vs 150 DEF hits for the 5% floor (1) every 2 seconds: 0.5 DPS.
    operators = pd.DataFrame([operator_stats_row()])
    row = calculate_matchups(operators, [(make_enemy_stats(base_attributes={'atk': 20}), 1)]).iloc[0]
    assert row['enemy_dps_to_operator'] == pytest.approx(0.5)
    assert row['seconds_for_enemy_to_kill_operator'] == pytest.approx(6000)


def test_required_operator_columns_are_produced_by_operator_stats():
    produced = OperatorStats('char_test', 1, 0, 0, operator_data=make_operator_data()).to_dict()
    assert [column for column in REQUIRED_OPERATOR_COLUMNS if column not in produced] == []
