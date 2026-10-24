"""Durable bid intent and recommendation storage.

An intent is never automatically released: after a timeout or process crash the
operator must reconcile it against Freelancer before deciding what to do next.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _private_parent(path: Path) -> None:
    """Create private app directories without changing existing directory modes."""
    if path.exists():
        return
    _private_parent(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass


def write_report(path: str | Path, reports: list[dict[str, Any]]) -> None:
    """Replace the report atomically, keeping proposal/client data private."""
    target = Path(path)
    _private_parent(target.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            json.dump(reports, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        _private_parent(self.path.parent)
        # Create securely before SQLite opens it; SQLite WAL/SHM inherit this mode.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        self.connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS attempts (
                environment TEXT NOT NULL,
                bidder_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                utc_day TEXT NOT NULL,
                status TEXT NOT NULL,
                bid_id INTEGER,
                PRIMARY KEY(environment, bidder_id, project_id)
            );
            CREATE INDEX IF NOT EXISTS attempts_daily
                ON attempts(environment, bidder_id, utc_day);
            CREATE TABLE IF NOT EXISTS assessments (
                environment TEXT NOT NULL,
                project_id INTEGER NOT NULL,
                updated_at REAL NOT NULL,
                report_json TEXT NOT NULL,
                PRIMARY KEY(environment, project_id)
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def save_report(self, environment: str, report: dict[str, Any]) -> None:
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
        self.connection.execute(
            "INSERT INTO assessments VALUES (?, ?, ?, ?) "
            "ON CONFLICT(environment, project_id) DO UPDATE SET "
            "updated_at=excluded.updated_at, report_json=excluded.report_json",
            (environment, report["id"], time.time(), encoded),
        )

    def attempt_status(self, environment: str, bidder_id: int, project_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM attempts WHERE environment=? AND bidder_id=? AND project_id=?",
            (environment, bidder_id, project_id),
        ).fetchone()
        return row["status"] if row else None

    def claim(
        self,
        environment: str,
        bidder_id: int,
        project_id: int,
        daily_limit: int,
        *,
        now: float | None = None,
    ) -> str:
        """Atomically enforce both duplicate prevention and the account's daily cap."""
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.attempt_status(environment, bidder_id, project_id) is not None:
                result = "duplicate"
            else:
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM attempts "
                    "WHERE environment=? AND bidder_id=? AND utc_day=?",
                    (environment, bidder_id, day),
                ).fetchone()[0]
                if count >= daily_limit:
                    result = "daily_limit"
                else:
                    self.connection.execute(
                        "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, 'reserved', NULL)",
                        (environment, bidder_id, project_id, now, day),
                    )
                    result = "reserved"
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def finish(
        self,
        environment: str,
        bidder_id: int,
        project_id: int,
        status: str,
        bid_id: int | None = None,
    ) -> None:
        if status not in {"submitted", "uncertain", "rejected"}:
            raise ValueError("Invalid terminal attempt status")
        updated = self.connection.execute(
            "UPDATE attempts SET status=?, bid_id=? "
            "WHERE environment=? AND bidder_id=? AND project_id=? AND status='reserved'",
            (status, bid_id, environment, bidder_id, project_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError("Missing or already completed bid intent")
