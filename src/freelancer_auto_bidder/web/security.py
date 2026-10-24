"""Password hashing and durable, revocable admin sessions.

Cookies carry opaque random tokens. Only token hashes are stored; request-layer
cookie attributes, origin checks, and CSRF enforcement belong to the FastAPI app.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..store import _private_parent

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_PASSWORD_BYTES = 1024
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_FAILURE_WINDOW = 15 * 60
_FAILURE_LIMIT = 5


class AuthError(ValueError):
    def __init__(self, message: str, status_code: int = 401):
        self.status_code = status_code
        super().__init__(message)


def _password_bytes(password: str) -> bytes | None:
    if not isinstance(password, str) or not password or len(password) > _MAX_PASSWORD_BYTES:
        return None
    try:
        encoded = password.encode("utf-8")
    except UnicodeError:
        return None
    return encoded if len(encoded) <= _MAX_PASSWORD_BYTES else None


def _parse_password_hash(encoded: str) -> tuple[bytes, bytes]:
    """Accept one fixed cost; untrusted stored parameters cannot increase hash work."""
    try:
        if not isinstance(encoded, str) or len(encoded) > 200:
            raise ValueError
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            raise ValueError
        salt = base64.b64decode(salt_text, validate=True)
        digest = base64.b64decode(digest_text, validate=True)
        if len(salt) != 16 or len(digest) != 32:
            raise ValueError
        return salt, digest
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("Admin password hash must use the supported scrypt format") from None


def hash_password(password: str) -> str:
    """Produce a salted hash for secret-manager configuration, never a default password."""
    value = _password_bytes(password)
    if value is None or len(password) < 12:
        raise ValueError(
            "Admin password must be at least 12 characters and at most 1024 UTF-8 bytes"
        )
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "$".join(
        (
            "scrypt",
            "16384",
            "8",
            "1",
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    value = _password_bytes(password)
    if value is None:
        return False
    try:
        salt, expected = _parse_password_hash(encoded)
    except ValueError:
        return False
    actual = hashlib.scrypt(value, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return hmac.compare_digest(actual, expected)


def _token_digest(token: object) -> str | None:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthManager:
    """One configured admin, persistent per-IP throttling, fixed-expiry sessions."""

    def __init__(
        self, db_path: str, username: str, password_hash: str, *, session_ttl_seconds: int = 28800
    ):
        _parse_password_hash(password_hash)
        if not isinstance(username, str) or not username.strip() or len(username) > 128:
            raise ValueError("Admin username must be a nonempty string of at most 128 characters")
        if type(session_ttl_seconds) is not int or not 1 <= session_ttl_seconds <= 86400:
            raise ValueError("Admin session lifetime must be from 1 to 86400 seconds")
        self.path = Path(db_path)
        self.username = username
        self._password_hash = password_hash
        self._credential_digest = hashlib.sha256(
            (username + "\0" + password_hash).encode("utf-8")
        ).hexdigest()
        self.session_ttl_seconds = session_ttl_seconds
        _private_parent(self.path.parent)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    credential_digest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS auth_failures (
                    id INTEGER PRIMARY KEY,
                    ip_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_failures_ip ON auth_failures(ip_hash, created_at);
            """)
            # A changed password or username revokes old sessions across restarts.
            connection.execute(
                "DELETE FROM auth_sessions WHERE credential_digest != ?", (self._credential_digest,)
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    def login(self, username: str, password: str, ip: str) -> dict:
        if not isinstance(ip, str) or len(ip) > 128:
            ip = "unknown"
        ip_hash = hashlib.sha256(ip.encode("utf-8", errors="replace")).hexdigest()
        now = time.time()
        with self._connection() as connection:
            # Serialize check+failure update, including concurrent requests/workers.
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM auth_failures WHERE created_at <= ?", (now - _FAILURE_WINDOW,)
                )
                connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
                failures = connection.execute(
                    "SELECT COUNT(*) FROM auth_failures WHERE ip_hash=?",
                    (ip_hash,),
                ).fetchone()[0]
                if failures >= _FAILURE_LIMIT:
                    connection.execute("COMMIT")
                    raise AuthError("Too many login attempts; try again later", 429)
                # Always perform the same password check for valid-size inputs,
                # including when a supplied username is wrong.
                password_ok = verify_password(password, self._password_hash)
                username_ok = (
                    isinstance(username, str)
                    and len(username) <= 128
                    and hmac.compare_digest(
                        username.encode("utf-8", errors="replace"), self.username.encode("utf-8")
                    )
                )
                if not password_ok or not username_ok:
                    connection.execute(
                        "INSERT INTO auth_failures(ip_hash, created_at) VALUES (?, ?)",
                        (ip_hash, now),
                    )
                    connection.execute("COMMIT")
                    raise AuthError("Invalid username or password")
                token = secrets.token_urlsafe(32)
                csrf_token = secrets.token_urlsafe(32)
                expires_at = now + self.session_ttl_seconds
                connection.execute(
                    "INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?)",
                    (
                        _token_digest(token),
                        self.username,
                        csrf_token,
                        expires_at,
                        self._credential_digest,
                    ),
                )
                connection.execute("DELETE FROM auth_failures WHERE ip_hash=?", (ip_hash,))
                connection.execute("COMMIT")
                return {
                    "token": token,
                    "csrf_token": csrf_token,
                    "username": self.username,
                    "expires_at": expires_at,
                }
            except AuthError:
                raise
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def authenticate(self, token: str) -> dict | None:
        digest = _token_digest(token)
        if digest is None:
            return None
        now = time.time()
        with self._connection() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            row = connection.execute(
                "SELECT username, csrf_token, expires_at FROM auth_sessions "
                "WHERE token_hash=? AND credential_digest=? AND expires_at > ?",
                (digest, self._credential_digest, now),
            ).fetchone()
        return dict(row) if row is not None else None

    def logout(self, token: str) -> None:
        digest = _token_digest(token)
        if digest is not None:
            with self._connection() as connection:
                connection.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest,))
