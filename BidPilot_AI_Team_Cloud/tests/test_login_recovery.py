"""Local diagnostics and password recovery preserve existing paid entitlement."""
import importlib.util
from pathlib import Path
import warnings

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

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


@pytest.mark.parametrize('action', ['check', 'unlock', 'reset-password'])
def test_unknown_account_does_not_modify_any_record(recovery_state, action, monkeypatch):
    sessions, _ = recovery_state
    monkeypatch.setattr(recovery, '_hidden_password', lambda *args: pytest.fail('Unknown account must not request a password'))
    with pytest.raises(SystemExit) as error:
        recovery.main([action, '--email', 'unknown@example.test'])
    assert error.value.code == 1
    with sessions() as session:
        assert session.query(db.Audit).filter_by(action='login_unlocked').count() == 0


def test_recovery_rejects_invalid_email_before_database_access(monkeypatch):
    monkeypatch.setattr(db, 'SessionLocal', lambda: pytest.fail('Invalid input must not open the database'))
    with pytest.raises(SystemExit):
        recovery.main(['unlock', '--email', 'invalid-address'])


def _seed_auth_state(sessions, uid, *, enabled=True):
    with sessions() as session:
        user = session.get(db.User, uid)
        user.password_hash = db.hash_password('Existing-recovery-password-123!')
        user.active = enabled
        user.gemini_key_cipher = 'synthetic-encrypted-gemini'
        user.openai_key_cipher = 'synthetic-encrypted-openai'
        session.add(db.LoginAttempt(identifier=account_identifier(user.email), attempted_at=1))
        session.add(db.LoginAttempt(identifier=account_identifier('other@example.test'), attempted_at=1))
        session.add(db.LoginAttempt(identifier=db.session_digest('login-v2:failed:ip:shared'), attempted_at=1))
        session.add(db.LoginAttempt(identifier=db.session_digest(f'password-change:{uid}'), attempted_at=1))
        other = db.User(email='other@example.test', name='Other member', password_hash='other-unchanged-hash')
        db.apply_plan(other, 'paid_starter')
        session.add(other)
        session.flush()
        session.add(db.AuthSession(token_hash='target-session', user_id=uid, csrf_hash='target-csrf', expires_at=2_000_000_000))
        session.add(db.AuthSession(token_hash='other-session', user_id=other.id, csrf_hash='other-csrf', expires_at=2_000_000_000))
        session.commit()
        return {column.name: getattr(user, column.name) for column in db.User.__table__.columns}, other.id


def _mock_passwords(monkeypatch, *passwords):
    values = iter(passwords)
    monkeypatch.setattr(recovery, '_hidden_password', lambda *args: next(values))


def test_read_only_check_has_no_secret_output_or_state_changes(recovery_state, capsys):
    sessions, uid = recovery_state
    before, _ = _seed_auth_state(sessions, uid, enabled=False)
    recovery.main(['check', '--email', 'recovery@example.test'])
    output = capsys.readouterr().out
    assert 'Enabled: no' in output and 'Plan: paid_pro; subscription: active' in output
    assert 'SQLite file:' in output and 'Diagnostic only' in output
    for private in (before['password_hash'], 'synthetic-encrypted-gemini', 'synthetic-encrypted-openai'):
        assert private not in output
    with sessions() as session:
        user = session.get(db.User, uid)
        assert {column.name: getattr(user, column.name) for column in db.User.__table__.columns} == before
        assert session.query(db.LoginAttempt).count() == 4
        assert session.query(db.AuthSession).count() == 2
        assert session.query(db.Audit).count() == 0


@pytest.mark.parametrize('password,expected', [('Existing-recovery-password-123!', 'yes'), ('Incorrect-password-123!', 'no')])
def test_hidden_password_check_distinguishes_actual_credential_match_without_mutation(recovery_state, monkeypatch, capsys, password, expected):
    sessions, uid = recovery_state
    before, _ = _seed_auth_state(sessions, uid)
    _mock_passwords(monkeypatch, password)
    recovery.main(['check', '--email', 'recovery@example.test', '--verify-password'])
    output = capsys.readouterr().out
    assert f'Password matches stored credential: {expected}' in output
    assert password not in output and before['password_hash'] not in output
    if expected == 'yes':
        assert 'same installation/database' in output
    with sessions() as session:
        assert session.get(db.User, uid).password_hash == before['password_hash']
        assert session.query(db.LoginAttempt).count() == 4
        assert session.query(db.AuthSession).count() == 2
        assert session.query(db.Audit).count() == 0


def test_reset_recovers_existing_password_preserving_paid_account_and_other_records(recovery_state, monkeypatch, capsys):
    sessions, uid = recovery_state
    before, other_id = _seed_auth_state(sessions, uid)
    password = 'New-recovery-password-123!'
    _mock_passwords(monkeypatch, password, password)
    recovery.main(['reset-password', '--email', ' RECOVERY@example.test '])
    with sessions() as session:
        user = session.get(db.User, uid)
        assert db.verify_password(password, user.password_hash)
        assert not db.verify_password('Existing-recovery-password-123!', user.password_hash)
        for name, value in before.items():
            if name not in ('password_hash', 'updated', 'lock_version'):
                assert getattr(user, name) == value, name
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 0
        assert session.get(db.AuthSession, 'other-session')
        assert session.get(db.User, other_id).password_hash == 'other-unchanged-hash'
        assert session.query(db.LoginAttempt).filter_by(identifier=account_identifier(user.email)).count() == 0
        assert session.query(db.LoginAttempt).count() == 3
        log = session.query(db.Audit).filter_by(user_id=uid, action='password_reset_local').one()
        assert password not in log.message and user.password_hash not in log.message
    output = capsys.readouterr().out
    assert password not in output and 'Plan, expiry, account status' in output


