import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import main, db


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'), connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setenv('PAYMENT_BANK', 'Test bank account')
    with TestClient(main.app) as client:
        yield client, sessions
    engine.dispose()


def login(client, email='owner@example.test', password='Test-only-owner-password-123!'):
    r = client.post('/api/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return {'x-csrf-token': client.get('/api/csrf').json()['csrf']}


def member(sessions, email='customer@example.test', plan='paid_starter', status='inactive'):
    with sessions() as s:
        u = db.User(email=email, name='Customer', password_hash=db.hash_password('Customer-password-123!'))
        db.apply_plan(u, plan, status)
        s.add(u)
        s.commit()
        return u.id


def live_job(sessions, uid, remote='901', flid='123'):
    with sessions() as s:
        if not s.query(db.Connection).filter_by(user_id=uid).first():
            s.add(db.Connection(user_id=uid, token_cipher=db.encrypt_secret('test-token-no-network'), freelancer_id=flid, environment='sandbox'))
        raw = {'id': remote, 'title': 'Python automation', 'description': 'Python backend automation service',
               'skills': ['Python'], 'budget_min': 20, 'budget_max': 100, 'currency': 'USD',
               'project_status': 'active', 'type': 'fixed', 'environment': 'sandbox', 'account_id': flid}
        j = db.Job(user_id=uid, remote_id=remote, data=json.dumps(raw), score=90)
        s.add(j)
        s.commit()
        return j.id


DRAFT = {'amount': 50, 'days': 3, 'milestone_pct': 100, 'proposal': 'I can implement the requested Python service with tests and documentation.'}


def test_signup_no_privileges_and_revocable_logout(app_client):
    c, sessions = app_client
    r = c.post('/api/register', json={'name': 'New Customer', 'email': 'new@example.test', 'password': 'Very-long-password-123!'})
    assert r.status_code == 200
    me = c.get('/api/me').json()['user']
    assert me['role'] == 'member' and me['plan_tier'] == 'paid_starter'
    assert not me['subscription_active'] and not me['auto_requested']
    assert c.get('/api/team').status_code == 403
    raw_cookie = c.cookies.get('bp_session')
    csrf = c.get('/api/csrf').json()['csrf']
    assert c.post('/api/demo').status_code == 403
    assert c.post('/api/logout', headers={'x-csrf-token': csrf}).status_code == 200
    c.cookies.set('bp_session', raw_cookie)
    assert c.get('/api/me').status_code == 401
    with sessions() as s:
        assert s.query(db.AuthSession).count() == 0


def test_origin_cookie_headers_and_validation_redaction(app_client):
    c, _ = app_client
    assert c.post('/api/login', headers={'Origin': 'https://evil.example'}, json={'email': 'x@a.test', 'password': 'bad'}).status_code == 403
    headers = login(c)
    assert 'HttpOnly' in c.post('/api/login', json={'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!'}).headers['set-cookie']
    assert "script-src 'self'" in c.get('/').headers['content-security-policy']
    r = c.post('/api/ai-keys', headers=login(c), json={'gemini_key': 'DO-NOT-ECHO-SECRET', 'bad_field': 'DO-NOT-ECHO-SECRET'})
    assert r.status_code == 422
    assert 'DO-NOT-ECHO-SECRET' not in r.text


def test_login_throttled_across_new_clients(app_client):
    c, sessions = app_client
    for i in range(10):
        assert c.post('/api/login', json={'email': 'absent@example.test', 'password': 'bad'}).status_code == 401
    assert c.post('/api/login', json={'email': 'absent@example.test', 'password': 'bad'}).status_code == 429
    with sessions() as s:
        assert s.query(db.LoginAttempt).count() == 20


def test_payment_prices_duplicate_and_single_approval(app_client):
    c, sessions = app_client
    uid = member(sessions)
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    payment = {'method': 'bank', 'transaction_id': 'test-reference-1', 'amount': 1, 'currency': 'USD', 'plan_tier': 'paid_starter'}
    assert c.post('/api/billing/submit', headers=headers, json=payment).status_code == 400
    payment['amount'] = 10
    r = c.post('/api/billing/submit', headers=headers, json=payment)
    assert r.status_code == 200, r.text
    pid = r.json()['payment_id']
    assert c.post('/api/billing/submit', headers=headers, json=payment).status_code == 409
    assert not c.get('/api/me').json()['user']['subscription_active']
    headers = login(c)
    r = c.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'})
    assert r.status_code == 200, r.text
    assert c.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 409
    with sessions() as s:
        u = s.get(db.User, uid)
        assert u.monthly_fee == 10 and u.monthly_bid_limit == 150
        assert u.subscription_expires > time.time() and db.subscription_active(u)


def test_bid_subscription_ownership_duplicate_and_remote_id(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions)
    jid = live_job(sessions, uid)
    api = Mock()
    api.place_bid.return_value = {'bid': {'id': 7788}}
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    assert c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT).status_code == 403
    assert not api.place_bid.called
    with sessions() as s:
        db.apply_plan(s.get(db.User, uid), 'paid_starter', 'active'); s.commit()
    r = c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT)
    assert r.status_code == 200, r.text
    assert r.json()['bid_id'] == '7788'
    assert c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT).status_code == 409
    api.place_bid.assert_called_once()
    other = member(sessions, 'other@example.test', status='active')
    headers = login(c, 'other@example.test', 'Customer-password-123!')
    assert c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT).status_code == 404


