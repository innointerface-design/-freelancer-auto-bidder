"""Interactive proposal provenance and failure regressions; no live provider calls."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app import integrations as ai
from app.advanced import AdvancedConfigError


DRAFT = 'I can review the supplied dashboard requirements and agree the scope before drafting the interface. Which screens should we prioritize?'
JOB = {'title': 'Python dashboard', 'description': 'Build a dashboard.', 'client_name': 'Alex'}


@pytest.fixture(autouse=True)
def isolated_providers(monkeypatch):
    with ai._AI_CIRCUIT_LOCK:
        ai._AI_CIRCUITS.clear()
    monkeypatch.delenv('OPENAI_API_MODE', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    # An accidental unmocked provider request must fail before reaching network.
    monkeypatch.setattr(ai, '_post_json', Mock(side_effect=AssertionError('Unexpected network request')))
    yield
    with ai._AI_CIRCUIT_LOCK:
        ai._AI_CIRCUITS.clear()


@pytest.fixture
def user():
    return SimpleNamespace(name='Tester', description='Python developer', skills='Python', portfolio='',
                           default_days=3, advanced_config='{}', proposal_tone='winning_direct')


@pytest.fixture
def credentials(monkeypatch):
    data = {'preferred_ai': 'gemini', 'gemini_key': 'synthetic-gemini-secret', 'gemini_model': 'test-gemini',
            'openai_key': 'synthetic-openai-secret', 'openai_model': 'test-openai'}
    monkeypatch.setattr(ai, 'resolve_ai_credentials', lambda _: data.copy())
    return data


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_success_identifies_actual_provider_and_model(monkeypatch, user, credentials, provider):
    credentials['preferred_ai'] = provider
    call = Mock(return_value=DRAFT)
    monkeypatch.setattr(ai, '_call_' + provider, call)
    result = ai.ai_proposal_result(JOB, user)
    assert result['proposal'] == DRAFT
    assert result['source'] == 'ai' and result['ai_used'] is True
    assert result['status'] == 'generated'
    assert result['provider'] == provider and result['model'] == credentials[provider + '_model']
    assert result['retry_after'] is None
    call.assert_called_once()
    assert not ai._AI_CIRCUITS


def test_auto_failover_labels_second_provider_without_repeating_paid_request(monkeypatch, user, credentials):
    credentials['preferred_ai'] = 'auto'
    first = Mock(side_effect=ai.AIProviderError('private provider body', failure_kind='quota_or_rate_limit', retry_after=300))
    second = Mock(return_value=DRAFT)
    monkeypatch.setattr(ai, '_call_gemini', first)
    monkeypatch.setattr(ai, '_call_openai', second)
    result = ai.ai_proposal_result(JOB, user)
    assert result['provider'] == 'openai' and result['ai_used'] is True
    assert [(item['provider'], item['status']) for item in result['attempts']] == [
        ('gemini', 'provider_error'), ('openai', 'generated')]
    first.assert_called_once()
    second.assert_called_once()
    assert 'private provider body' not in repr(result)


def test_explicit_provider_failure_is_labeled_local_and_never_switches(monkeypatch, user, credentials):
    remote = Mock(side_effect=ai.AIProviderError('synthetic-gemini-secret private project',
                                               status_code=401, failure_kind='credential_rejected', retry_after=300))
    other = Mock()
    monkeypatch.setattr(ai, '_call_gemini', remote)
    monkeypatch.setattr(ai, '_call_openai', other)
    result = ai.ai_proposal_result(JOB, user)
    assert result['source'] == 'local_template' and result['ai_used'] is False
    assert result['provider'] is None and result['model'] is None
    assert result['status'] == 'provider_error' and result['retry_after'] == 300
    assert 'Local template shown' in result['message'] and 'Credential rejected' in result['message']
    assert len(result['proposal']) >= 30
    assert 'synthetic-gemini-secret' not in repr(result)
    assert 'private project' not in repr(result)
    remote.assert_called_once()
    other.assert_not_called()


def test_cooldown_explains_paused_generation_and_remaining_seconds(monkeypatch, user, credentials):
    clock = [1000.0]
    monkeypatch.setattr(ai.time, 'monotonic', lambda: clock[0])
    remote = Mock(side_effect=[ai.AIProviderError('rate limited', failure_kind='quota_or_rate_limit', retry_after=300), DRAFT])
    monkeypatch.setattr(ai, '_call_gemini', remote)
    first = ai.ai_proposal_result(JOB, user)
    clock[0] += 12.2
    second = ai.ai_proposal_result(JOB, user)
    assert first['status'] == 'provider_error'
    assert second['status'] == 'provider_cooldown'
    assert second['retry_after'] == 288
    assert second['attempts'][0]['failure_kind'] == 'quota_or_rate_limit'
    assert 'Rate limit or quota' in second['message']
    remote.assert_called_once()
    clock[0] += 288
    recovered = ai.ai_proposal_result(JOB, user)
    assert recovered['source'] == 'ai' and recovered['proposal'] == DRAFT
    assert remote.call_count == 2


def test_concurrent_request_is_busy_not_reported_as_success(monkeypatch, user, credentials):
    fingerprint = ai._reserve_provider('gemini', credentials['gemini_key'], credentials['gemini_model'])
    remote = Mock()
    monkeypatch.setattr(ai, '_call_gemini', remote)
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'provider_busy'
    assert result['source'] == 'local_template' and result['ai_used'] is False
    assert result['retry_after'] is None
    assert 'Wait for the current request' in result['message']
    assert ai._AI_CIRCUITS[fingerprint]['in_flight'] is True
    remote.assert_not_called()
    ai._release_provider(fingerprint)


@pytest.mark.parametrize('preference', ['gemini', 'openai', 'auto'])
def test_missing_key_is_distinct_from_provider_failure(monkeypatch, user, credentials, preference):
    credentials.update(preferred_ai=preference, gemini_key='', openai_key='')
    remote = Mock()
    monkeypatch.setattr(ai, '_call_gemini', remote)
    monkeypatch.setattr(ai, '_call_openai', remote)
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'missing_credentials'
    assert result['source'] == 'local_template'
    assert result['attempts'] == [] and result['retry_after'] is None
    remote.assert_not_called()


def test_builtin_preference_never_claims_to_be_live_ai(monkeypatch, user, credentials):
    credentials['preferred_ai'] = 'smart_builtin'
    remote = Mock()
    monkeypatch.setattr(ai, '_call_gemini', remote)
    monkeypatch.setattr(ai, '_call_openai', remote)
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'builtin_selected' and result['source'] == 'local_template'
    assert result['ai_used'] is False and result['attempts'] == []
    remote.assert_not_called()


def test_explicit_ai_click_uses_provider_even_with_saved_template(monkeypatch, user, credentials):
    user.advanced_config = json.dumps({'default_template': 'Hello {client_name}, I can discuss your {project_title}. Regards, {signature}.'})
    call = Mock(return_value=DRAFT)
    monkeypatch.setattr(ai, '_call_gemini', call)
    result = ai.ai_proposal_result(JOB, user)
    assert result['proposal'] == DRAFT
    assert result['source'] == 'ai' and result['status'] == 'generated'
    assert result['ai_used'] is True and result['provider'] == 'gemini'
    call.assert_called_once()


def test_provider_failure_returns_saved_template_with_honest_failure(monkeypatch, user, credentials):
    user.advanced_config = json.dumps({'default_template': 'Hello {client_name}, I can discuss your {project_title}. Regards, {signature}.'})
    call = Mock(side_effect=ai.AIProviderError('private response', failure_kind='quota_or_rate_limit'))
    monkeypatch.setattr(ai, '_call_gemini', call)
    result = ai.ai_proposal_result(JOB, user)
    assert result['proposal'] == 'Hello Alex, I can discuss your Python dashboard. Regards, Tester.'
    assert result['source'] == 'saved_template' and result['status'] == 'provider_error'
    assert result['ai_used'] is False and result['provider'] is None
    assert 'Saved template shown' in result['message']
    assert 'private response' not in repr(result)
    call.assert_called_once()


def test_legacy_worker_preserves_template_first_preference(monkeypatch, user):
    user.advanced_config = json.dumps({'default_template': 'Hello {client_name}, I can discuss your {project_title}. Regards, {signature}.'})
    call = Mock()
    monkeypatch.setattr(ai, '_call_llm', call)
    assert ai.ai_proposal(JOB, user) == 'Hello Alex, I can discuss your Python dashboard. Regards, Tester.'
    call.assert_not_called()


def test_invalid_template_is_not_implicitly_bypassed(monkeypatch, user):
    user.advanced_config = json.dumps({'default_template': 'This template has an unsupported {unknown_secret} placeholder.'})
    call = Mock()
    monkeypatch.setattr(ai, '_call_llm_result', call)
    with pytest.raises(AdvancedConfigError):
        ai.ai_proposal_result(JOB, user)
    call.assert_not_called()


@pytest.mark.parametrize('reply', ['', 'Short', '\ud800' * 40, 'Invalid NUL: \x00' * 4])
def test_invalid_or_unsavable_proposal_is_not_labeled_success(monkeypatch, user, credentials, reply):
    monkeypatch.setattr(ai, '_call_gemini', Mock(return_value=reply))
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'provider_error' and result['ai_used'] is False
    assert result['attempts'][0]['failure_kind'] == 'invalid_response'
    assert 30 <= len(result['proposal']) <= 3500
    assert result['proposal'].encode('utf-8')


def test_unexpected_transport_error_never_exposes_request_data(monkeypatch, user, credentials):
    remote = Mock(side_effect=RuntimeError('Bearer synthetic-gemini-secret private prompt'))
    monkeypatch.setattr(ai, '_call_gemini', remote)
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'provider_error' and result['source'] == 'local_template'
    assert 'synthetic-gemini-secret' not in repr(result)
    assert 'private prompt' not in repr(result)
    again = ai.ai_proposal_result(JOB, user)
    assert again['status'] == 'provider_cooldown'
    remote.assert_called_once()


def test_long_ai_proposal_stays_within_save_and_bid_limits(monkeypatch, user, credentials):
    monkeypatch.setattr(ai, '_call_gemini', Mock(return_value='A complete proposal. ' * 300))
    result = ai.ai_proposal_result(JOB, user)
    assert result['ai_used'] is True and len(result['proposal']) == 2800


def test_legacy_proposal_keeps_string_return_and_patchable_llm(monkeypatch, user):
    call = Mock(return_value=DRAFT)
    monkeypatch.setattr(ai, '_call_llm', call)
    assert ai.ai_proposal(JOB, user) == DRAFT
    call.assert_called_once()


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_provider_truncation_has_a_specific_safe_status(monkeypatch, user, credentials, provider):
    credentials['preferred_ai'] = provider
    response = ({'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': DRAFT}]}}]}
                if provider == 'gemini' else
                {'choices': [{'finish_reason': 'length', 'message': {'content': DRAFT}}]})
    monkeypatch.setattr(ai, '_post_json', Mock(return_value=response))
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'provider_error'
    assert result['attempts'][0]['failure_kind'] == 'incomplete_response'
    assert 'did not finish' in result['message']


def test_timeout_classification_does_not_trigger_a_same_provider_retry(monkeypatch, user, credentials):
    # Exercise request translation as well as the interactive fallback contract.
    request = Mock(side_effect=httpx.ReadTimeout('synthetic-gemini-secret'))
    monkeypatch.setattr(httpx.Client, 'stream', request)
    monkeypatch.setattr(ai, '_post_json', lambda url, headers, payload, provider: ai._request_json('POST', url, headers, payload, provider))
    result = ai.ai_proposal_result(JOB, user)
    assert result['status'] == 'provider_error'
    assert result['attempts'][0]['failure_kind'] == 'timeout'
    assert 'may still have processed' in result['message']
    assert 'synthetic-gemini-secret' not in repr(result)
    request.assert_called_once()


def response_payload(text=DRAFT):
    return {'status': 'completed', 'output': [
        {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'Private reasoning never shown'}]},
        {'type': 'message', 'role': 'assistant', 'status': 'completed',
         'content': [{'type': 'output_text', 'text': text}]},
    ]}


@pytest.mark.parametrize('model', ['gpt-5-codex', 'gpt-5.2-codex-2026-01-01', 'codex-mini-latest',
                                    'gpt-5.4-pro', 'gpt-5-pro-2025-10-06', 'o1-pro', 'o3-deep-research',
                                    'o4-mini-deep-research'])
def test_official_responses_families_use_one_bounded_stateless_request(monkeypatch, model):
    post = Mock(return_value=response_payload())
    monkeypatch.setattr(ai, '_post_json', post)
    assert ai._call_openai('synthetic-secret', model, 'quoted project', 400) == DRAFT
    post.assert_called_once()
    url, headers, payload, provider = post.call_args.args
    assert url == 'https://api.openai.com/v1/responses' and provider == 'OpenAI'
    assert headers['Authorization'] == 'Bearer synthetic-secret'
    assert payload == {'model': model, 'instructions': ai.AI_SAFETY_RULES,
                       'input': 'quoted project', 'max_output_tokens': 400, 'store': False}


@pytest.mark.parametrize('mode,base,model,endpoint', [
    ('auto', 'https://provider.example/v1', 'gpt-5-codex', 'chat/completions'),
    ('auto', 'https://api.openai.com/v1', 'gpt-4.1-mini', 'chat/completions'),
    ('chat_completions', 'https://api.openai.com/v1', 'gpt-5-pro', 'chat/completions'),
    ('responses', 'https://provider.example/v1', 'future/text-model', 'responses'),
    ('responses', 'https://api.openai.com/v1', 'future-text-model', 'responses'),
])
def test_operator_protocol_override_and_third_party_compatibility(monkeypatch, mode, base, model, endpoint):
    monkeypatch.setenv('OPENAI_API_MODE', mode)
    monkeypatch.setenv('OPENAI_BASE_URL', base)
    reply = response_payload() if endpoint == 'responses' else {'choices': [{'finish_reason': 'stop', 'message': {'content': DRAFT}}]}
    post = Mock(return_value=reply)
    monkeypatch.setattr(ai, '_post_json', post)
    assert ai._call_openai('synthetic-secret', model, 'prompt', 9000, system_prompt='Custom instructions') == DRAFT
    assert post.call_args.args[0] == base + '/' + endpoint
    payload = post.call_args.args[2]
    if endpoint == 'responses':
        assert payload['max_output_tokens'] == 4096 and payload['instructions'] == 'Custom instructions'
    else:
        assert payload['messages'][0]['content'] == 'Custom instructions'
    post.assert_called_once()


@pytest.mark.parametrize('payload,kind', [
    ({'status': 'incomplete', 'output': []}, 'incomplete_response'),
    ({'status': 'in_progress', 'output': []}, 'incomplete_response'),
    ({'status': 'failed', 'error': {'message': 'private provider data'}, 'output': []}, 'incomplete_response'),
    ({'status': 'completed', 'output': []}, 'invalid_response'),
    ({'status': 'completed', 'output': {}}, 'invalid_response'),
    ({'status': 'completed', 'output': [{'type': 'function_call', 'arguments': 'private data'}]}, 'invalid_response'),
    ({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'status': 'incomplete', 'content': []}]}, 'incomplete_response'),
    ({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'refusal', 'refusal': 'private data'}]}]}, 'invalid_response'),
    (response_payload('invalid\x00output'), 'invalid_response'),
    (response_payload('invalid\ud800output'), 'invalid_response'),
])
def test_responses_rejects_incomplete_refused_or_unsafe_output_without_retry(monkeypatch, payload, kind):
    post = Mock(return_value=payload)
    monkeypatch.setattr(ai, '_post_json', post)
    with pytest.raises(ai.AIProviderError) as failure:
        ai._call_openai('synthetic-secret', 'gpt-5-pro', 'private prompt')
    assert failure.value.failure_kind == kind
    assert 'private' not in str(failure.value) and 'synthetic-secret' not in str(failure.value)
    post.assert_called_once()


def test_responses_reads_all_assistant_text_after_reasoning(monkeypatch):
    payload = response_payload('First paragraph.')
    payload['output'].append({'type': 'message', 'role': 'assistant', 'status': 'completed',
                              'content': [{'type': 'output_text', 'text': 'Second paragraph.'}]})
    monkeypatch.setattr(ai, '_post_json', Mock(return_value=payload))
    assert ai._call_openai('synthetic-secret', 'gpt-5-pro', 'prompt') == 'First paragraph.\nSecond paragraph.'


def test_protocol_change_has_separate_circuit_and_invalid_mode_makes_no_request(monkeypatch):
    key, model = 'synthetic-secret', 'gpt-5-pro'
    monkeypatch.setenv('OPENAI_API_MODE', 'responses')
    first = ai._provider_fingerprint('openai', key, model)
    monkeypatch.setenv('OPENAI_API_MODE', 'chat_completions')
    assert ai._provider_fingerprint('openai', key, model) != first
    monkeypatch.setenv('OPENAI_API_MODE', 'unknown-protocol')
    with pytest.raises(ai.AIProviderError, match='OPENAI_API_MODE'):
        ai._call_openai(key, model, 'prompt')
    ai._post_json.assert_not_called()
