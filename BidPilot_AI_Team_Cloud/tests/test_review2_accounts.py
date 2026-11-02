"""PostgreSQL-compatible account inputs without changing existing passwords."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg2.extensions import adapt
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main


PASSWORD = 'Account-review-password-123!'
PRIVATE_TEXT = 'private-input\x00must-not-be-echoed'


@pytest.fixture
def state(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/accounts.db',
                           connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with TestClient(main.app) as client:
        yield client, sessions
    engine.dispose()


@pytest.mark.parametrize(('model', 'payload', 'field'), [
    (main.LoginIn, {'email': 'member@example.test', 'password': PASSWORD}, 'email'),
    (main.RegisterIn, {'email': 'member@example.test', 'name': 'New member', 'password': PASSWORD}, 'name'),
    (main.MemberIn, {'email': 'member@example.test', 'name': 'New member', 'password': PASSWORD}, 'name'),
    (main.PaymentSubmitIn, {'method': 'bank', 'transaction_id': 'receipt-123', 'amount': 10}, 'transaction_id'),
    (main.PaymentSubmitIn, {'method': 'bank', 'transaction_id': 'receipt-123', 'amount': 10}, 'sender_phone'),
    (main.PaymentSubmitIn, {'method': 'bank', 'transaction_id': 'receipt-123', 'amount': 10}, 'notes'),
    (main.AIKeysIn, {}, 'gemini_model'),
    (main.AIKeysIn, {}, 'openai_model'),
    (main.DraftIn, {'amount': 50, 'days': 3, 'proposal': 'A detailed project proposal that meets the length limit.'}, 'proposal'),
])
def test_postgres_invalid_text_is_rejected_before_database_access(model, payload, field):
    value = PRIVATE_TEXT + '@example.test'
    # This is the actual PostgreSQL driver failure, without a live database.
    with pytest.raises(ValueError, match='NUL'):
        adapt(value).getquoted()
    with pytest.raises(ValidationError) as error:
        model.model_validate({**payload, field: value})
    assert error.value.errors()[0]['loc'] == (field,)


def test_clean_email_also_rejects_nul_for_direct_callers():
    with pytest.raises(HTTPException) as error:
        main.clean_email('member\x00@example.test')
    assert error.value.status_code == 400


@pytest.mark.parametrize(('path', 'payload'), [
    ('/api/login', {'email': PRIVATE_TEXT + '@example.test', 'password': PASSWORD}),
    ('/api/register', {'email': 'member@example.test', 'name': PRIVATE_TEXT, 'password': PASSWORD}),
])
def test_bad_account_input_returns_redacted_422_without_creating_state(state, path, payload):
    client, sessions = state
    with sessions() as session:
        before = (session.query(db.User).count(), session.query(db.LoginAttempt).count(),
                  session.query(db.AuthSession).count(), session.query(db.Audit).count())
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert 'private-input' not in response.text and 'must-not-be-echoed' not in response.text
    with sessions() as session:
        assert (session.query(db.User).count(), session.query(db.LoginAttempt).count(),
                session.query(db.AuthSession).count(), session.query(db.Audit).count()) == before


def test_settings_nul_is_rejected_without_partial_save(state):
    client, sessions = state
    assert client.post('/api/login', json={
        'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!',
    }).status_code == 200
    headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
    with sessions() as session:
        user = session.query(db.User).filter_by(email='owner@example.test').one()
        payload = {key: getattr(user, key) for key in main.SettingsIn.model_fields}
        before = (user.name, user.description, user.updated)
    response = client.post('/api/settings', headers=headers, json={
        **payload, 'name': 'Uncommitted edit', 'description': PRIVATE_TEXT,
    })
    assert response.status_code == 422
    assert 'private-input' not in response.text
    with sessions() as session:
        user = session.query(db.User).filter_by(email='owner@example.test').one()
        assert (user.name, user.description, user.updated) == before


def test_nul_in_existing_exact_password_remains_supported(state):
    client, sessions = state
    password = 'Literal\x00secret-password-123! '
    assert client.post('/api/register', json={
        'email': 'exact-password@example.test', 'name': 'Exact password', 'password': password,
    }).status_code == 200
    with sessions() as session:
        user = session.query(db.User).filter_by(email='exact-password@example.test').one()
        assert db.verify_password(password, user.password_hash)
    client.cookies.clear()
    assert client.post('/api/login', json={
        'email': 'exact-password@example.test', 'password': password,
    }).status_code == 200
    client.cookies.clear()
    assert client.post('/api/login', json={
        'email': 'exact-password@example.test', 'password': password.replace('\x00', ''),
    }).status_code == 401


@pytest.mark.parametrize('model', [main.RegisterIn, main.MemberIn])
def test_new_accounts_require_a_nonblank_password(model):
    with pytest.raises(ValidationError):
        model(email='new@example.test', name='New account', password=' ' * 12)


def test_legacy_whitespace_password_is_not_changed_by_login_validation():
    value = ' ' * 12
    assert main.LoginIn(email='legacy@example.test', password=value).password == value
