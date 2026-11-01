"""Paid renewal dates remain independent of the account's sign-in permission."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main, payment_settings


def moment(year, month, day):
    return int(datetime(year, month, day, 10, 15, tzinfo=ZoneInfo('Asia/Karachi')).timestamp())


@pytest.mark.parametrize('subscription_status', ['active', 'trial'])
def test_disabled_account_renewal_preserves_prepaid_months_and_original_anchors(
        tmp_path, monkeypatch, subscription_status):
    now = moment(2028, 2, 12)
    start = moment(2028, 1, 31)
    prepaid_end = moment(2028, 4, 30)
    password_hash = db.hash_password('Synthetic-disabled-customer-password!')
    engine = create_engine(f'sqlite:///{tmp_path}/renewal.db')
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main.time, 'time', lambda: now)
    try:
        with sessions() as session:
            owner = db.User(email='admin@example.test', name='Administrator', role='admin',
                            password_hash='Synthetic-admin-hash', plan_tier='free_team',
                            subscription_status='active')
            customer = db.User(email='disabled@example.test', name='Disabled customer', active=False,
                               password_hash=password_hash, plan_tier='paid_starter',
                               subscription_status=subscription_status, subscription_started_at=start,
                               billing_period_started=start, subscription_expires=prepaid_end,
                               auto_requested=False, auto_chat_greeting=False, auto_admin_allowed=False)
            session.add_all([owner, customer])
            session.flush()
            payment = db.PaymentRequest(user_id=customer.id, method='bank', transaction_id='settled-renewal',
                                        amount=10, currency='USD', plan_tier='paid_starter')
            session.add(payment)
            session.commit()
            owner_id, customer_id, payment_id = owner.id, customer.id, payment.id

        main.admin_decide_payment(payment_id, main.PaymentDecisionIn(action='approve'), owner_id)

        with sessions() as session:
            customer = session.get(db.User, customer_id)
            payment = session.get(db.PaymentRequest, payment_id)
            assert customer.subscription_expires == moment(2028, 5, 31)
            assert customer.subscription_started_at == customer.billing_period_started == start
            assert customer.subscription_status == 'active'
            assert customer.active is False and customer.password_hash == password_hash
            assert not customer.auto_requested and not customer.auto_chat_greeting
            assert not customer.auto_admin_allowed
            assert payment.status == 'approved'
            assert payment.access_starts_at == prepaid_end
            assert payment.access_expires_at == customer.subscription_expires
            assert payment.reviewed_by == owner_id and payment.reviewed_at == now
    finally:
        engine.dispose()


@pytest.mark.parametrize('variable,value', [
    ('PAYMENT_JAZZCASH', '03000000000\x01 Title'),
    ('PAYMENT_EASYPAISA', '03000000000\x01 Title'),
    ('PAYMENT_BANK', 'Account\x01details'),
    ('PAYMENT_STRIPE_URL', 'https://pay.example.test/a\x01b'),
    ('PAYMENT_WHATSAPP', 'invalid contact 123'),
])
def test_malformed_environment_payment_settings_fail_closed_with_admin_warning(monkeypatch, variable, value):
    for name in ('PAYMENT_JAZZCASH', 'PAYMENT_EASYPAISA', 'PAYMENT_BANK',
                 'PAYMENT_STRIPE_URL', 'PAYMENT_WHATSAPP'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)
    engine = create_engine('sqlite:///:memory:')
    db.Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine)() as session:
            public = payment_settings.public_payment_methods(session)
            assert public['channels'] == []
            assert not any(public[name] for name in ('jazzcash', 'easypaisa', 'nayapay', 'meezan',
                                                      'bank', 'stripe_url', 'whatsapp'))
            assert public['verification_mode'] == 'manual'
            admin = payment_settings._admin_response(session)
            assert admin['warnings']
            assert not any(destination['enabled'] for destination in admin['settings'].values()
                           if isinstance(destination, dict))
            assert session.query(payment_settings.PaymentConfiguration).count() == 0
    finally:
        engine.dispose()
