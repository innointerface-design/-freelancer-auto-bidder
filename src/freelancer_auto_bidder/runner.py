"""A bounded search/score cycle with dry-run-first submission orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .api import APIError, OperationCancelled, SubmissionUncertain
from .config import Settings
from .models import Project, parse_project
from .proposals import generate_proposal
from .scoring import Assessment, evaluate
from .store import Store, write_report

logger = logging.getLogger(__name__)


def _report(
    project: Project, assessment: Assessment, proposal: str, environment: str
) -> dict[str, Any]:
    host = "www.freelancer.com" if environment == "production" else "www.freelancer-sandbox.com"
    return {
        "id": project.id,
        "url": f"https://{host}/projects/{project.id}",
        "title": project.title,
        "score": assessment.score,
        "decision": assessment.decision,
        "reasons": list(assessment.reasons),
        "matched_services": list(assessment.matched_services),
        "required_skills": list(project.skills),
        "country": {
            "code": project.country_code,
            "name": project.country_name,
            "source": project.country_source,
        },
        "payment_verified": project.payment_verified,
        "client_rating": project.client_rating,
        "client_reviews": project.client_reviews,
        "budget": {
            "currency": project.currency,
            "minimum": str(project.budget_min) if project.budget_min is not None else None,
            "maximum": str(project.budget_max) if project.budget_max is not None else None,
        },
        "bid_amount": str(assessment.bid_amount) if assessment.bid_amount is not None else None,
        "proposal": proposal,
        "submission_status": "not_eligible" if assessment.decision == "SKIP" else "dry_run",
    }


class Runner:
    def __init__(
        self,
        settings: Settings,
        api: Any,
        store: Store,
        *,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.settings = settings
        self.api = api
        self.store = store
        self.should_stop = should_stop or (lambda: False)

    def _stop_requested(
        self,
        report: dict[str, Any] | None = None,
        *,
        reserved: bool = False,
    ) -> bool:
        if not self.should_stop():
            return False
        if report is not None:
            report["submission_status"] = "stopped_reserved" if reserved else "stopped"
        return True

    def run_once(self) -> list[dict[str, Any]]:
        """Run one bounded cycle. Auth/rate/API failures propagate for CLI suspension."""
        self.settings.validate()
        self.api.begin_cycle()
        now = time.time()
        candidates: dict[int, Project] = {}
        reports: list[dict[str, Any]] = []
        try:
            for query in self.settings.search_queries:
                for page in range(self.settings.max_pages_per_query):
                    if self._stop_requested():
                        return reports
                    result = self.api.search_projects(
                        query,
                        limit=self.settings.page_size,
                        offset=page * self.settings.page_size,
                        from_time=int(now - self.settings.max_project_age_hours * 3600),
                    )
                    rows, users = self._rows(result)
                    for raw in rows:
                        try:
                            project = parse_project(raw, users)
                        except (ValueError, TypeError, KeyError):
                            logger.warning("Skipping malformed project record")
                            continue
                        candidates[project.id] = project
                    if len(rows) < self.settings.page_size:
                        break
                    if page + 1 == self.settings.max_pages_per_query:
                        logger.warning(
                            "Search page bound reached; narrow queries or increase limit"
                        )
            if self._stop_requested():
                return reports
            ranked = [
                (project, evaluate(project, self.settings, now=now))
                for project in candidates.values()
            ]
            ranked.sort(key=lambda row: (-row[1].score, row[0].id))
            bidder_id = None
            for project, assessment in ranked:
                if self._stop_requested():
                    return reports
                proposal = (
                    generate_proposal(project, assessment) if assessment.decision == "BID" else ""
                )
                report = _report(project, assessment, proposal, self.settings.environment)
                if self.settings.live_enabled and assessment.decision == "BID":
                    report["submission_status"] = "pending_live_checks"
                reports.append(report)
                self.store.save_report(self.settings.environment, report)
                if self._stop_requested(report):
                    return reports
                if not self.settings.live_enabled or assessment.decision != "BID":
                    continue
                if bidder_id is None:
                    if self._stop_requested(report):
                        return reports
                    bidder_id = self.api.get_self_id()
                    if self._stop_requested(report):
                        return reports
                self._submit(project, bidder_id, report)
                self.store.save_report(self.settings.environment, report)
                if self.should_stop():
                    return reports
            return reports
        finally:
            # A partial report remains reviewable if later calls fail.
            for report in reports:
                self.store.save_report(self.settings.environment, report)
            write_report(self.settings.report_path, reports)
            logger.info(
                "Cycle complete: %d projects assessed, %d BID recommendations",
                len(reports),
                sum(r["decision"] == "BID" for r in reports),
            )

    @staticmethod
    def _rows(result: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("projects"), list)
            or not isinstance(result.get("users", {}), dict)
        ):
            raise APIError("Malformed project collection response")
        return result["projects"], result.get("users", {})

    def _submit(self, project: Project, bidder_id: int, report: dict[str, Any]) -> None:
        env = self.settings.environment
        if self._stop_requested(report):
            return
        if project.owner_id == bidder_id:
            report["submission_status"] = "own_project"
            return
        previous = self.store.attempt_status(env, bidder_id, project.id)
        if previous is not None:
            report["submission_status"] = f"local_duplicate:{previous}"
            return
        if self._stop_requested(report):
            return
        fresh_rows, fresh_users = self._rows(self.api.fetch_project(project.id))
        if self._stop_requested(report):
            return
        matching = [
            row
            for row in fresh_rows
            if isinstance(row, dict) and str(row.get("id")) == str(project.id)
        ]
        if len(matching) != 1:
            report["submission_status"] = "project_unavailable"
            return
        try:
            fresh = parse_project(matching[0], fresh_users)
        except (ValueError, TypeError, KeyError):
            report["submission_status"] = "fresh_project_malformed"
            logger.warning("Fresh project %d is malformed; submission blocked", project.id)
            return
        assessment = evaluate(fresh, self.settings, now=time.time())
        proposal = generate_proposal(fresh, assessment) if assessment.decision == "BID" else ""
        report.update(_report(fresh, assessment, proposal, env))
        if assessment.decision == "BID":
            report["submission_status"] = "pending_live_checks"
        if assessment.decision != "BID":
            report["submission_status"] = "fresh_project_ineligible"
            return
        if fresh.owner_id == bidder_id:
            report["submission_status"] = "own_project"
            return
        if self._stop_requested(report):
            return
        existing_bid = self.api.has_existing_bid(fresh.id, bidder_id)
        if self._stop_requested(report):
            return
        if existing_bid:
            report["submission_status"] = "remote_duplicate"
            return
        claimed = self.store.claim(env, bidder_id, fresh.id, self.settings.max_bids_per_day)
        if claimed != "reserved":
            report["submission_status"] = claimed
            return
        report["submission_status"] = "reserved"
        self.store.save_report(env, report)
        payload = {
            "project_id": fresh.id,
            "bidder_id": bidder_id,
            "description": proposal,
            "amount": float(assessment.bid_amount),
            "period": self.settings.bid_period_days,
            "milestone_percentage": self.settings.milestone_percentage,
        }
        if self._stop_requested(report, reserved=True):
            # Keep the committed intent after interruption; never silently release it.
            return
        try:
            bid_id = self.api.submit_bid(payload)
        except OperationCancelled:
            # The adapter cancelled before HTTP; retain the durable reservation.
            report["submission_status"] = "stopped_reserved"
            raise
        except SubmissionUncertain:
            self.store.finish(env, bidder_id, fresh.id, "uncertain")
            report["submission_status"] = "uncertain_requires_reconciliation"
            logger.error("Bid outcome uncertain for project %d; automatic replay blocked", fresh.id)
            raise
        except APIError:
            # No automatic replay even when the API explicitly rejects the request.
            self.store.finish(env, bidder_id, fresh.id, "rejected")
            report["submission_status"] = "rejected_requires_review"
            raise
        except BaseException:
            # Includes interruption after the write may have reached Freelancer.
            self.store.finish(env, bidder_id, fresh.id, "uncertain")
            report["submission_status"] = "uncertain_requires_reconciliation"
            raise
        self.store.finish(env, bidder_id, fresh.id, "submitted", bid_id)
        report["submission_status"] = "submitted"
        report["bid_id"] = bid_id
        logger.info("Bid submitted for project %d (bid %d)", fresh.id, bid_id)
