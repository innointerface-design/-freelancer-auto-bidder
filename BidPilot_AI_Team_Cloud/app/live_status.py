"""Authenticated worker and saved-connection status, without remote API calls."""
import json
import time

from fastapi import Depends, HTTPException

from .advanced import AdvancedConfigError, load_advanced, schedule_allows
from .db import Bid, Connection, Job, User, WorkerHeartbeat, subscription_active


HEARTBEAT_STALE_SECONDS = 35


def automation_status(session, uid, *, now=None):
    from . import main
    now = int(time.time() if now is None else now)
    user = session.get(User, uid)
    connection = session.query(Connection).filter_by(user_id=uid).first()
    heartbeat = session.get(WorkerHeartbeat, 'scanner')
    beat = int(heartbeat.updated_at or 0) if heartbeat else 0
    worker_online = bool(beat and 0 <= now - beat <= HEARTBEAT_STALE_SECONDS)
    interval = max(60, int(main.SCAN_SECONDS))
    last_scan = int(connection.last_scan or 0) if connection else 0
    last_success = int(connection.last_scan_success_at or 0) if connection else 0
    claim_until = int(connection.scan_claim_until or 0) if connection else 0
    scanning = bool(connection and connection.scan_claim and claim_until > now)
    pause = (connection.api_pause_reason or '') if connection else ''
    cooldown = max(int(user.freelancer_cooldown_until or 0),
                   int(connection.api_cooldown_until or 0) if connection else 0)
    if cooldown > now and not pause:
        pause = 'rate_limited'

    final_jobs = session.query(Bid.job_id).filter(Bid.user_id == uid)
    queued = 0
    if connection:
        candidates = session.query(Job.data).filter(
            Job.user_id == uid, Job.id.notin_(final_jobs), Job.decision != 'Skip',
            Job.score >= int(user.min_score if user.min_score is not None else 65),
        ).yield_per(200)
        for (data,) in candidates:
            try:
                raw = json.loads(data)
            except (TypeError, ValueError):
                continue
            if (isinstance(raw, dict) and not raw.get('demo')
                    and raw.get('environment') == connection.environment
                    and str(raw.get('account_id') or '') == str(connection.freelancer_id)
                    and raw.get('project_status') == 'active'):
                queued += 1
    consumed = session.query(Bid).filter(Bid.user_id == uid, Bid.status.in_(['sending', 'submitted', 'uncertain']))
    hourly_used = consumed.filter(Bid.updated >= now - 3600).count()
    daily_used = consumed.filter(Bid.updated >= now - 86400).count()

    enabled = bool(main.PLATFORM_AUTO and user.active and subscription_active(user)
                   and user.auto_requested and user.auto_admin_allowed and connection)
    try:
        advanced = load_advanced(user)
    except AdvancedConfigError:
        advanced = None
        enabled = False
    gap = advanced.min_bid_interval_seconds if advanced else 60
    latest = consumed.order_by(Bid.updated.desc()).first()
    last_bid = max(int(connection.last_bid_attempt_at or 0) if connection else 0,
                   int(latest.updated or 0) if latest else 0)
    next_bid = last_bid + gap if last_bid else 0

    state, reason = 'watching', 'The worker checks for projects on the saved scan schedule. Bids follow your filters and limits.'
    if not connection:
        state, reason = 'disconnected', 'Connect your Freelancer account in Settings.'
    elif not subscription_active(user):
        state, reason = 'subscription_inactive', 'An active subscription is required for live project checks and automatic bidding.'
    elif cooldown > now:
        state, reason = 'cooldown', 'Freelancer API cooldown is active. The worker will respect the provider wait.'
    elif pause and pause != 'rate_limited':
        state, reason = 'paused', 'Freelancer access is paused. Check the account and reconnect before continuing.'
    elif advanced is None:
        state, reason = 'invalid_settings', 'Advanced settings are invalid. Review and save them before enabling automation.'
    elif not enabled:
        state, reason = 'paused', 'Automatic bidding is OFF or paused in workspace settings. Project scanning still requires a running worker.'
    elif not worker_online:
        state, reason = 'offline', 'The automation worker has no recent heartbeat. Ask the operator to start or check the service.'
    elif not schedule_allows(advanced, now=now):
        state, reason = 'outside_schedule', 'Outside your configured bidding hours. Matching projects can wait for the next allowed window.'
    else:
        try:
            main.bid_limits(session, user)
        except HTTPException as error:
            state, reason = 'paused', str(error.detail)
        else:
            if next_bid > now:
                state, reason = 'waiting_gap', 'Waiting for the configured minimum gap before another bid can be attempted.'
            elif scanning:
                state, reason = 'scanning', 'A Freelancer project refresh is in progress. New matches are checked after the response.'

    return {
        'server_time': now,
        'worker': {'online': worker_online, 'last_heartbeat': beat, 'stale_after_seconds': HEARTBEAT_STALE_SECONDS},
        'connection': {'connected': bool(connection), 'last_scan_started_at': last_scan,
                       'last_scan_success_at': last_success, 'scan_in_progress': scanning,
                       'last_scan_failed': bool(connection and connection.last_error and last_scan
                                                and last_scan >= last_success and not scanning),
                       'scan_claim_until': claim_until, 'api_pause_reason': pause, 'cooldown_until': cooldown},
        'automation': {'state': state, 'enabled': enabled, 'reason': reason,
                       'scan_interval_seconds': interval,
                       'next_scan_at': max(last_scan + interval if last_scan else now, cooldown) if connection else 0,
                       'next_bid_at': next_bid, 'queue_count': queued,
                       'limits': {'hourly_max': user.max_hourly, 'daily_max': user.max_daily,
                                  'hourly_used': hourly_used, 'daily_used': daily_used}},
    }


def install_live_routes(app, current):
    @app.get('/api/automation/status')
    def get_automation_status(expected_user_id: int | None = None, uid: int = Depends(current)):
        from . import main
        if expected_user_id is not None and expected_user_id != uid:
            raise HTTPException(409, 'Session changed. Reload before continuing.')
        with main.SessionLocal() as session:
            return automation_status(session, uid)
