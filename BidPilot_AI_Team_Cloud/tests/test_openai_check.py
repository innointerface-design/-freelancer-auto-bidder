import io
import json
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

import check_openai_key as checker


class Response(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status
        self.requested_sizes = []

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.requested_sizes.append(size)
        return super().read(size)


def opener_for(status, payload):
    response = Response(status, json.dumps(payload).encode())
    opener = Mock()
    opener.open.return_value = response
    return opener, response


def test_exact_secret_get_only_fixed_official_host_no_app_or_environment_mutations(monkeypatch):
    import os
    import sys
    before_env, before_modules = dict(os.environ), set(sys.modules)
    opener, response = opener_for(200, {'object': 'list', 'data': [{'object': 'model', 'id': 'test-model'}]})
    key = 'key_exact-arbitrary-format-keep-this-private'
    result = checker.check_key(key, opener)
    request = opener.open.call_args.args[0]
    assert request.full_url == 'https://api.openai.com/v1/models'
    assert request.get_method() == 'GET' and request.data is None
    assert request.get_header('Authorization') == 'Bearer ' + key
    assert opener.open.call_args.kwargs == {'timeout': 15}
    assert result == {'status': 200, 'outcome': 'model_read_access'}
    assert response.requested_sizes == [1024 * 1024 + 1]
    assert dict(os.environ) == before_env
    assert not ({'app.db', 'dotenv'} & (set(sys.modules) - before_modules))
    assert key not in json.dumps(result)


@pytest.mark.parametrize('payload', [{}, {'object': 'list', 'data': 'not-a-list'},
                                    {'object': 'list', 'data': [{'id': 'model'}]},
                                    {'object': 'list', 'data': [{'object': 'model', 'id': ''}]}, []])
def test_success_requires_standard_models_list(payload):
    opener, _ = opener_for(200, payload)
    assert checker.check_key('any-printable-key-format', opener)['outcome'] == 'unexpected_response'


@pytest.mark.parametrize('status,expected', [(401, 'credential_rejected'), (403, 'access_denied'),
                                            (429, 'rate_or_quota_limited')])
def test_provider_errors_never_echo_body_or_claim_auth_from_other_status(status, expected):
    key = 'do-not-print-this-secret'
    error_body = {'error': {'code': 'invalid_api_key', 'message': key, 'other': 'private-response-content'}}
    error = urllib.error.HTTPError(checker.OFFICIAL_URL, status, key, {}, io.BytesIO(json.dumps(error_body).encode()))
    opener = Mock()
    opener.open.side_effect = error
    result = checker.check_key(key, opener)
    assert result == {'status': status, 'provider_code': 'invalid_api_key', 'outcome': expected}
    assert key not in json.dumps(result) and 'private-response-content' not in json.dumps(result)


def test_unrecognized_provider_code_is_not_returned():
    opener, _ = opener_for(401, {'error': {'code': 'private-key-in-code-field'}})
    assert checker.check_key('any-key', opener) == {'status': 401, 'outcome': 'credential_rejected'}


def test_redirect_handler_never_builds_redirect_request():
    request = urllib.request.Request(checker.OFFICIAL_URL, headers={'Authorization': 'Bearer private'})
    assert checker.NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://other.example/') is None
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(checker.OFFICIAL_URL, 302, 'Found',
                                                     {'Location': 'https://other.example/'}, io.BytesIO(b'private'))
    assert checker.check_key('private', opener) == {'status': 302, 'outcome': 'redirect_blocked'}
    assert opener.open.call_count == 1


def test_default_opener_enforces_tls_and_disables_redirects(monkeypatch):
    opener, _ = opener_for(200, {'object': 'list', 'data': []})
    build = Mock(return_value=opener)
    monkeypatch.setattr(checker.urllib.request, 'build_opener', build)
    assert checker.check_key('arbitrary-key')['outcome'] == 'model_read_access'
    handlers = build.call_args.args
    assert any(isinstance(handler, checker.NoRedirect) for handler in handlers)
    tls = next(handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler))._context
    import ssl
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('status,limit', [(200, 1024 * 1024), (401, 64 * 1024)])
def test_bounded_response_reads(status, limit):
    response = Response(status, b'x' * (limit + 100))
    opener = Mock()
    opener.open.return_value = response
    assert checker.check_key('private', opener) == {'status': status, 'outcome': 'response_too_large'}
    assert response.requested_sizes == [limit + 1]


def test_non_json_and_network_errors_do_not_leak_details():
    opener = Mock()
    opener.open.return_value = Response(200, b'<html>private-secret</html>')
    assert checker.check_key('private', opener) == {'status': 200, 'outcome': 'unreadable_response'}
    opener.open.side_effect = urllib.error.URLError('private-secret')
    assert checker.check_key('private', opener) == {'outcome': 'network_error'}


@pytest.mark.parametrize('key', ['', None, 'has space', 'line\nbreak', 'carriage\rreturn', 'nul\x00key', 'unicode-\u2603'])
def test_invalid_input_performs_no_request(key):
    opener = Mock()
    assert checker.check_key(key, opener) == {'outcome': 'invalid_input'}
    opener.open.assert_not_called()


def test_main_uses_hidden_prompt_without_storing_or_printing_secret(monkeypatch, capsys):
    secret = 'complete-hidden-secret'
    prompt = Mock(return_value=secret)
    check = Mock(return_value={'status': 200, 'outcome': 'model_read_access'})
    monkeypatch.setattr(checker.getpass, 'getpass', prompt)
    monkeypatch.setattr(checker, 'check_key', check)
    assert checker.main() == 0
    check.assert_called_once_with(secret)
    output = capsys.readouterr().out
    assert secret not in output and 'No app configuration is changed' in output
    assert json.loads(output.splitlines()[-1]) == {'status': 200, 'outcome': 'model_read_access'}


def test_truncated_http_response_never_raises_or_exposes_body():
    import http.client
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.getcode.return_value = 200
    response.read.side_effect = http.client.IncompleteRead(b'private-body-content')
    opener = Mock()
    opener.open.return_value = response
    assert checker.check_key('any-key', opener) == {'outcome': 'network_error'}


def test_hidden_input_failure_does_not_fall_back_to_visible_input(monkeypatch, capsys):
    def unavailable(prompt):
        import warnings
        warnings.warn('No secure terminal', checker.getpass.GetPassWarning)
        raise AssertionError('A visible fallback must never be reached')
    monkeypatch.setattr(checker.getpass, 'getpass', unavailable)
    check = Mock()
    monkeypatch.setattr(checker, 'check_key', check)
    assert checker.main() == 2
    check.assert_not_called()
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['outcome'] == 'hidden_input_unavailable_or_cancelled'
