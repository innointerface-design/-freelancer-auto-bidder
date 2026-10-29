"""Regression checks for authentication, encryption and billing persistence."""
import hashlib
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/database.db')
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setenv('ADMIN_EMAIL', 'owner@example.test')
    monkeypatch.setenv('ADMIN_PASSWORD', 'owner-unique-passphrase-123')
    monkeypatch.delenv('SECOND_FOUNDER_EMAIL', raising=False)
    monkeypatch.delenv('SECOND_FOUNDER_PASSWORD', raising=False)
    yield factory
    engine.dispose()


def test_password_salts_and_rejects_tampered_hashes():
    password = 'a-long-unique-passphrase'
    first, second = db.hash_password(password), db.hash_password(password)
    assert first != second
    assert db.verify_password(password, first)
    assert not db.verify_password(password + 'x', first)
    assert not db.verify_password(password, first.replace('pbkdf2', 'unsupported'))
    assert not db.verify_password(password, 'pbkdf2$999999999999$00$00')
    with pytest.raises(ValueError):
        db.hash_password('short')
    # Existing 350k-round hashes remain usable after the work-factor increase.
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 350000)
    assert db.verify_password(password, f'pbkdf2$350000${salt.hex()}${digest.hex()}')


def test_secret_encryption_is_nondeterministic_and_fails_closed():
    secret = 'freelancer-test-credential'
    encrypted = db.encrypt_secret(secret)
    assert encrypted != db.encrypt_secret(secret)
    assert secret not in encrypted
    assert db.decrypt_secret(encrypted) == secret
    assert db.decrypt_secret(encrypted[:-4] + 'xxxx') == ''
    assert db.decrypt_secret('plaintext-from-an-old-db') == ''


def test_free_founders_are_provisioned_once_and_only_owner_is_admin(isolated_db, monkeypatch):
    monkeypatch.setenv('SECOND_FOUNDER_EMAIL', 'founder@example.test')
    monkeypatch.setenv('SECOND_FOUNDER_PASSWORD', 'founder-unique-passphrase-123')
    db.init_db()
    db.init_db()
    with isolated_db() as s:
        users = s.query(db.User).order_by(db.User.id).all()
        assert len(users) == 2
        assert [u.role for u in users] == ['admin', 'member']
        assert all(u.plan_tier == 'free_team' and u.monthly_fee == 0 for u in users)
        assert all(not u.auto_requested for u in users)


def test_founder_config_cannot_take_over_an_existing_paid_member(isolated_db, monkeypatch):
    db.init_db()
    with isolated_db() as s:
        s.add(db.User(email='claimed@example.test', name='Customer',
                      password_hash=db.hash_password('customer-long-password')))
        s.commit()
    monkeypatch.setenv('SECOND_FOUNDER_EMAIL', 'claimed@example.test')
    monkeypatch.setenv('SECOND_FOUNDER_PASSWORD', 'new-password-never-used')
    with pytest.raises(RuntimeError, match='already belongs to a paid member'):
        db.init_db()
    with isolated_db() as s:
        customer = s.query(db.User).filter_by(email='claimed@example.test').one()
        assert customer.role == 'member' and customer.plan_tier != 'free_team'
        assert db.verify_password('customer-long-password', customer.password_hash)


def test_subscription_expiry_and_fixed_prices():
    user = db.User(active=True)
    db.apply_plan(user, 'paid_starter', now=100)
    assert user.monthly_fee == 10 and user.monthly_bid_limit == 150
    assert db.subscription_active(user, now=101)
    assert not db.subscription_active(user, now=100 + 30 * 86400)
    db.apply_plan(user, 'paid_pro', now=200)
    assert user.monthly_fee == 40 and user.monthly_bid_limit == 0
    db.apply_plan(user, 'free_team', now=300)
    assert db.subscription_active(user, now=10**10)
    user.active = False
    assert not db.subscription_active(user)
    with pytest.raises(ValueError):
        db.apply_plan(user, 'free-forever-for-anyone')


def test_session_tokens_hashed_expired_and_revoked(isolated_db):
    db.init_db()
    with isolated_db() as s:
        user = s.query(db.User).first()
        token, csrf = db.create_auth_session(s, user.id)
        s.commit()
        stored = s.query(db.AuthSession).one()
        assert stored.token_hash != token
        assert stored.csrf_hash != csrf
        assert db.get_auth_session(s, token).user_id == user.id
        assert db.get_auth_session(s, token + 'x') is None
        stored.expires_at = int(time.time()) - 1
        s.commit()
        assert db.get_auth_session(s, token) is None
        db.revoke_auth_session(s, token)
        s.commit()
        assert s.query(db.AuthSession).count() == 0


