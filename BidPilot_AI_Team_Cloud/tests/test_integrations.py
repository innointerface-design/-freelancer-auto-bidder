"""Credential, protocol and malformed-upstream regressions; never makes network calls."""
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app import integrations as ai
from app.freelancer import FreelancerAPI, FreelancerError, normalize_project, unwrap_user

REAL_CLIENT = httpx.Client


def mock_client(handler):
    return patch('httpx.Client', side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs))


def credentials(**overrides):
    data = dict(preferred_ai='auto', gemini_key='test-gemini-secret', gemini_model='gemini-2.5-flash',
                openai_key='test-openai-secret', openai_model='gpt-4.1-mini')
    data.update(overrides)
    return data


class ProviderTests(unittest.TestCase):
    def setUp(self):
        with ai._AI_CIRCUIT_LOCK:
            ai._AI_CIRCUITS.clear()

    def test_gemini_credential_is_header_only_and_thought_not_displayed(self):
        def handler(request):
            self.assertEqual(request.headers['x-goog-api-key'], 'dummy-secret')
            self.assertNotIn('dummy-secret', str(request.url))
            self.assertFalse(request.url.query)
            body = json.loads(request.content)
            self.assertIn('untrusted quoted data', body['systemInstruction']['parts'][0]['text'])
            self.assertIn('Never agree to payments', body['systemInstruction']['parts'][0]['text'])
            self.assertEqual(body['generationConfig']['thinkingConfig']['thinkingBudget'], 0)
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [
                {'text': 'private reasoning', 'thought': True}, {'text': 'Connected'}]}}]})
        with mock_client(handler), patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'api_key'}):
            self.assertEqual(ai._call_gemini('dummy-secret', 'gemini-2.5-flash', 'test'), 'Connected')

    def test_gemini_oauth_must_be_explicit(self):
        def handler(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer access-token')
            self.assertNotIn('x-goog-api-key', request.headers)
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]})
        with mock_client(handler), patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'oauth'}):
            self.assertEqual(ai._call_gemini('access-token', 'gemini-2.5-flash', 'test'), 'OK')

    def test_model_cannot_change_gemini_request_destination(self):
        with mock_client(lambda _: self.fail('Invalid model reached network')):
            for model in ('../../users', 'abc?key=stolen', 'abc#path', 'https://evil.example'):
                with self.assertRaises(ai.AIProviderError):
                    ai._call_gemini('secret', model, 'test')

    def test_provider_http_errors_never_reflect_credentials_or_remote_bodies(self):
        def handler(request):
            return httpx.Response(401, json={'error': {'message': 'key=' + credentials()['gemini_key']}})
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()):
            diagnostic = ai.test_ai_connection(None, 'gemini')['gemini']
            self.assertFalse(diagnostic['ok'])
            self.assertIn('401', diagnostic['message'])
            self.assertNotIn(credentials()['gemini_key'], str(diagnostic))
            self.assertNotIn('https://', str(diagnostic))

    def test_invalid_json_is_safe_diagnostic(self):
        with mock_client(lambda _: httpx.Response(200, text='not JSON')):
            with self.assertRaisesRegex(ai.AIProviderError, 'invalid JSON'):
                ai._call_openai('secret', 'gpt-4.1-mini', 'test')

    def test_malformed_gemini_content_produces_controlled_error(self):
        with mock_client(lambda _: httpx.Response(200, json={'candidates': [{'content': 'bad shape'}]})):
            with self.assertRaisesRegex(ai.AIProviderError, 'no text'):
                ai._call_gemini('secret', 'gemini-2.5-flash', 'test')

    def test_openai_request_and_response(self):
        def handler(request):
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer dummy-secret')
            payload = json.loads(request.content)
            self.assertIn('max_completion_tokens', payload)
            self.assertNotIn('temperature', payload)
            self.assertEqual(payload['messages'][0]['content'], ai.AI_SAFETY_RULES)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Connected'}}]})
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://api.openai.com/v1'}, clear=False), mock_client(handler):
            self.assertEqual(ai._call_openai('dummy-secret', 'gpt-4.1-mini', 'test'), 'Connected')

    def test_explicit_server_proxy_supports_compatible_token_parameter(self):
        def handler(request):
            self.assertEqual(str(request.url), 'https://provider.example/v1/chat/completions')
            self.assertIn('max_tokens', json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}]})
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://provider.example/v1'}), mock_client(handler):
            self.assertEqual(ai._call_openai('provider-issued-key', 'provider/model', 'test'), 'OK')

    def test_private_or_credential_bearing_proxy_urls_are_rejected(self):
        for url in ('http://api.example/v1', 'https://127.0.0.1/v1', 'https://[::1]/v1',
                    'https://localhost/v1', 'https://secret@api.example/v1', 'https://api.example/v1?key=secret'):
            with patch.dict(os.environ, {'OPENAI_BASE_URL': url}), self.assertRaises(ai.AIProviderError):
                ai._openai_base_url()

    def test_no_provider_redirect_is_followed(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={'Location': 'https://evil.example/steal'})
        with mock_client(handler), self.assertRaises(ai.AIProviderError):
            ai._call_gemini('secret', 'gemini-2.5-flash', 'test')
        self.assertEqual(len(calls), 1)

    def test_explicit_provider_does_not_send_prompt_to_another_provider(self):
        with patch.object(ai, 'resolve_ai_credentials', return_value=credentials(preferred_ai='gemini')), \
             patch.object(ai, '_call_gemini', side_effect=ai.AIProviderError('offline')), \
             patch.object(ai, '_call_openai') as other:
            self.assertIsNone(ai._call_llm('private project'))
            other.assert_not_called()

    def test_auto_can_fall_back_to_second_provider(self):
        with patch.object(ai, 'resolve_ai_credentials', return_value=credentials()), \
             patch.object(ai, '_call_gemini', side_effect=ai.AIProviderError('offline')), \
             patch.object(ai, '_call_openai', return_value='fallback'):
            self.assertEqual(ai._call_llm('project'), 'fallback')

    def test_resolver_uses_own_then_server_keys_never_another_user(self):
        db_stub = SimpleNamespace(decrypt_secret=lambda value: {'mine': 'my-secret'}.get(value, ''))
        user = SimpleNamespace(gemini_key_cipher='mine', openai_key_cipher='', preferred_ai='auto',
                               gemini_model='my-private-model', openai_model='expensive-unapproved-model')
        with patch.dict(sys.modules, {'app.db': db_stub}), patch.dict(os.environ, {'GEMINI_API_KEY': 'server-gemini', 'OPENAI_API_KEY': 'server-openai', 'OPENAI_MODEL': 'server-model'}):
            resolved = ai.resolve_ai_credentials(user)
            self.assertEqual(resolved['gemini_key'], 'my-secret')
            self.assertEqual(resolved['openai_key'], 'server-openai')
            self.assertEqual(resolved['gemini_model'], 'my-private-model')
            self.assertEqual(resolved['openai_model'], 'server-model')

    def test_failed_provider_is_called_once_across_a_hundred_worker_proposals(self):
        with patch.object(ai, 'resolve_ai_credentials', return_value=credentials(preferred_ai='gemini')), \
             patch.object(ai, '_call_gemini', side_effect=ai.AIProviderError('expired', retry_after=300)) as provider:
            for _ in range(100):
                self.assertIsNone(ai._call_llm('project draft'))
            self.assertEqual(provider.call_count, 1)
            self.assertNotIn(credentials()['gemini_key'], repr(ai._AI_CIRCUITS))

    def test_circuit_reopens_after_cooldown_and_fresh_key_bypasses_old_failure(self):
        clock = [1000.0]
        configuration = credentials(preferred_ai='gemini')
        with patch.object(ai, 'resolve_ai_credentials', side_effect=lambda _: configuration.copy()), \
             patch.object(ai.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(ai, '_call_gemini', side_effect=[ai.AIProviderError('expired', retry_after=300), 'fresh-key reply', 'recovered reply']) as provider:
            self.assertIsNone(ai._call_llm('draft'))
            self.assertIsNone(ai._call_llm('draft'))
            old_key = configuration['gemini_key']
            configuration['gemini_key'] = 'replacement-secret'
            self.assertEqual(ai._call_llm('draft'), 'fresh-key reply')
            configuration['gemini_key'] = old_key
            clock[0] += 301
            self.assertEqual(ai._call_llm('draft'), 'recovered reply')
            self.assertEqual(provider.call_count, 3)

    def test_inflight_reservation_prevents_concurrent_provider_stampede(self):
        fingerprint = ai._reserve_provider('gemini', 'dummy-secret', 'gemini-2.5-flash')
        self.assertIsNotNone(fingerprint)
        self.assertIsNone(ai._reserve_provider('gemini', 'dummy-secret', 'gemini-2.5-flash'))
        ai._release_provider(fingerprint)
        self.assertIsNotNone(ai._reserve_provider('gemini', 'dummy-secret', 'gemini-2.5-flash'))

    def test_neutral_local_chat_and_local_rules_label(self):
        user = SimpleNamespace(name='Tester', skills='python', portfolio='', countries='US',
                               excluded_keywords='', chat_greeting_template='')
        job = {'title': 'Python project', 'description': 'Build Python software', 'skills': ['Python']}
        with patch.object(ai, '_call_llm', return_value=None):
            reply = ai.ai_chat_reply('Client', 'Python project', 'Pay the fee now', user)
            review = ai.ai_review(job, user)
        self.assertNotIn('online right now', reply)
        self.assertNotIn('get started immediately', reply)
        self.assertIn('Local rules review', review)
        self.assertNotIn('AI Strategy Analysis', review)

    def test_successful_diagnostic_clears_failed_generation_cooldown(self):
        configuration = credentials(preferred_ai='gemini')
        with patch.object(ai, 'resolve_ai_credentials', return_value=configuration), \
             patch.object(ai, '_call_gemini', side_effect=[ai.AIProviderError('expired', retry_after=300), 'Connected', 'actual proposal']) as provider:
            self.assertIsNone(ai._call_llm('draft'))
            self.assertIsNone(ai._call_llm('draft while blocked'))
            self.assertTrue(ai.test_ai_connection(None, 'gemini')['gemini']['ok'])
            self.assertEqual(ai._call_llm('draft after repair'), 'actual proposal')
            self.assertEqual(provider.call_count, 3)

    def test_diagnostic_does_not_unlock_a_generation_request_in_flight(self):
        configuration = credentials(preferred_ai='gemini')
        fingerprint = ai._reserve_provider('gemini', configuration['gemini_key'], configuration['gemini_model'])
        with patch.object(ai, 'resolve_ai_credentials', return_value=configuration), \
             patch.object(ai, '_call_gemini', return_value='Connected'):
            self.assertTrue(ai.test_ai_connection(None, 'gemini')['gemini']['ok'])
        self.assertTrue(ai._AI_CIRCUITS[fingerprint]['in_flight'])
        ai._release_provider(fingerprint)

    def test_token_configuration_change_bypasses_old_configuration_failure(self):
        configuration = credentials(preferred_ai='openai')
        with patch.object(ai, 'resolve_ai_credentials', return_value=configuration), \
             patch.object(ai, '_call_openai', side_effect=[ai.AIProviderError('invalid token parameter'), 'recovered']) as provider:
            with patch.dict(os.environ, {'OPENAI_TOKEN_PARAMETER': 'broken'}):
                self.assertIsNone(ai._call_llm('draft'))
            with patch.dict(os.environ, {'OPENAI_TOKEN_PARAMETER': 'max_completion_tokens'}):
                self.assertEqual(ai._call_llm('draft'), 'recovered')
            self.assertEqual(provider.call_count, 2)

    def test_incomplete_or_filtered_provider_drafts_are_not_treated_as_success(self):
        for reason in ('MAX_TOKENS', 'SAFETY', 'RECITATION'):
            with mock_client(lambda _: httpx.Response(200, json={'candidates': [{'finishReason': reason, 'content': {'parts': [{'text': 'Incomplete project proposal'}]}}]})):
                with self.assertRaisesRegex(ai.AIProviderError, 'did not complete'):
                    ai._call_gemini('dummy-secret', 'gemini-2.5-flash', 'test')
        for reason in ('length', 'content_filter', 'tool_calls'):
            with mock_client(lambda _: httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': {'content': 'Incomplete project proposal'}}]})):
                with self.assertRaisesRegex(ai.AIProviderError, 'did not complete'):
                    ai._call_openai('dummy-secret', 'gpt-4.1-mini', 'test')

    def test_success_diagnostics_do_not_echo_arbitrary_provider_content(self):
        configuration = credentials(preferred_ai='gemini')
        with patch.object(ai, 'resolve_ai_credentials', return_value=configuration), \
             patch.object(ai, '_call_gemini', return_value=configuration['gemini_key']):
            result = ai.test_ai_connection(None, 'gemini')
        self.assertTrue(result['gemini']['ok'])
        self.assertNotIn(configuration['gemini_key'], str(result))


class StructuredProviderDiagnosticTests(unittest.TestCase):
    def diagnostic(self, provider, status, payload=None, content=None):
        def handler(request):
            return httpx.Response(status, json=payload) if content is None else httpx.Response(status, content=content)
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()):
            return ai.test_ai_connection(None, provider)[provider]

    def test_google_errorinfo_codes_have_fixed_safe_hints(self):
        cases = {
            'API_KEY_NOT_FOUND': 'credential_rejected', 'API_KEY_INVALID': 'credential_rejected',
            'API_KEY_EXPIRED': 'credential_rejected', 'SERVICE_DISABLED': 'service_disabled',
            'API_KEY_SERVICE_BLOCKED': 'credential_restriction', 'API_KEY_HTTP_REFERRER_BLOCKED': 'credential_restriction',
            'API_KEY_IP_ADDRESS_BLOCKED': 'credential_restriction', 'CONSUMER_INVALID': 'project_configuration',
            'PROJECT_NOT_FOUND': 'project_configuration', 'BILLING_DISABLED': 'billing_configuration',
            'ACCESS_TOKEN_SCOPE_INSUFFICIENT': 'oauth_scope',
        }
        for reason, kind in cases.items():
            result = self.diagnostic('gemini', 403, {'error': {
                'status': 'PERMISSION_DENIED', 'message': 'secret-key https://private.example/?key=secret-key',
                'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': reason,
                             'metadata': {'key': 'secret-key', 'consumer': 'private-project-123'}}],
            }})
            self.assertEqual(result['provider_status'], 'PERMISSION_DENIED')
            self.assertEqual(result['provider_reason'], reason)
            self.assertEqual(result['failure_kind'], kind)
            self.assertEqual(result['response_format'], 'json')
            for private in ('secret-key', 'private.example', 'private-project-123'):
                self.assertNotIn(private, str(result))

    def test_unknown_status_reason_and_metadata_are_never_echoed(self):
        result = self.diagnostic('gemini', 403, {'error': {
            'status': 'secret-api-key', 'message': 'Go to https://evil.example/secret-api-key',
            'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'API_KEY_INVALID:secret-api-key',
                         'metadata': {'reason': 'secret-api-key'}}],
        }})
        self.assertNotIn('provider_status', result)
        self.assertNotIn('provider_reason', result)
        self.assertEqual(result['failure_kind'], 'access_denied')
        self.assertNotIn('secret-api-key', str(result))
        self.assertNotIn('evil.example', str(result))

    def test_remote_message_classification_returns_only_predefined_label_and_hint(self):
        result = self.diagnostic('gemini', 400, {'error': {'message':
            'API key not valid: secret-api-key, prompt=private prompt, endpoint=https://private.example'}})
        self.assertEqual(result['failure_kind'], 'credential_rejected')
        for private in ('secret-api-key', 'private prompt', 'private.example'):
            self.assertNotIn(private, str(result))

    def test_openai_codes_types_are_allowlisted_and_401_remains_credential_rejection(self):
        result = self.diagnostic('openai', 401, {'error': {
            'type': 'authentication_error', 'code': 'invalid_api_key',
            'message': 'Incorrect API key: test-openai-secret https://private.example', 'param': 'private input',
        }})
        self.assertEqual(result['status_code'], 401)
        self.assertEqual(result['provider_status'], 'authentication_error')
        self.assertEqual(result['provider_reason'], 'invalid_api_key')
        self.assertEqual(result['failure_kind'], 'credential_rejected')
        for private in ('test-openai-secret', 'private.example', 'private input'):
            self.assertNotIn(private, str(result))
        unknown = self.diagnostic('openai', 401, {'error': {'code': 'secret-key', 'type': 'private_type'}})
        self.assertNotIn('provider_reason', unknown)
        self.assertNotIn('provider_status', unknown)
        self.assertEqual(unknown['failure_kind'], 'credential_rejected')

    def test_non_json_error_body_is_classified_without_reflection(self):
        result = self.diagnostic('gemini', 403, content=b'<html>secret-key https://private.example</html>')
        self.assertEqual(result['status_code'], 403)
        self.assertEqual(result['response_format'], 'non_json')
        self.assertEqual(result['failure_kind'], 'access_denied')
        self.assertNotIn('secret-key', str(result))
        self.assertNotIn('private.example', str(result))

    def test_oversized_error_body_is_discarded_and_original_status_preserved(self):
        body = json.dumps({'error': {'status': 'PERMISSION_DENIED', 'message': 'secret-key ' + 'x' * 70000}}).encode()
        result = self.diagnostic('gemini', 403, content=body)
        self.assertEqual(result['status_code'], 403)
        self.assertEqual(result['response_format'], 'too_large')
        self.assertNotIn('provider_status', result)
        self.assertNotIn('secret-key', str(result))

    def test_selected_model_in_catalog_does_not_turn_404_into_model_missing_claim(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == 'POST':
                return httpx.Response(404, json={'error': {'status': 'NOT_FOUND', 'message': 'secret-key requested entity not found'}})
            return httpx.Response(200, json={'models': [{'name': 'models/gemini-2.5-flash', 'supportedGenerationMethods': ['generateContent']}]})
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()):
            result = ai.test_ai_connection(None, 'gemini')['gemini']
        self.assertEqual(result['status_code'], 404)
        self.assertEqual(result['provider_status'], 'NOT_FOUND')
        self.assertEqual(result['failure_kind'], 'generation_access_unconfirmed')
        self.assertTrue(result['selected_model_listed'])
        self.assertFalse(result['ok'])
        self.assertIn('listing does not prove generation access', result['message'])
        self.assertIn('cause is not yet confirmed', result['message'])
        self.assertNotIn('Choose', result['message'])
        self.assertNotIn('Select one', result['model_discovery_message'])
        self.assertNotIn('secret-key', str(result))
        self.assertEqual([request.method for request in requests], ['POST', 'GET'])

    def test_google_key_not_found_reason_on_404_is_not_recast_as_missing_model(self):
        def handler(request):
            if request.method == 'POST':
                return httpx.Response(404, json={'error': {'status': 'NOT_FOUND', 'details': [{
                    '@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'API_KEY_NOT_FOUND',
                }]}})
            return httpx.Response(200, json={'models': [{'name': 'models/gemini-2.5-flash', 'supportedGenerationMethods': ['generateContent']}]})
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()):
            result = ai.test_ai_connection(None, 'gemini')['gemini']
        self.assertEqual(result['status_code'], 404)
        self.assertEqual(result['provider_reason'], 'API_KEY_NOT_FOUND')
        self.assertEqual(result['failure_kind'], 'credential_rejected')
        self.assertTrue(result['selected_model_listed'])
        self.assertIn('not evidence that the selected model is missing', result['message'])
        self.assertNotIn('Choose', result['message'])


