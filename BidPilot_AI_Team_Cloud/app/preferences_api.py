"""Authenticated preference editing and credential-free configuration backups."""
import json
import time
from typing import Any, Literal

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError, field_validator

from .advanced import AdvancedConfigError, AdvancedSettings, load_advanced
from .db import User, Job, Connection, audit, public_user
from .logic import score_for_user


MAX_BACKUP_BYTES = 128 * 1024


class SettingsBackup(BaseModel):
    model_config = {'extra': 'forbid', 'allow_inf_nan': False}
    format: Literal['bidpilot-settings']
    version: Literal[1]
    settings: dict[str, Any] = Field(max_length=40)
    advanced: AdvancedSettings

    @field_validator('version', mode='before')
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('Unsupported backup version')
        return value


def _basic_preferences(main, raw):
    # This allowlist is the ordinary preferences input model, which contains
    # no password, token, role, subscription, provider credential or endpoint.
    if not isinstance(raw, dict) or set(raw) - set(main.SettingsIn.model_fields):
        raise HTTPException(422, 'Backup contains unsupported preference fields')
    try:
        values = main.SettingsIn.model_validate({**raw, 'auto_requested': False, 'auto_chat_greeting': False})
    except ValidationError:
        raise HTTPException(422, 'Invalid backup preferences; use a complete BidPilot settings export') from None
    if (len(values.name.strip()) < 2 or not values.skills.strip() or not values.search_terms.strip()
            or values.max_bid < values.min_budget):
        raise HTTPException(422, 'Backup contains invalid profile or bid limits')
    if values.portfolio and not values.portfolio.startswith(('https://', 'http://')):
        raise HTTPException(422, 'Backup portfolio must use an HTTP or HTTPS address')
    return values


def _rescore(s, uid, user):
    for row in s.query(Job).filter_by(user_id=uid):
        try:
            raw = json.loads(row.data)
            if not isinstance(raw, dict):
                raise ValueError('Invalid project')
            result = score_for_user(raw, user)
        except (ValueError, TypeError):
            result = {'score': 0, 'decision': 'Skip', 'reasons': ['Project data is invalid; scan again']}
        row.score = result['score']
        row.decision = result['decision']
        row.reasons = json.dumps(result['reasons'])


def _load(user):
    try:
        return load_advanced(user)
    except AdvancedConfigError:
        raise HTTPException(409, 'Saved advanced settings are invalid; save a valid configuration before automation') from None


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _restore(main, uid, preferences, advanced):
    # SQL work and rescoring must not block the ASGI event loop.
    with main.SessionLocal() as s:
        main.lock_user(s, uid)
        user = s.get(User, uid)
        if s.query(main.Bid).filter_by(user_id=uid, status='sending').first():
            raise HTTPException(409, 'A bid is being sent; check its result before restoring settings')
        for name, value in preferences.model_dump().items():
            setattr(user, name, value)
        user.advanced_config = advanced.model_dump_json()
        user.updated = int(time.time())
        _rescore(s, uid, user)
        audit(s, uid, 'settings_restored', 'Preferences restored; auto-bidding and auto-chat switched OFF; credentials and billing preserved')
        s.commit()
        return {'ok': True, 'automation_paused': True,
                'user': public_user(user, s.query(Connection).filter_by(user_id=uid).first()),
                'settings': advanced.model_dump(mode='json')}


def install_preferences_routes(app, current, mutate):
    @app.get('/api/advanced-settings')
    def get_advanced_settings(uid: int = Depends(current)):
        from . import main
        with main.SessionLocal() as s:
            return {'settings': _load(s.get(User, uid)).model_dump(mode='json')}

    @app.post('/api/advanced-settings')
    def save_advanced_settings(values: AdvancedSettings, uid: int = Depends(mutate)):
        from . import main
        with main.SessionLocal() as s:
            main.lock_user(s, uid)
            user = s.get(User, uid)
            user.advanced_config = values.model_dump_json()
            user.updated = int(time.time())
            _rescore(s, uid, user)
            audit(s, uid, 'advanced_settings_updated', 'Project filters, proposal templates, queue and schedule preferences saved')
            s.commit()
            return {'settings': values.model_dump(mode='json')}

    @app.get('/api/settings/export')
    def export_settings(uid: int = Depends(current)):
        from . import main
        with main.SessionLocal() as s:
            user = s.get(User, uid)
            raw = {name: getattr(user, name) for name in main.SettingsIn.model_fields}
            preferences = _basic_preferences(main, raw)
            backup = SettingsBackup(
                format='bidpilot-settings', version=1,
                settings=preferences.model_dump(mode='json'), advanced=_load(user),
            )
            return backup.model_dump(mode='json')

    @app.post('/api/settings/import')
    async def import_settings(request: Request, uid: int = Depends(mutate)):
        from . import main
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise HTTPException(413, 'Settings backup must be at most 128 KiB')
            chunks.append(chunk)
        try:
            raw = json.loads(b''.join(chunks), object_pairs_hook=_json_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))
            backup = SettingsBackup.model_validate(raw)
        except (ValueError, UnicodeError, RecursionError):
            # Never echo file contents, validation input or supplied credentials.
            raise HTTPException(422, 'Invalid settings backup; expected a version 1 BidPilot export') from None
        preferences = _basic_preferences(main, backup.settings)
        return await run_in_threadpool(_restore, main, uid, preferences, backup.advanced)
