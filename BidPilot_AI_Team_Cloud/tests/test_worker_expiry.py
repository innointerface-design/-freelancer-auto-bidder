"""An idle worker expires paid access without waiting for a customer login."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, worker


def test_idle_worker_expires_paid_account_without_touching_founder(tmp_path, monkeypatch):
    now = 1_800_000_000
    engine = create_engine(f'sqlite:///{tmp_path}/expiry.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(worker, 'SessionLocal', sessions)
    monkeypatch.setattr(worker.time, 'time', lambda: now)
    db.init_db()
    with sessions() as session:
        founder = session.query(db.User).filter_by(role='admin').one()
        founder.auto_requested = founder.auto_chat_greeting = True
        member = db.User(email='expired@example.test', name='Paid member', password_hash='unused')
        db.apply_plan(member, 'paid_starter', now=now - 90 * 86400)
        member.auto_requested = member.auto_chat_greeting = True
        session.add(member)
        session.commit()
        founder_id, member_id = founder.id, member.id
    scheduler = worker.WorkerScheduler(max_workers=1)
    try:
        scheduler.tick(0)
        with sessions() as session:
            member = session.get(db.User, member_id)
            assert member.subscription_status == 'expired'
            assert not member.auto_requested and not member.auto_chat_greeting
            founder = session.get(db.User, founder_id)
            assert founder.subscription_status == 'active' and founder.auto_requested
            assert session.get(db.WorkerHeartbeat, 'scanner').updated_at == now
            assert not scheduler.in_flight
        scheduler.tick(31)
        with sessions() as session:
            audits = session.query(db.Audit).filter_by(user_id=member_id, action='subscription_expired').all()
            assert len(audits) == 1
    finally:
        scheduler.close()
        engine.dispose()
