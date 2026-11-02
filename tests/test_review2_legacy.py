"""Remaining cross-platform and malformed-provider regression cases."""

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from freelancer_auto_bidder import store as store_module
from freelancer_auto_bidder.api import APIError, FreelancerAPI, SubmissionUncertain
from freelancer_auto_bidder.config import Settings
from freelancer_auto_bidder.models import parse_project
from freelancer_auto_bidder.runner import Runner
from freelancer_auto_bidder.scoring import evaluate
from freelancer_auto_bidder.store import Store, write_report
from freelancer_auto_bidder.web.ai import AIError, ProposalGenerator


def test_report_replacement_works_without_unix_fchmod(tmp_path, monkeypatch):
    # Native Windows has no os.fchmod; tempfile still securely creates the file.
    monkeypatch.delattr(store_module.os, "fchmod", raising=False)
    path = tmp_path / "private" / "report.json"
    write_report(path, [{"id": 1, "title": "First report"}])
    write_report(path, [{"id": 2, "title": "Updated report"}])
    assert json.loads(path.read_text()) == [{"id": 2, "title": "Updated report"}]
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_deep_api_json_remains_sanitized_and_posts_are_never_retried(method):
    calls = []
    depth = 10000
    body = '{"status":"success","result":{"data":' + '[' * depth + '0' + ']' * depth + '}}'
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, text=body)
    ))
    settings = replace(Settings(token="test-only-token"), dry_run=False,
                       enable_auto_bidding=True, permission_verified=True,
                       permission_reference="synthetic approval")
    api = FreelancerAPI(settings, client=client, sleep=lambda _: None)
    payload = {"project_id": 1, "bidder_id": 2, "description": "Reviewed proposal",
               "amount": Decimal("50"), "period": 3, "milestone_percentage": 100}
    expected = APIError if method == "GET" else SubmissionUncertain
    try:
        with pytest.raises(expected, match="Unrecognized") as error:
            api.get_self_id() if method == "GET" else api.submit_bid(payload)
        assert len(calls) == 1
        assert body not in str(error.value)
    finally:
        api.close()


@pytest.mark.parametrize("field", ["id", "owner_id"])
def test_out_of_database_range_project_ids_skip_only_bad_listing(tmp_path, field):
    bad = {"id": 3, "owner_id": 4, field: 2**63}
    good = {"id": 1, "owner_id": 2, "title": "Valid listing"}
    settings = replace(Settings(token="test-only-token"), search_queries=("video editing",),
                       database_path=str(tmp_path / "bidder.db"),
                       report_path=str(tmp_path / "report.json"))
    api = SimpleNamespace(begin_cycle=lambda: None,
                          search_projects=lambda *args, **kwargs: {
                              "projects": [bad, good], "users": {}})
    with Store(settings.database_path) as database:
        reports = Runner(settings, api, database).run_once()
        assert [row["id"] for row in reports] == [1]
        assert database.connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 1
    assert [row["id"] for row in json.loads((tmp_path / "report.json").read_text())] == [1]


@pytest.mark.parametrize("field", ["id", "owner_id"])
def test_signed_database_id_boundary_stays_usable(field):
    project = parse_project({"id": 1, "owner_id": 2, field: 2**63 - 1}, {})
    assert getattr(project, field) == 2**63 - 1


@pytest.mark.parametrize("field", ["title", "description", "skills", "country"])
def test_invalid_unicode_listing_does_not_break_the_scan_or_report(tmp_path, field):
    bad = {"id": 3, "owner_id": 4, "title": "Malformed listing"}
    users = {}
    if field == "skills":
        bad["jobs"] = [{"name": "Invalid \ud800"}]
    elif field == "country":
        users["4"] = {"location": {"country": {"name": "Invalid \ud800"}}}
    else:
        bad[field] = "Invalid \ud800"
    good = {"id": 1, "owner_id": 2, "title": "Valid café video 🎬"}
    settings = replace(Settings(token="test-only-token"), search_queries=("video editing",),
                       database_path=str(tmp_path / "bidder.db"),
                       report_path=str(tmp_path / "report.json"))
    api = SimpleNamespace(begin_cycle=lambda: None, search_projects=lambda *args, **kwargs: {
        "projects": [bad, good], "users": users,
    })
    with Store(settings.database_path) as database:
        reports = Runner(settings, api, database).run_once()
        assert [row["id"] for row in reports] == [1]
    assert json.loads((tmp_path / "report.json").read_text())[0]["title"] == good["title"]


@pytest.mark.parametrize("identifier", [2**63, 10**100])
def test_invalid_bid_receipt_id_is_uncertain_not_success(identifier):
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(
            200, json={"status": "success", "result": {"id": identifier}}
        )
    ))
    settings = replace(Settings(token="test-only-token"), dry_run=False,
                       enable_auto_bidding=True, permission_verified=True,
                       permission_reference="synthetic approval")
    api = FreelancerAPI(settings, client=client)
    try:
        with pytest.raises(SubmissionUncertain, match="valid ID"):
            api.submit_bid({"project_id": 1, "bidder_id": 2, "description": "Reviewed proposal",
                            "amount": Decimal("50"), "period": 3, "milestone_percentage": 100})
        assert len(calls) == 1
    finally:
        api.close()


@pytest.mark.parametrize("failure", ["surrogate", "deep_json", "length", "content_filter"])
def test_invalid_ai_completion_is_a_safe_drafting_error(failure):
    project = parse_project({"id": 1, "owner_id": 2, "title": "Video editing"}, {})
    payload = {"choices": [{"message": {"content": "A useful draft"}, "finish_reason": "stop"}]}
    if failure == "surrogate":
        payload["choices"][0]["message"]["content"] = "Invalid draft \ud800"
    elif failure in {"length", "content_filter"}:
        payload["choices"][0]["finish_reason"] = failure
    body = json.dumps(payload)
    if failure == "deep_json":
        depth = 10000
        body = '[' * depth + '0' + ']' * depth
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, text=body)
    ))
    try:
        generator = ProposalGenerator("synthetic-ai-key", client=client)
        with pytest.raises(AIError, match="invalid or oversized") as error:
            generator.generate(project, evaluate(project, Settings()))
        assert len(calls) == 1
        assert "Invalid draft" not in str(error.value)
    finally:
        client.close()


def test_valid_unicode_ai_completion_still_returns_complete_draft():
    project = parse_project({"id": 1, "owner_id": 2, "title": "Video editing"}, {})
    draft = "I can edit your café’s video 🎬. Please share the source footage."
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": draft}, "finish_reason": "stop"}]}
    ))) as client:
        result = ProposalGenerator("synthetic-ai-key", client=client).generate(
            project, evaluate(project, Settings()))
    assert result == {"text": draft, "provider": "openai"}
    assert json.dumps(result, ensure_ascii=False).encode("utf-8")
