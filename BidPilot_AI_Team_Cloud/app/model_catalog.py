"""Credential-scoped, bounded discovery of provider models for text drafting.

Listing a model does not prove generation access or compatibility with a specific
generation endpoint. This module never changes a saved selection or generates text.
"""
import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from .integrations import (
    AIProviderError, _credential, _gemini_headers, _openai_base_url,
    _request_json, resolve_ai_credentials,
)


REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
MIN_REFRESH_INTERVAL_SECONDS = 60
ERROR_CACHE_SECONDS = 60
MAX_CACHE_ENTRIES = 256
MAX_GEMINI_PAGES = 3
MAX_OPENAI_MODELS = 2000
_WAIT_SECONDS = 35.0
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}\Z')
_GEMINI_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z')
# A denylist of clearly non-text tasks allows future text model families through.
# Neither provider's listing endpoint certifies chat-completion compatibility.
_NON_TEXT = re.compile(
    r'(?:^|[-._:/])(?:image|imagen|veo|sora|dall-e|audio|realtime|embedding|embed|'
    r'moderation|whisper|transcribe|transcription|tts|lyria)(?:$|[-._:/])', re.I,
)
_COMPLETE_MESSAGE = 'Available text model candidates. Listing does not verify generation access; test your selected model.'
_EMPTY_MESSAGE = 'No text model candidates were returned for this credential. Check provider access and permissions.'
_PARTIAL_MESSAGE = 'The model list is incomplete. You can keep your saved selection and refresh later.'
_ERROR_MESSAGE = 'Model discovery failed. Check provider credentials, permissions, billing and connectivity, then retry.'
_STALE_MESSAGE = 'Model refresh failed. Previously discovered models are shown; their current availability is unconfirmed.'


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _refresh_time(seconds):
    return datetime.fromtimestamp(time.time() + max(0, seconds), timezone.utc).isoformat().replace('+00:00', 'Z')


