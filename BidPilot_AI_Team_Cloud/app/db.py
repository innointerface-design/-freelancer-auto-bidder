import os, json, time, hashlib, hmac, secrets, re, threading, base64, binascii, logging
from contextlib import contextmanager
from urllib.parse import urlparse
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, Text, ForeignKey, UniqueConstraint, event, text, inspect, update
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Test processes provide isolated credentials explicitly. Loading a developer's
# .env here could reintroduce real provider keys or an overriding password_B64.
if os.getenv('APP_ENV', '').strip().lower() != 'test':
    load_dotenv(interpolate=False)
log = logging.getLogger('bidpilot.db')
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB = os.getenv('DATABASE_URL', f'sqlite:///{BASE}/bidpilot_local.db')
# SQLAlchemy 2.1 defaults bare PostgreSQL URLs to psycopg3; this release ships psycopg2.
if DB.startswith(('postgres://', 'postgresql://')):
    DB = 'postgresql+psycopg2://' + DB.split('://', 1)[1]
if os.getenv('RENDER') and DB.startswith('sqlite'):
    raise RuntimeError('Production Render deployment requires persistent Postgres DATABASE_URL')

APP_ENV = os.getenv('APP_ENV', 'development').strip().lower()
SESSION_SECRET = os.getenv('SESSION_SECRET', '')
FERNET_KEY = os.getenv('FERNET_KEY', '')
if len(SESSION_SECRET) < 32 or any(x in SESSION_SECRET.lower() for x in ('set_random', 'changeme', 'change_me', 'example', 'fallback')) or not FERNET_KEY:
    raise RuntimeError('Configure SESSION_SECRET (32+ chars) and FERNET_KEY in .env')

try:
    FERNET = Fernet(FERNET_KEY.encode())
except (ValueError, TypeError):
    raise RuntimeError('FERNET_KEY must be a valid generated Fernet key') from None
SECURE_COOKIES = os.getenv('SECURE_COOKIES', 'true' if APP_ENV == 'production' else 'false').lower() == 'true'
if APP_ENV == 'production':
    public_url = urlparse(os.getenv('PUBLIC_URL', ''))
    if public_url.scheme != 'https' or not public_url.hostname or not SECURE_COOKIES:
        raise RuntimeError('Production requires HTTPS PUBLIC_URL and SECURE_COOKIES=true')
PLATFORM_AUTO = os.getenv('PLATFORM_AUTOBID_APPROVED', 'false').lower() == 'true'
ALLOW_PROD = os.getenv('ALLOW_PRODUCTION_CONNECTION', 'true').lower() == 'true'
SCAN_SECONDS = max(30, int(os.getenv('SCAN_SECONDS', '60')))

