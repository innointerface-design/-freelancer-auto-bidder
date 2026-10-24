from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from freelancer_auto_bidder.web.repository import WebRepository


def test_defaults_are_copied_and_nested_saved_settings_are_merged(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")
    defaults = {"pricing": {"daily_limit": 3, "USD": {"minimum": 50, "maximum": 250}}}
    loaded = repository.get_settings(defaults)
    loaded["pricing"]["USD"]["maximum"] = 500
    assert defaults["pricing"]["USD"]["maximum"] == 250
    repository.save_settings({"pricing": {"USD": {"minimum": 75}}})
    assert repository.get_settings(defaults) == {
        "pricing": {"daily_limit": 3, "USD": {"minimum": 75, "maximum": 250}}
    }


def test_controls_persist_and_cannot_automate_without_monitoring(tmp_path):
    path = tmp_path / "web.sqlite3"
    repository = WebRepository(path)
    assert repository.controls() == {"monitor_enabled": False, "auto_bidder_enabled": False}
    assert repository.set_controls(auto_bidder_enabled=True) == {
        "monitor_enabled": True,
        "auto_bidder_enabled": True,
    }
    restarted = WebRepository(path)
    assert restarted.controls()["auto_bidder_enabled"] is True
    assert restarted.set_controls(monitor_enabled=False) == {
        "monitor_enabled": False,
        "auto_bidder_enabled": False,
    }
    assert restarted.set_controls(monitor_enabled=False, auto_bidder_enabled=True) == {
        "monitor_enabled": False,
        "auto_bidder_enabled": False,
    }


@pytest.mark.parametrize(
    "controls",
    [
        {"monitor_enabled": "false"},
        {"auto_bidder_enabled": 1},
        {"unknown": True},
    ],
)
def test_invalid_controls_are_rejected(tmp_path, controls):
    repository = WebRepository(tmp_path / "web.sqlite3")
    with pytest.raises(ValueError):
        repository.set_controls(**controls)
    assert not repository.controls()["auto_bidder_enabled"]


def test_runtime_updates_are_persistent_and_do_not_discard_other_fields(tmp_path):
    path = tmp_path / "web.sqlite3"
    repository = WebRepository(path)
    assert repository.runtime() == {
        "last_run_at": None,
        "next_run_at": None,
        "last_error": None,
        "blocked_until": 0,
    }
    repository.set_runtime(last_run_at=100, blocked_until=500)
    repository.set_runtime(last_error="API unavailable")
    assert WebRepository(path).runtime() == {
        "last_run_at": 100,
        "next_run_at": None,
        "last_error": "API unavailable",
        "blocked_until": 500,
    }


def test_drafts_are_updated_without_crossing_environments(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")
    assert repository.get_draft(1, "production") is None
    first = repository.save_draft(1, "production", "A client's original draft", "template")
    assert first["text"] == "A client's original draft"
    repository.save_draft(1, "sandbox", "Sandbox draft", "openai")
    final = repository.save_draft(1, "production", "Edited draft", "edited")
    assert repository.get_draft(1, "production") == final
    assert repository.get_draft(1, "sandbox")["text"] == "Sandbox draft"
    assert repository.draft_count("production") == 1
    assert repository.draft_count("sandbox") == 1
    assert repository.draft_count("demo") == 0


def test_activity_is_newest_first_and_limits_results(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")
    repository.event("Started monitoring")
    repository.event("API unavailable", "warning")
    events = repository.events(limit=1)
    assert len(events) == 1
    assert events[0]["message"] == "API unavailable"
    assert events[0]["level"] == "warning"
    assert isinstance(events[0]["id"], int) and isinstance(events[0]["created_at"], float)


def test_chart_aggregates_actual_utc_runs_and_fills_empty_days(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    repository.record_run(10, 5, 1, now=today.timestamp())
    repository.record_run(4, 2, 0, now=(today + timedelta(minutes=10)).timestamp())
    repository.record_run(3, 1, 0, now=(today - timedelta(days=2)).timestamp())
    repository.record_run(999, 999, 999, now=(today - timedelta(days=20)).timestamp())
    chart = repository.chart(3)
    assert chart == [
        {
            "date": (today - timedelta(days=2)).date().isoformat(),
            "assessed": 3,
            "recommended": 1,
            "submitted": 0,
        },
        {
            "date": (today - timedelta(days=1)).date().isoformat(),
            "assessed": 0,
            "recommended": 0,
            "submitted": 0,
        },
        {"date": today.date().isoformat(), "assessed": 14, "recommended": 7, "submitted": 1},
    ]


def test_shared_repository_can_be_used_by_many_threads(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")

    def write(index):
        repository.set_runtime(**{f"worker_{index}": index})
        repository.event(f"Worker {index}")
        repository.record_run(1, 1, 0)
        repository.save_draft(index, "production", f"Draft {index}", "template")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(20)))
    assert len(repository.events(100)) == 20
    assert repository.draft_count("production") == 20
    assert repository.chart(1)[0]["assessed"] == 20
    assert all(repository.runtime()[f"worker_{index}"] == index for index in range(20))


def test_repository_and_new_parent_directory_are_private(tmp_path):
    path = tmp_path / "private" / "web.sqlite3"
    WebRepository(path).event("Initialized")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not any(path.parent.glob("*.json"))


def test_nonfinite_json_is_rejected_without_changing_stored_state(tmp_path):
    repository = WebRepository(tmp_path / "web.sqlite3")
    repository.save_settings({"safe": True})
    with pytest.raises(ValueError):
        repository.save_settings({"unsafe": float("nan")})
    with pytest.raises(ValueError):
        repository.set_runtime(blocked_until=float("inf"))
    assert repository.get_settings({}) == {"safe": True}
    assert repository.runtime()["blocked_until"] == 0


def test_settings_are_visible_to_new_instances(tmp_path):
    path = tmp_path / "web.sqlite3"
    WebRepository(path).save_settings({"ai": {"tone": "Friendly"}})
    assert WebRepository(Path(path)).get_settings({}) == {"ai": {"tone": "Friendly"}}