def _fingerprint(provider, key):
    config = (os.getenv('GEMINI_AUTH_MODE', 'api_key').strip().lower()
              if provider == 'gemini' else os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/'))
    return hashlib.sha256(json.dumps((provider, key, config), ensure_ascii=True).encode()).digest()


def _models(found, provider):
    return [{'id': item, 'name': item, 'provider': provider} for item in sorted(found, key=str.casefold)]


def _accepted_id(value, provider, key):
    pattern = _GEMINI_ID if provider == 'gemini' else _MODEL_ID
    return isinstance(value, str) and bool(pattern.fullmatch(value)) and key not in value


def _loaded(found, provider, *, complete=False, failed=False):
    status = 'complete' if complete else ('error' if failed and not found else 'partial')
    return {
        'models': _models(found, provider), 'status': status, 'complete': complete,
        'message': ((_COMPLETE_MESSAGE if found else _EMPTY_MESSAGE) if complete
                    else (_ERROR_MESSAGE if status == 'error' else _PARTIAL_MESSAGE)),
        'fetched_at': _utc_now(), 'stale': False,
    }


def _load_gemini(key):
    found, seen_tokens, token = set(), set(), None
    malformed, pages_loaded = False, 0
    try:
        headers = _gemini_headers(key)
        for _ in range(MAX_GEMINI_PAGES):
            params = {'pageSize': 100}
            if token is not None:
                params['pageToken'] = token
            data = _request_json(
                'GET', 'https://generativelanguage.googleapis.com/v1beta/models',
                headers, None, 'Gemini', params=params, timeout=10.0,
            )
            records = data.get('models')
            if not isinstance(records, list):
                raise AIProviderError('Invalid model list')
            pages_loaded += 1
            for item in records[:100]:
                if not isinstance(item, dict):
                    malformed = True
                    continue
                methods, name = item.get('supportedGenerationMethods'), item.get('name')
                if not isinstance(methods, list) or not isinstance(name, str) or not name.startswith('models/'):
                    malformed = True
                    continue
                model_id = name[len('models/'):]
                if not _accepted_id(model_id, 'gemini', key):
                    malformed = True
                    continue
                if 'generateContent' in methods and not _NON_TEXT.search(model_id):
                    found.add(model_id)
            if len(records) > 100:
                break
            token = data.get('nextPageToken')
            if token is None or token == '':
                return _loaded(found, 'gemini', complete=not malformed)
            if (not isinstance(token, str) or len(token) > 4096 or not token.isascii()
                    or any(ord(c) < 32 or ord(c) == 127 for c in token) or token in seen_tokens):
                break
            seen_tokens.add(token)
    except AIProviderError:
        return _loaded(found, 'gemini', failed=not pages_loaded)
    return _loaded(found, 'gemini')


def _load_openai(key):
    key = _credential(key)
    data = _request_json(
        'GET', _openai_base_url() + '/models',
        {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        None, 'OpenAI', timeout=10.0,
    )
    records = data.get('data')
    if not isinstance(records, list):
        raise AIProviderError('Invalid model list')
    found, malformed = set(), False
    for item in records[:MAX_OPENAI_MODELS]:
        model_id = item.get('id') if isinstance(item, dict) else None
        if not _accepted_id(model_id, 'openai', key):
            malformed = True
            continue
        if not _NON_TEXT.search(model_id):
            found.add(model_id)
    return _loaded(found, 'openai', complete=not malformed and len(records) <= MAX_OPENAI_MODELS and not data.get('has_more'))


def _snapshot(state, *, cached, throttled=False):
    """Must be called under the cache lock; callers cannot mutate cache entries."""
    result = state.get('result')
    if result is None:
        result = {'models': [], 'status': 'refreshing', 'complete': False,
                  'message': 'Model discovery is already running. Refresh shortly.',
                  'fetched_at': None, 'stale': False}
    return {
        **result, 'models': [dict(model) for model in result['models']],
        'cached': cached, 'refresh_throttled': throttled,
        'refresh_after': _refresh_time(state.get('expires', time.monotonic()) - time.monotonic()),
    }


def _cached_provider(provider, key, force):
    fingerprint = _fingerprint(provider, key)
    with _CACHE_LOCK:
        now = time.monotonic()
        state = _CACHE.get(fingerprint)
        if state is not None:
            _CACHE.move_to_end(fingerprint)
            if not state['event'].is_set():
                wait_event = state['event']
            else:
                if not force and now < state['expires']:
                    return _snapshot(state, cached=True)
                if now - state['last_attempt'] < MIN_REFRESH_INTERVAL_SECONDS:
                    return _snapshot(state, cached=True, throttled=True)
                state['event'] = threading.Event()
                state['last_attempt'] = now
                wait_event = None
        else:
            # Never evict an active request: that would allow duplicate remote calls.
            if len(_CACHE) >= MAX_CACHE_ENTRIES:
                removable = next((k for k, item in _CACHE.items() if item['event'].is_set()), None)
                if removable is None:
                    return {'models': [], 'status': 'refreshing', 'complete': False,
                            'message': 'Model discovery is busy. Refresh shortly.',
                            'fetched_at': None, 'refresh_after': _refresh_time(60),
                            'cached': False, 'refresh_throttled': True, 'stale': False}
                _CACHE.pop(removable)
            state = {'event': threading.Event(), 'last_attempt': now, 'expires': now}
            _CACHE[fingerprint] = state
            wait_event = None

    if wait_event is not None:
        wait_event.wait(timeout=_WAIT_SECONDS)
        with _CACHE_LOCK:
            return _snapshot(state, cached=True, throttled=force)

    try:
        result = _load_gemini(key) if provider == 'gemini' else _load_openai(key)
    except Exception:
        # Provider responses, exception text, URLs and credentials are never returned.
        # Complete the single-flight event even for unexpected transport failures.
        result = _loaded(set(), provider, failed=True)
    with _CACHE_LOCK:
        previous = state.get('result')
        if result['status'] == 'error' and previous and previous['models']:
            result = {**result, 'models': previous['models'], 'status': 'partial',
                      'fetched_at': previous['fetched_at'], 'stale': True, 'message': _STALE_MESSAGE}
        state['result'] = result
        ttl = REFRESH_INTERVAL_SECONDS if result['complete'] else ERROR_CACHE_SECONDS
        state['expires'] = time.monotonic() + ttl
        state['event'].set()
        return _snapshot(state, cached=False)


def get_model_catalog(user=None, *, force=False):
    """Return current credential-scoped candidates, preserving the saved selection.

    A normal read refreshes after six hours. Failed/partial reads retry after one
    minute; manual refreshes also wait at least one minute between remote attempts.
    Keys remain local to this call and only their SHA-256 fingerprint is cached.
    """
    credentials = resolve_ai_credentials(user)
    providers = {}
    for provider in ('gemini', 'openai'):
        key = credentials.get(provider + '_key') or ''
        selected = credentials.get(provider + '_model') or ''
        # Saved selection is separate from discovery and cannot be removed by an
        # outage or a list that does not include that selection.
        if not isinstance(selected, str) or (key and key in selected):
            selected = ''
        if key:
            entry = _cached_provider(provider, key, bool(force))
        else:
            entry = {'models': [], 'status': 'unconfigured', 'complete': False,
                     'message': 'Save a provider API key to discover available models.',
                     'fetched_at': None, 'refresh_after': None,
                     'cached': False, 'refresh_throttled': False, 'stale': False}
        providers[provider] = {
            **entry, 'provider': provider, 'configured': bool(key),
            'selected_model': selected, 'generation_verified': False,
        }
    return {'providers': providers, 'refresh_interval_seconds': REFRESH_INTERVAL_SECONDS,
            'min_refresh_interval_seconds': MIN_REFRESH_INTERVAL_SECONDS,
            'generation_verified': False}
