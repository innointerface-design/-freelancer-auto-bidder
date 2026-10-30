import json, time, os, secrets, re, logging, hashlib, hmac, math
from typing import Literal
from urllib.parse import urlsplit
from sqlalchemy import update, text
from types import SimpleNamespace
from sqlalchemy.exc import IntegrityError
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .db import (
    SessionLocal, init_db, User, Connection, Job, Bid, ChatGreeted, PaymentRequest, Audit,
    APP_ENV, SESSION_SECRET, SECURE_COOKIES, PLATFORM_AUTO, ALLOW_PROD,
    encrypt_secret, audit, public_user, hash_password, verify_password,
    AuthSession, LoginAttempt, create_auth_session, get_auth_session, revoke_auth_session, session_digest,
    plan_config, apply_plan, subscription_active
)
from .logic import score_job, score_for_user, make_proposal, auto_eligible, price_for
from .integrations import get_client, scan_for_user, extract_skills, ai_proposal, ai_review, ai_chat_reply, test_ai_connection, resolve_ai_credentials, fetch_live_bids
from .freelancer import FreelancerAPI, FreelancerError
from .advanced import AdvancedConfigError, evaluate_filters, load_advanced, schedule_allows

log = logging.getLogger('bidpilot')
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
ROOT = os.path.abspath(os.path.dirname(__file__))


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='BidPilot Team Cloud — Bidman Pro SaaS Edition', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount('/static', StaticFiles(directory=os.path.join(ROOT, 'static')), name='static')


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's input echo may contain passwords, API keys or bearer tokens.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()
    ]})


def _parse_origin(value):
    # urlsplit silently strips some controls; reject them before parsing headers.
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            return None
        return parsed.scheme, parsed.hostname.lower(), port
    except (ValueError, UnicodeError):
        return None


def _origin_allowed(origin, expected, request_base):
    observed, configured = _parse_origin(origin), _parse_origin(expected)
    if observed is None or configured is None:
        return False
    if observed == configured:
        return True
    if APP_ENV not in ('development', 'test'):
        return False
    actual = _parse_origin(request_base)
    loopback = {'localhost', '127.0.0.1', '::1'}
    # Browser opened via a local alias: still require a same-origin request,
    # the configured scheme/port, and exact known loopback names. No DNS lookup.
    return (actual is not None and observed == actual
            and observed[1] in loopback and configured[1] in loopback
            and observed[0] == configured[0] and observed[2] == configured[2])


@app.middleware('http')
async def secure_requests(request: Request, call_next):
    denial = None
    if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origins = request.headers.getlist('origin')
        if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
            denial = 'This request came from another website. Open BidPilot directly and sign in again.'
        elif len(origins) > 1:
            denial = 'Invalid browser origin headers. Reload the app and try again.'
        elif origins:
            expected = os.getenv('PUBLIC_URL', '').strip() or str(request.base_url)
            if not _origin_allowed(origins[0], expected, str(request.base_url)):
                denial = 'This browser address does not match this app. Open the address configured for the server and try again.'
    response = JSONResponse(status_code=403, content={'detail': denial}) if denial else await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    if SECURE_COOKIES:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


class InputModel(BaseModel):
    model_config = {'allow_inf_nan': False, 'extra': 'forbid'}


class LoginIn(InputModel):
    email: str = Field(min_length=5, max_length=240)
    password: str = Field(min_length=1, max_length=256)


class RegisterIn(InputModel):
    email: str = Field(min_length=5, max_length=240)
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=256)


class MemberIn(InputModel):
    email: str = Field(min_length=5, max_length=240)
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    plan_tier: Literal['free_team', 'paid_starter', 'paid_pro'] = 'paid_pro'
    subscription_status: Literal['active', 'trial', 'inactive', 'expired'] = 'active'
    monthly_fee: float = 40.0


class MemberPlanIn(InputModel):
    plan_tier: Literal['free_team', 'paid_starter', 'paid_pro'] = 'paid_pro'
    subscription_status: Literal['active', 'trial', 'inactive', 'expired'] = 'active'
    monthly_fee: float = 40.0


class PaymentSubmitIn(InputModel):
    plan_tier: Literal['paid_starter', 'paid_pro'] = 'paid_starter'
    method: str = Field(min_length=2, max_length=50)
    transaction_id: str = Field(min_length=3, max_length=120)
    amount: float = Field(gt=0, le=100000)
    currency: str = Field(default='USD', max_length=15)
    sender_phone: str = Field(default='', max_length=60)
    notes: str = Field(default='', max_length=500)


class PaymentDecisionIn(InputModel):
    action: str


class AIKeysIn(InputModel):
    preferred_ai: Literal['auto', 'gemini', 'openai', 'smart_builtin'] = 'auto'
    gemini_key: str = Field(default='', max_length=500)
    gemini_model: str = Field(default='gemini-3.5-flash-lite', max_length=60)
    openai_key: str = Field(default='', max_length=500)
    openai_model: str = Field(default='gpt-4.1-mini', max_length=60)
    clear_gemini: bool = False
    clear_openai: bool = False


class AITestIn(InputModel):
    provider: Literal['both', 'gemini', 'openai'] = 'both'  # 'both', 'gemini', 'openai'


class SettingsIn(InputModel):
    name: str = Field(min_length=2, max_length=100)
    skills: str = Field(min_length=1, max_length=1200)
    search_terms: str = Field(min_length=1, max_length=600)
    excluded_keywords: str = Field(default='', max_length=600)
    countries: str = Field(min_length=0, max_length=250)
    description: str = Field(max_length=1500)
    portfolio: str = Field(max_length=500)
    proposal_tone: str = Field(default='winning_direct', max_length=40)
    bid_strategy: str = Field(default='average', max_length=30)
    default_days: int = Field(default=3, ge=1, le=365)
    milestone_pct: int = Field(default=100, ge=10, le=100)
    auto_chat_greeting: bool = False
    chat_greeting_template: str = Field(default='', max_length=1200)
    allow_hourly: bool = True
    allow_any_currency: bool = True
    require_payment_verified: bool = False
    require_country_match: bool = False
    min_score: int = Field(ge=20, le=100)
    min_budget: float = Field(ge=1, le=1000000)
    max_bid: float = Field(ge=1, le=1000000)
    max_daily: int = Field(ge=1, le=200)
    max_hourly: int = Field(default=3, ge=1, le=60)
    auto_requested: bool = False


class ConnectionIn(InputModel):
    token: str = Field(min_length=10, max_length=2000)
    environment: str = 'production'


class DraftIn(InputModel):
    amount: float = Field(gt=0, le=1000000)
    days: int = Field(ge=1, le=365)
    milestone_pct: int = Field(default=100, ge=10, le=100)
    proposal: str = Field(min_length=30, max_length=3500)


class SubmitIn(InputModel):
    intent: str = Field(min_length=1, max_length=100)
    confirmation: str = Field(max_length=40)


class InstantBidIn(InputModel):
    amount: float = Field(gt=0, le=1000000)
    days: int = Field(ge=1, le=365)
    milestone_pct: int = Field(default=100, ge=10, le=100)
    proposal: str = Field(min_length=30, max_length=3500)


class ChatPreviewIn(InputModel):
    client_name: str = Field(default='Client', max_length=200)
    project_title: str = Field(default='Video Editing & AI Production', max_length=300)
    client_message: str = Field(default='Hi, are you available?', max_length=5000)


class PermissionIn(InputModel):
    enabled: bool


def csrf_for(token):
    return hmac.new(SESSION_SECRET.encode(), ('csrf:' + token).encode(), hashlib.sha256).hexdigest()


def get_auth(request: Request, csrf=False):
    token = request.cookies.get('bp_session', '')
    with SessionLocal() as s:
        session = get_auth_session(s, token)
        if not session:
            raise HTTPException(401, 'Please sign in')
        if csrf and not secrets.compare_digest(session.csrf_hash, session_digest(request.headers.get('x-csrf-token', ''))):
            raise HTTPException(403, 'CSRF token missing or invalid')
        u = s.get(User, session.user_id)
        if not u or not u.active:
            raise HTTPException(401, 'Account disabled or expired')
        return u.id


def throttle(request, email, action):
    """Persist account and network throttles so workers/restarts share limits."""
    ip = request.client.host if request.client else 'unknown'
    identifiers = [session_digest(f'{action}:ip:{ip}'), session_digest(f'{action}:email:{email}')]
    now = int(time.time())
    with SessionLocal() as s:
        if s.bind.dialect.name == 'postgresql':
            for ident in sorted(identifiers):
                s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(ident[:15], 16)})
        s.query(LoginAttempt).filter(LoginAttempt.attempted_at < now - 900).delete(synchronize_session=False)
        for ident in identifiers:
            if s.query(LoginAttempt).filter(LoginAttempt.identifier == ident, LoginAttempt.attempted_at >= now - 900).count() >= (10 if action == 'login' else 5):
                raise HTTPException(429, 'Too many attempts. Try again in 15 minutes.')
        for ident in identifiers:
            s.add(LoginAttempt(identifier=ident, attempted_at=now))
        s.commit()


