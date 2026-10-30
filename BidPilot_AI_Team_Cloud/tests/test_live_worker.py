"""Live refresh leases, honest success timestamps and worker cadence controls."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import db, main, worker
from app.freelancer import FreelancerError

real_autobid_cycle = main.run_autobid_cycle


@pytest.fixture
def live_database(tmp_path, monkeypatch):
    clock = [1_800_000_000]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    engine = create_engine(f'sqlite:///{tmp_path}/live.db', connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(worker, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'PLATFORM_AUTO', True)
    monkeypatch.setattr(main, 'run_autobid_cycle', Mock(return_value={'submitted': 0}))
    monkeypatch.setattr(main, 'run_autochat_cycle', Mock(return_value={'replied': 0}))
    db.init_db()
    with sessions() as session:
        user = session.query(db.User).filter_by(role='admin').one()
        user.skills = user.search_terms = 'Python'
        user.auto_requested = True
        connection = db.Connection(user_id=user.id, token_cipher=db.encrypt_secret('synthetic-live-token'),
                                   environment='sandbox', freelancer_id='123')
        session.add(connection)
        session.commit()
        ids = user.id, connection.id
    yield sessions, *ids, clock
    engine.dispose()


def project(remote='8801'):
    return {'id': remote, 'title': 'Python backend service',
            'description': 'Implement the supplied Python service with tests and documentation.',
            'skills': ['Python'], 'currency': 'USD', 'type': 'fixed',
            'budget_min': 20, 'budget_max': 100, 'owner_id': '456', 'project_status': 'active'}


def test_success_timestamp_and_automation_follow_actual_scan_completion(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    start = clock[0]
    def scan(*args):
        with sessions() as session:
            connection = session.get(db.Connection, connection_id)
            assert connection.last_scan == start
            assert connection.last_scan_success_at == 0
            assert connection.scan_claim and connection.scan_claim_until > start
        clock[0] += 7
        return [project()]
    monkeypatch.setattr(main, 'scan_for_user', scan)
    result = main.perform_scan(uid, force=True)
    assert result['found'] == 1 and result['new'] == 1
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.last_scan_success_at == start + 7
        assert connection.last_scan == start
        assert connection.scan_claim == '' and connection.scan_claim_until == 0
        saved = json.loads(session.query(db.Job).filter_by(user_id=uid).one().data)
        assert saved['environment'] == 'sandbox' and saved['account_id'] == '123'
    main.run_autobid_cycle.assert_called_once_with(uid)
    main.run_autochat_cycle.assert_called_once_with(uid)


def test_background_refresh_rechecks_full_minute_under_database_lock(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    scanner = Mock(return_value=[])
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    monkeypatch.setattr(main, 'SCAN_SECONDS', 60)
    main.perform_scan(uid, force=True)
    clock[0] += 59
    with pytest.raises(ValueError):
        main.perform_scan(uid, force=True)
    assert scanner.call_count == 1
    clock[0] += 1
    main.perform_scan(uid, force=True)
    assert scanner.call_count == 2
    with sessions() as session:
        assert session.get(db.Connection, connection_id).last_scan == clock[0]


def test_manual_refresh_keeps_existing_25_second_wait(live_database, monkeypatch):
    _, uid, _, clock = live_database
    scanner = Mock(return_value=[])
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    main.perform_scan(uid)
    clock[0] += 24
    with pytest.raises(ValueError):
        main.perform_scan(uid)
    clock[0] += 1
    main.perform_scan(uid)
    assert scanner.call_count == 2


def test_long_running_scan_cannot_overlap_after_rapid_refresh_wait(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    entered, release = threading.Event(), threading.Event()
    def slow_scan(*args):
        entered.set()
        assert release.wait(5), 'Controlled scan was not released'
        return [project()]
    scanner = Mock(side_effect=slow_scan)
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(main.perform_scan, uid)
        try:
            assert entered.wait(5)
            clock[0] += 40
            with pytest.raises(ValueError):
                main.perform_scan(uid)
            assert scanner.call_count == 1
            with sessions() as session:
                assert session.get(db.Connection, connection_id).last_scan_success_at == 0
        finally:
            release.set()
        assert first.result(timeout=5)['new'] == 1
    with sessions() as session:
        assert session.query(db.Job).filter_by(user_id=uid).count() == 1


def test_expired_scan_lease_recovers_after_worker_crash(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        connection.last_scan = clock[0] - 301
        connection.scan_claim = 'abandoned-process-claim'
        connection.scan_claim_until = clock[0] - 1
        session.commit()
    scanner = Mock(return_value=[project()])
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    assert main.perform_scan(uid, force=True)['new'] == 1
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.scan_claim == '' and connection.scan_claim_until == 0
        assert connection.last_scan_success_at == clock[0]


def test_failed_scan_releases_own_lease_and_preserves_last_success(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    with sessions() as session:
        session.get(db.Connection, connection_id).last_scan_success_at = clock[0] - 500
        session.commit()
    scanner = Mock(side_effect=FreelancerError('Controlled unavailable provider', status_code=503))
    monkeypatch.setattr(main, 'scan_for_user', scanner)
    with pytest.raises(FreelancerError):
        main.perform_scan(uid, force=True)
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.scan_claim == '' and connection.scan_claim_until == 0
        assert connection.last_scan_success_at == clock[0] - 500
        assert connection.last_error
    main.run_autobid_cycle.assert_not_called()
    clock[0] += 60
    scanner.side_effect = None
    scanner.return_value = []
    assert main.perform_scan(uid, force=True)['ok']


@pytest.mark.parametrize('change', ['claim', 'token'])
def test_stale_scan_cannot_ingest_or_clear_a_replacement_claim(live_database, monkeypatch, change):
    sessions, uid, connection_id, clock = live_database
    def changed_scan(*args):
        with sessions() as session:
            connection = session.get(db.Connection, connection_id)
            connection.scan_claim = 'replacement-scan-claim'
            connection.scan_claim_until = clock[0] + 300
            if change == 'token':
                connection.token_cipher = db.encrypt_secret('replacement-verified-token')
            session.commit()
        return [project()]
    monkeypatch.setattr(main, 'scan_for_user', changed_scan)
    with pytest.raises(ValueError):
        main.perform_scan(uid, force=True)
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.scan_claim == 'replacement-scan-claim'
        assert connection.scan_claim_until == clock[0] + 300
        assert connection.last_scan_success_at == 0
        assert session.query(db.Job).filter_by(user_id=uid).count() == 0
    main.run_autobid_cycle.assert_not_called()


def test_live_columns_and_success_status_survive_initialization_restart(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    monkeypatch.setattr(main, 'scan_for_user', Mock(return_value=[]))
    main.perform_scan(uid, force=True)
    with sessions() as session:
        original = session.get(db.Connection, connection_id).token_cipher
    db.init_db()
    db.init_db()
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.token_cipher == original
        assert connection.last_scan_success_at == clock[0]
        assert connection.scan_claim == '' and connection.scan_claim_until == 0


def test_unexpected_scan_failure_does_not_leave_an_active_lease(live_database, monkeypatch):
    sessions, uid, connection_id, _ = live_database
    monkeypatch.setattr(main, 'scan_for_user', Mock(side_effect=RuntimeError('synthetic-private-internal-detail')))
    with pytest.raises((RuntimeError, ValueError, FreelancerError)):
        main.perform_scan(uid, force=True)
    with sessions() as session:
        connection = session.get(db.Connection, connection_id)
        assert connection.scan_claim == '' and connection.scan_claim_until == 0
        assert connection.last_scan_success_at == 0
        assert 'synthetic-private-internal-detail' not in (connection.last_error or '')
    main.run_autobid_cycle.assert_not_called()


def test_scheduler_starts_refresh_at_exact_minute_even_after_recent_queue_drain(live_database, monkeypatch):
    _, uid, _, clock = live_database
    rows = [[uid, clock[0], 0, True, True]]
    monkeypatch.setattr(worker, 'active_accounts', lambda now: rows)
    queue = Mock(return_value={'submitted': 0})
    scanner = Mock(return_value={'ok': True})
    monkeypatch.setattr(worker, 'run_autobid_cycle', queue)
    monkeypatch.setattr(worker, 'perform_scan', scanner)
    scheduler = worker.WorkerScheduler()
    try:
        clock[0] += 58
        scheduler.tick(58)
        scheduler.in_flight[uid].result(timeout=5)
        scanner.assert_not_called()
        queue.assert_called_once_with(uid, stop_event=scheduler.stop_event)
        clock[0] += 1
        scheduler.tick(59)
        scanner.assert_not_called()
        clock[0] += 1
        scheduler.tick(60)
        scheduler.in_flight[uid].result(timeout=5)
        scanner.assert_called_once_with(uid, force=True, stop_event=scheduler.stop_event)
    finally:
        scheduler.close()


def test_slow_accounts_do_not_block_heartbeat_or_other_accounts_and_pool_is_bounded(live_database, monkeypatch):
    sessions, _, _, clock = live_database
    rows = [[number, 0, 0, True, True] for number in range(1, 7)]
    monkeypatch.setattr(worker, 'active_accounts', lambda now: rows)
    release, all_started = threading.Event(), threading.Event()
    guard = threading.Lock()
    calls = []
    def slow_scan(uid, **kwargs):
        with guard:
            calls.append(uid)
            if len(calls) == 4:
                all_started.set()
        assert release.wait(5), 'Controlled workers were not released'
        rows[uid - 1][1] = clock[0]
        return {'ok': True}
    scanner = Mock(side_effect=slow_scan)
    monkeypatch.setattr(worker, 'perform_scan', scanner)
    monkeypatch.setattr(worker, 'PLATFORM_AUTO', False)
    scheduler = worker.WorkerScheduler(max_workers=999)
    try:
        scheduler.tick(0)
        assert all_started.wait(5)
        assert scheduler.max_workers == 4
        assert len(scheduler.in_flight) == 4
        first = list(scheduler.in_flight.values())
        clock[0] += 10
        scheduler.tick(10)
        assert scanner.call_count == 4, 'In-flight accounts must not be queued twice'
        with sessions() as session:
            assert session.get(db.WorkerHeartbeat, 'scanner').updated_at == clock[0]
        release.set()
        for future in first:
            future.result(timeout=5)
        scheduler.tick(11)
        for future in list(scheduler.in_flight.values()):
            future.result(timeout=5)
        assert set(calls) == set(range(1, 7))
        assert len(calls) == 6
    finally:
        release.set()
        scheduler.close()


def test_scheduler_does_not_repoll_heartbeat_before_ten_seconds(live_database, monkeypatch):
    sessions, _, _, clock = live_database
    monkeypatch.setattr(worker, 'active_accounts', lambda now: [])
    scheduler = worker.WorkerScheduler()
    try:
        scheduler.tick(0)
        started = clock[0]
        clock[0] += 9
        scheduler.tick(9)
        with sessions() as session:
            assert session.get(db.WorkerHeartbeat, 'scanner').updated_at == started
        clock[0] += 1
        scheduler.tick(10)
        with sessions() as session:
            assert session.get(db.WorkerHeartbeat, 'scanner').updated_at == started + 10
    finally:
        scheduler.close()


def test_scheduler_skips_persisted_scan_lease_and_disabled_queue_without_scanning(live_database, monkeypatch):
    _, uid, _, clock = live_database
    rows = [[uid, clock[0] - 400, clock[0] + 50, True, True],
            [uid + 1, clock[0] - 10, 0, False, True]]
    monkeypatch.setattr(worker, 'active_accounts', lambda now: rows)
    scanner, queue = Mock(), Mock()
    monkeypatch.setattr(worker, 'perform_scan', scanner)
    monkeypatch.setattr(worker, 'run_autobid_cycle', queue)
    scheduler = worker.WorkerScheduler()
    try:
        scheduler.tick(0)
        assert not scheduler.in_flight
        scanner.assert_not_called()
        queue.assert_not_called()
    finally:
        scheduler.close()


def test_close_prevents_new_tasks_and_stops_actions_after_running_readonly_scan(live_database, monkeypatch):
    sessions, uid, connection_id, clock = live_database
    entered, release = threading.Event(), threading.Event()
    def slow_scan(*args):
        entered.set()
        assert release.wait(5), 'Controlled scan was not released'
        return [project()]
    monkeypatch.setattr(main, 'scan_for_user', slow_scan)
    scheduler = worker.WorkerScheduler(max_workers=1)
    future = None
    try:
        scheduler.tick(0)
        assert entered.wait(5)
        future = scheduler.in_flight[uid]
        scheduler.close(wait=False)
        assert scheduler.stop_event.is_set()
        release.set()
        assert future.result(timeout=5)['ok']
        scheduler.tick(60)
        main.run_autobid_cycle.assert_not_called()
        main.run_autochat_cycle.assert_not_called()
        with sessions() as session:
            assert session.get(db.Connection, connection_id).last_scan_success_at == clock[0]
            assert session.query(db.Bid).filter_by(user_id=uid).count() == 0
    finally:
        release.set()
        if future is not None:
            future.result(timeout=5)
        scheduler.close()


def test_v3_database_additive_upgrade_preserves_accounts_passwords_and_tokens(live_database):
    sessions, uid, connection_id, clock = live_database
    with sessions() as session:
        user = session.get(db.User, uid)
        user.description = 'Saved customer profile must survive the upgrade'
        user.openai_key_cipher = db.encrypt_secret('synthetic-saved-ai-secret')
        connection = session.get(db.Connection, connection_id)
        connection.last_scan = clock[0] - 90
        connection.last_bid_attempt_at = clock[0] - 100
        session.commit()
        original = (user.password_hash, user.openai_key_cipher, connection.token_cipher)
    # Reconstruct the actual pre-v4 connection schema and absent heartbeat table.
    with db.engine.begin() as connection:
        for column in ('last_scan_success_at', 'scan_claim', 'scan_claim_until'):
            connection.execute(text(f'ALTER TABLE connections DROP COLUMN {column}'))
        connection.execute(text('DROP TABLE worker_heartbeats'))
    assert 'last_scan_success_at' not in {column['name'] for column in inspect(db.engine).get_columns('connections')}
    db.init_db()
    db.init_db()
    with sessions() as session:
        user = session.get(db.User, uid)
        connection = session.get(db.Connection, connection_id)
        assert (user.password_hash, user.openai_key_cipher, connection.token_cipher) == original
        assert user.description == 'Saved customer profile must survive the upgrade'
        assert connection.last_scan == clock[0] - 90
        assert connection.last_bid_attempt_at == clock[0] - 100
        assert connection.last_scan_success_at == 0
        assert connection.scan_claim == '' and connection.scan_claim_until == 0
        assert db.decrypt_secret(connection.token_cipher) == 'synthetic-live-token'
        assert db.decrypt_secret(user.openai_key_cipher) == 'synthetic-saved-ai-secret'
        assert session.query(db.WorkerHeartbeat).count() == 0
    assert 'worker_heartbeats' in inspect(db.engine).get_table_names()


def test_stop_during_ai_generation_prevents_reservation_and_external_send(live_database, monkeypatch):
    sessions, uid, _, _ = live_database
    stop = threading.Event()
    with sessions() as session:
        user = session.get(db.User, uid)
        user.min_score = 1
        raw = project()
        raw.update(environment='sandbox', account_id='123')
        session.add(db.Job(user_id=uid, remote_id=raw['id'], data=json.dumps(raw), score=99, decision='Strong match'))
        session.commit()
    def complete_ai(*args):
        stop.set()
        return 'I will implement and test your Python backend service using the supplied requirements.'
    proposer = Mock(side_effect=complete_ai)
    external = Mock(side_effect=AssertionError('Stopped automation must not reach Freelancer'))
    monkeypatch.setattr(main, 'ai_proposal', proposer)
    monkeypatch.setattr(main, 'get_client', external)
    result = real_autobid_cycle(uid, stop_event=stop)
    assert result['submitted'] == 0 and result['reason'] == 'Worker is stopping'
    proposer.assert_called_once()
    external.assert_not_called()
    with sessions() as session:
        assert session.query(db.Bid).filter_by(user_id=uid).count() == 0