engine = create_engine(
    DB,
    connect_args={'check_same_thread': False, 'timeout': 30} if DB.startswith('sqlite') else {'connect_timeout': 15},
    pool_pre_ping=True, hide_parameters=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()

def _private_sqlite_database(dialect, connection_record, connection_args, connection_parameters):
    # SQLite normally inherits the process umask (often 022), exposing customer
    # rows to other local users. Create/chmod the database before sqlite opens
    # it, so the WAL/journal files also inherit the private database mode.
    if os.name == 'nt':
        return
    database_file = connection_args[0] if connection_args else ':memory:'
    if database_file in ('', ':memory:'):
        return
    if connection_parameters.get('uri'):
        raise RuntimeError('Use an ordinary filesystem path for a private SQLite database')
    descriptor = os.open(database_file, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    # A crash may leave sidecar files from an older permissive installation.
    # Tightening the main database does not update existing sidecar modes.
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            sidecar = os.open(database_file + suffix, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            continue
        try:
            os.fchmod(sidecar, 0o600)
        finally:
            os.close(sidecar)


if DB.startswith('sqlite'):
    event.listen(engine, 'do_connect', _private_sqlite_database)

    @event.listens_for(engine, 'connect')
    def _sqlite_settings(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.close()

PLANS = {
    'paid_starter': {'monthly_fee': 10.0, 'monthly_bid_limit': 150},
    'paid_pro': {'monthly_fee': 40.0, 'monthly_bid_limit': 0},
    'free_team': {'monthly_fee': 0.0, 'monthly_bid_limit': 0},
}


def plan_config(tier):
    if tier not in PLANS:
        raise ValueError('Unknown subscription plan')
    return dict(PLANS[tier])


def subscription_active(user, now=None):
    now = int(time.time()) if now is None else int(now)
    if not user or not user.active or user.plan_tier not in PLANS:
        return False
    if user.plan_tier == 'free_team':
        return user.subscription_status == 'active'
    return user.subscription_status in ('active', 'trial') and int(user.subscription_expires or 0) > now


def apply_plan(user, tier, status='active', now=None):
    config = plan_config(tier)
    if status not in ('active', 'trial', 'inactive', 'expired'):
        raise ValueError('Invalid subscription status')
    now = int(time.time()) if now is None else int(now)
    user.plan_tier = tier
    user.monthly_fee = config['monthly_fee']
    user.monthly_bid_limit = config['monthly_bid_limit']
    user.subscription_status = status
    user.subscription_expires = 0 if tier == 'free_team' or status not in ('active', 'trial') else now + 30 * 86400
    user.billing_period_started = now
    return user



def encrypt_secret(raw_val):
    val = (raw_val or '').strip()
    if not val:
        return ''
    return FERNET.encrypt(val.encode('utf-8')).decode('utf-8')


def decrypt_secret(cipher_val):
    if not cipher_val:
        return ''
    try:
        return FERNET.decrypt(cipher_val.encode('utf-8')).decode('utf-8')
    except Exception:
        return ''


def mask_key(raw_val):
    val = (raw_val or '').strip()
    if not val:
        return ''
    if len(val) <= 8:
        return '••••' + val[-2:]
    return val[:4] + '••••••••' + val[-4:]


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String(240), unique=True, nullable=False)
    name = Column(String(100), default='Team member')
    password_hash = Column(String(220), nullable=False)
    role = Column(String(12), default='member')
    active = Column(Boolean, default=True)

    # Free founders are provisioned server-side; all other accounts require payment.
    plan_tier = Column(String(30), default='paid_pro')
    subscription_status = Column(String(30), default='inactive')
    monthly_fee = Column(Float, default=40.0)
    subscription_expires = Column(Integer, default=0)
    billing_period_started = Column(Integer, default=0)
    monthly_bid_limit = Column(Integer, default=0)
    lock_version = Column(Integer, default=0)

    # Encrypted Dual AI Keys (Google Gemini + OpenAI ChatGPT) & Preferred Provider
    preferred_ai = Column(String(30), default='auto')  # auto (Gemini -> ChatGPT -> Smart AI), gemini, openai, smart_builtin
    gemini_key_cipher = Column(Text, default='')
    gemini_model = Column(String(60), default='gemini-3.5-flash-lite')
    openai_key_cipher = Column(Text, default='')
    openai_model = Column(String(60), default='gpt-4.1-mini')

    # Bidman.co Style Profile & Auto-Bidding Filters
    skills = Column(Text, default='video editing, motion graphics, AI video, short-form editing, video ads, UI/UX design')
    search_terms = Column(Text, default='video editor, motion graphics, ai video, tiktok editor, ui ux')
    excluded_keywords = Column(Text, default='telegram, upfront fee, registration fee, adult, gambling')
    countries = Column(Text, default='US,GB,AU,CA,AE,DE,NL,SG')
    portfolio = Column(String(500), default='')
    description = Column(Text, default='Professional Video Editor, AI Video Creator & UI/UX Designer delivering high-converting visuals with fast turnaround.')
    proposal_tone = Column(String(40), default='winning_direct')

    # Pricing, Milestone & Auto-Chat Settings
    bid_strategy = Column(String(30), default='average')  # minimum, average, maximum, competitive_low
    default_days = Column(Integer, default=3)
    milestone_pct = Column(Integer, default=100)
    auto_chat_greeting = Column(Boolean, default=False)
    chat_greeting_template = Column(
        Text,
        default='Hi {client_name}, thanks for reaching out about {project_title}. Could you share the files, scope and preferred deadline for review?'
    )

    allow_hourly = Column(Boolean, default=True)
    allow_any_currency = Column(Boolean, default=True)
    require_payment_verified = Column(Boolean, default=False)
    require_country_match = Column(Boolean, default=False)

    min_score = Column(Integer, default=65)
    min_budget = Column(Float, default=20.0)
    max_bid = Column(Float, default=1500.0)
    max_daily = Column(Integer, default=50)
    max_hourly = Column(Integer, default=15)

    auto_requested = Column(Boolean, default=False)
    auto_admin_allowed = Column(Boolean, default=True)
    updated = Column(Integer, default=lambda: int(time.time()))


class Connection(Base):
    __tablename__ = 'connections'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False)
    token_cipher = Column(Text, nullable=False)
    environment = Column(String(20), default='sandbox')
    freelancer_id = Column(String(80), nullable=False)
    freelancer_name = Column(String(200), default='')
    last_scan = Column(Integer, default=0)
    last_error = Column(Text, default='')
    __table_args__ = (UniqueConstraint('environment', 'freelancer_id', name='uq_freelancer_account_environment'),)


class Job(Base):
    __tablename__ = 'jobs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    remote_id = Column(String(80), nullable=False)
    data = Column(Text, nullable=False)
    score = Column(Integer, default=0)
    decision = Column(String(40), default='Review')
    reasons = Column(Text, default='[]')
    discovered = Column(Integer, default=lambda: int(time.time()))
    __table_args__ = (UniqueConstraint('user_id', 'remote_id'),)


class Bid(Base):
    __tablename__ = 'bids'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    job_id = Column(Integer, ForeignKey('jobs.id'), nullable=False)
    amount = Column(Float, nullable=False)
    days = Column(Integer, default=3)
    milestone_pct = Column(Integer, default=100)
    proposal = Column(Text, nullable=False)
    status = Column(String(30), default='draft')
    source = Column(String(15), default='manual')
    intent = Column(String(70), default='')
    intent_expiry = Column(Integer, default=0)
    remote_bid_id = Column(String(100), default='')
    updated = Column(Integer, default=lambda: int(time.time()))
    __table_args__ = (UniqueConstraint('user_id', 'job_id'),)


class ChatGreeted(Base):
    __tablename__ = 'chat_greeted'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    thread_id = Column(String(80), nullable=False)
    project_id = Column(String(80), default='')
    client_name = Column(String(120), default='')
    reply_text = Column(Text, default='')
    status = Column(String(30), default='sending')
    replied_at = Column(Integer, default=lambda: int(time.time()))
    __table_args__ = (UniqueConstraint('user_id', 'thread_id'),)


class PaymentRequest(Base):
    __tablename__ = 'payment_requests'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    method = Column(String(50), nullable=False)
    transaction_id = Column(String(120), nullable=False)
    amount = Column(Float, default=40.0)
    plan_tier = Column(String(30), default='paid_pro')
    currency = Column(String(15), default='USD')
    sender_phone = Column(String(60), default='')
    notes = Column(Text, default='')
    status = Column(String(30), default='pending')
    created = Column(Integer, default=lambda: int(time.time()))
    reviewed_at = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint('method', 'transaction_id', name='uq_payment_reference'),)


class Audit(Base):
    __tablename__ = 'audits'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String(80), nullable=False)
    message = Column(Text, default='')
    created = Column(Integer, default=lambda: int(time.time()))


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    csrf_hash = Column(String(64), nullable=False)
    expires_at = Column(Integer, nullable=False, index=True)


