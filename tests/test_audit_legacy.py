"""Regression coverage for malformed credentials and optional API numbers."""

import warnings

import pytest

from freelancer_auto_bidder.cli import main
from freelancer_auto_bidder.config import ConfigError, load_settings
from freelancer_auto_bidder.models import parse_project
from freelancer_auto_bidder.web import cli as web_cli


@pytest.mark.parametrize(
    "token", ["synthetic-token-\u2603", "synthetic token", "synthetic\ttoken", "synthetic\x00token"]
)
def test_rejects_tokens_that_cannot_be_sent_in_http_headers(token):
    with pytest.raises(ConfigError, match="OAuth token") as error:
        load_settings(env={"FREELANCER_OAUTH_TOKEN": token})
    assert token not in str(error.value)


def test_check_reports_invalid_header_token_without_traceback_or_network(monkeypatch, capsys):
    token = "synthetic-token-\u2603"
    monkeypatch.setenv("FREELANCER_OAUTH_TOKEN", token)
    monkeypatch.setattr(
        "freelancer_auto_bidder.cli.FreelancerAPI",
        lambda *args, **kwargs: pytest.fail("Configuration check must remain offline"),
    )
    assert main(["check"]) == 2
    output = capsys.readouterr().out
    assert "Configuration error" in output
    assert token not in output


def test_overflowing_optional_api_numbers_remain_unknown_instead_of_aborting_scan():
    project = parse_project(
        {"id": 1, "owner_id": 2, "time_submitted": 10**400},
        {"2": {"employer_reputation": {"entire_history": {"overall": 10**400}}}},
    )
    assert project.time_submitted is None
    assert project.client_rating is None


@pytest.mark.parametrize("failure", ["warning", "eof", "interrupt"])
@pytest.mark.parametrize("fail_at", [1, 2])
def test_admin_hash_utility_refuses_visible_input_fallback_and_cancellation(
    monkeypatch, capsys, failure, fail_at
):
    secret = "Synthetic-private-password-123!"
    calls = 0

    def unavailable(*args):
        nonlocal calls
        calls += 1
        if calls != fail_at:
            return secret
        if failure == "warning":
            warnings.warn("Password may be echoed", web_cli.getpass.GetPassWarning)
            pytest.fail("Warning must stop before reading echoed stdin")
        if failure == "eof":
            raise EOFError
        raise KeyboardInterrupt

    monkeypatch.setattr(web_cli.getpass, "getpass", unavailable)
    monkeypatch.setattr(
        web_cli, "hash_password", lambda *args: pytest.fail("Must not hash incomplete input")
    )
    assert web_cli.main(["hash-password"]) == 2
    assert calls == fail_at
    output = capsys.readouterr().out
    assert "no hash was generated" in output
    assert secret not in output
