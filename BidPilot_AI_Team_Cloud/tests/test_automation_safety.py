"""Persistent pacing, provider cooldowns and queue dispatch regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main, worker
from app.freelancer import FreelancerAPI, FreelancerError, _retry_after, normalize_project


@pytest.fixture
def safety_database(tmp_path, monkeypatch):
    clock = [1_800_000_000]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    engine = create_engine('sqlite:///' + str(tmp_path / 'safety.db'), connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(worker, 'SessionLocal', sessions)
    db.init_db()
    with sessions() as s:
        user = s.query(db.User).filter_by(role='admin').one()
        user.skills, user.search_terms = 'Python', 'Python'
        user.min_score = 20
        user.auto_requested = True
        user.max_daily, user.max_hourly = 100, 60
        connection = db.Connection(user_id=user.id, token_cipher=db.encrypt_secret('synthetic-sandbox-token'), freelancer_id='123', environment='sandbox')
        s.add(connection)
        s.commit()
        uid, connection_id = user.id, connection.id
    yield sessions, uid, connection_id, clock
    engine.dispose()


def add_job(sessions, uid, remote, *, discovered=None, score=80):
    with sessions() as s:
        raw = {'id': str(remote), 'title': 'Python automation service', 'description': 'Implement a Python backend with tests and documentation.',
               'skills': ['Python'], 'budget_min': 20, 'budget_max': 100, 'currency': 'USD', 'owner_id': '456',
               'project_status': 'active', 'type': 'fixed', 'environment': 'sandbox', 'account_id': '123'}
        job = db.Job(user_id=uid, remote_id=str(remote), data=json.dumps(raw), score=score, decision='Strong match')
        if discovered is not None:
            job.discovered = discovered
        s.add(job)
        s.commit()
        return job.id


def draft():
    return main.DraftIn(amount=60, days=3, milestone_pct=100,
                        proposal='I can implement the requested Python service with a clear scope, tests and documentation.')


def reserve(sessions, uid, job_id):
    with sessions() as s:
        return main.reserve_bid(s, uid, job_id, draft=draft())


def test_retry_after_and_status_survive_without_remote_body_leak(monkeypatch):
    real_client = httpx.Client
    secret = 'never-reflect-this-remote-body'
    transport = httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After': '92000'}, text=secret))
    monkeypatch.setattr(httpx, 'Client', lambda *args, **kwargs: real_client(*args, transport=transport, **kwargs))
    with pytest.raises(FreelancerError) as failure:
        FreelancerAPI('synthetic-token').me()
    assert failure.value.status_code == 429
    assert failure.value.retry_after == 92000
    assert secret not in str(failure.value)
    now = 1_800_000_000
    date = format_datetime(datetime.fromtimestamp(now + 181, timezone.utc), usegmt=True)
    assert _retry_after(date, now=now) == 181
    assert _retry_after('0') == 0
    for value in ('-1', '1.5', 'inf', '9' * 200, 'not-a-date'):
        assert _retry_after(value, now=now) is None


def test_concurrent_distinct_reservations_obey_gap_even_with_large_quota(safety_database):
    sessions, uid, _, _ = safety_database
    jobs = [add_job(sessions, uid, remote) for remote in (901, 902)]
    def attempt(job_id):
        try:
            reserve(sessions, uid, job_id)
            return 200
        except main.HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, jobs)) == [200, 429]
    with sessions() as s:
        assert s.query(db.Bid).filter_by(user_id=uid, status='sending').count() == 1


def test_gap_starts_at_delayed_actual_dispatch_and_survives_restart(safety_database, monkeypatch):
    sessions, uid, connection_id, clock = safety_database
    first, second = [add_job(sessions, uid, remote) for remote in (901, 902)]
    reservation = reserve(sessions, uid, first)
    clock[0] += 100
    api = Mock()
    api.place_bid.return_value = {'bid': {'id': 7001}}
    monkeypatch.setattr(main, 'get_client', lambda *args: api)
    assert main.send_reserved(*reservation)['status'] == 'submitted'
    db.init_db()
    with sessions() as s:
        assert s.get(db.Connection, connection_id).last_bid_attempt_at == clock[0]
    clock[0] += 59
    with pytest.raises(main.HTTPException) as failure:
        reserve(sessions, uid, second)
    assert failure.value.status_code == 429
    clock[0] += 1
    reserve(sessions, uid, second)
    assert api.place_bid.call_count == 1


@pytest.mark.parametrize('status,reason', [(401, 'auth_rejected'), (403, 'access_denied')])
def test_scan_auth_failure_pauses_all_account_calls_until_reconnect(safety_database, monkeypatch, status, reason):
    sessions, uid, connection_id, _ = safety_database
    failure = FreelancerError('Controlled provider rejection', status_code=status)
    scanner = Mock(side_effect=failure)
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    with pytest.raises(FreelancerError):
        main.perform_scan(uid)
    with sessions() as s:
        assert s.get(db.Connection, connection_id).api_pause_reason == reason
        user = s.get(db.User, uid)
        assert not user.auto_requested and not user.auto_chat_greeting
    db.init_db()
    with pytest.raises(main.HTTPException):
        main.perform_scan(uid)
    with pytest.raises(main.HTTPException):
        main.get_live_bids(uid)
    assert main.run_autobid_cycle(uid)['submitted'] == 0
    assert main.run_autochat_cycle(uid)['replied'] == 0
    assert scanner.call_count == 1
    api = Mock()
    api.me.return_value = {'id': 123, 'username': 'verified-user', 'jobs': []}
    monkeypatch.setattr(main, 'FreelancerAPI', lambda *args: api)
    main.connect(main.ConnectionIn(token='replacement-synthetic-token', environment='sandbox'), uid)
    with sessions() as s:
        connection = s.get(db.Connection, connection_id)
        assert connection.api_pause_reason == ''
        main.require_freelancer_available(connection, s.get(db.User, uid))


def test_provider_retry_after_survives_disconnect_and_blocks_reconnect(safety_database, monkeypatch):
    sessions, uid, connection_id, clock = safety_database
    with sessions() as s:
        connection = s.get(db.Connection, connection_id)
        snapshot = (connection.environment, connection.freelancer_id, connection.token_cipher)
    main.record_freelancer_failure(uid, connection_id, snapshot, FreelancerError('Rate limited', status_code=429, retry_after=92000))
    main.disconnect(uid)
    db.init_db()
    never_call = Mock(side_effect=AssertionError('Cooldown reached the provider'))
    monkeypatch.setattr(main, 'FreelancerAPI', never_call)
    with pytest.raises(main.HTTPException) as failure:
        main.connect(main.ConnectionIn(token='synthetic-new-token', environment='sandbox'), uid)
    assert failure.value.status_code == 429
    with sessions() as s:
        user = s.get(db.User, uid)
        assert user.freelancer_cooldown_until == clock[0] + 92000
        public = db.public_user(user)
        assert public['api_cooldown_until'] == clock[0] + 92000
        assert public['api_pause_reason'] == 'rate_limited'
    never_call.assert_not_called()


def test_stale_api_failure_cannot_pause_replaced_connection(safety_database):
    sessions, uid, connection_id, _ = safety_database
    with sessions() as s:
        connection = s.get(db.Connection, connection_id)
        old_snapshot = (connection.environment, connection.freelancer_id, connection.token_cipher)
        connection.token_cipher = db.encrypt_secret('replacement-token')
        s.commit()
    main.record_freelancer_failure(uid, connection_id, old_snapshot, FreelancerError('Old failure', status_code=403))
    with sessions() as s:
        assert s.get(db.Connection, connection_id).api_pause_reason == ''


@pytest.mark.parametrize('status,reason', [(401, 'auth_rejected'), (403, 'access_denied'), (429, 'rate_limited'), (None, 'uncertain_result')])
def test_failed_automatic_send_stops_queue_without_retry(safety_database, monkeypatch, status, reason):
    sessions, uid, connection_id, _ = safety_database
    jobs = [add_job(sessions, uid, remote) for remote in (901, 902)]
    api = Mock()
    api.place_bid.side_effect = FreelancerError('Controlled send failure', status_code=status, retry_after=600)
    monkeypatch.setattr(main, 'get_client', lambda *args: api)
    proposer = Mock(return_value=draft().proposal)
    monkeypatch.setattr(main, 'ai_proposal', proposer)
    result = main.run_autobid_cycle(uid)
    assert result['submitted'] == 0
    assert api.place_bid.call_count == 1
    assert proposer.call_count == 1
    with sessions() as s:
        bids = s.query(db.Bid).filter_by(user_id=uid).all()
        assert len(bids) == 1 and bids[0].status == 'uncertain' and bids[0].job_id == jobs[0]
        assert s.get(db.Connection, connection_id).api_pause_reason == reason
    assert main.run_autobid_cycle(uid)['submitted'] == 0
    assert api.place_bid.call_count == 1


def test_fifo_excludes_consumed_jobs_before_candidate_selection(safety_database, monkeypatch):
    sessions, uid, _, clock = safety_database
    with sessions() as s:
        for offset in range(105):
            job = db.Job(user_id=uid, remote_id=str(1000 + offset), data='{}', score=99, decision='Strong match', discovered=clock[0] - 1000)
            s.add(job)
            s.flush()
            s.add(db.Bid(user_id=uid, job_id=job.id, amount=60, proposal=draft().proposal, status='submitted', updated=clock[0] - 1000))
        s.commit()
    first = add_job(sessions, uid, 2001, discovered=clock[0] - 120, score=75)
    add_job(sessions, uid, 2002, discovered=clock[0] - 60, score=99)
    api = Mock()
    api.place_bid.return_value = {'bid': {'id': 7001}}
    monkeypatch.setattr(main, 'get_client', lambda *args: api)
    monkeypatch.setattr(main, 'ai_proposal', lambda *args: draft().proposal)
    # Quotas count the previous local submissions, so use an earlier billing day.
    with sessions() as s:
        s.query(db.Bid).update({'updated': clock[0] - 90000}, synchronize_session=False)
        s.commit()
    assert main.run_autobid_cycle(uid)['submitted'] == 1
    assert api.place_bid.call_args.args[0] == 2001
    with sessions() as s:
        assert s.query(db.Bid).filter_by(job_id=first).one().status == 'submitted'


def test_advanced_filter_change_cancels_reserved_bid_before_request(safety_database, monkeypatch):
    sessions, uid, _, _ = safety_database
    job_id = add_job(sessions, uid, 901)
    reservation = reserve(sessions, uid, job_id)
    with sessions() as s:
        user = s.get(db.User, uid)
        user.advanced_config = json.dumps({'allowed_currencies': ['EUR']})
        s.commit()
    never_call = Mock(side_effect=AssertionError('Blocked dispatch reached network'))
    monkeypatch.setattr(main, 'get_client', never_call)
    with pytest.raises(main.HTTPException) as failure:
        main.send_reserved(*reservation)
    assert failure.value.status_code == 409
    never_call.assert_not_called()
    with sessions() as s:
        assert s.query(db.Bid).filter_by(job_id=job_id).one().status == 'cancelled'


def test_worker_drains_pending_queue_without_scanning_between_intervals(safety_database, monkeypatch):
    sessions, uid, connection_id, clock = safety_database
    with sessions() as s:
        s.get(db.Connection, connection_id).last_scan = clock[0] - 10
        s.commit()
    scanner, queue = Mock(), Mock(return_value={'submitted': 0})
    monkeypatch.setattr(worker, 'perform_scan', scanner)
    monkeypatch.setattr(worker, 'run_autobid_cycle', queue)
    worker.once()
    scanner.assert_not_called()
    queue.assert_called_once_with(uid)


def test_normalized_client_username_is_not_display_name():
    result = normalize_project({'id': 901}, {'display_name': 'Not the username', 'username': 'actual_client'})
    assert result['client_username'] == 'actual_client'


def test_initial_connection_429_cannot_be_retried_without_wait(safety_database, monkeypatch):
    sessions, uid, _, clock = safety_database
    main.disconnect(uid)
    api = Mock()
    api.me.side_effect = FreelancerError('Connection verification rate limited', status_code=429, retry_after=123456)
    factory = Mock(return_value=api)
    monkeypatch.setattr(main, 'FreelancerAPI', factory)
    request = main.ConnectionIn(token='synthetic-first-token', environment='sandbox')
    for _ in range(2):
        with pytest.raises(main.HTTPException) as failure:
            main.connect(request, uid)
        assert failure.value.status_code == 429
    assert api.me.call_count == 1
    with sessions() as s:
        assert s.get(db.User, uid).freelancer_cooldown_until == clock[0] + 123456
        assert s.query(db.Connection).filter_by(user_id=uid).count() == 0


def test_stale_rate_limit_preserves_wait_without_poisoning_new_connection(safety_database):
    sessions, uid, connection_id, clock = safety_database
    with sessions() as s:
        connection = s.get(db.Connection, connection_id)
        old_snapshot = (connection.environment, connection.freelancer_id, connection.token_cipher)
        connection.token_cipher = db.encrypt_secret('replacement-token')
        s.commit()
        current_cipher = connection.token_cipher
    main.record_freelancer_failure(uid, connection_id, old_snapshot,
                                  FreelancerError('Old rate limit', status_code=429, retry_after=420))
    with sessions() as s:
        connection, user = s.get(db.Connection, connection_id), s.get(db.User, uid)
        assert connection.api_pause_reason == ''
        assert connection.token_cipher == current_cipher
        assert user.auto_requested is True
        assert user.freelancer_cooldown_until == clock[0] + 420
        with pytest.raises(main.HTTPException) as failure:
            main.require_freelancer_available(connection, user)
        assert failure.value.status_code == 429
