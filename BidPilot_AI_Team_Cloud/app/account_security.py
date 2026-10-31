"""Own-account password and session controls; credentials are never returned."""
import time

from fastapi import Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from .db import (
    AuthSession, LoginAttempt, User, audit, get_auth_session, hash_password,
    session_digest, verify_password,
)


class AccountSecurityIn(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_user_id: int | None = Field(default=None, ge=1, strict=True)


class PasswordChangeIn(AccountSecurityIn):
    current_password: str = Field(min_length=1, max_length=256, strict=True)
    new_password: str = Field(min_length=12, max_length=256, strict=True)

    @field_validator('new_password')
    @classmethod
    def meaningful_password(cls, value):
        if not value.strip():
            raise ValueError('Choose a non-blank password')
        return value


def _expected_account(expected, uid):
    if expected is not None and expected != uid:
        raise HTTPException(409, 'Account changed. Reload before changing security settings.')


def _active_session(session, uid, request):
    # Recheck after acquiring the user lock: another request may have changed
    # the password or revoked this session since the auth dependency ran.
    user = session.get(User, uid)
    authenticated = get_auth_session(session, request.cookies.get('bp_session', ''))
    if not user or not user.active or not authenticated or authenticated.user_id != uid:
        raise HTTPException(401, 'Please sign in again')
    return user


def _clear_cookie(response):
    from . import main
    response.delete_cookie('bp_session', path='/', secure=main.SECURE_COOKIES,
                           httponly=True, samesite='strict')


def install_account_security_routes(app, current, mutate):
    @app.get('/api/security')
    def account_security(expected_user_id: int | None = Query(default=None, ge=1), uid: int = Depends(current)):
        from . import main
        _expected_account(expected_user_id, uid)
        with main.SessionLocal() as session:
            active_sessions = session.query(AuthSession).filter(
                AuthSession.user_id == uid, AuthSession.expires_at > int(time.time()),
            ).count()
        return {
            'active_sessions': active_sessions,
            'password_storage': 'salted_pbkdf2',
            'saved_credentials_encrypted': True,
            'password_sharing_required': False,
            'payment_verification': 'administrator_review',
            'notice': 'Never share your account password, API keys or OTP for a payment or plan upgrade.',
        }

    @app.post('/api/security/password')
    def change_password(values: PasswordChangeIn, request: Request, response: Response, uid: int = Depends(mutate)):
        from . import main
        _expected_account(values.expected_user_id, uid)
        now = int(time.time())
        identifier = session_digest(f'password-change:{uid}')
        with main.SessionLocal() as session:
            main.lock_user(session, uid)
            user = _active_session(session, uid, request)
            failures = session.query(LoginAttempt).filter(
                LoginAttempt.identifier == identifier, LoginAttempt.attempted_at >= now - 900,
            ).count()
            if failures >= 5:
                raise HTTPException(429, 'Too many password attempts. Try again in 15 minutes.')
            if not verify_password(values.current_password, user.password_hash):
                session.add(LoginAttempt(identifier=identifier, attempted_at=now))
                session.commit()
                raise HTTPException(400, 'Current password is incorrect')
            if values.new_password == values.current_password:
                raise HTTPException(400, 'Choose a different new password')
            user.password_hash = hash_password(values.new_password)
            user.updated = now
            session.query(AuthSession).filter_by(user_id=uid).delete(synchronize_session=False)
            session.query(LoginAttempt).filter_by(identifier=identifier).delete(synchronize_session=False)
            audit(session, uid, 'password_changed', 'Own account password changed; all sessions revoked')
            session.commit()
        _clear_cookie(response)
        return {'ok': True, 'sign_in_required': True}

    @app.post('/api/security/logout-all')
    def logout_all(values: AccountSecurityIn, request: Request, response: Response, uid: int = Depends(mutate)):
        from . import main
        _expected_account(values.expected_user_id, uid)
        with main.SessionLocal() as session:
            main.lock_user(session, uid)
            _active_session(session, uid, request)
            session.query(AuthSession).filter_by(user_id=uid).delete(synchronize_session=False)
            audit(session, uid, 'sessions_revoked', 'All account sessions revoked by signed-in user')
            session.commit()
        _clear_cookie(response)
        return {'ok': True, 'sign_in_required': True}
