"""Successful sign-ins and Pro activation must not create login lockouts."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth_limits, db, main


PASSWORD = 'Subscriber-private-password-123!'
OWNER_PASSWORD = 'Test-only-owner-password-123!'
EMAIL = 'subscriber@example.test'


@pytest.fixture
def state(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/login.db', connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setenv('PAYMENT_BANK', 'Synthetic reviewed bank destination')
    with TestClient(main.app) as client:
        with sessions() as session:
            member = db.User(email=EMAIL, name='Paid subscriber', password_hash=db.hash_password(PASSWORD))
            db.apply_plan(member, 'paid_starter', 'inactive')
            session.add(member)
            session.commit()
            uid = member.id
        yield client, sessions, uid
    engine.dispose()


def sign_in(client, email=EMAIL, password=PASSWORD):
    return client.post('/api/login', json={'email': email, 'password': password})


def failures(sessions, email=EMAIL):
    with sessions() as session:
        return session.query(db.LoginAttempt).filter_by(identifier=auth_limits.account_identifier(email)).count()


def test_more_than_ten_valid_sign_ins_do_not_accumulate_failures_on_shared_ip(state):
    client, sessions, _ = state
    for index in range(16):
        email, password = (EMAIL, PASSWORD) if index % 2 else ('owner@example.test', OWNER_PASSWORD)
        assert sign_in(client, email, password).status_code == 200
        token = client.get('/api/csrf').json()['csrf']
        assert client.post('/api/logout', headers={'x-csrf-token': token}).status_code == 200
    assert failures(sessions) == 0 and failures(sessions, 'owner@example.test') == 0


def test_old_mixed_success_counters_do_not_lock_a_valid_upgraded_client(state):
    client, sessions, _ = state
    now = int(time.time())
    with sessions() as session:
        for category in ('login:email:' + EMAIL, 'login:ip:testclient'):
            session.add_all(db.LoginAttempt(identifier=db.session_digest(category), attempted_at=now) for _ in range(10))
        session.commit()
    assert sign_in(client).status_code == 200
    assert failures(sessions) == 0


def test_invalid_attempts_persist_and_report_actual_retry_time(state, monkeypatch):
    client, sessions, _ = state
    now = int(time.time())
    clock = [now]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    for _ in range(10):
        assert sign_in(client, password='Wrong-password-123!').status_code == 401
    assert failures(sessions) == 10
    clock[0] += 100
    blocked = sign_in(client)
    assert blocked.status_code == 429
    assert blocked.json()['retry_after_seconds'] == 800
    assert blocked.headers['retry-after'] == '800'
    assert 'password' not in blocked.json()['detail'].lower()
    db.init_db()
    assert sign_in(client).status_code == 429
    clock[0] = now + 900
    assert sign_in(client).status_code == 200
    assert failures(sessions) == 0


def test_success_clears_only_own_email_failures_not_ip_or_other_account(state):
    client, sessions, _ = state
    assert sign_in(client, password='Wrong-password-123!').status_code == 401
    assert sign_in(client, 'other@example.test', 'Wrong-password-123!').status_code == 401
    assert sign_in(client).status_code == 200
    assert failures(sessions) == 0
    assert failures(sessions, 'other@example.test') == 1
    with sessions() as session:
        network = db.session_digest('login-v2:failed:ip:testclient')
        assert session.query(db.LoginAttempt).filter_by(identifier=network).count() == 2


def test_pro_approval_logout_relogin_and_restart_preserve_password(state):
    client, sessions, uid = state
    with sessions() as session:
        original = session.get(db.User, uid).password_hash
        payment = db.PaymentRequest(user_id=uid, method='bank', transaction_id='verified-pro-reference',
                                    amount=40, currency='USD', plan_tier='paid_pro')
        session.add(payment)
        session.commit()
        pid = payment.id
    assert sign_in(client, 'owner@example.test', OWNER_PASSWORD).status_code == 200
    csrf = client.get('/api/csrf').json()['csrf']
    assert client.post(f'/api/admin/payments/{pid}/decide', headers={'x-csrf-token': csrf},
                       json={'action': 'approve'}).status_code == 200
    client.cookies.clear()
    assert sign_in(client).status_code == 200
    user = client.get('/api/me').json()['user']
    assert user['plan_tier'] == 'paid_pro' and user['subscription_active']
    csrf = client.get('/api/csrf').json()['csrf']
    assert client.post('/api/logout', headers={'x-csrf-token': csrf}).status_code == 200
    db.init_db()
    assert sign_in(client).status_code == 200
    with sessions() as session:
        assert session.get(db.User, uid).password_hash == original
        assert db.verify_password(PASSWORD, original)


def test_inactive_plan_does_not_block_login_to_renew_or_pay(state):
    client, _, _ = state
    assert sign_in(client).status_code == 200
    assert client.get('/api/me').json()['user']['subscription_active'] is False
    assert client.get('/api/billing').status_code == 200


def test_disabled_and_unknown_accounts_share_generic_auth_failure(state):
    client, sessions, uid = state
    with sessions() as session:
        session.get(db.User, uid).active = False
        session.commit()
    disabled = sign_in(client)
    unknown = sign_in(client, 'unknown@example.test', PASSWORD)
    assert disabled.status_code == unknown.status_code == 401
    assert disabled.json() == unknown.json() == {'detail': 'Invalid email or password'}


def test_password_change_during_hash_verification_cannot_mint_old_password_session(state, monkeypatch):
    client, sessions, uid = state
    actual_verify = main.verify_password
    new_password = 'Changed-concurrently-password-123!'
    def changed(password, stored):
        verified = actual_verify(password, stored)
        with sessions() as session:
            session.get(db.User, uid).password_hash = db.hash_password(new_password)
            session.query(db.AuthSession).filter_by(user_id=uid).delete()
            session.commit()
        return verified
    monkeypatch.setattr(main, 'verify_password', changed)
    assert sign_in(client).status_code == 401
    with sessions() as session:
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 0
    monkeypatch.setattr(main, 'verify_password', actual_verify)
    assert sign_in(client, password=new_password).status_code == 200


def test_concurrent_failed_attempts_have_bounded_admission(state, monkeypatch):
    client, sessions, _ = state
    entered, release = [], threading.Event()
    mutex = threading.Lock()
    def invalid(*args):
        with mutex:
            entered.append(1)
            if len(entered) == 10:
                release.set()
        assert release.wait(10)
        return False
    monkeypatch.setattr(main, 'verify_password', invalid)
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: sign_in(client, password='Bad-private-password').status_code, range(12)))
    assert statuses.count(401) == 10 and statuses.count(429) == 2
    assert len(entered) == 10 and failures(sessions) == 10


def test_request_budget_remains_even_for_successful_sign_ins(state, monkeypatch):
    client, sessions, _ = state
    now = int(time.time())
    identifier = db.session_digest('login-v2:requests:ip:testclient')
    with sessions() as session:
        session.add_all(db.LoginAttempt(identifier=identifier, attempted_at=now) for _ in range(120))
        session.commit()
    blocked = sign_in(client)
    assert blocked.status_code == 429 and 1 <= blocked.json()['retry_after_seconds'] <= 60
    assert failures(sessions) == 0


def test_high_network_failure_budget_remains_after_scoped_account_unlock(state):
    client, sessions, _ = state
    now = int(time.time())
    network = db.session_digest('login-v2:failed:ip:testclient')
    with sessions() as session:
        session.add_all(db.LoginAttempt(identifier=network, attempted_at=now) for _ in range(100))
        session.add(db.LoginAttempt(identifier=auth_limits.account_identifier(EMAIL), attempted_at=now))
        session.commit()
    with sessions() as session:
        assert auth_limits.unlock_account_failures(session, EMAIL) == 1
        session.commit()
    assert sign_in(client).status_code == 429
    assert failures(sessions) == 0


def test_admin_unlock_requires_role_csrf_and_correct_expected_account(state):
    client, sessions, uid = state
    assert client.post(f'/api/team/{uid}/login-unlock', json={}).status_code == 401
    assert sign_in(client).status_code == 200
    member_csrf = client.get('/api/csrf').json()['csrf']
    assert client.post(f'/api/team/{uid}/login-unlock', headers={'x-csrf-token': member_csrf}, json={}).status_code == 403
    assert sign_in(client, 'owner@example.test', OWNER_PASSWORD).status_code == 200
    csrf = client.get('/api/csrf').json()['csrf']
    owner_id = client.get('/api/me').json()['user']['id']
    with sessions() as session:
        user = session.get(db.User, uid)
        before = (user.password_hash, user.plan_tier, user.subscription_status)
        session.add(db.LoginAttempt(identifier=auth_limits.account_identifier(EMAIL), attempted_at=int(time.time())))
        other = db.session_digest('password-change:' + str(uid))
        session.add(db.LoginAttempt(identifier=other, attempted_at=int(time.time())))
        session.commit()
    url = f'/api/team/{uid}/login-unlock'
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, headers={'x-csrf-token': csrf}, json={'expected_user_id': owner_id + 100}).status_code == 409
    assert failures(sessions) == 1
    assert client.post(url, headers={'x-csrf-token': csrf}, json={'expected_user_id': owner_id}).status_code == 200
    assert failures(sessions) == 0
    with sessions() as session:
        user = session.get(db.User, uid)
        assert before == (user.password_hash, user.plan_tier, user.subscription_status)
        assert session.query(db.LoginAttempt).filter_by(identifier=other).count() == 1
        assert session.query(db.Audit).filter_by(user_id=uid, action='login_unlocked').count() == 1


def test_registration_clears_only_the_new_accounts_prior_failures(state):
    client, sessions, _ = state
    email = 'new-subscriber@example.test'
    for _ in range(10):
        assert sign_in(client, email, 'Invalid-private-password').status_code == 401
    assert failures(sessions, email) == 10
    result = client.post('/api/register', json={'name': 'New subscriber', 'email': email, 'password': PASSWORD})
    assert result.status_code == 200
    assert failures(sessions, email) == 0
    client.cookies.clear()
    assert sign_in(client, email).status_code == 200
    with sessions() as session:
        network = db.session_digest('login-v2:failed:ip:testclient')
        assert session.query(db.LoginAttempt).filter_by(identifier=network).count() == 10


def test_stale_lease_release_cannot_delete_reused_ids_for_a_newer_request(state):
    _, sessions, _ = state
    now = int(time.time())
    lease = auth_limits.reserve_login(sessions, EMAIL, 'reused-id-network', now=now)
    with sessions() as session:
        session.query(db.LoginAttempt).filter(db.LoginAttempt.id.in_(lease.lease_ids)).delete(synchronize_session=False)
        for row_id, name in zip(lease.lease_ids, ('account_lease', 'network_lease')):
            session.add(db.LoginAttempt(id=row_id, identifier=lease.identifiers[name], attempted_at=now + 60))
        session.commit()
    auth_limits.release_login(sessions, lease)
    with sessions() as session:
        assert session.query(db.LoginAttempt).filter(db.LoginAttempt.id.in_(lease.lease_ids)).count() == 2
        assert not auth_limits.finish_login(session, lease, True, now=now + 60)
        session.commit()
        assert session.query(db.LoginAttempt).filter(db.LoginAttempt.id.in_(lease.lease_ids)).count() == 2


def test_request_finishing_after_lease_expiry_cannot_create_a_session(state, monkeypatch):
    client, sessions, uid = state
    clock = [int(time.time())]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    verifier = main.verify_password
    def slow(password, stored):
        result = verifier(password, stored)
        clock[0] += auth_limits.LEASE_SECONDS
        return result
    monkeypatch.setattr(main, 'verify_password', slow)
    assert sign_in(client).status_code == 409
    with sessions() as session:
        assert session.query(db.AuthSession).filter_by(user_id=uid).count() == 0
    assert failures(sessions) == 0


def test_operator_created_pro_user_can_sign_in_again_with_the_same_password(state):
    client, sessions, _ = state
    assert sign_in(client, 'owner@example.test', OWNER_PASSWORD).status_code == 200
    csrf = client.get('/api/csrf').json()['csrf']
    email = 'admin-created-pro@example.test'
    created = client.post('/api/team', headers={'x-csrf-token': csrf}, json={
        'email': email, 'name': 'Admin created Pro member', 'password': PASSWORD,
        'plan_tier': 'paid_pro', 'subscription_status': 'active',
    })
    assert created.status_code == 200
    assert PASSWORD not in created.text and 'password_hash' not in created.text
    uid = created.json()['member']['id']
    with sessions() as session:
        password_hash = session.get(db.User, uid).password_hash
    for _ in range(6):
        client.cookies.clear()
        assert sign_in(client, email).status_code == 200
        assert client.get('/api/me').json()['user']['plan_tier'] == 'paid_pro'
    db.init_db()
    with sessions() as session:
        assert session.get(db.User, uid).password_hash == password_hash
    assert failures(sessions, email) == 0


def test_direct_reservation_helpers_normalize_account_identity(state):
    _, sessions, _ = state
    first = auth_limits.reserve_login(sessions, ' Subscriber@Example.Test ', 'test-network')
    second = auth_limits.reserve_login(sessions, EMAIL, 'test-network')
    assert first.identifiers['account'] == second.identifiers['account']
    assert first.identifiers['account_lease'] == second.identifiers['account_lease']
    auth_limits.release_login(sessions, first)
    auth_limits.release_login(sessions, second)