def clean_email(email):
    email = email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise HTTPException(400, 'Invalid email')
    return email


def set_session(s, user_id, response):
    token, _ = create_auth_session(s, user_id)
    s.flush()
    session = get_auth_session(s, token)
    session.csrf_hash = session_digest(csrf_for(token))
    response.set_cookie('bp_session', token, max_age=12 * 3600, httponly=True,
                        secure=SECURE_COOKIES, samesite='strict', path='/')


def current(request: Request):
    return get_auth(request, False)


def mutate(request: Request):
    return get_auth(request, True)


def admin_only(uid):
    with SessionLocal() as s:
        u = s.get(User, uid)
        if not u or u.role != 'admin':
            raise HTTPException(403, 'Admin access required')


def owned(s, cls, objid, uid):
    obj = s.get(cls, objid)
    if not obj or obj.user_id != uid:
        raise HTTPException(404, 'Not found')
    return obj


def pack_job(j):
    d = json.loads(j.data)
    return {
        **d,
        'id': j.id,
        'remote_id': j.remote_id,
        'score': j.score,
        'decision': j.decision,
        'reasons': json.loads(j.reasons or '[]')
    }


def public_bid(b):
    return {
        'status': b.status,
        'proposal': b.proposal,
        'amount': b.amount,
        'days': b.days,
        'milestone_pct': getattr(b, 'milestone_pct', 100),
        'source': b.source,
        'updated': b.updated,
        'remote_bid_id': b.remote_bid_id
    }


@app.get('/')
def index():
    return FileResponse(os.path.join(ROOT, 'static', 'index.html'))


@app.get('/health')
def health():
    try:
        with SessionLocal() as s:
            s.execute(text('SELECT 1'))
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'unavailable'})
    return {'status': 'ok', 'service': 'BidPilot Team Cloud'}


@app.post('/api/register')
def register(x: RegisterIn, request: Request, response: Response):
    email = clean_email(x.email)
    if len(x.name.strip()) < 2:
        raise HTTPException(400, 'Enter your name')
    throttle(request, email, 'register')
    with SessionLocal() as s:
        # Signup never trusts an email address to grant founder/admin privileges.
        u = User(email=email, name=x.name.strip(), password_hash=hash_password(x.password),
                 role='member', auto_requested=False, auto_chat_greeting=False)
        apply_plan(u, 'paid_starter', 'inactive')
        s.add(u)
        try:
            s.flush()
            set_session(s, u.id, response)
            audit(s, u.id, 'registered')
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, 'Unable to create account with this email. Try signing in.')
    return {'ok': True}


@app.post('/api/login')
def login(x: LoginIn, response: Response, request: Request):
    email = x.email.strip().lower()
    throttle(request, email, 'login')
    with SessionLocal() as s:
        u = s.query(User).filter_by(email=email).first()
        valid = verify_password(x.password, u.password_hash if u else DUMMY_PASSWORD_HASH)
        if not u or not valid or not u.active:
            raise HTTPException(401, 'Invalid email or password')
        revoke_auth_session(s, request.cookies.get('bp_session', ''))
        set_session(s, u.id, response)
        s.commit()
        return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, response: Response, uid: int = Depends(mutate)):
    with SessionLocal() as s:
        revoke_auth_session(s, request.cookies.get('bp_session', ''))
        s.commit()
    response.delete_cookie('bp_session', path='/', secure=SECURE_COOKIES, httponly=True, samesite='strict')
    return {'ok': True}


@app.get('/api/me')
def me(uid: int = Depends(current)):
    with SessionLocal() as s:
        u = s.get(User, uid)
        c = s.query(Connection).filter_by(user_id=uid).first()
        creds = resolve_ai_credentials(u)
        active_providers = []
        if creds['gemini_key']:
            active_providers.append(f"Gemini ({creds['gemini_model']})")
        if creds['openai_key']:
            active_providers.append(f"ChatGPT ({creds['openai_model']})")
        ai_label = ' + '.join(active_providers) if active_providers else 'Local proposal templates (no live AI connection verified)'
        return {
            'user': public_user(u, c),
            'platform_auto_authorized': PLATFORM_AUTO,
            'ai_enabled': bool(active_providers),
            'ai_provider': ai_label,
            'payment_methods': {
                'jazzcash': os.getenv('PAYMENT_JAZZCASH', ''),
                'easypaisa': os.getenv('PAYMENT_EASYPAISA', ''),
                'bank': os.getenv('PAYMENT_BANK', ''),
                'stripe_url': os.getenv('PAYMENT_STRIPE_URL', ''),
                'whatsapp': os.getenv('PAYMENT_WHATSAPP', '')
            }
        }


@app.post('/api/ai-keys')
def save_ai_keys(x: AIKeysIn, uid: int = Depends(mutate)):
    """Saves Google Gemini API Key and OpenAI ChatGPT API Key encrypted in the database directly from the UI."""
    with SessionLocal() as s:
        u = s.get(User, uid)
        if x.preferred_ai in ('auto', 'gemini', 'openai', 'smart_builtin'):
            u.preferred_ai = x.preferred_ai
        if x.gemini_model.strip():
            u.gemini_model = x.gemini_model.strip()
        if x.openai_model.strip():
            u.openai_model = x.openai_model.strip()

        if x.clear_gemini:
            u.gemini_key_cipher = ''
        elif x.gemini_key.strip():
            u.gemini_key_cipher = encrypt_secret(x.gemini_key.strip())

        if x.clear_openai:
            u.openai_key_cipher = ''
        elif x.openai_key.strip():
            u.openai_key_cipher = encrypt_secret(x.openai_key.strip())

        u.updated = int(time.time())
        audit(s, uid, 'ai_keys_updated', f'Preferred={u.preferred_ai}, GeminiModel={u.gemini_model}, OpenAIModel={u.openai_model}')
        s.commit()
        c = s.query(Connection).filter_by(user_id=uid).first()
        creds = resolve_ai_credentials(u)
        active_providers = []
        if creds['gemini_key']:
            active_providers.append(f"Gemini ({creds['gemini_model']})")
        if creds['openai_key']:
            active_providers.append(f"ChatGPT ({creds['openai_model']})")
        return {
            'ok': True,
            'user': public_user(u, c),
            'ai_provider': ' + '.join(active_providers) if active_providers else 'Local proposal templates (no live AI connection verified)'
        }


@app.post('/api/ai-keys/test')
def test_ai_keys_endpoint(x: AITestIn, uid: int = Depends(mutate)):
    """Runs a live verification ping on the saved Gemini and/or OpenAI ChatGPT API keys."""
    cloud_limit(uid)
    with SessionLocal() as s:
        require_subscription(s, uid)
        u = s.get(User, uid)
        res = test_ai_connection(u, provider=x.provider)
        return {'ok': True, 'results': res}


@app.get('/api/csrf')
def csrf(request: Request, uid: int = Depends(current)):
    return {'csrf': csrf_for(request.cookies['bp_session'])}


@app.get('/api/billing')
def my_billing(uid: int = Depends(current)):
    with SessionLocal() as s:
        u = s.get(User, uid)
        reqs = s.query(PaymentRequest).filter_by(user_id=uid).order_by(PaymentRequest.id.desc()).limit(25).all()
        return {
            'plan_tier': u.plan_tier,
            'subscription_status': u.subscription_status,
            'monthly_fee': u.monthly_fee,
            'subscription_expires': u.subscription_expires,
            'subscription_active': subscription_active(u),
            'plans': [{'plan_tier': 'paid_starter', 'amount': 10, 'monthly_bid_limit': 150}, {'plan_tier': 'paid_pro', 'amount': 40, 'monthly_bid_limit': 0}],
            'payments': [
                {
                    'id': r.id,
                    'method': r.method,
                    'plan_tier': r.plan_tier,
                    'transaction_id': r.transaction_id,
                    'amount': r.amount,
                    'currency': r.currency,
                    'sender_phone': r.sender_phone,
                    'status': r.status,
                    'created': r.created
                }
                for r in reqs
            ]
        }


