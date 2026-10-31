"""Deterministic subscription dates and idempotent expiry; no AI payment decisions."""
import calendar
from datetime import datetime
import time
from zoneinfo import ZoneInfo


BILLING_TIMEZONE = 'Asia/Karachi'
PERIOD_LABEL = 'calendar month'


def next_calendar_month(timestamp, months=1, *, anchor_day=None):
    """Same Karachi wall time next month, clamped to that month's last day."""
    source = datetime.fromtimestamp(int(timestamp), ZoneInfo(BILLING_TIMEZONE))
    index = source.year * 12 + source.month - 1 + int(months)
    year, month = divmod(index, 12)
    month += 1
    day = min(int(anchor_day or source.day), calendar.monthrange(year, month)[1])
    return int(source.replace(year=year, month=month, day=day).timestamp())


def subscription_start(user):
    saved = int(getattr(user, 'subscription_started_at', 0) or 0)
    legacy = int(getattr(user, 'billing_period_started', 0) or 0) if int(getattr(user, 'subscription_expires', 0) or 0) > 0 else 0
    return saved or legacy


def renewal_expiry(user, now=None):
    now = int(time.time() if now is None else now)
    start = subscription_start(user)
    day = datetime.fromtimestamp(start, ZoneInfo(BILLING_TIMEZONE)).day if start else None
    return next_calendar_month(max(now, int(user.subscription_expires or 0)), anchor_day=day)


def billing_period_start(user, now=None):
    """Keep old 30-day quota anchors; new grants use calendar-month windows."""
    now = int(time.time() if now is None else now)
    anchor = int(user.billing_period_started or now)
    if not int(getattr(user, 'subscription_started_at', 0) or 0):
        return anchor + max(0, (now - anchor) // (30 * 86400)) * (30 * 86400)
    first = datetime.fromtimestamp(anchor, ZoneInfo(BILLING_TIMEZONE))
    current = datetime.fromtimestamp(now, ZoneInfo(BILLING_TIMEZONE))
    months = max(0, (current.year - first.year) * 12 + current.month - first.month)
    candidate = next_calendar_month(anchor, months, anchor_day=first.day)
    if candidate > now and months:
        candidate = next_calendar_month(anchor, months - 1, anchor_day=first.day)
    return candidate


def expire_due_subscriptions(session, *, now=None, user_id=None):
    """Conditional updates cannot expire a concurrently renewed subscription.

    Caller owns the transaction. Actual paid actions separately check their
    current timestamp, so an offline worker never grants access past expiry.
    """
    from .db import User, audit

    now = int(time.time() if now is None else now)
    query = session.query(User.id).filter(
        User.plan_tier.in_(['paid_starter', 'paid_pro']),
        User.subscription_status.in_(['active', 'trial']),
        User.subscription_expires > 0, User.subscription_expires <= now,
    )
    if user_id is not None:
        query = query.filter(User.id == user_id)
    count = 0
    for (uid,) in query.order_by(User.id).all():
        changed = session.query(User).filter(
            User.id == uid, User.plan_tier.in_(['paid_starter', 'paid_pro']),
            User.subscription_status.in_(['active', 'trial']),
            User.subscription_expires > 0, User.subscription_expires <= now,
        ).update({User.subscription_status: 'expired', User.auto_requested: False,
                  User.auto_chat_greeting: False, User.updated: now,
                  User.lock_version: User.lock_version + 1}, synchronize_session='fetch')
        if changed:
            audit(session, uid, 'subscription_expired', 'Paid access expired; automatic bids and greetings paused.')
            count += 1
    return count


def expire_subscriptions(*, now=None):
    """Worker/startup entry point, using the current configured session factory."""
    from . import db

    with db.SessionLocal() as session:
        count = expire_due_subscriptions(session, now=now)
        session.commit()
        return count
