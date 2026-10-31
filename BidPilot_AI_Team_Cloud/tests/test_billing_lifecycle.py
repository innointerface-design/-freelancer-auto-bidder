"""Calendar subscriptions are clock-enforced, auditable and separate from payment claims."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import db, main
from app.billing_lifecycle import (
    billing_period_start, expire_due_subscriptions, expire_subscriptions,
    next_calendar_month, subscription_start,
)


def moment(year, month, day, hour=10):
    return int(datetime(year, month, day, hour, 15, 0, tzinfo=ZoneInfo('Asia/Karachi')).timestamp())


@pytest.fixture
def state(tmp_path, monkeypatch):
    clock = [moment(2028, 1, 31)]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    engine = create_engine(f'sqlite:///{tmp_path}/billing.db', connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setenv('PAYMENT_BANK', 'Synthetic bank destination')
    with TestClient(main.app) as client:
        with sessions() as session:
            user = db.User(email='subscriber@example.test', name='Subscriber Full Name',
                           password_hash=db.hash_password('Subscriber-password-123!'))
            db.apply_plan(user, 'paid_starter', 'inactive')
            session.add(user)
            session.commit()
            uid = user.id
        yield client, sessions, uid, clock
    engine.dispose()


def login(client, owner=True):
    email, password = ('owner@example.test', 'Test-only-owner-password-123!') if owner else ('subscriber@example.test', 'Subscriber-password-123!')
    response = client.post('/api/login', json={'email': email, 'password': password})
    assert response.status_code == 200
    return {'x-csrf-token': client.get('/api/csrf').json()['csrf']}


def receipt(sessions, uid, reference='payment-1'):
    with sessions() as session:
        row = db.PaymentRequest(user_id=uid, method='bank', transaction_id=reference,
                                amount=10, currency='USD', plan_tier='paid_starter')
        session.add(row)
        session.commit()
        return row.id


@pytest.mark.parametrize('start,expected', [
    ((2027, 1, 31), (2027, 2, 28)), ((2028, 1, 31), (2028, 2, 29)),
    ((2028, 2, 29), (2028, 3, 29)), ((2027, 12, 31), (2028, 1, 31)),
    ((2028, 3, 30), (2028, 4, 30)),
])
def test_next_month_uses_karachi_calendar_and_same_wall_time(start, expected):
    assert next_calendar_month(moment(*start)) == moment(*expected)


def test_unpaid_signup_does_not_report_an_activation_date(state):
    client, sessions, uid, _ = state
    login(client, owner=False)
    response = client.get(f'/api/billing?expected_user_id={uid}').json()
    assert response['subscription_started_at'] == 0
    assert response['subscription_expires'] == 0 and not response['subscription_active']
    with sessions() as session:
        assert subscription_start(session.get(db.User, uid)) == 0


def test_verified_approval_records_dates_without_password_or_admin_permission_override(state):
    client, sessions, uid, clock = state
    with sessions() as session:
        user = session.get(db.User, uid)
        user.auto_admin_allowed = False
        session.commit()
    pid = receipt(sessions, uid)
    headers = login(client)
    reviewed = client.get('/api/admin/payments').json()['payments'][0]
    assert reviewed['user_name'] == 'Subscriber Full Name'
    assert reviewed['user_email'] == 'subscriber@example.test'
    assert 'password' not in str(reviewed).lower()
    result = client.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'})
    assert result.status_code == 200
    assert result.json()['subscription_started_at'] == clock[0]
    assert result.json()['subscription_expires'] == moment(2028, 2, 29)
    assert client.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 409
    with sessions() as session:
        user = session.get(db.User, uid)
        payment = session.get(db.PaymentRequest, pid)
        assert not user.auto_admin_allowed and not user.auto_requested
        assert payment.access_starts_at == clock[0]
        assert payment.access_expires_at == user.subscription_expires
        assert payment.reviewed_at == clock[0] and payment.reviewed_by > 0
        assert db.verify_password('Subscriber-password-123!', user.password_hash)


def test_renewal_keeps_original_day_and_quota_anchor_without_month_end_drift(state):
    client, sessions, uid, clock = state
    with sessions() as session:
        db.apply_plan(session.get(db.User, uid), 'paid_starter', now=clock[0])
        session.commit()
    headers = login(client)
    pid = receipt(sessions, uid)
    assert client.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 200
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.subscription_expires == moment(2028, 3, 31)
        assert user.subscription_started_at == user.billing_period_started == clock[0]
        assert session.get(db.PaymentRequest, pid).access_starts_at == moment(2028, 2, 29)
        assert billing_period_start(user, now=moment(2028, 2, 29) - 1) == moment(2028, 1, 31)
        assert billing_period_start(user, now=moment(2028, 2, 29)) == moment(2028, 2, 29)
        assert billing_period_start(user, now=moment(2028, 3, 31)) == moment(2028, 3, 31)


def test_exact_expiry_blocks_access_and_persists_once_even_without_worker(state):
    client, sessions, uid, clock = state
    with sessions() as session:
        user = session.get(db.User, uid)
        db.apply_plan(user, 'paid_starter', now=clock[0])
        user.auto_requested = user.auto_chat_greeting = True
        end = user.subscription_expires
        session.commit()
        assert db.subscription_active(user, now=end - 1)
        assert not db.subscription_active(user, now=end)
    clock[0] = end
    login(client, owner=False)
    assert client.get('/api/me').json()['user']['subscription_status'] == 'expired'
    assert client.get('/api/billing').json()['subscription_status'] == 'expired'
    assert expire_subscriptions(now=end) == 0
    with sessions() as session:
        user = session.get(db.User, uid)
        assert not user.auto_requested and not user.auto_chat_greeting
        assert session.query(db.Audit).filter_by(user_id=uid, action='subscription_expired').count() == 1
        founder = session.query(db.User).filter_by(role='admin').one()
        assert founder.subscription_status == 'active' and db.subscription_active(founder)


def test_disabled_account_is_separate_from_subscription_expiry(state):
    _, sessions, uid, clock = state
    with sessions() as session:
        user = session.get(db.User, uid)
        db.apply_plan(user, 'paid_starter', now=clock[0])
        user.active = False
        public = db.public_user(user)
        assert public['subscription_status'] == 'active'
        assert public['subscription_active'] is False
        founder = session.query(db.User).filter_by(role='admin').one()
        founder.active = False
        public = db.public_user(founder)
        assert public['subscription_status'] == 'active'
        assert public['subscription_active'] is False


def test_two_expiry_workers_do_not_duplicate_audit_or_expire_a_renewal(state):
    _, sessions, uid, clock = state
    with sessions() as session:
        user = session.get(db.User, uid)
        db.apply_plan(user, 'paid_starter', now=clock[0])
        user.subscription_expires = clock[0] - 1
        session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: expire_subscriptions(now=clock[0]), range(2)))
    assert sum(results) == 1
    with sessions() as stale:
        stale.get(db.User, uid)
        with sessions() as renewed:
            user = renewed.get(db.User, uid)
            db.apply_plan(user, 'paid_starter', now=clock[0])
            user.auto_requested = True
            renewed.commit()
        assert expire_due_subscriptions(stale, now=clock[0]) == 0
        stale.commit()
    with sessions() as session:
        user = session.get(db.User, uid)
        assert user.subscription_status == 'active' and user.auto_requested
        assert session.query(db.Audit).filter_by(user_id=uid, action='subscription_expired').count() == 1


def test_receipt_alone_does_not_activate_and_stale_account_or_password_notes_are_rejected(state):
    client, sessions, uid, _ = state
    headers = login(client, owner=False)
    body = {'method': 'bank', 'transaction_id': 'claim-only', 'amount': 10, 'currency': 'USD',
            'plan_tier': 'paid_starter', 'expected_user_id': uid}
    assert client.post('/api/billing/submit', headers=headers, json={**body, 'expected_user_id': uid + 1}).status_code == 409
    assert client.post('/api/billing/submit', headers=headers, json={**body, 'notes': 'password=PRIVATE-DO-NOT-ECHO'}).status_code == 422
    assert client.post('/api/billing/submit', headers=headers, json={**body, 'password': 'PRIVATE-DO-NOT-ECHO'}).status_code == 422
    result = client.post('/api/billing/submit', headers=headers, json=body)
    assert result.status_code == 200
    assert client.get('/api/billing').json()['subscription_active'] is False
    with sessions() as session:
        assert session.query(db.PaymentRequest).count() == 1
        assert not db.subscription_active(session.get(db.User, uid))
    headers = login(client)
    pid = result.json()['payment_id']
    assert client.post(f'/api/admin/payments/{pid}/decide', headers=headers,
                       json={'action': 'approve', 'expected_user_id': uid}).status_code == 409
    with sessions() as session:
        assert session.get(db.PaymentRequest, pid).status == 'pending'


def test_legacy_additive_migration_preserves_expiry_password_and_encrypted_keys(state):
    _, sessions, uid, clock = state
    with sessions() as session:
        user = session.get(db.User, uid)
        db.apply_plan(user, 'paid_starter', now=clock[0])
        user.gemini_key_cipher = db.encrypt_secret('synthetic-private-gemini')
        session.commit()
        old = user.subscription_expires, user.password_hash, user.gemini_key_cipher
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE users DROP COLUMN subscription_started_at'))
        for column in ('reviewed_by', 'access_starts_at', 'access_expires_at'):
            connection.execute(text(f'ALTER TABLE payment_requests DROP COLUMN {column}'))
    db.init_db()
    db.init_db()
    assert 'subscription_started_at' in {item['name'] for item in inspect(db.engine).get_columns('users')}
    with sessions() as session:
        user = session.get(db.User, uid)
        assert (user.subscription_expires, user.password_hash, user.gemini_key_cipher) == old
        assert user.subscription_started_at == 0
        assert subscription_start(user) == clock[0]
        assert billing_period_start(user, now=clock[0] + 30 * 86400) == clock[0] + 30 * 86400
