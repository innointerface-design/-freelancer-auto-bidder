"""Dashboard policy and adapters around the unchanged Freelancer bidding engine."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..api import (
    APIError,
    AuthenticationError,
    FreelancerAPI,
    OperationCancelled,
    PermissionDenied,
    RateLimited,
    SubmissionUncertain,
)
from ..config import BudgetRule, ConfigError, Settings
from ..models import Project, parse_project
from ..runner import Runner
from ..scoring import evaluate
from ..store import Store
from .ai import AIError, ProposalGenerator
from .demo import DemoAPI
from .scheduler import schedule_allows


class WebServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def _merge(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _number(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise WebServiceError("Budget amounts must be finite numbers")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise WebServiceError("Budget amounts must be finite numbers") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise WebServiceError("Budget amounts must be positive finite numbers")
    return number


def _snapshot(project: Project) -> dict:
    value = asdict(project)
    for field in ("budget_min", "budget_max"):
        value[field] = str(value[field]) if value[field] is not None else None
    value["skills"] = list(value["skills"])
    return value


def _restore(value: dict) -> Project:
    value = dict(value)
    for field in ("budget_min", "budget_max"):
        value[field] = Decimal(value[field]) if value[field] is not None else None
    value["skills"] = tuple(value["skills"])
    return Project(**value)


class _CapturingAPI:
    """Retain full project facts without altering the engine's transport or runner."""

    def __init__(self, api: Any):
        self.api = api
        self.projects: dict[int, Project] = {}

    def __getattr__(self, name: str):
        return getattr(self.api, name)

    def _capture(self, result: dict) -> dict:
        if isinstance(result, dict):
            users = result.get("users", {})
            for raw in result.get("projects", []):
                try:
                    project = parse_project(raw, users)
                    self.projects[project.id] = project
                except (ValueError, TypeError, KeyError):
                    continue
        return result

    def search_projects(self, *args, **kwargs) -> dict:
        return self._capture(self.api.search_projects(*args, **kwargs))

    def fetch_project(self, *args, **kwargs) -> dict:
        return self._capture(self.api.fetch_project(*args, **kwargs))


