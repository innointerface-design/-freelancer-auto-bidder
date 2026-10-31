"""Payment destinations are editable only by the operator and validated before publishing."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main
from app import payment_settings as payments


@pytest.fixture
def client_state(tmp_path, monkeypatch):
    for name in ('PAYMENT_JAZZCASH', 'PAYMENT_EASYPAISA', 'PAYMENT_BANK', 'PAYMENT_STRIPE_URL', 'PAYMENT_WHATSAPP'):
        monkeypatch.delenv(name, raising=False)
    engine = create_engine(f'sqlite:///{tmp_path}/destinations.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    if not any(getattr(route, 'path', '') == '/api/admin/payment-settings' for route in main.app.routes):
        payments.install_payment_settings_routes(main.app, main.current, main.mutate, main.admin_only)
    with TestClient(main.app) as client:
        with sessions() as session:
            uid = session.query(db.User).filter_by(role='admin').one().id
        yield client, sessions, uid
    engine.dispose()


def login(client, email='owner@example.test', password='Test-only-owner-password-123!'):
    response = client.post('/api/login', json={'email': email, 'password': password})
    assert response.status_code == 200, response.text
    return {'x-csrf-token': client.get('/api/csrf').json()['csrf']}


def payload():
    return {'settings': payments.default_settings().model_dump()}


def test_provided_destinations_are_available_without_implicit_configuration_writes(client_state):
    _, sessions, _ = client_state
    with sessions() as session:
        result = payments.public_payment_methods(session)
        assert result['jazzcash'] == result['easypaisa'] == '03128315518'
        assert result['nayapay'] == 'PK78NAYA1234503128315518'
        assert result['meezan'] == 'PK81MEZN0029040112662811'
        assert {channel['id'] for channel in result['channels']} == {'jazzcash', 'easypaisa', 'nayapay', 'meezan'}
        assert result['verification_mode'] == 'manual'
        assert not payments.payment_method_configured(session, 'bank')
        assert not payments.payment_method_configured(session, 'stripe')
        assert not payments.payment_method_configured(session, 'unknown')
        assert session.query(payments.PaymentConfiguration).count() == 0


def test_admin_routes_require_authentication_csrf_and_role(client_state):
    client, sessions, _ = client_state
    assert client.get('/api/admin/payment-settings').status_code == 401
    headers = login(client)
    assert client.post('/api/admin/payment-settings', json=payload()).status_code == 403
    assert client.post('/api/admin/payment-settings', headers=headers, json=payload()).status_code == 200
    with sessions() as session:
        user = db.User(email='customer@example.test', name='Customer',
                       password_hash=db.hash_password('Customer-password-123!'))
        db.apply_plan(user, 'paid_starter', 'inactive')
        session.add(user)
        session.commit()
    customer_headers = login(client, 'customer@example.test', 'Customer-password-123!')
    assert client.get('/api/admin/payment-settings').status_code == 403
    assert client.post('/api/admin/payment-settings', headers=customer_headers, json=payload()).status_code == 403


def test_saved_settings_persist_and_explicit_disable_wins_over_environment(client_state, monkeypatch):
    client, sessions, uid = client_state
    headers = login(client)
    body = payload()
    body['settings']['jazzcash']['enabled'] = False
    body['settings']['nayapay']['account_title'] = 'Actual merchant title entered by operator'
    body['expected_user_id'] = uid
    response = client.post('/api/admin/payment-settings', headers=headers, json=body)
    assert response.status_code == 200, response.text
    monkeypatch.setenv('PAYMENT_JAZZCASH', '03999999999')
    db.init_db()
    with sessions() as session:
        result = payments.public_payment_methods(session)
        assert result['jazzcash'] == '' and not payments.payment_method_configured(session, 'jazzcash')
        assert next(channel for channel in result['channels'] if channel['id'] == 'nayapay')['account_title'] == body['settings']['nayapay']['account_title']
        row = session.get(payments.PaymentConfiguration, 1)
        assert row.updated_by == uid and row.updated_at > 0
        assert json.loads(row.payload)['jazzcash']['enabled'] is False
        entry = session.query(db.Audit).filter_by(action='payment_settings_updated').one()
        assert body['settings']['nayapay']['iban'] not in entry.message
    assert client.get('/api/admin/payment-settings').json()['settings'] == body['settings']


def test_existing_operator_environment_destinations_are_preserved_before_first_save(client_state, monkeypatch):
    _, sessions, _ = client_state
    monkeypatch.setenv('PAYMENT_BANK', 'Existing configured bank account details')
    monkeypatch.setenv('PAYMENT_JAZZCASH', '03000000000 — Merchant title')
    monkeypatch.setenv('PAYMENT_STRIPE_URL', 'https://buy.stripe.com/example')
    with sessions() as session:
        result = payments.public_payment_methods(session)
        assert result['bank'] == 'Existing configured bank account details'
        assert result['jazzcash'] == '03000000000 — Merchant title'
        assert result['stripe_url'] == 'https://buy.stripe.com/example'
        assert payments.payment_method_configured(session, 'bank')


@pytest.mark.parametrize('field,value', [
    ('nayapay', 'PK78NAYA1234503128315519'),
    ('meezan', 'PK81MEZN002904112662811'),
    ('meezan', 'PK78NAYA1234503128315518'),
    ('nayapay', 'PK81MEZN0029040112662811'),
    ('nayapay', 'GB82WEST12345698765432'),
])
def test_invalid_or_wrong_bank_iban_cannot_be_published(client_state, field, value):
    client, sessions, _ = client_state
    body = payload()
    body['settings'][field]['iban'] = value
    response = client.post('/api/admin/payment-settings', headers=login(client), json=body)
    assert response.status_code == 422
    assert value not in response.text
    with sessions() as session:
        assert session.query(payments.PaymentConfiguration).count() == 0


def test_disabled_invalid_draft_is_admin_only_and_can_be_corrected(client_state):
    client, sessions, _ = client_state
    body = payload()
    body['settings']['meezan'].update(enabled=False, iban='PK81MEZN002904112662811')
    response = client.post('/api/admin/payment-settings', headers=login(client), json=body)
    assert response.status_code == 200 and response.json()['warnings']
    assert response.json()['settings']['meezan']['iban'] == 'PK81MEZN002904112662811'
    with sessions() as session:
        result = payments.public_payment_methods(session)
        assert result['meezan'] == ''
        assert 'PK81MEZN002904112662811' not in json.dumps(result)
        assert not payments.payment_method_configured(session, 'meezan')


@pytest.mark.parametrize('field,value', [
    ('number', '0300'), ('number', 'not-a-phone'), ('number', '03128315518\nmalicious'),
    ('enabled', 'false'), ('account_title', 'Invalid\x00title'),
])
def test_wallet_validation_and_strict_boolean_inputs(client_state, field, value):
    client, _, _ = client_state
    body = payload()
    body['settings']['jazzcash'][field] = value
    assert client.post('/api/admin/payment-settings', headers=login(client), json=body).status_code == 422


def test_wallet_and_iban_formatting_are_normalized(client_state):
    client, _, _ = client_state
    body = payload()
    body['settings']['jazzcash']['number'] = '+92 312-8315518'
    body['settings']['nayapay']['iban'] = 'pk78 naya 1234 5031 2831 5518'
    response = client.post('/api/admin/payment-settings', headers=login(client), json=body)
    assert response.status_code == 200
    assert response.json()['settings']['jazzcash']['number'] == '03128315518'
    assert response.json()['settings']['nayapay']['iban'] == 'PK78NAYA1234503128315518'


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.test', 'https://user:password@example.test/pay',
                                 'https://example.test:bad/pay', 'https://example.test/unsafe path'])
def test_enabled_payment_links_require_safe_https(client_state, url):
    client, _, _ = client_state
    body = payload()
    body['settings']['stripe'].update(enabled=True, url=url)
    response = client.post('/api/admin/payment-settings', headers=login(client), json=body)
    assert response.status_code == 422 and url not in response.text


def test_expected_account_guards_prevent_stale_admin_updates(client_state):
    client, sessions, uid = client_state
    headers = login(client)
    assert client.get(f'/api/admin/payment-settings?expected_user_id={uid + 1}').status_code == 409
    body = payload()
    body['expected_user_id'] = uid + 1
    assert client.post('/api/admin/payment-settings', headers=headers, json=body).status_code == 409
    with sessions() as session:
        assert session.query(payments.PaymentConfiguration).count() == 0


def test_passwords_credentials_and_unknown_admin_fields_are_not_accepted(client_state):
    client, _, _ = client_state
    body = payload()
    body['settings']['password'] = 'PRIVATE-INPUT-MUST-NOT-ECHO'
    response = client.post('/api/admin/payment-settings', headers=login(client), json=body)
    assert response.status_code == 422
    assert 'PRIVATE-INPUT-MUST-NOT-ECHO' not in response.text


def test_corrupt_stored_settings_fail_closed_without_env_or_default_fallback(client_state, monkeypatch):
    client, sessions, uid = client_state
    with sessions() as session:
        session.add(payments.PaymentConfiguration(id=1, payload='invalid-json', updated_by=uid))
        session.commit()
    monkeypatch.setenv('PAYMENT_BANK', 'Must not silently fall back to this account')
    with sessions() as session:
        public = payments.public_payment_methods(session)
        assert public['channels'] == [] and not public['bank']
    login(client)
    response = client.get('/api/admin/payment-settings')
    assert response.status_code == 200 and response.json()['warnings']
    assert not any(value['enabled'] for key, value in response.json()['settings'].items() if isinstance(value, dict))
