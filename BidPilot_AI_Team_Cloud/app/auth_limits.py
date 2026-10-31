"""Persistent sign-in limits distinguish failures from successful account use."""
from dataclasses import dataclass
import time

from fastapi import HTTPException
from sqlalchemy import and_, or_, text, update

from .db import LoginAttempt, session_digest


FAILURE_WINDOW = 900
ACCOUNT_FAILURE_LIMIT = 10
NETWORK_FAILURE_LIMIT = 100
REQUEST_WINDOW = 60
NETWORK_REQUEST_LIMIT = 120
LEASE_SECONDS = 30


class LoginRateLimited(HTTPException):
    def __init__(self, retry_after):
        self.retry_after_seconds = max(1, int(retry_after))
        super().__init__(429, 'Too many sign-in attempts. Try again after the displayed wait.',
                         headers={'Retry-After': str(self.retry_after_seconds)})


def account_identifier(email):
    return session_digest('login-v2:failed:email:' + email.strip().lower())


def _identifiers(email, ip):
    email = email.strip().lower()
    return {
        'account': account_identifier(email),
        'network': session_digest('login-v2:failed:ip:' + ip),
        'requests': session_digest('login-v2:requests:ip:' + ip),
        'account_lease': session_digest('login-v2:inflight:email:' + email),
        'network_lease': session_digest('login-v2:inflight:ip:' + ip),
    }


def lock_identifiers(session, identifiers):
    if session.bind.dialect.name == 'postgresql':
        for identifier in sorted(set(identifiers)):
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                            {'key': int(identifier[:15], 16)})
    elif session.bind.dialect.name == 'sqlite':
        # An UPDATE takes SQLite's write reservation even when no row matches.
        # It serializes unknown-account admission without locking during hashing.
        session.execute(update(LoginAttempt).where(LoginAttempt.id < 0).values(
            attempted_at=LoginAttempt.attempted_at))


@dataclass(frozen=True)
class LoginReservation:
    identifiers: dict
    lease_ids: tuple
    claimed_at: int


def reserve_login(sessions, email, ip, *, now=None):
    now = int(time.time() if now is None else now)
    identifiers = _identifiers(email, ip)
    with sessions() as session:
        lock_identifiers(session, [identifiers['account'], identifiers['network']])
        session.query(LoginAttempt).filter(LoginAttempt.attempted_at < now - FAILURE_WINDOW).delete(synchronize_session=False)
        waits = []
        for category, lease_category, limit in (
            ('account', 'account_lease', ACCOUNT_FAILURE_LIMIT),
            ('network', 'network_lease', NETWORK_FAILURE_LIMIT),
        ):
            failures = [row[0] for row in session.query(LoginAttempt.attempted_at).filter(
                LoginAttempt.identifier == identifiers[category],
                LoginAttempt.attempted_at > now - FAILURE_WINDOW,
            ).all()]
            leases = [row[0] for row in session.query(LoginAttempt.attempted_at).filter(
                LoginAttempt.identifier == identifiers[lease_category],
                LoginAttempt.attempted_at > now - LEASE_SECONDS,
            ).all()]
            if len(failures) + len(leases) >= limit:
                deadlines = [stamp + FAILURE_WINDOW for stamp in failures]
                deadlines.extend(stamp + LEASE_SECONDS for stamp in leases)
                waits.append(sorted(deadlines)[len(deadlines) - limit] - now)
        requests = [row[0] for row in session.query(LoginAttempt.attempted_at).filter(
            LoginAttempt.identifier == identifiers['requests'],
            LoginAttempt.attempted_at > now - REQUEST_WINDOW,
        ).all()]
        if len(requests) >= NETWORK_REQUEST_LIMIT:
            waits.append(sorted(requests)[len(requests) - NETWORK_REQUEST_LIMIT] + REQUEST_WINDOW - now)
        if waits:
            raise LoginRateLimited(max(waits))
        leases = [LoginAttempt(identifier=identifiers[name], attempted_at=now)
                  for name in ('account_lease', 'network_lease')]
        session.add_all(leases)
        session.add(LoginAttempt(identifier=identifiers['requests'], attempted_at=now))
        session.flush()
        reservation = LoginReservation(identifiers, tuple(row.id for row in leases), now)
        session.commit()
        return reservation


def _owned_leases(session, reservation):
    claims = [and_(LoginAttempt.id == row_id,
                   LoginAttempt.identifier == reservation.identifiers[name],
                   LoginAttempt.attempted_at == reservation.claimed_at)
              for row_id, name in zip(reservation.lease_ids, ('account_lease', 'network_lease'))]
    return session.query(LoginAttempt).filter(or_(*claims))


def finish_login(session, reservation, authenticated, *, now=None):
    """Caller holds both identifier locks and commits together with the session."""
    now = int(time.time() if now is None else now)
    owned = _owned_leases(session, reservation)
    active = (0 <= now - reservation.claimed_at < LEASE_SECONDS and owned.count() == 2)
    owned.delete(synchronize_session=False)
    if not active:
        return False
    if authenticated:
        session.query(LoginAttempt).filter_by(identifier=reservation.identifiers['account']).delete(synchronize_session=False)
    else:
        session.add_all([LoginAttempt(identifier=reservation.identifiers[name], attempted_at=now)
                         for name in ('account', 'network')])
    return True


def release_login(sessions, reservation):
    with sessions() as session:
        lock_identifiers(session, [reservation.identifiers['account'], reservation.identifiers['network']])
        _owned_leases(session, reservation).delete(synchronize_session=False)
        session.commit()


def unlock_account_failures(session, email):
    identifier = account_identifier(email)
    lock_identifiers(session, [identifier])
    return session.query(LoginAttempt).filter_by(identifier=identifier).delete(synchronize_session=False)
