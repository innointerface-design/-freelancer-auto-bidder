from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from freelancer_auto_bidder.web.scheduler import Monitor, schedule_allows


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def schedule(**changes):
    return {
        "enabled": True,
        "timezone": "Asia/Karachi",
        "days": [0, 1, 2, 3, 4],
        "start": "09:00",
        "end": "18:00",
    } | changes


def test_disabled_schedule_allows_any_time():
    assert schedule_allows({"enabled": False}, instant("2026-10-25T22:00:00"))


@pytest.mark.parametrize(
    "when,allowed",
    [
        ("2026-10-26T03:59:59", False),
        ("2026-10-26T04:00:00", True),
        ("2026-10-26T12:59:59", True),
        ("2026-10-26T13:00:00", False),
        ("2026-10-24T05:00:00", False),
    ],
)
def test_local_time_weekdays_and_half_open_boundaries(when, allowed):
    assert schedule_allows(schedule(), instant(when)) is allowed


@pytest.mark.parametrize(
    "when,allowed",
    [
        ("2026-10-26T16:59:59", False),
        ("2026-10-26T17:00:00", True),
        ("2026-10-26T20:59:59", True),
        ("2026-10-26T21:00:00", False),
        ("2026-10-27T17:30:00", False),
    ],
)
def test_overnight_window_uses_its_start_day(when, allowed):
    config = schedule(days=[0], start="22:00", end="02:00")
    assert schedule_allows(config, instant(when)) is allowed


def test_overnight_window_wraps_sunday_to_monday():
    config = schedule(timezone="UTC", days=[6], start="22:00", end="02:00")
    assert schedule_allows(config, instant("2026-10-26T01:59:59"))
    assert not schedule_allows(config, instant("2026-10-26T02:00:00"))


def test_equal_times_allow_entire_selected_calendar_day():
    config = schedule(timezone="UTC", days=[0], start="09:00", end="09:00")
    assert schedule_allows(config, instant("2026-10-26T00:00:00"))
    assert schedule_allows(config, instant("2026-10-26T23:59:59"))
    assert not schedule_allows(config, instant("2026-10-27T00:00:00"))


def test_both_dst_fold_occurrences_are_allowed():
    config = schedule(timezone="Europe/London", days=[6], start="01:00", end="02:00")
    assert schedule_allows(config, instant("2026-10-25T00:30:00"))
    assert schedule_allows(config, instant("2026-10-25T01:30:00"))
    assert not schedule_allows(config, instant("2026-10-25T02:00:00"))


def test_spring_dst_gap_does_not_invent_a_local_hour():
    config = schedule(timezone="Europe/London", days=[6], start="01:00", end="02:00")
    assert not schedule_allows(config, instant("2026-03-29T00:30:00"))
    assert not schedule_allows(config, instant("2026-03-29T01:30:00"))


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule_allows(schedule(), datetime(2026, 10, 26, 12))


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "false"},
        {"days": [True]},
        {"days": [7]},
        {"days": "Monday"},
        {"start": "9:00"},
        {"end": "24:00"},
    ],
)
def test_invalid_schedule_fails_closed(changes):
    with pytest.raises(ValueError):
        schedule_allows(schedule(**changes), instant("2026-10-26T10:00:00"))


@pytest.mark.parametrize("interval", [0, 299, -1, float("nan"), float("inf"), True, "300"])
def test_monitor_rejects_intervals_shorter_than_five_minutes_or_invalid(interval):
    with pytest.raises(ValueError):
        Monitor(object(), interval_seconds=interval)


def test_monitor_does_not_start_until_requested_and_stops_promptly():
    called = threading.Event()

    class Service:
        calls = 0

        def monitor_tick(self):
            self.calls += 1
            called.set()

    service = Service()
    monitor = Monitor(service)
    assert service.calls == 0 and not monitor.running
    monitor.start()
    assert called.wait(timeout=2)
    monitor.stop()
    assert not monitor.running and service.calls == 1


def test_concurrent_start_calls_cannot_overlap_cycles():
    entered, release = threading.Event(), threading.Event()

    class Service:
        calls = 0

        def monitor_tick(self):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=3)

    service = Service()
    monitor = Monitor(service)
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: monitor.start(), range(5)))
        assert entered.wait(timeout=2)
        assert service.calls == 1
    finally:
        release.set()
        monitor.stop()
    assert not monitor.running


def test_service_can_request_its_monitor_stop_without_self_join():
    called = threading.Event()

    class Service:
        def monitor_tick(self):
            monitor.stop()
            called.set()

    monitor = Monitor(Service())
    monitor.start()
    assert called.wait(timeout=2)
    monitor.stop()
    assert not monitor.running


def test_monitor_can_restart_after_a_completed_stop():
    called = threading.Event()

    class Service:
        calls = 0

        def monitor_tick(self):
            self.calls += 1
            called.set()

    service = Service()
    monitor = Monitor(service)
    for _ in range(2):
        called.clear()
        monitor.start()
        assert called.wait(timeout=2)
        monitor.stop()
    assert service.calls == 2


def test_worker_errors_are_sanitized_and_worker_remains_stoppable(caplog):
    entered = threading.Event()

    class Service:
        def monitor_tick(self):
            entered.set()
            raise RuntimeError("provider-secret-should-not-be-logged")

    monitor = Monitor(Service())
    with caplog.at_level(logging.ERROR):
        monitor.start()
        assert entered.wait(timeout=2)
        monitor.stop()
    assert "Monitoring cycle failed" in caplog.text
    assert "provider-secret" not in caplog.text