def test_duplicate_payment_reference_rejected_in_database(isolated_db):
    db.init_db()
    with isolated_db() as s:
        owner = s.query(db.User).first()
        first = db.PaymentRequest(user_id=owner.id, method='bank', transaction_id='same-reference')
        s.add(first)
        s.commit()
        s.add(db.PaymentRequest(user_id=owner.id, method='bank', transaction_id='same-reference'))
        with pytest.raises(IntegrityError):
            s.commit()


def test_existing_legacy_subscription_does_not_get_permanent_paid_access(isolated_db):
    db.init_db()
    with isolated_db() as s:
        s.add(db.User(email='legacy@example.test', name='Legacy',
                      password_hash=db.hash_password('legacy-password-long'),
                      plan_tier='paid_pro', subscription_status='active', monthly_fee=29,
                      subscription_expires=0))
        s.commit()
    db.init_db()
    with isolated_db() as s:
        legacy = s.query(db.User).filter_by(email='legacy@example.test').one()
        assert legacy.monthly_fee == 40
        assert legacy.subscription_status == 'inactive'
        assert not db.subscription_active(legacy)


def test_public_user_does_not_show_server_key_fragments(isolated_db, monkeypatch):
    db.init_db()
    monkeypatch.setenv('GEMINI_API_KEY', 'private-server-only-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-server-only-secret')
    with isolated_db() as s:
        user = s.query(db.User).first()
        public = db.public_user(user)
        assert public['has_gemini_key'] and public['has_openai_key']
        assert public['gemini_key_masked'] == public['openai_key_masked'] == ''


