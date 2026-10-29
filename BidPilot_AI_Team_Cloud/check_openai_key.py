"""Check official OpenAI model-list access without importing or configuring BidPilot."""
import getpass
import http.client
import json
import ssl
import urllib.error
import urllib.request
import warnings

OFFICIAL_URL = 'https://api.openai.com/v1/models'
SAFE_CODES = frozenset({'invalid_api_key', 'insufficient_quota', 'rate_limit_exceeded',
                        'permission_denied', 'account_deactivated', 'organization_restricted'})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def check_key(key, opener=None):
    """Return a safe diagnostic; success proves model-list access only."""
    if not isinstance(key, str) or not key or any(ord(c) <= 32 or ord(c) >= 127 for c in key):
        return {'outcome': 'invalid_input'}
    if opener is None:
        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(OFFICIAL_URL, method='GET',
                                     headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    try:
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.getcode()
            result = {'status': status}
            if 300 <= status < 400:
                return {**result, 'outcome': 'redirect_blocked'}
            limit = 1024 * 1024 if status == 200 else 64 * 1024
            body = response.read(limit + 1)
            if len(body) > limit:
                return {**result, 'outcome': 'response_too_large'}
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                return {**result, 'outcome': 'unreadable_response'}
            if status == 200:
                models = payload.get('data') if isinstance(payload, dict) else None
                if (isinstance(models, list) and payload.get('object') == 'list'
                        and all(isinstance(model, dict) and model.get('object') == 'model'
                                and isinstance(model.get('id'), str) and model['id'] for model in models)):
                    return {**result, 'outcome': 'model_read_access'}
                return {**result, 'outcome': 'unexpected_response'}
            error = payload.get('error') if isinstance(payload, dict) else None
            code = error.get('code') if isinstance(error, dict) else None
            if isinstance(code, str) and code in SAFE_CODES:
                result['provider_code'] = code
            outcome = {401: 'credential_rejected', 403: 'access_denied',
                       429: 'rate_or_quota_limited'}.get(status, 'provider_http_error')
            return {**result, 'outcome': outcome}
    except (urllib.error.URLError, OSError, ValueError, UnicodeError, http.client.HTTPException):
        return {'outcome': 'network_error'}


def main():
    print('Official OpenAI check: secret input is hidden and is not stored.')
    print('No app configuration is changed. Success checks model-list access, not generation or billing.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('Paste the complete secret key privately: ')
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print(json.dumps({'outcome': 'hidden_input_unavailable_or_cancelled'}))
        return 2
    result = check_key(key)
    print(json.dumps(result))
    return 0 if result.get('outcome') == 'model_read_access' else 1


if __name__ == '__main__':
    raise SystemExit(main())
