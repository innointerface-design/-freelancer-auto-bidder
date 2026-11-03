"""API and dispatch invariants for discovery, provenance and blocked clients."""
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


@pytest.fixture
def feature_client(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'features.db'),
                           connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    forbidden = Mock(side_effect=AssertionError('No live marketplace request in feature tests'))
    monkeypatch.setattr(main, 'get_client', forbidden)
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'email': 'owner@example.test',
                                               'password': 'Test-only-owner-password-123!'}).status_code == 200
        headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
        uid = client.get('/api/me').json()['user']['id']
        with sessions() as s:
            s.get(db.User, uid).skills = 'Python'
            s.add(db.Connection(user_id=uid, token_cipher=db.encrypt_secret('synthetic-token'),
                                freelancer_id='123', environment='sandbox'))
            s.commit()
        yield client, sessions, uid, headers, forbidden
    engine.dispose()


def add_project(sessions, uid, remote='901', **changes):
    raw = {'id': remote, 'title': 'Python service', 'description': 'Build a tested Python backend.',
           'skills': ['Python'], 'budget_min': 20, 'budget_max': 100, 'currency': 'USD',
           'owner_id': '456', 'project_status': 'active', 'type': 'fixed', 'environment': 'sandbox',
           'account_id': '123', 'payment_verified': True}
    raw.update(changes)
    with sessions() as s:
        j = db.Job(user_id=uid, remote_id=remote, data=json.dumps(raw), score=90,
                   decision='Strong match', reasons='["Skills matched"]')
        s.add(j)
        s.commit()
        return j.id


DRAFT = {'amount': 50, 'days': 3, 'milestone_pct': 100,
         'proposal': 'I can implement the Python backend with a scoped delivery and tests.'}


@pytest.mark.parametrize('fields', [
    {'payment_verified': False}, {'payment_verified': None},
    {'payment_verified': 'true'},
])
def test_blocked_project_cannot_submit_or_prepare(feature_client, fields):
    client, sessions, uid, headers, forbidden = feature_client
    jid = add_project(sessions, uid, **fields)
    # Saving an editable draft is allowed; creating a send/confirmation is not.
    assert client.post(f'/api/jobs/{jid}/draft', json=DRAFT, headers=headers).status_code == 200
    for endpoint, payload in [('instant-bid', DRAFT), ('prepare', {})]:
        response = client.post(f'/api/jobs/{jid}/{endpoint}', json=payload, headers=headers)
        assert response.status_code == 403, response.text
        assert 'blocked' in response.json()['detail'].lower()
    forbidden.assert_not_called()
    with sessions() as s:
        assert s.query(db.Bid).one().status == 'draft'
        assert s.query(db.Connection).one().last_bid_attempt_at == 0


def test_verification_rechecked_between_reservation_and_dispatch(feature_client):
    _, sessions, uid, _, forbidden = feature_client
    jid = add_project(sessions, uid)
    with sessions() as s:
        reservation = main.reserve_bid(s, uid, jid, draft=main.DraftIn(**DRAFT))
    with sessions() as s:
        job = s.get(db.Job, jid)
        raw = json.loads(job.data)
        raw['payment_verified'] = None
        job.data = json.dumps(raw)
        s.commit()
    with pytest.raises(HTTPException) as error:
        main.send_reserved(*reservation)
    assert error.value.status_code == 409
    assert 'blocked' in error.value.detail.lower()
    forbidden.assert_not_called()
    with sessions() as s:
        assert s.query(db.Bid).one().status == 'cancelled'


def test_job_metadata_stats_and_rescan_use_one_policy(feature_client):
    client, sessions, uid, _, _ = feature_client
    verified = add_project(sessions, uid, '901', client_verifications={'email_verified': False})
    unknown = add_project(sessions, uid, '902', payment_verified=None)
    failed = add_project(sessions, uid, '903', payment_verified=False)
    payload = client.get('/api/jobs').json()
    jobs = payload['jobs'] if isinstance(payload, dict) else payload
    rows = {row['id']: row for row in jobs}
    assert rows[verified]['verification'] == {'blocked': False, 'reasons': [], 'checks': {'payment_verified': True, 'email_verified': False}}
    assert rows[unknown]['blocked'] and rows[failed]['blocked']
    assert rows[unknown]['decision'] == 'Skip'
    assert client.get('/api/automation/status').json()['automation']['queue_count'] == 1
    stats = client.get('/api/stats').json()
    assert (stats['jobs'], stats['strong'], stats['blocked'], stats['total_jobs']) == (1, 1, 2, 3)
    with sessions() as s:
        row = s.get(db.Job, unknown)
        raw = json.loads(row.data)
        raw['payment_verified'] = True
        row.data = json.dumps(raw)
        s.commit()
    stats = client.get('/api/stats').json()
    assert (stats['jobs'], stats['blocked']) == (2, 1)


def test_catalog_uses_authenticated_account_and_checks_expected_actor(feature_client, monkeypatch):
    client, sessions, uid, headers, _ = feature_client
    catalog = Mock(return_value={'providers': {}, 'generation_verified': False})
    monkeypatch.setattr(main, 'get_model_catalog', catalog)
    response = client.get(f'/api/ai/models?refresh=true&expected_user_id={uid}')
    assert response.status_code == 200
    assert catalog.call_args.args[0].id == uid
    assert catalog.call_args.kwargs == {'force': True}
    catalog.reset_mock()
    assert client.get(f'/api/ai/models?expected_user_id={uid + 1}').status_code == 409
    catalog.assert_not_called()
    client.post('/api/logout', headers=headers)
    assert client.get('/api/ai/models').status_code == 401
    catalog.assert_not_called()


def test_model_ids_120_chars_survive_save_and_catalog_read(feature_client, monkeypatch):
    client, sessions, uid, headers, _ = feature_client
    models = {'gemini_model': 'gemini-future-'.ljust(120, 'x'), 'openai_model': 'ft:gpt-future:'.ljust(120, 'x')}
    assert client.post('/api/ai-keys', json=models, headers=headers).status_code == 200
    with sessions() as s:
        user = s.get(db.User, uid)
        assert user.gemini_model == models['gemini_model']
        assert user.openai_model == models['openai_model']
    assert client.post('/api/ai-keys', json={'openai_model': 'x' * 121}, headers=headers).status_code == 422


@pytest.mark.parametrize('ai_used,source,status', [(True, 'ai', 'generated'), (False, 'local_template', 'provider_error')])
def test_interactive_route_preserves_provenance_and_tenant_scope(feature_client, monkeypatch, ai_used, source, status):
    client, sessions, uid, headers, _ = feature_client
    jid = add_project(sessions, uid)
    result = {'proposal': DRAFT['proposal'], 'ai_used': ai_used, 'source': source, 'status': status,
              'provider': 'gemini' if ai_used else None, 'model': 'gemini-test' if ai_used else None,
              'retry_after': None, 'message': 'Safe synthetic status.', 'attempts': []}
    provider = Mock(return_value=result)
    monkeypatch.setattr(main, 'ai_proposal_result', provider)
    response = client.post(f'/api/jobs/{jid}/ai-proposal', headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert all(body[key] == value for key, value in result.items())
    assert body['suggested_amount'] > 0
    provider.reset_mock()
    with sessions() as s:
        outsider = db.User(email='other-feature@example.test', name='Other', password_hash='unused')
        s.add(outsider)
        s.commit()
        other_uid = outsider.id
    foreign = add_project(sessions, other_uid, '999')
    assert client.post(f'/api/jobs/{foreign}/ai-proposal', headers=headers).status_code == 404
    provider.assert_not_called()
