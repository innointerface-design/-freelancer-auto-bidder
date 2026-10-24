"""Web launcher and an interactive password-hash utility."""

from __future__ import annotations

import argparse
import getpass
import logging
import os

from ..config import ConfigError
from .security import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BidPilot Freelancer dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hash-password", help="Generate a salted admin hash using hidden input")
    serve = commands.add_parser("serve", help="Launch one FastAPI web worker")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        try:
            password = getpass.getpass("Admin password (12+ characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                parser.error("Passwords do not match")
            print(hash_password(password))
        except (ValueError, EOFError) as exc:
            print(str(exc))
            return 2
        return 0
    import uvicorn

    from .app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        app = create_app()
    except (ConfigError, ValueError) as exc:
        print(f"Web configuration error: {exc}")
        return 2
    # Forwarded headers are honored only from explicitly trusted proxies.
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
