"""Advanced filters and templates must preserve explicit user boundaries."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.advanced import (
    AdvancedConfigError, AdvancedSettings, KeywordTemplate, evaluate_filters,
    load_advanced, render_proposal_template, schedule_allows,
)


BASE_TEMPLATE = 'Hello {client_name}, I would like to discuss {project_title}. Best, {signature}'


def user(config=None, **fields):
    data = dict(advanced_config=json.dumps(config or {}), skills='Python, automation',
                name='Anas', portfolio='', default_days=3)
    data.update(fields)
    return SimpleNamespace(**data)


def project(**fields):
    data = dict(title='Python backend developer', description='Build an automation service.',
                skills=['Python'], currency='USD', country_code='US', owner_id='321',
                client_username='actualEmployer', client_name='Client Name',
                client_reviews=30, client_rating=4.8, posted_at=1_800_000_000)
    data.update(fields)
    return data


def test_defaults_are_complete_nonsecret_preferences():
    config = load_advanced(user())
    assert config.min_bid_interval_seconds == 60
    assert config.queue_order == 'fifo'
    assert config.search_fields == ['title', 'description', 'skills']
    assert not config.schedule_enabled
    assert config.active_days == list(range(7))
    assert config.model_dump()['keyword_templates'] == []
    assert load_advanced(None) == config
    assert load_advanced('') == config
    assert not set(config.model_dump()) & {'openai_key', 'gemini_key', 'token', 'password', 'session_secret', 'auto_requested', 'plan_tier'}
    with pytest.raises(ValidationError):
        AdvancedSettings(openai_key='never-store-credentials-in-preferences')


def test_list_codes_are_normalized_without_substring_identity_matching():
    config = AdvancedSettings(allowed_currencies=['usd', 'USD', 'eur'],
                              excluded_countries=['uk', 'gb'],
                              excluded_clients=['@ActualEmployer', 'actualemployer', '321'])
    assert config.allowed_currencies == ['USD', 'EUR']
    assert config.excluded_countries == ['GB']
    assert config.excluded_clients == ['actualemployer', '321']


@pytest.mark.parametrize('payload', [
    {'allowed_currencies': 'USD'}, {'allowed_currencies': ['US']},
    {'allowed_currencies': ['ＵＳＤ']}, {'excluded_countries': ['USA']},
    {'excluded_clients': ['some name']}, {'excluded_clients': ['a/b']},
    {'manual_review_keywords': ['']}, {'manual_review_keywords': ['x\ny']},
    {'search_fields': []}, {'search_fields': ['title', 'unknown']},
    {'search_fields': 'title'}, {'active_days': []}, {'active_days': [True]},
    {'active_days': [7]}, {'active_days': ['1']}, {'timezone': '/etc/passwd'},
    {'timezone': 'Imaginary/Timezone'}, {'start_hour': 24}, {'end_hour': -1},
    {'schedule_enabled': 'false'}, {'min_bid_interval_seconds': 5},
    {'min_bid_interval_seconds': True}, {'min_client_rating': float('nan')},
    {'min_client_rating': float('inf')}, {'min_client_rating': True},
    {'min_client_reviews': True}, {'min_client_reviews': -1},
    {'max_job_age_minutes': 10081}, {'queue_order': 'fastest'},
])
def test_invalid_preferences_cannot_silently_relax_filters(payload):
    with pytest.raises(ValidationError):
        AdvancedSettings.model_validate(payload)


@pytest.mark.parametrize('template', [
    'Hello {client_name.__class__}, here is my proposal for your project.',
    'Hello {client_name[secret]}, here is my proposal for your project.',
    'Hello {client_name!r}, here is my proposal for your project.',
    'Hello {client_name:>100}, here is my proposal for your project.',
    'Hello {unknown}, here is my proposal for your project.',
    'Hello {}, here is my proposal for your project.',
    'Hello {client_name, here is my proposal for your project.',
    'Hello {client_name}\x00, here is my proposal for your project.',
])
def test_templates_allow_only_documented_plain_placeholders(template):
    with pytest.raises(ValidationError):
        AdvancedSettings(default_template=template)


def test_keyword_template_names_unique_and_rules_bounded():
    rule = dict(name='Python', keywords=['python'], template=BASE_TEMPLATE)
    with pytest.raises(ValidationError):
        AdvancedSettings(keyword_templates=[rule, {**rule, 'name': 'PYTHON'}])
    with pytest.raises(ValidationError):
        KeywordTemplate(**{**rule, 'keywords': []})
    with pytest.raises(ValidationError):
        KeywordTemplate(**{**rule, 'keywords': ['x'] * 31})
    with pytest.raises(ValidationError):
        AdvancedSettings(keyword_templates=[{**rule, 'name': str(i)} for i in range(11)])


@pytest.mark.parametrize('raw', [
    'broken-secret-value', 'null', '[]', 'false', '{"min_bid_interval_seconds":5}',
    '{"queue_order":"fifo","queue_order":"score"}',
    '{"min_client_rating":NaN}', '{"unexpected":"secret-value"}',
    'x' * 131073,
])
def test_corrupt_stored_configuration_fails_closed_without_echo(raw):
    with pytest.raises(AdvancedConfigError) as error:
        load_advanced(SimpleNamespace(advanced_config=raw))
    assert str(error.value) == 'Saved advanced settings are invalid. Review and save them before using automation.'
    assert 'secret-value' not in str(error.value)


def test_client_restrictions_match_exact_username_or_id():
    config = {'excluded_clients': ['actualemployer']}
    assert any('Employer username' in item for item in evaluate_filters(project(), user(config)))
    assert not evaluate_filters(project(client_username='actualEmployerPlus', client_name='actualEmployer'), user(config))
    assert any('Employer username' in item for item in evaluate_filters(project(), user({'excluded_clients': ['321']})))
    assert not evaluate_filters(project(owner_id='3210', client_username='other'), user({'excluded_clients': ['321']}))


def test_currency_country_and_client_quality_filters_explain_unknown_values():
    config = {'allowed_currencies': ['USD'], 'excluded_countries': ['GB'],
              'min_client_rating': 4.5, 'min_client_reviews': 10}
    assert not evaluate_filters(project(), user(config))
    reasons = evaluate_filters(project(currency='', country_code='UK', client_rating=None, client_reviews=True), user(config))
    assert len(reasons) == 4
    assert any('currency' in item for item in reasons)
    assert any('country' in item for item in reasons)
    assert any('rating is unknown' in item for item in reasons)
    assert any('review count is unknown' in item for item in reasons)
    assert any('review count' in item for item in evaluate_filters(project(client_reviews=10.5), user(config)))
    assert any('rating' in item for item in evaluate_filters(project(client_rating=6), user(config)))


def test_selected_fields_keep_skill_aliases_and_whole_term_matching():
    config = {'search_fields': ['skills']}
    assert not evaluate_filters(project(title='other', description='', skills=['Python']), user(config))
    assert evaluate_filters(project(title='Python', description='Python', skills=['Other']), user(config))
    aliases = user({'search_fields': ['title']}, skills='web development')
    assert not evaluate_filters(project(title='React application'), aliases)
    assert evaluate_filters(project(title='Reaction footage'), aliases)
    csharp = user({'search_fields': ['title']}, skills='C#')
    assert not evaluate_filters(project(title='C# developer'), csharp)
    assert evaluate_filters(project(title='C developer'), csharp)


def test_manual_review_keywords_only_block_automatic_submission():
    profile = user({'manual_review_keywords': ['legal agreement']})
    job = project(description='Python backend and legal-agreement review')
    assert any('manual review' in item for item in evaluate_filters(job, profile))
    assert not evaluate_filters(job, profile, automatic=False)


def test_maximum_project_age_does_not_treat_missing_or_future_dates_as_new():
    profile = user({'max_job_age_minutes': 10})
    now = 1_800_000_600
    assert not evaluate_filters(project(), profile, now=now)
    assert evaluate_filters(project(posted_at=1_799_999_999), profile, now=now)
    for value in (None, 0, float('nan'), now + 301, True):
        assert any('posting time' in item for item in evaluate_filters(project(posted_at=value), profile, now=now))
    assert not evaluate_filters(project(posted_at=now + 60), profile, now=now)


def test_schedule_all_day_and_local_hour_boundaries():
    monday_utc = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
    config = AdvancedSettings(schedule_enabled=True, timezone='Asia/Karachi', active_days=[0], start_hour=9, end_hour=17)
    assert schedule_allows(config, monday_utc)
    assert not schedule_allows(config, monday_utc.replace(hour=12))
    assert not schedule_allows(config, monday_utc.replace(day=27))
    assert schedule_allows(AdvancedSettings(), monday_utc)
    full_day = AdvancedSettings(schedule_enabled=True, active_days=[0], start_hour=0, end_hour=0)
    assert schedule_allows(full_day, monday_utc.replace(hour=23))
    assert not schedule_allows(full_day, monday_utc.replace(day=27))


def test_overnight_early_hours_belong_to_previous_selected_day():
    config = AdvancedSettings(schedule_enabled=True, active_days=[6], start_hour=22, end_hour=2)
    sunday = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
    assert schedule_allows(config, sunday)
    assert schedule_allows(config, sunday.replace(day=26, hour=1))
    assert not schedule_allows(config, sunday.replace(day=26, hour=2))
    assert not schedule_allows(config, sunday.replace(day=26, hour=23))
    assert not schedule_allows(config, sunday.replace(hour=1))


def test_schedule_uses_timezone_rules_for_spring_gap_and_fall_fold():
    spring = AdvancedSettings(schedule_enabled=True, timezone='America/New_York', active_days=[6], start_hour=3, end_hour=5)
    assert not schedule_allows(spring, datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc))
    assert schedule_allows(spring, datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc))
    fall = AdvancedSettings(schedule_enabled=True, timezone='America/New_York', active_days=[6], start_hour=1, end_hour=2)
    assert schedule_allows(fall, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    assert schedule_allows(fall, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
    assert not schedule_allows(fall, datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc))


def test_schedule_accepts_unix_time_but_rejects_ambiguous_or_invalid_time():
    config = AdvancedSettings(schedule_enabled=True)
    assert schedule_allows(config, 1_800_000_000)
    for value in (True, float('nan'), float('inf'), '2026-01-01', datetime(2026, 1, 1)):
        with pytest.raises(AdvancedConfigError):
            schedule_allows(config, value)


def test_template_uses_supplied_values_without_recursive_formatting_or_inventing_facts():
    template = 'Hi {client_name}, regarding {project_title}. Skills: {skills}. Portfolio: {portfolio}. Regards {signature}. {{literal}}'
    rendered = render_proposal_template(project(title='{signature}', client_name='Amina'), user({'default_template': template}))
    assert rendered == 'Hi Amina, regarding {signature}. Skills: Python, automation. Portfolio: . Regards Anas. {literal}'
    assert 'experience' not in rendered
    assert render_proposal_template(project(), user()) is None


def test_keyword_templates_use_enabled_first_match_and_selected_fields():
    config = {
        'search_fields': ['title'], 'default_template': 'Default proposal about {project_title}, signed by {signature}.',
        'keyword_templates': [
            {'name': 'Disabled', 'keywords': ['python'], 'template': 'Disabled draft for {project_title}, signed by {signature}.', 'enabled': False},
            {'name': 'All', 'keywords': ['python', 'automation'], 'match_mode': 'all', 'template': 'All-keywords draft about {project_title}, signed by {signature}.'},
            {'name': 'Any', 'keywords': ['python', 'design'], 'match_mode': 'any', 'template': 'Any-keyword draft about {project_title}, signed by {signature}.'},
        ],
    }
    profile = user(config)
    assert render_proposal_template(project(), profile).startswith('Any-keyword draft')
    assert render_proposal_template(project(title='Python automation'), profile).startswith('All-keywords draft')
    assert render_proposal_template(project(title='Other', description='Python'), profile).startswith('Default proposal')


def test_expanded_template_length_is_bounded_and_unknown_template_cannot_fallback():
    repeated = '{project_description}' * 10
    profile = user({'default_template': repeated})
    with pytest.raises(AdvancedConfigError, match='Expanded proposal'):
        render_proposal_template(project(description='x' * 1000), profile)
    with pytest.raises(AdvancedConfigError, match='Expanded proposal'):
        render_proposal_template(project(), user({'default_template': '{portfolio}' * 5}))
    with pytest.raises(AdvancedConfigError, match='Saved advanced settings'):
        render_proposal_template(project(), user({'default_template': 'Unsafe {signature.__class__} placeholder text'}))


def test_working_hours_have_packaged_timezone_data_without_os_database():
    import zoneinfo
    original = zoneinfo.TZPATH
    try:
        zoneinfo.reset_tzpath(())
        zoneinfo.ZoneInfo.clear_cache()
        config = AdvancedSettings(timezone='Asia/Karachi', schedule_enabled=True,
                                  active_days=[0], start_hour=9, end_hour=17)
        # Monday 04:00 UTC is 09:00 in Karachi, even without system zone files.
        assert schedule_allows(config, datetime(2026, 11, 2, 4, tzinfo=timezone.utc))
        assert not schedule_allows(config, datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    finally:
        zoneinfo.reset_tzpath(original)
        zoneinfo.ZoneInfo.clear_cache()
