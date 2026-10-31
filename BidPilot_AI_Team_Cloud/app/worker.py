"""Bounded background scanning with independent worker health reporting."""
from concurrent.futures import ThreadPoolExecutor
import logging
import signal
import threading
import time

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from .db import init_db, SessionLocal, User, Connection, WorkerHeartbeat, PLATFORM_AUTO, SCAN_SECONDS
from .main import perform_scan, run_autobid_cycle, run_autochat_cycle
from .billing_lifecycle import expire_subscriptions

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('bidpilot-worker')
HEARTBEAT_SECONDS = 10
MAX_CONCURRENT_ACCOUNTS = 4
PENDING_CHECK_SECONDS = 5
EXPIRY_CHECK_SECONDS = 30


def active_accounts(now):
    """Read scheduling metadata only; every action rechecks permission itself."""
    with SessionLocal() as session:
        return session.query(
            Connection.user_id, Connection.last_scan, Connection.scan_claim_until,
            User.auto_requested, User.auto_admin_allowed,
        ).join(User, Connection.user_id == User.id).filter(
            User.active.is_(True), User.subscription_status.in_(['active', 'trial']),
            or_(User.plan_tier == 'free_team', User.subscription_expires > now),
            Connection.api_pause_reason.in_(['', 'rate_limited']),
            User.freelancer_cooldown_until <= now,
            Connection.api_cooldown_until <= now,
        ).all()


def write_heartbeat(now):
    with SessionLocal() as session:
        changed = session.query(WorkerHeartbeat).filter_by(worker_key='scanner').update({'updated_at': now})
        if not changed:
            session.add(WorkerHeartbeat(worker_key='scanner', updated_at=now))
        try:
            session.commit()
        except IntegrityError:
            # Another worker may have created the shared health row concurrently.
            session.rollback()
            session.query(WorkerHeartbeat).filter_by(worker_key='scanner').update({'updated_at': now})
            session.commit()


def auto_for_user(uid):
    res = run_autobid_cycle(uid)
    chat = run_autochat_cycle(uid)
    return res.get('submitted', 0) + chat.get('replied', 0)


def once():
    """Single sequential pass, useful for operator diagnostics and existing callers."""
    now = int(time.time())
    expire_subscriptions(now=now)
    for uid, last_scan, claim_until, _, _ in active_accounts(now):
        if int(claim_until or 0) > now:
            continue
        try:
            if not last_scan or now - last_scan >= SCAN_SECONDS:
                summary = perform_scan(uid, force=True)
                log.info('Account %s scan and automation summary: %s', uid, summary)
            elif PLATFORM_AUTO:
                run_autobid_cycle(uid)
        except Exception as error:
            log.warning('Account %s scan failed (%s); inspect account settings/activity', uid, type(error).__name__)


class WorkerScheduler:
    """One task per account, with a small bounded pool and no unbounded backlog.

    The scheduler thread writes its heartbeat independently of remote API/AI work.
    Persistent per-account leases and dispatch reservations coordinate other worker
    processes and browser actions too. These controls do not promise API latency.
    """
    def __init__(self, *, max_workers=MAX_CONCURRENT_ACCOUNTS):
        self.max_workers = max(1, min(MAX_CONCURRENT_ACCOUNTS, int(max_workers)))
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix='bidpilot-account')
        self.in_flight = {}
        self._next_cycle = {}
        self._retry_until = {}
        self._last_dispatched = {}
        self._heartbeat_at = None
        self._expiry_at = None
        self._closed = False

    def _run_account(self, uid, scan_due):
        if self.stop_event.is_set():
            return
        if scan_due:
            result = perform_scan(uid, force=True, stop_event=self.stop_event)
            log.info('Account %s scan and automation summary: %s', uid, result)
            return result
        elif PLATFORM_AUTO:
            # Drain stored matches between scans without polling project/chat APIs.
            return run_autobid_cycle(uid, stop_event=self.stop_event)

    def tick(self, now_monotonic=None):
        if self._closed or self.stop_event.is_set():
            return
        monotonic = time.monotonic() if now_monotonic is None else now_monotonic
        now = int(time.time())
        if self._expiry_at is None or monotonic - self._expiry_at >= EXPIRY_CHECK_SECONDS:
            expire_subscriptions(now=now)
            self._expiry_at = monotonic
        for uid, future in list(self.in_flight.items()):
            if not future.done():
                continue
            del self.in_flight[uid]
            try:
                future.result()
            except Exception as error:
                log.warning('Account %s task failed (%s); inspect account settings/activity', uid, type(error).__name__)
                self._retry_until[uid] = monotonic + PENDING_CHECK_SECONDS
        if self._heartbeat_at is None or monotonic - self._heartbeat_at >= HEARTBEAT_SECONDS:
            write_heartbeat(now)
            self._heartbeat_at = monotonic
        capacity = self.max_workers - len(self.in_flight)
        if capacity <= 0:
            return
        ready = []
        for uid, last_scan, claim_until, requested, allowed in active_accounts(now):
            if uid in self.in_flight or int(claim_until or 0) > now or monotonic < self._retry_until.get(uid, 0):
                continue
            scan_due = not last_scan or now - last_scan >= SCAN_SECONDS
            if not scan_due and monotonic < self._next_cycle.get(uid, 0):
                continue
            if scan_due or (PLATFORM_AUTO and requested and allowed):
                ready.append((not scan_due, self._last_dispatched.get(uid, float('-inf')), uid, scan_due))
        # Fresh scans take priority, then give waiting accounts their turn.
        ready.sort()
        for _, _, uid, scan_due in ready[:capacity]:
            if self.stop_event.is_set():
                break
            self.in_flight[uid] = self.executor.submit(self._run_account, uid, scan_due)
            self._next_cycle[uid] = monotonic + PENDING_CHECK_SECONDS
            self._last_dispatched[uid] = monotonic

    def close(self, *, wait=True):
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        # Active read-only requests may finish. Their automation paths observe
        # stop_event again after API/AI work, before reserving or sending actions.
        self.executor.shutdown(wait=wait, cancel_futures=True)


def main():
    init_db()
    scheduler = WorkerScheduler()
    def stop_on_signal(signum, frame):
        log.info('Worker stop requested; cancelling queued automation')
        scheduler.stop_event.set()
    previous_term = signal.signal(signal.SIGTERM, stop_on_signal)
    log.info('BidPilot worker started (scan interval: %ss, auto-bid gate: %s)', SCAN_SECONDS, PLATFORM_AUTO)
    try:
        while not scheduler.stop_event.is_set():
            started = time.monotonic()
            try:
                scheduler.tick(started)
            except Exception as error:
                log.warning('Worker scheduler failed (%s); inspect service/database health', type(error).__name__)
            scheduler.stop_event.wait(max(0, 1 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        log.info('Worker stopping; cancelling queued automation')
    finally:
        scheduler.close()
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == '__main__':
    main()
