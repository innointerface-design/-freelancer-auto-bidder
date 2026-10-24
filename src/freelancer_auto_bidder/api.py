"""Official REST API transport. GET retries are bounded; bid POSTs are never retried."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class APIError(RuntimeError):
    """Sanitized API error; never contains response bodies or credentials."""


class AuthenticationError(APIError):
    pass


class PermissionDenied(APIError):
    pass


class RequestBudgetExceeded(APIError):
    pass


class RateLimited(APIError):
    def __init__(self, retry_after: float = 300):
        self.retry_after = retry_after
        super().__init__(f"API rate limit reached; pause for at least {retry_after:.0f} seconds")


class SubmissionUncertain(APIError):
    """A POST may have reached the server. Reconcile manually; never automatically replay."""


class OperationCancelled(APIError):
    """Stopped before initiating the next HTTP request."""


DETAIL_FLAGS = {
    "full_description": "true",
    "job_details": "true",
    "user_details": "true",
    "user_country_details": "true",
    "user_location_details": "true",
    "user_status": "true",
    "user_employer_reputation": "true",
}


def _positive_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise APIError("API response did not contain a valid identifier")
    return value


def _retry_after(value: str | None) -> float:
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return max(1.0, seconds)
        except ValueError:
            try:
                moment = parsedate_to_datetime(value)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return max(1.0, (moment - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return 300.0


class FreelancerAPI:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        should_stop: Callable[[], bool] = lambda: False,
    ):
        settings.validate()
        self.settings = settings
        self._client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._should_stop = should_stop
        self._last_request: float | None = None
        self._requests = 0
        self._cooldown_until = 0.0

    def close(self) -> None:
        self._client.close()

    def begin_cycle(self) -> None:
        self._requests = 0

    def _pace(self) -> None:
        now = self._monotonic()
        if self._cooldown_until > now:
            raise RateLimited(self._cooldown_until - now)
        if self._requests >= self.settings.max_requests_per_cycle:
            raise RequestBudgetExceeded("Configured API request budget exhausted for this cycle")
        if self._last_request is not None:
            delay = self.settings.min_request_interval_seconds - (now - self._last_request)
            if delay > 0:
                self._sleep(delay)
        self._requests += 1
        self._last_request = self._monotonic()

    def _request(
        self, method: str, resource: str, *, params: dict | None = None, payload: dict | None = None
    ) -> dict:
        is_post = method == "POST"
        if is_post and not self.settings.live_enabled:
            raise PermissionDenied("Bid submission is disabled; dry-run must remain enabled")
        tries = 1 if is_post else self.settings.max_get_retries + 1
        for attempt in range(tries):
            if self._should_stop():
                raise OperationCancelled("API operation stopped before request")
            self._pace()
            # A signal can arrive during pacing, after the runner's last check.
            if self._should_stop():
                raise OperationCancelled("API operation stopped before request")
            try:
                response = self._client.request(
                    method,
                    self.settings.api_base + resource,
                    params=params,
                    json=payload,
                    headers={
                        "Freelancer-OAuth-V1": self.settings.token,
                        "Accept": "application/json",
                    },
                    follow_redirects=False,
                    timeout=self.settings.request_timeout_seconds,
                )
            except httpx.TransportError:
                if is_post:
                    raise SubmissionUncertain(
                        "Bid transport failed; remote outcome is unknown"
                    ) from None
                if attempt + 1 < tries:
                    log.warning(
                        "Transient API read failure; retry %d of %d", attempt + 1, tries - 1
                    )
                    self._sleep(2**attempt)
                    continue
                raise APIError("API transport failed after bounded read retries") from None
            status = response.status_code
            if status == 401:
                raise AuthenticationError("OAuth token rejected; stop and check authentication")
            if status == 403:
                raise PermissionDenied("API access denied; verify application/account permissions")
            if status == 429:
                delay = _retry_after(response.headers.get("Retry-After"))
                self._cooldown_until = self._monotonic() + delay
                raise RateLimited(delay)
            if status >= 500:
                if is_post:
                    raise SubmissionUncertain("Bid API server error; remote outcome is unknown")
                if attempt + 1 < tries:
                    log.warning(
                        "API read returned HTTP %d; retry %d of %d", status, attempt + 1, tries - 1
                    )
                    self._sleep(2**attempt)
                    continue
            if not 200 <= status < 300:
                if is_post and 300 <= status < 400:
                    raise SubmissionUncertain("Unexpected bid redirect; remote outcome is unknown")
                raise APIError(f"API request rejected with HTTP {status}")
            try:
                body = response.json()
                if not isinstance(body, dict) or body.get("status") != "success":
                    raise ValueError
                result = body["result"]
                if not isinstance(result, dict):
                    raise ValueError
                return result
            except (ValueError, KeyError):
                if is_post:
                    raise SubmissionUncertain(
                        "Unrecognized bid response; remote outcome is unknown"
                    )
                raise APIError("Unrecognized API response envelope") from None
        raise APIError("API read failed")

    @staticmethod
    def _projects(result: dict) -> dict:
        if not isinstance(result.get("projects"), list) or not isinstance(
            result.get("users", {}), dict
        ):
            raise APIError("Unrecognized project search response")
        return result

    def search_projects(self, query: str, limit: int, offset: int, from_time: float) -> dict:
        return self._projects(
            self._request(
                "GET",
                "projects/0.1/projects/active/",
                params={
                    **DETAIL_FLAGS,
                    "query": query,
                    "limit": limit,
                    "offset": offset,
                    "from_time": int(from_time),
                    "sort_field": "time_submitted",
                    "reverse_sort": "true",
                    "project_types[]": "fixed",
                },
            )
        )

    def fetch_project(self, project_id: int) -> dict:
        # Listing projects by ID returns the same joined projects/users shape as search.
        return self._projects(
            self._request(
                "GET",
                "projects/0.1/projects/",
                params={
                    **DETAIL_FLAGS,
                    "projects[]": _positive_id(project_id),
                    "limit": 1,
                },
            )
        )

    def get_self_id(self) -> int:
        result = self._request("GET", "users/0.1/self/")
        return _positive_id(result.get("id"))

    def has_existing_bid(self, project_id: int, bidder_id: int) -> bool:
        # Query only this account. A full page is conservatively incomplete.
        result = self._request(
            "GET",
            f"projects/0.1/projects/{_positive_id(project_id)}/bids/",
            params={"bidders[]": _positive_id(bidder_id), "limit": 100, "offset": 0},
        )
        bids = result.get("bids")
        if not isinstance(bids, list):
            raise APIError("Cannot verify existing bids: missing bids list")
        for bid in bids:
            if not isinstance(bid, dict) or "bidder_id" not in bid or "project_id" not in bid:
                raise APIError("Cannot verify existing bids: incomplete bid data")
            bid_owner = _positive_id(bid["bidder_id"])
            bid_project = _positive_id(bid["project_id"])
            if bid_owner == bidder_id and bid_project == project_id:
                return True
        total = result.get("total_count")
        if total is not None and (type(total) is not int or total > len(bids) or total < 0):
            raise APIError("Cannot verify existing bids: incomplete total count")
        if len(bids) >= 100:
            raise APIError("Cannot verify existing bids: incomplete pagination")
        return False

    def submit_bid(self, payload: dict) -> int:
        if not self.settings.live_enabled:
            raise PermissionDenied("Automatic submission is disabled")
        required = {
            "project_id",
            "bidder_id",
            "description",
            "amount",
            "period",
            "milestone_percentage",
        }
        if set(payload) != required:
            raise APIError("Invalid bid payload fields")
        _positive_id(payload["project_id"])
        _positive_id(payload["bidder_id"])
        amount = Decimal(str(payload["amount"]))
        if not amount.is_finite() or amount <= 0:
            raise APIError("Invalid bid amount")
        if (
            type(payload["period"]) is not int
            or not 1 <= payload["period"] <= 365
            or type(payload["milestone_percentage"]) is not int
            or not 1 <= payload["milestone_percentage"] <= 100
            or not isinstance(payload["description"], str)
            or not payload["description"].strip()
        ):
            raise APIError("Invalid bid period, milestone, or proposal")
        wire_payload = {**payload, "amount": float(amount)}
        result = self._request("POST", "projects/0.1/bids/", payload=wire_payload)
        try:
            return _positive_id(result.get("id"))
        except APIError:
            raise SubmissionUncertain(
                "Bid response lacks a valid ID; remote outcome is unknown"
            ) from None
