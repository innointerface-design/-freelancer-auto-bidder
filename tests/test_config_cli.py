import logging

import pytest

from freelancer_auto_bidder.cli import _Redact, main
from freelancer_auto_bidder.config import ConfigError, load_settings

ENV = {"FREELANCER_OAUTH_TOKEN": "test-only-token"}


def test_dry_run_defaults_and_secret_repr():
    settings = load_settings(env=ENV)
    assert settings.dry_run and not settings.live_enabled
    assert "test-only-token" not in repr(settings)


@pytest.mark.parametrize(
    "extra",
    [
        {"FREELANCER_DRY_RUN": "false"},
        {"FREELANCER_DRY_RUN": "false", "FREELANCER_ENABLE_AUTO_BIDDING": "true"},
        {
            "FREELANCER_DRY_RUN": "false",
            "FREELANCER_ENABLE_AUTO_BIDDING": "true",
            "FREELANCER_AUTO_BID_PERMISSION_VERIFIED": "true",
        },
        {"FREELANCER_DRY_RUN": "off"},
        {"FREELANCER_ENVIRONMENT": "https://example.com"},
    ],
)
def test_unsafe_or_invalid_environment_rejected(extra):
    with pytest.raises(ConfigError):
        load_settings(env=ENV | extra)


def test_four_live_gates_required_and_explicit():
    settings = load_settings(
        env=ENV
        | {
            "FREELANCER_DRY_RUN": "false",
            "FREELANCER_ENABLE_AUTO_BIDDING": "true",
            "FREELANCER_AUTO_BID_PERMISSION_VERIFIED": "true",
            "FREELANCER_PERMISSION_REFERENCE": "verified application approval reference",
        }
    )
    assert settings.live_enabled


@pytest.mark.parametrize(
    "content",
    [
        "poll_interval_seconds = 60",
        "score_threshold = 101",
        "token = 'secret'",
        "dry_run = false",
        "search_queries = []",
        "unknown = true",
        "require_verified_payment = 0",
        "require_known_country = 'false'",
        "database_path = ''",
        "report_path = false",
        "preferred_countries = ['usa']",
        "database_path = 'same'\nreport_path = 'same'",
        "budgets = 5",
        "[budgets.USD]\nmin_budget='NaN'\nbid_amount='10'\nmax_bid='100'",
    ],
)
def test_invalid_toml_config_rejected(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_settings(path, env=ENV)


def test_missing_token_rejected():
    with pytest.raises(ConfigError, match="OAUTH_TOKEN"):
        load_settings(env={})


def test_multiline_token_rejected():
    with pytest.raises(ConfigError):
        load_settings(env={"FREELANCER_OAUTH_TOKEN": "invalid\nheader"})


def test_check_command_does_not_call_network(monkeypatch, capsys):
    monkeypatch.setenv("FREELANCER_OAUTH_TOKEN", "test-only-token")
    monkeypatch.setattr(
        "freelancer_auto_bidder.cli.FreelancerAPI", lambda _: pytest.fail("check must be offline")
    )
    assert main(["check"]) == 0
    assert "NOT been verified" in capsys.readouterr().out


def test_log_redaction():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "problem %s", ("token-value",), None)
    assert _Redact("token-value").filter(record)
    assert record.getMessage() == "problem [REDACTED]"
