#File Documentation
'''
File: storage.py
Description: Reads and writes every file the program keeps in the ref/
    and io/ folders, so file names, formats, and required columns are
    defined in one place.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-03
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
ROSTER_FILE_NAME = 'roster.csv'
# When the roster was last saved, so the program can suggest refreshing an old one.
ROSTER_INFO_FILE_NAME = 'roster_info.json'
OPERATOR_STATS_FILE_NAME = 'operator_stats_output.csv'


class MissingColumnsError(Exception):
    '''
    Raised when a saved file is missing columns the program needs, usually
    because it was saved by an older version of an earlier step.
    '''

    def __init__(self, file_path: Path, missing_columns: list):
        '''
        Args:
            file_path: The file that was read.
            missing_columns: The required columns it doesn't have.
        '''
        # Kept as attributes so callers can build their own message.
        self.file_path = file_path
        self.missing_columns = missing_columns
        super().__init__(f'{file_path} is missing columns {missing_columns}')


def _save_csv(df: pd.DataFrame, file_path: Path) -> Path:
    '''
    Saves df to file_path as a CSV file (without the index), creating the
    folder if needed, and returns file_path.
    '''
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
        '''
        Args:
            ref_dir: The folder for files the program reads back.
            io_dir: The folder for files meant to be viewed.
        '''
        self.ref_dir = Path(ref_dir)
        self.io_dir = Path(io_dir)

    @classmethod
    def from_settings(cls, settings) -> 'Storage':
        '''
        Returns a Storage using the settings' ref/ and io/ folders.
        '''
        return cls(settings.ref_dir, settings.io_dir)

    # File paths. Each file's location is defined only here, so the steps
    # never build paths themselves.

    @property
    def raw_data_file(self) -> Path:
        return self.ref_dir / RAW_DATA_FILE_NAME

    @property
    def roster_file(self) -> Path:
        return self.ref_dir / ROSTER_FILE_NAME

    @property
    def roster_view_file(self) -> Path:
        return self.io_dir / ROSTER_FILE_NAME

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
        '''
        Saves the raw account data to ref/ and returns the file's path.
        '''
        # indent=4 makes the (large) file readable when opened by hand.
        self.raw_data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.raw_data_file, 'w') as f:
            f.write(json.dumps(raw_data, indent=4))
        return self.raw_data_file

    @property
    def roster_info_file(self) -> Path:
        return self.ref_dir / ROSTER_INFO_FILE_NAME

    def save_roster(self, df: pd.DataFrame, saved_at: datetime | None = None) -> list:
        '''
        Saves the roster to ref/ (read by the stats step) and io/ (for
        viewing), and records when it was saved (now, unless saved_at is
        given) and how many operators it has in roster_info.json.

        Args:
            df: The roster, one row per operator.
            saved_at: When the roster was saved; None uses the current time.

        Returns:
            The paths of the ref/ and io/ roster files.
        '''
        saved_at = saved_at or datetime.now()
        paths = [_save_csv(df, self.roster_file), _save_csv(df, self.roster_view_file)]
        with open(self.roster_info_file, 'w') as f:
            f.write(json.dumps({'saved_at': saved_at.isoformat(), 'operator_count': len(df)}, indent=4))
        return paths

    def roster_saved_at(self) -> datetime | None:
        '''
        Returns when the roster was last saved, or None if there is no saved
        roster. Rosters saved before roster_info.json existed fall back to the
        roster file's modified time.

        Returns:
            When the roster was saved, or None.
        '''
        # A missing roster_info.json is expected for older rosters, so only
        # an unreadable one is warned about. Either way, fall back below.
        try:
            with open(self.roster_info_file) as f:
                return datetime.fromisoformat(json.load(f)['saved_at'])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning('Ignoring unreadable %s: %s', self.roster_info_file, exc)
        if self.roster_file.exists():
            return datetime.fromtimestamp(self.roster_file.stat().st_mtime)
        return None

    def load_roster(self) -> pd.DataFrame:
        '''
        Returns the saved roster from ref/.
        '''
        return pd.read_csv(self.roster_file)

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

        Args:
            required_columns: The columns the caller needs.

        Returns:
            The operator stats.

        Raises:
            FileNotFoundError: If the stats haven't been saved yet.
            MissingColumnsError: If any required column is missing.
        '''
        # Checking the columns here gives one clear error up front, instead
        # of a KeyError part way through the caller's calculations.
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
