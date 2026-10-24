"""Timezone-aware operating windows and an explicit, single-thread polling lifecycle.

Importing this module does not start monitoring. The web service retains ownership
of submission policy, cancellation, API cooldowns and the persistent bid ledger.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class _MonitoringService(Protocol):
    def monitor_tick(self) -> None:
        """Run a cycle if the persisted controls and cooldown permit it."""


def _wall_time(value: str):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Schedule times must use HH:MM")
    return datetime.strptime(value, "%H:%M").time()


def schedule_allows(schedule: dict, now: datetime | None = None) -> bool:
    """Return whether an aware instant falls inside the configured local window.

    Weekdays are Monday=0 through Sunday=6. The start is inclusive and the end
    exclusive. An overnight window belongs to its starting weekday; equal start
    and end times mean the whole selected calendar day. Disabled schedules allow
    any time. UTC instants are converted through ZoneInfo, including DST folds.
    """
    enabled = schedule.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("Schedule enabled must be a boolean")
    if not enabled:
        return True
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Schedule evaluation requires a timezone-aware time")
    zone = ZoneInfo(schedule["timezone"])
    days = schedule["days"]
    if not isinstance(days, (list, tuple)) or any(
        type(day) is not int or not 0 <= day <= 6 for day in days
    ):
        raise ValueError("Schedule days must be weekdays from 0 through 6")
    start = _wall_time(schedule["start"])
    end = _wall_time(schedule["end"])
    local = current.astimezone(zone)
    clock = local.time().replace(tzinfo=None)
    weekday = local.weekday()
    if start == end:
        return weekday in days
    if start < end:
        return weekday in days and start <= clock < end
    return (clock >= start and weekday in days) or (clock < end and (weekday - 1) % 7 in days)


class Monitor:
    """Call the service serially, at most once per five-minute polling interval.

    ``start`` is idempotent. ``stop`` interrupts the between-cycle wait and joins
    the active worker for at most 35 seconds. The owning app should first request
    service cancellation so that an in-flight API operation can wind down safely.
    A stopped worker can be started again after its previous thread has exited.
    """

    def __init__(self, service: _MonitoringService, *, interval_seconds: float = 300):
        if (
            type(interval_seconds) not in {int, float}
            or not math.isfinite(interval_seconds)
            or interval_seconds < 300
        ):
            raise ValueError("Monitoring interval must be at least 300 seconds")
        self.service = service
        self.interval_seconds = float(interval_seconds)
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._shutdown.clear()
            worker = threading.Thread(target=self._run, name="freelancer-job-monitor", daemon=True)
            self._thread = worker
            worker.start()

    def stop(self) -> None:
        self._shutdown.set()
        with self._lock:
            worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=35)
            if worker.is_alive():
                logger.warning("Monitoring worker is finishing an in-flight operation")

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                self.service.monitor_tick()
            except Exception:
                # Provider exception text can include request context or secrets.
                logger.error("Monitoring cycle failed; check application status")
            deadline = time.monotonic() + self.interval_seconds
            while not self._shutdown.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._shutdown.wait(min(remaining, 30.0))
