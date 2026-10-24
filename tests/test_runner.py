from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier, Event

import pytest

from freelancer_auto_bidder.api import APIError, OperationCancelled, SubmissionUncertain
from freelancer_auto_bidder.config import ConfigError, Settings
from freelancer_auto_bidder.runner import Runner
from freelancer_auto_bidder.store import Store


def project(project_id=101):
    return {
        "id": project_id,
        "owner_id": 11,
        "title": "YouTube Shorts video editing",
        "description": "Edit short-form video content for YouTube Shorts with 9:16 framing, "
        "engaging captions and clean audio. Deliver three videos.",
        "type": "fixed",
        "status": "active",
        "time_submitted": time.time() - 60,
        "currency": {"code": "USD"},
        "budget": {"minimum": 50, "maximum": 250},
        "jobs": [{"name": "Video Editing"}],
    }


def collection(projects=None):
    return {
        "projects": projects if projects is not None else [project()],
        "users": {
            "11": {
                "id": 11,
                "location": {"country": {"code": "US", "name": "United States"}},
                "status": {"payment_verified": True},
                "employer_reputation": {"entire_history": {"overall": 4.9, "reviews": 12}},
            }
        },
    }


def settings(tmp_path, *, live=False, **overrides):
    base = Settings(
        token="unit-test-token",
        search_queries=("video editing", "YouTube Shorts"),
        database_path=str(tmp_path / "private" / "bidder.sqlite3"),
        report_path=str(tmp_path / "private" / "report.json"),
        dry_run=not live,
        enable_auto_bidding=live,
        permission_verified=live,
        permission_reference="test-fixture-permission" if live else "",
    )
    return replace(base, **overrides)


class FakeAPI:
    def __init__(self):
        self.search_result = collection()
        self.fresh_result = None
        self.remote_bid = False
        self.submit_error = None
        self.check_error = None
        self.calls = []
        self.payloads = []

    def begin_cycle(self):
        self.calls.append("begin_cycle")

    def search_projects(self, query, **params):
        self.calls.append(("search", query, params))
        return deepcopy(self.search_result)

    def fetch_project(self, project_id):
        self.calls.append(("fresh", project_id))
        return deepcopy(self.fresh_result or self.search_result)

    def get_self_id(self):
        self.calls.append("self")
        return 77

    def has_existing_bid(self, project_id, bidder_id):
        self.calls.append(("existing_bid", project_id, bidder_id))
        if self.check_error:
            raise self.check_error
        return self.remote_bid

    def submit_bid(self, payload):
        self.calls.append("post")
        self.payloads.append(payload)
        if self.submit_error:
            raise self.submit_error
        return 901


def test_dry_run_creates_one_personalized_report_without_live_calls(tmp_path):
    config = settings(tmp_path)
    api = FakeAPI()
    with Store(config.database_path) as store:
        reports = Runner(config, api, store).run_once()
        assert len(reports) == 1
        assert reports[0]["decision"] == "BID"
        assert reports[0]["submission_status"] == "dry_run"
        assert "Shorts" in reports[0]["proposal"]
        assert reports[0]["country"]["code"] == "US"
        assert store.attempt_status("production", 77, 101) is None
        assert store.connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 1
    assert not api.payloads
    assert "self" not in api.calls
    assert not any(isinstance(call, tuple) and call[0] == "existing_bid" for call in api.calls)
    assert json.loads(open(config.report_path).read())[0]["id"] == 101
    assert os.stat(config.report_path).st_mode & 0o777 == 0o600
    assert os.stat(config.database_path).st_mode & 0o777 == 0o600