class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id = Column(Integer, primary_key=True)
    identifier = Column(String(64), nullable=False, index=True)
    attempted_at = Column(Integer, nullable=False, index=True)


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12 to 256 characters')
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    return f'pbkdf2$600000${salt.hex()}${dk.hex()}'


def verify_password(password, stored):
    try:
        algorithm, rounds, salt, digest = stored.split('$')
        rounds = int(rounds)
        if algorithm != 'pbkdf2' or not 100000 <= rounds <= 2000000 or len(password) > 256:
            return False
        if len(bytes.fromhex(salt)) != 16 or len(bytes.fromhex(digest)) != 32:
            return False
        got = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), rounds)
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except (ValueError, AttributeError, TypeError):
        return False


def session_digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def create_auth_session(s, user_id):
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    now = int(time.time())
    s.query(AuthSession).filter(AuthSession.expires_at <= now).delete(synchronize_session=False)
    s.add(AuthSession(token_hash=session_digest(token), user_id=user_id,
                      csrf_hash=session_digest(csrf), expires_at=now + 12 * 3600))
    return token, csrf


def get_auth_session(s, token):
    if not token or len(token) > 256:
        return None
    row = s.get(AuthSession, session_digest(token))
    return row if row and row.expires_at > int(time.time()) else None