class OpenAICredentialIdentifierHintTests(unittest.TestCase):
    def run_diagnostic(self, key, base, status=401, reason='invalid_api_key'):
        configuration = credentials(openai_key=key)
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + key)
            if status == 200:
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'Connected'}}]})
            return httpx.Response(status, json={'error': {'type': 'authentication_error', 'code': reason,
                                                         'message': 'Rejected ' + key + ' at https://private.example'}})
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=configuration), \
             patch.dict(os.environ, {'OPENAI_BASE_URL': base}):
            result = ai.test_ai_connection(None, 'openai')['openai']
        self.assertEqual(len(requests), 1)
        self.assertNotIn(key, str(result))
        self.assertNotIn('private.example', str(result))
        return result

    def test_official_401_invalid_key_with_key_identifier_shape_gets_contextual_hint(self):
        for reason in ('invalid_api_key', 'incorrect_api_key'):
            result = self.run_diagnostic('key_dummy_record_id', 'https://api.openai.com/v1', reason=reason)
            self.assertEqual(result['credential_hint'], 'possible_key_identifier')
            self.assertEqual(result['status_code'], 401)
            self.assertEqual(result['failure_kind'], 'credential_rejected')
            self.assertIn('may be an API key record ID', result['message'])
            self.assertIn('full secret value', result['message'])

    def test_key_identifier_prefix_is_not_rejected_when_provider_accepts_it(self):
        result = self.run_diagnostic('key_accepted_by_provider', 'https://api.openai.com/v1', status=200)
        self.assertTrue(result['ok'])
        self.assertNotIn('credential_hint', result)

    def test_custom_provider_401_does_not_get_official_identifier_hint(self):
        result = self.run_diagnostic('key_custom_provider_secret', 'https://compatible-provider.example/v1')
        self.assertEqual(result['status_code'], 401)
        self.assertNotIn('credential_hint', result)
        self.assertNotIn('record ID', result['message'])

    def test_sk_project_key_rejection_does_not_get_identifier_hint(self):
        result = self.run_diagnostic('sk-proj-dummy-secret', 'https://api.openai.com/v1')
        self.assertEqual(result['status_code'], 401)
        self.assertNotIn('credential_hint', result)
        self.assertNotIn('record ID', result['message'])

    def test_key_identifier_shape_without_explicit_invalid_key_reason_gets_no_hint(self):
        result = self.run_diagnostic('key_dummy_record_id', 'https://api.openai.com/v1', reason='permission_denied')
        self.assertEqual(result['status_code'], 401)
        self.assertNotIn('credential_hint', result)


