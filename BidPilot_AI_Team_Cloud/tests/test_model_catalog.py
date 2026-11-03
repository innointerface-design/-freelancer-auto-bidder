"""Model discovery regressions; all credentials and provider responses are synthetic."""
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from app import model_catalog as catalog
from app.integrations import AIProviderError


def credentials(**changes):
    values = {'gemini_key': 'synthetic-gemini-secret', 'gemini_model': 'gemini-saved',
              'openai_key': 'synthetic-openai-secret', 'openai_model': 'gpt-saved'}
    values.update(changes)
    return values


def gemini_model(model_id, methods=None):
    return {'name': 'models/' + model_id, 'supportedGenerationMethods': methods or ['generateContent']}


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        with catalog._CACHE_LOCK:
            catalog._CACHE.clear()
        self.environment = patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'api_key',
                                                   'OPENAI_BASE_URL': 'https://api.openai.com/v1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Patch the transport by default: a missed test fixture must never call an API.
        self.transport = patch.object(catalog, '_request_json', side_effect=AssertionError('Unexpected provider call'))
        self.request = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.resolver_patch = patch.object(catalog, 'resolve_ai_credentials', return_value=credentials())
        self.resolve = self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)

    def result(self, provider='openai', *, force=False):
        self.resolve.return_value = credentials(**{('gemini_key' if provider == 'openai' else 'openai_key'): ''})
        return catalog.get_model_catalog(force=force)['providers'][provider]

    def reply(self, value):
        self.request.side_effect = None
        self.request.return_value = value

    def test_future_text_families_are_discovered_without_an_allowlist(self):
        self.reply({'data': [{'id': value} for value in ['gpt-future-12', 'o99-reasoning', 'brand-new-text-family',
                                                        'ft:gpt-future:organisation:custom:v1']]})
        result = self.result()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual({row['id'] for row in result['models']},
                         {'gpt-future-12', 'o99-reasoning', 'brand-new-text-family', 'ft:gpt-future:organisation:custom:v1'})
        self.assertTrue(all(row['name'] == row['id'] and row['provider'] == 'openai' for row in result['models']))
        self.assertFalse(result['generation_verified'])
        self.assertEqual(result['selected_model'], 'gpt-saved')
        args, kwargs = self.request.call_args
        self.assertEqual(args[:2], ('GET', 'https://api.openai.com/v1/models'))
        self.assertIsNone(args[3])
        self.assertEqual(args[2]['Authorization'], 'Bearer synthetic-openai-secret')
        self.assertNotIn('synthetic-openai-secret', args[1])

    def test_obviously_non_text_models_are_filtered(self):
        excluded = ['dall-e-3', 'gpt-image-1', 'text-embedding-3-large', 'omni-moderation-latest',
                    'whisper-1', 'gpt-4o-transcribe', 'gpt-4o-mini-tts', 'gpt-audio', 'gpt-realtime',
                    'sora-2', 'imagen-4', 'veo-3', 'lyria-2']
        self.reply({'data': [{'id': value} for value in excluded + ['gpt-text-future']]})
        result = self.result()
        self.assertEqual([row['id'] for row in result['models']], ['gpt-text-future'])
        self.assertTrue(result['complete'])

    def test_gemini_paginates_supported_models_and_deduplicates(self):
        self.request.side_effect = [
            {'models': [gemini_model('gemini-future-pro'), gemini_model('gemini-embed', ['embedContent']),
                        gemini_model('gemini-future-audio')], 'nextPageToken': 'page-2'},
            {'models': [gemini_model('gemini-future-pro'), gemini_model('gemini-future-flash')]},
        ]
        result = self.result('gemini')
        self.assertEqual([row['id'] for row in result['models']], ['gemini-future-flash', 'gemini-future-pro'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.request.call_args_list[1].kwargs['params'], {'pageSize': 100, 'pageToken': 'page-2'})
        for call in self.request.call_args_list:
            self.assertEqual(call.args[0], 'GET')
            self.assertIsNone(call.args[3])
            self.assertEqual(call.args[2]['x-goog-api-key'], 'synthetic-gemini-secret')
            self.assertNotIn('synthetic-gemini-secret', call.args[1])

    def test_gemini_oauth_uses_bearer_header(self):
        self.reply({'models': [gemini_model('gemini-future')]})
        with patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'oauth'}):
            self.result('gemini')
        headers = self.request.call_args.args[2]
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-gemini-secret')
        self.assertNotIn('x-goog-api-key', headers)

    def test_gemini_partial_page_failure_preserves_discovered_models(self):
        self.request.side_effect = [
            {'models': [gemini_model('gemini-found')], 'nextPageToken': 'page-2'},
            AIProviderError('Sensitive remote response synthetic-gemini-secret'),
        ]
        result = self.result('gemini')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['complete'])
        self.assertEqual(result['models'][0]['id'], 'gemini-found')
        self.assertNotIn('Sensitive', json.dumps(result))
        self.assertNotIn('synthetic-gemini-secret', json.dumps(result))

    def test_gemini_later_failure_is_partial_even_when_earlier_page_has_no_text(self):
        self.request.side_effect = [{'models': [], 'nextPageToken': 'page-2'}, AIProviderError('Failed')]
        self.assertEqual(self.result('gemini')['status'], 'partial')

    def test_gemini_pagination_is_bounded_and_marks_partial(self):
        self.request.side_effect = [
            {'models': [gemini_model('gemini-page-' + str(n))], 'nextPageToken': 'page-' + str(n + 1)}
            for n in range(catalog.MAX_GEMINI_PAGES)
        ]
        result = self.result('gemini')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.request.call_count, catalog.MAX_GEMINI_PAGES)
        self.assertEqual(len(result['models']), catalog.MAX_GEMINI_PAGES)

    def test_gemini_repeated_or_invalid_page_tokens_never_cause_unbounded_calls(self):
        for token in ['repeated', 'bad\nheader', 'bad\x7f', 123, 'x' * 4097]:
            with self.subTest(token=str(token)[:20]):
                with catalog._CACHE_LOCK:
                    catalog._CACHE.clear()
                self.reply({'models': [gemini_model('gemini-future')], 'nextPageToken': token})
                self.request.reset_mock()
                result = self.result('gemini')
                self.assertEqual(result['status'], 'partial')
                self.assertLessEqual(self.request.call_count, 2)

    def test_unsafe_ids_and_remote_names_are_not_reflected(self):
        self.reply({'data': [{'id': 'gpt-safe', 'name': 'synthetic-openai-secret'}, {'id': '<script>alert(1)</script>'},
                             {'id': 'bad\nline'}, {'id': 'gpt-synthetic-openai-secret'}, {'id': '\ud800'},
                             {'id': 'x' * 121}, None]})
        result = self.result()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['models'], [{'id': 'gpt-safe', 'name': 'gpt-safe', 'provider': 'openai'}])
        self.assertNotIn('synthetic-openai-secret', json.dumps(result))
        self.assertNotIn('<script>', json.dumps(result))

    def test_openai_oversize_list_and_has_more_are_partial(self):
        for data in [{'data': [{'id': 'gpt-a'}, {'id': 'gpt-b'}]}, {'data': [{'id': 'gpt-a'}], 'has_more': True}]:
            with self.subTest(data=data), patch.object(catalog, 'MAX_OPENAI_MODELS', 1):
                with catalog._CACHE_LOCK:
                    catalog._CACHE.clear()
                self.reply(data)
                result = self.result()
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(len(result['models']), 1)

    def test_unconfigured_keys_make_no_provider_calls_and_keep_selection(self):
        self.resolve.return_value = credentials(gemini_key='', openai_key='')
        result = catalog.get_model_catalog()
        self.request.assert_not_called()
        for provider in result['providers'].values():
            self.assertEqual(provider['status'], 'unconfigured')
            self.assertFalse(provider['configured'])
            self.assertIsNone(provider['fetched_at'])
            self.assertIsNone(provider['refresh_after'])
            self.assertTrue(provider['selected_model'])

    def test_malformed_lists_and_remote_exceptions_are_safe_errors(self):
        for provider, response in [('openai', {}), ('openai', {'data': 'secret'}),
                                   ('gemini', {}), ('gemini', {'models': 'secret'})]:
            with self.subTest(provider=provider, response=response):
                with catalog._CACHE_LOCK:
                    catalog._CACHE.clear()
                self.reply(response)
                result = self.result(provider)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['complete'])
                self.assertTrue(result['selected_model'])
        with catalog._CACHE_LOCK:
            catalog._CACHE.clear()
        self.request.side_effect = RuntimeError('raw remote secret synthetic-openai-secret')
        result = self.result()
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('synthetic-openai-secret', json.dumps(result))
        self.assertNotIn('raw remote', json.dumps(result))
        self.assertTrue(all(item['event'].is_set() for item in catalog._CACHE.values()))

    def test_empty_success_is_complete_without_fabricated_models(self):
        self.reply({'data': []})
        result = self.result()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['models'], [])
        self.assertEqual(result['selected_model'], 'gpt-saved')
        self.assertIn('No text model', result['message'])

    def test_cache_is_six_hours_and_expiry_finds_a_new_model(self):
        self.reply({'data': [{'id': 'gpt-current'}]})
        with patch.object(catalog.time, 'monotonic', return_value=1000) as clock:
            first = self.result()
            self.assertFalse(first['cached'])
            clock.return_value = 1000 + catalog.REFRESH_INTERVAL_SECONDS - 1
            self.assertTrue(self.result()['cached'])
            self.request.assert_called_once()
            clock.return_value += 2
            self.reply({'data': [{'id': 'gpt-current'}, {'id': 'gpt-future'}]})
            updated = self.result()
            self.assertFalse(updated['cached'])
            self.assertEqual(self.request.call_count, 2)
            self.assertEqual({row['id'] for row in updated['models']}, {'gpt-current', 'gpt-future'})

    def test_manual_refresh_is_throttled_for_one_minute(self):
        self.reply({'data': []})
        with patch.object(catalog.time, 'monotonic', return_value=1000) as clock:
            self.result()
            clock.return_value = 1059
            self.assertTrue(self.result(force=True)['refresh_throttled'])
            self.request.assert_called_once()
            clock.return_value = 1060
            result = self.result(force=True)
            self.assertFalse(result['refresh_throttled'])
            self.assertEqual(self.request.call_count, 2)

    def test_failed_discovery_retries_after_short_error_ttl(self):
        self.request.side_effect = AIProviderError('Remote failure')
        with patch.object(catalog.time, 'monotonic', return_value=1000) as clock:
            self.assertEqual(self.result()['status'], 'error')
            clock.return_value = 1059
            self.assertTrue(self.result()['cached'])
            self.request.assert_called_once()
            clock.return_value = 1061
            self.reply({'data': [{'id': 'gpt-recovered'}]})
            self.assertEqual(self.result()['status'], 'complete')
            self.assertEqual(self.request.call_count, 2)

    def test_failed_refresh_preserves_stale_models_with_explicit_partial_status(self):
        self.reply({'data': [{'id': 'gpt-previous'}]})
        with patch.object(catalog.time, 'monotonic', return_value=1000) as clock:
            first = self.result()
            clock.return_value = 1061
            self.request.side_effect = AIProviderError('Sensitive body')
            result = self.result(force=True)
            self.assertEqual(result['status'], 'partial')
            self.assertTrue(result['stale'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['fetched_at'], first['fetched_at'])
            self.assertEqual(result['models'], first['models'])
            self.assertIn('Previously discovered', result['message'])

    def test_cache_is_scoped_to_credential_and_server_configuration(self):
        self.reply({'data': [{'id': 'gpt-accessible'}], 'models': [gemini_model('gemini-accessible')]})
        self.resolve.return_value = credentials(gemini_key='')
        catalog.get_model_catalog()
        self.resolve.return_value = credentials(gemini_key='', openai_key='different-key')
        catalog.get_model_catalog()
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://alternate.example/v1'}):
            catalog.get_model_catalog()
            self.assertEqual(self.request.call_args.args[1], 'https://alternate.example/v1/models')
        self.assertEqual(self.request.call_count, 3)
        self.resolve.return_value = credentials(openai_key='')
        catalog.get_model_catalog()
        with patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'oauth'}):
            catalog.get_model_catalog()
        self.assertEqual(self.request.call_count, 5)
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in catalog._CACHE))
        self.assertNotIn('synthetic', repr(catalog._CACHE))

    def test_same_key_different_selections_share_catalog_not_saved_model(self):
        self.reply({'data': [{'id': 'gpt-new'}]})
        self.resolve.return_value = credentials(gemini_key='', openai_model='gpt-one')
        first = catalog.get_model_catalog()['providers']['openai']
        self.resolve.return_value = credentials(gemini_key='', openai_model='gpt-two')
        second = catalog.get_model_catalog()['providers']['openai']
        self.assertEqual(first['selected_model'], 'gpt-one')
        self.assertEqual(second['selected_model'], 'gpt-two')
        self.request.assert_called_once()

    def test_caller_mutations_cannot_poison_cached_results(self):
        self.reply({'data': [{'id': 'gpt-safe'}]})
        result = self.result()
        result['models'][0]['id'] = 'poisoned'
        result['models'].append({'id': 'extra'})
        self.assertEqual([row['id'] for row in self.result()['models']], ['gpt-safe'])

    def test_concurrent_requests_use_one_remote_fetch(self):
        started, release, waiter_started = threading.Event(), threading.Event(), threading.Event()
        self.resolve.return_value = credentials(gemini_key='')

        def fetch(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return {'data': [{'id': 'gpt-concurrent'}]}

        def waiting_request():
            waiter_started.set()
            return catalog.get_model_catalog(force=True)['providers']['openai']

        self.request.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(lambda: catalog.get_model_catalog()['providers']['openai'])
            self.assertTrue(started.wait(timeout=2))
            second = pool.submit(waiting_request)
            self.assertTrue(waiter_started.wait(timeout=2))
            release.set()
            self.assertEqual(first.result(timeout=4)['models'], second.result(timeout=4)['models'])
        self.request.assert_called_once()

    def test_cache_bound_evicts_finished_entries_but_never_inflight(self):
        self.reply({'data': []})
        with patch.object(catalog, 'MAX_CACHE_ENTRIES', 2):
            for n in range(3):
                self.resolve.return_value = credentials(gemini_key='', openai_key='synthetic-key-' + str(n))
                catalog.get_model_catalog()
            self.assertEqual(len(catalog._CACHE), 2)
            with catalog._CACHE_LOCK:
                for state in catalog._CACHE.values():
                    state['event'].clear()
            self.resolve.return_value = credentials(gemini_key='', openai_key='another-new-key')
            result = catalog.get_model_catalog()['providers']['openai']
            self.assertEqual(result['status'], 'refreshing')
            self.assertEqual(len(catalog._CACHE), 2)
            self.assertEqual(self.request.call_count, 3)

    def test_invalid_base_url_fails_without_network_or_raw_config_disclosure(self):
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://secret:password@provider.example/v1'}):
            result = self.result()
        self.assertEqual(result['status'], 'error')
        self.request.assert_not_called()
        self.assertNotIn('password', json.dumps(result))

    def test_invalid_gemini_auth_mode_fails_without_network(self):
        with patch.dict(os.environ, {'GEMINI_AUTH_MODE': 'unsupported'}):
            self.assertEqual(self.result('gemini')['status'], 'error')
        self.request.assert_not_called()


class CatalogCredentialResolutionTests(unittest.TestCase):
    def test_real_resolver_uses_only_user_key_or_explicit_server_key(self):
        from app import integrations
        from app.db import encrypt_secret
        with catalog._CACHE_LOCK:
            catalog._CACHE.clear()
        own_user = SimpleNamespace(openai_key_cipher=encrypt_secret('personal-synthetic-key'),
                                   openai_model='gpt-personal', gemini_key_cipher='')
        shared_user = SimpleNamespace(openai_key_cipher='', openai_model='gpt-expensive-user-selection',
                                      gemini_key_cipher='')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'shared-synthetic-key', 'OPENAI_MODEL': 'gpt-server',
                                     'GEMINI_API_KEY': '', 'OPENAI_BASE_URL': 'https://api.openai.com/v1'}), \
                patch.object(catalog, 'resolve_ai_credentials', wraps=integrations.resolve_ai_credentials), \
                patch.object(catalog, '_request_json', return_value={'data': []}) as request:
            personal = catalog.get_model_catalog(own_user)['providers']['openai']
            shared = catalog.get_model_catalog(shared_user)['providers']['openai']
        self.assertEqual(personal['selected_model'], 'gpt-personal')
        self.assertEqual(shared['selected_model'], 'gpt-server')
        self.assertEqual(request.call_args_list[0].args[2]['Authorization'], 'Bearer personal-synthetic-key')
        self.assertEqual(request.call_args_list[1].args[2]['Authorization'], 'Bearer shared-synthetic-key')
        self.assertEqual(len(catalog._CACHE), 2)


if __name__ == '__main__':
    unittest.main()
