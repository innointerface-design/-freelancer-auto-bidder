"""Strict configuration; secrets and live-submission acknowledgements are environment-only."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BudgetRule:
    min_budget: Decimal
    bid_amount: Decimal
    max_bid: Decimal


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)
    environment: str = "production"
    dry_run: bool = True
    enable_auto_bidding: bool = False
    permission_verified: bool = False
    permission_reference: str = ""
    poll_interval_seconds: int = 300
    search_queries: tuple[str, ...] = (
        "video editing",
        "motion graphics",
        "AI video",
        "YouTube Shorts",
        "product video",
    )
    page_size: int = 50
    max_pages_per_query: int = 2
    max_requests_per_cycle: int = 80
    min_request_interval_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    max_get_retries: int = 2
    max_project_age_hours: int = 24
    score_threshold: int = 70
    preferred_countries: tuple[str, ...] = ("US", "GB", "CA", "AU", "AE")
    require_known_country: bool = True
    require_verified_payment: bool = True
    min_client_rating: float = 3.5
    bid_period_days: int = 5
    milestone_percentage: int = 100
    max_bids_per_day: int = 3
    database_path: str = "data/bidder.sqlite3"
    report_path: str = "data/recommendations.json"
    budgets: dict[str, BudgetRule] = field(
        default_factory=lambda: {"USD": BudgetRule(Decimal("50"), Decimal("100"), Decimal("250"))}
    )

    @property
    def live_enabled(self) -> bool:
        return (
            not self.dry_run
            and self.enable_auto_bidding
            and self.permission_verified
            and bool(self.permission_reference.strip())
        )

    @property
    def api_base(self) -> str:
        host = (
            "www.freelancer.com"
            if self.environment == "production"
            else ("www.freelancer-sandbox.com")
        )
        return f"https://{host}/api/"

    def validate(self, *, require_token: bool = True) -> None:
        if not isinstance(self.token, str) or any(c in self.token for c in ("\r", "\n")):
            raise ConfigError("OAuth token must be a single-line string")
        if require_token and not self.token.strip():
            raise ConfigError(
                "Set FREELANCER_OAUTH_TOKEN through your environment or secret manager"
            )
        if self.environment not in {"production", "sandbox"}:
            raise ConfigError("FREELANCER_ENVIRONMENT must be production or sandbox")
        if not self.dry_run and not self.live_enabled:
            raise ConfigError(
                "Live bidding requires ENABLE_AUTO_BIDDING, verified permission, and "
                "a non-empty FREELANCER_PERMISSION_REFERENCE; use dry-run otherwise"
            )
        for name in (
            "dry_run",
            "enable_auto_bidding",
            "permission_verified",
            "require_known_country",
            "require_verified_payment",
        ):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"{name} must be a boolean")
        if not isinstance(self.permission_reference, str):
            raise ConfigError("permission_reference must be a string")
        for name in ("database_path", "report_path"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ConfigError(f"{name} must be a nonempty path string")
        if Path(self.database_path).resolve() == Path(self.report_path).resolve():
            raise ConfigError("database_path and report_path must be different files")
        ranges = {
            "poll_interval_seconds": (300, 86400),
            "page_size": (1, 100),
            "max_pages_per_query": (1, 20),
            "max_requests_per_cycle": (1, 10000),
            "max_get_retries": (0, 5),
            "max_project_age_hours": (1, 720),
            "score_threshold": (1, 100),
            "bid_period_days": (1, 365),
            "milestone_percentage": (1, 100),
            "max_bids_per_day": (1, 100),
        }
        for name, (lower, upper) in ranges.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ConfigError(f"{name} must be an integer from {lower} to {upper}")
        for name, lower, upper in (
            ("min_request_interval_seconds", 0.1, 300),
            ("request_timeout_seconds", 1, 300),
            ("min_client_rating", 0, 5),
        ):
            value = getattr(self, name)
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise ConfigError(f"Invalid {name}")
        if (
            not isinstance(self.search_queries, (tuple, list))
            or not self.search_queries
            or any(not isinstance(q, str) or not q.strip() for q in self.search_queries)
        ):
            raise ConfigError("search_queries must contain nonempty strings")
        if not isinstance(self.preferred_countries, (tuple, list)) or any(
            not isinstance(c, str) or len(c) != 2 or not c.isalpha() or c != c.upper()
            for c in self.preferred_countries
        ):
            raise ConfigError("preferred_countries must contain uppercase two-letter country codes")
        if not isinstance(self.budgets, dict) or not self.budgets:
            raise ConfigError("Configure at least one currency budget rule")
        for code, rule in self.budgets.items():
            if (
                not isinstance(code, str)
                or len(code) != 3
                or not code.isalpha()
                or code != code.upper()
            ):
                raise ConfigError("Budget currency keys must be uppercase ISO currency codes")
            values = (rule.min_budget, rule.bid_amount, rule.max_bid)
            if any(not v.is_finite() or v <= 0 or v.as_tuple().exponent < -2 for v in values):
                raise ConfigError(
                    f"{code} amounts must be positive with at most two decimal places"
                )
            if not rule.min_budget <= rule.bid_amount <= rule.max_bid:
                raise ConfigError(f"{code} requires min_budget <= bid_amount <= max_bid")


def _boolean(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ConfigError(f"{key} must be true or false")
    return value == "true"


def load_settings(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    data = {}
    if path:
        try:
            with open(path, "rb") as stream:
                data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("Cannot read configuration TOML") from exc
    allowed = set(Settings.__dataclass_fields__) - {
        "token",
        "environment",
        "dry_run",
        "enable_auto_bidding",
        "permission_verified",
        "permission_reference",
    }
    if set(data) - allowed:
        raise ConfigError(
            "Unknown or environment-only configuration keys: "
            + ", ".join(sorted(set(data) - allowed))
        )
    for key in ("search_queries", "preferred_countries"):
        if key in data:
            if not isinstance(data[key], list) or any(not isinstance(x, str) for x in data[key]):
                raise ConfigError(f"{key} must be an array of strings")
            data[key] = tuple(data[key])
    if "budgets" in data:
        try:
            if not isinstance(data["budgets"], dict):
                raise TypeError
            data["budgets"] = {
                code: BudgetRule(**{key: Decimal(str(value)) for key, value in row.items()})
                for code, row in data["budgets"].items()
            }
        except (InvalidOperation, TypeError, AttributeError) as exc:
            raise ConfigError("Invalid currency budget configuration") from exc
    result = Settings(
        **data,
        token=env.get("FREELANCER_OAUTH_TOKEN", ""),
        environment=env.get("FREELANCER_ENVIRONMENT", "production"),
        dry_run=_boolean(env, "FREELANCER_DRY_RUN", True),
        enable_auto_bidding=_boolean(env, "FREELANCER_ENABLE_AUTO_BIDDING", False),
        permission_verified=_boolean(env, "FREELANCER_AUTO_BID_PERMISSION_VERIFIED", False),
        permission_reference=env.get("FREELANCER_PERMISSION_REFERENCE", ""),
    )
    result.validate()
    return result
