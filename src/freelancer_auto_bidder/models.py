"""Normalize documented API fields without inventing missing client information."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class Project:
    id: int
    owner_id: int
    title: str
    description: str
    currency: str
    budget_min: Decimal | None
    budget_max: Decimal | None
    project_type: str
    status: str
    time_submitted: float | None
    country_code: str | None
    country_name: str | None
    country_source: str | None
    payment_verified: bool | None
    client_rating: float | None
    client_reviews: int | None
    skills: tuple[str, ...]


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Project and owner IDs must be positive integers")
    if isinstance(value, str) and not value.isdigit():
        raise ValueError("Project and owner IDs must be positive integers")
    if not isinstance(value, (int, str)):
        raise ValueError("Project and owner IDs must be positive integers")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("Project and owner IDs must be positive integers")
    return parsed


def _money(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _number(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and minimum <= number <= maximum else None


_COUNTRY_ALIASES = {
    "US": ("US", "USA", "United States", "United States of America"),
    "GB": ("GB", "GBR", "UK", "United Kingdom", "Great Britain", "Britain"),
    "CA": ("CA", "CAN", "Canada"),
    "AU": ("AU", "AUS", "Australia"),
    "AE": ("AE", "ARE", "UAE", "United Arab Emirates"),
    "IN": ("IN", "IND", "India"),
    "PK": ("PK", "PAK", "Pakistan"),
    "BD": ("BD", "BGD", "Bangladesh"),
    "FR": ("FR", "FRA", "France"),
    "DE": ("DE", "DEU", "Germany"),
    "NZ": ("NZ", "NZL", "New Zealand"),
    "SA": ("SA", "SAU", "Saudi Arabia"),
    "SG": ("SG", "SGP", "Singapore"),
    "ZA": ("ZA", "ZAF", "South Africa"),
}


def _country_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


_COUNTRY_LOOKUP = {
    _country_key(alias): code for code, aliases in _COUNTRY_ALIASES.items() for alias in aliases
}


def _country(owner: dict) -> tuple[str | None, str | None, str | None]:
    country = _object(_object(owner.get("location")).get("country"))
    raw_code, raw_name = _text(country.get("code")), _text(country.get("name"))
    if not raw_code and not raw_name:
        return None, None, None
    code = _COUNTRY_LOOKUP.get(_country_key(raw_code))
    if not code and re.fullmatch(r"[A-Za-z]{2}", raw_code):
        code = raw_code.upper()
    name_code = _COUNTRY_LOOKUP.get(_country_key(raw_name))
    if raw_code and code is None:
        return None, None, None
    if code and name_code and code != name_code:
        return None, None, None
    # A preferred-country code paired with an unrecognised, different name is ambiguous.
    if code in {"US", "GB", "CA", "AU", "AE"} and raw_name and name_code != code:
        return None, None, None
    return code or name_code, raw_name or None, "owner.location.country"


def parse_project(raw: dict, users: dict) -> Project:
    """Join owner_id to result.users; project.location is deliberately never read.

    Embedded owners must identify the same account. A conflicting owner record is
    not usable for client assessment. Unknown/malformed optional fields remain None.
    """
    if not isinstance(raw, dict) or not isinstance(users, dict):
        raise ValueError("Expected project and users objects")
    project_id = _positive_id(raw.get("id"))
    owner_id = _positive_id(raw.get("owner_id"))
    owner = _object(users.get(str(owner_id), users.get(owner_id)))
    if owner:
        # A keyed users record may omit id; when supplied it must agree with the key.
        try:
            if owner.get("id") is not None and _positive_id(owner["id"]) != owner_id:
                owner = {}
        except ValueError:
            owner = {}
    else:
        embedded = _object(raw.get("owner"))
        try:
            owner = embedded if _positive_id(embedded.get("id")) == owner_id else {}
        except ValueError:
            owner = {}
    country_code, country_name, country_source = _country(owner)
    status = _object(owner.get("status"))
    payment = status.get("payment_verified")
    history = _object(_object(owner.get("employer_reputation")).get("entire_history"))
    rating = _number(history.get("overall"), 0, 5)
    reviews = history.get("reviews")
    if type(reviews) is not int or reviews < 0:
        reviews = None
    budget, currency = _object(raw.get("budget")), _object(raw.get("currency"))
    skills = raw.get("jobs")
    if not isinstance(skills, list):
        skills = []
    return Project(
        id=project_id,
        owner_id=owner_id,
        title=_text(raw.get("title")),
        description=_text(raw.get("description")),
        currency=_text(currency.get("code")).upper(),
        budget_min=_money(budget.get("minimum")),
        budget_max=_money(budget.get("maximum")),
        project_type=_text(raw.get("type")).lower(),
        status=_text(raw.get("status")).lower(),
        time_submitted=_number(raw.get("time_submitted"), 0, 1e12),
        country_code=country_code,
        country_name=country_name,
        country_source=country_source,
        payment_verified=payment if type(payment) is bool else None,
        client_rating=rating,
        client_reviews=reviews,
        skills=tuple(
            _text(job.get("name"))
            for job in skills
            if isinstance(job, dict) and _text(job.get("name"))
        ),
    )
