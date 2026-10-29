import math
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.logic import auto_eligible, make_proposal, price_for, score_job
from app.scoring_base import parse_number, job_score, extract_client_requirements


def user(**overrides):
    values = dict(
        min_budget=20, max_bid=100, bid_strategy='average', active=True,
        plan_tier='free_team', subscription_status='active',
        auto_requested=True, auto_admin_allowed=True, skills='python, automation',
        countries='US', excluded_keywords='', min_score=20, max_daily=50,
        max_hourly=15, description='', portfolio='', name='Tester', default_days=3,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def job(**overrides):
    values = dict(
        title='Python automation developer',
        description='Build a Python automation service with tests. ' * 12,
        skills=['Python', 'Automation'], payment_verified=True,
        budget_min=30, budget_max=300, currency='USD', type='fixed',
        client_reviews=40, client_rating=4.9, country_code='US',
        posted_at=time.time(), project_status='active',
    )
    values.update(overrides)
    return values


def eligible(j, u=None, subscription=True, **kwargs):
    fake_db = SimpleNamespace(subscription_active=lambda _: subscription)
    with patch.dict(sys.modules, {'app.db': fake_db}):
        return auto_eligible(j, 99, u or user(), object(), **kwargs)


class PricingTests(unittest.TestCase):
    def test_average_never_exceeds_user_cap(self):
        self.assertEqual(price_for(job(), user()), 100)

    def test_every_strategy_respects_both_ranges(self):
        for strategy in ('minimum', 'average', 'maximum', 'competitive_low'):
            value = price_for(job(budget_min=10, budget_max=300), user(bid_strategy=strategy))
            self.assertGreaterEqual(value, 20)
            self.assertLessEqual(value, 100)

    def test_no_intersection_returns_none(self):
        self.assertIsNone(price_for(job(budget_min=200), user()))
        self.assertIsNone(price_for(job(budget_min=1, budget_max=10), user()))
        self.assertIsNone(price_for(job(budget_min=500, budget_max=100), user()))

    def test_invalid_values_cannot_create_price(self):
        for invalid in (math.nan, math.inf, -math.inf, 'broken', -1):
            self.assertIsNone(price_for(job(budget_max=invalid), user()))
            self.assertIsNone(price_for(job(), user(max_bid=invalid)))

    def test_cent_rounding_preserves_caps(self):
        self.assertEqual(price_for(job(), user(max_bid=99.999)), 99.99)
        self.assertEqual(price_for(job(budget_min=20.001), user(bid_strategy='minimum')), 20.01)


class MatchingRegressionTests(unittest.TestCase):
    def test_short_technical_skills_are_not_discarded(self):
        for skill in ('Go', 'R', 'C#', 'C++', 'AI'):
            with self.subTest(skill=skill):
                result = score_job(job(title=f'{skill} developer', description='', skills=[]), skill, '')
                self.assertEqual(result['matched'], [skill.casefold()])

    def test_aliases_do_not_match_inside_unrelated_words(self):
        for skill, title in (('python', 'Need both versions'), ('web development', 'Reaction footage editor'), ('java', 'JavaScript designer')):
            with self.subTest(skill=skill):
                result = score_job(job(title=title, description='', skills=[]), skill, '')
                self.assertEqual(result['matched'], [])
                self.assertEqual(result['decision'], 'Skip')

    def test_string_or_named_object_skills_are_not_lost(self):
        for skills in ('C#', [{'name': 'C#'}], {'name': 'C#'}):
            result = score_job(job(title='', description='', skills=skills), 'C#', '')
            self.assertEqual(result['matched'], ['c#'])

    def test_duplicate_profile_skills_do_not_inflate_score(self):
        original = score_job(job(), 'python', '')
        duplicates = score_job(job(), 'python, Python, python', '')
        self.assertEqual(original['score'], duplicates['score'])
        self.assertEqual(duplicates['matched'], ['python'])

    def test_risk_phrase_normalizes_spacing_and_format_characters(self):
        for phrase in ('registration\nfee', 'registration-fee', 'registration\u200b fee', 'REGISTRATION  FEE'):
            with self.subTest(phrase=phrase):
                result = score_job(job(description='Python developer, pay a ' + phrase), 'python', '')
                self.assertEqual(result['decision'], 'Skip')

    def test_two_character_custom_exclusion_is_honored(self):
        result = score_job(job(title='Python and AI developer'), 'python', '', 'AI')
        self.assertEqual(result['decision'], 'Skip')

    def test_future_dates_do_not_receive_freshness_bonus(self):
        result = score_job(job(posted_at=time.time() + 100000), 'python', '')
        self.assertEqual(result['breakdown']['freshness'], 1)


class BaseScoringRegressionTests(unittest.TestCase):
    def test_nonfinite_and_boolean_numbers_are_invalid(self):
        for value in ('NaN', 'Infinity', math.inf, True, False, 10 ** 1000):
            self.assertIsNone(parse_number(value))

    def test_base_scorer_handles_nullable_skill_and_nonfinite_reviews(self):
        result = job_score(job(skills=None, client_reviews='Infinity', client_rating='NaN'))
        self.assertIsInstance(result['score'], int)

    def test_base_scorer_handles_structured_skill_names(self):
        result = job_score(job(title='', description='', skills=[{'name': 'video editing'}]))
        self.assertIn('Video editing', result['matched'])

    def test_requirements_do_not_read_ai_or_ad_inside_words(self):
        requirements = extract_client_requirements('Maintain a dashboard', '')
        self.assertFalse(any('AI-generated' in value for value in requirements))
        self.assertFalse(any('promotional video ads' in value for value in requirements))

    def test_subcent_bounds_round_inward_without_tolerance(self):
        self.assertEqual(price_for(job(budget_min=20.000000000001), user(bid_strategy='minimum')), 20.01)
        self.assertEqual(price_for(job(), user(max_bid=99.999999999999)), 99.99)
        self.assertIsNone(price_for(job(budget_min=20.000000000001, budget_max=20.009999999999), user()))

    def test_boolean_price_is_not_a_currency_amount(self):
        self.assertIsNone(price_for(job(budget_min=True), user()))


class EligibilityTests(unittest.TestCase):
    def test_active_matching_job_is_eligible(self):
        allowed, reasons, amount = eligible(job())
        self.assertTrue(allowed, reasons)
        self.assertEqual(amount, 100)

    def test_risk_words_are_hard_veto_even_with_low_score_threshold(self):
        allowed, reasons, _ = eligible(job(description='Python automation: pay a registration fee to start.'))
        self.assertFalse(allowed)
        self.assertTrue(any('risk filters' in r for r in reasons))

    def test_no_skill_match_is_hard_veto(self):
        self.assertFalse(eligible(job(title='Translate a novel', description='Translation only', skills=['French']))[0])

    def test_custom_exclusion_is_hard_veto(self):
        self.assertFalse(eligible(job(), user(excluded_keywords='automation'))[0])

    def test_closed_unknown_and_missing_status_are_blocked(self):
        for status in (None, '', 'closed', 'cancelled', 'draft'):
            self.assertFalse(eligible(job(project_status=status))[0])

    def test_own_project_is_blocked_for_string_or_numeric_id(self):
        fake_db = SimpleNamespace(subscription_active=lambda _: True)
        with patch.dict(sys.modules, {'app.db': fake_db}):
            for owner_id in ('12345', 12345):
                allowed, reasons, _ = auto_eligible(job(owner_id=owner_id), 99, user(), SimpleNamespace(freelancer_id='12345'))
                self.assertFalse(allowed)
                self.assertIn('Cannot bid on your own project', reasons)
            allowed, reasons, _ = auto_eligible(job(owner_id='98765'), 99, user(), SimpleNamespace(freelancer_id='12345'))
            self.assertTrue(allowed, reasons)

    def test_missing_budget_does_not_generate_auto_bid(self):
        self.assertFalse(eligible(job(budget_min=None, budget_max=None))[0])

    def test_subscription_and_opt_in_enforced(self):
        self.assertFalse(eligible(job(), subscription=False)[0])
        self.assertFalse(eligible(job(), user(active=False))[0])
        self.assertFalse(eligible(job(), user(auto_requested=False))[0])
        self.assertFalse(eligible(job(), platform_gate=False)[0])

    def test_demo_and_attempt_limits_block_submission(self):
        self.assertFalse(eligible(job(demo=True))[0])
        self.assertFalse(eligible(job(), day_count=50)[0])
        self.assertFalse(eligible(job(), hour_count=15)[0])

    def test_unknown_currency_or_pricing_type_prevents_auto_bid(self):
        for value in (None, '', 'unknown', 'USD$'):
            self.assertFalse(eligible(job(currency=value))[0])
        for value in (None, '', 'contest', 'unrecognized'):
            self.assertFalse(eligible(job(type=value))[0])

    def test_country_aliases_and_whitespace_filter_correctly(self):
        allowed, reasons, _ = eligible(job(country_code='GB'), user(countries='USA\nUK', require_country_match=True))
        self.assertTrue(allowed, reasons)
        allowed, reasons, _ = eligible(job(country_code='USA'), user(countries='US', require_country_match=True))
        self.assertTrue(allowed, reasons)

    def test_nonfinite_remote_numbers_do_not_crash_scoring(self):
        result = score_job(job(client_reviews='Infinity', client_rating='NaN'), 'python', '')
        self.assertIsInstance(result['score'], int)


class ProposalTests(unittest.TestCase):
    def test_fallback_uses_profile_without_invented_experience(self):
        for tone in ('consultative', 'agency_pitch', 'friendly_expert', 'winning_direct'):
            proposal = make_proposal(job(), user(proposal_tone=tone, description='I use Python.'))
            self.assertIn('I use Python.', proposal)
            self.assertNotIn('extensive hands-on experience', proposal)
            self.assertNotIn('unlimited revisions', proposal)
            self.assertNotIn('ready to start immediately', proposal)
            self.assertIn('subject to confirming the scope', proposal)


if __name__ == '__main__':
    unittest.main()
