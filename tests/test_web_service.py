from dataclasses import replace
from types import SimpleNamespace

import pytest

from freelancer_auto_bidder.api import (
    AuthenticationError,
    OperationCancelled,
    RateLimited,
    SubmissionUncertain,
)
from freelancer_auto_bidder.config import Settings
from freelancer_auto_bidder.store import Store
from freelancer_auto_bidder.web.demo import DemoAPI
from freelancer_auto_bidder.web.repository import WebRepository
from freelancer_auto_bidder.web.service import WebService, WebServiceError


def make_service(tmp_path, *, demo=True, live=False, api_factory=None, **values):
    engine = Settings(
        token="unit-test-token" if not demo else "",
        database_path=str(tmp_path / "engine.sqlite3"),
        report_path=str(tmp_path / "report.json"),
        search_queries=("video",),
        dry_run=not live,
        enable_auto_bidding=live,
        permission_verified=live,
        permission_reference="test-fixture-approval" if live else "",
    )
    websettings = SimpleNamespace(
        engine=engine,
        database_path=str(tmp_path / "web.sqlite3"),
        demo_mode=demo,
        openai_key="",
        openai_model="gpt-4o-mini",
        **values,
    )
    repository = WebRepository(websettings.database_path)
    options = {"api_factory": api_factory} if api_factory else {}
    return WebService(websettings, repository, **options)


class RecordingAPI(DemoAPI):
    instances = []

    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.submissions = []
        self.instances.append(self)

    def submit_bid(self, payload):
        self._check_stop()
        self.submissions.append(payload)
        return 60000 + payload["project_id"]


@pytest.fixture(autouse=True)
def reset_recording():
    RecordingAPI.instances = []


def test_default_dashboard_is_empty_with_monitoring_and_auto_off(tmp_path):
    service = make_service(tmp_path, demo=False)
    state = service.dashboard()
    assert state["mode"] == "dry_run"
    assert not state["monitor_enabled"] and not state["auto_bidder_enabled"]
    assert state["stats"]["jobs_total"] == 0
    assert state["stats"]["submitted_total"] == 0
    assert all(row["submitted"] == 0 for row in state["chart"])
    service.monitor_tick()
    assert service.repository.runtime()["last_run_at"] is None


def test_demo_uses_existing_runner_scoring_without_official_api(tmp_path):
    service = make_service(tmp_path, api_factory=lambda *_a, **_k: pytest.fail("real API in demo"))
    result = service.search("video")
    assert result["total"] == 8
    assert {item["decision"] for item in result["items"]} == {"BID", "SKIP"}
    assert all("_project" not in item for item in result["items"])
    assert all(item["description"] for item in result["items"])
    assert all(item["updated_at"] for item in result["items"])
    assert any(item["country"]["code"] == "US" for item in result["items"])
    assert any(item["country"]["code"] == "GB" for item in result["items"])
    assert service.history() == {"items": [], "total": 0}
    assert service.dashboard()["mode"] == "demo"
    assert service.dashboard()["stats"]["submitted_total"] == 0


def test_missing_credentials_do_not_construct_api(tmp_path):
    service = make_service(
        tmp_path, demo=False, api_factory=lambda *_a, **_k: pytest.fail("unconfigured API")
    )
    service.settings.engine = replace(service.settings.engine, token="")
    with pytest.raises(WebServiceError, match="OAUTH_TOKEN") as error:
        service.search()
    assert error.value.status_code == 503
    assert not service.dashboard()["configured"]


def test_manual_search_is_dry_run_even_with_live_permission_and_switches(tmp_path):
    service = make_service(tmp_path, demo=False, live=True, api_factory=RecordingAPI)
    service.controls({"auto_bidder_enabled": True})
    service.search("video")
    assert not RecordingAPI.instances[-1].settings.live_enabled
    assert RecordingAPI.instances[-1].submissions == []
    assert service.history()["total"] == 0


