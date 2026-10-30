"""Validated project rules, proposal templates and working hours.

Configuration contains only user-selected preferences. Credentials, subscription
state and permissions are deliberately absent from this model.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
import json
import math
import re
from string import Formatter
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .scoring_base import contains_term, country_code, normalized_text, parse_number, skill_text


class AdvancedConfigError(ValueError):
    """A fixed, non-sensitive error suitable for a disabled automation result."""


TEMPLATE_FIELDS = frozenset((
    'client_name', 'project_title', 'skills', 'portfolio', 'signature',
    'delivery_days', 'project_description',
))
SEARCH_FIELDS = ('title', 'description', 'skills')


def _text_items(value, *, maximum, item_length=100, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f'Use a list containing {minimum} to {maximum} items')
    items = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError('List items must be text')
        item = item.strip()
        if not 1 <= len(item) <= item_length or any(ord(c) < 32 for c in item):
            raise ValueError(f'List items must contain 1 to {item_length} printable characters')
        if item not in items:
            items.append(item)
    return items


def _template(value, *, optional=False):
    if not isinstance(value, str):
        raise ValueError('Proposal template must be text')
    value = value.replace('\r\n', '\n').strip()
    if optional and not value:
        return ''
    if not 30 <= len(value) <= 2800 or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Proposal template must contain 30 to 2800 characters')
    try:
        for _, name, specification, conversion in Formatter().parse(value):
            if name is not None and (name not in TEMPLATE_FIELDS or specification or conversion):
                raise ValueError('Use only the documented proposal placeholders without format options')
    except ValueError:
        raise ValueError('Use only the documented proposal placeholders and balanced braces') from None
    return value


class KeywordTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=60)
    keywords: list[str] = Field(min_length=1, max_length=30)
    match_mode: Literal['any', 'all'] = 'any'
    template: str = Field(min_length=30, max_length=2800)
    enabled: bool = Field(default=True, strict=True)

    @field_validator('name')
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Enter a printable template name')
        return value

    @field_validator('keywords', mode='before')
    @classmethod
    def validate_keywords(cls, value):
        return _text_items(value, maximum=30, minimum=1)

    @field_validator('template')
    @classmethod
    def validate_template(cls, value):
        return _template(value)


class AdvancedSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    allowed_currencies: list[str] = Field(default_factory=list, max_length=30)
    excluded_countries: list[str] = Field(default_factory=list, max_length=100)
    excluded_clients: list[str] = Field(default_factory=list, max_length=100)
    min_client_rating: float = Field(default=0, ge=0, le=5, strict=True)
    min_client_reviews: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    search_fields: list[Literal['title', 'description', 'skills']] = Field(
        default_factory=lambda: list(SEARCH_FIELDS), min_length=1, max_length=3,
    )
    manual_review_keywords: list[str] = Field(default_factory=list, max_length=50)
    default_template: str = Field(default='', max_length=2800)
    keyword_templates: list[KeywordTemplate] = Field(default_factory=list, max_length=10)
    min_bid_interval_seconds: int = Field(default=60, ge=60, le=86400, strict=True)
    queue_order: Literal['fifo', 'score'] = 'fifo'
    max_job_age_minutes: int = Field(default=0, ge=0, le=10080, strict=True)
    schedule_enabled: bool = Field(default=False, strict=True)
    timezone: str = Field(default='UTC', min_length=1, max_length=100)
    active_days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start_hour: int = Field(default=0, ge=0, le=23, strict=True)
    end_hour: int = Field(default=0, ge=0, le=23, strict=True)

    @field_validator('allowed_currencies', mode='before')
    @classmethod
    def validate_currencies(cls, value):
        items = _text_items(value, maximum=30, item_length=3)
        if any(not re.fullmatch(r'[A-Za-z]{3}', item) for item in items):
            raise ValueError('Use three-letter currency codes such as USD')
        return list(dict.fromkeys(item.upper() for item in items))

    @field_validator('excluded_countries', mode='before')
    @classmethod
    def validate_countries(cls, value):
        items = _text_items(value, maximum=100, item_length=2)
        if any(not re.fullmatch(r'[A-Za-z]{2}', item) for item in items):
            raise ValueError('Use two-letter country codes such as GB')
        return list(dict.fromkeys(country_code(item) for item in items))

    @field_validator('excluded_clients', mode='before')
    @classmethod
    def validate_clients(cls, value):
        items = _text_items(value, maximum=100, item_length=81)
        normalized = [item.removeprefix('@').casefold() for item in items]
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,79}', item) for item in normalized):
            raise ValueError('Use an exact employer username or numeric account ID')
        return list(dict.fromkeys(normalized))

    @field_validator('manual_review_keywords', mode='before')
    @classmethod
    def validate_manual_keywords(cls, value):
        return _text_items(value, maximum=50)

    @field_validator('search_fields', mode='before')
    @classmethod
    def validate_search_fields(cls, value):
        if not isinstance(value, list) or not 1 <= len(value) <= 3:
            raise ValueError('Choose at least one project search field')
        if any(not isinstance(item, str) or item not in SEARCH_FIELDS for item in value):
            raise ValueError('Unknown project search field')
        return list(dict.fromkeys(value))

    @field_validator('default_template')
    @classmethod
    def validate_default_template(cls, value):
        return _template(value, optional=True)

    @field_validator('keyword_templates')
    @classmethod
    def validate_template_names(cls, value):
        names = [item.name.casefold() for item in value]
        if len(names) != len(set(names)):
            raise ValueError('Keyword template names must be unique')
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError('Use an IANA timezone such as Asia/Karachi or UTC') from None
        return value

    @field_validator('active_days', mode='before')
    @classmethod
    def validate_active_days(cls, value):
        if (not isinstance(value, list) or not 1 <= len(value) <= 7
                or any(type(item) is not int or not 0 <= item <= 6 for item in value)):
            raise ValueError('Choose weekdays numbered 0 (Monday) through 6 (Sunday)')
        return list(dict.fromkeys(value))


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Repeated configuration field')
        result[key] = value
    return result


def load_advanced(user=None):
    """Return validated settings; corrupt stored settings never loosen filters.

    Also accepts a validated model, dictionary or serialized JSON for import and
    standalone validation. Empty/missing legacy configuration means defaults.
    """
    if isinstance(user, AdvancedSettings):
        return user
    raw = user if isinstance(user, (dict, str)) else getattr(user, 'advanced_config', None)
    try:
        if raw is None or raw == '':
            raw = {}
        if isinstance(raw, str):
            if len(raw) > 131_072:
                raise ValueError('Configuration too large')
            raw = json.loads(raw, object_pairs_hook=_unique_json_object)
        if not isinstance(raw, dict):
            raise ValueError('Configuration must be an object')
        return AdvancedSettings.model_validate(raw)
    except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
        raise AdvancedConfigError('Saved advanced settings are invalid. Review and save them before using automation.') from None


def _utc_now(now=None):
    if now is None:
        return datetime.now(datetime_timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise AdvancedConfigError('Working-hours checks require an aware timestamp')
        return now.astimezone(datetime_timezone.utc)
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise AdvancedConfigError('Working-hours checks require a valid timestamp')
    try:
        return datetime.fromtimestamp(now, datetime_timezone.utc)
    except (ValueError, OSError, OverflowError):
        raise AdvancedConfigError('Working-hours checks require a valid timestamp') from None


def schedule_allows(config, now=None):
    """Check an IANA local window; early overnight hours belong to the prior day."""
    config = load_advanced(config)
    if not config.schedule_enabled:
        return True
    local = _utc_now(now).astimezone(ZoneInfo(config.timezone))
    start, end = config.start_hour, config.end_hour
    if start == end:
        return local.weekday() in config.active_days
    if start < end:
        return local.weekday() in config.active_days and start <= local.hour < end
    if local.hour >= start:
        return local.weekday() in config.active_days
    if local.hour < end:
        return (local - timedelta(days=1)).weekday() in config.active_days
    return False


def _job_text(job, fields):
    return ' '.join(skill_text(job.get(field)) if field == 'skills' else str(job.get(field) or '') for field in fields)


def evaluate_filters(job, user, *, automatic=True, now=None):
    """Explain configured blocks without mutating a project or user.

    Manual-review keywords block automation only. Other configured filters also
    apply to manual submission, so manual mode cannot evade an explicit rule.
    Schedule is separate to keep manual review available outside working hours.
    """
    config = load_advanced(user)
    reasons = []
    currency = str(job.get('currency') or '').strip().upper()
    if config.allowed_currencies and currency not in config.allowed_currencies:
        reasons.append('Project currency is outside your allowed currency list')
    if country_code(job.get('country_code')) in config.excluded_countries:
        reasons.append('Employer country is excluded')
    identities = {str(job.get('owner_id') or '').strip().casefold()}
    identities.update(str(job.get(name) or '').strip().removeprefix('@').casefold()
                      for name in ('client_username', 'employer_username', 'owner_username'))
    if set(config.excluded_clients) & identities:
        reasons.append('Employer username or account ID is excluded')
    rating = parse_number(job.get('client_rating'))
    if config.min_client_rating and (rating is None or not config.min_client_rating <= rating <= 5):
        reasons.append('Employer rating is unknown or below your minimum')
    reviews = parse_number(job.get('client_reviews'))
    if config.min_client_reviews and (reviews is None or not reviews.is_integer() or reviews < config.min_client_reviews):
        reasons.append('Employer review count is unknown or below your minimum')
    # Match the chosen fields using the same whole-term aliases as normal scoring.
    from .logic import ALIASES
    skills = [normalized_text(item) for item in re.split(r'[,;\n]+', str(getattr(user, 'skills', '') or '')) if item.strip()]
    selected_text = _job_text(job, config.search_fields)
    if not any(contains_term(selected_text, candidate) for skill in skills for candidate in [skill] + ALIASES.get(skill, [])):
        reasons.append('No profile skill matched in your selected project search fields')
    if automatic and any(contains_term(_job_text(job, SEARCH_FIELDS), term) for term in config.manual_review_keywords):
        reasons.append('Project contains a keyword requiring manual review')
    if config.max_job_age_minutes:
        posted = parse_number(job.get('posted_at'))
        current = _utc_now(now).timestamp()
        if posted is None or posted <= 0 or posted > current + 300:
            reasons.append('Project posting time is unknown or invalid for your age filter')
        elif current - posted > config.max_job_age_minutes * 60:
            reasons.append('Project is older than your maximum project age')
    return reasons


def render_proposal_template(job, user):
    """Render the first matching enabled rule, then the optional default.

    Values are plain supplied facts, never evaluated or recursively formatted.
    A rule's name, selection order and source project text remain untrusted data.
    """
    config = load_advanced(user)
    text = _job_text(job, config.search_fields)
    template = config.default_template
    for rule in config.keyword_templates:
        checks = [contains_term(text, keyword) for keyword in rule.keywords]
        if rule.enabled and (all(checks) if rule.match_mode == 'all' else any(checks)):
            template = rule.template
            break
    if not template:
        return None
    values = {
        'client_name': str(job.get('client_name') or 'Client')[:80],
        'project_title': str(job.get('title') or 'your project')[:180],
        'skills': str(getattr(user, 'skills', '') or '')[:1000],
        'portfolio': str(getattr(user, 'portfolio', '') or '')[:500],
        'signature': str(getattr(user, 'name', '') or '')[:100],
        'delivery_days': str(getattr(user, 'default_days', '') or '')[:3],
        'project_description': str(job.get('description') or '')[:300],
    }
    try:
        rendered = template.format_map(values).strip()
    except (KeyError, ValueError, AttributeError):
        raise AdvancedConfigError('Saved proposal template is invalid. Review it before generating a proposal.') from None
    if not 30 <= len(rendered) <= 2800:
        raise AdvancedConfigError('Expanded proposal must contain 30 to 2800 characters. Shorten or complete its template.')
    return rendered
