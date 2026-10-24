"""Fictional dashboard examples in the official API's result shape.

This module never connects to Freelancer. Demo records do not represent real
clients or available projects, and DemoAPI refuses every submission request.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable

from ..api import OperationCancelled, PermissionDenied


def make_demo_result() -> dict:
    now = time.time()
    examples = (
        (
            "YouTube Shorts editor for a wellness series",
            "Edit six 30-second vertical videos for YouTube Shorts. Add readable captions, "
            "clean audio and an engaging opening. Source footage and brand assets are supplied.",
            "US",
            "United States",
            "USD",
            100,
            300,
            True,
            ("Video Editing", "YouTube"),
        ),
        (
            "After Effects motion graphics for a product launch",
            "Create motion graphics for a 45-second product launch video. Use our brand "
            "assets, kinetic typography and smooth transitions in Adobe After Effects.",
            "GB",
            "United Kingdom",
            "USD",
            150,
            500,
            True,
            ("Motion Graphics", "After Effects"),
        ),
        (
            "AI video ad for a skincare product",
            "Create a 30-second AI video ad from an approved storyboard and product assets. "
            "Keep the promotional video brand-aligned with clear product benefits.",
            "CA",
            "Canada",
            "USD",
            120,
            400,
            True,
            ("AI Video", "Video Production"),
        ),
        (
            "Product video editing in DaVinci Resolve",
            "Edit a 60-second product video using our supplied footage. We need color "
            "grading, balanced audio and polished transitions in DaVinci Resolve.",
            "AU",
            "Australia",
            "USD",
            100,
            350,
            True,
            ("Video Editing", "DaVinci Resolve"),
        ),
        (
            "Promotional Instagram Reels for a cafe",
            "Edit four short-form promotional videos for Instagram Reels from supplied "
            "footage. Include 9:16 framing, captions and our existing brand colors.",
            "AE",
            "United Arab Emirates",
            "AED",
            400,
            1000,
            False,
            ("Video Editing", "Instagram"),
        ),
        (
            "TikTok video editing with captions",
            "Edit three 30-second TikTok videos with readable captions, clean pacing "
            "and vertical framing. We will provide the source footage and visual reference.",
            None,
            None,
            "USD",
            80,
            250,
            True,
            ("Video Editing", "TikTok"),
        ),
        (
            "Short-form video editing on a small budget",
            "Edit a 30-second short-form video from supplied clips with simple transitions "
            "and readable captions. Please keep to the listed fixed budget.",
            "CA",
            "Canada",
            "GBP",
            5,
            15,
            True,
            ("Video Editing",),
        ),
        (
            "Build a video editing web application",
            "Develop a video editing web application with user accounts, a rendering API "
            "and cloud storage. This is a software development project requiring coding.",
            "US",
            "United States",
            "USD",
            300,
            1000,
            True,
            ("Python", "Web Development", "Video Editing"),
        ),
    )
    projects = []
    users = {}
    for index, example in enumerate(examples):
        title, description, code, country_name, currency, minimum, maximum, paid, skills = example
        project_id, owner_id = 70001 + index, 90001 + index
        projects.append(
            {
                "id": project_id,
                "owner_id": owner_id,
                "title": title,
                "description": description,
                "type": "fixed",
                "status": "active",
                "time_submitted": now - (index + 1) * 600,
                "currency": {"code": currency},
                "budget": {"minimum": minimum, "maximum": maximum},
                "jobs": [{"name": skill} for skill in skills],
            }
        )
        users[str(owner_id)] = {
            "id": owner_id,
            "location": {"country": {"code": code, "name": country_name}} if code else {},
            "status": {"payment_verified": paid},
            "employer_reputation": {
                "entire_history": {"overall": 4.8, "reviews": 12 + index},
            },
        }
    return {"projects": projects, "users": users}


class DemoAPI:
    """Offline adapter with the engine's API interface and no submission capability."""

    def __init__(self, settings, *, should_stop: Callable[[], bool] = lambda: False):
        self.settings = settings
        self._should_stop = should_stop
        self._result = make_demo_result()

    def _check_stop(self) -> None:
        if self._should_stop():
            raise OperationCancelled("Demo operation stopped")

    def begin_cycle(self) -> None:
        self._check_stop()

    def search_projects(self, query: str, limit: int, offset: int, from_time: float) -> dict:
        self._check_stop()
        words = [word for word in query.casefold().split() if len(word) > 2]
        projects = [
            row
            for row in self._result["projects"]
            if row["time_submitted"] >= from_time
            and (
                not words
                or any(
                    word in (row["title"] + " " + row["description"]).casefold() for word in words
                )
            )
        ]
        return {
            "projects": copy.deepcopy(projects[offset : offset + limit]),
            "users": copy.deepcopy(self._result["users"]),
        }

    def fetch_project(self, project_id: int) -> dict:
        self._check_stop()
        return {
            "projects": copy.deepcopy(
                [row for row in self._result["projects"] if row["id"] == project_id]
            ),
            "users": copy.deepcopy(self._result["users"]),
        }

    def get_self_id(self) -> int:
        self._check_stop()
        return 99999

    def has_existing_bid(self, project_id: int, bidder_id: int) -> bool:
        self._check_stop()
        return False

    def submit_bid(self, payload: dict) -> int:
        raise PermissionDenied("Demo mode never submits real bids")

    def close(self) -> None:
        pass
