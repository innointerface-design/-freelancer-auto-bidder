from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from freelancer_auto_bidder.api import (
    APIError,
    AuthenticationError,
    FreelancerAPI,
    OperationCancelled,
    PermissionDenied,
    RateLimited,
    RequestBudgetExceeded,
    SubmissionUncertain,
)
from freelancer_auto_bidder.config import Settings


def make_api(handler, **changes):
    settings = replace(Settings(token="test-only-token"), **changes)
    return FreelancerAPI(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )


def success(result):
    return httpx.Response(200, json={"status": "success", "result": result})


def test_official_search_request_and_owner_flags():
    def handle(request):
        assert request.url.host == "www.freelancer.com"
        assert request.url.path == "/api/projects/0.1/projects/active/"
        assert request.headers["Freelancer-OAuth-V1"] == "test-only-token"
        assert request.url.params["full_description"] == "true"
        assert request.url.params["user_country_details"] == "true"
        assert request.url.params["user_status"] == "true"
        assert request.url.params["user_employer_reputation"] == "true"
        assert request.url.params["from_time"] == "1000"
        assert request.url.params["offset"] == "50"
        assert request.url.params["project_types[]"] == "fixed"
        return success({"projects": [], "users": {}})

    assert make_api(handle).search_projects("video editing", 50, 50, 1000)["projects"] == []


def test_sandbox_and_fetch_by_project_id():
    def handle(request):
        assert request.url.host == "www.freelancer-sandbox.com"
        assert request.url.path == "/api/projects/0.1/projects/"
        assert request.url.params["projects[]"] == "42"
        return success({"projects": [], "users": {}})

    make_api(handle, environment="sandbox").fetch_project(42)


def test_existing_bid_is_account_filtered():
    def handle(request):
        assert request.url.path == "/api/projects/0.1/projects/42/bids/"
        assert request.url.params["bidders[]"] == "7"
        return success({"bids": [{"id": 1, "bidder_id": 7, "project_id": 42}]})

    assert make_api(handle).has_existing_bid(42, 7)


@pytest.mark.parametrize(
    "bids",
    [
        None,
        [{}],
        [{"bidder_id": 8, "project_id": 42}] * 100,
        [{"bidder_id": "7", "project_id": 42}],
    ],
)
def test_incomplete_duplicate_check_fails_closed(bids):
    api = make_api(lambda _: success({"bids": bids}))
    with pytest.raises(APIError):
        api.has_existing_bid(42, 7)


def test_truncated_bid_list_fails_closed():
    api = make_api(lambda _: success({"bids": [], "total_count": 1}))
    with pytest.raises(APIError):
        api.has_existing_bid(42, 7)


@pytest.mark.parametrize("code,error", [(401, AuthenticationError), (403, PermissionDenied)])
def test_auth_errors_stop_without_retry_or_response_leak(code, error):
    calls = []
    api = make_api(lambda r: calls.append(r) or httpx.Response(code, text="test-only-token secret"))
    with pytest.raises(error) as exc:
        api.get_self_id()
    assert "test-only-token" not in str(exc.value)
    assert len(calls) == 1


def test_rate_limit_respects_retry_after_and_blocks_more_requests():
    calls = []
    api = make_api(lambda r: calls.append(r) or httpx.Response(429, headers={"Retry-After": "600"}))
    with pytest.raises(RateLimited) as exc:
        api.get_self_id()
    assert exc.value.retry_after == 600
    api.begin_cycle()
    with pytest.raises(RateLimited):
        api.get_self_id()
    assert len(calls) == 1


def test_get_retries_bounded_and_request_budget_counts_retries():
    calls = []
    api = make_api(lambda r: calls.append(r) or httpx.Response(503), max_requests_per_cycle=2)
    with pytest.raises(RequestBudgetExceeded):
        api.get_self_id()
    assert len(calls) == 2


def test_read_transport_retried_but_no_secret_exception_context():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ConnectError("test-only-token secret", request=request)

    with pytest.raises(APIError) as exc:
        make_api(handle).get_self_id()
    assert "test-only-token" not in str(exc.value)
    assert len(calls) == 3


def test_disabled_submission_makes_no_network_call():
    api = make_api(lambda _: pytest.fail("dry-run performed a network write"))
    with pytest.raises(PermissionDenied):
        api.submit_bid({})


LIVE = dict(
    dry_run=False,
    enable_auto_bidding=True,
    permission_verified=True,
    permission_reference="test-approval",
)
PAYLOAD = {
    "project_id": 42,
    "bidder_id": 7,
    "description": "Personalized proposal",
    "amount": Decimal("100.50"),
    "period": 5,
    "milestone_percentage": 100,
}


@pytest.mark.parametrize("failure", ["timeout", "server", "malformed", "redirect", "missing_id"])
def test_bid_post_never_retried_when_outcome_uncertain(failure):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/api/projects/0.1/bids/"
        if failure == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        if failure == "server":
            return httpx.Response(503)
        if failure == "malformed":
            return httpx.Response(200, text="invalid")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.org"})
        return success({})

    with pytest.raises(SubmissionUncertain):
        make_api(handle, **LIVE).submit_bid(PAYLOAD)
    assert len(calls) == 1


def test_successful_bid_payload():
    import json

    def handle(request):
        payload = json.loads(request.content)
        assert payload == {**PAYLOAD, "amount": 100.5}
        return success({"id": 1234})

    assert make_api(handle, **LIVE).submit_bid(PAYLOAD) == 1234


def test_redirect_never_forwards_credentials():
    calls = []
    api = make_api(
        lambda r: (
            calls.append(r) or httpx.Response(302, headers={"Location": "https://example.org/leak"})
        )
    )
    with pytest.raises(APIError):
        api.get_self_id()
    assert len(calls) == 1


def test_invalid_envelope_fails_closed():
    with pytest.raises(APIError):
        make_api(lambda _: httpx.Response(200, json={"result": {"id": 1}})).get_self_id()


def test_cancellation_during_request_pacing_prevents_bid_post():
    calls = []
    cancelled = [False]

    def pace_sleep(_delay):
        cancelled[0] = True

    api = FreelancerAPI(
        replace(Settings(token="test-only-token"), **LIVE),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or success({"id": 7})
            )
        ),
        sleep=pace_sleep,
        monotonic=lambda: 0.0,
        should_stop=lambda: cancelled[0],
    )
    assert api.get_self_id() == 7
    with pytest.raises(OperationCancelled):
        api.submit_bid(PAYLOAD)
    assert len(calls) == 1 and calls[0].method == "GET"
