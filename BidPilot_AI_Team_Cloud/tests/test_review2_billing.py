"""A valid Unicode settings export must be restorable without loosening the size cap."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main, preferences_api


@pytest.fixture
def backup_client(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/backup.db', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with TestClient(main.app) as client:
        response = client.post('/api/login', json={
            'email': 'owner@example.test', 'password': 'Test-only-owner-password-123!',
        })
        assert response.status_code == 200
        headers = {'x-csrf-token': client.get('/api/csrf').json()['csrf']}
        yield client, headers, sessions
    engine.dispose()


def full_unicode_settings():
    # Four-byte Unicode characters are valid proposal/keyword content. All
    # templates and keyword lists reach the accepted model limits together.
    return {
        'default_template': '\U0001f680' * 2800,
        'manual_review_keywords': ['\U0001f680' * 98 + f'{i:02}' for i in range(50)],
        'keyword_templates': [{
            'name': '\U0001f680' * 58 + f'{i:02}',
            'keywords': ['\U0001f680' * 98 + f'{j:02}' for j in range(30)],
            'template': '\U0001f680' * 2800,
        } for i in range(10)],
    }


def test_largest_unicode_template_collections_round_trip_through_own_backup(backup_client):
    client, headers, sessions = backup_client
    values = full_unicode_settings()
    saved = client.post('/api/advanced-settings', headers=headers, json=values)
    assert saved.status_code == 200, saved.text
    exported = client.get('/api/settings/export')
    assert exported.status_code == 200
    # Match the browser's JSON.stringify(..., null, 2) download representation.
    backup = json.dumps(exported.json(), ensure_ascii=False, indent=2).encode('utf-8')
    assert 128 * 1024 < len(backup) <= preferences_api.MAX_BACKUP_BYTES
    assert client.post('/api/advanced-settings', headers=headers, json={}).status_code == 200
    restored = client.post('/api/settings/import', headers=headers, content=backup)
    assert restored.status_code == 200, restored.text
    assert restored.json()['settings'] == saved.json()['settings']
    with sessions() as session:
        user = session.query(db.User).filter_by(role='admin').one()
        assert json.loads(user.advanced_config) == saved.json()['settings']
        assert not user.auto_requested and not user.auto_chat_greeting


def test_oversized_backup_is_rejected_without_mutation(backup_client):
    client, headers, sessions = backup_client
    with sessions() as session:
        user = session.query(db.User).filter_by(role='admin').one()
        before = (user.name, user.advanced_config, session.query(db.Audit).count())
    result = client.post('/api/settings/import', headers=headers,
                         content=b' ' * (preferences_api.MAX_BACKUP_BYTES + 1))
    assert result.status_code == 413
    with sessions() as session:
        user = session.query(db.User).filter_by(role='admin').one()
        assert (user.name, user.advanced_config, session.query(db.Audit).count()) == before
