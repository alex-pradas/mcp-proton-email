"""A5: non-loopback hosts refuse to start; caps are enforced."""

import pytest

from mcp_proton_email.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("PROTONMCP_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PROTONMCP_USERNAMES", "user@example.com")


def test_defaults_load():
    cfg = load_config()
    assert cfg.imap_host == "127.0.0.1"
    assert cfg.allow_send is False
    assert cfg.read_only is False
    assert cfg.send_from == ("user@example.com",)
    assert cfg.primary_username == "user@example.com"


def test_username_required(monkeypatch):
    monkeypatch.delenv("PROTONMCP_USERNAMES")
    with pytest.raises(ConfigError, match="USERNAMES"):
        load_config()


@pytest.mark.parametrize("host", ["192.168.50.2", "evil.example.com", "0.0.0.0"])
def test_non_loopback_refused(monkeypatch, host):
    monkeypatch.setenv("PROTONMCP_IMAP_HOST", host)
    with pytest.raises(ConfigError, match="loopback"):
        load_config()


def test_non_loopback_override(monkeypatch):
    monkeypatch.setenv("PROTONMCP_SMTP_HOST", "192.168.50.2")
    monkeypatch.setenv("PROTONMCP_ALLOW_NON_LOOPBACK", "true")
    assert load_config().smtp_host == "192.168.50.2"


def test_localhost_is_loopback(monkeypatch):
    monkeypatch.setenv("PROTONMCP_IMAP_HOST", "localhost")
    assert load_config().imap_host == "localhost"


def test_caps_enforced(monkeypatch):
    monkeypatch.setenv("PROTONMCP_MAX_RESULTS", "10000")
    monkeypatch.setenv("PROTONMCP_MAX_BODY_CHARS", "999999999")
    cfg = load_config()
    assert cfg.max_results == 200
    assert cfg.max_body_chars == 200_000
