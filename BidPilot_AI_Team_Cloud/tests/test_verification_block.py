"""Unverified or unknown payment stays out of bidding; other flags are informational."""
from types import SimpleNamespace

import pytest

from app.freelancer import normalize_project
from app.logic import auto_eligible, score_for_user, score_job
from app.verification import classify_verification, verification_block_reasons


def project(**overrides):
    value = dict(
        title='Python automation service', description='Build a Python service with tests. ' * 15,
        skills=['Python', 'Automation'], payment_verified=True,
        budget_min=30, budget_max=300, currency='USD', type='fixed',
        client_reviews=40, client_rating=4.9, country_code='US', project_status='active',
    )
    value.update(overrides)
    return value


def bidder():
    return SimpleNamespace(
        min_budget=20, max_bid=100, bid_strategy='average', active=True,
        auto_requested=True, auto_admin_allowed=True, skills='python, automation',
        countries='US', excluded_keywords='', min_score=20, max_daily=50, max_hourly=15,
        require_payment_verified=False,
    )


@pytest.mark.parametrize('payment', [False, None, 'true', 'false', 0, 1, [], {}])
def test_only_boolean_true_payment_is_verified(payment):
    classified = classify_verification(project(payment_verified=payment))
    assert classified['blocked'] is True
    assert classified['checks']['payment_verified'] is (False if payment is False else None)
    assert classified['reasons'] == [
        'Client payment is unverified' if payment is False else 'Client payment verification is unknown'
    ]


def test_missing_payment_is_blocked_for_legacy_saved_job():
    saved = project()
    del saved['payment_verified']
    assert verification_block_reasons(saved) == ['Client payment verification is unknown']


def test_nested_positive_does_not_override_missing_canonical_payment_status():
    saved = project(client_verifications={'payment_verified': True})
    del saved['payment_verified']
    assert verification_block_reasons(saved) == ['Client payment verification is unknown']


@pytest.mark.parametrize('field', ['email_verified', 'identity_verified', 'phone_verified'])
@pytest.mark.parametrize('nested', [False, True])
def test_optional_failure_does_not_block_verified_payment(field, nested):
    failed = project(**({'client_verifications': {field: False}} if nested else {field: False}))
    classified = classify_verification(failed)
    assert classified['blocked'] is False
    assert classified['checks'][field] is False
    assert classified['reasons'] == []
    assert score_job(failed, 'python, automation', '')['decision'] != 'Skip'


@pytest.mark.parametrize('optional', [{}, {'email_verified': None}, {'identity_verified': 'false'},
                                    {'phone_verified': 0}, {'deposit_made': False, 'profile_complete': False}])
def test_missing_unknown_and_unrelated_optional_statuses_do_not_invent_failures(optional):
    classified = classify_verification(project(client_verifications=optional))
    assert classified == {'blocked': False, 'reasons': [], 'checks': {'payment_verified': True}}


def test_unknown_country_reviews_and_rating_are_not_verification_failures():
    saved = project(country_code='', client_reviews=None, client_rating=None)
    assert classify_verification(saved)['blocked'] is False
    assert score_job(saved, 'python, automation', '')['decision'] != 'Skip'


def test_normalization_preserves_known_api_booleans_without_inventing_other_statuses():
    normalized = normalize_project({'id': 123}, {'status': {
        'payment_verified': True, 'email_verified': False, 'phone_verified': True,
        'identity_verified': None, 'deposit_made': False, 'profile_complete': False,
    }})
    assert normalized['client_verifications'] == {
        'payment_verified': True, 'email_verified': False, 'phone_verified': True,
    }
    assert normalized['payment_verified'] is True
    assert verification_block_reasons(normalized) == []


def test_normalization_falls_back_to_actual_top_level_boolean_status():
    normalized = normalize_project({'id': 123}, {
        'status': {'payment_verified': 'yes', 'email_verified': None},
        'payment_verified': True, 'email_verified': False,
    })
    assert normalized['client_verifications'] == {'payment_verified': True, 'email_verified': False}


@pytest.mark.parametrize('source', [None, {}, {'status': None}, {'status': {'payment_verified': 1}}])
def test_missing_or_malformed_employer_status_is_unknown_payment(source):
    normalized = normalize_project({'id': 123}, source)
    assert normalized['payment_verified'] is None
    assert normalized['client_verifications'] == {}
    assert verification_block_reasons(normalized) == ['Client payment verification is unknown']


def test_contradictory_failure_cannot_be_hidden_by_top_level_verified_flag():
    saved = project(client_verifications={'payment_verified': False})
    assert verification_block_reasons(saved) == ['Client payment is unverified']
    scored = score_job(saved, 'python', '')
    assert scored['decision'] == 'Skip'
    assert 'Payment verified' not in scored['reasons']


@pytest.mark.parametrize('overrides', [
    {'payment_verified': False}, {'payment_verified': None},
])
def test_blocked_project_scores_skip_and_cannot_autobid_when_old_toggle_is_off(monkeypatch, overrides):
    monkeypatch.setattr('app.db.subscription_active', lambda _: True)
    saved = project(**overrides)
    user = bidder()
    classified = classify_verification(saved)
    scored = score_for_user(saved, user)
    allowed, causes, _ = auto_eligible(saved, 99, user, object())
    assert scored['decision'] == 'Skip'
    assert all(reason in scored['reasons'] for reason in classified['reasons'])
    assert allowed is False
    assert all(reason in causes for reason in classified['reasons'])


@pytest.mark.parametrize('optional', [{}, {'email_verified': False}, {'identity_verified': False}, {'phone_verified': False}])
def test_payment_verified_with_optional_fields_still_scores_and_bids(monkeypatch, optional):
    monkeypatch.setattr('app.db.subscription_active', lambda _: True)
    saved = project(client_verifications=optional)
    assert score_for_user(saved, bidder())['decision'] != 'Skip'
    allowed, causes, _ = auto_eligible(saved, 99, bidder(), object())
    assert allowed is True, causes
