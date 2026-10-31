"""Account security requires the current secret and never exposes other accounts."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import account_security, db, main


PASSWORD = 'Test-only-owner-password-123!'
NEW_PASSWORD = 'Updated-own-account-password-456!'


@pytest.fixture
def security_client(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/security.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    if not any(getattr(route, 'path', '') == '/api/security' for route in main.app.routes):
        account_security.install_account_security_routes(main.app, main.current, main.mutate)
    with TestClient(main.app) as client:
        yield client, sessions
    engine.dispose()


def login(client, password=PASSWORD):
    response = client.post('/api/login', json={'email': 'owner@example.test', 'password': password})
    assert response.status_code == 200, response.text
    user = client.get('/api/me').json()['user']
    headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
    return user['id'], headers, client.cookies.get('bp_session')


def request_password(client, headers, uid, **changes):
    return client.post('/api/security/password', headers=headers, json={
        'current_password': PASSWORD, 'new_password': NEW_PASSWORD, 'expected_user_id': uid, **changes,
    })


def other_session(sessions):
    with sessions() as session:
        user = db.User(email='other-security@example.test', name='Other account',
                       password_hash=db.hash_password('Other-account-password-123!'))
        session.add(user)
        session.flush()
        token, _ = db.create_auth_session(session, user.id)
        session.commit()
        return user.id, token


def test_security_auth_csrf_and_readonly_summary(security_client):
    client, sessions = security_client
    assert client.get('/api/security').status_code == 401
    assert client.post('/api/security/logout-all', json={}).status_code == 401
    uid, headers, _ = login(client)
    other_session(sessions)
    response = client.get(f'/api/security?expected_user_id={uid}')
    assert response.status_code == 200
    result = response.json()
    assert result['active_sessions'] == 1
    assert result['password_storage'] == 'salted_pbkdf2'
    assert result['saved_credentials_encrypted'] is True
    assert result['password_sharing_required'] is False
    assert 'hash' not in response.text and PASSWORD not in response.text
    assert client.post('/api/security/logout-all', json={}).status_code == 403
    assert request_password(client, {}, uid).status_code == 403
    assert client.get('/api/security?expected_user_id=0').status_code == 422
    assert client.get(f'/api/security?expected_user_id={uid + 100}').status_code == 409
    assert client.get('/api/me').status_code == 200


def test_password_change_rehashes_and_revokes_all_own_sessions_only(security_client):
    client, sessions = security_client
    uid, _, first_token = login(client)
    client.cookies.clear()
    uid, headers, second_token = login(client)
    other_uid, token = other_session(sessions)
    with sessions() as session:
        original_hash = session.get(db.User, uid).password_hash
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 2
    response = request_password(client, headers, uid)
    assert response.status_code == 200 and response.json() == {'ok': True, 'sign_in_required': True}
    assert 'Max-Age=0' in response.headers['set-cookie']
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.password_hash != original_hash
        assert db.verify_password(NEW_PASSWORD, user.password_hash)
        assert not db.verify_password(PASSWORD, user.password_hash)
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 0
        assert session.query(db.AuthSession).filter_by(user_id=other_uid).count() == 1
        records = ' '.join(record.message for record in session.query(db.Audit))
        assert PASSWORD not in records and NEW_PASSWORD not in records
        assert session.query(db.Audit).filter_by(user_id=uid, action='password_changed').count() == 1
    for old_token in (first_token, second_token):
        client.cookies.set('bp_session', old_token)
        assert client.get('/api/me').status_code == 401
    client.cookies.set('bp_session', token)
    assert client.get('/api/me').json()['user']['id'] == other_uid
    client.cookies.clear()
    login(client, NEW_PASSWORD)
    db.init_db()
    with sessions() as session:
        assert db.verify_password(NEW_PASSWORD, session.get(db.User, uid).password_hash)


def test_wrong_password_throttle_persists_and_is_separate_from_login(security_client, monkeypatch):
    client, sessions = security_client
    uid, headers, _ = login(client)
    clock = [int(time.time())]
    monkeypatch.setattr(account_security.time, 'time', lambda: clock[0])
    wrong = 'Incorrect-secret-never-logged-123!'
    for _ in range(5):
        response = request_password(client, headers, uid, current_password=wrong)
        assert response.status_code == 400
        assert wrong not in response.text and NEW_PASSWORD not in response.text
    assert request_password(client, headers, uid).status_code == 429
    identifier = db.session_digest(f'password-change:{uid}')
    with sessions() as session:
        assert session.query(db.LoginAttempt).filter_by(identifier=identifier).count() == 5
        assert session.query(db.Audit).filter_by(action='password_changed').count() == 0
        assert db.verify_password(PASSWORD, session.get(db.User, uid).password_hash)
    uid, headers, _ = login(client)
    clock[0] += 901
    assert request_password(client, headers, uid).status_code == 200


def test_expected_account_and_session_rechecks_precede_secret_verification(security_client, monkeypatch):
    client, sessions = security_client
    uid, headers, _ = login(client)
    calls = []
    monkeypatch.setattr(account_security, 'verify_password', lambda *args: calls.append(args))
    assert request_password(client, headers, uid + 1).status_code == 409
    assert client.post('/api/security/logout-all', headers=headers, json={'expected_user_id': uid + 1}).status_code == 409
    monkeypatch.setattr(account_security, 'get_auth_session', lambda *args: None)
    assert request_password(client, headers, uid).status_code == 401
    assert calls == []
    with sessions() as session:
        assert db.verify_password(PASSWORD, session.get(db.User, uid).password_hash)
        assert session.query(db.Audit).filter_by(action='password_changed').count() == 0


@pytest.mark.parametrize('changes', [
    {'new_password': 'short'}, {'new_password': ' ' * 12},
    {'new_password': 'DO-NOT-ECHO-NEW-SECRET' * 20}, {'new_password': 123456789012},
    {'current_password': ''}, {'password_hash': 'do-not-accept-prehashed-password'},
    {'user_id': 9999}, {'expected_user_id': True},
])
def test_invalid_security_inputs_are_rejected_without_echoing_secrets(security_client, changes):
    client, sessions = security_client
    uid, headers, _ = login(client)
    response = request_password(client, headers, uid, **changes)
    assert response.status_code == 422
    assert PASSWORD not in response.text and NEW_PASSWORD not in response.text
    assert 'DO-NOT-ECHO-NEW-SECRET' not in response.text
    with sessions() as session:
        assert db.verify_password(PASSWORD, session.get(db.User, uid).password_hash)


def test_same_password_does_not_revoke_sessions(security_client):
    client, sessions = security_client
    uid, headers, _ = login(client)
    assert request_password(client, headers, uid, new_password=PASSWORD).status_code == 400
    assert client.get('/api/me').status_code == 200
    with sessions() as session:
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 1


def test_logout_all_revokes_only_current_account_and_clears_cookie(security_client):
    client, sessions = security_client
    uid, _, first_token = login(client)
    client.cookies.clear()
    uid, headers, second_token = login(client)
    other_uid, _ = other_session(sessions)
    response = client.post('/api/security/logout-all', headers=headers, json={'expected_user_id': uid})
    assert response.status_code == 200 and response.json()['sign_in_required']
    with sessions() as session:
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 0
        assert session.query(db.AuthSession).filter_by(user_id=other_uid).count() == 1
        assert db.verify_password(PASSWORD, session.get(db.User, uid).password_hash)
    for token in (first_token, second_token):
        client.cookies.set('bp_session', token)
        assert client.get('/api/me').status_code == 401
