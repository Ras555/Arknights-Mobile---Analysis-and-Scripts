#File Documentation
'''
File: settings.py
Description: Loads the program's settings: the account email and optional
    settings from the .env file, the folders the program reads from and
    writes to, and the HellaAPI client and cache settings.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import dotenv_values

from arkdata.sources.hella_api import (
    BULK_TIMEOUT_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_RETRY_DELAY_SECONDS,
    HELLA_API_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
)
from arkdata.sources.hella_cache import DEFAULT_BULK_THRESHOLD

# Constants

# src/arknights_calc/settings.py -> the project folder is two levels above src/.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CACHE_DAYS = 14
DEFAULT_ROSTER_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class Settings:
    '''
    The program's settings. Built by load_settings() and passed to whatever
    needs it, rather than read from module-level globals.

    ref_dir holds outputs the program reads back on later runs, io_dir holds
    outputs meant to be viewed by the user, and log_dir holds the log files.

    The HellaAPI settings control the API client (URL, timeouts, retries) and
    its disk cache (in ref_dir/cache): cache_max_age is the longest the cache
    goes without checking for changes, and cache_bulk_threshold is how many
    changed documents are re-downloaded one by one before the whole
    collection is downloaded instead. roster_max_age is how old the saved
    roster can be before the stats and matchups steps suggest refreshing it.
    '''
    project_root: Path
    user_email: str | None
    ref_dir: Path
    io_dir: Path
    log_dir: Path
    hella_api_url: str = HELLA_API_BASE_URL
    request_timeout: float = REQUEST_TIMEOUT_SECONDS
    bulk_timeout: float = BULK_TIMEOUT_SECONDS
    request_retries: int = DEFAULT_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS
    cache_max_age: timedelta = timedelta(days=DEFAULT_CACHE_DAYS)
    cache_bulk_threshold: int = DEFAULT_BULK_THRESHOLD
    roster_max_age: timedelta = timedelta(days=DEFAULT_ROSTER_MAX_AGE_DAYS)

    @property
    def log_file(self) -> Path:
        return self.log_dir / 'arknights.log'

    @property
    def cache_dir(self) -> Path:
        return self.ref_dir / 'cache'


def _env_value(env_values: Mapping[str, str | None], name: str) -> str | None:
    # The .env file takes priority over environment variables set in the shell.
    return env_values.get(name) or os.environ.get(name)


def _env_days(env_values: Mapping[str, str | None], name: str, default: int) -> timedelta:
    '''
    Returns the number of days set in name as a timedelta, or default days if
    it isn't set. Raises ValueError if it isn't a positive number.
    '''
    value = _env_value(env_values, name)
    if value is None:
        return timedelta(days=default)
    try:
        days = float(value)
    except ValueError:
        days = 0
    if days <= 0:
        raise ValueError(f'{name} must be a positive number of days, got {value!r}')
    return timedelta(days=days)


def load_settings(project_root: Path | None = None, env_file: Path | None = None) -> Settings:
    '''
    Returns the program's Settings.

    Each setting is read from the .env file in the project folder, falling
    back to the environment variable of the same name. The .env values are
    read without being copied into os.environ, so loading settings has no
    side effects. Settings:
        USER_EMAIL           the Yostar account email (no default)
        HELLA_API_URL        the HellaAPI base URL (default https://awedtan.ca/api)
        HELLA_CACHE_DAYS     days the HellaAPI cache can go without checking for changes (default 14)
        ROSTER_MAX_AGE_DAYS  days before a refresh of the saved roster is suggested (default 7)
    '''
    project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
    env_file = Path(env_file) if env_file is not None else project_root / '.env'
    env_values = dotenv_values(env_file) if env_file.exists() else {}

    return Settings(
        project_root=project_root,
        user_email=_env_value(env_values, 'USER_EMAIL'),
        ref_dir=project_root / 'ref',
        io_dir=project_root / 'io',
        log_dir=project_root / 'logs',
        hella_api_url=_env_value(env_values, 'HELLA_API_URL') or HELLA_API_BASE_URL,
        cache_max_age=_env_days(env_values, 'HELLA_CACHE_DAYS', DEFAULT_CACHE_DAYS),
        roster_max_age=_env_days(env_values, 'ROSTER_MAX_AGE_DAYS', DEFAULT_ROSTER_MAX_AGE_DAYS),
    )
