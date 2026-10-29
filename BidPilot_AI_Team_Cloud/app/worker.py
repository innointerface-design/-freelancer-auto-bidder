"""BidPilot Pro Background Worker: Fast 60-second Bidman-style scanner, AI Auto-Bidder & Auto-Chat Greeter."""
import time, logging
from sqlalchemy import or_, and_
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
        ids = [
            r[0] for r in s.query(Connection.user_id)
            .join(User, Connection.user_id == User.id)
            .filter(User.active.is_(True), User.subscription_status.in_(['active', 'trial']),
                    or_(User.plan_tier == 'free_team', User.subscription_expires > int(time.time())),
                    Connection.last_scan < int(time.time()) - SCAN_SECONDS)
            .all()
        ]
    for uid in ids:
        try:
            summary = perform_scan(uid, force=True)
            log.info('Account %s scan, auto-bid & auto-chat summary: %s', uid, summary)
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
