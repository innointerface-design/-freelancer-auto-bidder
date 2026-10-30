"""BidPilot Pro Background Worker: Fast 60-second Bidman-style scanner, AI Auto-Bidder & Auto-Chat Greeter."""
import time, logging
from sqlalchemy import or_
from .db import init_db, SessionLocal, User, Connection, PLATFORM_AUTO, SCAN_SECONDS
from .main import perform_scan, run_autobid_cycle, run_autochat_cycle

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('bidpilot-worker')


def auto_for_user(uid):
    res = run_autobid_cycle(uid)
    chat = run_autochat_cycle(uid)
    return res.get('submitted', 0) + chat.get('replied', 0)


def once():
    with SessionLocal() as s:
        accounts = [
            (r[0], r[1]) for r in s.query(Connection.user_id, Connection.last_scan)
            .join(User, Connection.user_id == User.id)
            .filter(User.active.is_(True), User.subscription_status.in_(['active', 'trial']),
                    or_(User.plan_tier == 'free_team', User.subscription_expires > int(time.time())),
                    Connection.api_pause_reason.in_(['', 'rate_limited']),
                    User.freelancer_cooldown_until <= int(time.time()),
                    Connection.api_cooldown_until <= int(time.time()))
            .all()
        ]
    for uid, last_scan in accounts:
        try:
            if not last_scan or last_scan < int(time.time()) - SCAN_SECONDS:
                summary = perform_scan(uid, force=True)
                log.info('Account %s scan, auto-bid & auto-chat summary: %s', uid, summary)
            elif PLATFORM_AUTO:
                # Drain saved pending jobs between scans without re-polling
                # projects or chat threads. The persistent bid gap still applies.
                run_autobid_cycle(uid)
        except Exception as e:
            log.warning('Account %s scan failed (%s); inspect account settings/activity', uid, type(e).__name__)


def main():
    init_db()
    log.info('BidPilot AI Auto-Bidder & Chat Worker started (Scan interval: %ss, Auto-Bid Gate: %s)', SCAN_SECONDS, PLATFORM_AUTO)
    while True:
        try:
            once()
        except Exception:
            log.exception('Worker loop failed')
        time.sleep(20)


if __name__ == '__main__':
    main()