@app.post('/api/billing/submit')
def submit_payment(x: PaymentSubmitIn, uid: int = Depends(mutate)):
    methods = {'jazzcash': 'PAYMENT_JAZZCASH', 'easypaisa': 'PAYMENT_EASYPAISA', 'bank': 'PAYMENT_BANK', 'stripe': 'PAYMENT_STRIPE_URL'}
    method = x.method.strip().lower()
    if method not in methods or not os.getenv(methods[method], '').strip():
        raise HTTPException(400, 'Payment method is not configured. Contact the administrator.')
    if x.currency.strip().upper() != 'USD' or abs(x.amount - plan_config(x.plan_tier)['monthly_fee']) > 0.001:
        raise HTTPException(400, 'Submit the selected plan price in USD. Administrator must independently verify settlement/conversion.')
    tid = x.transaction_id.strip()
    if len(tid) < 3:
        raise HTTPException(400, 'A transaction reference is required')
    with SessionLocal() as s:
        lock_user(s, uid)
        if s.query(PaymentRequest).filter_by(method=method, transaction_id=tid).first():
            raise HTTPException(409, 'This payment reference has already been submitted')
        if s.query(PaymentRequest).filter_by(user_id=uid, status='pending').count() >= 3:
            raise HTTPException(409, 'Please wait for review of your pending payments')
        pr = PaymentRequest(user_id=uid, method=method, transaction_id=tid, amount=x.amount,
                            currency='USD', plan_tier=x.plan_tier, sender_phone=x.sender_phone.strip(),
                            notes=x.notes.strip(), status='pending')
        s.add(pr)
        audit(s, uid, 'payment_submitted', 'Payment reference submitted for manual review')
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, 'This payment reference has already been submitted')
        return {'ok': True, 'payment_id': pr.id}


@app.get('/api/admin/payments')
def admin_payments(uid: int = Depends(current)):
    admin_only(uid)
    with SessionLocal() as s:
        users = {u.id: u for u in s.query(User).all()}
        reqs = s.query(PaymentRequest).order_by(PaymentRequest.id.desc()).limit(60).all()
        return {
            'payments': [
                {
                    'id': r.id,
                    'user_id': r.user_id,
                    'user_name': users[r.user_id].name if r.user_id in users else 'Unknown',
                    'user_email': users[r.user_id].email if r.user_id in users else '',
                    'method': r.method,
                    'plan_tier': r.plan_tier,
                    'transaction_id': r.transaction_id,
                    'amount': r.amount,
                    'currency': r.currency,
                    'sender_phone': r.sender_phone,
                    'notes': r.notes,
                    'status': r.status,
                    'created': r.created
                }
                for r in reqs
            ]
        }


@app.post('/api/admin/payments/{payment_id}/decide')
def admin_decide_payment(payment_id: int, x: PaymentDecisionIn, uid: int = Depends(mutate)):
    admin_only(uid)
    if x.action not in ('approve', 'reject'):
        raise HTTPException(400, 'Choose approve or reject')
    with SessionLocal() as s:
        pr = s.query(PaymentRequest).filter_by(id=payment_id).with_for_update().first()
        if not pr:
            raise HTTPException(404, 'Payment request not found')
        # Conditional update is also atomic on SQLite, where FOR UPDATE is ignored.
        changed = s.query(PaymentRequest).filter_by(id=payment_id, status='pending').update(
            {'status': 'approved' if x.action == 'approve' else 'rejected', 'reviewed_at': int(time.time())}, synchronize_session=False)
        if changed != 1:
            raise HTTPException(409, 'This payment has already been reviewed')
        u = s.get(User, pr.user_id)
        if u and x.action == 'approve':
            lock_user(s, u.id)
            if pr.plan_tier not in ('paid_starter', 'paid_pro') or pr.currency != 'USD' or abs(pr.amount - plan_config(pr.plan_tier)['monthly_fee']) > 0.001:
                raise HTTPException(400, 'Payment does not match an available plan')
            if u.plan_tier == 'free_team':
                raise HTTPException(400, 'Founder accounts do not require payment')
            if subscription_active(u) and u.plan_tier == pr.plan_tier:
                u.subscription_expires = max(int(time.time()), u.subscription_expires) + 30 * 86400
                u.subscription_status = 'active'
            else:
                apply_plan(u, pr.plan_tier, 'active')
            u.auto_admin_allowed = True
            audit(s, u.id, 'subscription_activated', f'Payment #{pr.id} manually verified by admin {uid}; 30-day access')
        audit(s, uid, 'payment_reviewed', f'Payment #{pr.id} -> {x.action}')
        s.commit()
        return {'ok': True, 'status': 'approved' if x.action == 'approve' else 'rejected'}


@app.get('/api/team')
def team(uid: int = Depends(current)):
    admin_only(uid)
    with SessionLocal() as s:
        members = [public_user(u, s.query(Connection).filter_by(user_id=u.id).first()) for u in s.query(User).order_by(User.id)]
        free_count = sum(1 for m in members if m.get('plan_tier') == 'free_team')
        paid_count = sum(1 for m in members if m.get('plan_tier') != 'free_team')
        mrr = sum(float(m.get('monthly_fee') or 0) for m in members if m.get('plan_tier') != 'free_team' and m.get('subscription_status') == 'active')
        pending_payments = s.query(PaymentRequest).filter_by(status='pending').count()
        return {
            'members': members,
            'billing_summary': {
                'free_team_count': free_count,
                'paid_subscribers_count': paid_count,
                'estimated_mrr_usd': round(mrr, 2),
                'pending_payments': pending_payments
            }
        }


@app.get('/api/team/{member_id}/overview')
def team_member_overview(member_id: int, uid: int = Depends(current)):
    admin_only(uid)
    with SessionLocal() as s:
        m = s.get(User, member_id)
        if not m:
            raise HTTPException(404, 'Member not found')
        c = s.query(Connection).filter_by(user_id=member_id).first()
        jobs = s.query(Job).filter_by(user_id=member_id).order_by(Job.discovered.desc()).limit(50).all()
        bids = s.query(Bid).filter_by(user_id=member_id).all()
        by_job = {b.job_id: b for b in bids}
        events = s.query(Audit).filter_by(user_id=member_id).order_by(Audit.id.desc()).limit(30).all()
        return {
            'member': public_user(m, c),
            'counts': {
                'jobs': s.query(Job).filter_by(user_id=member_id).count(),
                'bids': s.query(Bid).filter_by(user_id=member_id).count(),
                'submitted': s.query(Bid).filter_by(user_id=member_id, status='submitted').count()
            },
            'jobs': [
                {
                    'title': json.loads(j.data).get('title', ''),
                    'score': j.score,
                    'remote_id': j.remote_id,
                    'demo': bool(json.loads(j.data).get('demo')),
                    'bid_status': by_job[j.id].status if j.id in by_job else 'none',
                    'bid_amount': by_job[j.id].amount if j.id in by_job else None,
                    'currency': json.loads(j.data).get('currency', '')
                }
                for j in jobs
            ],
            'activity': [{'action': a.action, 'message': a.message, 'created': a.created} for a in events]
        }


@app.post('/api/team')
def new_member(x: MemberIn, uid: int = Depends(mutate)):
    admin_only(uid)
    x.email = clean_email(x.email)
    if len(x.name.strip()) < 2:
        raise HTTPException(400, 'Enter a name with at least two non-space characters')
    tier = x.plan_tier
    fee = plan_config(tier)['monthly_fee']
    with SessionLocal() as s:
        lock_user(s, uid)
        if tier == 'free_team' and s.query(User).filter_by(plan_tier='free_team').count() >= 2:
            raise HTTPException(409, 'Both free founder slots are already assigned')
        if s.query(User).filter_by(email=x.email.lower().strip()).first():
            raise HTTPException(409, 'Account already exists')
        u = User(
            email=x.email.lower().strip(),
            name=x.name.strip(),
            password_hash=hash_password(x.password),
            plan_tier=tier,
            subscription_status=x.subscription_status if x.subscription_status in ('active', 'trial', 'expired') else 'active',
            monthly_fee=fee,
            auto_requested=False,
            auto_admin_allowed=True
        )
        apply_plan(u, tier, x.subscription_status)
        s.add(u)
        s.flush()
        audit(s, uid, 'team_member_created', f'New member: {u.email} ({tier}, ${fee}/mo)')
        s.commit()
        return {'member': public_user(u)}


@app.post('/api/team/{member_id}/plan')
def update_member_plan(member_id: int, x: MemberPlanIn, uid: int = Depends(mutate)):
    admin_only(uid)
    with SessionLocal() as s:
        u = s.get(User, member_id)
        if not u:
            raise HTTPException(404, 'Member not found')
        lock_user(s, uid)
        if x.plan_tier == 'free_team' and u.plan_tier != 'free_team' and s.query(User).filter_by(plan_tier='free_team').count() >= 2:
            raise HTTPException(409, 'Both free founder slots are already assigned')
        apply_plan(u, x.plan_tier, x.subscription_status)
        audit(s, uid, 'member_plan_updated', f'{u.email}: {u.plan_tier} ({u.subscription_status}, ${u.monthly_fee}/mo)')
        s.commit()
        return {'ok': True, 'member': public_user(u)}


