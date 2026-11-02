import os
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path

import pytest

import arknights_calc.settings as settings_module
from arknights_calc.settings import Settings, load_settings


def test_project_root_is_the_repository_folder():
    assert (settings_module.PROJECT_ROOT / 'pyproject.toml').exists()
    assert (settings_module.PROJECT_ROOT / 'src' / 'arknights_calc').is_dir()


def test_load_settings_reads_email_from_env_file_and_builds_paths(tmp_path, monkeypatch):
    monkeypatch.delenv('USER_EMAIL', raising=False)
    (tmp_path / '.env').write_text('USER_EMAIL=me@example.com\n')

    settings = load_settings(tmp_path)

    assert settings == Settings(project_root=tmp_path, user_email='me@example.com', ref_dir=tmp_path / 'ref',
                                io_dir=tmp_path / 'io', log_dir=tmp_path / 'logs')
    assert settings.log_file == tmp_path / 'logs' / 'arknights.log'


def test_load_settings_does_not_change_os_environ(tmp_path, monkeypatch):
    monkeypatch.delenv('USER_EMAIL', raising=False)
    (tmp_path / '.env').write_text('USER_EMAIL=me@example.com\n')
    load_settings(tmp_path)
    assert 'USER_EMAIL' not in os.environ


def test_env_file_takes_priority_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('USER_EMAIL', 'shell@example.com')
    (tmp_path / '.env').write_text('USER_EMAIL=file@example.com\n')
    assert load_settings(tmp_path).user_email == 'file@example.com'


def test_environment_used_when_env_file_missing_or_has_no_email(tmp_path, monkeypatch):
    monkeypatch.setenv('USER_EMAIL', 'shell@example.com')
    assert load_settings(tmp_path).user_email == 'shell@example.com'
    (tmp_path / '.env').write_text('OTHER=1\n')
    assert load_settings(tmp_path).user_email == 'shell@example.com'


def test_no_email_anywhere_gives_none(tmp_path, monkeypatch):
    monkeypatch.delenv('USER_EMAIL', raising=False)
    assert load_settings(tmp_path).user_email is None


def test_custom_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv('USER_EMAIL', raising=False)
    env_file = tmp_path / 'other.env'
    env_file.write_text('USER_EMAIL=other@example.com\n')
    assert load_settings(tmp_path, env_file=str(env_file)).user_email == 'other@example.com'


def test_defaults_to_project_root(monkeypatch):
    monkeypatch.setattr(settings_module, 'PROJECT_ROOT', Path('/project'))
    settings = load_settings(env_file=Path('/does/not/exist.env'))
    assert settings.project_root == Path('/project')
    assert settings.ref_dir == Path('/project/ref')


def test_settings_are_frozen(tmp_path):
    with pytest.raises(FrozenInstanceError):
        load_settings(tmp_path).user_email = 'changed'


# HellaAPI and roster settings

def test_hella_and_roster_defaults(tmp_path, monkeypatch):
    for name in ('HELLA_API_URL', 'HELLA_CACHE_DAYS', 'ROSTER_MAX_AGE_DAYS'):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(tmp_path)
    assert settings.hella_api_url == 'https://awedtan.ca/api'
    assert (settings.request_timeout, settings.bulk_timeout, settings.request_retries, settings.retry_delay) == (10, 120, 3, 1.0)
    assert settings.cache_max_age == timedelta(days=14)
    assert settings.cache_bulk_threshold == 40
    assert settings.roster_max_age == timedelta(days=7)
    assert settings.cache_dir == tmp_path / 'ref' / 'cache'
    assert (settings_module.DEFAULT_CACHE_DAYS, settings_module.DEFAULT_ROSTER_MAX_AGE_DAYS) == (14, 7)


def test_hella_and_roster_settings_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HELLA_CACHE_DAYS', '99')
    (tmp_path / '.env').write_text('HELLA_API_URL=http://localhost/api\nHELLA_CACHE_DAYS=7\nROSTER_MAX_AGE_DAYS=2.5\n')
    settings = load_settings(tmp_path)
    assert settings.hella_api_url == 'http://localhost/api'
    assert settings.cache_max_age == timedelta(days=7)
    assert settings.roster_max_age == timedelta(days=2.5)


def test_day_settings_fall_back_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ROSTER_MAX_AGE_DAYS', '3')
    assert load_settings(tmp_path).roster_max_age == timedelta(days=3)


@pytest.mark.parametrize('value', ['0', '-1', 'abc', ''])
def test_invalid_day_settings_raise(tmp_path, monkeypatch, value):
    monkeypatch.delenv('HELLA_CACHE_DAYS', raising=False)
    (tmp_path / '.env').write_text(f'HELLA_CACHE_DAYS={value}\n')
    if value == '':
        # An empty value counts as not set.
        assert load_settings(tmp_path).cache_max_age == timedelta(days=14)
        return
    with pytest.raises(ValueError, match=f"^HELLA_CACHE_DAYS must be a positive number of days, got '{value}'$"):
        load_settings(tmp_path)


@pytest.mark.parametrize('value, days', [('1', 1), ('0.5', 0.5), ('30', 30)])
def test_small_and_fractional_day_settings_are_valid(tmp_path, monkeypatch, value, days):
    monkeypatch.delenv('ROSTER_MAX_AGE_DAYS', raising=False)
    (tmp_path / '.env').write_text(f'ROSTER_MAX_AGE_DAYS={value}\n')
    assert load_settings(tmp_path).roster_max_age == timedelta(days=days)
