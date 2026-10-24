"""Web-only configuration; credentials are never persisted through dashboard settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from ..config import ConfigError, Settings, load_settings


@dataclass(frozen=True)
class WebSettings:
    engine: Settings = field(default_factory=Settings)
    admin_username: str = "admin"
    admin_password_hash: str = field(default="", repr=False)
    database_path: str = "data/web.sqlite3"
    cookie_secure: bool = True
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    origin: str = ""
    demo_mode: bool = False
    start_monitor_thread: bool = True
    session_ttl_seconds: int = 28800
    openai_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"

    def __post_init__(self) -> None:
        if self.demo_mode:
            # A preview must never overwrite or later masquerade as real account
            # data. Derive separate files even when explicit base paths are supplied.
            def demo_path(value: str) -> str:
                path = Path(value)
                if path.stem.endswith(".demo"):
                    return str(path)
                return str(path.with_name(path.stem + ".demo" + path.suffix))

            object.__setattr__(self, "database_path", demo_path(self.database_path))
            object.__setattr__(
                self,
                "engine",
                replace(
                    self.engine,
                    database_path=demo_path(self.engine.database_path),
                    report_path=demo_path(self.engine.report_path),
                ),
            )

    def validate(self) -> None:
        self.engine.validate(require_token=False)
        if not self.admin_password_hash:
            raise ConfigError(
                "Set FREELANCER_ADMIN_PASSWORD_HASH using freelancer-web hash-password"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", self.admin_username):
            raise ConfigError("Invalid administrator username")
        if not self.allowed_hosts or any(
            not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or host == "*"
            for host in self.allowed_hosts
        ):
            raise ConfigError(
                "Set explicit FREELANCER_WEB_ALLOWED_HOSTS without wildcards or ports"
            )
        if not self.cookie_secure and any(
            host not in {"localhost", "127.0.0.1", "testserver"} for host in self.allowed_hosts
        ):
            raise ConfigError("Insecure cookies are permitted only for loopback development hosts")
        if self.origin:
            parsed = urlsplit(self.origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ConfigError("FREELANCER_WEB_ORIGIN must be a plain HTTP(S) origin")
            if parsed.hostname not in self.allowed_hosts:
                raise ConfigError("Web origin host must be in FREELANCER_WEB_ALLOWED_HOSTS")
            if self.cookie_secure and parsed.scheme != "https":
                raise ConfigError("Secure cookies require an HTTPS origin")
        if not 300 <= self.session_ttl_seconds <= 86400:
            raise ConfigError("Session lifetime must be from 300 to 86400 seconds")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", self.openai_model):
            raise ConfigError("Invalid OPENAI_MODEL")
        if not isinstance(self.database_path, str) or not self.database_path.strip():
            raise ConfigError("Web database path is required")
        if Path(self.database_path).resolve() in {
            Path(self.engine.database_path).resolve(),
            Path(self.engine.report_path).resolve(),
        }:
            raise ConfigError(
                "The web database, engine database and report must use separate paths"
            )


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, str(default)).lower().strip()
    if value not in {"true", "false"}:
        raise ConfigError(f"{name} must be true or false")
    return value == "true"


def load_web_settings(env: Mapping[str, str] | None = None) -> WebSettings:
    env = os.environ if env is None else env
    token = env.get("FREELANCER_OAUTH_TOKEN", "")
    # The dashboard can start before account configuration. The placeholder is
    # discarded immediately and is never passed to the official API adapter.
    engine = load_settings(
        env.get("FREELANCER_CONFIG_FILE") or None,
        env={**env, "FREELANCER_OAUTH_TOKEN": token or "configuration-only"},
    )
    engine = replace(engine, token=token)
    try:
        lifetime = int(env.get("FREELANCER_WEB_SESSION_TTL", "28800"))
    except ValueError:
        raise ConfigError("Invalid FREELANCER_WEB_SESSION_TTL") from None
    result = WebSettings(
        engine=engine,
        admin_username=env.get("FREELANCER_ADMIN_USERNAME", "admin"),
        admin_password_hash=env.get("FREELANCER_ADMIN_PASSWORD_HASH", ""),
        database_path=env.get("FREELANCER_WEB_DATABASE", "data/web.sqlite3"),
        cookie_secure=_bool(env, "FREELANCER_WEB_COOKIE_SECURE", True),
        allowed_hosts=tuple(
            h.strip()
            for h in env.get("FREELANCER_WEB_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
            if h.strip()
        ),
        origin=env.get("FREELANCER_WEB_ORIGIN", "").rstrip("/"),
        demo_mode=_bool(env, "FREELANCER_WEB_DEMO_MODE", False),
        start_monitor_thread=_bool(env, "FREELANCER_WEB_START_MONITOR", True),
        session_ttl_seconds=lifetime,
        openai_key=env.get("OPENAI_API_KEY", ""),
        openai_model=env.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
    result.validate()
    return result