def test_auto_on_cannot_override_environment_safety_gates(tmp_path):
    service = make_service(tmp_path, demo=False, live=False, api_factory=RecordingAPI)
    result = service.controls({"auto_bidder_enabled": True})
    assert result["monitor_enabled"] and result["auto_bidder_enabled"]
    assert result["mode"] == "dry_run" and not result["live_allowed"]
    service.monitor_tick()
    assert RecordingAPI.instances[-1].submissions == []


def test_permitted_monitor_uses_existing_atomic_daily_cap_and_history(tmp_path):
    service = make_service(tmp_path, demo=False, live=True, api_factory=RecordingAPI)
    service.update_settings({"pricing": {"max_bids_per_day": 1}})
    service.controls({"auto_bidder_enabled": True})
    service.monitor_tick()
    assert len(RecordingAPI.instances[-1].submissions) == 1
    history = service.history()
    assert history["total"] == 1 and history["items"][0]["status"] == "submitted"
    assert history["items"][0]["bid_id"] is not None
    state = service.dashboard()
    assert state["stats"]["submitted_total"] == 1
    assert state["stats"]["submitted_today"] == 1
    assert state["stats"]["attempted_today"] == 1
    assert state["stats"]["daily_limit"] == 1
    assert state["mode"] == "live"
    assert sum(day["submitted"] for day in state["chart"]) == 1
    service.repository.set_runtime(last_run_at=0)
    service.monitor_tick()
    assert RecordingAPI.instances[-1].submissions == []


def test_schedule_outside_window_preserves_monitoring_but_prevents_submission(tmp_path):
    service = make_service(tmp_path, demo=False, live=True, api_factory=RecordingAPI)
    service.update_settings({"monitor": {"schedule": {"enabled": True, "days": []}}})
    service.controls({"auto_bidder_enabled": True})
    service.monitor_tick()
    assert RecordingAPI.instances[-1].submissions == []
    assert service.dashboard()["mode"] == "dry_run"
    assert service.dashboard()["monitor_enabled"]
    assert not service.dashboard()["schedule_active"]


def test_monitor_respects_minimum_five_minute_interval(tmp_path):
    service = make_service(tmp_path, demo=False, api_factory=RecordingAPI)
    service.controls({"monitor_enabled": True})
    service.monitor_tick()
    service.monitor_tick()
    assert len(RecordingAPI.instances) == 1
    assert service.repository.runtime()["next_run_at"] > service.repository.runtime()["last_run_at"]


def test_off_controls_cancel_live_work_before_post(tmp_path):
    service = make_service(tmp_path, demo=False, live=True)

    class StopAtDuplicateCheck(RecordingAPI):
        def has_existing_bid(self, project_id, bidder_id):
            service.controls({"auto_bidder_enabled": False})
            return False

    service.api_factory = StopAtDuplicateCheck
    service.controls({"auto_bidder_enabled": True})
    service.monitor_tick()
    assert RecordingAPI.instances[-1].submissions == []
    assert service.history()["total"] == 0


def test_process_stop_prevents_new_cycle(tmp_path):
    service = make_service(tmp_path)
    service.stop()
    with pytest.raises(WebServiceError, match="shutting down"):
        service.search()
    assert service.jobs()["total"] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"monitor_enabled": 1},
        {"auto_bidder_enabled": "true"},
        {"dry_run": False},
        {"token": "attempted-secret"},
        {},
    ],
)
def test_controls_reject_non_boolean_and_secret_inputs(tmp_path, patch):
    with pytest.raises(WebServiceError):
        make_service(tmp_path).controls(patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"connection": {"live_allowed": True}},
        {"token": "attempted-secret"},
        {"pricing": {"max_bids_per_day": True}},
        {"pricing": {"max_bids_per_day": 0}},
        {"pricing": {"bid_period_days": 0}},
        {"pricing": {"score_threshold": 101}},
        {"pricing": {"budgets": {"USD": {"bid_amount": "NaN"}}}},
        {"pricing": {"budgets": {"USD": {"bid_amount": "10000"}}}},
        {"pricing": {"budgets": {"USD": {"bid_amount": "1.001"}}}},
        {"pricing": {"budgets": {"usd": {"min_budget": "1"}}}},
        {"monitor": {"schedule": None}},
        {"monitor": {"schedule": {"enabled": "false"}}},
        {"monitor": {"schedule": {"timezone": "Not/A_Zone"}}},
        {"monitor": {"schedule": {"days": [7]}}},
        {"monitor": {"schedule": {"days": [False]}}},
        {"monitor": {"schedule": {"start": "25:00"}}},
        {"monitor": {"search_queries": []}},
        {"ai": {"instructions": "x" * 4001}},
        {"ai": {"model": "https://evil.invalid"}},
        {"ai": {"configured": True}},
    ],
)
def test_settings_reject_unsafe_or_invalid_changes_atomically(tmp_path, patch):
    service = make_service(tmp_path)
    before = service.get_settings()
    with pytest.raises(WebServiceError):
        service.update_settings(patch)
    assert service.get_settings() == before


