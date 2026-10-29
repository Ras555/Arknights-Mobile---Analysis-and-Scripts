#File Documentation
'''
File: settings.py
Description: Loads the program's settings (the account email from the .env
    file and the folders the program reads from and writes to).
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# Constants

# src/arknights_calc/settings.py -> the project folder is two levels above src/.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    '''
    The program's settings. Built by load_settings() and passed to whatever
    needs it, rather than read from module-level globals.

    ref_dir holds outputs the program reads back on later runs, io_dir holds
    outputs meant to be viewed by the user, and log_dir holds the log files.
    '''
    project_root: Path
    user_email: str | None
    ref_dir: Path
    io_dir: Path
    log_dir: Path

    @property
    def log_file(self) -> Path:
        return self.log_dir / 'arknights.log'


def load_settings(project_root: Path | None = None, env_file: Path | None = None) -> Settings:
    '''
    Returns the program's Settings.

    USER_EMAIL is read from the .env file in the project folder, falling back
    to the USER_EMAIL environment variable. The .env values are read without
    being copied into os.environ, so loading settings has no side effects.
    '''
    project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
    env_file = Path(env_file) if env_file is not None else project_root / '.env'
    env_values = dotenv_values(env_file) if env_file.exists() else {}

    return Settings(
        project_root=project_root,
        user_email=env_values.get('USER_EMAIL') or os.environ.get('USER_EMAIL'),
        ref_dir=project_root / 'ref',
        io_dir=project_root / 'io',
        log_dir=project_root / 'logs',
    )
