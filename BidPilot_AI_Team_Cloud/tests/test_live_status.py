"""Live status is private, read-only and never confuses an attempt with success."""
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main
from app.live_status import automation_status


@pytest.fixture
def live_state(tmp_path, monkeypatch):
    clock = [1_800_000_000]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    engine = create_engine(f'sqlite:///{tmp_path}/status.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'PLATFORM_AUTO', True)
    with TestClient(main.app) as client:
        with sessions() as session:
            uid = session.query(db.User).filter_by(role='admin').one().id
        yield client, sessions, uid, clock
    engine.dispose()


def connect(sessions, uid, now, **changes):
    with sessions() as session:
        user = session.get(db.User, uid)
        user.auto_requested = True
        connection = db.Connection(user_id=uid, token_cipher=db.encrypt_secret('synthetic-private-oauth'),
                                   environment='sandbox', freelancer_id='123', **changes)
        session.add(connection)
        session.add(db.WorkerHeartbeat(worker_key='scanner', updated_at=now))
        session.commit()
        return connection.id


def status(sessions, uid, now):
    with sessions() as session:
        return automation_status(session, uid, now=now)


def test_status_authentication_account_guard_and_no_remote_calls(live_state, monkeypatch):
    client, sessions, uid, clock = live_state
    remote = Mock(side_effect=AssertionError('Status must not call a remote API'))
    monkeypatch.setattr(main, 'get_client', remote)
    monkeypatch.setattr(main, 'scan_for_user', remote)
    assert client.get('/api/automation/status').status_code == 401
    assert client.post('/api/login', json={'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!'}).status_code == 200
    connect(sessions, uid, clock[0])
    response = client.get(f'/api/automation/status?expected_user_id={uid}')
    assert response.status_code == 200
    assert response.json()['automation']['state'] == 'watching'
    assert 'synthetic-private-oauth' not in response.text
    assert 'token_cipher' not in response.text and 'freelancer_id' not in response.text
    assert client.get(f'/api/automation/status?expected_user_id={uid + 1}').status_code == 409
    remote.assert_not_called()


def test_scan_attempt_is_not_reported_as_a_success(live_state):
    _, sessions, uid, clock = live_state
    now = clock[0]
    connect(sessions, uid, now, last_scan=now - 5, scan_claim='active-lease', scan_claim_until=now + 295)
    result = status(sessions, uid, now)
    assert result['automation']['state'] == 'scanning'
    assert result['connection']['last_scan_success_at'] == 0
    assert result['connection']['last_scan_started_at'] == now - 5
    assert result['automation']['next_scan_at'] == now + 55
    assert result['worker']['online']


@pytest.mark.parametrize('age,online', [(0, True), (35, True), (36, False), (-1, False)])
def test_heartbeat_freshness_is_independent_of_api_success(live_state, age, online):
    _, sessions, uid, clock = live_state
    connect(sessions, uid, clock[0] - age, last_scan_success_at=clock[0] - 300)
    result = status(sessions, uid, clock[0])
    assert result['worker']['online'] is online
    assert result['connection']['last_scan_success_at'] == clock[0] - 300
    assert result['automation']['state'] == ('watching' if online else 'offline')


def test_pending_count_is_own_live_account_only_and_excludes_existing_bids(live_state):
    _, sessions, uid, clock = live_state
    connect(sessions, uid, clock[0])
    with sessions() as session:
        other = db.User(email='other@example.test', name='Other', password_hash='unused')
        db.apply_plan(other, 'free_team')
        session.add(other)
        session.flush()
        base = {'environment': 'sandbox', 'account_id': '123', 'project_status': 'active'}
        variants = [{}, {'demo': True}, {'account_id': 'old'}, {'environment': 'production'},
                    {'project_status': 'closed'}, {'account_id': ''}]
        for index, override in enumerate(variants):
            session.add(db.Job(user_id=uid, remote_id=str(index), data=json.dumps({**base, **override}), score=90))
        session.add(db.Job(user_id=other.id, remote_id='other', data=json.dumps(base), score=90))
        session.add(db.Job(user_id=uid, remote_id='broken', data='not-json', score=90))
        session.add(db.Job(user_id=uid, remote_id='list', data='[]', score=90))
        session.add(db.Job(user_id=uid, remote_id='low', data=json.dumps(base), score=10))
        session.add(db.Job(user_id=uid, remote_id='skip', data=json.dumps(base), score=90, decision='Skip'))
        for value in ('draft', 'submitted', 'uncertain'):
            job = db.Job(user_id=uid, remote_id=value, data=json.dumps(base), score=90)
            session.add(job)
            session.flush()
            session.add(db.Bid(user_id=uid, job_id=job.id, status=value, proposal='saved', amount=30,
                               updated=clock[0] - 120))
        session.commit()
    result = status(sessions, uid, clock[0])
    assert result['automation']['queue_count'] == 1
    assert result['automation']['limits']['hourly_used'] == 2
    with sessions() as session:
        session.query(db.Connection).filter_by(user_id=uid).delete()
        session.commit()
    result = status(sessions, uid, clock[0])
    assert result['automation']['queue_count'] == 0
    assert result['automation']['state'] == 'disconnected'


def test_pause_cooldown_schedule_and_bid_limits_are_reported_without_mutation(live_state):
    _, sessions, uid, clock = live_state
    now = clock[0]
    cid = connect(sessions, uid, now, last_scan=now - 30)
    with sessions() as session:
        session.get(db.User, uid).freelancer_cooldown_until = now + 120
        session.commit()
    result = status(sessions, uid, now)
    assert result['automation']['state'] == 'cooldown'
    assert result['automation']['next_scan_at'] == now + 120
    with sessions() as session:
        user = session.get(db.User, uid)
        user.freelancer_cooldown_until = 0
        session.get(db.Connection, cid).api_pause_reason = 'auth_rejected'
        session.commit()
    assert status(sessions, uid, now)['automation']['state'] == 'paused'
    with sessions() as session:
        user = session.get(db.User, uid)
        session.get(db.Connection, cid).api_pause_reason = ''
        user.advanced_config = json.dumps({'schedule_enabled': True, 'timezone': 'UTC',
                                           'active_days': [0], 'start_hour': 0, 'end_hour': 1})
        session.commit()
    assert status(sessions, uid, now)['automation']['state'] == 'outside_schedule'
    with sessions() as session:
        user = session.get(db.User, uid)
        user.advanced_config = '{}'
        user.max_hourly = 0
        session.commit()
    result = status(sessions, uid, now)
    assert result['automation']['state'] == 'paused'
    assert 'Hourly bid limit' in result['automation']['reason']
    with sessions() as session:
        assert session.get(db.User, uid).auto_requested
        assert session.get(db.Connection, cid).last_scan == now - 30


def test_invalid_config_and_actual_pacing_are_visible(live_state, monkeypatch):
    _, sessions, uid, clock = live_state
    now = clock[0]
    connect(sessions, uid, now, last_bid_attempt_at=now - 10)
    monkeypatch.setattr(main, 'SCAN_SECONDS', 1)
    result = status(sessions, uid, now)
    assert result['automation']['state'] == 'waiting_gap'
    assert result['automation']['next_bid_at'] == now + 50
    assert result['automation']['scan_interval_seconds'] == 60
    with sessions() as session:
        session.get(db.User, uid).advanced_config = '{invalid'
        session.commit()
    result = status(sessions, uid, now)
    assert result['automation']['state'] == 'invalid_settings'
    assert result['automation']['enabled'] is False


def test_failed_refresh_keeps_previous_success_but_is_not_reported_healthy(live_state):
    _, sessions, uid, clock = live_state
    now = clock[0]
    cid = connect(sessions, uid, now, last_scan=now - 5, last_scan_success_at=now - 120,
                  last_error='Project refresh did not complete')
    result = status(sessions, uid, now)
    assert result['connection']['last_scan_failed'] is True
    assert result['connection']['last_scan_success_at'] == now - 120
    with sessions() as session:
        connection = session.get(db.Connection, cid)
        connection.scan_claim = 'new-attempt'
        connection.scan_claim_until = now + 300
        session.commit()
    assert status(sessions, uid, now)['connection']['last_scan_failed'] is False
