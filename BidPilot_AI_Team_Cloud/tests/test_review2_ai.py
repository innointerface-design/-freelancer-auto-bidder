"""Provider Unicode and support-context privacy regressions; no external calls."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, integrations as ai, main, support


def provider_response(provider, reply):
    if provider == 'gemini':
        payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': reply}]}}]}
    else:
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': reply}}]}
    # Escaped lone surrogates are legal JSON input to Python's decoder, even
    # though they cannot be encoded in the UTF-8 HTTP response returned to users.
    return httpx.Response(200, content=json.dumps(payload).encode('ascii'))


def mock_provider(monkeypatch, provider, reply):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: provider_response(provider, reply))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('reply', ['A broken high surrogate: \ud800', 'A broken low surrogate: \udfff', 'Invalid NUL: \x00'])
def test_invalid_unicode_provider_output_is_a_safe_provider_error(monkeypatch, provider, reply):
    mock_provider(monkeypatch, provider, reply)
    with pytest.raises(ai.AIProviderError) as failure:
        getattr(ai, '_call_' + provider)('synthetic-provider-key', 'test-model', 'Synthetic question')
    assert 'invalid text encoding' in str(failure.value)
    assert 'surrogate' not in str(failure.value)


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_valid_multilingual_provider_output_is_preserved(monkeypatch, provider):
    reply = 'سلام — hello 👋'
    mock_provider(monkeypatch, provider, reply)
    answer = getattr(ai, '_call_' + provider)('synthetic-provider-key', 'test-model', 'Synthetic question')
    assert answer == reply
    assert answer.encode('utf-8').decode('utf-8') == reply


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_chat_api_returns_unavailable_instead_of_crashing_on_invalid_provider_text(tmp_path, monkeypatch, provider):
    engine = create_engine(f'sqlite:///{tmp_path}/unicode-chat.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    creds = {provider + '_key': 'synthetic-provider-key', provider + '_model': 'test-model'}
    monkeypatch.setattr(ai, 'resolve_ai_credentials', lambda user: creds)
    with ai._AI_CIRCUIT_LOCK:
        ai._AI_CIRCUITS.clear()
    try:
        with TestClient(main.app) as client:
            login = client.post('/api/login', json={
                'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!',
            })
            assert login.status_code == 200
            headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
            mock_provider(monkeypatch, provider, 'Bad provider output \ud800')
            response = client.post('/api/support/chat', headers=headers, json={
                'question': 'Explain recursion', 'provider': provider, 'mode': 'general',
            })
            assert response.status_code == 200
            result = response.json()
            assert result['provider'] == 'unavailable' and result['ai_used'] is False
            assert 'Bad provider output' not in response.text
    finally:
        engine.dispose()
        with ai._AI_CIRCUIT_LOCK:
            ai._AI_CIRCUITS.clear()


@pytest.mark.parametrize('value', [
    'password="PRIVATE-MIDDLE PRIVATE-TAIL"',
    "api_key='PRIVATE-MIDDLE PRIVATE-TAIL'",
    r'password="PRIVATE-MIDDLE\" PRIVATE-TAIL"',
])
def test_quoted_snapshot_credentials_are_redacted_before_json_escaping(value):
    prompt = support.make_support_prompt('Help with settings', [], {
        'configured_models': {'gemini': value},
        'nested': [value], 'connected': True, 'max_daily': 10,
    })
    data = json.loads(prompt.split('Quoted support data:\n', 1)[1])
    assert 'PRIVATE-MIDDLE' not in prompt and 'PRIVATE-TAIL' not in prompt
    assert data['safe_account_snapshot']['configured_models']['gemini'] == '[redacted credential]'
    assert data['safe_account_snapshot']['nested'] == ['[redacted credential]']
    assert data['safe_account_snapshot']['connected'] is True
    assert data['safe_account_snapshot']['max_daily'] == 10


def test_known_snapshot_secret_with_escapes_is_redacted_without_corrupting_code():
    secret = 'synthetic-"quoted"-\\-private'
    code = 'api_key = os.getenv("OPENAI_API_KEY")'
    prompt = support.make_support_prompt(code, [], {'configured_models': {'openai': secret}}, [secret])
    data = json.loads(prompt.split('Quoted support data:\n', 1)[1])
    assert data['safe_account_snapshot']['configured_models']['openai'] == '[redacted credential]'
    assert data['latest_question'] == code
