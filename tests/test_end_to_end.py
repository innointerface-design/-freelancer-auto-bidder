"""Exercise the CLI, real API adapter, scoring, proposals and disk ledger together."""

import json
import time

import httpx
import pytest

from freelancer_auto_bidder.api import FreelancerAPI
from freelancer_auto_bidder.cli import main


@pytest.mark.parametrize("live", [False, True])
def test_cli_cycle_uses_official_contract_and_never_duplicates(tmp_path, monkeypatch, live):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FREELANCER_OAUTH_TOKEN", "integration-fixture-token")
    monkeypatch.setenv("FREELANCER_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("FREELANCER_DRY_RUN", str(not live).lower())
    monkeypatch.setenv("FREELANCER_ENABLE_AUTO_BIDDING", str(live).lower())
    monkeypatch.setenv("FREELANCER_AUTO_BID_PERMISSION_VERIFIED", str(live).lower())
    monkeypatch.setenv("FREELANCER_PERMISSION_REFERENCE", "fixture approval" if live else "")
    config = tmp_path / "config.toml"
    config.write_text('search_queries = ["YouTube Shorts"]\n')
    project = {
        "id": 123,
        "owner_id": 99,
        "title": "YouTube Shorts video editor",
        "description": "Edit a 30 second vertical video with captions for YouTube Shorts.",
        "budget": {"minimum": 50, "maximum": 250},
        "currency": {"code": "USD"},
        "type": "fixed",
        "status": "active",
        "time_submitted": time.time() - 20,
    }
    collection = {
        "projects": [project],
        "users": {
            "99": {
                "id": 99,
                "location": {"country": {"code": "CA", "name": "Canada"}},
                "status": {"payment_verified": True},
                "employer_reputation": {"entire_history": {"overall": 4.8, "reviews": 10}},
            }
        },
    }
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "www.freelancer-sandbox.com"
        path = request.url.path
        if path == "/api/projects/0.1/bids/":
            assert live and request.method == "POST"
            payload = json.loads(request.content)
            assert payload["amount"] == 100 and payload["bidder_id"] == 77
            assert "captions" in payload["description"] and "30-second" in payload["description"]
            result = {"id": 555}
        elif path.endswith("/bids/"):
            result = {"bids": []}
        elif path == "/api/users/0.1/self/":
            result = {"id": 77}
        else:
            assert request.method == "GET"
            result = collection
        return httpx.Response(200, json={"status": "success", "result": result})

    monkeypatch.setattr(
        "freelancer_auto_bidder.cli.FreelancerAPI",
        lambda settings, **kwargs: FreelancerAPI(
            settings,
            client=httpx.Client(transport=httpx.MockTransport(handle)),
            sleep=lambda _: None,
            **kwargs,
        ),
    )
    for _ in range(2):
        assert main(["--config", str(config), "run", "--once"]) == 0
    posts = [r for r in requests if r.method == "POST"]
    assert len(posts) == (1 if live else 0)
    reports = json.loads((tmp_path / "data/recommendations.json").read_text())
    assert reports[0]["decision"] == "BID"
    assert reports[0]["country"]["code"] == "CA"
    assert reports[0]["submission_status"] == ("local_duplicate:submitted" if live else "dry_run")