def test_live_bid_once_across_polls_and_restarts(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    with Store(config.database_path) as store:
        runner = Runner(config, api, store)
        assert runner.run_once()[0]["submission_status"] == "submitted"
        assert runner.run_once()[0]["submission_status"] == "local_duplicate:submitted"
    with Store(config.database_path) as store:
        assert (
            Runner(config, api, store)
            .run_once()[0]["submission_status"]
            .startswith("local_duplicate")
        )
    assert len(api.payloads) == 1
    payload = api.payloads[0]
    assert payload["project_id"] == 101 and payload["bidder_id"] == 77
    assert payload["amount"] == 100
    assert payload["period"] == config.bid_period_days


def test_dry_run_does_not_prevent_later_enabled_bid(tmp_path):
    config, api = settings(tmp_path), FakeAPI()
    with Store(config.database_path) as store:
        Runner(config, api, store).run_once()
        live = settings(tmp_path, live=True)
        assert Runner(live, api, store).run_once()[0]["submission_status"] == "submitted"
    assert len(api.payloads) == 1


def test_timeout_is_durable_hold_and_never_replayed(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.submit_error = SubmissionUncertain("Unknown POST outcome")
    with Store(config.database_path) as store:
        with pytest.raises(SubmissionUncertain):
            Runner(config, api, store).run_once()
        assert store.attempt_status("production", 77, 101) == "uncertain"
    report = json.loads(open(config.report_path).read())[0]
    assert report["submission_status"] == "uncertain_requires_reconciliation"
    api.submit_error = None
    with Store(config.database_path) as store:
        result = Runner(config, api, store).run_once()
        assert result[0]["submission_status"] == "local_duplicate:uncertain"
    assert len(api.payloads) == 1


def test_api_cancellation_after_reservation_keeps_hold(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.submit_error = OperationCancelled("Stopped before request")
    with Store(config.database_path) as store:
        with pytest.raises(OperationCancelled):
            Runner(config, api, store).run_once()
        assert store.attempt_status("production", 77, 101) == "reserved"
    report = json.loads(open(config.report_path).read())[0]
    assert report["submission_status"] == "stopped_reserved"


def test_explicit_api_rejection_is_not_automatically_replayed(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.submit_error = APIError("Bid rejected")
    with Store(config.database_path) as store:
        with pytest.raises(APIError):
            Runner(config, api, store).run_once()
        assert store.attempt_status("production", 77, 101) == "rejected"
        assert Runner(config, api, store).run_once()[0]["submission_status"] == (
            "local_duplicate:rejected"
        )
    assert len(api.payloads) == 1


def test_remote_bid_blocks_submission(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.remote_bid = True
    with Store(config.database_path) as store:
        assert Runner(config, api, store).run_once()[0]["submission_status"] == "remote_duplicate"
        assert store.attempt_status("production", 77, 101) is None
    assert not api.payloads


def test_uncertain_duplicate_read_fails_closed_before_claim(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.check_error = APIError("Bid list unavailable")
    with Store(config.database_path) as store:
        with pytest.raises(APIError):
            Runner(config, api, store).run_once()
        assert store.attempt_status("production", 77, 101) is None
    assert not api.payloads


def test_fresh_project_is_rescored_and_may_become_ineligible(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.fresh_result = collection()
    api.fresh_result["users"]["11"]["status"]["payment_verified"] = False
    with Store(config.database_path) as store:
        report = Runner(config, api, store).run_once()[0]
        assert report["decision"] == "SKIP"
        assert report["submission_status"] == "fresh_project_ineligible"
    assert not api.payloads


def test_malformed_fresh_project_blocks_only_that_submission(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.fresh_result = collection()
    del api.fresh_result["projects"][0]["owner_id"]
    with Store(config.database_path) as store:
        report = Runner(config, api, store).run_once()[0]
        assert report["submission_status"] == "fresh_project_malformed"
    assert not api.payloads


def test_own_project_is_skipped(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    api.get_self_id = lambda: 11
    with Store(config.database_path) as store:
        assert Runner(config, api, store).run_once()[0]["submission_status"] == "own_project"
    assert not api.payloads


def test_daily_cap_counts_all_attempts_and_orders_by_score(tmp_path):
    config, api = settings(tmp_path, live=True, max_bids_per_day=1), FakeAPI()
    api.search_result = collection([project(102), project(101)])
    with Store(config.database_path) as store:
        reports = Runner(config, api, store).run_once()
        assert [r["id"] for r in reports] == [101, 102]
        assert [r["submission_status"] for r in reports] == ["submitted", "daily_limit"]
    assert len(api.payloads) == 1


def test_incomplete_live_configuration_fails_before_api_calls(tmp_path):
    config = settings(tmp_path, live=True, permission_verified=False)
    api = FakeAPI()
    with Store(config.database_path) as store:
        with pytest.raises(ConfigError):
            Runner(config, api, store).run_once()
    assert api.calls == []


def test_atomic_claim_prevents_concurrent_daily_limit_overrun(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    with Store(path):
        pass
    barrier = Barrier(4)

    def claim(index):
        with Store(path) as store:
            barrier.wait(timeout=10)
            return store.claim("production", 77, index, 1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))
    assert results.count("reserved") == 1
    assert results.count("daily_limit") == 3


def test_atomic_claim_prevents_same_project_concurrent_submission(tmp_path):
    path = tmp_path / "same-project.sqlite3"
    with Store(path):
        pass
    barrier = Barrier(4)

    def claim(_):
        with Store(path) as store:
            barrier.wait(timeout=10)
            return store.claim("production", 77, 101, 10)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))
    assert results.count("reserved") == 1
    assert results.count("duplicate") == 3


def test_claim_is_per_account_environment_and_retained_after_crash(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    now = 1_800_000_000
    with Store(path) as store:
        assert store.claim("production", 77, 101, 1, now=now) == "reserved"
        assert store.claim("production", 77, 102, 1, now=now) == "daily_limit"
        assert store.claim("sandbox", 77, 101, 1, now=now) == "reserved"
        assert store.claim("production", 88, 101, 1, now=now) == "reserved"
    with Store(path) as store:
        assert store.claim("production", 77, 101, 1, now=now + 86400) == "duplicate"
        assert store.claim("production", 77, 102, 1, now=now + 86400) == "reserved"


def test_stop_during_search_prevents_next_request_and_submission(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    stopped = Event()
    search = api.search_projects

    def stop_after_search(*args, **kwargs):
        result = search(*args, **kwargs)
        stopped.set()
        return result

    api.search_projects = stop_after_search
    with Store(config.database_path) as store:
        reports = Runner(config, api, store, should_stop=stopped.is_set).run_once()
    assert reports == []
    assert sum(isinstance(call, tuple) and call[0] == "search" for call in api.calls) == 1
    assert "self" not in api.calls
    assert not api.payloads


def test_stop_during_identity_request_prevents_project_fetch(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    stopped = Event()

    def stop_after_identity():
        stopped.set()
        return 77

    api.get_self_id = stop_after_identity
    with Store(config.database_path) as store:
        reports = Runner(config, api, store, should_stop=stopped.is_set).run_once()
        assert store.attempt_status("production", 77, 101) is None
    assert reports[0]["submission_status"] == "stopped"
    assert not any(isinstance(call, tuple) and call[0] == "fresh" for call in api.calls)
    assert not api.payloads


def test_stop_during_duplicate_check_prevents_claim_and_submission(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    stopped = Event()

    def stop_after_check(*_):
        stopped.set()
        return False

    api.has_existing_bid = stop_after_check
    with Store(config.database_path) as store:
        reports = Runner(config, api, store, should_stop=stopped.is_set).run_once()
        assert store.attempt_status("production", 77, 101) is None
    assert reports[0]["submission_status"] == "stopped"
    assert not api.payloads


def test_stop_after_reservation_keeps_durable_intent_and_never_posts(tmp_path):
    config, api = settings(tmp_path, live=True), FakeAPI()
    stopped = Event()

    class StopAfterClaim(Store):
        def claim(self, *args, **kwargs):
            result = super().claim(*args, **kwargs)
            if result == "reserved":
                stopped.set()
            return result

    with StopAfterClaim(config.database_path) as store:
        reports = Runner(config, api, store, should_stop=stopped.is_set).run_once()
        assert store.attempt_status("production", 77, 101) == "reserved"
    assert reports[0]["submission_status"] == "stopped_reserved"
    assert json.loads(open(config.report_path).read())[0]["submission_status"] == "stopped_reserved"
    assert not api.payloads
    with Store(config.database_path) as store:
        # Even after restarting without a stop signal, the durable hold blocks replay.
        assert Runner(config, api, store).run_once()[0]["submission_status"] == (
            "local_duplicate:reserved"
        )
    assert not api.payloads