@pytest.mark.parametrize('inputs', [('',), ('short',), (' ' * 12,), ('x' * 257,), ('Valid-password-123!', 'Mismatch-password-123!')])
def test_reset_bad_input_never_mutates_existing_state(recovery_state, monkeypatch, inputs):
    sessions, uid = recovery_state
    before, _ = _seed_auth_state(sessions, uid)
    _mock_passwords(monkeypatch, *inputs)
    with pytest.raises(SystemExit) as error:
        recovery.main(['reset-password', '--email', 'recovery@example.test'])
    assert error.value.code == 1
    with sessions() as session:
        assert session.get(db.User, uid).password_hash == before['password_hash']
        assert session.query(db.LoginAttempt).count() == 4
        assert session.query(db.AuthSession).count() == 2
        assert session.query(db.Audit).count() == 0


def test_disabled_account_cannot_be_reset_or_enabled_implicitly(recovery_state, monkeypatch):
    sessions, uid = recovery_state
    before, _ = _seed_auth_state(sessions, uid, enabled=False)
    monkeypatch.setattr(recovery, '_hidden_password', lambda *args: pytest.fail('Disabled account must not request a password'))
    with pytest.raises(SystemExit):
        recovery.main(['reset-password', '--email', 'recovery@example.test'])
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.active is False and user.password_hash == before['password_hash']
        assert session.query(db.LoginAttempt).count() == 4


def test_secure_entry_refuses_nonterminal_input_without_calling_getpass(monkeypatch):
    parser = recovery.argparse.ArgumentParser()
    monkeypatch.setattr(recovery.sys.stdin, 'isatty', lambda: False)
    monkeypatch.setattr(recovery.getpass, 'getpass', lambda *args: pytest.fail('Never fallback to echoed stdin'))
    with pytest.raises(SystemExit):
        recovery._hidden_password(parser, 'Password: ')


@pytest.mark.parametrize('failure', ['warning', 'eof', 'interrupt'])
def test_secure_entry_refuses_getpass_fallback_or_cancellation(monkeypatch, failure):
    parser = recovery.argparse.ArgumentParser()
    monkeypatch.setattr(recovery.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(recovery.sys.stderr, 'isatty', lambda: True)
    def unavailable(*args):
        if failure == 'warning':
            warnings.warn('Password may be echoed', recovery.getpass.GetPassWarning)
            pytest.fail('Warning must prevent insecure fallback input')
        if failure == 'eof':
            raise EOFError
        raise KeyboardInterrupt
    monkeypatch.setattr(recovery.getpass, 'getpass', unavailable)
    with pytest.raises(SystemExit):
        recovery._hidden_password(parser, 'Password: ')


def test_recovery_refuses_missing_sqlite_database_before_connecting(tmp_path, monkeypatch):
    missing = tmp_path / 'not-created.db'
    engine = create_engine(f'sqlite:///{missing}')
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', lambda: pytest.fail('Must not create missing database'))
    with pytest.raises(SystemExit):
        recovery.main(['check', '--email', 'recovery@example.test'])
    assert not missing.exists()
    engine.dispose()


def test_database_location_never_prints_remote_url_credentials(capsys):
    class Engine:
        class dialect:
            name = 'postgresql'
        class url:
            def __str__(self):
                pytest.fail('Remote URL must never be stringified')
    output = recovery._database_location(recovery.argparse.ArgumentParser(), Engine())
    assert output == 'Database backend: postgresql (connection details hidden)'


def test_reset_rolls_back_password_sessions_and_counters_if_audit_write_fails(recovery_state, monkeypatch, capsys):
    sessions, uid = recovery_state
    before, _ = _seed_auth_state(sessions, uid)
    _mock_passwords(monkeypatch, 'New-recovery-password-123!', 'New-recovery-password-123!')
    def failed_audit(*args):
        raise SQLAlchemyError('synthetic-private-error')
    monkeypatch.setattr(db, 'audit', failed_audit)
    with pytest.raises(SystemExit):
        recovery.main(['reset-password', '--email', 'recovery@example.test'])
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.password_hash == before['password_hash']
        assert user.lock_version == before['lock_version']
        assert session.query(db.LoginAttempt).count() == 4
        assert session.query(db.AuthSession).count() == 2
    assert 'synthetic-private-error' not in capsys.readouterr().err


def test_reset_rejects_concurrent_password_change_and_rolls_back_counter_clear(recovery_state, monkeypatch):
    sessions, uid = recovery_state
    _seed_auth_state(sessions, uid)
    password = 'New-recovery-password-123!'
    calls = []
    concurrent_hash = db.hash_password('Concurrent-password-change-123!')
    def prompt(*args):
        calls.append(True)
        if len(calls) == 2:
            with sessions() as session:
                session.get(db.User, uid).password_hash = concurrent_hash
                session.commit()
        return password
    monkeypatch.setattr(recovery, '_hidden_password', prompt)
    with pytest.raises(SystemExit):
        recovery.main(['reset-password', '--email', 'recovery@example.test'])
    with sessions() as session:
        assert session.get(db.User, uid).password_hash == concurrent_hash
        assert session.query(db.LoginAttempt).count() == 4
        assert session.query(db.AuthSession).count() == 2
        assert session.query(db.Audit).count() == 0