class GeminiDiscoveryTests(unittest.TestCase):
    @staticmethod
    def model(name, methods=None):
        return {'name': 'models/' + name, 'supportedGenerationMethods': ['generateContent'] if methods is None else methods}

    def test_404_discovers_supported_models_readonly_without_changing_selected_model(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers['x-goog-api-key'], 'test-gemini-secret')
            self.assertNotIn('test-gemini-secret', str(request.url))
            if request.method == 'POST':
                return httpx.Response(404, json={'error': {'message': 'unavailable'}})
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.url.path, '/v1beta/models')
            self.assertEqual(request.url.params['pageSize'], '100')
            return httpx.Response(200, json={'models': [self.model('gemini-supported'), self.model('embedding', ['embedContent']),
                self.model('gemini-supported'), {'name': 'models/invalid/path', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/missing-methods'}, None]})
        with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()), \
             patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'api_key'}):
            result = ai.test_ai_connection(None, 'gemini')['gemini']
        self.assertFalse(result['ok'])
        self.assertEqual(result['status_code'], 404)
        self.assertEqual(result['model'], credentials()['gemini_model'])
        self.assertEqual(result['available_models'], ['gemini-supported'])
        self.assertTrue(result['model_discovery_complete'])
        self.assertIn('not returned in the catalog', result['message'])
        self.assertFalse(result['selected_model_listed'])
        self.assertEqual([r.method for r in requests], ['POST', 'GET'])

    def test_listing_uses_explicit_oauth_and_pages_without_key_in_url(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers['Authorization'], 'Bearer dummy-oauth')
            self.assertNotIn('x-goog-api-key', request.headers)
            self.assertNotIn('dummy-oauth', str(request.url))
            if len(requests) == 1:
                return httpx.Response(200, json={'models': [self.model('first')], 'nextPageToken': 'page two&opaque'})
            self.assertEqual(request.url.params['pageToken'], 'page two&opaque')
            return httpx.Response(200, json={'models': [self.model('second'), self.model('first')]})
        with mock_client(handler), patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'oauth'}):
            result = ai._discover_gemini_models('dummy-oauth')
        self.assertEqual(result['available_models'], ['first', 'second'])
        self.assertTrue(result['model_discovery_complete'])
        self.assertEqual(len(requests), 2)

    def test_listing_stops_after_three_pages_and_marks_partial(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'models': [self.model('page-' + str(len(requests)))], 'nextPageToken': str(len(requests))})
        with mock_client(handler):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertEqual(len(requests), 3)
        self.assertEqual(result['available_models'], ['page-1', 'page-2', 'page-3'])
        self.assertFalse(result['model_discovery_complete'])
        self.assertIn('three pages', result['model_discovery_message'])

    def test_later_page_failure_preserves_partial_models_and_redacts_remote_body(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json={'models': [self.model('known-good')], 'nextPageToken': 'next'})
            return httpx.Response(401, json={'error': {'message': 'dummy-secret credential rejected'}})
        with mock_client(handler):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertEqual(result['available_models'], ['known-good'])
        self.assertFalse(result['model_discovery_complete'])
        self.assertIn('401', result['model_discovery_message'])
        self.assertNotIn('dummy-secret', str(result))
        self.assertNotIn('https://', str(result))

    def test_discovery_redirect_is_not_followed(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={'Location': 'https://evil.example/steal'})
        with mock_client(handler):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertEqual(len(calls), 1)
        self.assertFalse(result['model_discovery_complete'])
        self.assertIn('302', result['model_discovery_message'])
        self.assertNotIn('dummy-secret', str(result))

    def test_malformed_model_lists_and_page_tokens_are_safe_partial_results(self):
        for payload in ({'models': {}}, {'models': None}, {'models': [], 'nextPageToken': {}},
                        {'models': [], 'nextPageToken': False}, {'models': [], 'nextPageToken': 'x' * 4097}):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=payload)
            with mock_client(handler):
                result = ai._discover_gemini_models('dummy-secret')
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['available_models'], [])
            self.assertFalse(result['model_discovery_complete'])
            self.assertIn('invalid model', result['model_discovery_message'])

    def test_repeated_pagination_token_does_not_loop(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'models': [self.model('known-good')], 'nextPageToken': 'same'})
        with mock_client(handler):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['available_models'], ['known-good'])
        self.assertFalse(result['model_discovery_complete'])
        self.assertIn('repeated', result['model_discovery_message'])

    def test_missing_models_can_mean_no_models_and_is_actionable(self):
        with mock_client(lambda _: httpx.Response(200, json={})):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertEqual(result['available_models'], [])
        self.assertTrue(result['model_discovery_complete'])
        self.assertIn('No generateContent models', result['model_discovery_message'])

    def test_other_errors_do_not_trigger_discovery_and_openai_401_stays_explicit(self):
        for provider in ('gemini', 'openai'):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(401, json={'error': {'message': credentials()[provider + '_key']}})
            with mock_client(handler), patch.object(ai, 'resolve_ai_credentials', return_value=credentials()):
                result = ai.test_ai_connection(None, provider)[provider]
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['status_code'], 401)
            self.assertFalse(result['ok'])
            self.assertNotIn('available_models', result)
            self.assertNotIn(credentials()[provider + '_key'], str(result))

    def test_listing_response_size_is_bounded(self):
        with mock_client(lambda _: httpx.Response(200, content=b' ' * 1_000_001)):
            result = ai._discover_gemini_models('dummy-secret')
        self.assertFalse(result['model_discovery_complete'])
        self.assertIn('safe size limit', result['model_discovery_message'])