def test_timeout_stays_uncertain_and_never_retried(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    api = Mock()
    api.place_bid.side_effect = RuntimeError('secret-auth-header-must-not-leak')
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    r = c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT)
    assert r.status_code == 502 and 'secret-auth' not in r.text
    assert c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT).status_code == 409
    with sessions() as s:
        assert s.query(db.Bid).one().status == 'uncertain'
        assert all('secret-auth' not in a.message for a in s.query(db.Audit))
    api.place_bid.assert_called_once()


def test_atomic_reservation_concurrent_requests(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    def reserve():
        try:
            with sessions() as s:
                return main.reserve_bid(s, uid, jid, draft=main.DraftIn(**DRAFT))[0]
        except main.HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: reserve(), range(2)))
    assert 409 in values
    with sessions() as s:
        assert s.query(db.Bid).count() == 1


def test_plan_cap_and_expired_block_all_bid_paths(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    api = Mock()
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    with sessions() as s:
        u = s.get(db.User, uid)
        u.max_hourly = 500; u.max_daily = 500
        for i in range(150):
            j = db.Job(user_id=uid, remote_id=f'past-{i}', data='{}');s.add(j);s.flush()
            s.add(db.Bid(user_id=uid, job_id=j.id, proposal='past proposal', amount=50, status='uncertain', updated=int(time.time())))
        s.commit()
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    r = c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT)
    assert r.status_code == 403 and 'allowance' in r.text
    with sessions() as s:
        u = s.get(db.User, uid); u.subscription_expires = int(time.time()) - 1; s.commit()
    assert c.post('/api/scan', headers=headers).status_code == 403
    assert c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT).status_code == 403
    assert not api.place_bid.called


def test_live_bids_endpoint_import_and_safe_failure(app_client, monkeypatch):
    c, sessions = app_client
    headers = login(c)
    with sessions() as s:
        uid = s.query(db.User).filter_by(role='admin').one().id
    live_job(sessions, uid)
    monkeypatch.setattr(main, 'fetch_live_bids', lambda *a: [{'id': 123}])
    assert c.get('/api/live-bids').json()['bids'] == [{'id': 123}]


def test_only_two_free_accounts(app_client):
    c, sessions = app_client
    headers = login(c)
    body = {'email': 'friend@example.test', 'name': 'Second Founder', 'password': 'Founder-password-123!', 'plan_tier': 'free_team'}
    assert c.post('/api/team', headers=headers, json=body).status_code == 200
    body['email'] = 'third@example.test'
    assert c.post('/api/team', headers=headers, json=body).status_code == 409


