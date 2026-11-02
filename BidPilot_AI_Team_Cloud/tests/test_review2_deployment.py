"""Deployment and bootstrap input regressions from the uploaded-source review."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import yaml

from app import db


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review2_setup', ROOT / 'setup_local.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_render_build_context_contains_application_and_docker_copy_sources():
    # Render resolves rootDir from the Git repository, not the blueprint folder.
    repo = ROOT.parent
    if not (repo / 'pyproject.toml').exists():
        pytest.skip('Monorepo blueprint check requires the full GitHub source archive')
    blueprint = yaml.safe_load((ROOT / 'render.yaml').read_text())
    for service in blueprint['services']:
        context = repo / service.get('rootDir', '.') / service.get('dockerContext', '.')
        dockerfile = repo / service.get('rootDir', '.') / service.get('dockerfilePath', 'Dockerfile')
        assert dockerfile.is_file(), f"{service['name']}: Dockerfile missing in build context"
        assert (context / 'requirements.txt').is_file()
        assert (context / 'app/main.py').is_file()
        assert (context / 'app/worker.py').is_file()


def test_setup_reprompts_for_nul_email_without_printing_input(monkeypatch, capsys):
    bad = 'private\x00address@example.test'
    responses = iter([bad, 'owner@example.test'])
    monkeypatch.setattr('builtins.input', lambda *_: next(responses))
    assert setup.ask_email('Email: ') == 'owner@example.test'
    assert bad not in capsys.readouterr().out


@pytest.mark.parametrize('blank', [' ' * 12, '\t' * 12, '\u2003' * 12])
def test_setup_reprompts_for_blank_password_preserving_exact_valid_value(monkeypatch, blank):
    password = ' exact\x00password 123! '
    responses = iter([blank, password, password])
    monkeypatch.setattr(setup, '_hidden_password', lambda *_: next(responses))
    assert setup.ask_password('Password: ') == password


@pytest.fixture
def bootstrap_db(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/bootstrap.db')
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setenv('ADMIN_EMAIL', 'owner@example.test')
    monkeypatch.setenv('ADMIN_PASSWORD', 'owner-test-password-123!')
    for name in ('ADMIN_PASSWORD_B64', 'SECOND_FOUNDER_PASSWORD_B64',
                 'SECOND_FOUNDER_EMAIL', 'SECOND_FOUNDER_PASSWORD'):
        monkeypatch.delenv(name, raising=False)
    yield factory
    engine.dispose()


def test_bootstrap_email_validation_rejects_database_unsupported_nul():
    assert not db._valid_email('bad\x00email@example.test')
    assert db._valid_email('owner@example.test')


def test_blank_new_owner_password_does_not_create_account(bootstrap_db, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', ' ' * 12)
    with pytest.raises(RuntimeError, match='Set ADMIN_EMAIL'):
        db.init_db()
    with bootstrap_db() as session:
        assert session.query(db.User).count() == 0


def test_blank_optional_founder_is_skipped_and_existing_owner_preserved(bootstrap_db, monkeypatch):
    db.init_db()
    with bootstrap_db() as session:
        existing = session.query(db.User).one().password_hash
    monkeypatch.setenv('SECOND_FOUNDER_EMAIL', 'founder@example.test')
    monkeypatch.setenv('SECOND_FOUNDER_PASSWORD', ' ' * 12)
    # Existing owner credentials are never parsed or replaced on a restart.
    monkeypatch.setenv('ADMIN_PASSWORD', ' ' * 12)
    db.init_db()
    with bootstrap_db() as session:
        assert session.query(db.User).count() == 1
        assert session.query(db.User).one().password_hash == existing
