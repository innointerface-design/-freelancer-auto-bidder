"""Operator unlock is scoped to a counter, never a password or paid entitlement."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db
from app.auth_limits import account_identifier


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bidpilot_manage_login', ROOT / 'manage_login.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def recovery_state(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/recovery.db')
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    # Restore the caller's directory after the helper verifies its own app root.
    monkeypatch.chdir(tmp_path)
    db.init_db()
    with sessions() as session:
        user = db.User(email='recovery@example.test', name='Pro customer', password_hash='unchanged-salted-hash')
        db.apply_plan(user, 'paid_pro')
        session.add(user)
        session.commit()
        uid = user.id
    yield sessions, uid
    engine.dispose()


def test_operator_unlock_preserves_password_plan_sessions_and_other_limits(recovery_state, capsys):
    sessions, uid = recovery_state
    with sessions() as session:
        user = session.get(db.User, uid)
        before = (user.password_hash, user.subscription_expires, user.plan_tier, user.active)
        session.add(db.LoginAttempt(identifier=account_identifier(user.email), attempted_at=1))
        for category in ('login-v2:failed:ip:shared', 'cloud:2', 'password-change:2'):
            session.add(db.LoginAttempt(identifier=db.session_digest(category), attempted_at=1))
        session.add(db.AuthSession(token_hash='synthetic-session-hash', user_id=uid, csrf_hash='synthetic-csrf', expires_at=2_000_000_000))
        session.commit()
    recovery.main(['unlock', '--email', ' RECOVERY@example.test '])
    with sessions() as session:
        user = session.get(db.User, uid)
        assert (user.password_hash, user.subscription_expires, user.plan_tier, user.active) == before
        assert session.query(db.LoginAttempt).filter_by(identifier=account_identifier(user.email)).count() == 0
        assert session.query(db.LoginAttempt).count() == 3
        assert session.get(db.AuthSession, 'synthetic-session-hash')
        assert session.query(db.Audit).filter_by(user_id=uid, action='login_unlocked').count() == 1
    output = capsys.readouterr().out
    assert 'unchanged-salted-hash' not in output and 'Pro customer' not in output
    assert 'network/request limits still apply' in output


def test_unknown_account_does_not_modify_any_record(recovery_state):
    sessions, _ = recovery_state
    with pytest.raises(SystemExit) as error:
        recovery.main(['unlock', '--email', 'unknown@example.test'])
    assert error.value.code == 1
    with sessions() as session:
        assert session.query(db.Audit).filter_by(action='login_unlocked').count() == 0


def test_recovery_rejects_invalid_email_before_database_access(monkeypatch):
    monkeypatch.setattr(db, 'SessionLocal', lambda: pytest.fail('Invalid input must not open the database'))
    with pytest.raises(SystemExit):
        recovery.main(['unlock', '--email', 'invalid-address'])
