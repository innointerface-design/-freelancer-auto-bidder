"""HTTP-level security and dashboard integration without external API requests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from freelancer_auto_bidder.config import ConfigError, Settings
from freelancer_auto_bidder.web.app import create_app
from freelancer_auto_bidder.web.security import hash_password
from freelancer_auto_bidder.web.settings import WebSettings, load_web_settings

PASSWORD = "http-test-fixture-password"


@pytest.fixture(scope="module")
def admin_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def web_settings(tmp_path, admin_hash):
    return WebSettings(
        engine=Settings(
            database_path=str(tmp_path / "engine.sqlite3"),
            report_path=str(tmp_path / "recommendations.json"),
        ),
        admin_password_hash=admin_hash,
        database_path=str(tmp_path / "web.sqlite3"),
        cookie_secure=False,
        allowed_hosts=("testserver",),
        origin="http://testserver",
        demo_mode=True,
        start_monitor_thread=False,
    )


@pytest.fixture
def client(web_settings):
    with TestClient(create_app(web_settings)) as client:
        yield client


def sign_in(client):
    response = client.post(
        "/api/auth/login",
        headers={"X-Login-Intent": "admin"},
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_public_health_login_and_static_assets_are_available(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert "password" in client.get("/").text.lower()
    assert client.get("/login").status_code == 200
    assert client.get("/static/login.js").status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/session",
        "/api/dashboard",
        "/api/jobs",
        "/api/history",
        "/api/settings",
    ],
)
def test_all_dashboard_reads_require_authentication(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in to continue"


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/api/search", {"query": "video editing"}),
        ("post", "/api/controls", {"monitor_enabled": True}),
        ("patch", "/api/settings", {"ai": {"tone": "concise"}}),
        ("post", "/api/jobs/70001/proposal", {}),
        ("put", "/api/jobs/70001/proposal", {"text": "Draft text"}),
        ("post", "/api/auth/logout", {}),
    ],
)
def test_dashboard_mutations_require_session(client, method, path, payload):
    assert client.request(method, path, json=payload).status_code == 401


def test_login_returns_opaque_http_only_session_and_csrf_separately(client):
    response = client.post(
        "/api/auth/login",
        headers={"X-Login-Intent": "admin"},
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert "token" not in response.json()
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["csrf_token"] == response.json()["csrf_token"]
    assert session.json()["username"] == "admin"
    assert "password" not in session.text


def test_secure_cookie_and_hsts_in_https_mode(web_settings):
    secure = replace(web_settings, cookie_secure=True, origin="https://testserver")
    with TestClient(create_app(secure), base_url="https://testserver") as client:
        response = client.post(
            "/api/auth/login",
            headers={"X-Login-Intent": "admin"},
            json={"username": "admin", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()
        assert response.headers["strict-transport-security"].startswith("max-age=")


def test_new_login_rotates_and_revokes_previous_session(client):
    sign_in(client)
    first = client.cookies.get("bidder_session")
    sign_in(client)
    assert client.cookies.get("bidder_session") != first
    assert client.app.state.auth.authenticate(first) is None


def test_logout_revokes_server_session_and_clears_cookie(client):
    headers = sign_in(client)
    old_cookie = client.cookies.get("bidder_session")
    response = client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 200
    assert client.app.state.auth.authenticate(old_cookie) is None
    assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Login-Intent": "wrong"},
        {"X-Login-Intent": "admin", "Origin": "https://attacker.example"},
        {"X-Login-Intent": "admin", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_login_rejects_missing_intent_or_untrusted_origin(client, headers):
    response = client.post(
        "/api/auth/login",
        headers=headers,
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 403
    assert client.get("/api/session").status_code == 401


def test_incorrect_login_is_generic_and_rate_limited(client):
    for index in range(6):
        response = client.post(
            "/api/auth/login",
            headers={"X-Login-Intent": "admin"},
            json={"username": "not-admin", "password": "wrong-fixture-password"},
        )
        assert response.status_code == (401 if index < 5 else 429)
        assert "wrong-fixture-password" not in response.text


def test_valid_session_requires_csrf_and_matching_origin(client):
    csrf = sign_in(client)
    assert client.post("/api/controls", json={"monitor_enabled": True}).status_code == 403
    assert (
        client.post(
            "/api/controls",
            headers={"X-CSRF-Token": "incorrect"},
            json={"monitor_enabled": True},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/controls",
            headers={**csrf, "Origin": "https://attacker.example"},
            json={"monitor_enabled": True},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/controls",
            headers={**csrf, "Sec-Fetch-Site": "cross-site"},
            json={"monitor_enabled": True},
        ).status_code
        == 403
    )
    assert not client.get("/api/dashboard").json()["monitor_enabled"]


def test_non_ascii_csrf_header_fails_closed(client):
    sign_in(client)
    response = client.post(
        "/api/controls",
        headers=[(b"x-csrf-token", b"\xff")],
        json={"monitor_enabled": True},
    )
    assert response.status_code == 403


def test_untrusted_host_rejected_before_login(client):
    assert client.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400


def test_security_headers_prevent_inline_scripts_embedding_and_caching(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


def test_validation_errors_do_not_echo_password_or_unknown_secret_fields(client):
    secret = "sensitive-fixture-value"
    response = client.post(
        "/api/auth/login",
        headers={"X-Login-Intent": "admin"},
        json={"username": "admin", "password": secret, "api_key": secret},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert response.json() == {"detail": "Invalid request fields"}


def test_oversized_and_malformed_json_requests_fail_cleanly(client):
    response = client.post(
        "/api/auth/login",
        headers={"Content-Type": "application/json"},
        content='{"password":"' + "x" * 65536 + '"}',
    )
    assert response.status_code == 413
    response = client.post(
        "/api/auth/login",
        headers={"Content-Type": "application/json"},
        content='{"password": invalid-sensitive-fixture}',
    )
    assert response.status_code == 422
    assert "invalid-sensitive-fixture" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"monitor_enabled": "true"},
        {"auto_bidder_enabled": 1},
        {"dry_run": False},
        {"FREELANCER_OAUTH_TOKEN": "secret"},
    ],
)
def test_controls_accept_only_strict_known_booleans(client, payload):
    headers = sign_in(client)
    assert client.post("/api/controls", headers=headers, json=payload).status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "sort=invalid",
        "min_budget=-1",
    ],
)
def test_job_filter_parameter_bounds(client, query):
    sign_in(client)
    assert client.get("/api/jobs?" + query).status_code == 422


def test_demo_login_search_draft_edit_history_and_safe_controls(client):
    headers = sign_in(client)
    before = client.get("/api/dashboard").json()
    assert before["mode"] == "demo" and not before["live_allowed"]
    assert not before["auto_bidder_enabled"]
    response = client.post("/api/search", headers=headers, json={"query": "video editing"})
    assert response.status_code == 200, response.text
    jobs = client.get("/api/jobs?countries=US,GB,CA,AU,AE&decision=BID").json()
    assert jobs["total"] >= 1
    project_id = jobs["items"][0]["id"]
    proposal = client.post(f"/api/jobs/{project_id}/proposal", headers=headers, json={})
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["provider"] == "template"
    assert proposal.json()["text"]
    saved = client.put(
        f"/api/jobs/{project_id}/proposal",
        headers=headers,
        json={"text": "Hello, this is my reviewed project-specific draft."},
    )
    assert saved.status_code == 200
    assert saved.json()["text"] == "Hello, this is my reviewed project-specific draft."
    controls = client.post(
        "/api/controls",
        headers=headers,
        json={"auto_bidder_enabled": True},
    )
    assert controls.status_code == 200
    assert controls.json()["auto_bidder_enabled"]
    assert controls.json()["monitor_enabled"]
    assert controls.json()["mode"] == "demo" and not controls.json()["live_allowed"]
    assert client.get("/api/history").json()["items"] == []
    assert client.get("/api/dashboard").json()["stats"]["submitted_total"] == 0
    stopped = client.post(
        "/api/controls",
        headers=headers,
        json={"monitor_enabled": False},
    ).json()
    assert not stopped["monitor_enabled"] and not stopped["auto_bidder_enabled"]


def test_settings_exclude_secrets_and_keep_ai_separate_from_prices(client):
    headers = sign_in(client)
    response = client.get("/api/settings")
    assert response.status_code == 200
    before = response.json()
    assert "password_hash" not in response.text and "openai_key" not in response.text
    updated = client.patch(
        "/api/settings",
        headers=headers,
        json={"ai": {"instructions": "Use concise English.", "tone": "friendly"}},
    )
    assert updated.status_code == 200, updated.text
    after = client.get("/api/settings").json()
    assert after["ai"]["instructions"] == "Use concise English."
    assert after["pricing"] == before["pricing"]
    rejected = client.patch(
        "/api/settings",
        headers=headers,
        json={"connection": {"FREELANCER_OAUTH_TOKEN": "sensitive-fixture-value"}},
    )
    assert rejected.status_code in {400, 422}
    assert "sensitive-fixture-value" not in rejected.text


def test_second_web_process_cannot_own_same_database(web_settings):
    with TestClient(create_app(web_settings)):
        with pytest.raises(RuntimeError, match="exactly one worker"):
            with TestClient(create_app(web_settings)):
                pass


def test_auto_bidder_is_off_after_app_restart(web_settings):
    with TestClient(create_app(web_settings)) as first:
        headers = sign_in(first)
        assert first.post(
            "/api/controls",
            headers=headers,
            json={"auto_bidder_enabled": True},
        ).json()["auto_bidder_enabled"]
    with TestClient(create_app(web_settings)) as restarted:
        sign_in(restarted)
        assert not restarted.get("/api/dashboard").json()["auto_bidder_enabled"]


@pytest.mark.parametrize(
    "changes",
    [
        {"admin_password_hash": ""},
        {"allowed_hosts": ("*",)},
        {"allowed_hosts": ("public.example",), "cookie_secure": False},
        {"origin": "https://unknown.example"},
        {"session_ttl_seconds": 1},
    ],
)
def test_web_configuration_fails_closed(web_settings, changes):
    with pytest.raises((ConfigError, ValueError)):
        replace(web_settings, **changes).validate()


def test_non_numeric_session_ttl_environment_rejected(admin_hash):
    with pytest.raises(ConfigError, match="SESSION_TTL"):
        load_web_settings(
            {
                "FREELANCER_ADMIN_PASSWORD_HASH": admin_hash,
                "FREELANCER_WEB_SESSION_TTL": "not-numeric",
            }
        )


def test_login_can_start_without_freelancer_token(web_settings):
    assert not web_settings.engine.token
    with TestClient(create_app(replace(web_settings, demo_mode=False))) as client:
        sign_in(client)
        state = client.get("/api/dashboard").json()
        assert not state["configured"] and state["mode"] == "dry_run"


def test_demo_data_settings_and_activity_never_leak_into_real_workspace(tmp_path, admin_hash):
    common = {
        "engine": Settings(
            database_path=str(tmp_path / "engine.sqlite3"),
            report_path=str(tmp_path / "recommendations.json"),
        ),
        "admin_password_hash": admin_hash,
        "database_path": str(tmp_path / "web.sqlite3"),
        "cookie_secure": False,
        "allowed_hosts": ("testserver",),
        "origin": "http://testserver",
        "start_monitor_thread": False,
    }
    demo_settings = WebSettings(**common, demo_mode=True)
    real_settings = WebSettings(**common, demo_mode=False)
    assert demo_settings.database_path != real_settings.database_path
    assert demo_settings.engine.database_path != real_settings.engine.database_path
    assert demo_settings.engine.report_path != real_settings.engine.report_path
    with TestClient(create_app(demo_settings)) as demo:
        headers = sign_in(demo)
        assert demo.post("/api/search", headers=headers, json={}).status_code == 200
        jobs = demo.get("/api/jobs?decision=BID").json()
        assert jobs["total"] > 0
        project_id = jobs["items"][0]["id"]
        assert (
            demo.put(
                f"/api/jobs/{project_id}/proposal",
                headers=headers,
                json={"text": "Fictional demo draft only."},
            ).status_code
            == 200
        )
        assert (
            demo.patch(
                "/api/settings",
                headers=headers,
                json={"ai": {"instructions": "DEMO ONLY"}},
            ).status_code
            == 200
        )
        assert (
            demo.post(
                "/api/controls",
                headers=headers,
                json={"monitor_enabled": True},
            ).status_code
            == 200
        )
        assert demo.get("/api/dashboard").json()["stats"]["drafts"] == 1
    with TestClient(create_app(real_settings)) as real:
        sign_in(real)
        state = real.get("/api/dashboard").json()
        assert state["mode"] == "dry_run"
        assert not state["monitor_enabled"] and not state["auto_bidder_enabled"]
        assert state["stats"]["jobs_total"] == 0
        assert state["stats"]["drafts"] == 0
        assert all(day["assessed"] == 0 for day in state["chart"])
        assert real.get("/api/jobs").json()["items"] == []
        assert real.get("/api/history").json()["items"] == []
        assert real.get("/api/settings").json()["ai"]["instructions"] == ""