def revoke_auth_session(s, token):
    if token:
        s.query(AuthSession).filter_by(token_hash=session_digest(token)).delete(synchronize_session=False)


def audit(s, user_id, action, message=''):
    # Never include API response bodies or credentials in an audit message.
    s.add(Audit(user_id=user_id, action=action, message=str(message)[:500]))


def _auto_migrate_sqlite():
    """Add missing columns for the supplied SQLite or PostgreSQL schema, atomically.

    This is a limited additive migration. It deliberately fails on errors rather
    than starting against a half-migrated database. Back up before upgrades.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    migration_cols = {
        'users': [
            ('plan_tier', "VARCHAR(30) DEFAULT 'paid_pro'"),
            ('subscription_status', "VARCHAR(30) DEFAULT 'inactive'"),
            ('monthly_fee', 'FLOAT DEFAULT 40.0'),
            ('subscription_expires', 'INTEGER DEFAULT 0'),
            ('billing_period_started', 'INTEGER DEFAULT 0'),
            ('monthly_bid_limit', 'INTEGER DEFAULT 0'),
            ('lock_version', 'INTEGER DEFAULT 0'),
            ('preferred_ai', "VARCHAR(30) DEFAULT 'auto'"),
            ('gemini_key_cipher', "TEXT DEFAULT ''"),
            ('gemini_model', "VARCHAR(60) DEFAULT 'gemini-3.5-flash-lite'"),
            ('openai_key_cipher', "TEXT DEFAULT ''"),
            ('openai_model', "VARCHAR(60) DEFAULT 'gpt-4.1-mini'"),
            ('excluded_keywords', "TEXT DEFAULT 'telegram, upfront fee, registration fee, adult, gambling'"),
            ('proposal_tone', "VARCHAR(40) DEFAULT 'winning_direct'"),
            ('bid_strategy', "VARCHAR(30) DEFAULT 'average'"),
            ('default_days', 'INTEGER DEFAULT 3'),
            ('milestone_pct', 'INTEGER DEFAULT 100'),
            ('auto_chat_greeting', 'BOOLEAN DEFAULT FALSE'),
            ('chat_greeting_template', "TEXT DEFAULT ''"),
            ('allow_hourly', 'BOOLEAN DEFAULT TRUE'),
            ('allow_any_currency', 'BOOLEAN DEFAULT TRUE'),
            ('require_payment_verified', 'BOOLEAN DEFAULT FALSE'),
            ('require_country_match', 'BOOLEAN DEFAULT FALSE'),
            ('max_hourly', 'INTEGER DEFAULT 15'),
        ],
        'bids': [('milestone_pct', 'INTEGER DEFAULT 100')],
        'payment_requests': [('plan_tier', "VARCHAR(30) DEFAULT 'paid_pro'")],
        'chat_greeted': [('status', "VARCHAR(30) DEFAULT 'unknown'")],
    }
    with engine.begin() as conn:
        for table_name, columns in migration_cols.items():
            if table_name not in existing_tables:
                continue
            existing = {c['name'] for c in insp.get_columns(table_name)}
            for column_name, definition in columns:
                if column_name not in existing:
                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}'))
        # Normalize legacy prices without giving unpaid legacy accounts access.
        conn.execute(text("UPDATE users SET monthly_fee=40.0 WHERE plan_tier='paid_pro'"))
        conn.execute(text("UPDATE users SET monthly_fee=10.0, monthly_bid_limit=150 WHERE plan_tier='paid_starter'"))
        conn.execute(text("UPDATE users SET monthly_fee=0.0 WHERE plan_tier='free_team'"))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_reference_idx ON payment_requests (method, transaction_id)'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_freelancer_account_environment_idx ON connections (environment, freelancer_id)'))
        # Legacy active paid plans have no verified expiry; admin must verify them.
        conn.execute(text("UPDATE users SET subscription_status='inactive' WHERE plan_tier!='free_team' AND (subscription_expires IS NULL OR subscription_expires=0)"))


def _valid_email(value):
    return isinstance(value, str) and len(value) <= 240 and bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value))


_init_thread_lock = threading.RLock()


@contextmanager
def _database_initialization_lock():
    """Serialize schema/bootstrap work across web and worker processes.

    PostgreSQL uses a session advisory lock because metadata DDL and bootstrap
    use multiple database connections. Local SQLite uses an adjacent private
    lock file, including on Windows. Existing lock files must not be removed
    while any app process is running.
    """
    with _init_thread_lock:
        if engine.dialect.name == 'postgresql':
            with engine.connect() as connection:
                lock_id = 0x42494450494C4F54
                connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': lock_id})
                try:
                    yield
                finally:
                    try:
                        connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_id})
                        connection.commit()
                    except Exception:
                        # Never return a pooled session holding an advisory lock.
                        connection.invalidate()
                        raise
        elif engine.dialect.name == 'sqlite' and engine.url.database not in (None, '', ':memory:'):
            lock_path = os.path.abspath(engine.url.database) + '.init.lock'
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(descriptor, 'a+b') as handle:
                if os.name == 'nt':
                    import msvcrt
                    if os.fstat(handle.fileno()).st_size == 0:
                        handle.write(b'\0')
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            yield


def init_db():
    with _database_initialization_lock():
        _initialize_database()


def _bootstrap_password(variable):
    """Decode the setup utility's exact UTF-8 representation; preserve legacy envs.

    Base64 prevents dotenv/Compose escaping differences. It is encoding, not
    encryption: protect the .env just like a plaintext password file.
    """
    encoded_variable = variable + '_B64'
    if encoded_variable not in os.environ:
        return os.getenv(variable, '')
    try:
        return base64.b64decode(os.environ[encoded_variable], validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise RuntimeError(f'{encoded_variable} must be valid base64-encoded UTF-8') from None


def _optional_founder_warning():
    # Configuration values can include personal data or credentials. Keep this
    # message generic and never log the conflicting address or password.
    log.warning('Optional founder provisioning skipped. Manage founder accounts through Admin; existing accounts were preserved.')


def _provision_optional_founder(s, owner_email):
    second_email = os.getenv('SECOND_FOUNDER_EMAIL', '').strip().lower()
    if not second_email:
        return
    if not _valid_email(second_email) or second_email == owner_email:
        _optional_founder_warning()
        return
    existing = s.query(User).filter_by(email=second_email).first()
    if existing:
        # Setup variables are a create-once hint, never an authority to change
        # an existing customer's role, plan, credentials or subscription.
        if existing.plan_tier != 'free_team':
            _optional_founder_warning()
        return
    owner = s.query(User).filter_by(role='admin').first()
    if not owner:
        _optional_founder_warning()
        return
    # The Admin API uses this same owner-row update when assigning free seats.
    # Share its transaction lock before counting, including on PostgreSQL where
    # the global bootstrap advisory lock does not cover normal API requests.
    s.execute(update(User).where(User.id == owner.id).values(lock_version=User.lock_version + 1),
              execution_options={'synchronize_session': False})
    if s.query(User).filter_by(plan_tier='free_team').count() >= 2:
        _optional_founder_warning()
        return
    try:
        second_password = _bootstrap_password('SECOND_FOUNDER_PASSWORD')
        if not 12 <= len(second_password) <= 256 or second_password.startswith('SET_'):
            _optional_founder_warning()
            return
        founder_hash = hash_password(second_password)
    except (RuntimeError, ValueError, UnicodeError):
        _optional_founder_warning()
        return
    try:
        # A simultaneous signup may claim this email after the existence check.
        # Isolate an optional insert conflict so an otherwise valid owner can
        # still commit and the web/worker startup can complete safely.
        with s.begin_nested():
            founder = User(email=second_email, name='Co-founder',
                           password_hash=founder_hash, role='member',
                           auto_requested=False, auto_admin_allowed=True)
            apply_plan(founder, 'free_team')
            s.add(founder)
            s.flush()
    except IntegrityError:
        _optional_founder_warning()


def _initialize_database():
    Base.metadata.create_all(engine)
    _auto_migrate_sqlite()
    email = os.getenv('ADMIN_EMAIL', '').strip().lower()
    with SessionLocal() as s:
        if not s.query(User).filter_by(role='admin').first():
            # Mandatory owner bootstrap remains fail-closed. Obsolete bootstrap
            # secrets are not parsed after an owner has already been created.
            password = _bootstrap_password('ADMIN_PASSWORD')
            if not _valid_email(email) or not 12 <= len(password) <= 256 or password.startswith('SET_'):
                raise RuntimeError('Set ADMIN_EMAIL and a unique ADMIN_PASSWORD (12-256 chars)')
            if s.query(User).filter_by(email=email).first():
                raise RuntimeError('ADMIN_EMAIL already belongs to a member; use a separate owner email')
            if s.query(User).filter_by(plan_tier='free_team').count() >= 2:
                raise RuntimeError('Two free founder seats already exist; provision the owner manually')
            owner = User(email=email, name='Administrator (Owner)',
                         password_hash=hash_password(password), role='admin',
                         auto_requested=False, auto_admin_allowed=True)
            apply_plan(owner, 'free_team')
            s.add(owner)
            s.flush()
        _provision_optional_founder(s, email)
        s.commit()


def public_user(u, connection=None):
    gem_raw = decrypt_secret(getattr(u, 'gemini_key_cipher', ''))
    oai_raw = decrypt_secret(getattr(u, 'openai_key_cipher', ''))
    return dict(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        active=u.active,
        plan_tier=getattr(u, 'plan_tier', 'paid_pro'),
        subscription_status=('expired' if u.subscription_status in ('active', 'trial') and not subscription_active(u) else u.subscription_status),
        subscription_active=subscription_active(u),
        subscription_expires=u.subscription_expires,
        billing_period_started=u.billing_period_started,
        monthly_bid_limit=u.monthly_bid_limit,
        monthly_fee=getattr(u, 'monthly_fee', 40.0),
        preferred_ai=getattr(u, 'preferred_ai', 'auto'),
        gemini_model=getattr(u, 'gemini_model', 'gemini-3.5-flash-lite') or 'gemini-3.5-flash-lite',
        openai_model=getattr(u, 'openai_model', 'gpt-4.1-mini') or 'gpt-4.1-mini',
        has_gemini_key=bool(gem_raw or os.getenv('GEMINI_API_KEY', '').strip()),
        gemini_key_masked=mask_key(gem_raw),
        has_openai_key=bool(oai_raw or os.getenv('OPENAI_API_KEY', '').strip()),
        openai_key_masked=mask_key(oai_raw),
        skills=u.skills,
        search_terms=u.search_terms,
        excluded_keywords=getattr(u, 'excluded_keywords', ''),
        countries=u.countries,
        portfolio=u.portfolio,
        description=u.description,
        proposal_tone=getattr(u, 'proposal_tone', 'winning_direct'),
        bid_strategy=getattr(u, 'bid_strategy', 'average'),
        default_days=getattr(u, 'default_days', 3),
        milestone_pct=getattr(u, 'milestone_pct', 100),
        auto_chat_greeting=getattr(u, 'auto_chat_greeting', False),
        chat_greeting_template=getattr(u, 'chat_greeting_template', ''),
        allow_hourly=getattr(u, 'allow_hourly', True),
        allow_any_currency=getattr(u, 'allow_any_currency', True),
        require_payment_verified=getattr(u, 'require_payment_verified', False),
        require_country_match=getattr(u, 'require_country_match', False),
        min_score=u.min_score,
        min_budget=u.min_budget,
        max_bid=u.max_bid,
        max_daily=u.max_daily,
        max_hourly=getattr(u, 'max_hourly', 15),
        auto_requested=u.auto_requested,
        auto_admin_allowed=u.auto_admin_allowed,
        connected=bool(connection),
        freelancer_name=connection.freelancer_name if connection else '',
        environment=connection.environment if connection else '',
        last_scan=connection.last_scan if connection else 0,
        last_error=connection.last_error if connection else ''
    )
