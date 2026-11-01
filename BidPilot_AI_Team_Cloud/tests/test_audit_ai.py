"""Synthetic regressions for pasted credentials and malformed provider JSON."""
import json

import httpx
import pytest

from app import integrations as ai, support


@pytest.mark.parametrize('assignment', [
    '{"api_key": "UNCONFIGURED-PRIVATE-VALUE"}',
    "{'password': 'UNCONFIGURED-PRIVATE-VALUE'}",
    '{"access_token": "UNCONFIGURED-PRIVATE-VALUE"}',
    '{"x-goog-api-key": "UNCONFIGURED-PRIVATE-VALUE"}',
    'OPENAI_API_KEY=UNCONFIGURED-PRIVATE-VALUE',
    'GEMINI_API_KEY=UNCONFIGURED-PRIVATE-VALUE',
    '{"openai_key": "UNCONFIGURED-PRIVATE-VALUE"}',
    '{"gemini_key": "UNCONFIGURED-PRIVATE-VALUE"}',
])
@pytest.mark.parametrize('mode', ['support', 'general'])
def test_pasted_credential_assignments_are_redacted_before_cloud_and_display(monkeypatch, assignment, mode):
    captured = []
    creds = {'gemini_key': 'synthetic-configured-key', 'gemini_model': 'synthetic-model'}

    def provider(key, model, prompt, **kwargs):
        captured.append(prompt)
        return assignment

    monkeypatch.setattr(ai, '_call_gemini', provider)
    result = support.support_chat_reply(
        assignment, [{'role': 'assistant', 'content': assignment}], 'gemini', creds, {}, mode=mode,
    )
    assert result['ai_used'] and result['provider'] == 'gemini'
    assert 'UNCONFIGURED-PRIVATE-VALUE' not in captured[0]
    assert 'UNCONFIGURED-PRIVATE-VALUE' not in json.dumps(result)
    assert '[redacted credential]' in captured[0] and '[redacted credential]' in result['reply']


@pytest.mark.parametrize('assignment', [
    '''password:'a PRIVATE-MIDDLE PRIVATE-TAIL' safe suffix''',
    '''{"api_key": "a PRIVATE-MIDDLE PRIVATE-TAIL"} safe suffix''',
    r'''password="a \"PRIVATE-MIDDLE\" PRIVATE-TAIL" safe suffix''',
    r'''password='a \'PRIVATE-MIDDLE\' PRIVATE-TAIL' safe suffix''',
    r'''api_key="a PRIVATE-MIDDLE\\folder PRIVATE-TAIL" safe suffix''',
    '''password='a PRIVATE-MIDDLE PRIVATE-TAIL''',
    '''api_key="a PRIVATE-MIDDLE PRIVATE-TAIL''',
    '''password="a PRIVATE-MIDDLE PRIVATE-TAIL\\''',
])
@pytest.mark.parametrize('mode', ['support', 'general'])
def test_complete_quoted_credentials_are_redacted_in_cloud_prompts_and_replies(monkeypatch, assignment, mode):
    captured = []
    creds = {'gemini_key': 'synthetic-configured-key', 'gemini_model': 'synthetic-model'}

    def provider(key, model, prompt, **kwargs):
        captured.append(prompt)
        return assignment

    monkeypatch.setattr(ai, '_call_gemini', provider)
    result = support.support_chat_reply(
        assignment, [{'role': 'user', 'content': assignment}], 'gemini', creds, {}, mode=mode,
    )
    assert result['ai_used'] and result['provider'] == 'gemini'
    for output in (captured[0], result['reply']):
        assert 'PRIVATE-MIDDLE' not in output and 'PRIVATE-TAIL' not in output
        assert '[redacted credential]' in output
    if 'safe suffix' in assignment:
        assert 'safe suffix' in captured[0] and 'safe suffix' in result['reply']


@pytest.mark.parametrize('status', [200, 401])
@pytest.mark.parametrize('provider', ['Gemini', 'OpenAI'])
def test_deep_provider_json_is_a_safe_error_with_original_http_status(monkeypatch, status, provider):
    real_client = httpx.Client
    body = b'{"nested":' + b'[' * 10_000 + b'0' + b']' * 10_000 + b'}'
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))

    with pytest.raises(ai.AIProviderError) as failure:
        ai._request_json('POST', 'https://provider.example/generate', {}, {}, provider)

    if status == 401:
        assert failure.value.status_code == 401
        assert failure.value.failure_kind == 'credential_rejected'
        assert failure.value.response_format == 'non_json'
    else:
        assert 'invalid JSON' in str(failure.value)
    assert 'nested' not in str(failure.value)
