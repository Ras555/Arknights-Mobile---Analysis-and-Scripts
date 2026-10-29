import os
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
    with pytest.raises(Exception):
        load_settings(tmp_path).user_email = 'changed'