@app.post('/api/team/{member_id}/permission')
def team_permission(member_id: int, x: PermissionIn, uid: int = Depends(mutate)):
    admin_only(uid)
    if x.enabled and not PLATFORM_AUTO:
        raise HTTPException(403, 'Platform-level automation switch is off')
    with SessionLocal() as s:
        u = s.get(User, member_id)
        if not u:
            raise HTTPException(404, 'Member not found')
        u.auto_admin_allowed = x.enabled
        audit(s, uid, 'auto_member_approval', f'{u.email}: {x.enabled}')
        s.commit()
        return {'ok': True}


@app.post('/api/team/{member_id}/active')
def team_active(member_id: int, x: PermissionIn, uid: int = Depends(mutate)):
    admin_only(uid)
    if member_id == uid and not x.enabled:
        raise HTTPException(400, 'You cannot disable your own admin session')
    with SessionLocal() as s:
        u = s.get(User, member_id)
        if not u:
            raise HTTPException(404, 'Not found')
        lock_user(s, member_id)
        u.active = x.enabled
        if not x.enabled:
            s.query(AuthSession).filter_by(user_id=member_id).delete(synchronize_session=False)
        u.auto_requested = False if not x.enabled else u.auto_requested
        audit(s, uid, 'member_status', f'{u.email}: active={x.enabled}')
        s.commit()
    return {'ok': True}


@app.post('/api/settings')
def settings(x: SettingsIn, uid: int = Depends(mutate)):
    if len(x.name.strip()) < 2 or not x.skills.strip() or not x.search_terms.strip():
        raise HTTPException(400, 'Enter a name and non-empty skills and search terms')
    if x.max_bid < x.min_budget:
        raise HTTPException(400, 'Maximum bid must not be below minimum price')
    if x.portfolio and not (x.portfolio.startswith('https://') or x.portfolio.startswith('http://')):
        raise HTTPException(400, 'Portfolio requires https:// or http:// URL')
    if (x.auto_requested or x.auto_chat_greeting) and not PLATFORM_AUTO:
        raise HTTPException(403, 'Server auto-bid switch is disabled in .env')
    with SessionLocal() as s:
        u = s.get(User, uid)
        for k, v in x.model_dump().items():
            setattr(u, k, v)
        u.updated = int(time.time())
        for j in s.query(Job).filter_by(user_id=uid).all():
            raw = json.loads(j.data)
            sc = score_for_user(raw, u)
            j.score = sc['score']
            j.decision = sc['decision']
            j.reasons = json.dumps(sc['reasons'])
        audit(s, uid, 'settings_updated', f'Bidman Pro settings updated (Auto-Pilot={u.auto_requested}, Milestone={u.milestone_pct}%, Auto-Chat={u.auto_chat_greeting})')
        s.commit()
        return {'user': public_user(u, s.query(Connection).filter_by(user_id=uid).first())}


@app.post('/api/connect')
def connect(x: ConnectionIn, uid: int = Depends(mutate)):
    if x.environment not in ('sandbox', 'production'):
        raise HTTPException(400, 'Select sandbox or production')
    if x.environment == 'production' and not ALLOW_PROD:
        raise HTTPException(403, 'Set ALLOW_PRODUCTION_CONNECTION=true in .env')
    with SessionLocal() as s:
        previous = s.query(Connection).filter_by(user_id=uid).first()
        user = s.get(User, uid)
        if max(int(user.freelancer_cooldown_until or 0), int(previous.api_cooldown_until or 0) if previous else 0) > int(time.time()):
            raise HTTPException(429, 'Wait for the Freelancer API cooldown before reconnecting.')
    try:
        api = FreelancerAPI(x.token, 'https://www.freelancer-sandbox.com' if x.environment == 'sandbox' else 'https://www.freelancer.com')
        identity = api.me()
        flid = identity.get('id')
        name = identity.get('username') or identity.get('display_name') or str(flid)
        if not flid:
            raise FreelancerError('Account ID missing from Freelancer response')
        skills = extract_skills(identity, api)
    except FreelancerError as e:
        if getattr(e, 'status_code', None) == 429:
            # A first connection has no Connection row yet. Preserve the
            # provider wait on the user so retry/reconnect cannot bypass it.
            retry = getattr(e, 'retry_after', None)
            wait = max(30, int(retry) if isinstance(retry, (int, float)) and math.isfinite(retry) and retry >= 0 else 300)
            with SessionLocal() as s:
                lock_user(s, uid)
                user = s.get(User, uid)
                user.freelancer_cooldown_until = max(int(user.freelancer_cooldown_until or 0), int(time.time()) + wait)
                audit(s, uid, 'freelancer_paused', 'rate_limited')
                s.commit()
            raise HTTPException(429, 'Freelancer API rate limit reached. Wait for the cooldown before reconnecting.') from None
        raise HTTPException(400, str(e))
    with SessionLocal() as s:
        lock_user(s, uid)
        if s.query(Bid).filter_by(user_id=uid, status='sending').first():
            raise HTTPException(409, 'A bid is being sent. Check its result before changing the connection.')
        other = s.query(Connection).filter_by(freelancer_id=str(flid), environment=x.environment).first()
        if other and other.user_id != uid:
            raise HTTPException(409, 'This Freelancer account is already connected by another team member')
        c = s.query(Connection).filter_by(user_id=uid).first()
        if not c:
            c = Connection(user_id=uid, token_cipher='')
            s.add(c)
        account_changed = c.environment != x.environment or c.freelancer_id != str(flid)
        c.token_cipher = encrypt_secret(x.token)
        if account_changed:
            s.query(Bid).filter(Bid.user_id == uid, Bid.status.in_(['sending', 'submitted', 'uncertain'])).update({'status': 'uncertain'}, synchronize_session=False)
        c.environment = x.environment
        c.freelancer_id = str(flid)
        c.freelancer_name = name
        c.last_scan = 0
        c.last_error = ''
        c.api_pause_reason = ''
        c.api_cooldown_until = 0
        if account_changed:
            c.last_bid_attempt_at = 0
        u = s.get(User, uid)
        u.auto_requested = False
        u.auto_chat_greeting = False
        if skills:
            u.skills = ', '.join(skills)
            u.search_terms = ', '.join(skills[:6])
        audit(s, uid, 'freelancer_connected', f'{x.environment} @{name}; {len(skills)} named skills imported')
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, 'This Freelancer account is already connected by another member')
        return {'ok': True, 'freelancer_name': name, 'skills_imported': skills}


@app.post('/api/disconnect')
def disconnect(uid: int = Depends(mutate)):
    with SessionLocal() as s:
        lock_user(s, uid)
        if s.query(Bid).filter_by(user_id=uid, status='sending').first():
            raise HTTPException(409, 'A bid is being sent. Check its result before changing the connection.')
        c = s.query(Connection).filter_by(user_id=uid).first()
        if c:
            s.delete(c)
        u = s.get(User, uid)
        u.auto_requested = False
        u.auto_chat_greeting = False
        audit(s, uid, 'freelancer_disconnected')
        s.commit()
    return {'ok': True}


@app.get('/api/jobs')
def jobs(uid: int = Depends(current)):
    with SessionLocal() as s:
        arr = s.query(Job).filter_by(user_id=uid).order_by(Job.score.desc(), Job.discovered.desc()).limit(300).all()
        bids = {b.job_id: b for b in s.query(Bid).filter_by(user_id=uid).all()}
        return {'jobs': [{**pack_job(j), 'bid': public_bid(bids[j.id]) if j.id in bids else None} for j in arr]}


@app.get('/api/audit')
def audit_log(uid: int = Depends(current)):
    with SessionLocal() as s:
        return {
            'events': [
                {'action': a.action, 'message': a.message, 'created': a.created}
                for a in s.query(Audit).filter_by(user_id=uid).order_by(Audit.id.desc()).limit(80)
            ]
        }


def ingest(s, u, records):
    new = 0
    for raw in records:
        if not raw.get('id'):
            continue
        score = score_for_user(raw, u)
        row = s.query(Job).filter_by(user_id=u.id, remote_id=str(raw['id'])).first()
        if not row:
            row = Job(user_id=u.id, remote_id=str(raw['id']), data='{}')
            s.add(row)
            new += 1
        row.data = json.dumps(raw, ensure_ascii=False)
        row.score = score['score']
        row.decision = score['decision']
        row.reasons = json.dumps(score['reasons'])
    return new


def lock_user(s, uid):
    # Serialize reservations across API processes and worker on SQLite/PostgreSQL.
    s.execute(update(User).where(User.id == uid).values(lock_version=User.lock_version + 1),
              execution_options={'synchronize_session': False})
    s.expire_all()