class WebService:
    def __init__(self, websettings, repository, *, api_factory=FreelancerAPI):
        self.settings = websettings
        self.repository = repository
        self.api_factory = api_factory
        self._cycle_lock = threading.Lock()
        self._shutdown = threading.Event()
        # Store owns schema migration; no engine tables are replaced or cleared.
        with Store(self.settings.engine.database_path):
            pass

    def stop(self) -> None:
        self._shutdown.set()

    def _defaults(self) -> dict:
        engine = self.settings.engine
        return {
            "pricing": {
                "max_bids_per_day": engine.max_bids_per_day,
                "bid_period_days": engine.bid_period_days,
                "score_threshold": engine.score_threshold,
                "budgets": {
                    code: {
                        field: str(getattr(rule, field))
                        for field in ("min_budget", "bid_amount", "max_bid")
                    }
                    for code, rule in engine.budgets.items()
                },
            },
            "monitor": {
                "search_queries": list(engine.search_queries),
                "schedule": {
                    "enabled": False,
                    "timezone": "Asia/Karachi",
                    "days": list(range(7)),
                    "start": "09:00",
                    "end": "18:00",
                },
            },
            "ai": {"instructions": "", "tone": "Professional", "model": self.settings.openai_model},
        }

    def _config(self) -> dict:
        return self.repository.get_settings(self._defaults())

    def get_settings(self) -> dict:
        config = self._config()
        config["ai"]["configured"] = bool(self.settings.openai_key) and not self.settings.demo_mode
        config["connection"] = {
            "freelancer_configured": bool(self.settings.engine.token),
            "environment": self.settings.engine.environment,
            "live_allowed": self.settings.engine.live_enabled and not self.settings.demo_mode,
            "demo_mode": self.settings.demo_mode,
        }
        return config

    @staticmethod
    def _validate_patch(patch: dict) -> None:
        allowed = {
            "pricing": {"max_bids_per_day", "bid_period_days", "score_threshold", "budgets"},
            "monitor": {"search_queries", "schedule"},
            "ai": {"instructions", "tone", "model"},
        }
        if not isinstance(patch, dict) or not patch or set(patch) - set(allowed):
            raise WebServiceError("Only pricing, monitor and AI settings may be edited")
        for section, values in patch.items():
            if not isinstance(values, dict) or set(values) - allowed[section]:
                raise WebServiceError("Unknown or read-only setting")
        schedule = patch.get("monitor", {}).get("schedule")
        if "schedule" in patch.get("monitor", {}) and (
            not isinstance(schedule, dict)
            or set(schedule) - {"enabled", "timezone", "days", "start", "end"}
        ):
            raise WebServiceError("Unknown schedule setting")
        budgets = patch.get("pricing", {}).get("budgets")
        if budgets is not None:
            if not isinstance(budgets, dict) or not budgets:
                raise WebServiceError("At least one currency budget is required")
            for code, rule in budgets.items():
                if (
                    not isinstance(code, str)
                    or not re.fullmatch(r"[A-Z]{3}", code)
                    or not isinstance(rule, dict)
                    or set(rule) - {"min_budget", "bid_amount", "max_bid"}
                ):
                    raise WebServiceError("Invalid currency budget setting")

    def _engine(self, config: dict) -> Settings:
        pricing = config["pricing"]
        try:
            budgets = {
                code: BudgetRule(
                    **{key: _number(value, positive=True) for key, value in rule.items()}
                )
                for code, rule in pricing["budgets"].items()
            }
            engine = replace(
                self.settings.engine,
                budgets=budgets,
                max_bids_per_day=pricing["max_bids_per_day"],
                bid_period_days=pricing["bid_period_days"],
                score_threshold=pricing["score_threshold"],
                search_queries=tuple(config["monitor"]["search_queries"]),
            )
            engine.validate(require_token=False)
        except (ConfigError, TypeError, KeyError, AttributeError):
            raise WebServiceError("Invalid bid pricing or search configuration") from None
        return engine

    def _validate_config(self, config: dict) -> None:
        queries = config["monitor"]["search_queries"]
        if (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 20
            or any(not isinstance(q, str) or not 1 <= len(q.strip()) <= 200 for q in queries)
        ):
            raise WebServiceError("Use 1 to 20 search queries, each at most 200 characters")
        schedule = config["monitor"]["schedule"]
        if type(schedule["enabled"]) is not bool:
            raise WebServiceError("Schedule enabled must be a boolean")
        try:
            if not isinstance(schedule["timezone"], str) or len(schedule["timezone"]) > 80:
                raise ValueError
            ZoneInfo(schedule["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            raise WebServiceError("Choose a valid IANA schedule timezone") from None
        days = schedule["days"]
        if (
            not isinstance(days, list)
            or any(type(day) is not int or not 0 <= day <= 6 for day in days)
            or len(days) != len(set(days))
        ):
            raise WebServiceError("Schedule days must be unique weekdays from 0 to 6")
        for field in ("start", "end"):
            if not isinstance(schedule[field], str) or not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", schedule[field]
            ):
                raise WebServiceError("Schedule times must use HH:MM")
        ai = config["ai"]
        if not isinstance(ai["instructions"], str) or len(ai["instructions"]) > 4000:
            raise WebServiceError("AI instructions must be at most 4000 characters")
        if not isinstance(ai["tone"], str) or not 1 <= len(ai["tone"].strip()) <= 80:
            raise WebServiceError("AI tone must contain 1 to 80 characters")
        if not isinstance(ai["model"], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", ai["model"]
        ):
            raise WebServiceError("Invalid AI model identifier")
        self._engine(config)

    def update_settings(self, patch: dict) -> dict:
        self._validate_patch(patch)
        if not self._cycle_lock.acquire(blocking=False):
            raise WebServiceError("Wait for the active search before changing settings", 409)
        try:
            config = _merge(self._config(), patch)
            self._validate_config(config)
            # Normalize nominal money without ever converting between currencies.
            for rule in config["pricing"]["budgets"].values():
                for key in rule:
                    rule[key] = str(_number(rule[key], positive=True))
            self.repository.save_settings(config)
            self.repository.event("Dashboard settings updated")
            return self.get_settings()
        finally:
            self._cycle_lock.release()

    def controls(self, patch: dict) -> dict:
        if (
            not isinstance(patch, dict)
            or not patch
            or set(patch) - {"monitor_enabled", "auto_bidder_enabled"}
            or any(type(value) is not bool for value in patch.values())
        ):
            raise WebServiceError("Controls must contain explicit boolean ON/OFF values")
        values = self.repository.set_controls(**patch)
        self.repository.set_runtime(
            next_run_at=None
            if not values["monitor_enabled"]
            else time.time() + self.settings.engine.poll_interval_seconds
        )
        self.repository.event(
            (
                "Automatic bidder enabled"
                if self.settings.engine.live_enabled and not self.settings.demo_mode
                else "Automatic proposals enabled in dry-run"
            )
            if values["auto_bidder_enabled"]
            else ("Job monitoring enabled" if values["monitor_enabled"] else "Monitoring paused")
        )
        return self.dashboard()

    def _reports(self) -> list[dict]:
        with Store(self.settings.engine.database_path) as store:
            rows = store.connection.execute(
                "SELECT report_json, updated_at FROM assessments WHERE environment=?",
                (self.settings.engine.environment,),
            ).fetchall()
        reports = []
        for row in rows:
            value = json.loads(row["report_json"])
            value.setdefault("updated_at", row["updated_at"])
            value.setdefault("description", "")
            reports.append(value)
        return reports

    def _public(self, report: dict) -> dict:
        value = {key: item for key, item in report.items() if not key.startswith("_")}
        value["draft"] = self.repository.get_draft(report["id"], self.settings.engine.environment)
        return value

    def jobs(self, filters: dict | None = None) -> dict:
        filters = filters or {}
        query = str(filters.get("query") or "").strip().casefold()
        skill = str(filters.get("skill") or "").strip().casefold().replace("_", " ")
        countries = {
            code.strip().upper()
            for code in str(filters.get("countries") or "").split(",")
            if code.strip()
        }
        if any(not re.fullmatch(r"[A-Z]{2}|UNKNOWN", code) for code in countries):
            raise WebServiceError("Country filters must use two-letter country codes")
        currency = str(filters.get("currency") or "").strip().upper()
        if currency and not re.fullmatch(r"[A-Z]{3}", currency):
            raise WebServiceError("Choose a three-letter project currency")
        amounts = {}
        for key in ("min_budget", "max_budget"):
            value = filters.get(key)
            amounts[key] = _number(value) if value is not None and value != "" else None
        lower, upper = amounts["min_budget"], amounts["max_budget"]
        if (lower is not None or upper is not None) and not currency:
            raise WebServiceError("Choose a currency before filtering nominal budget amounts")
        if lower is not None and upper is not None and lower > upper:
            raise WebServiceError("Minimum budget cannot exceed maximum budget")
        decision = str(filters.get("decision") or "").upper()
        if decision and decision not in {"BID", "SKIP"}:
            raise WebServiceError("Recommendation must be BID or SKIP")
        sort = filters.get("sort", "score")
        if sort not in {"score", "newest"}:
            raise WebServiceError("Sort must be score or newest")
        try:
            limit, offset = int(filters.get("limit", 20)), int(filters.get("offset", 0))
            if not 1 <= limit <= 100 or offset < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise WebServiceError("Invalid result page") from None
        reports = []
        for report in self._reports():
            text = f"{report['title']} {report.get('description', '')}".casefold()
            skills = (
                " ".join(report.get("required_skills", []) + report.get("matched_services", []))
                .casefold()
                .replace("_", " ")
            )
            country = report.get("country", {}).get("code") or "UNKNOWN"
            budget = report.get("budget", {})
            if (query and query not in text) or (skill and skill not in skills):
                continue
            if countries and country not in countries:
                continue
            if currency and budget.get("currency") != currency:
                continue
            if decision and report.get("decision") != decision:
                continue
            try:
                if lower is not None and (
                    budget.get("maximum") is None or Decimal(budget["maximum"]) < lower
                ):
                    continue
                if upper is not None and (
                    budget.get("minimum") is None or Decimal(budget["minimum"]) > upper
                ):
                    continue
            except InvalidOperation:
                continue
            reports.append(report)
        reports.sort(
            key=(lambda row: (-row.get("score", 0), -row.get("updated_at", 0)))
            if sort == "score"
            else lambda row: -row.get("updated_at", 0)
        )
        return {
            "items": [self._public(row) for row in reports[offset : offset + limit]],
            "total": len(reports),
        }

    def history(self, limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise WebServiceError("History limit must be between 1 and 500")
        reports = {row["id"]: row for row in self._reports()}
        with Store(self.settings.engine.database_path) as store:
            rows = store.connection.execute(
                "SELECT project_id, status, bid_id, created_at, environment FROM attempts "
                "WHERE environment=? ORDER BY created_at DESC LIMIT ?",
                (self.settings.engine.environment, limit),
            ).fetchall()
            total = store.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE environment=?",
                (self.settings.engine.environment,),
            ).fetchone()[0]
        return {
            "items": [
                {
                    **dict(row),
                    "title": reports.get(row["project_id"], {}).get(
                        "title", f"Project {row['project_id']}"
                    ),
                }
                for row in rows
            ],
            "total": total,
        }

    def dashboard(self) -> dict:
        config = self._config()
        controls = self.repository.controls()
        runtime = self.repository.runtime()
        reports = self._reports()
        today = datetime.now(timezone.utc).date().isoformat()
        with Store(self.settings.engine.database_path) as store:
            submitted_total = store.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE environment=? AND status='submitted'",
                (self.settings.engine.environment,),
            ).fetchone()[0]
            submitted_today = store.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE environment=? AND status='submitted' "
                "AND utc_day=?",
                (self.settings.engine.environment, today),
            ).fetchone()[0]
            attempted_today = store.connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE environment=? AND utc_day=?",
                (self.settings.engine.environment, today),
            ).fetchone()[0]
        in_schedule = schedule_allows(config["monitor"]["schedule"])
        live = (
            self.settings.engine.live_enabled
            and controls["auto_bidder_enabled"]
            and controls["monitor_enabled"]
            and in_schedule
            and not self.settings.demo_mode
        )
        countries = Counter(row.get("country", {}).get("code") or "UNKNOWN" for row in reports)
        return {
            "mode": "demo" if self.settings.demo_mode else ("live" if live else "dry_run"),
            "environment": self.settings.engine.environment,
            "configured": bool(self.settings.engine.token) or self.settings.demo_mode,
            **controls,
            "live_allowed": self.settings.engine.live_enabled and not self.settings.demo_mode,
            "schedule_active": in_schedule,
            "next_run_at": runtime["next_run_at"],
            "last_run_at": runtime["last_run_at"],
            "last_error": runtime["last_error"],
            "timezone": config["monitor"]["schedule"]["timezone"],
            "stats": {
                "jobs_total": len(reports),
                "recommended": sum(row["decision"] == "BID" for row in reports),
                "submitted_today": submitted_today,
                "attempted_today": attempted_today,
                "submitted_total": submitted_total,
                "daily_limit": config["pricing"]["max_bids_per_day"],
                "drafts": self.repository.draft_count(self.settings.engine.environment),
            },
            "activity": self.repository.events(),
            "chart": self.repository.chart(),
            "country_breakdown": [
                {"country": code, "count": count} for code, count in countries.most_common()
            ],
        }

    def _enrich(self, capture: _CapturingAPI, store: Store) -> None:
        rows = store.connection.execute(
            "SELECT report_json FROM assessments WHERE environment=?",
            (self.settings.engine.environment,),
        ).fetchall()
        for row in rows:
            report = json.loads(row["report_json"])
            project = capture.projects.get(report["id"])
            if project is not None:
                report.update(
                    description=project.description,
                    updated_at=time.time(),
                    _project=_snapshot(project),
                )
                store.save_report(self.settings.engine.environment, report)

    def _run(self, query: str = "", *, scheduled: bool = False) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            raise WebServiceError("A search is already running; try again shortly", 409)
        api = None
        try:
            if self._shutdown.is_set():
                raise WebServiceError("The application is shutting down", 503)
            runtime = self.repository.runtime()
            if runtime["blocked_until"] > time.time():
                raise WebServiceError(
                    "Freelancer API cooldown is active; wait before searching", 429
                )
            if not scheduled and not self.settings.demo_mode:
                previous = runtime.get("last_manual_search_at", 0)
                if previous and time.time() - previous < 5:
                    raise WebServiceError("Wait five seconds between manual API searches", 429)
            if not self.settings.demo_mode and not self.settings.engine.token:
                raise WebServiceError(
                    "Configure FREELANCER_OAUTH_TOKEN before live job search", 503
                )
            config = self._config()
            engine = self._engine(config)
            controls = self.repository.controls()
            live = (
                scheduled
                and engine.live_enabled
                and controls["auto_bidder_enabled"]
                and schedule_allows(config["monitor"]["schedule"])
                and not self.settings.demo_mode
            )
            engine = replace(
                engine,
                dry_run=not live,
                enable_auto_bidding=engine.enable_auto_bidding if live else False,
            )
            if query:
                engine = replace(engine, search_queries=(query,))
            if self.settings.demo_mode:
                engine = replace(
                    engine, token="offline-demo-only", dry_run=True, enable_auto_bidding=False
                )
            if not scheduled and not self.settings.demo_mode:
                self.repository.set_runtime(last_manual_search_at=time.time())

            def cancelled() -> bool:
                if self._shutdown.is_set():
                    return True
                current = self.repository.controls()
                if scheduled and not current["monitor_enabled"]:
                    return True
                return bool(
                    live
                    and (
                        not current["auto_bidder_enabled"]
                        or not schedule_allows(self._config()["monitor"]["schedule"])
                    )
                )

            factory = DemoAPI if self.settings.demo_mode else self.api_factory
            api = factory(engine, should_stop=cancelled)
            capture = _CapturingAPI(api)
            with Store(engine.database_path) as store:
                try:
                    reports = Runner(engine, capture, store, should_stop=cancelled).run_once()
                finally:
                    self._enrich(capture, store)
            submitted = sum(row["submission_status"] == "submitted" for row in reports)
            recommended = sum(row["decision"] == "BID" for row in reports)
            self.repository.record_run(len(reports), recommended, submitted)
            self.repository.set_runtime(last_run_at=time.time(), last_error=None)
            self.repository.event(
                f"{'Demo' if self.settings.demo_mode else 'Job'} search assessed {len(reports)} "
                f"projects; {recommended} recommended; {submitted} submitted"
            )
            return self.jobs({"limit": 100})
        except RateLimited as exc:
            self.repository.set_runtime(
                blocked_until=time.time() + max(1, exc.retry_after),
                last_error="Freelancer API rate limit reached",
            )
            self.repository.event(
                "Freelancer API rate limit reached; monitoring will wait", "warning"
            )
            raise WebServiceError(
                "Freelancer API rate limit reached; wait before searching", 429
            ) from None
        except (AuthenticationError, PermissionDenied):
            self.repository.set_controls(monitor_enabled=False, auto_bidder_enabled=False)
            self.repository.set_runtime(last_error="Freelancer account/API permission check failed")
            self.repository.event("Monitoring stopped: check Freelancer API permissions", "error")
            raise WebServiceError("Freelancer account/API permission check failed", 502) from None
        except SubmissionUncertain:
            self.repository.set_controls(monitor_enabled=False, auto_bidder_enabled=False)
            self.repository.set_runtime(
                last_error="Bid outcome uncertain; inspect the durable ledger"
            )
            self.repository.event("Bid outcome uncertain; monitoring stopped for review", "error")
            raise WebServiceError(
                "Bid outcome uncertain; inspect Freelancer and the bid ledger", 502
            ) from None
        except OperationCancelled:
            self.repository.event("Search stopped before the next API request")
            return self.jobs({"limit": 100})
        except APIError:
            self.repository.set_runtime(last_error="Freelancer API search failed")
            self.repository.event("Freelancer API search failed; review connection status", "error")
            raise WebServiceError("Freelancer API search failed; try again later", 502) from None
        finally:
            if api is not None:
                api.close()
            self._cycle_lock.release()

    def search(self, query: str = "") -> dict:
        if not isinstance(query, str) or len(query.strip()) > 200:
            raise WebServiceError("Search query must be at most 200 characters")
        # Interactive searches cannot submit bids, even when environment gates are open.
        return self._run(query.strip(), scheduled=False)

    def monitor_tick(self) -> None:
        if self._shutdown.is_set() or not self.repository.controls()["monitor_enabled"]:
            return
        now = time.time()
        runtime = self.repository.runtime()
        if runtime["blocked_until"] > now:
            self.repository.set_runtime(next_run_at=runtime["blocked_until"])
            return
        previous = runtime["last_run_at"]
        if previous and now - previous < self.settings.engine.poll_interval_seconds:
            self.repository.set_runtime(
                next_run_at=previous + self.settings.engine.poll_interval_seconds
            )
            return
        try:
            self._run(scheduled=True)
        except WebServiceError as exc:
            if exc.status_code not in {409, 429, 502}:
                self.repository.set_runtime(last_error=str(exc))
                self.repository.event(str(exc), "warning")
        finally:
            if self.repository.controls()["monitor_enabled"]:
                self.repository.set_runtime(
                    next_run_at=max(
                        time.time() + self.settings.engine.poll_interval_seconds,
                        self.repository.runtime()["blocked_until"],
                    )
                )
            else:
                self.repository.set_runtime(next_run_at=None)

    def _project(self, project_id: int) -> Project:
        if type(project_id) is not int or project_id <= 0:
            raise WebServiceError("Invalid project identifier")
        report = next((row for row in self._reports() if row["id"] == project_id), None)
        if report is None:
            raise WebServiceError("Project not found", 404)
        if not isinstance(report.get("_project"), dict):
            raise WebServiceError("Run a fresh dashboard search to load full project details", 409)
        try:
            return _restore(report["_project"])
        except (TypeError, ValueError, KeyError, InvalidOperation):
            raise WebServiceError(
                "Project details are unavailable; run a fresh search", 409
            ) from None

    def generate_proposal(self, project_id: int, instructions: str = "") -> dict:
        project = self._project(project_id)
        config = self._config()
        if not isinstance(instructions, str) or len(instructions) > 4000:
            raise WebServiceError("AI instructions must be at most 4000 characters")
        combined = "\n".join(
            value for value in (config["ai"]["instructions"], instructions) if value
        )
        if len(combined) > 4000:
            raise WebServiceError("Combined AI instructions must be at most 4000 characters")
        generator = ProposalGenerator("" if self.settings.demo_mode else self.settings.openai_key)
        try:
            result = generator.generate(
                project,
                evaluate(project, self._engine(config)),
                instructions=combined,
                tone=config["ai"]["tone"],
                model=config["ai"]["model"],
            )
        except AIError as exc:
            raise WebServiceError(str(exc), 503) from None
        finally:
            generator.close()
        self.repository.save_draft(
            project_id, self.settings.engine.environment, result["text"], result["provider"]
        )
        self.repository.event(f"Proposal draft generated for project {project_id}")
        return result

    def save_proposal(self, project_id: int, text: str) -> dict:
        self._project(project_id)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            raise WebServiceError("Proposal must contain 1 to 4000 characters")
        draft = self.repository.save_draft(
            project_id, self.settings.engine.environment, text.strip(), "edited"
        )
        self.repository.event(f"Proposal draft saved for project {project_id}")
        return draft
