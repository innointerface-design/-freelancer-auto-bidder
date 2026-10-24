"""Command-line entry point. Polling never starts on import."""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import threading
import time
from collections import Counter

from .api import (
    APIError,
    AuthenticationError,
    FreelancerAPI,
    OperationCancelled,
    PermissionDenied,
    RateLimited,
    RequestBudgetExceeded,
    SubmissionUncertain,
)
from .config import ConfigError, load_settings
from .runner import Runner
from .store import Store

log = logging.getLogger(__name__)


class _Redact(logging.Filter):
    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = message.replace(self.secret, "[REDACTED]") if self.secret else message
        record.args = ()
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freelancer.com dry-run-first bidder")
    parser.add_argument(
        "--config", help="Path to TOML configuration (secrets are environment-only)"
    )
    parser.add_argument("--log-level", choices=["INFO", "WARNING", "ERROR"], default="INFO")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate configuration and local safety gates; no API calls")
    run = commands.add_parser(
        "run", help="Search, score, generate proposals, and optionally submit"
    )
    run.add_argument("--once", action="store_true", help="Run a single polling cycle")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_Redact(settings.token))
    # Library wire logging can expose URL or header context; app logs are sufficient.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    mode = (
        "LIVE: automatic submission enabled"
        if settings.live_enabled
        else "DRY RUN: no bids submitted"
    )
    log.warning("%s; environment=%s", mode, settings.environment)
    if args.command == "check":
        print(f"Configuration valid. {mode}. API permission has NOT been verified by this command.")
        return 0
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    # No long blocking sleeps: signals interrupt Event.wait between polling cycles.
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
    api = FreelancerAPI(settings, should_stop=stopped.is_set)
    try:
        with Store(settings.database_path) as store:
            runner = Runner(settings, api, store, should_stop=stopped.is_set)
            while not stopped.is_set():
                started = time.monotonic()
                delay = float(settings.poll_interval_seconds)
                exit_code = 0
                try:
                    reports = runner.run_once()
                    counts = Counter(row["decision"] for row in reports)
                    log.info(
                        "BID=%d SKIP=%d; report=%s",
                        counts["BID"],
                        counts["SKIP"],
                        settings.report_path,
                    )
                except OperationCancelled:
                    log.info("Polling stopped before the next API request")
                    return 0
                except (AuthenticationError, PermissionDenied) as exc:
                    log.error("%s. Polling stopped; correct permissions before restarting.", exc)
                    return 3
                except SubmissionUncertain as exc:
                    log.error(
                        "%s. Polling stopped; inspect the bid ledger and Freelancer account.", exc
                    )
                    return 4
                except RateLimited as exc:
                    delay = max(delay, exc.retry_after)
                    log.warning("%s", exc)
                    exit_code = 5
                except RequestBudgetExceeded as exc:
                    log.warning("%s; consider narrowing queries", exc)
                    exit_code = 5
                except APIError as exc:
                    log.error("%s; next cycle will retry reads", exc)
                    exit_code = 5
                if args.once:
                    return exit_code
                # Wait a full cooldown after a rate error, otherwise keep a 5-minute start cadence.
                if exit_code != 5:
                    delay = max(0.0, delay - (time.monotonic() - started))
                log.info("Next polling cycle in %.0f seconds", delay)
                while delay > 0 and not stopped.is_set():
                    chunk = min(delay, 30.0)
                    stopped.wait(chunk)
                    delay -= chunk
            return 0
    except (OSError, sqlite3.Error):
        log.error(
            "Local state/report I/O failed; polling stopped. Check data paths and permissions."
        )
        return 6
    finally:
        api.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