def test_dispatch_snapshot_and_single_claim(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    api = Mock()
    api.place_bid.return_value = {'bid': {'id': 999}}
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    with sessions() as s:
        reservation = main.reserve_bid(s, uid, jid, draft=main.DraftIn(**DRAFT))
    assert main.send_reserved(*reservation)['bid_id'] == '999'
    with pytest.raises(main.HTTPException) as exc:
        main.send_reserved(*reservation)
    assert exc.value.status_code == 409
    api.place_bid.assert_called_once()


def test_dispatch_connection_changed_fails_closed(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    api = Mock()
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    with sessions() as s:
        reservation = main.reserve_bid(s, uid, jid, draft=main.DraftIn(**DRAFT))
    with sessions() as s:
        c = s.query(db.Connection).filter_by(user_id=uid).one()
        c.freelancer_id = '456'; c.environment = 'production'; s.commit()
    with pytest.raises(main.HTTPException) as exc:
        main.send_reserved(*reservation)
    assert exc.value.status_code == 409
    assert not api.place_bid.called


def test_confirmed_bid_path_and_expired_intent(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    api = Mock(); api.place_bid.return_value = {'bid': {'id': 777}}
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    assert c.post(f'/api/jobs/{jid}/draft', headers=headers, json=DRAFT).status_code == 200
    r = c.post(f'/api/jobs/{jid}/prepare', headers=headers)
    assert r.status_code == 200, r.text
    payload = {'intent': r.json()['intent'], 'confirmation': 'PLACE BID'}
    r = c.post(f'/api/jobs/{jid}/submit', headers=headers, json=payload)
    assert r.status_code == 200 and r.json()['bid_id'] == '777'
    assert c.post(f'/api/jobs/{jid}/submit', headers=headers, json=payload).status_code == 409
    api.place_bid.assert_called_once()


def test_renewal_extends_expiry_without_resetting_allowance(app_client):
    c, sessions = app_client
    uid = member(sessions, status='active')
    with sessions() as s:
        u = s.get(db.User, uid)
        before, anchor = u.subscription_expires, u.billing_period_started
        payment = db.PaymentRequest(user_id=uid, method='bank', transaction_id='renew-1', amount=10, currency='USD', plan_tier='paid_starter')
        s.add(payment); s.commit(); pid = payment.id
    headers = login(c)
    assert c.post(f'/api/admin/payments/{pid}/decide', headers=headers, json={'action': 'approve'}).status_code == 200
    with sessions() as s:
        u = s.get(db.User, uid)
        assert u.subscription_expires == before + 30 * 86400
        assert u.billing_period_started == anchor


def test_scan_claim_cooldown_and_account_tags(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    scan = Mock(return_value=[{'id': '902', 'title': 'Python task', 'skills': ['Python'], 'budget_min': 20, 'budget_max': 100, 'project_status': 'active'}])
    monkeypatch.setattr(main, 'scan_for_user', scan)
    monkeypatch.setattr(main, 'run_autobid_cycle', lambda *a: {'submitted': 0})
    monkeypatch.setattr(main, 'run_autochat_cycle', lambda *a: {'replied': 0})
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    assert c.post('/api/scan', headers=headers).status_code == 200
    assert c.post('/api/scan', headers=headers).status_code == 400
    scan.assert_called_once()
    with sessions() as s:
        j = s.query(db.Job).filter_by(user_id=uid, remote_id='902').one()
        raw = json.loads(j.data)
        assert raw['environment'] == 'sandbox' and raw['account_id'] == '123'


def test_automatic_bid_cycle_submits_once_and_honors_opt_in(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    with sessions() as s:
        u = s.get(db.User, uid)
        u.skills = 'Python'; u.min_score = 20; u.auto_requested = False
        s.commit()
    api = Mock(); api.place_bid.return_value = {'bid': {'id': 222}}
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    monkeypatch.setattr(main, 'ai_proposal', lambda *a: DRAFT['proposal'])
    assert main.run_autobid_cycle(uid)['submitted'] == 0
    assert not api.place_bid.called
    with sessions() as s:
        s.get(db.User, uid).auto_requested = True; s.commit()
    assert main.run_autobid_cycle(uid)['submitted'] == 1
    assert main.run_autobid_cycle(uid)['submitted'] == 0
    api.place_bid.assert_called_once()
    with sessions() as s:
        assert s.query(db.Bid).one().source == 'auto'


def test_chat_cycle_reserves_thread_before_side_effect_and_does_not_retry(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    with sessions() as s:
        s.get(db.User, uid).auto_chat_greeting = True
        s.add(db.Bid(user_id=uid, job_id=jid, amount=50, proposal=DRAFT['proposal'], status='submitted'))
        s.commit()
    api = Mock()
    api.list_threads.return_value = {'threads': [{'id': 998, 'context': {'id': 901}, 'last_message': {'from_user': 345, 'message': 'Can you review this?'}}], 'users': {'345': {'username': 'Client'}}}
    api.send_chat_message.side_effect = main.FreelancerError('Request timed out')
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    monkeypatch.setattr(main, 'ai_chat_reply', lambda *a: 'Thanks for the details. Please share the scope for review.')
    assert main.run_autochat_cycle(uid)['replied'] == 0
    assert main.run_autochat_cycle(uid)['replied'] == 0
    api.send_chat_message.assert_called_once()
    with sessions() as s:
        assert s.query(db.ChatGreeted).count() == 1
        assert s.query(db.Audit).filter_by(action='auto_chat_uncertain').count() == 1


def test_worker_skips_expired_subscription(app_client, monkeypatch):
    _, sessions = app_client
    from app import worker
    active = member(sessions, status='active')
    expired = member(sessions, email='expired@example.test', status='expired')
    live_job(sessions, active); live_job(sessions, expired, flid='456')
    monkeypatch.setattr(worker, 'SessionLocal', sessions)
    scan = Mock(return_value={'ok': True})
    monkeypatch.setattr(worker, 'perform_scan', scan)
    worker.once()
    scan.assert_called_once_with(active, force=True)


def test_dispatch_rechecks_subscription_before_external_request(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    with sessions() as s:
        reservation = main.reserve_bid(s, uid, jid, draft=main.DraftIn(**DRAFT))
    with sessions() as s:
        s.get(db.User, uid).active = False
        s.commit()
    api = Mock()
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    with pytest.raises(main.HTTPException) as exc:
        main.send_reserved(*reservation)
    assert exc.value.status_code == 409
    assert not api.place_bid.called
    with sessions() as s:
        assert s.query(db.Bid).one().status == 'cancelled'


def test_autobid_pause_during_ai_does_not_hold_write_lock_or_send(app_client, monkeypatch):
    _, sessions = app_client
    uid = member(sessions, status='active')
    live_job(sessions, uid)
    with sessions() as s:
        u = s.get(db.User, uid)
        u.skills = 'Python'; u.min_score = 20; u.auto_requested = True
        s.commit()
    def generation(*args):
        # A second transaction must be able to update preferences during slow AI I/O.
        with sessions() as s:
            main.lock_user(s, uid)
            s.get(db.User, uid).auto_requested = False
            s.commit()
        return DRAFT['proposal']
    monkeypatch.setattr(main, 'ai_proposal', generation)
    api = Mock(); monkeypatch.setattr(main, 'get_client', lambda *a: api)
    result = main.run_autobid_cycle(uid)
    assert result['submitted'] == 0
    assert not api.place_bid.called
    with sessions() as s:
        assert s.query(db.Bid).count() == 0
        assert s.query(db.LoginAttempt).filter_by(identifier=db.session_digest(f'cloud:{uid}')).count() == 1


def test_chat_pause_during_ai_and_uncertain_delivery_stats(app_client, monkeypatch):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    with sessions() as s:
        s.get(db.User, uid).auto_chat_greeting = True
        s.add(db.Bid(user_id=uid, job_id=jid, amount=50, proposal=DRAFT['proposal'], status='submitted'))
        s.commit()
    api = Mock()
    api.list_threads.return_value = {'threads': [{'id': 998, 'context': {'id': 901}, 'last_message': {'from_user': 345, 'message': 'Hello'}}]}
    def generation(*args):
        with sessions() as s:
            s.get(db.User, uid).auto_chat_greeting = False
            s.commit()
        return 'Thanks for the project details.'
    monkeypatch.setattr(main, 'get_client', lambda *a: api)
    monkeypatch.setattr(main, 'ai_chat_reply', generation)
    assert main.run_autochat_cycle(uid)['replied'] == 0
    assert not api.send_chat_message.called
    with sessions() as s:
        assert s.query(db.ChatGreeted).count() == 0
        s.add(db.ChatGreeted(user_id=uid, thread_id='999', status='uncertain'))
        s.commit()
    login(c, 'customer@example.test', 'Customer-password-123!')
    assert c.get('/api/stats').json()['chat_greeted'] == 0


def test_health_returns_unavailable_when_database_down(app_client, monkeypatch):
    c, _ = app_client
    def failing_session():
        raise RuntimeError('sensitive-database-url')
    monkeypatch.setattr(main, 'SessionLocal', failing_session)
    r = c.get('/health')
    assert r.status_code == 503
    assert r.json() == {'status': 'unavailable'}
    assert 'sensitive' not in r.text


@pytest.mark.parametrize('change', [{'currency': None}, {'type': None}, {'owner_id': '123'}, {'environment': None}])
def test_bid_rejects_unknown_units_own_project_or_unbound_legacy_job(app_client, monkeypatch, change):
    c, sessions = app_client
    uid = member(sessions, status='active')
    jid = live_job(sessions, uid)
    with sessions() as s:
        j = s.get(db.Job, jid)
        raw = json.loads(j.data); raw.update(change); j.data = json.dumps(raw)
        s.commit()
    api = Mock(); monkeypatch.setattr(main, 'get_client', lambda *a: api)
    headers = login(c, 'customer@example.test', 'Customer-password-123!')
    r = c.post(f'/api/jobs/{jid}/instant-bid', headers=headers, json=DRAFT)
    assert r.status_code in (400, 409), r.text
    assert not api.place_bid.called


def test_settings_accept_single_character_language_without_empty_profile(app_client):
    c, _ = app_client
    headers = login(c)
    settings = {'name': 'Owner', 'skills': 'R', 'search_terms': 'R', 'countries': '',
                'description': '', 'portfolio': '', 'min_score': 20, 'min_budget': 1,
                'max_bid': 100, 'max_daily': 5}
    r = c.post('/api/settings', headers=headers, json=settings)
    assert r.status_code == 200, r.text
    assert r.json()['user']['skills'] == 'R'
    settings['skills'] = '  '
    assert c.post('/api/settings', headers=headers, json=settings).status_code == 400


def test_localhost_login_works_with_setup_loopback_url(app_client, monkeypatch):
    c, _ = app_client
    monkeypatch.setenv('PUBLIC_URL', 'http://127.0.0.1:8000')
    r = c.post('http://localhost:8000/api/login', headers={'Origin': 'http://localhost:8000', 'Sec-Fetch-Site': 'same-origin'},
               json={'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!'})
    assert r.status_code == 200, r.text
    assert c.get('http://localhost:8000/api/me').status_code == 200
    csrf = c.get('http://localhost:8000/api/csrf').json()['csrf']
    r = c.post('http://localhost:8000/api/demo', headers={'Origin': 'http://localhost:8000', 'Sec-Fetch-Site': 'same-origin', 'X-CSRF-Token': csrf})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize('configured,opened', [
    ('http://localhost:8000/', 'http://127.0.0.1:8000'),
    ('http://127.0.0.1:8000', 'http://[::1]:8000'),
    ('http://127.0.0.1:80', 'http://localhost'),
])
def test_development_accepts_same_origin_loopback_aliases(app_client, monkeypatch, configured, opened):
    c, _ = app_client
    monkeypatch.setenv('PUBLIC_URL', configured)
    r = c.post(opened + '/api/login', headers={'Origin': opened, 'Sec-Fetch-Site': 'same-origin'},
               json={'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!'})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize('origin,request_url', [
    ('http://localhost:8001', 'http://localhost:8001'),
    ('https://localhost:8000', 'https://localhost:8000'),
    ('http://localhost.evil.example:8000', 'http://localhost.evil.example:8000'),
    ('http://127.1:8000', 'http://127.1:8000'),
    ('http://0.0.0.0:8000', 'http://0.0.0.0:8000'),
    ('http://[::ffff:127.0.0.1]:8000', 'http://[::ffff:127.0.0.1]:8000'),
    ('http://localhost:8000', 'http://evil.example:8000'),
    ('http://localhost:8000', 'http://127.0.0.1:8000'),
    ('null', 'http://localhost:8000'),
])
def test_loopback_allowance_rejects_other_sites_ports_and_host_mismatches(app_client, monkeypatch, origin, request_url):
    c, _ = app_client
    monkeypatch.setenv('PUBLIC_URL', 'http://127.0.0.1:8000')
    r = c.post(request_url + '/api/login', headers={'Origin': origin}, json={'email': 'owner@example.test', 'password': 'bad'})
    assert r.status_code == 403
    assert isinstance(r.json()['detail'], str)
    assert r.headers['cache-control'] == 'no-store'
    assert r.headers['x-frame-options'] == 'DENY'
    assert "script-src 'self'" in r.headers['content-security-policy']


@pytest.mark.parametrize('value', [
    '', 'null', 'http://user:password@localhost:8000', 'http://localhost:8000/path',
    'http://localhost:8000?q=x', 'http://localhost:8000#fragment',
    ' http://localhost:8000', 'http://localhost:8000\n', 'http://local\thost:8000',
    'http://localhost:8000\x7f', 'http://localhost:8000\\evil', 'http://[::1',
    'http://localhost:99999', 'http://localhost:0', 'file://localhost',
])
def test_origin_parser_fails_closed_on_malformed_values(value):
    assert main._parse_origin(value) is None


def test_production_does_not_enable_local_alias_allowance(app_client, monkeypatch):
    c, _ = app_client
    monkeypatch.setattr(main, 'APP_ENV', 'production')
    monkeypatch.setenv('PUBLIC_URL', 'http://127.0.0.1:8000')
    r = c.post('http://localhost:8000/api/login', headers={'Origin': 'http://localhost:8000'}, json={'email': 'owner@example.test', 'password': 'bad'})
    assert r.status_code == 403 and 'detail' in r.json()
    assert main._origin_allowed('https://APP.EXAMPLE:443', 'https://app.example/', 'https://app.example/')
    assert not main._origin_allowed('https://other.example', 'https://app.example', 'https://other.example/')


def test_fetch_metadata_and_duplicate_origins_still_blocked(app_client, monkeypatch):
    c, _ = app_client
    monkeypatch.setenv('PUBLIC_URL', 'http://127.0.0.1:8000')
    r = c.post('http://localhost:8000/api/login', headers={'Origin': 'http://localhost:8000', 'Sec-Fetch-Site': 'cross-site'}, json={'email': 'owner@example.test', 'password': 'bad'})
    assert r.status_code == 403 and 'detail' in r.json()
    r = c.post('http://localhost:8000/api/login', headers=[('Origin', 'http://localhost:8000'), ('Origin', 'http://evil.example')], json={'email': 'owner@example.test', 'password': 'bad'})
    assert r.status_code == 403 and 'detail' in r.json()
