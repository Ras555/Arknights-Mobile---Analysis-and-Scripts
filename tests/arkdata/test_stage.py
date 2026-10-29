import logging

import pytest

from arkdata.calculations.stage import count_enemy_spawns, load_stage_enemies

# count_enemy_spawns

def test_count_enemy_spawns():
    waves = [
        {'fragments': [{'actions': [
            {'actionType': 'SPAWN', 'key': 'e1', 'count': 3},
            {'actionType': 'SPAWN', 'key': 'e2'},
            {'actionType': 'STORY', 'key': 'e1', 'count': 9},
        ]}]},
        {'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 2}]}, {'actions': None}]},
        {'fragments': None},
    ]
    assert count_enemy_spawns(waves) == {'e1': 5, 'e2': 1}


@pytest.mark.parametrize('waves', [None, []])
def test_count_enemy_spawns_empty(waves):
    assert count_enemy_spawns(waves) == {}


# load_stage_enemies

def test_load_stage_enemies_skips_non_db_enemies(caplog):
    stage = {'levels': {
        'waves': [{'fragments': [{'actions': [{'actionType': 'SPAWN', 'key': 'e1', 'count': 4}]}]}],
        'enemyDbRefs': [
            {'useDb': True, 'id': 'e1', 'level': 1, 'overwrittenData': {'x': 1}},
            {'useDb': False, 'id': 'custom'},
            {'useDb': True, 'id': 'e2'},
        ],
    }}
    made = []

    def fake_make(enemy_id, level, overwritten):
        made.append((enemy_id, level, overwritten))
        return enemy_id

    with caplog.at_level(logging.WARNING):
        result = load_stage_enemies(stage, fake_make)
    assert made == [('e1', 1, {'x': 1}), ('e2', 0, None)]
    assert result == [('e1', 4), ('e2', 0)]
    assert 'Skipping custom' in caplog.text


def test_load_stage_enemies_no_refs():
    assert load_stage_enemies({'levels': {}}, lambda *a: None) == []
