"""HTTP adapter for Freelancer's official projects, users, and messages API v0.1."""
import json
import math
import re
from urllib.parse import quote
import httpx


class FreelancerError(Exception):
    pass


class FreelancerAPI:
    def __init__(self, token, base_url='https://www.freelancer.com'):
        self.base = base_url.rstrip('/')
        if self.base not in ('https://www.freelancer.com', 'https://www.freelancer-sandbox.com'):
            raise FreelancerError('Unrecognized Freelancer API host')
        token = str(token or '').strip()
        if not token or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token):
            raise FreelancerError('Enter a valid Freelancer OAuth token without whitespace')
        self.token = token

    def _req(self, method, resource, path, params=None, payload=None, form_encoded=False):
        # Resource and path are adapter-owned. Never follow a redirect with the OAuth header.
        if resource not in ('users', 'projects', 'messages') or '..' in path or '?' in path or '#' in path:
            raise FreelancerError('Invalid Freelancer API resource')
        url = f'{self.base}/api/{resource}/0.1/{path.lstrip("/")}'
        headers = {'Freelancer-OAuth-V1': self.token, 'User-Agent': 'BidPilot Pro/3.0', 'Accept': 'application/json'}
        kwargs = {'params': params, 'headers': headers}
        if payload is not None:
            kwargs['data' if form_encoded else 'json'] = payload
        try:
            with httpx.Client(timeout=httpx.Timeout(25.0, connect=8.0), follow_redirects=False) as client:
                with client.stream(method, url, **kwargs) as response:
                    if not 200 <= response.status_code < 300:
                        raise FreelancerError(_http_error(response.status_code))
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 3_000_000:
                            raise FreelancerError('Freelancer response exceeded the safe size limit')
                        chunks.append(chunk)
                    result = json.loads(b''.join(chunks))
            if not isinstance(result, dict) or 'result' not in result:
                raise FreelancerError('Freelancer returned an unexpected response; check account access and API scopes')
            if result.get('status') not in (None, 'success'):
                # Remote messages can echo credentials or user data, so do not reflect their body.
                raise FreelancerError('Freelancer rejected the request; verify token scopes and account eligibility')
            data = result['result']
            if not isinstance(data, (dict, list)):
                raise FreelancerError('Freelancer returned an invalid result')
            return data
        except httpx.TimeoutException as exc:
            raise FreelancerError('Freelancer request timed out. A submitted bid may have succeeded; check the account before retrying') from None
        except httpx.RequestError:
            raise FreelancerError('Cannot reach Freelancer. Check server network access and try again') from None
        except (ValueError, UnicodeError):
            raise FreelancerError('Freelancer API returned invalid JSON') from None

    def me(self):
        result = self._req('GET', 'users', 'self/', params={'jobs': 'true', 'status': 'true'})
        if not isinstance(result, dict) or not 1 <= len(str(result.get('id', ''))) <= 20 or not str(result.get('id', '')).isascii() or not str(result.get('id', '')).isdigit() or int(result['id']) <= 0:
            raise FreelancerError('Account ID missing from Freelancer response; reconnect with a user OAuth token')
        return result

    def user(self, user_id):
        return self._req('GET', 'users', f'users/{int(user_id)}/', params={
            'user_details': 'true',
            'user_country_details': 'true',
            'user_employer_reputation': 'true',
            'user_status': 'true'
        })

    def search(self, query, limit=25, offset=0):
        return self._req('GET', 'projects', 'projects/active/', params={
            'query': query,
            'limit': max(1, min(int(limit), 40)),
            'offset': max(0, int(offset)),
            'sort_field': 'time_updated',
            'full_description': 'true',
            'job_details': 'true',
            'user_details': 'true',
            'user_country_details': 'true',
            'user_employer_reputation': 'true',
            'user_status': 'true'
        })

    def project(self, project_id):
        return self._req('GET', 'projects', f'projects/{int(project_id)}/', params={
            'full_description': 'true',
            'job_details': 'true',
            'user_details': 'true',
            'user_country_details': 'true',
            'user_employer_reputation': 'true',
            'user_status': 'true'
        })

    def place_bid(self, project_id, bidder_id, description, amount, period, milestone_percentage=100):
        try:
            amount = float(amount)
            project_id, bidder_id, period = int(project_id), int(bidder_id), int(period)
            pct = int(milestone_percentage)
        except (ValueError, TypeError, OverflowError):
            raise FreelancerError('Invalid bid amount, account, period or milestone') from None
        if (not math.isfinite(amount) or amount <= 0 or project_id <= 0 or bidder_id <= 0
                or not 1 <= period <= 365 or not 10 <= pct <= 100):
            raise FreelancerError('Invalid bid amount, account, period or milestone')
        description = str(description or '').strip()
        if not 20 <= len(description) <= 10000:
            raise FreelancerError('Bid proposal must contain between 20 and 10000 characters')
        result = self._req('POST', 'projects', 'bids/', payload=dict(
            project_id=project_id, bidder_id=bidder_id, description=description,
            amount=amount, period=period, milestone_percentage=pct
        ))
        if not isinstance(result, dict):
            raise FreelancerError('Bid response uncertain; check your Freelancer account before retrying')
        bid = result.get('bid') if isinstance(result.get('bid'), dict) else result
        if not bid.get('id'):
            raise FreelancerError('Bid response uncertain; check your Freelancer account before retrying')
        return {**result, 'bid': bid}

    def list_my_bids(self, bidder_id, limit=35, offset=0):
        """Fetch one bounded page of bids; callers may supply offset for pagination."""
        return self._req('GET', 'projects', 'bids/', params={
            'bidders[]': [int(bidder_id)],
            'limit': max(1, min(int(limit), 50)),
            'offset': max(0, int(offset)),
            'project_details': 'true',
            'job_details': 'true'
        })

    def list_threads(self, limit=15):
        """Fetches recent active chat threads where clients messaged the user."""
        return self._req('GET', 'messages', 'threads/', params={
            'limit': max(1, min(int(limit), 30)),
            'context_details': 'true',
            'user_details': 'true',
            'last_message': 'true'
        })

    def send_chat_message(self, thread_id, message_text):
        """Sends an instant reply into a Freelancer chat thread."""
        return self._req(
            'POST',
            'messages',
            f'threads/{int(thread_id)}/messages/',
            payload={'message': str(message_text)[:2000]},
            form_encoded=True
        )


