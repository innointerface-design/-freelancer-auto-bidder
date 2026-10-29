"""Independent concurrency checks for billing decisions and quota reservations."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


@pytest.fixture
def review_database(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/review.db', connect_args={'check_same_thread': False, 'timeout': 30})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    db.init_db()
    with sessions() as s:
        owner = s.query(db.User).filter_by(role='admin').one()
        owner_id = owner.id
        customer = db.User(email='review-customer@example.test', name='Review Customer',
                           password_hash=db.hash_password('Review-only-password-123!'))
        db.apply_plan(customer, 'paid_starter', 'active')
        customer.max_hourly = 1
        customer.max_daily = 5
        s.add(customer)
        s.flush()
        customer_id = customer.id
        s.add(db.Connection(user_id=customer_id, token_cipher=db.encrypt_secret('test-token'),
                            freelancer_id='123', environment='sandbox'))
        for remote_id in ('1001', '1002'):
            import json
            raw = {'id': remote_id, 'title': 'Controlled sandbox project', 'budget_min': 20,
                   'budget_max': 100, 'project_status': 'active', 'environment': 'sandbox',
                   'account_id': '123', 'currency': 'USD', 'type': 'fixed'}
            s.add(db.Job(user_id=customer_id, remote_id=remote_id, data=json.dumps(raw), score=99))
        s.commit()
    yield sessions, owner_id, customer_id
    engine.dispose()


def test_concurrent_distinct_projects_cannot_exceed_hourly_quota(review_database):
    sessions, _, uid = review_database
    with sessions() as s:
        job_ids = [j.id for j in s.query(db.Job).filter_by(user_id=uid).all()]
    draft = main.DraftIn(amount=50, days=3, milestone_pct=100,
                         proposal='Controlled sandbox proposal for the requested project with documentation.')
    def reserve(job_id):
        try:
            with sessions() as s:
                main.reserve_bid(s, uid, job_id, draft=draft)
            return 200
        except main.HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, job_ids))
    assert sorted(outcomes) == [200, 429]
    with sessions() as s:
        assert s.query(db.Bid).filter_by(user_id=uid, status='sending').count() == 1


def test_concurrent_admin_decisions_activate_payment_once(review_database):
    sessions, owner_id, uid = review_database
    with sessions() as s:
        payment = db.PaymentRequest(user_id=uid, method='bank', transaction_id='controlled-reference',
                                    amount=10, currency='USD', plan_tier='paid_starter')
        s.add(payment)
        s.commit()
        pid = payment.id
    def approve(_):
        try:
            return main.admin_decide_payment(pid, main.PaymentDecisionIn(action='approve'), uid=owner_id)['status']
        except main.HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, range(2)))
    assert outcomes.count('approved') == 1 and outcomes.count(409) == 1
    with sessions() as s:
        assert s.query(db.Audit).filter_by(user_id=uid, action='subscription_activated').count() == 1