def require_freelancer_available(connection, user=None):
    """Shared persisted circuit for scans, bids, chats and live-bid refreshes."""
    reason = getattr(connection, 'api_pause_reason', '') or ''
    until = max(int(getattr(connection, 'api_cooldown_until', 0) or 0), int(getattr(user, 'freelancer_cooldown_until', 0) or 0))
    if until > int(time.time()):
        raise HTTPException(429, 'Freelancer API cooldown active. Wait until the displayed time before trying again.')
    if reason and reason != 'rate_limited':
        messages = {
            'auth_rejected': 'Freelancer rejected the token. Reconnect with a verified OAuth token.',
            'access_denied': 'Freelancer denied access. Check account permissions, then reconnect.',
            'uncertain_result': 'A Freelancer result is uncertain. Check the account manually, then reconnect before continuing.',
        }
        raise HTTPException(403, messages.get(reason, 'Freelancer access is paused. Check the account and reconnect.'))


def record_freelancer_failure(uid, connection_id, snapshot, error, *, uncertain=False):
    """Persist a safe failure only for the exact connection that made the call."""
    status = getattr(error, 'status_code', None)
    if status not in (401, 403, 429) and not uncertain:
        return
    with SessionLocal() as s:
        lock_user(s, uid)
        connection = s.get(Connection, connection_id)
        if not connection or connection.user_id != uid or (connection.environment, connection.freelancer_id, connection.token_cipher) != snapshot:
            if status == 429:
                # The request can finish after disconnect/token replacement.
                # Preserve its provider wait without poisoning the new token's
                # authentication or connection-specific pause state.
                retry = getattr(error, 'retry_after', None)
                wait = max(30, int(retry) if isinstance(retry, (int, float)) and math.isfinite(retry) and retry >= 0 else 300)
                user = s.get(User, uid)
                user.freelancer_cooldown_until = max(int(user.freelancer_cooldown_until or 0), int(time.time()) + wait)
                audit(s, uid, 'freelancer_paused', 'rate_limited')
                s.commit()
            return
        if status == 429:
            retry = getattr(error, 'retry_after', None)
            wait = max(30, int(retry) if isinstance(retry, (int, float)) and math.isfinite(retry) and retry >= 0 else 300)
            connection.api_cooldown_until = max(int(connection.api_cooldown_until or 0), int(time.time()) + wait)
            user = s.get(User, uid)
            user.freelancer_cooldown_until = max(int(user.freelancer_cooldown_until or 0), connection.api_cooldown_until)
            # Never replace a stronger authentication/unknown-result pause.
            if not connection.api_pause_reason or connection.api_pause_reason == 'rate_limited':
                connection.api_pause_reason = 'rate_limited'
        else:
            connection.api_pause_reason = {401: 'auth_rejected', 403: 'access_denied'}.get(status, 'uncertain_result')
            connection.api_cooldown_until = 0
            user = s.get(User, uid)
            user.auto_requested = False
            user.auto_chat_greeting = False
        connection.last_error = {
            'auth_rejected': 'OAuth token rejected; reconnect required.',
            'access_denied': 'API access denied; verify permissions and reconnect.',
            'rate_limited': 'API rate limit reached; waiting for the provider cooldown.',
            'uncertain_result': 'Unknown send result; check Freelancer manually before reconnecting.',
        }.get(connection.api_pause_reason, 'Freelancer API paused.')
        audit(s, uid, 'freelancer_paused', connection.api_pause_reason)
        s.commit()


def require_bid_spacing(s, user, connection):
    try:
        advanced = load_advanced(user)
    except AdvancedConfigError:
        raise HTTPException(409, 'Advanced settings are invalid. Save valid settings before bidding.') from None
    now = int(time.time())
    recent = s.query(Bid.updated).filter(Bid.user_id == user.id, Bid.status.in_(['sending', 'submitted', 'uncertain'])).order_by(Bid.updated.desc()).first()
    last = max(int(connection.last_bid_attempt_at or 0), int(recent[0]) if recent else 0)
    if s.query(Bid.id).filter_by(user_id=user.id, status='sending').first():
        raise HTTPException(429, 'Another bid is being sent. Wait for its result before bidding again.')
    if last and now < last + advanced.min_bid_interval_seconds:
        raise HTTPException(429, 'Minimum gap between bids is active; the project remains queued.')
    return now


def require_subscription(s, uid):
    u = s.get(User, uid)
    if not u or not subscription_active(u):
        raise HTTPException(403, 'An active subscription is required')
    return u


def cloud_limit(uid):
    with SessionLocal() as s:
        lock_user(s, uid)
        require_subscription(s, uid)
        now = int(time.time())
        ident = session_digest(f'cloud:{uid}')
        s.query(LoginAttempt).filter(LoginAttempt.attempted_at < now - 900).delete(synchronize_session=False)
        if s.query(LoginAttempt).filter(LoginAttempt.identifier == ident, LoginAttempt.attempted_at >= now - 900).count() >= 60:
            raise HTTPException(429, 'AI request limit reached. Try again in 15 minutes.')
        s.add(LoginAttempt(identifier=ident, attempted_at=now))
        s.commit()