def _http_error(status):
    messages = {
        400: 'Freelancer rejected request parameters; check bid and profile requirements',
        401: 'Freelancer token is invalid or expired. Reconnect with a valid OAuth token',
        403: 'Freelancer denied access. Check OAuth scopes, account verification and API permissions',
        404: 'Freelancer resource was not found or is no longer available',
        409: 'Freelancer reports a conflict. Check whether the bid already exists',
        429: 'Freelancer API rate limit reached. Increase the scan interval and try later',
    }
    if 300 <= status < 400:
        return 'Freelancer redirected the request; credentials were not forwarded'
    return messages.get(status, f'Freelancer API HTTP {status}; try later or check account status')


def unwrap_user(result, owner_id):
    if not isinstance(result, dict):
        return {}
    users = result.get('users') or result.get('user') or result
    if isinstance(users, dict):
        if str(users.get('id', '')) == str(owner_id):
            return users
        found = users.get(str(owner_id))
        if found is None and isinstance(owner_id, (str, int)):
            found = users.get(owner_id, {})
        return found if isinstance(found, dict) else {}
    if isinstance(users, list):
        return next((u for u in users if isinstance(u, dict) and str(u.get('id')) == str(owner_id)), {})
    return {}


def normalize_project(raw, user=None, sandbox=False):
    user = user if isinstance(user, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    loc = user.get('location') if isinstance(user.get('location'), dict) else {}
    cc = user.get('country') or loc.get('country') or {}
    if not isinstance(cc, dict):
        cc = {}
    status = user.get('status') if isinstance(user.get('status'), dict) else {}
    pv = status.get('payment_verified')
    if not isinstance(pv, bool):
        pv = user.get('payment_verified')
    if not isinstance(pv, bool):
        pv = None
    rep = user.get('employer_reputation') or {}
    if not isinstance(rep, dict):
        rep = {}
    allrep = rep.get('entire_history') or rep.get('entire_history_details') or rep
    if not isinstance(allrep, dict):
        allrep = rep
    reviews = allrep.get('reviews')
    if isinstance(reviews, list):
        reviews = len(reviews)
    if reviews is None:
        reviews = allrep.get('total_reviews')
    rating = allrep.get('overall')
    if isinstance(rating, dict):
        rating = rating.get('rating')
    budget = raw.get('budget') if isinstance(raw.get('budget'), dict) else {}
    currency = raw.get('currency') if isinstance(raw.get('currency'), dict) else {}
    currency_code = currency.get('code')
    currency_code = currency_code.strip().upper() if isinstance(currency_code, str) else ''
    if not re.fullmatch(r'[A-Z]{3}', currency_code):
        currency_code = ''
    project_type = raw.get('type')
    project_type = project_type.strip().lower() if isinstance(project_type, str) else ''
    if project_type not in ('fixed', 'hourly'):
        project_type = ''
    slug = str(raw.get('seo_url') or '').strip('/')
    pid = raw.get('id')
    web_host = 'https://www.freelancer-sandbox.com' if sandbox else 'https://www.freelancer.com'
    url = f'{web_host}/projects/{quote(slug)}' if slug else (f'{web_host}/projects/{pid}' if pid else '')
    skills_raw = raw.get('jobs') or []
    skills = [j.get('name', '') if isinstance(j, dict) else str(j) for j in skills_raw] if isinstance(skills_raw, list) else []
    bs = raw.get('bid_stats') if isinstance(raw.get('bid_stats'), dict) else {}
    return dict(
        id=str(pid),
        title=str(raw.get('title') or 'Untitled project'),
        description=str(raw.get('description') or raw.get('preview_description') or ''),
        budget_min=budget.get('minimum'),
        budget_max=budget.get('maximum'),
        currency=currency_code,
        type=project_type,
        skills=skills,
        posted_at=raw.get('time_submitted') or raw.get('time_updated'),
        owner_id=raw.get('owner_id'),
        country=cc.get('name') or '',
        country_code=cc.get('code') or '',
        payment_verified=pv,
        client_reviews=reviews,
        client_rating=rating,
        client_name=user.get('display_name') or user.get('username') or 'Employer',
        url=url,
        project_status=raw.get('status'),
        frontend_status=raw.get('frontend_project_status'),
        bid_count=bs.get('bid_count'),
        demo=False,
        sandbox=sandbox
    )
