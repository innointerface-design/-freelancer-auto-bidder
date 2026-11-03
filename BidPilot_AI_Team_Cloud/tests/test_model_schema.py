"""Long provider IDs survive fresh schemas and legacy database upgrades."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import MetaData, String, Text, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import db


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/model-schema.db')
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'SessionLocal', factory)
    monkeypatch.setenv('ADMIN_EMAIL', 'schema-owner@example.test')
    monkeypatch.setenv('ADMIN_PASSWORD', 'synthetic-schema-owner-password')
    for name in ('ADMIN_PASSWORD_B64', 'SECOND_FOUNDER_EMAIL', 'SECOND_FOUNDER_PASSWORD',
                 'SECOND_FOUNDER_PASSWORD_B64'):
        monkeypatch.delenv(name, raising=False)
    yield engine, factory
    engine.dispose()


def _write_and_read_long_models(factory):
    models = {'gemini_model': 'gemini-future-'.ljust(120, 'x'),
              'openai_model': 'ft:gpt-future:organisation:'.ljust(120, 'x')}
    assert all(len(value) == 120 for value in models.values())
    with factory() as session:
        owner = session.query(db.User).filter_by(email='schema-owner@example.test').one()
        original_password = owner.password_hash
        original_key = owner.openai_key_cipher
        for field, value in models.items():
            setattr(owner, field, value)
        session.commit()
    db.init_db()
    db.init_db()
    with factory() as session:
        owner = session.query(db.User).filter_by(email='schema-owner@example.test').one()
        for field, value in models.items():
            assert getattr(owner, field) == value
        assert owner.password_hash == original_password
        assert owner.openai_key_cipher == original_key


def test_fresh_database_supports_120_character_provider_model_ids(isolated_database):
    engine, factory = isolated_database
    db.init_db()
    metadata = {column['name']: column['type'] for column in inspect(engine).get_columns('users')}
    assert metadata['gemini_model'].length == 120
    assert metadata['openai_model'].length == 120
    _write_and_read_long_models(factory)


def test_old_sqlite_varchar60_preserves_account_and_accepts_long_ids(isolated_database):
    engine, factory = isolated_database
    legacy_users = db.User.__table__.to_metadata(MetaData())
    legacy_users.c.gemini_model.type = String(60)
    legacy_users.c.openai_model.type = String(60)
    legacy_users.create(engine)
    db.Base.metadata.create_all(engine)
    with factory() as session:
        session.add(db.User(email='schema-owner@example.test', name='Existing owner',
                            password_hash=db.hash_password('existing-private-password'),
                            role='admin', openai_key_cipher=db.encrypt_secret('synthetic-private-key'),
                            gemini_model='gemini-legacy-choice', openai_model='gpt-legacy-choice'))
        session.commit()
    db.init_db()
    with factory() as session:
        owner = session.query(db.User).one()
        assert db.verify_password('existing-private-password', owner.password_hash)
        assert db.decrypt_secret(owner.openai_key_cipher) == 'synthetic-private-key'
        assert owner.gemini_model == 'gemini-legacy-choice'
        assert owner.openai_model == 'gpt-legacy-choice'
    # SQLite does not enforce VARCHAR lengths; do not rebuild existing user data.
    metadata = {column['name']: column['type'] for column in inspect(engine).get_columns('users')}
    assert metadata['gemini_model'].length == 60
    assert metadata['openai_model'].length == 60
    _write_and_read_long_models(factory)


def test_missing_legacy_model_columns_are_added_with_120_character_capacity(isolated_database):
    engine, factory = isolated_database
    db.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE users DROP COLUMN gemini_model'))
        connection.execute(text('ALTER TABLE users DROP COLUMN openai_model'))
    db.init_db()
    metadata = {column['name']: column['type'] for column in inspect(engine).get_columns('users')}
    assert metadata['gemini_model'].length == 120
    assert metadata['openai_model'].length == 120
    _write_and_read_long_models(factory)


@pytest.mark.parametrize('column_type,expected_alters', [
    (String(60), 2), (String(119), 2), (String(120), 0),
    (String(180), 0), (String(), 0), (Text(), 0),
])
def test_postgres_widens_only_short_existing_model_columns_once(monkeypatch, column_type, expected_alters):
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    connection = engine.begin.return_value.__enter__.return_value
    metadata = {column.name: {'name': column.name, 'type': column.type}
                for column in db.User.__table__.columns}
    for name in ('gemini_model', 'openai_model'):
        metadata[name]['type'] = column_type
    inspector = MagicMock()
    inspector.get_table_names.return_value = ['users']
    inspector.get_columns.side_effect = lambda table: list(metadata.values())
    statements = []

    def execute(statement):
        sql = str(statement)
        statements.append(sql)
        if sql.startswith('ALTER TABLE users ALTER COLUMN '):
            column = sql.split()[5]
            assert column in ('gemini_model', 'openai_model')
            assert sql.endswith(' TYPE VARCHAR(120)')
            metadata[column]['type'] = String(120)

    connection.execute.side_effect = execute
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(db, 'inspect', lambda value: inspector)
    db._auto_migrate_sqlite()
    db._auto_migrate_sqlite()
    alterations = [statement for statement in statements if statement.startswith('ALTER TABLE')]
    assert len(alterations) == expected_alters
    assert len(set(alterations)) == expected_alters