class FreelancerTests(unittest.TestCase):
    def test_identity_uses_official_oauth_header_and_user_id(self):
        def handler(request):
            self.assertEqual(request.headers['Freelancer-OAuth-V1'], 'dummy-oauth')
            self.assertEqual(request.url.path, '/api/users/0.1/self/')
            return httpx.Response(200, json={'status': 'success', 'result': {'id': 123, 'username': 'tester'}})
        with mock_client(handler):
            self.assertEqual(FreelancerAPI('dummy-oauth').me()['id'], 123)

    def test_direct_bid_response_is_normalized_and_bidder_id_is_numeric(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['bidder_id'], 123)
            self.assertEqual(payload['project_id'], 456)
            self.assertEqual(payload['amount'], 100.0)
            return httpx.Response(200, json={'status': 'success', 'result': {'id': 789, 'bidder_id': 123}})
        with mock_client(handler):
            result = FreelancerAPI('dummy-oauth').place_bid(456, '123', 'A detailed project proposal.', 100, 3)
            self.assertEqual(result['bid']['id'], 789)

    def test_unknown_bid_response_is_uncertain_not_success(self):
        with mock_client(lambda _: httpx.Response(200, json={'status': 'success', 'result': {}})):
            with self.assertRaisesRegex(FreelancerError, 'uncertain'):
                FreelancerAPI('dummy-oauth').place_bid(456, 123, 'A detailed project proposal.', 100, 3)

    def test_nonfinite_bid_rejected_before_network(self):
        with mock_client(lambda _: self.fail('Invalid amount reached API')):
            for amount in (float('nan'), float('inf'), -10):
                with self.assertRaises(FreelancerError):
                    FreelancerAPI('dummy-oauth').place_bid(456, 123, 'A detailed project proposal.', amount, 3)

    def test_rate_limit_safe_and_no_remote_key_echo(self):
        with mock_client(lambda _: httpx.Response(429, text='secret-oauth')):
            with self.assertRaises(FreelancerError) as context:
                FreelancerAPI('secret-oauth').me()
            self.assertIn('rate limit', str(context.exception))
            self.assertNotIn('secret-oauth', str(context.exception))

    def test_freelancer_redirect_does_not_forward_credentials(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(301, headers={'Location': 'https://evil.example'})
        with mock_client(handler), self.assertRaisesRegex(FreelancerError, 'not forwarded'):
            FreelancerAPI('secret-oauth').me()
        self.assertEqual(len(calls), 1)

    def test_bid_listing_filter_is_plural_and_offset_is_preserved(self):
        def handler(request):
            self.assertEqual(request.url.params.get('bidders[]'), '123')
            self.assertNotIn('bidder_id', request.url.params)
            self.assertEqual(request.url.params.get('offset'), '40')
            return httpx.Response(200, json={'status': 'success', 'result': {'bids': []}})
        with mock_client(handler):
            FreelancerAPI('secret-oauth').list_my_bids(123, offset=40)

    def test_live_bids_filter_other_accounts_and_handle_list_project_shapes(self):
        account = SimpleNamespace(freelancer_id='123')
        api = SimpleNamespace(list_my_bids=lambda *args, **kwargs: {
            'bids': [{'id': 1, 'bidder_id': 123, 'project_id': '456'}, {'id': 2, 'bidder_id': 999, 'project_id': 456}],
            'projects': [{'id': 456, 'title': 'Example'}],
        })
        with patch.object(ai, 'get_client', return_value=api):
            bids = ai.fetch_live_bids(None, account)
        self.assertEqual(len(bids), 1)
        self.assertEqual(bids[0]['project_title'], 'Example')

    def test_live_bid_failure_does_not_masquerade_as_empty_result(self):
        account = SimpleNamespace(freelancer_id='123')
        api = SimpleNamespace(list_my_bids=lambda *args, **kwargs: (_ for _ in ()).throw(FreelancerError('expired')))
        with patch.object(ai, 'get_client', return_value=api), self.assertRaisesRegex(FreelancerError, 'expired'):
            ai.fetch_live_bids(None, account)

    def test_user_shapes_and_null_project_strings(self):
        record = {'id': 7, 'username': 'person'}
        for envelope in ({'users': {'7': record}}, {'users': [record]}, {'user': record}, record):
            self.assertEqual(unwrap_user(envelope, 7), record)
        normalized = normalize_project({'id': 9, 'title': None, 'description': None}, sandbox=True)
        self.assertEqual(normalized['title'], 'Untitled project')
        self.assertEqual(normalized['description'], '')
        self.assertTrue(normalized['url'].startswith('https://www.freelancer-sandbox.com/'))

    def test_empty_search_configuration_is_actionable_error_without_network(self):
        user = SimpleNamespace(search_terms='', skills='')
        conn = SimpleNamespace(environment='sandbox')
        api = SimpleNamespace(search=lambda *a, **kw: self.fail('Empty query reached network'))
        with patch.object(ai, 'get_client', return_value=api), self.assertRaisesRegex(FreelancerError, 'search terms'):
            ai.scan_for_user(user, conn)

    def test_malformed_project_or_bid_envelopes_are_errors_not_empty_success(self):
        conn = SimpleNamespace(environment='sandbox', freelancer_id='123')
        user = SimpleNamespace(search_terms='Python', skills='Python')
        for response in ({}, {'error': 'request rejected'}, {'projects': {}}, []):
            api = SimpleNamespace(search=lambda *a, **kw: response)
            with patch.object(ai, 'get_client', return_value=api), self.assertRaisesRegex(FreelancerError, 'search response'):
                ai.scan_for_user(user, conn)
        for response in ({}, {'error': 'request rejected'}, {'bids': {}}, []):
            api = SimpleNamespace(list_my_bids=lambda *a, **kw: response)
            with patch.object(ai, 'get_client', return_value=api), self.assertRaisesRegex(FreelancerError, 'bids response'):
                ai.fetch_live_bids(user, conn)

    def test_project_scan_skips_invalid_ids_that_would_crash_autobid(self):
        conn = SimpleNamespace(environment='sandbox')
        user = SimpleNamespace(search_terms='Python', skills='Python')
        records = [{'id': value, 'title': 'Python project'} for value in (None, 0, -3, 'not-a-number', '9' * 5000, '123')]
        api = SimpleNamespace(search=lambda *a, **kw: {'projects': records})
        with patch.object(ai, 'get_client', return_value=api):
            self.assertEqual([job['id'] for job in ai.scan_for_user(user, conn)], ['123'])

    def test_self_id_must_be_positive_before_token_is_saved(self):
        for user_id in (0, -1, '', None, 'not-numeric', '9' * 5000):
            with mock_client(lambda _: httpx.Response(200, json={'status': 'success', 'result': {'id': user_id}})):
                with self.assertRaisesRegex(FreelancerError, 'Account ID'):
                    FreelancerAPI('dummy-oauth').me()

    def test_unknown_currency_and_pricing_type_are_not_invented(self):
        for raw in ({'id': 1}, {'id': 1, 'currency': None, 'type': None},
                    {'id': 1, 'currency': {'code': 123}, 'type': 123},
                    {'id': 1, 'currency': {'code': 'US'}, 'type': 'unexpected'}):
            normalized = normalize_project(raw)
            self.assertEqual(normalized['currency'], '')
            self.assertEqual(normalized['type'], '')

    def test_official_currency_types_and_owner_id_survive_normalization(self):
        for pricing in ('fixed', 'hourly'):
            normalized = normalize_project({'id': 1, 'currency': {'code': ' usd '},
                                            'type': pricing.upper(), 'owner_id': 456})
            self.assertEqual(normalized['currency'], 'USD')
            self.assertEqual(normalized['type'], pricing)
            self.assertEqual(normalized['owner_id'], 456)


if __name__ == '__main__':
    unittest.main()
