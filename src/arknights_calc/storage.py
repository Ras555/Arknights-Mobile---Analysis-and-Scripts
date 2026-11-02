#File Documentation
'''
File: storage.py
Description: Reads and writes every file the program keeps in the ref/
    and io/ folders, so file names, formats, and required columns are
    defined in one place.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Constants

RAW_DATA_FILE_NAME = 'raw_data.json'
ELITE2_OPERATORS_FILE_NAME = 'elite2_operators.csv'
# When the roster was last saved, so the program can suggest refreshing an old one.
ROSTER_INFO_FILE_NAME = 'roster_info.json'
OPERATOR_STATS_FILE_NAME = 'operator_stats_output.csv'


class MissingColumnsError(Exception):
    '''
    Raised when a saved file is missing columns the program needs, usually
    because it was saved by an older version of an earlier step.
    '''

    def __init__(self, file_path: Path, missing_columns: list):
        self.file_path = file_path
        self.missing_columns = missing_columns
        super().__init__(f'{file_path} is missing columns {missing_columns}')


def _save_csv(df: pd.DataFrame, file_path: Path) -> Path:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(file_path, index=False)
    return file_path


class Storage:
    '''
    Reads and writes the program's files. ref_dir holds outputs the program
    reads back on later runs; io_dir holds outputs meant to be viewed by the
    user. Some files are written to both.
    '''

    def __init__(self, ref_dir: Path, io_dir: Path):
        self.ref_dir = Path(ref_dir)
        self.io_dir = Path(io_dir)

    @classmethod
    def from_settings(cls, settings) -> 'Storage':
        return cls(settings.ref_dir, settings.io_dir)

    @property
    def raw_data_file(self) -> Path:
        return self.ref_dir / RAW_DATA_FILE_NAME

    @property
    def elite2_operators_file(self) -> Path:
        return self.ref_dir / ELITE2_OPERATORS_FILE_NAME

    @property
    def operator_stats_file(self) -> Path:
        return self.ref_dir / OPERATOR_STATS_FILE_NAME

    @property
    def operator_stats_view_file(self) -> Path:
        return self.io_dir / OPERATOR_STATS_FILE_NAME

    def stage_enemy_stats_file(self, stage_id: str) -> Path:
        return self.io_dir / f'stage_{stage_id}_enemy_stats.csv'

    def stage_matchups_file(self, stage_id: str) -> Path:
        return self.io_dir / f'stage_{stage_id}_matchups.csv'

    def save_raw_data(self, raw_data: dict) -> Path:
        self.raw_data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.raw_data_file, 'w') as f:
            f.write(json.dumps(raw_data, indent=4))
        return self.raw_data_file

    @property
    def roster_info_file(self) -> Path:
        return self.ref_dir / ROSTER_INFO_FILE_NAME

    def save_elite2_operators(self, df: pd.DataFrame, saved_at: datetime | None = None) -> Path:
        '''
        Saves the Elite 2 roster, and records when it was saved (now, unless
        saved_at is given) and how many operators it has in roster_info.json.
        '''
        saved_at = saved_at or datetime.now()
        path = _save_csv(df, self.elite2_operators_file)
        with open(self.roster_info_file, 'w') as f:
            f.write(json.dumps({'saved_at': saved_at.isoformat(), 'operator_count': len(df)}, indent=4))
        return path

    def roster_saved_at(self) -> datetime | None:
        '''
        Returns when the roster was last saved, or None if there is no saved
        roster. Rosters saved before roster_info.json existed fall back to the
        roster file's modified time.
        '''
        try:
            with open(self.roster_info_file) as f:
                return datetime.fromisoformat(json.load(f)['saved_at'])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning('Ignoring unreadable %s: %s', self.roster_info_file, exc)
        if self.elite2_operators_file.exists():
            return datetime.fromtimestamp(self.elite2_operators_file.stat().st_mtime)
        return None

    def load_elite2_operators(self) -> pd.DataFrame:
        return pd.read_csv(self.elite2_operators_file)

    def save_operator_stats(self, df: pd.DataFrame) -> list:
        '''
        Saves the operator stats to ref/ (read by the matchups step) and io/
        (for viewing). Returns both paths.
        '''
        return [_save_csv(df, self.operator_stats_file), _save_csv(df, self.operator_stats_view_file)]

    def load_operator_stats(self, required_columns=()) -> pd.DataFrame:
        '''
        Loads the operator stats from ref/. Raises MissingColumnsError if any
        of required_columns are missing.
        '''
        df = pd.read_csv(self.operator_stats_file)
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            raise MissingColumnsError(self.operator_stats_file, missing_columns)
        return df

    def save_stage_results(self, stage_id: str, enemy_df: pd.DataFrame, matchups: pd.DataFrame) -> list:
        '''
        Saves a stage's enemy stats and matchups to io/. Returns both paths.
        '''
        return [
            _save_csv(enemy_df, self.stage_enemy_stats_file(stage_id)),
            _save_csv(matchups, self.stage_matchups_file(stage_id)),
        ]
