"""Changing the startup directory must not silently select a different account store."""
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.database_url import resolve_database_url


def test_relative_sqlite_path_uses_application_directory_from_every_startup_folder(tmp_path, monkeypatch):
    application = tmp_path / 'application'
    application.mkdir()
    for folder in (tmp_path, application):
        monkeypatch.chdir(folder)
        result = resolve_database_url('sqlite:///./bidpilot_local.db', application)
        assert Path(make_url(result).database) == application / 'bidpilot_local.db'
    assert not list(tmp_path.rglob('*.db'))


@pytest.mark.parametrize('canonical_exists', [False, True])
def test_existing_noncanonical_database_requires_explicit_choice_without_changes(tmp_path, monkeypatch, canonical_exists):
    application = tmp_path / 'application'
    application.mkdir()
    previous = tmp_path / 'accounts.db'
    previous.write_bytes(b'original-private-account-store')
    canonical = application / 'accounts.db'
    if canonical_exists:
        canonical.write_bytes(b'other-private-account-store')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match='absolute DATABASE_URL'):
        resolve_database_url('sqlite:///accounts.db', application)
    assert previous.read_bytes() == b'original-private-account-store'
    assert canonical.exists() is canonical_exists
    if canonical_exists:
        assert canonical.read_bytes() == b'other-private-account-store'


@pytest.mark.parametrize('url', ['sqlite://', 'sqlite:///:memory:', 'postgresql://test:test@localhost/test'])
def test_memory_and_remote_locations_keep_existing_semantics(tmp_path, url):
    assert resolve_database_url(url, tmp_path) == url


def test_explicit_absolute_database_is_not_relocated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    url = 'sqlite:///' + str(tmp_path / 'explicit.db')
    assert resolve_database_url(url, tmp_path / 'other') == url


def test_query_parameters_and_nested_paths_are_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = make_url(resolve_database_url('sqlite:///data/people.db?timeout=12', tmp_path / 'app'))
    assert Path(result.database) == tmp_path / 'app' / 'data' / 'people.db'
    assert result.query['timeout'] == '12'
