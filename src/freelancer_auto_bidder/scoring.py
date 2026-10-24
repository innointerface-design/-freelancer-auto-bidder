"""Explainable service matching and conservative, currency-specific eligibility."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal

from .config import Settings
from .models import Project


@dataclass(frozen=True)
class Assessment:
    project_id: int
    score: int
    decision: str
    reasons: tuple[str, ...]
    matched_services: tuple[str, ...]
    bid_amount: Decimal | None


_SERVICES = {
    "short_form": r"\b(?:short[- ]form|youtube\s+shorts?|instagram\s+reels?|"
    r"tiktok|tik\s+tok|reels?)\b",
    "motion_graphics": r"\b(?:motion\s+graphics?|after\s+effects|adobe\s+ae)\b",
    "ai_video": r"\b(?:ai[- ](?:video|animation|animated)|(?:video|animation)\s+(?:using|with)"
    r"\s+ai|artificial\s+intelligence\s+(?:video|animation))\b",
    "product_video": r"\b(?:(?:product|promotional|promo)\s+(?:videos?|ads?|commercials?)|"
    r"video\s+(?:ads?|commercials?))\b",
    "video_editing": r"\b(?:video\s+edit(?:ing|or|ors)?|edit(?:ing)?\s+(?:a\s+|the\s+)?"
    r"(?:videos?|footage)|premiere\s+pro|davinci\s+resolve)\b",
}

_SOFTWARE_BUILD = re.compile(
    r"\b(?:build|develop|code|program|implement|create)\s+"
    r"(?:(?:a|an|the|our|new|custom|responsive|video[- ]streaming|streaming|social|"
    r"ai[- ]video|video[- ]editing|tiktok|youtube|motion[- ]graphics)\s+){0,4}"
    r"\b(?:website|web\s*app|mobile\s*app|application|api|platform|plugin|software|bot)\b",
    re.I | re.S,
)

_NEGATED_PREFIX = re.compile(
    r"\b(?:no|without|not|not\s+(?:looking\s+for|seeking)|"
    r"(?:do\s+not|don't)\s+(?:want|need|require)|no\s+need\s+for)\s+(?:any\s+)?$",
    re.I,
)
_NEGATED_SUFFIX = re.compile(
    r"^(?:\s+(?:is|are))?\s+(?:not\s+(?:needed|required|wanted)|unnecessary)\b",
    re.I,
)
_ONSITE_REQUIREMENT = re.compile(
    r"\b(?:must|need(?:ed)?\s+to|required\s+to|you\s+will|you\s+must)\s+"
    r"(?:attend|film|shoot|record)\b.{0,45}\b(?:on[- ]site|on[- ]location|in[- ]person)\b|"
    r"\b(?:on[- ]site|on[- ]location|in[- ]person)\s+(?:camera\s+)?"
    r"(?:filming|shoot(?:ing)?|videography|video\s+shoot)\s+"
    r"(?:is\s+)?(?:required|needed|mandatory)\b",
    re.I,
)


def _service_requests(pattern: str, text: str) -> tuple[bool, bool]:
    requested = negated = False
    for match in re.finditer(pattern, text, re.I):
        prefix, suffix = text[max(0, match.start() - 70) : match.start()], text[match.end() :]
        if _NEGATED_PREFIX.search(prefix) or _NEGATED_SUFFIX.search(suffix):
            negated = True
        else:
            requested = True
    return requested, negated


def evaluate(project: Project, settings: Settings, *, now: float | None = None) -> Assessment:
    """Score 0–100: service 50, budget 15, preferred country 15, payment 10, reviews 10.

    Eligibility failures override the numeric score. Currency rules use nominal
    amounts in the API's currency, never inferred country or foreign-exchange rates.
    Unknown country/payment, budget, status, or posting time cannot silently pass
    their configured gates. Reviews are scored separately from completed projects.
    """
    now = time.time() if now is None else now
    content_text = "\n".join((project.title, project.description))
    requested_services = []
    for name, pattern in _SERVICES.items():
        description_requested, description_negated = _service_requests(pattern, project.description)
        title_requested, _ = _service_requests(pattern, project.title)
        if description_requested or (title_requested and not description_negated):
            requested_services.append(name)
    matched = tuple(requested_services)
    # Search tags alone cannot establish what the client actually wants delivered.
    if _SOFTWARE_BUILD.search(content_text):
        matched = ()
    if matched == ("short_form",) and not re.search(
        r"\b(?:videos?|edit\w*|reels?|short[- ]form|animation|animate)\b",
        content_text,
        re.I,
    ):
        matched = ()
    reasons: list[str] = []
    failures: list[str] = []
    if _ONSITE_REQUIREMENT.search(content_text):
        failures.append("Project requires on-site camera filming beyond the configured services")
    score = 0
    if matched:
        score += 50
        reasons.append("Service match: " + ", ".join(matched))
    else:
        failures.append("No relevant video production or editing service match")
    relevant_skills = [
        skill
        for skill in project.skills
        if any(re.search(pattern, skill, re.I) for pattern in _SERVICES.values())
    ]
    if relevant_skills:
        reasons.append("Relevant listed skills: " + ", ".join(relevant_skills))
    if len(project.description.split()) < 6 or len(project.description) < 30:
        failures.append("Project description is too incomplete to assess requirements")
    if project.project_type != "fixed":
        failures.append("Only fixed-price projects are supported in this version")
    if project.status != "active":
        failures.append("Project is not confirmed active")
    if project.time_submitted is None:
        failures.append("Project posting time is unknown")
    elif project.time_submitted > now + 300:
        failures.append("Project posting time is in the future")
    elif now - project.time_submitted > settings.max_project_age_hours * 3600:
        failures.append("Project is older than the configured age limit")

    amount = None
    rule = settings.budgets.get(project.currency)
    if rule is None:
        failures.append("Project currency has no configured budget rule")
    elif project.budget_min is None or project.budget_max is None:
        failures.append("Project budget minimum or maximum is unknown")
    elif project.budget_min > project.budget_max:
        failures.append("Project budget minimum exceeds maximum")
    elif project.budget_max < rule.min_budget:
        failures.append("Project budget is below the configured minimum")
    else:
        candidate = max(rule.bid_amount, project.budget_min)
        candidate = min(candidate, project.budget_max)
        if candidate < rule.min_budget or candidate > rule.max_bid:
            failures.append("Project budget cannot fit the configured bidding range")
        else:
            amount = candidate.quantize(Decimal("0.01"))
            if amount < project.budget_min or amount > project.budget_max:
                amount = None
                failures.append("Project budget cannot fit a two-decimal bid amount")
            else:
                score += 15
                reasons.append(f"Budget fits configured {project.currency} limits")

    if project.country_code in settings.preferred_countries:
        score += 15
        reasons.append(f"Preferred client country: {project.country_code} (API owner record)")
    elif project.country_code or project.country_name:
        reasons.append("Client country is outside the preferred list (API owner record)")
    else:
        reasons.append("Client country is unknown; no location inferred")
        if settings.require_known_country:
            failures.append("A known client country is required")

    if project.payment_verified is True:
        score += 10
        reasons.append("Client payment is verified in the API owner record")
    else:
        reasons.append(
            "Client payment is unverified"
            if project.payment_verified is False
            else "Client payment verification is unknown"
        )
        if settings.require_verified_payment:
            failures.append("Verified client payment is required")

    if project.client_rating is not None and project.client_reviews:
        if project.client_rating < settings.min_client_rating:
            failures.append("Client rating is below the configured minimum")
        else:
            score += 10
            reasons.append(
                f"Client rating {project.client_rating:g}/5 from {project.client_reviews} reviews"
            )
    else:
        reasons.append("Client has no usable employer review history")
    if score < settings.score_threshold:
        failures.append(f"Score {score} is below the BID threshold {settings.score_threshold}")
    return Assessment(
        project_id=project.id,
        score=score,
        decision="SKIP" if failures else "BID",
        reasons=tuple(failures + reasons),
        matched_services=matched,
        bid_amount=amount,
    )
