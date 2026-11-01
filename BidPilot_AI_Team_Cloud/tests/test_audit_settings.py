"""Expired automation stays off and malformed older project rows remain editable."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


@pytest.fixture
def state(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/settings.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with TestClient(main.app) as client:
        with sessions() as session:
            user = db.User(email='settings-audit@example.test', name='Settings customer',
                           password_hash=db.hash_password('Settings-only-password-123!'))
            db.apply_plan(user, 'paid_pro')
            session.add(user)
            session.commit()
            uid = user.id
        assert client.post('/api/login', json={'email': 'settings-audit@example.test',
                                              'password': 'Settings-only-password-123!'}).status_code == 200
        headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
        with sessions() as session:
            user = session.get(db.User, uid)
            payload = {key: getattr(user, key) for key in main.SettingsIn.model_fields}
        yield client, sessions, uid, headers, payload
    engine.dispose()


@pytest.mark.parametrize('flag', ['auto_requested', 'auto_chat_greeting'])
def test_expired_subscription_cannot_reenable_automation_via_settings(state, flag):
    client, sessions, uid, headers, payload = state
    with sessions() as session:
        user = session.get(db.User, uid)
        user.subscription_status = 'expired'
        user.subscription_expires = int(time.time()) - 1
        session.commit()
    response = client.post('/api/settings', headers=headers,
                           json={**payload, flag: True, 'name': 'Unsaved replacement'})
    assert response.status_code == 403
    with sessions() as session:
        user = session.get(db.User, uid)
        assert not user.auto_requested and not user.auto_chat_greeting
        assert user.name == 'Settings customer'
        assert user.active and user.subscription_status == 'expired'


@pytest.mark.parametrize('data', ['{invalid-json', 'null', '[1, 2, 3]',
                                '{"title":"Older project","budget_max":NaN}',
                                '{"title":"Older project","budget_max":1e999}',
                                '{"nested":' * 2000 + '0' + '}' * 2000])
def test_invalid_older_project_does_not_break_settings_save(state, data):
    client, sessions, uid, headers, payload = state
    with sessions() as session:
        session.add(db.Job(user_id=uid, remote_id='older-project', data=data, score=90))
        session.commit()
    response = client.post('/api/settings', headers=headers, json={**payload, 'name': 'Updated customer'})
    assert response.status_code == 200
    listing = client.get('/api/jobs')
    assert listing.status_code == 200
    public = listing.json()['jobs'][0]
    assert public['data_error'] and public['decision'] == 'Skip' and public['score'] == 0
    assert client.post(f'/api/jobs/{public["id"]}/proposal', headers=headers).status_code == 409
    with sessions() as session:
        assert session.get(db.User, uid).name == 'Updated customer'
        job = session.query(db.Job).filter_by(user_id=uid).one()
        assert job.score == 0 and job.decision == 'Skip'


def test_extreme_stored_budget_cannot_crash_bid_validation():
    assert main.valid_amount({'budget_min': 1, 'budget_max': 10 ** 400}, 100) is False


def test_project_corrupted_after_reservation_cancels_before_any_external_request(state, monkeypatch):
    _, sessions, uid, _, _ = state
    cipher = db.encrypt_secret('synthetic-nonprovider-token')
    with sessions() as session:
        connection = db.Connection(user_id=uid, environment='sandbox', freelancer_id='123', token_cipher=cipher)
        job = db.Job(user_id=uid, remote_id='456', data='{invalid-after-reservation')
        session.add_all([connection, job])
        session.flush()
        bid = db.Bid(user_id=uid, job_id=job.id, status='sending', source='manual', intent='private-nonce',
                     amount=50, days=3, proposal='A sufficiently detailed synthetic proposal for this project.')
        session.add(bid)
        session.commit()
        bid_id, connection_id = bid.id, connection.id
    monkeypatch.setattr(main, 'get_client', lambda *args: pytest.fail('Corrupted project must never reach Freelancer'))
    with pytest.raises(main.HTTPException) as error:
        main.send_reserved(bid_id, '456', connection_id, 'sandbox', '123', cipher, 'private-nonce')
    assert error.value.status_code == 409 and 'no bid request was sent' in error.value.detail
    with sessions() as session:
        assert session.get(db.Bid, bid_id).status == 'cancelled'
        assert session.query(db.Audit).filter_by(user_id=uid, action='bid_cancelled').count() == 1
