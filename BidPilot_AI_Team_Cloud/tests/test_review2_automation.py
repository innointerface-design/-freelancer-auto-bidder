"""Regression coverage for hidden phrase boundaries and truthful worker status."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, main
from app.advanced import evaluate_filters
from app.live_status import automation_status
from app.logic import score_job
from app.scoring_base import contains_term, job_score


@pytest.mark.parametrize('separator', ['\u200b', '\u200c', '\u2060', '\ufeff'])
def test_hidden_phrase_boundaries_still_block_risk_and_custom_exclusions(separator):
    project = {
        'title': 'Python video editing developer',
        'description': 'Please pay a registration' + separator + 'fee.',
        'skills': ['Python', 'Video editing'],
        'payment_verified': True, 'budget_min': 100, 'budget_max': 500,
    }
    result = score_job(project, 'python', '', search_fields=['title'])
    assert result['decision'] == 'Skip'
    assert any('registration fee' in reason for reason in result['reasons'])
    assert job_score(project)['decision'] == 'Skip'
    project['description'] = 'Payment via wire' + separator + 'transfer.'
    result = score_job(project, 'python', '', 'wire transfer', search_fields=['title'])
    assert result['decision'] == 'Skip'
    assert any('wire transfer' in reason for reason in result['reasons'])


@pytest.mark.parametrize('phrase', [
    'legal\u200bagreement', 'legal\u200cagreement',
    'le\u200bgal\u200bagree\u200bment',
])
def test_hidden_manual_review_terms_stop_only_automatic_actions(phrase):
    user = SimpleNamespace(skills='python', advanced_config=json.dumps({
        'manual_review_keywords': ['legal agreement'],
    }))
    project = {'title': 'Python developer', 'description': phrase}
    assert any('manual review' in reason for reason in evaluate_filters(project, user))
    assert not evaluate_filters(project, user, automatic=False)


@pytest.mark.parametrize('text,term,expected', [
    ('tele\u200bgram', 'telegram', True),
    ('regis\u200btration\u200bfee', 'registration fee', True),
    ('registration\nfee', 'registration fee', True),
    ('registration-fee', 'registration fee', True),
    ('reaction footage', 'react', False),
    ('React\u200bion footage', 'react', False),
    ('x\u200bReact', 'react', False),
    ('\u200bReact\u200b developer', 'react', True),
    ('\u200bC#\u200b developer', 'c#', True),
    ('larger service', 'r', False),
    ('C++ developer', 'C', False),
    ('C# developer', 'C#', True),
])
def test_phrase_matching_preserves_whole_term_guards(text, term, expected):
    assert contains_term(text, term) is expected


def test_hidden_character_does_not_turn_negated_skill_into_a_match():
    project = {'title': 'Accounting work', 'description': 'No video editing needed; bookkeeping only.',
               'payment_verified': True, 'budget_max': 100, 'client_rating': 5,
               'client_reviews': 10, 'country_code': 'US'}
    plain = job_score(project)
    hidden = job_score({**project, 'description': project['description'].replace('editing', 'edi\u200bting')})
    assert plain['matched'] == hidden['matched'] == []
    assert plain['decision'] == hidden['decision'] == 'Skip'
    assert plain['score'] == hidden['score']


@pytest.fixture
def state(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(main.time, 'time', lambda: now)
    monkeypatch.setattr(main, 'PLATFORM_AUTO', True)
    engine = create_engine(f'sqlite:///{tmp_path}/status.db')
    db.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        user = db.User(email='review-status@example.test', name='Status',
                       password_hash='unused', auto_requested=True)
        db.apply_plan(user, 'free_team', now=now)
        session.add(user)
        session.flush()
        uid = user.id
        session.add(db.Connection(user_id=uid, token_cipher='synthetic-not-opened',
                                  environment='sandbox', freelancer_id='123'))
        session.add(db.WorkerHeartbeat(worker_key='scanner', updated_at=now))
        session.commit()
    yield sessions, uid, now
    engine.dispose()


def test_sending_bid_remains_paused_after_minimum_gap_without_mutation(state):
    sessions, uid, now = state
    with sessions() as session:
        job = db.Job(user_id=uid, remote_id='1', data='{}')
        session.add(job)
        session.flush()
        bid = db.Bid(user_id=uid, job_id=job.id, status='sending',
                     proposal='reserved proposal', amount=100, updated=now - 7200)
        session.add(bid)
        session.commit()
        user = session.get(db.User, uid)
        connection = session.query(db.Connection).filter_by(user_id=uid).one()
        with pytest.raises(HTTPException) as blocked:
            main.require_bid_spacing(session, user, connection)
        assert blocked.value.status_code == 429
        result = automation_status(session, uid, now=now)
        assert result['automation']['state'] == 'paused'
        assert 'bid' in result['automation']['reason'].lower()
        assert 'result' in result['automation']['reason'].lower()
        session.expire_all()
        assert session.get(db.Bid, bid.id).status == 'sending'
        assert session.get(db.User, uid).auto_requested is True


@pytest.mark.parametrize('payload', [
    '{"extra":' + '[' * 1200 + '0' + ']' * 1200 + '}',
    '{"extra":NaN}', '{"extra":1e999}', '{"extra":"\\ud800"}',
])
def test_queue_skips_invalid_saved_project_data(state, payload):
    sessions, uid, now = state
    with sessions() as session:
        base = {'environment': 'sandbox', 'account_id': '123', 'project_status': 'active'}
        # Add valid metadata without parsing the deliberately invalid value.
        corrupted = json.dumps(base)[:-1] + ',' + payload[1:]
        session.add(db.Job(user_id=uid, remote_id='broken', data=corrupted, score=99))
        session.add(db.Job(user_id=uid, remote_id='valid', data=json.dumps(base), score=99))
        session.commit()
        result = automation_status(session, uid, now=now)
        assert result['automation']['queue_count'] == 1
        assert result['automation']['state'] == 'watching'


@pytest.mark.parametrize('broken', ['null', '{bad json', '{"extra":' + '[' * 1200 + '0' + ']' * 1200 + '}'])
def test_corrupt_project_does_not_starve_other_client_greetings(state, monkeypatch, broken):
    sessions, uid, _ = state
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with sessions() as session:
        session.get(db.User, uid).auto_chat_greeting = True
        for remote, data in [('901', broken), ('902', json.dumps({
            'environment': 'sandbox', 'account_id': '123', 'title': 'Valid project',
        }))]:
            job = db.Job(user_id=uid, remote_id=remote, data=data)
            session.add(job)
            session.flush()
            session.add(db.Bid(user_id=uid, job_id=job.id, amount=50,
                               proposal='A synthetic submitted proposal.', status='submitted'))
        session.commit()
    api = Mock()
    api.list_threads.return_value = {'threads': [
        {'id': 998, 'context': {'id': 901}, 'last_message': {'from_user': 345, 'message': 'Hello'}},
        {'id': 999, 'context': {'id': 902}, 'last_message': {'from_user': 345, 'message': 'Hello'}},
    ]}
    ai = Mock(return_value='Thanks for the details. Please share the project scope.')
    monkeypatch.setattr(main, 'get_client', lambda *_: api)
    monkeypatch.setattr(main, 'ai_chat_reply', ai)
    assert main.run_autochat_cycle(uid)['replied'] == 1
    ai.assert_called_once()
    assert ai.call_args.args[1] == 'Valid project'
    api.send_chat_message.assert_called_once()
    with sessions() as session:
        assert session.query(db.ChatGreeted).one().thread_id == '999'
