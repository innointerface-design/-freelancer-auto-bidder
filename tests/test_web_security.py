from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from freelancer_auto_bidder.web.security import (
    AuthError,
    AuthManager,
    hash_password,
    verify_password,
)

PASSWORD = "test-fixture-password-only"


@pytest.fixture
def encoded():
    return hash_password(PASSWORD)


@pytest.fixture
def manager(tmp_path, encoded):
    return AuthManager(str(tmp_path / "private/web.sqlite3"), "admin", encoded)


def test_scrypt_roundtrip_salts_and_wrong_password(encoded):
    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong-password", encoded)
    assert hash_password(PASSWORD) != encoded
    assert PASSWORD not in encoded


@pytest.mark.parametrize("password", ["", "too-short", "x" * 1025, "😊" * 300])
def test_password_creation_requires_reasonable_length(password):
    with pytest.raises(ValueError):
        hash_password(password)


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "plaintext",
        "scrypt$999999999$8$1$a$b",
        "scrypt$16384$8$1$invalid*$invalid*",
        "scrypt$16384$8$1$YQ==$YQ==",
    ],
)
def test_malformed_hash_rejected_without_expensive_parameters(tmp_path, encoded):
    assert not verify_password(PASSWORD, encoded)
    with pytest.raises(ValueError, match="scrypt"):
        AuthManager(str(tmp_path / "web.db"), "admin", encoded)


def test_sessions_are_opaque_revocable_and_private(manager):
    session = manager.login("admin", PASSWORD, "127.0.0.1")
    assert session["token"] != session["csrf_token"]
    principal = manager.authenticate(session["token"])
    assert principal == {key: session[key] for key in ("username", "csrf_token", "expires_at")}
    with sqlite3.connect(manager.path) as connection:
        row = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()
    assert row[0] != session["token"] and len(row[0]) == 64
    assert session["token"].encode() not in manager.path.read_bytes()
    assert os.stat(manager.path).st_mode & 0o777 == 0o600
    assert os.stat(manager.path.parent).st_mode & 0o777 == 0o700
    manager.logout(session["token"])
    assert manager.authenticate(session["token"]) is None


def test_session_lifetime_is_fixed_and_expired_sessions_removed(manager, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr("freelancer_auto_bidder.web.security.time.time", lambda: now[0])
    session = manager.login("admin", PASSWORD, "ip")
    now[0] += 100
    assert manager.authenticate(session["token"])["expires_at"] == session["expires_at"]
    now[0] = session["expires_at"]
    assert manager.authenticate(session["token"]) is None
    with sqlite3.connect(manager.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("token", [None, "", "unknown", "x" * 100000, "é" * 43])
def test_invalid_tokens_never_authenticate(manager, token):
    assert manager.authenticate(token) is None
    manager.logout(token)


def test_login_failures_persist_and_limit_applies_to_correct_password(manager):
    for _ in range(5):
        with pytest.raises(AuthError) as failure:
            manager.login("admin", "wrong", "ip")
        assert failure.value.status_code == 401
    restarted = AuthManager(str(manager.path), "admin", manager._password_hash)
    with pytest.raises(AuthError) as blocked:
        restarted.login("admin", PASSWORD, "ip")
    assert blocked.value.status_code == 429
    assert restarted.login("admin", PASSWORD, "different-ip")["token"]


def test_login_throttle_expires(manager, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr("freelancer_auto_bidder.web.security.time.time", lambda: now[0])
    for _ in range(5):
        with pytest.raises(AuthError):
            manager.login("admin", "wrong", "ip")
    now[0] += 900
    assert manager.login("admin", PASSWORD, "ip")["token"]


def test_success_resets_only_the_same_ip_failure_count(manager):
    for _ in range(4):
        with pytest.raises(AuthError):
            manager.login("admin", "wrong", "ip")
    manager.login("admin", PASSWORD, "ip")
    for _ in range(5):
        with pytest.raises(AuthError) as failure:
            manager.login("admin", "wrong", "ip")
        assert failure.value.status_code == 401


def test_wrong_username_performs_password_verification(manager, monkeypatch):
    checked = []
    original = verify_password

    def spy(password, encoded):
        checked.append(password)
        return original(password, encoded)

    monkeypatch.setattr("freelancer_auto_bidder.web.security.verify_password", spy)
    with pytest.raises(AuthError, match="Invalid username or password"):
        manager.login("wrong-admin", PASSWORD, "ip")
    assert checked == [PASSWORD]


def test_password_changes_revoke_existing_sessions(manager):
    session = manager.login("admin", PASSWORD, "ip")
    changed = AuthManager(str(manager.path), "admin", hash_password("new-fixture-password"))
    assert changed.authenticate(session["token"]) is None
    assert manager.authenticate(session["token"]) is None


def test_concurrent_failures_cannot_race_beyond_limit(manager, monkeypatch):
    # Fast deterministic verifier isolates the transaction invariant from hash cost.
    monkeypatch.setattr("freelancer_auto_bidder.web.security.verify_password", lambda *_: False)
    barrier = Barrier(8)

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            manager.login("admin", "wrong", "ip")
        except AuthError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert statuses.count(401) == 5
    assert statuses.count(429) == 3
