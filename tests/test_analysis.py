from dataclasses import replace
from decimal import Decimal

import pytest

from freelancer_auto_bidder.config import Settings
from freelancer_auto_bidder.models import parse_project
from freelancer_auto_bidder.scoring import evaluate

NOW = 1_800_000_000.0


@pytest.fixture
def api_record():
    return {
        "id": 123,
        "owner_id": 42,
        "title": "YouTube Shorts video editor",
        "description": "Edit five YouTube Shorts from supplied footage with captions in 9:16.",
        "currency": {"code": "USD"},
        "budget": {"minimum": 50, "maximum": 250},
        "type": "fixed",
        "status": "active",
        "time_submitted": NOW - 120,
        "jobs": [{"name": "Video Editing"}],
    }


@pytest.fixture
def users():
    return {
        "42": {
            "id": 42,
            "location": {"country": {"code": "US", "name": "United States"}},
            "status": {"payment_verified": True},
            "employer_reputation": {"entire_history": {"overall": 4.9, "reviews": 12, "all": 20}},
        }
    }


def test_eligible_job_has_explainable_full_score(api_record, users):
    project = parse_project(api_record, users)
    result = evaluate(project, Settings(), now=NOW)
    assert (result.decision, result.score, result.bid_amount) == ("BID", 100, Decimal("100.00"))
    assert result.matched_services == ("short_form", "video_editing")
    assert project.client_reviews == 12  # completed project count is a different field


@pytest.mark.parametrize(
    ("currency", "project_country"),
    [
        ("USD", "US"),
        ("GBP", "GB"),
        ("AED", "AE"),
    ],
)
def test_never_infers_country_from_currency_or_project_location(
    api_record,
    currency,
    project_country,
):
    api_record["currency"]["code"] = currency
    api_record["location"] = {"country": {"code": project_country}}
    project = parse_project(api_record, {})
    assert (project.country_code, project.country_name, project.country_source) == (
        None,
        None,
        None,
    )
    assert evaluate(project, Settings(), now=NOW).decision == "SKIP"


@pytest.mark.parametrize(
    ("code", "name", "expected"),
    [
        ("USA", "United States of America", "US"),
        ("UK", "United Kingdom", "GB"),
        ("", "Canada", "CA"),
        ("AU", "Australia", "AU"),
        ("UAE", "United Arab Emirates", "AE"),
    ],
)
def test_normalizes_preferred_countries(api_record, users, code, name, expected):
    users["42"]["location"]["country"] = {"code": code, "name": name}
    project = parse_project(api_record, users)
    assert project.country_code == expected
    assert project.country_source == "owner.location.country"


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("US", "Canada"),
        ("IN", "United States"),
        ("GB", "Germany"),
        ("US", "Atlantis"),
    ],
)
def test_conflicting_country_data_is_unknown(api_record, users, code, name):
    users["42"]["location"]["country"] = {"code": code, "name": name}
    project = parse_project(api_record, users)
    assert project.country_code is None
    assert project.country_source is None


def test_must_join_correct_owner(api_record, users):
    users["42"]["id"] = 100
    project = parse_project(api_record, users)
    assert project.country_code is None
    assert project.payment_verified is None


def test_embedded_owner_must_identify_client(api_record, users):
    api_record["owner"] = users["42"]
    assert parse_project(api_record, {}).country_code == "US"
    api_record["owner"]["id"] = 999
    assert parse_project(api_record, {}).country_code is None


@pytest.mark.parametrize("payment", [False, None, "true", "false", 1])
def test_payment_requires_actual_true_boolean(api_record, users, payment):
    users["42"]["status"]["payment_verified"] = payment
    project = parse_project(api_record, users)
    result = evaluate(project, Settings(), now=NOW)
    assert project.payment_verified is not True
    assert result.decision == "SKIP"
    assert any("payment" in reason for reason in result.reasons)


def test_nonpreferred_country_receives_no_priority_bonus(api_record, users):
    users["42"]["location"]["country"] = {"code": "IN", "name": "India"}
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "BID"
    assert result.score == 85


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected_amount"),
    [
        (50, 75, "75.00"),
        (150, 250, "150.00"),
        (50, 1000, "100.00"),
    ],
)
def test_amount_respects_config_and_project_range(
    api_record,
    users,
    minimum,
    maximum,
    expected_amount,
):
    api_record["budget"] = {"minimum": minimum, "maximum": maximum}
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "BID"
    assert result.bid_amount == Decimal(expected_amount)