def test_pricing_prompts_and_schedule_are_separate_and_persist(tmp_path):
    service = make_service(tmp_path)
    service.update_settings(
        {
            "pricing": {"max_bids_per_day": 2, "budgets": {"USD": {"bid_amount": "125.00"}}},
            "monitor": {"schedule": {"timezone": "Europe/London", "days": [0, 1, 2]}},
            "ai": {"instructions": "Emphasize pacing.", "tone": "Friendly"},
        }
    )
    reloaded = make_service(tmp_path)
    config = reloaded.get_settings()
    assert config["pricing"]["max_bids_per_day"] == 2
    assert config["pricing"]["budgets"]["USD"]["bid_amount"] == "125.00"
    assert config["ai"]["instructions"] == "Emphasize pacing."
    assert config["monitor"]["schedule"]["timezone"] == "Europe/London"
    assert "token" not in str(config) and "openai_key" not in str(config)


def test_search_filter_country_skill_currency_budget_and_page(tmp_path):
    service = make_service(tmp_path)
    service.search("video")
    result = service.jobs(
        {
            "countries": "US",
            "currency": "USD",
            "min_budget": 90,
            "max_budget": 250,
            "skill": "video editing",
            "decision": "BID",
        }
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == 70001
    assert service.jobs({"countries": "UNKNOWN"})["total"] == 1
    assert service.jobs({"decision": "SKIP"})["total"] >= 3
    assert len(service.jobs({"limit": 2, "offset": 1})["items"]) == 2
    assert service.jobs({"query": "DaVinci Resolve"})["total"] == 1


@pytest.mark.parametrize(
    "filters",
    [
        {"min_budget": 10},
        {"max_budget": 100},
        {"currency": "USD", "min_budget": 200, "max_budget": 10},
        {"currency": "USD", "min_budget": "NaN"},
        {"countries": "USA"},
        {"currency": "USDZ"},
        {"limit": 101},
        {"sort": "random"},
        {"decision": "BUY"},
    ],
)
def test_invalid_filters_and_cross_currency_budget_comparison_rejected(tmp_path, filters):
    with pytest.raises(WebServiceError):
        make_service(tmp_path).jobs(filters)


def test_generated_and_edited_drafts_survive_restart_without_submission(tmp_path):
    service = make_service(tmp_path)
    service.search("video")
    result = service.generate_proposal(70001)
    assert result["provider"] == "template"
    assert "YouTube Shorts" in result["text"]
    saved = service.save_proposal(70001, "My reviewed short proposal.")
    assert saved["provider"] == "edited"
    reloaded = make_service(tmp_path)
    item = reloaded.jobs({"query": "wellness"})["items"][0]
    assert item["draft"]["text"] == "My reviewed short proposal."
    assert reloaded.dashboard()["stats"]["drafts"] == 1
    assert reloaded.history()["total"] == 0


def test_ai_draft_preferences_are_passed_without_bid_pricing_or_credentials(tmp_path, monkeypatch):
    service = make_service(tmp_path, demo=False, api_factory=RecordingAPI)
    service.settings.openai_key = "test-ai-key"
    service.search("video")
    service.update_settings({"ai": {"instructions": "Keep it direct.", "tone": "Friendly"}})
    calls = []

    class FakeGenerator:
        def __init__(self, api_key):
            assert api_key == "test-ai-key"

        def generate(self, project, assessment, **values):
            calls.append(values)
            return {"text": "A reviewed AI draft.", "provider": "openai"}

        def close(self):
            pass

    monkeypatch.setattr("freelancer_auto_bidder.web.service.ProposalGenerator", FakeGenerator)
    assert service.generate_proposal(70001, "Mention captions.")["provider"] == "openai"
    assert calls[0]["instructions"] == "Keep it direct.\nMention captions."
    assert calls[0]["tone"] == "Friendly"
    assert set(calls[0]) == {"instructions", "tone", "model"}
    assert service.history()["total"] == 0


def test_existing_cli_assessments_are_preserved_but_need_fresh_search_for_ai(tmp_path):
    service = make_service(tmp_path)
    with Store(service.settings.engine.database_path) as store:
        store.save_report(
            "production",
            {
                "id": 77,
                "title": "Legacy CLI report",
                "decision": "SKIP",
                "score": 10,
                "country": {},
                "budget": {},
            },
        )
    assert service.jobs()["total"] == 1
    with pytest.raises(WebServiceError, match="fresh dashboard search"):
        service.generate_proposal(77)


def test_rate_limit_cooldown_is_durable_across_service_restarts(tmp_path):
    class LimitedAPI(RecordingAPI):
        def search_projects(self, *args, **kwargs):
            raise RateLimited(900)

    service = make_service(tmp_path, demo=False, api_factory=LimitedAPI)
    with pytest.raises(WebServiceError) as error:
        service.search()
    assert error.value.status_code == 429
    assert service.repository.runtime()["blocked_until"] > 0
    reloaded = make_service(
        tmp_path, demo=False, api_factory=lambda *_a, **_k: pytest.fail("ignored cooldown")
    )
    with pytest.raises(WebServiceError, match="cooldown"):
        reloaded.search()


def test_manual_searches_are_throttled_across_service_restarts(tmp_path):
    service = make_service(tmp_path, demo=False, api_factory=RecordingAPI)
    service.search("video")
    reloaded = make_service(
        tmp_path, demo=False, api_factory=lambda *_a, **_k: pytest.fail("ignored manual throttle")
    )
    with pytest.raises(WebServiceError, match="five seconds") as error:
        reloaded.search("video")
    assert error.value.status_code == 429


def test_attempt_cap_display_counts_held_attempts_even_when_not_submitted(tmp_path):
    service = make_service(tmp_path)
    with Store(service.settings.engine.database_path) as store:
        assert store.claim("production", 77, 123, 3) == "reserved"
        assert store.claim("production", 77, 124, 3) == "reserved"
        store.finish("production", 77, 124, "rejected")
    state = service.dashboard()
    assert state["stats"]["attempted_today"] == 2
    assert state["stats"]["submitted_today"] == 0


@pytest.mark.parametrize(
    "api_error", [AuthenticationError("do not disclose"), SubmissionUncertain("do not disclose")]
)
def test_permission_or_uncertain_submission_stops_monitoring_safely(tmp_path, api_error):
    class FailedAPI(RecordingAPI):
        def search_projects(self, *args, **kwargs):
            raise api_error

    service = make_service(tmp_path, demo=False, live=True, api_factory=FailedAPI)
    service.controls({"auto_bidder_enabled": True})
    service.monitor_tick()
    assert not service.repository.controls()["monitor_enabled"]
    assert not service.repository.controls()["auto_bidder_enabled"]
    assert "do not disclose" not in str(service.dashboard())


def test_cancellation_during_adapter_read_returns_without_real_bid(tmp_path):
    class CancelledAPI(RecordingAPI):
        def search_projects(self, *args, **kwargs):
            raise OperationCancelled("Stopped before request")

    service = make_service(tmp_path, demo=False, api_factory=CancelledAPI)
    assert service.search()["items"] == []
    assert service.history()["total"] == 0
