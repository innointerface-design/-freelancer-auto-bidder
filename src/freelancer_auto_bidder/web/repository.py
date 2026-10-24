"""Private, durable web state with a fresh SQLite connection per operation.

The original bidding engine keeps its own ledger. This repository only adds
dashboard settings, controls, activity, analytics and editable proposal drafts.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..store import _private_parent

_CONTROLS = {"monitor_enabled": False, "auto_bidder_enabled": False}
_RUNTIME = {
    "last_run_at": None,
    "next_run_at": None,
    "last_error": None,
    "blocked_until": 0,
}


def _merge(defaults: dict, saved: dict) -> dict:
    result = copy.deepcopy(defaults)
    for key, value in saved.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class WebRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        _private_parent(self.path.parent)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS web_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    utc_day TEXT NOT NULL,
                    assessed INTEGER NOT NULL,
                    recommended INTEGER NOT NULL,
                    submitted INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS web_runs_day ON web_runs(utc_day);
                CREATE TABLE IF NOT EXISTS web_drafts (
                    environment TEXT NOT NULL,
                    project_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(environment, project_id)
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _read_state(connection: sqlite3.Connection, key: str, defaults: dict) -> dict:
        row = connection.execute("SELECT value_json FROM web_state WHERE key=?", (key,)).fetchone()
        if row is None:
            return copy.deepcopy(defaults)
        value = json.loads(row["value_json"])
        if not isinstance(value, dict):
            raise ValueError("Malformed dashboard state")
        return _merge(defaults, value)

    @staticmethod
    def _write_state(connection: sqlite3.Connection, key: str, value: dict) -> None:
        connection.execute(
            "INSERT INTO web_state(key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, _json(value)),
        )

    def get_settings(self, defaults: dict) -> dict:
        with self._connect() as connection:
            return self._read_state(connection, "settings", defaults)

    def save_settings(self, settings: dict) -> None:
        encoded = _json(settings)
        if not isinstance(settings, dict):
            raise ValueError("Dashboard settings must be an object")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO web_state(key, value_json) VALUES ('settings', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (encoded,),
            )

    def controls(self) -> dict:
        with self._connect() as connection:
            return self._read_state(connection, "controls", _CONTROLS)

    def set_controls(self, **values: bool) -> dict:
        if set(values) - set(_CONTROLS) or any(
            type(value) is not bool for value in values.values()
        ):
            raise ValueError("Invalid dashboard controls")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                controls = self._read_state(connection, "controls", _CONTROLS)
                controls.update(values)
                if values.get("auto_bidder_enabled") is True:
                    controls["monitor_enabled"] = True
                if values.get("monitor_enabled") is False:
                    controls["monitor_enabled"] = False
                    controls["auto_bidder_enabled"] = False
                self._write_state(connection, "controls", controls)
                connection.execute("COMMIT")
                return controls
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def runtime(self) -> dict:
        with self._connect() as connection:
            return self._read_state(connection, "runtime", _RUNTIME)

    def set_runtime(self, **values: Any) -> None:
        # Validate serialization before opening the update transaction.
        _json(values)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                runtime = self._read_state(connection, "runtime", _RUNTIME)
                runtime.update(values)
                self._write_state(connection, "runtime", runtime)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def event(self, message: str, level: str = "info") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO web_events(created_at, level, message) VALUES (?, ?, ?)",
                (time.time(), level, message),
            )

    def events(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Activity limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, level, message FROM web_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_run(
        self, assessed: int, recommended: int, submitted: int, *, now: float | None = None
    ) -> None:
        if any(type(value) is not int or value < 0 for value in (assessed, recommended, submitted)):
            raise ValueError("Run counters must be nonnegative integers")
        now = time.time() if now is None else now
        utc_day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO web_runs(created_at, utc_day, assessed, recommended, submitted) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, utc_day, assessed, recommended, submitted),
            )

    def chart(self, days: int = 7) -> list[dict]:
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("Chart days must be between 1 and 365")
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=days - 1)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT utc_day, SUM(assessed) AS assessed, SUM(recommended) AS recommended, "
                "SUM(submitted) AS submitted FROM web_runs WHERE utc_day>=? AND utc_day<=? "
                "GROUP BY utc_day ORDER BY utc_day",
                (first.isoformat(), today.isoformat()),
            ).fetchall()
        recorded = {row["utc_day"]: row for row in rows}
        result = []
        for offset in range(days):
            day = (first + timedelta(days=offset)).isoformat()
            row = recorded.get(day)
            result.append(
                {
                    "date": day,
                    "assessed": row["assessed"] if row else 0,
                    "recommended": row["recommended"] if row else 0,
                    "submitted": row["submitted"] if row else 0,
                }
            )
        return result

    def save_draft(self, project_id: int, environment: str, text: str, provider: str) -> dict:
        updated_at = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO web_drafts(environment, project_id, text, provider, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(environment, project_id) DO UPDATE SET "
                "text=excluded.text, provider=excluded.provider, updated_at=excluded.updated_at",
                (environment, project_id, text, provider, updated_at),
            )
        return {"text": text, "provider": provider, "updated_at": updated_at}

    def get_draft(self, project_id: int, environment: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT text, provider, updated_at FROM web_drafts "
                "WHERE project_id=? AND environment=?",
                (project_id, environment),
            ).fetchone()
        return dict(row) if row else None

    def draft_count(self, environment: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM web_drafts WHERE environment=?", (environment,)
            ).fetchone()[0]