def bid_limits(s, u):
    now = int(time.time())
    consumed = s.query(Bid).filter(Bid.user_id == u.id, Bid.status.in_(['sending', 'submitted', 'uncertain']))
    if consumed.filter(Bid.updated >= now - 3600).count() >= u.max_hourly:
        raise HTTPException(429, 'Hourly bid limit reached')
    if consumed.filter(Bid.updated >= now - 86400).count() >= u.max_daily:
        raise HTTPException(429, 'Daily bid limit reached')
    quota = plan_config(u.plan_tier)['monthly_bid_limit']
    anchor = int(u.billing_period_started or now)
    period_start = anchor + max(0, (now - anchor) // (30 * 86400)) * (30 * 86400)
    if quota and consumed.filter(Bid.updated >= period_start).count() >= quota:
        raise HTTPException(403, 'Your plan bid allowance has been used. Renew or upgrade your subscription.')


def reserve_bid(s, uid, job_id, *, draft=None, intent=None, source='manual'):
    lock_user(s, uid)
    u = require_subscription(s, uid)
    j = owned(s, Job, job_id, uid)
    raw = json.loads(j.data)
    c = s.query(Connection).filter_by(user_id=uid).first()
    if not c or raw.get('demo'):
        raise HTTPException(403, 'Connect a Freelancer account and use a live project')
    require_freelancer_available(c, u)
    if c.environment == 'production' and not ALLOW_PROD:
        raise HTTPException(403, 'Production Freelancer connections are disabled')
    if not re.fullmatch(r'[A-Z]{3}', str(raw.get('currency') or '')) or raw.get('type') not in ('fixed', 'hourly'):
        raise HTTPException(400, 'Project currency or pricing type is missing; scan again')
    if str(raw.get('owner_id') or '') == str(c.freelancer_id):
        raise HTTPException(400, 'You cannot bid on your own project')
    if not raw.get('environment') or not raw.get('account_id'):
        raise HTTPException(409, 'Project predates this connection verification; scan again')
    if raw.get('environment') and raw['environment'] != c.environment:
        raise HTTPException(409, 'Project belongs to a different Freelancer environment; scan again')
    if raw.get('account_id') and str(raw['account_id']) != str(c.freelancer_id):
        raise HTTPException(409, 'Project belongs to a previous Freelancer connection; scan again')
    b = s.query(Bid).filter_by(user_id=uid, job_id=job_id).first()
    if b and b.status in ('sending', 'submitted', 'uncertain'):
        raise HTTPException(409, 'Bid already sent or awaiting manual reconciliation')
    if intent is not None and (not b or b.status != 'ready' or not intent or not secrets.compare_digest(b.intent or '', intent) or b.intent_expiry < int(time.time())):
        raise HTTPException(409, 'Confirmation expired, missing or already used')
    amount = draft.amount if draft else b.amount if b else 0
    if not valid_amount(raw, amount) or not active_project(raw):
        raise HTTPException(400, 'Outside project budget, missing budget, or project closed; scan again')
    if not u.min_budget <= amount <= u.max_bid:
        raise HTTPException(400, 'Bid must respect your configured minimum and maximum amount')
    try:
        blocking = evaluate_filters(raw, u, automatic=source == 'auto')
    except AdvancedConfigError:
        raise HTTPException(409, 'Advanced settings are invalid. Save valid settings before bidding.') from None
    if blocking:
        raise HTTPException(403, 'Project blocked by your configured filters: ' + '; '.join(blocking))
    if source == 'auto':
        allowed, _, expected_amount = auto_eligible(raw, j.score, u, c, PLATFORM_AUTO, b)
        if not allowed:
            raise HTTPException(403, 'Automation settings or project eligibility changed; bid not sent')
        if expected_amount is None or not math.isclose(amount, expected_amount, abs_tol=1e-9):
            raise HTTPException(409, 'Bid price changed while generating proposal; generate again')
    bid_limits(s, u)
    attempt_time = require_bid_spacing(s, u, c)
    if not b:
        b = Bid(user_id=uid, job_id=job_id, proposal='', amount=amount)
        s.add(b)
    if draft:
        b.amount, b.days, b.milestone_pct, b.proposal = draft.amount, draft.days, draft.milestone_pct, draft.proposal.strip()
    if not 30 <= len(b.proposal.strip()) <= 3500:
        raise HTTPException(400, 'Proposal must contain 30 to 3500 characters')
    nonce = secrets.token_urlsafe(32)
    b.status, b.source, b.intent = 'sending', source, nonce
    b.updated = attempt_time
    c.last_bid_attempt_at = attempt_time
    audit(s, uid, 'bid_reserved', f'Project #{j.remote_id}; source={source}')
    s.commit()  # Reserve before network I/O; uncertain requests are never auto-retried.
    return b.id, j.remote_id, c.id, c.environment, c.freelancer_id, c.token_cipher, nonce


def send_reserved(bid_id, remote_id, connection_id, environment, freelancer_id, token_cipher, nonce):
    with SessionLocal() as s:
        b = s.get(Bid, bid_id)
        if not b:
            raise HTTPException(409, 'Bid reservation not found')
        lock_user(s, b.user_id)
        b = s.get(Bid, bid_id)
        changed = s.query(Bid).filter_by(id=bid_id, status='sending', intent=nonce).update({'intent': ''}, synchronize_session=False)
        if changed != 1:
            raise HTTPException(409, 'Bid dispatch has already been claimed')
        u = s.get(User, b.user_id)
        c = s.get(Connection, connection_id)
        j = s.get(Job, b.job_id)
        raw = json.loads(j.data) if j else {}
        reason = ''
        if not u or not subscription_active(u):
            reason = 'Account or subscription is no longer active'
        elif not c or c.user_id != b.user_id or (c.environment, c.freelancer_id, c.token_cipher) != (environment, freelancer_id, token_cipher):
            reason = 'Freelancer connection changed before dispatch'
        elif environment == 'production' and not ALLOW_PROD:
            reason = 'Production bidding is disabled'
        elif not active_project(raw) or not valid_amount(raw, b.amount) or not u.min_budget <= b.amount <= u.max_bid:
            reason = 'Project or bid limits changed before dispatch'
        elif b.source == 'auto' and not auto_eligible(raw, j.score, u, c, PLATFORM_AUTO)[0]:
            reason = 'Automation was paused or project eligibility changed'
        if not reason:
            try:
                require_freelancer_available(c, u)
                if evaluate_filters(raw, u, automatic=b.source == 'auto'):
                    reason = 'Project filters changed before dispatch'
            except HTTPException as exc:
                reason = str(exc.detail)
            except AdvancedConfigError:
                reason = 'Advanced settings are invalid'
        if reason:
            # No external request occurred, so cancellation is known and can be reviewed safely.
            b.status, b.intent = 'cancelled', ''
            audit(s, b.user_id, 'bid_cancelled', f'Project #{remote_id}; {reason}')
            s.commit()
            raise HTTPException(409, reason + '; no bid request was sent')
        # A reservation may wait before dispatch. The gap begins when the
        # outbound attempt starts, not when its earlier reservation was made.
        c.last_bid_attempt_at = int(time.time())
        b.updated = c.last_bid_attempt_at
        s.commit()  # Release DB lock before the external request.
        try:
            snapshot = SimpleNamespace(environment=environment, freelancer_id=freelancer_id, token_cipher=token_cipher)
            result = get_client(snapshot, None).place_bid(int(remote_id), int(freelancer_id), b.proposal, b.amount, b.days, b.milestone_pct)
            remote_bid_id = str(result.get('bid', {}).get('id', '')) if isinstance(result, dict) else ''
            if not remote_bid_id:
                raise FreelancerError('Bid response missing ID; check Freelancer manually')
        except Exception as exc:
            b.status = 'uncertain'
            audit(s, b.user_id, 'bid_uncertain', f'Project #{remote_id}; check Freelancer manually before any retry')
            s.commit()
            record_freelancer_failure(b.user_id, connection_id, (environment, freelancer_id, token_cipher), exc, uncertain=True)
            raise HTTPException(502, 'Freelancer result is uncertain. Check your Freelancer account; this app will not retry automatically.')
        b.status, b.remote_bid_id = 'submitted', remote_bid_id
        audit(s, b.user_id, 'bid_submitted', f'Project #{remote_id}')
        s.commit()
        return {'status': 'submitted', 'bid_id': remote_bid_id}


def run_autobid_cycle(uid):
    if not PLATFORM_AUTO:
        return {'submitted': 0, 'reason': 'Platform auto-bidding disabled'}
    with SessionLocal() as s:
        try:
            user = require_subscription(s, uid)
            advanced = load_advanced(user)
            if not user.auto_requested or not user.auto_admin_allowed:
                return {'submitted': 0, 'reason': 'Auto-bidding is paused in settings'}
            connection = s.query(Connection).filter_by(user_id=uid).first()
            if not connection:
                return {'submitted': 0, 'reason': 'Freelancer account not connected'}
            require_freelancer_available(connection, user)
            require_bid_spacing(s, user, connection)
            if not schedule_allows(advanced):
                return {'submitted': 0, 'reason': 'Outside your configured bidding schedule'}
        except HTTPException as exc:
            return {'submitted': 0, 'reason': str(exc.detail)}
        except AdvancedConfigError:
            return {'submitted': 0, 'reason': 'Advanced settings are invalid; automatic bidding paused'}
        order = (Job.discovered.asc(), Job.id.asc()) if advanced.queue_order == 'fifo' else (Job.score.desc(), Job.discovered.asc(), Job.id.asc())
        final_jobs = s.query(Bid.job_id).filter(Bid.user_id == uid, Bid.status.notin_(['draft', 'ready']))
        # Filter before collecting IDs. A fixed first-100 window would leave
        # newer projects stuck behind already-consumed or ineligible records.
        # Close this read session before any AI generation or reservation write.
        jobs = [row[0] for row in s.query(Job.id).filter(
            Job.user_id == uid, Job.id.notin_(final_jobs),
            Job.decision != 'Skip', Job.score >= int(user.min_score or 65),
        ).order_by(*order).yield_per(200)]
    count = 0
    for job_id in jobs:
        try:
            with SessionLocal() as s:
                u = require_subscription(s, uid)
                c = s.query(Connection).filter_by(user_id=uid).first()
                if not c:
                    return {'submitted': count, 'reason': 'Freelancer account not connected'}
                require_freelancer_available(c, u)
                require_bid_spacing(s, u, c)
                j = owned(s, Job, job_id, uid)
                raw = json.loads(j.data)
                existing = s.query(Bid).filter_by(user_id=uid, job_id=j.id).first()
                allowed, _, amount = auto_eligible(raw, j.score, u, c, PLATFORM_AUTO, existing)
                if not allowed:
                    continue
                bid_limits(s, u)
                # Loaded fields remain available after session closes; no DB write lock during AI I/O.
                days, milestone = u.default_days, u.milestone_pct
            cloud_limit(uid)
            proposal_text = ai_proposal(raw, u)
            draft = DraftIn(amount=amount, days=days, milestone_pct=milestone, proposal=proposal_text)
            with SessionLocal() as s:
                # Re-read settings and atomically reserve after generation; a user can pause meanwhile.
                reservation = reserve_bid(s, uid, job_id, draft=draft, source='auto')
            send_reserved(*reservation)
            count += 1
        except HTTPException as exc:
            if exc.status_code in (401, 403, 429, 502):
                return {'submitted': count, 'reason': str(exc.detail)}
            continue
        except (ValueError, FreelancerError):
            continue
    return {'submitted': count}


def run_autochat_cycle(uid):
    if not PLATFORM_AUTO:
        return {'replied': 0}
    with SessionLocal() as s:
        u = s.get(User, uid)
        conn = s.query(Connection).filter_by(user_id=uid).first()
        if not u or not subscription_active(u) or not u.auto_chat_greeting or not u.auto_admin_allowed or not conn:
            return {'replied': 0}
        if conn.environment == 'production' and not ALLOW_PROD:
            return {'replied': 0}
        try:
            require_freelancer_available(conn, u)
            if not schedule_allows(load_advanced(u)):
                return {'replied': 0, 'reason': 'Outside your configured automation schedule'}
        except (HTTPException, AdvancedConfigError):
            return {'replied': 0, 'reason': 'Freelancer access or advanced configuration is paused'}
        snapshot = (conn.id, conn.environment, conn.freelancer_id, conn.token_cipher)
        try:
            api = get_client(conn, None)
        except FreelancerError:
            return {'replied': 0}
    try:
        res = api.list_threads(limit=15)
    except FreelancerError as exc:
        record_freelancer_failure(uid, snapshot[0], snapshot[1:], exc)
        return {'replied': 0}
    threads = res.get('threads', []) if isinstance(res, dict) else []
    users_map = res.get('users', {}) if isinstance(res, dict) else {}
    if not isinstance(threads, list):
        return {'replied': 0}
    count = 0
    for th in threads:
        if not isinstance(th, dict):
            continue
        thread = th.get('thread') or th
        if not isinstance(thread, dict):
            continue
        tid = str(thread.get('id') or '')
        last_msg = th.get('last_message') or thread.get('last_message') or {}
        if not tid.isdigit() or not isinstance(last_msg, dict):
            continue
        sender_id = str(last_msg.get('from_user') or last_msg.get('sender_id') or '')
        if not sender_id.isdigit() or sender_id == snapshot[2]:
            continue
        ctx = thread.get('context') or {}
        if not isinstance(ctx, dict):
            continue
        project_id = str(ctx.get('id') or '')
        with SessionLocal() as s:
            if s.query(ChatGreeted).filter_by(user_id=uid, thread_id=tid).first():
                continue
            project = s.query(Job).filter_by(user_id=uid, remote_id=project_id).first()
            if not project or not s.query(Bid).filter_by(user_id=uid, job_id=project.id, status='submitted').first():
                continue
            raw = json.loads(project.data)
            if not raw.get('environment') or not raw.get('account_id'):
                continue
            if raw.get('environment') and raw['environment'] != snapshot[1]:
                continue
            if raw.get('account_id') and str(raw['account_id']) != snapshot[2]:
                continue
            title = raw.get('title') or 'your project'
        info = users_map.get(sender_id, users_map.get(int(sender_id), {})) if isinstance(users_map, dict) else {}
        info = info if isinstance(info, dict) else {}
        name = str(info.get('display_name') or info.get('username') or 'there')[:120]
        try:
            cloud_limit(uid)
        except HTTPException:
            return {'replied': count}
        reply = ai_chat_reply(name, title, str(last_msg.get('message') or last_msg.get('body') or '')[:5000], u)
        with SessionLocal() as s:
            lock_user(s, uid)
            current_user = s.get(User, uid)
            current_conn = s.query(Connection).filter_by(user_id=uid).first()
            if (not subscription_active(current_user) or not current_user.auto_chat_greeting
                    or not current_user.auto_admin_allowed or not current_conn
                    or (current_conn.id, current_conn.environment, current_conn.freelancer_id, current_conn.token_cipher) != snapshot):
                return {'replied': count}
            try:
                require_freelancer_available(current_conn, current_user)
                if not schedule_allows(load_advanced(current_user)):
                    return {'replied': count}
            except (HTTPException, AdvancedConfigError):
                return {'replied': count}
            record = ChatGreeted(user_id=uid, thread_id=tid, project_id=project_id, client_name=name, reply_text=reply, status='sending')
            s.add(record)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                continue
            try:
                api.send_chat_message(int(tid), reply)
                record.status = 'submitted'
                audit(s, uid, 'auto_chat_replied', f'Thread #{tid}')
                count += 1
            except FreelancerError as exc:
                record.status = 'uncertain'
                audit(s, uid, 'auto_chat_uncertain', f'Thread #{tid}; check Freelancer manually, no automatic retry')
                s.commit()
                record_freelancer_failure(uid, snapshot[0], snapshot[1:], exc, uncertain=True)
                return {'replied': count, 'reason': 'A send failed; automation paused for account review'}
            s.commit()
    return {'replied': count}


def perform_scan(uid, force=False):
    with SessionLocal() as s:
        lock_user(s, uid)
        u = require_subscription(s, uid)
        c = s.query(Connection).filter_by(user_id=uid).first()
        if not c:
            raise ValueError('Connect your official Freelancer account first in Settings')
        require_freelancer_available(c, u)
        now = int(time.time())
        if c.last_scan and now - c.last_scan < 25:
            raise ValueError('Please wait 25 seconds between rapid scans')
        snapshot = SimpleNamespace(token_cipher=c.token_cipher, environment=c.environment, freelancer_id=c.freelancer_id)
        connection_id = c.id
        c.last_scan = now  # Claim this scan across workers before external I/O.
        s.commit()
        try:
            data = scan_for_user(u, snapshot)
        except FreelancerError as exc:
            lock_user(s, uid)
            c = s.get(Connection, connection_id)
            if c and (c.environment, c.freelancer_id, c.token_cipher) == (snapshot.environment, snapshot.freelancer_id, snapshot.token_cipher):
                c.last_error = str(exc)[:300]
            audit(s, uid, 'scan_failed', str(exc)[:180])
            s.commit()
            record_freelancer_failure(uid, connection_id, (snapshot.environment, snapshot.freelancer_id, snapshot.token_cipher), exc)
            raise
        lock_user(s, uid)
        c = s.get(Connection, connection_id)
        if not c or (c.environment, c.freelancer_id, c.token_cipher) != (snapshot.environment, snapshot.freelancer_id, snapshot.token_cipher):
            raise ValueError('Connection changed during scan; results discarded. Scan again.')
        u = require_subscription(s, uid)
        for raw in data:
            raw['environment'] = c.environment
            raw['account_id'] = c.freelancer_id
        n = ingest(s, u, data)
        c.last_error = ''
        audit(s, uid, 'scan', f'{len(data)} API projects scanned; {n} new matches')
        s.commit()
    auto_res, chat_res = {'submitted': 0}, {'replied': 0}
    if PLATFORM_AUTO:
        try:
            auto_res = run_autobid_cycle(uid)
            chat_res = run_autochat_cycle(uid)
        except Exception:
            log.warning('Automation cycle failed for account %s; inspect account activity', uid)
    return {'ok': True, 'found': len(data), 'new': n,
            'auto_submitted': auto_res.get('submitted', 0), 'chat_replied': chat_res.get('replied', 0)}


@app.post('/api/scan')
def scan(uid: int = Depends(mutate)):
    try:
        return perform_scan(uid)
    except (ValueError, FreelancerError) as e:
        raise HTTPException(400, str(e))


@app.post('/api/autobid/run')
def trigger_autobid_now(uid: int = Depends(mutate)):
    res = run_autobid_cycle(uid)
    chat = run_autochat_cycle(uid)
    return {'ok': True, **res, 'chat_replied': chat.get('replied', 0)}


@app.post('/api/chat/preview')
def preview_chat_greeting(x: ChatPreviewIn, uid: int = Depends(mutate)):
    cloud_limit(uid)
    with SessionLocal() as s:
        require_subscription(s, uid)
        u = s.get(User, uid)
        reply = ai_chat_reply(x.client_name, x.project_title, x.client_message, u)
        return {'reply': reply}


DEMO = [
    ('TikTok Reels & YouTube Shorts Video Editor — Ongoing', '$30 per 20-second clip. We need funny, fast-paced Reels for a custom brand. Dynamic captions, strong 3-second hooks, sound effects and weekly ongoing work.', 'US', True, 30, 80, 'video editing,short-form editing'),
    ('AI Product Video Ads for Skincare Brand (Veo / Runway / ElevenLabs)', 'Create 3 cinematic AI product video ads for Shopify, Instagram and TikTok. 30-45 seconds, realistic AI visuals, voiceover plus polished final edit in Premiere/After Effects.', 'GB', True, 120, 300, 'ai video,video ads,video editing'),
    ('Modern SaaS Dashboard & Mobile App UI/UX Design in Figma', 'Looking for an experienced UI/UX designer to create a clean dark-mode SaaS web app and interactive Figma prototype.', 'US', True, 200, 500, 'ui/ux design,graphic design'),
    ('Real Estate Video Editing — Luxury Dubai Property Tours', 'Need luxury real estate video editing for Dubai villas. Color grading, speed ramps, motion graphics, callout typography and 60-second property tours.', 'AE', True, 100, 250, 'video editing,real estate video'),
    ('Motion Graphics Brand Promo & Logo Animation', 'Animate our brand promo for social media. After Effects, kinetic typography, 20-second vertical ad.', 'AU', True, 85, 200, 'motion graphics,video ads'),
    ('Short video editing (New client - unverified payment)', 'Edit 5 YouTube Shorts, add captions and quick cuts for gaming/entertainment channel.', 'CA', None, 25, 75, 'short-form editing,video editing'),
    ('Telegram upfront investment job', 'Video editing job; pay registration fee on Telegram before starting.', 'CA', False, 10, 20, 'video editing')
]


@app.post('/api/demo')
def demo(uid: int = Depends(mutate)):
    with SessionLocal() as s:
        lock_user(s, uid)
        u = s.get(User, uid)
        t = int(time.time())
        arr = []
        for ix, (title, description, country, verified, lo, hi, skills) in enumerate(DEMO):
            arr.append(dict(
                id='demo-' + str(ix + 1),
                title=title,
                description=description,
                skills=skills.split(','),
                country_code=country,
                country={'US': 'United States', 'GB': 'United Kingdom', 'AU': 'Australia', 'AE': 'UAE', 'CA': 'Canada'}.get(country, ''),
                payment_verified=verified,
                budget_min=lo,
                budget_max=hi,
                currency='USD',
                type='fixed',
                client_reviews=ix * 3 + 2 if verified is not None else None,
                client_rating=4.9 if verified else None,
                posted_at=t - ix * 600,
                client_name='Demo Client',
                url='',
                demo=True,
                project_status='active'
            ))
        num = ingest(s, u, arr)
        audit(s, uid, 'demo_import', f'{num} demo jobs imported')
        s.commit()
    return {'new': num}


@app.post('/api/jobs/{job_id}/proposal')
def proposal(job_id: int, request: Request, uid: int = Depends(mutate)):
    with SessionLocal() as s:
        j = owned(s, Job, job_id, uid)
        u = s.get(User, uid)
        job = json.loads(j.data)
        try:
            proposal_text = make_proposal(job, u)
        except AdvancedConfigError:
            raise HTTPException(400, 'Saved proposal template is invalid for this project. Review your advanced settings.') from None
        return {
            'proposal': proposal_text,
            'suggested_amount': price_for(job, u),
            'suggested_days': getattr(u, 'default_days', 3) or 3,
            'suggested_milestone': getattr(u, 'milestone_pct', 100) or 100
        }


@app.post('/api/jobs/{job_id}/ai-review')
def review_ai(job_id: int, uid: int = Depends(mutate)):
    cloud_limit(uid)
    with SessionLocal() as s:
        require_subscription(s, uid)
        j = owned(s, Job, job_id, uid)
        u = s.get(User, uid)
        return {
            'review': ai_review(json.loads(j.data), u),
            'note': 'BidPilot AI Opportunity & Strategy Analysis'
        }


@app.post('/api/jobs/{job_id}/ai-proposal')
def proposal_ai(job_id: int, uid: int = Depends(mutate)):
    cloud_limit(uid)
    with SessionLocal() as s:
        require_subscription(s, uid)
        j = owned(s, Job, job_id, uid)
        u = s.get(User, uid)
        job = json.loads(j.data)
        try:
            proposal_text = ai_proposal(job, u)
        except AdvancedConfigError:
            raise HTTPException(400, 'Saved proposal template is invalid for this project. Review your advanced settings.') from None
        return {
            'proposal': proposal_text,
            'suggested_amount': price_for(job, u),
            'suggested_days': getattr(u, 'default_days', 3) or 3,
            'suggested_milestone': getattr(u, 'milestone_pct', 100) or 100
        }


@app.post('/api/jobs/{job_id}/draft')
def save_draft(job_id: int, x: DraftIn, uid: int = Depends(mutate)):
    with SessionLocal() as s:
        lock_user(s, uid)
        j = owned(s, Job, job_id, uid)
        raw = json.loads(j.data)
        if not raw.get('demo') and not valid_amount(raw, x.amount):
            raise HTTPException(400, 'Bid amount must fit the project budget in listing currency')
        b = s.query(Bid).filter_by(user_id=uid, job_id=job_id).first()
        if b and b.status in ('sending', 'submitted', 'uncertain'):
            raise HTTPException(409, 'Bid was already submitted or is awaiting reconciliation')
        if not b:
            b = Bid(user_id=uid, job_id=job_id, proposal='', amount=x.amount)
            s.add(b)
        b.amount = x.amount
        b.days = x.days
        b.milestone_pct = x.milestone_pct
        b.proposal = x.proposal
        b.status = 'draft'
        b.updated = int(time.time())
        audit(s, uid, 'draft_saved', str(job_id))
        s.commit()
        return {'ok': True}


def valid_amount(raw, amount):
    try:
        a, b = raw.get('budget_min'), raw.get('budget_max')
        if a is None and b is None:
            return False
        vals = [float(v) for v in (a, b, amount) if v is not None]
        return all(math.isfinite(v) and v >= 0 for v in vals) and amount > 0 and (a is None or amount >= float(a)) and (b is None or amount <= float(b))
    except (TypeError, ValueError):
        return False


def active_project(raw):
    return raw.get('project_status') == 'active'


@app.post('/api/jobs/{job_id}/instant-bid')
def instant_bid(job_id: int, x: InstantBidIn, uid: int = Depends(mutate)):
    with SessionLocal() as s:
        reservation = reserve_bid(s, uid, job_id, draft=x, source='instant_ai')
    return send_reserved(*reservation)


@app.post('/api/jobs/{job_id}/prepare')
def prepare_bid(job_id: int, uid: int = Depends(mutate)):
    with SessionLocal() as s:
        lock_user(s, uid)
        j = owned(s, Job, job_id, uid)
        raw = json.loads(j.data)
        c = s.query(Connection).filter_by(user_id=uid).first()
        if raw.get('demo') or not c:
            raise HTTPException(403, 'Demo jobs cannot submit bids. Connect your own account.')
        b = s.query(Bid).filter_by(user_id=uid, job_id=job_id).first()
        if not b or b.status not in ('draft', 'ready'):
            raise HTTPException(400, 'Save a proposal draft first')
        if not valid_amount(raw, b.amount):
            raise HTTPException(400, 'Bid is outside project budget')
        if not active_project(raw):
            raise HTTPException(400, 'Project is closed')
        b.intent = secrets.token_urlsafe(32)
        b.intent_expiry = int(time.time()) + 600
        b.status = 'ready'
        b.updated = int(time.time())
        audit(s, uid, 'bid_prepared', f'Job {job_id}; {raw.get("currency")} {b.amount}')
        s.commit()
        return {
            'intent': b.intent,
            'preview': {
                'title': raw.get('title'),
                'amount': b.amount,
                'currency': raw.get('currency'),
                'days': b.days,
                'milestone_pct': getattr(b, 'milestone_pct', 100),
                'proposal': b.proposal,
                'environment': c.environment
            }
        }


@app.post('/api/jobs/{job_id}/submit')
def submit_bid(job_id: int, x: SubmitIn, uid: int = Depends(mutate)):
    if x.confirmation != 'PLACE BID':
        raise HTTPException(400, 'Type PLACE BID to confirm')
    with SessionLocal() as s:
        reservation = reserve_bid(s, uid, job_id, intent=x.intent)
    return send_reserved(*reservation)


@app.get('/api/live-bids')
def get_live_bids(uid: int = Depends(current)):
    """Fetches all live bids placed on Freelancer from the official API."""
    with SessionLocal() as s:
        require_subscription(s, uid)
        u = s.get(User, uid)
        conn = s.query(Connection).filter_by(user_id=uid).first()
        if not conn:
            return {'bids': [], 'connected': False, 'message': 'Freelancer account not connected'}
        require_freelancer_available(conn, u)
        try:
            live = fetch_live_bids(u, conn)
            return {'bids': live, 'connected': True}
        except FreelancerError as e:
            snapshot = (conn.environment, conn.freelancer_id, conn.token_cipher)
            connection_id = conn.id
            s.rollback()
            record_freelancer_failure(uid, connection_id, snapshot, e)
            raise HTTPException(502, str(e))

@app.get('/api/stats')
def stats(uid: int = Depends(current)):
    with SessionLocal() as s:
        total = s.query(Job).filter_by(user_id=uid).count()
        strong = s.query(Job).filter_by(user_id=uid).filter(Job.score >= 68).count()
        drafts = s.query(Bid).filter_by(user_id=uid, status='draft').count()
        submitted = s.query(Bid).filter_by(user_id=uid, status='submitted').count()
        auto_sent = s.query(Bid).filter_by(user_id=uid, status='submitted', source='auto').count()
        chat_greeted = s.query(ChatGreeted).filter_by(user_id=uid, status='submitted').count()
        return {'jobs': total, 'strong': strong, 'drafts': drafts, 'submitted': submitted, 'auto_sent': auto_sent, 'chat_greeted': chat_greeted}


from .preferences_api import install_preferences_routes
install_preferences_routes(app, current, mutate)
from .support import install_support_routes
install_support_routes(app, current, mutate)