@pytest.mark.parametrize(
    "budget",
    [
        {},
        {"minimum": 50},
        {"minimum": 250, "maximum": 200},
        {"minimum": 5, "maximum": 10},
        {"minimum": 300, "maximum": 500},
        {"minimum": "NaN", "maximum": 250},
        {"minimum": 50, "maximum": "Infinity"},
    ],
)
def test_bad_or_unaffordable_budgets_skip(api_record, users, budget):
    api_record["budget"] = budget
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert result.bid_amount is None


def test_unknown_currency_never_converts(api_record, users):
    api_record["currency"] = {"code": "GBP"}
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert result.bid_amount is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "closed"),
        ("status", ""),
        ("type", "hourly"),
        ("time_submitted", None),
        ("time_submitted", NOW - 90_000),
        ("time_submitted", NOW + 600),
        ("description", "Need video editor"),
    ],
)
def test_ineligible_project_overrides_high_score(api_record, users, field, value):
    api_record[field] = value
    assert evaluate(parse_project(api_record, users), Settings(), now=NOW).decision == "SKIP"


@pytest.mark.parametrize(
    ("title", "description"),
    [
        (
            "Build a video streaming website",
            "Build a video streaming website with account login and billing.",
        ),
        (
            "Develop AI video app",
            "Develop an AI video application for users to submit their videos online.",
        ),
        (
            "Create motion graphics website",
            "Create a website with motion graphics portfolio and a contact form.",
        ),
        ("Python developer", "Need a Python developer to create ETL jobs for our data warehouse."),
        (
            "TikTok account manager",
            "Need a TikTok account manager to plan hashtags and post schedules.",
        ),
    ],
)
def test_irrelevant_software_jobs_skip_even_with_skill_tags(api_record, users, title, description):
    api_record.update(title=title, description=description)
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert result.matched_services == ()


def test_video_for_a_website_is_a_production_task(api_record, users):
    api_record.update(
        title="Create product video for our website",
        description="Create a product video for our website using the supplied product footage.",
    )
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "BID"
    assert "product_video" in result.matched_services


@pytest.mark.parametrize(
    "description",
    [
        "No video editing is required; we only need a calendar of content topics.",
        "Video editing is not required; we need help with content calendar research.",
        "We do not need video editing, only spreadsheet planning for our account.",
    ],
)
def test_explicit_service_negation_overrides_title_and_skill_tag(api_record, users, description):
    api_record.update(title="Video editing planning", description=description)
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert result.matched_services == ()


@pytest.mark.parametrize(
    "requirement",
    [
        "You must shoot on-site in London and edit the product video.",
        "On-site camera shooting is required followed by video editing of our event.",
    ],
)
def test_mandatory_on_site_filming_is_outside_services(api_record, users, requirement):
    api_record["description"] = requirement
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert any("on-site camera filming" in reason for reason in result.reasons)


def test_client_provided_on_site_footage_does_not_require_local_shoot(api_record, users):
    api_record["description"] = (
        "Edit our product video using supplied footage from our on-site camera shooting."
    )
    assert evaluate(parse_project(api_record, users), Settings(), now=NOW).decision == "BID"


def test_rating_is_not_completed_project_count(api_record, users):
    history = users["42"]["employer_reputation"]["entire_history"]
    history.pop("reviews")
    history["all"] = 500
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.score == 90
    assert "no usable employer review history" in " ".join(result.reasons)


def test_low_client_rating_skips(api_record, users):
    users["42"]["employer_reputation"]["entire_history"]["overall"] = 2.0
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "SKIP"
    assert any("rating is below" in reason for reason in result.reasons)


def test_first_time_client_with_no_review_history_can_be_recommended(api_record, users):
    users["42"]["employer_reputation"]["entire_history"] = {"overall": 0, "reviews": 0}
    result = evaluate(parse_project(api_record, users), Settings(), now=NOW)
    assert result.decision == "BID"
    assert result.score == 90


def test_configurable_unknown_country_gate_does_not_invent_priority(api_record, users):
    users["42"].pop("location")
    result = evaluate(
        parse_project(api_record, users),
        replace(Settings(), require_known_country=False),
        now=NOW,
    )
    assert result.decision == "BID"
    assert result.score == 85


@pytest.mark.parametrize("invalid_id", [None, True, -1, 0, "not-an-id", 1.5])
def test_bad_identifiers_are_rejected(api_record, invalid_id):
    api_record["id"] = invalid_id
    with pytest.raises(ValueError):
        parse_project(api_record, {})