def test_fresh_database_bootstrap_serialized_across_processes(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from sqlalchemy import create_engine, text

    database = tmp_path / 'concurrent-bootstrap.db'
    environment = os.environ.copy()
    environment['DATABASE_URL'] = f'sqlite:///{database}'
    environment.pop('SECOND_FOUNDER_EMAIL', None)
    environment.pop('SECOND_FOUNDER_PASSWORD', None)
    code = 'from app.db import init_db; init_db()'
    processes = [subprocess.Popen([sys.executable, '-c', code], cwd=str(Path(__file__).parents[1]),
                                  env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(4)]
    for process in processes:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, error
    engine = create_engine(f'sqlite:///{database}')
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one() == 1
        lock_path = Path(str(database) + '.init.lock')
        assert lock_path.exists()
        if os.name != 'nt':
            assert lock_path.stat().st_mode & 0o777 == 0o600
    finally:
        engine.dispose()


def test_bootstrap_password_base64_preserves_exact_utf8_and_legacy_fallback(monkeypatch):
    import base64
    value = 'literal-${DOLLARS}\\\\slashes-اردو-password'
    monkeypatch.delenv('ADMIN_PASSWORD_B64', raising=False)
    monkeypatch.setenv('ADMIN_PASSWORD', value)
    assert db._bootstrap_password('ADMIN_PASSWORD') == value
    monkeypatch.setenv('ADMIN_PASSWORD_B64', base64.b64encode(value.encode('utf-8')).decode('ascii'))
    monkeypatch.setenv('ADMIN_PASSWORD', 'ignored-legacy-value')
    assert db._bootstrap_password('ADMIN_PASSWORD') == value
    monkeypatch.setenv('SECOND_FOUNDER_PASSWORD_B64', '')
    monkeypatch.setenv('SECOND_FOUNDER_PASSWORD', 'ignored-fallback')
    assert db._bootstrap_password('SECOND_FOUNDER_PASSWORD') == ''


@pytest.mark.parametrize('encoded', ['not valid base64 secret', '/w==', 'é'])
def test_bootstrap_invalid_base64_never_discloses_value(monkeypatch, encoded):
    monkeypatch.setenv('ADMIN_PASSWORD_B64', encoded)
    with pytest.raises(RuntimeError) as error:
        db._bootstrap_password('ADMIN_PASSWORD')
    assert encoded not in str(error.value)
    assert 'ADMIN_PASSWORD_B64' in str(error.value)


def test_freelancer_identity_unique_within_environment(isolated_db):
    db.init_db()
    with isolated_db() as s:
        owner = s.query(db.User).first()
        member = db.User(email='account-owner@example.test', name='Customer',
                         password_hash=db.hash_password('customer-password-123'))
        s.add(member)
        s.flush()
        s.add(db.Connection(user_id=owner.id, token_cipher=db.encrypt_secret('test-token-one'),
                            environment='sandbox', freelancer_id='123'))
        s.commit()
        s.add(db.Connection(user_id=member.id, token_cipher=db.encrypt_secret('test-token-two'),
                            environment='sandbox', freelancer_id='123'))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        # Numeric identities may overlap between the separate live/sandbox services.
        s.add(db.Connection(user_id=member.id, token_cipher=db.encrypt_secret('test-token-two'),
                            environment='production', freelancer_id='123'))
        s.commit()
        assert s.query(db.Connection).count() == 2


def test_legacy_duplicate_freelancer_accounts_fail_migration_closed(isolated_db):
    from sqlalchemy import text
    db.init_db()
    with db.engine.begin() as connection:
        connection.execute(text('DROP TABLE connections'))
        connection.execute(text('''CREATE TABLE connections (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, token_cipher TEXT NOT NULL,
            environment VARCHAR(20), freelancer_id VARCHAR(80), freelancer_name VARCHAR(200),
            last_scan INTEGER, last_error TEXT)'''))
        connection.execute(text("INSERT INTO connections (id,user_id,token_cipher,environment,freelancer_id) VALUES (1,1,'encrypted','sandbox','123'),(2,2,'encrypted','sandbox','123')"))
    with pytest.raises(IntegrityError):
        db.init_db()


@pytest.mark.parametrize('scheme', ['postgres', 'postgresql', 'postgresql+psycopg2'])
def test_postgres_urls_use_installed_driver(scheme):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'DATABASE_URL': scheme + '://testuser:testpassword@127.0.0.1:5432/testdb'}
    # Import creates the engine but never connects; reproduces production driver loading.
    result = subprocess.run([sys.executable, '-c', 'from app.db import engine; print(engine.url.drivername)'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'postgresql+psycopg2'


def test_sqlite_database_and_wal_private_under_permissive_umask(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    if os.name == 'nt':
        pytest.skip('Windows uses account ACLs instead of POSIX mode bits')
    database = tmp_path / 'private-database.db'
    environment = os.environ.copy()
    environment['DATABASE_URL'] = f'sqlite:///{database}'
    code = '''
import os
os.umask(0o022)
from app.db import init_db, engine
from sqlalchemy import text
init_db()
with engine.connect() as connection:
    connection.execute(text('SELECT count(*) FROM users'))
    path = engine.url.database
    for suffix in ('', '-wal', '-shm'):
        filename = path + suffix
        if os.path.exists(filename):
            assert os.stat(filename).st_mode & 0o077 == 0, suffix
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=str(Path(__file__).parents[1]),
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert database.stat().st_mode & 0o777 == 0o600


def test_existing_sqlite_permissions_are_tightened_and_symlinks_rejected(tmp_path):
    import os
    if os.name == 'nt':
        pytest.skip('Windows uses account ACLs instead of POSIX mode bits')
    path = tmp_path / 'legacy.db'
    path.write_bytes(b'')
    path.chmod(0o666)
    sidecars = [tmp_path / ('legacy.db' + suffix) for suffix in ('-wal', '-shm', '-journal')]
    for sidecar in sidecars:
        sidecar.write_bytes(b'')
        sidecar.chmod(0o666)
    db._private_sqlite_database(None, None, [str(path)], {})
    assert path.stat().st_mode & 0o777 == 0o600
    assert all(sidecar.stat().st_mode & 0o777 == 0o600 for sidecar in sidecars)
    symlink = tmp_path / 'redirect.db'
    symlink.symlink_to(path)
    with pytest.raises(OSError):
        db._private_sqlite_database(None, None, [str(symlink)], {})
    with pytest.raises(RuntimeError, match='ordinary filesystem path'):
        db._private_sqlite_database(None, None, ['file:legacy.db?uri=true'], {'uri': True})


def test_chat_reservation_status_migration_preserves_legacy_unknown(isolated_db):
    from sqlalchemy import text
    db.init_db()
    with db.engine.begin() as connection:
        connection.execute(text('DROP TABLE chat_greeted'))
        connection.execute(text('''CREATE TABLE chat_greeted (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, thread_id VARCHAR(80),
            project_id VARCHAR(80), client_name VARCHAR(120), reply_text TEXT, replied_at INTEGER)'''))
        connection.execute(text("INSERT INTO chat_greeted(id,user_id,thread_id,reply_text) VALUES (1,1,'legacy-thread','legacy-text')"))
    db.init_db()
    with isolated_db() as s:
        legacy = s.get(db.ChatGreeted, 1)
        assert legacy.status == 'unknown'
        reservation = db.ChatGreeted(user_id=1, thread_id='new-thread')
        s.add(reservation)
        s.commit()
        assert reservation.status == 'sending'
