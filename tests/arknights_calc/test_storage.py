import json
import logging
import os
from datetime import datetime

import pandas as pd
import pytest

from arknights_calc.settings import Settings
from arknights_calc.storage import MissingColumnsError, Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / 'ref', tmp_path / 'io')


def test_from_settings_uses_ref_and_io_dirs(tmp_path):
    settings = Settings(project_root=tmp_path, user_email=None, ref_dir=tmp_path / 'r', io_dir=tmp_path / 'i',
                        log_dir=tmp_path / 'l')
    storage = Storage.from_settings(settings)
    assert (storage.ref_dir, storage.io_dir) == (tmp_path / 'r', tmp_path / 'i')


def test_accepts_string_paths(tmp_path):
    storage = Storage(str(tmp_path / 'ref'), str(tmp_path / 'io'))
    assert storage.raw_data_file == tmp_path / 'ref' / 'raw_data.json'


def test_file_locations(storage, tmp_path):
    assert storage.raw_data_file == tmp_path / 'ref' / 'raw_data.json'
    assert storage.elite2_operators_file == tmp_path / 'ref' / 'elite2_operators.csv'
    assert storage.operator_stats_file == tmp_path / 'ref' / 'operator_stats_output.csv'
    assert storage.operator_stats_view_file == tmp_path / 'io' / 'operator_stats_output.csv'
    assert storage.stage_enemy_stats_file('main_06-09') == tmp_path / 'io' / 'stage_main_06-09_enemy_stats.csv'
    assert storage.stage_matchups_file('main_06-09') == tmp_path / 'io' / 'stage_main_06-09_matchups.csv'


def test_save_raw_data_writes_4_space_json_and_overwrites(storage):
    storage.save_raw_data({'run': 1})
    assert storage.save_raw_data({'user': {'id': 1}}) == storage.raw_data_file
    assert storage.raw_data_file.read_text() == json.dumps({'user': {'id': 1}}, indent=4)


def test_elite2_operators_round_trip_without_index(storage):
    df = pd.DataFrame({'id': ['char_a'], 'potential': [1], 'level': [40], 'phase': [2], 'trust': [100.0], 'skill_level': [7]})
    assert storage.save_elite2_operators(df) == storage.elite2_operators_file
    assert storage.elite2_operators_file.read_text().splitlines()[0] == 'id,potential,level,phase,trust,skill_level'
    pd.testing.assert_frame_equal(storage.load_elite2_operators(), df, check_dtype=False)


def test_save_operator_stats_writes_ref_and_io_and_overwrites(storage):
    storage.save_operator_stats(pd.DataFrame({'operator_id': ['old']}))
    paths = storage.save_operator_stats(pd.DataFrame({'operator_id': ['a'], 'stunImmune': [False]}))
    assert paths == [storage.operator_stats_file, storage.operator_stats_view_file]
    for path in paths:
        assert path.read_text().splitlines() == ['operator_id,stunImmune', 'a,False']


def test_load_operator_stats_restores_types(storage):
    storage.save_operator_stats(pd.DataFrame({'operator_id': ['a'], 'level': [30], 'stunImmune': [False]}))
    df = storage.load_operator_stats()
    assert df['level'].dtype == 'int64'
    assert df['stunImmune'].dtype == 'bool'


def test_load_operator_stats_with_required_columns_present(storage):
    storage.save_operator_stats(pd.DataFrame({'operator_id': ['a'], 'atk': [1]}))
    assert storage.load_operator_stats(['operator_id', 'atk'])['atk'].tolist() == [1]


def test_load_operator_stats_lists_missing_columns(storage):
    storage.save_operator_stats(pd.DataFrame({'operator_id': ['a']}))
    with pytest.raises(MissingColumnsError) as exc:
        storage.load_operator_stats(['operator_id', 'atk', 'def'])
    assert exc.value.file_path == storage.operator_stats_file
    assert exc.value.missing_columns == ['atk', 'def']
    assert str(exc.value) == f"{storage.operator_stats_file} is missing columns ['atk', 'def']"


def test_save_stage_results_writes_both_files_to_io(storage):
    paths = storage.save_stage_results('main_06-09', pd.DataFrame({'enemy_id': ['e']}), pd.DataFrame({'operator_id': ['a']}))
    assert paths == [storage.stage_enemy_stats_file('main_06-09'), storage.stage_matchups_file('main_06-09')]
    assert paths[0].read_text().splitlines() == ['enemy_id', 'e']
    assert paths[1].read_text().splitlines() == ['operator_id', 'a']
    storage.save_stage_results('main_06-09', pd.DataFrame({'enemy_id': ['f']}), pd.DataFrame({'operator_id': ['b']}))
    assert paths[0].read_text().splitlines() == ['enemy_id', 'f']


def test_saving_creates_missing_parent_folders(tmp_path):
    storage = Storage(tmp_path / 'a' / 'b' / 'ref', tmp_path / 'c' / 'd' / 'io')
    storage.save_raw_data({})
    storage.save_operator_stats(pd.DataFrame({'operator_id': ['a']}))
    assert storage.raw_data_file.exists()
    assert storage.operator_stats_view_file.exists()


# roster info

def test_save_elite2_operators_records_when_and_how_many(storage):
    df = pd.DataFrame({'id': ['a', 'b']})
    storage.save_elite2_operators(df, saved_at=datetime(2026, 10, 24, 9, 30))
    assert storage.roster_info_file == storage.ref_dir / 'roster_info.json'
    assert json.loads(storage.roster_info_file.read_text()) == {'saved_at': '2026-10-24T09:30:00', 'operator_count': 2}
    assert storage.roster_saved_at() == datetime(2026, 10, 24, 9, 30)


def test_save_elite2_operators_defaults_to_now(storage):
    before = datetime.now()
    storage.save_elite2_operators(pd.DataFrame({'id': ['a']}))
    assert before <= storage.roster_saved_at() <= datetime.now()


def test_roster_saved_at_is_none_without_a_roster(storage):
    assert storage.roster_saved_at() is None


def test_roster_saved_at_falls_back_to_roster_file_time(storage):
    storage.elite2_operators_file.parent.mkdir(parents=True)
    storage.elite2_operators_file.write_text('id\na\n')
    os.utime(storage.elite2_operators_file, (1793000000, 1793000000))
    assert storage.roster_saved_at() == datetime.fromtimestamp(1793000000)


@pytest.mark.parametrize('content', ['not json', '{}', '{"saved_at": "bad date"}', '[]'])
def test_unreadable_roster_info_falls_back_to_roster_file_time(storage, caplog, content):
    storage.save_elite2_operators(pd.DataFrame({'id': ['a']}))
    os.utime(storage.elite2_operators_file, (1793000000, 1793000000))
    storage.roster_info_file.write_text(content)
    with caplog.at_level(logging.WARNING):
        assert storage.roster_saved_at() == datetime.fromtimestamp(1793000000)
    assert f'Ignoring unreadable {storage.roster_info_file}: ' in caplog.text
    assert not caplog.text.rstrip().endswith(': None')
