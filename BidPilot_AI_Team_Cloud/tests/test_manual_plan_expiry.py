"""Manual paid expiry preserves sign-in and billing history, with stale-plan guards."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


PASSWORD = 'Customer-private-password-123!'
EMAIL = 'expire-customer@example.test'


@pytest.fixture
def state(tmp_path, monkeypatch):
    clock = [2_000_000_000]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    engine = create_engine(f'sqlite:///{tmp_path}/manual-expiry.db',
                           connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with TestClient(main.app) as client:
        with sessions() as session:
            user = db.User(email=EMAIL, name='Expiry customer', password_hash=db.hash_password(PASSWORD),
                           auto_requested=True, auto_chat_greeting=True, auto_admin_allowed=True,
                           gemini_key_cipher=db.encrypt_secret('synthetic-gemini-value'),
                           openai_key_cipher=db.encrypt_secret('synthetic-openai-value'))
            db.apply_plan(user, 'paid_pro', now=clock[0] - 100)
            session.add(user)
            session.flush()
            uid = user.id
            session.add(db.Connection(user_id=uid, token_cipher=db.encrypt_secret('synthetic-freelancer-value'),
                                      freelancer_id='1234', environment='sandbox'))
            job = db.Job(user_id=uid, remote_id='recorded-job', data=json.dumps({'title': 'Prior project'}))
            session.add(job)
            session.flush()
            session.add(db.Bid(user_id=uid, job_id=job.id, amount=50, proposal='Prior submitted proposal', status='submitted'))
            session.add(db.PaymentRequest(user_id=uid, method='bank', transaction_id='settled-existing-receipt',
                                          amount=40, currency='USD', plan_tier='paid_pro', status='approved',
                                          access_starts_at=user.subscription_started_at,
                                          access_expires_at=user.subscription_expires))
            session.commit()
        yield client, sessions, uid, clock
    engine.dispose()


def login(client, owner=True):
    email, password = ('owner@example.test', 'Test-only-owner-password-123!') if owner else (EMAIL, PASSWORD)
    response = client.post('/api/login', json={'email': email, 'password': password})
    assert response.status_code == 200, response.text
    return {'x-csrf-token': client.get('/api/csrf').json()['csrf']}


def snapshot(sessions, uid, owner_id=None):
    with sessions() as session:
        user = session.get(db.User, uid)
        owner = session.query(db.User).filter_by(role='admin').one()
        return {'expected_user_id': owner_id or owner.id,
                'expected_plan_tier': user.plan_tier,
                'expected_subscription_status': user.subscription_status,
                'expected_subscription_expires': user.subscription_expires}


def expire(client, uid, body, headers):
    return client.post(f'/api/team/{uid}/expire-plan', headers=headers, json=body)


def test_manual_expiry_preserves_account_sessions_keys_and_billing_history(state):
    client, sessions, uid, clock = state
    login(client, owner=False)
    customer_cookie = client.cookies.get('bp_session')
    # Signing in another account in the same browser revokes its prior cookie.
    # Model the customer's separate browser before creating the admin session.
    client.cookies.clear()
    headers = login(client)
    body = snapshot(sessions, uid)
    with sessions() as session:
        user = session.get(db.User, uid)
        before = {column.name: getattr(user, column.name) for column in db.User.__table__.columns}
        session_hash = db.session_digest(customer_cookie)
        assert session.get(db.AuthSession, session_hash)
        receipt = session.query(db.PaymentRequest).filter_by(user_id=uid).one()
        receipt_before = {column.name: getattr(receipt, column.name) for column in db.PaymentRequest.__table__.columns}
        connection_before = session.query(db.Connection).filter_by(user_id=uid).one().token_cipher
    response = expire(client, uid, body, headers)
    assert response.status_code == 200
    result = response.json()
    assert result['already_expired'] is False
    assert result['member']['subscription_status'] == 'expired'
    assert result['member']['subscription_expires'] == clock[0]
    assert result['member']['active'] is True and result['member']['subscription_active'] is False
    with sessions() as session:
        user = session.get(db.User, uid)
        changed_fields = {'subscription_status', 'subscription_expires', 'auto_requested', 'auto_chat_greeting', 'updated', 'lock_version'}
        for field, value in before.items():
            if field not in changed_fields:
                assert getattr(user, field) == value, field
        assert not user.auto_requested and not user.auto_chat_greeting
        assert session.get(db.AuthSession, session_hash)
        receipt = session.query(db.PaymentRequest).filter_by(user_id=uid).one()
        assert {column.name: getattr(receipt, column.name) for column in db.PaymentRequest.__table__.columns} == receipt_before
        assert session.query(db.Bid).filter_by(user_id=uid, status='submitted').count() == 1
        assert session.query(db.Connection).filter_by(user_id=uid).one().token_cipher == connection_before
        event = session.query(db.Audit).filter_by(user_id=uid, action='subscription_expired_manual').one()
        assert f'Administrator {body["expected_user_id"]}' in event.message
    client.cookies.clear()
    client.cookies.set('bp_session', customer_cookie)
    assert client.get('/api/me').status_code == 200
    assert client.get('/api/billing').json()['subscription_status'] == 'expired'
    assert client.post('/api/ai-keys/test', headers=login(client, owner=False), json={'provider': 'both'}).status_code == 403
    assert client.get('/api/me').json()['user']['subscription_active'] is False


@pytest.mark.parametrize('target_enabled', [True, False])
def test_manual_expiry_never_changes_account_enabled_state(state, target_enabled):
    client, sessions, uid, _ = state
    with sessions() as session:
        session.get(db.User, uid).active = target_enabled
        session.commit()
    response = expire(client, uid, snapshot(sessions, uid), login(client))
    assert response.status_code == 200
    with sessions() as session:
        assert session.get(db.User, uid).active is target_enabled


def test_expiry_is_scoped_to_target_and_founder_is_refused(state):
    client, sessions, uid, _ = state
    headers = login(client)
    with sessions() as session:
        owner = session.query(db.User).filter_by(role='admin').one()
        owner_id = owner.id
        before = owner.password_hash, owner.plan_tier, owner.subscription_status, owner.active
    body = snapshot(sessions, uid)
    assert expire(client, owner_id, body, headers).status_code == 400
    assert expire(client, uid, body, headers).status_code == 200
    with sessions() as session:
        owner = session.get(db.User, owner_id)
        assert (owner.password_hash, owner.plan_tier, owner.subscription_status, owner.active) == before
        assert db.subscription_active(owner)


def test_member_anonymous_and_csrf_cannot_expire_a_plan(state):
    client, sessions, uid, _ = state
    body = snapshot(sessions, uid)
    assert expire(client, uid, body, {}).status_code == 401
    member_headers = login(client, owner=False)
    assert expire(client, uid, {**body, 'expected_user_id': uid}, member_headers).status_code == 403
    login(client)
    assert expire(client, uid, body, {}).status_code == 403
    assert expire(client, uid, body, {'x-csrf-token': 'wrong-token'}).status_code == 403
    with sessions() as session:
        assert session.get(db.User, uid).subscription_status == 'active'
        assert session.query(db.Audit).filter_by(action='subscription_expired_manual').count() == 0


def test_missing_target_or_changed_admin_account_fails_without_mutation(state):
    client, sessions, uid, _ = state
    headers = login(client)
    body = snapshot(sessions, uid)
    assert expire(client, uid + 1000, body, headers).status_code == 404
    assert expire(client, uid, {**body, 'expected_user_id': uid}, headers).status_code == 409
    with sessions() as session:
        assert session.get(db.User, uid).subscription_status == 'active'


@pytest.mark.parametrize('field,value', [('expected_plan_tier', 'paid_starter'),
                                        ('expected_subscription_status', 'trial'),
                                        ('expected_subscription_expires', 1)])
def test_stale_target_snapshot_does_not_expire_current_plan(state, field, value):
    client, sessions, uid, _ = state
    body = snapshot(sessions, uid)
    response = expire(client, uid, {**body, field: value}, login(client))
    assert response.status_code == 409
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.subscription_status == 'active' and user.auto_requested
        assert session.query(db.Audit).filter_by(action='subscription_expired_manual').count() == 0


def test_stale_expiry_after_payment_renewal_is_rejected(state):
    client, sessions, uid, _ = state
    body = snapshot(sessions, uid)
    with sessions() as session:
        payment = db.PaymentRequest(user_id=uid, method='bank', transaction_id='verified-renewal',
                                    amount=40, currency='USD', plan_tier='paid_pro')
        session.add(payment)
        session.commit()
        pid = payment.id
    headers = login(client)
    assert client.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 200
    assert expire(client, uid, body, headers).status_code == 409
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.subscription_expires > body['expected_subscription_expires']
        assert user.subscription_status == 'active'
        assert session.query(db.PaymentRequest).filter_by(id=pid).one().status == 'approved'


def test_duplicate_expiry_is_idempotent_and_stale_original_request_rejected(state):
    client, sessions, uid, clock = state
    headers = login(client)
    original = snapshot(sessions, uid)
    assert expire(client, uid, original, headers).status_code == 200
    clock[0] += 10
    assert expire(client, uid, original, headers).status_code == 409
    result = expire(client, uid, snapshot(sessions, uid), headers)
    assert result.status_code == 200 and result.json()['already_expired'] is True
    with sessions() as session:
        assert session.get(db.User, uid).subscription_expires == clock[0] - 10
        assert session.query(db.Audit).filter_by(action='subscription_expired_manual').count() == 1


def test_manual_expiry_preserves_an_earlier_natural_end_date(state):
    client, sessions, uid, clock = state
    with sessions() as session:
        session.get(db.User, uid).subscription_expires = clock[0] - 50
        session.commit()
    result = expire(client, uid, snapshot(sessions, uid), login(client))
    assert result.status_code == 200
    assert result.json()['member']['subscription_expires'] == clock[0] - 50


@pytest.mark.parametrize('change', [{'expected_user_id': True}, {'expected_user_id': '1'},
                                  {'expected_subscription_expires': True}, {'expected_subscription_expires': -1},
                                  {'expected_plan_tier': 'free_team'}, {'extra': 'do-not-echo-private-value'}])
def test_request_snapshot_is_strict_and_extra_values_are_not_echoed(state, change):
    client, sessions, uid, _ = state
    response = expire(client, uid, {**snapshot(sessions, uid), **change}, login(client))
    assert response.status_code == 422
    assert 'do-not-echo-private-value' not in response.text


def test_missing_snapshot_fields_are_required(state):
    client, _, uid, _ = state
    assert expire(client, uid, {}, login(client)).status_code == 422


def test_admin_can_still_review_and_renew_expired_customer(state):
    client, sessions, uid, clock = state
    headers = login(client)
    before = snapshot(sessions, uid)
    assert expire(client, uid, before, headers).status_code == 200
    with sessions() as session:
        user = session.get(db.User, uid)
        password_hash = user.password_hash
        payment = db.PaymentRequest(user_id=uid, method='bank', transaction_id='renew-after-expiry',
                                    amount=40, currency='USD', plan_tier='paid_pro')
        session.add(payment)
        session.commit()
        pid = payment.id
    assert client.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 200
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.subscription_status == 'active' and user.subscription_expires > clock[0]
        assert user.password_hash == password_hash and user.active
        assert not user.auto_requested and not user.auto_chat_greeting
