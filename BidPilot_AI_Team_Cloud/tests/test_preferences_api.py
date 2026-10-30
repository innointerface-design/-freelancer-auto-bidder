"""Preference restore is atomic, tenant-scoped and cannot restore credentials or permissions."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


@pytest.fixture
def client_state(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/preferences.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with TestClient(main.app) as client:
        yield client, sessions
    engine.dispose()


def sign_in(client, email='owner@example.test', password='Test-only-owner-password-123!'):
    response = client.post('/api/login', json={'email': email, 'password': password})
    assert response.status_code == 200
    return {'x-csrf-token': client.get('/api/csrf').json()['csrf']}


def test_preferences_require_authentication_and_csrf(client_state):
    client, _ = client_state
    assert client.get('/api/advanced-settings').status_code == 401
    assert client.get('/api/settings/export').status_code == 401
    sign_in(client)
    assert client.post('/api/advanced-settings', json={}).status_code == 403
    assert client.post('/api/settings/import', json={}).status_code == 403


def test_advanced_settings_normalize_and_rescore_without_changing_keys(client_state):
    client, sessions = client_state
    headers = sign_in(client)
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        owner.openai_key_cipher = db.encrypt_secret('synthetic-preserved-openai-secret')
        raw = {'id': '801', 'title': 'Python service', 'description': 'Python service implementation',
               'skills': ['Python'], 'currency': 'EUR', 'budget_min': 20, 'budget_max': 100,
               'type': 'fixed', 'country_code': 'DE'}
        owner.skills = 'Python'
        s.add(db.Job(user_id=owner.id, remote_id='801', data=json.dumps(raw), score=90))
        s.commit()
        original = owner.openai_key_cipher
    response = client.post('/api/advanced-settings', headers=headers, json={
        'allowed_currencies': ['usd', 'USD'], 'excluded_countries': ['gb'],
        'min_bid_interval_seconds': 90, 'manual_review_keywords': ['NDA'],
    })
    assert response.status_code == 200, response.text
    result = response.json()['settings']
    assert result['allowed_currencies'] == ['USD'] and result['excluded_countries'] == ['GB']
    assert client.get('/api/advanced-settings').json()['settings'] == result
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        assert owner.openai_key_cipher == original
        assert s.query(db.Job).filter_by(user_id=owner.id).one().decision == 'Skip'


@pytest.mark.parametrize('invalid', [
    {'min_bid_interval_seconds': 1}, {'timezone': 'not/a/timezone'},
    {'search_fields': []}, {'allowed_currencies': ['US']},
    {'default_template': 'Please use {client_name.__class__} for this project.'},
    {'role': 'admin'}, {'openai_key': 'SYNTHETIC-DO-NOT-ECHO'},
])
def test_invalid_advanced_settings_are_rejected_without_input_echo(client_state, invalid):
    client, _ = client_state
    response = client.post('/api/advanced-settings', headers=sign_in(client), json=invalid)
    assert response.status_code == 422
    assert 'SYNTHETIC-DO-NOT-ECHO' not in response.text


def test_export_is_credential_free_and_restore_preserves_security_and_billing(client_state):
    client, sessions = client_state
    headers = sign_in(client)
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        owner.gemini_key_cipher = db.encrypt_secret('synthetic-gemini-private')
        owner.openai_key_cipher = db.encrypt_secret('synthetic-openai-private')
        owner.auto_requested = owner.auto_chat_greeting = True
        connection = db.Connection(user_id=owner.id, token_cipher=db.encrypt_secret('synthetic-freelancer-private'),
                                   environment='sandbox', freelancer_id='123')
        s.add(connection)
        s.commit()
        before = (owner.password_hash, owner.role, owner.plan_tier, owner.subscription_expires,
                  owner.gemini_key_cipher, owner.openai_key_cipher, connection.token_cipher)
    exported = client.get('/api/settings/export')
    assert exported.status_code == 200
    payload = exported.json()
    assert set(payload) == {'format', 'version', 'settings', 'advanced'}
    assert not payload['settings']['auto_requested'] and not payload['settings']['auto_chat_greeting']
    for private in ('synthetic-gemini-private', 'synthetic-openai-private', 'synthetic-freelancer-private', *before):
        if isinstance(private, str) and len(private) > 25:
            assert private not in exported.text
    assert not {'role', 'password_hash', 'plan_tier', 'openai_key', 'token_cipher'} & set(payload['settings'])
    payload['settings'].update(name='Restored display name', auto_requested=True, auto_chat_greeting=True)
    payload['advanced'].update(min_bid_interval_seconds=180, allowed_currencies=['USD'])
    restored = client.post('/api/settings/import', headers=headers, json=payload)
    assert restored.status_code == 200, restored.text
    assert restored.json()['automation_paused']
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        connection = s.query(db.Connection).filter_by(user_id=owner.id).one()
        after = (owner.password_hash, owner.role, owner.plan_tier, owner.subscription_expires,
                 owner.gemini_key_cipher, owner.openai_key_cipher, connection.token_cipher)
        assert before == after
        assert owner.name == 'Restored display name'
        assert not owner.auto_requested and not owner.auto_chat_greeting
        assert json.loads(owner.advanced_config)['min_bid_interval_seconds'] == 180


@pytest.mark.parametrize('location', ['top', 'settings', 'advanced'])
def test_restore_cannot_inject_keys_roles_or_partial_settings(client_state, location):
    client, sessions = client_state
    headers = sign_in(client)
    payload = client.get('/api/settings/export').json()
    payload['settings']['name'] = 'MUST NOT COMMIT'
    target = payload if location == 'top' else payload[location]
    target['openai_key_cipher'] = 'SYNTHETIC-PRIVATE-FIELD'
    response = client.post('/api/settings/import', headers=headers, json=payload)
    assert response.status_code == 422
    assert 'SYNTHETIC-PRIVATE-FIELD' not in response.text
    with sessions() as s:
        assert s.query(db.User).filter_by(role='admin').one().name != 'MUST NOT COMMIT'


def test_restore_rejects_oversized_malformed_and_duplicate_json(client_state):
    client, _ = client_state
    headers = sign_in(client)
    assert client.post('/api/settings/import', headers=headers, content=b' ' * (128 * 1024 + 1)).status_code == 413
    for content in ('{"format":"bidpilot-settings","format":"other"}', '{"settings": NaN}', '[invalid'):
        assert client.post('/api/settings/import', headers=headers, content=content).status_code == 422


def test_restore_rejects_in_flight_mutation_and_does_not_clear_provider_pause(client_state):
    client, sessions = client_state
    headers = sign_in(client)
    payload = client.get('/api/settings/export').json()
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        connection = db.Connection(user_id=owner.id, token_cipher=db.encrypt_secret('synthetic-token'),
                                   environment='sandbox', freelancer_id='123', api_pause_reason='access_denied')
        s.add(connection)
        job = db.Job(user_id=owner.id, remote_id='808', data='{}')
        s.add(job)
        s.flush()
        bid = db.Bid(user_id=owner.id, job_id=job.id, amount=50, proposal='A test-only controlled proposal.', status='sending')
        s.add(bid)
        s.commit()
    assert client.post('/api/settings/import', headers=headers, json=payload).status_code == 409
    with sessions() as s:
        s.query(db.Bid).update({'status': 'uncertain'})
        s.commit()
    assert client.post('/api/settings/import', headers=headers, json=payload).status_code == 200
    with sessions() as s:
        assert s.query(db.Connection).one().api_pause_reason == 'access_denied'


def test_preferences_are_tenant_scoped(client_state):
    client, sessions = client_state
    headers = sign_in(client)
    assert client.post('/api/advanced-settings', headers=headers, json={'excluded_clients': ['private-owner-client']}).status_code == 200
    with sessions() as s:
        user = db.User(email='member@example.test', name='Member', password_hash=db.hash_password('Member-only-password-123!'))
        db.apply_plan(user, 'paid_starter', 'inactive')
        s.add(user)
        s.commit()
    sign_in(client, 'member@example.test', 'Member-only-password-123!')
    advanced = client.get('/api/advanced-settings').json()['settings']
    assert advanced['excluded_clients'] == []
    assert 'private-owner-client' not in client.get('/api/settings/export').text
