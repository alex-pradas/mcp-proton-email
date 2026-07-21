"""Multi-account support: each Proton account has its own Bridge password (its
own Proton Pass item), its own IMAP connection, and can only send as its own
addresses. Single-account behavior must be unchanged (backward compatible)."""

import os

import pytest
from fastmcp.exceptions import ToolError

from mcp_proton_email.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("PROTONMCP_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PROTONMCP_USERNAMES", "a@x.com,b@y.com")


# -- config: per-account Pass item resolution ---------------------------------


def test_single_account_uses_pass_item_default(monkeypatch):
    monkeypatch.setenv("PROTONMCP_USERNAMES", "solo@x.com")
    cfg = load_config()
    assert cfg.pass_item_for("solo@x.com") == "proton-bridge"


def test_single_account_respects_pass_item_override(monkeypatch):
    monkeypatch.setenv("PROTONMCP_USERNAMES", "solo@x.com")
    monkeypatch.setenv("PROTONMCP_PASS_ITEM", "my-bridge")
    cfg = load_config()
    assert cfg.pass_item_for("solo@x.com") == "my-bridge"


def test_multi_account_defaults_item_to_username():
    # Two accounts, no explicit items -> each account's item title is its username.
    cfg = load_config()
    assert cfg.pass_item_for("a@x.com") == "a@x.com"
    assert cfg.pass_item_for("b@y.com") == "b@y.com"


def test_multi_account_explicit_pass_items(monkeypatch):
    monkeypatch.setenv("PROTONMCP_PASS_ITEMS", "bridge-a,bridge-b")
    cfg = load_config()
    assert cfg.pass_item_for("a@x.com") == "bridge-a"
    assert cfg.pass_item_for("b@y.com") == "bridge-b"


def test_pass_items_length_must_match_usernames(monkeypatch):
    monkeypatch.setenv("PROTONMCP_PASS_ITEMS", "only-one")
    with pytest.raises(ConfigError, match="PASS_ITEMS"):
        load_config()


def test_pass_items_positional_blank_rejected(monkeypatch):
    # A blank entry must NOT be silently dropped — that would misalign each
    # account with the wrong item (and thus the wrong Bridge password).
    monkeypatch.setenv("PROTONMCP_USERNAMES", "a@x.com,b@y.com")
    monkeypatch.setenv("PROTONMCP_PASS_ITEMS", "bridge-a,")
    with pytest.raises(ConfigError, match="PASS_ITEMS"):
        load_config()


def test_pass_item_for_unknown_account_raises():
    cfg = load_config()
    with pytest.raises(KeyError):
        cfg.pass_item_for("stranger@z.com")


# -- state: per-account secret providers & connection routing -----------------


def _make_state(tmp_path):
    from mcp_proton_email.audit import AuditLog
    from mcp_proton_email.state import AppState

    return AppState(config=load_config(), audit=AuditLog(tmp_path / "audit"))


def test_secret_provider_is_per_account_and_cached(tmp_path):
    state = _make_state(tmp_path)
    pa = state.secret_for("a@x.com")
    pb = state.secret_for("b@y.com")
    assert pa is not pb, "each account gets its own SecretProvider"
    assert state.secret_for("a@x.com") is pa, "providers are cached per account"
    # each points at the account's own Pass item
    assert pa._item == "a@x.com" and pb._item == "b@y.com"


def test_secret_for_rejects_unknown_account(tmp_path):
    state = _make_state(tmp_path)
    with pytest.raises(ToolError, match="unknown account"):
        state.secret_for("stranger@z.com")


def test_connection_uses_the_accounts_own_secret(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    ca = state.connection("a@x.com")
    cb = state.connection("b@y.com")
    assert ca is not cb
    assert ca.username == "a@x.com" and cb.username == "b@y.com"
    # the connection must carry the per-account secret, not a shared one
    assert ca._secrets is state.secret_for("a@x.com")
    assert cb._secrets is state.secret_for("b@y.com")


# -- state: From defaults to the selected account's own address ---------------


def test_validate_from_defaults_to_selected_account(tmp_path, monkeypatch):
    # allowlist must contain both account addresses for multi-account sending
    monkeypatch.setenv("PROTONMCP_SEND_FROM", "a@x.com,b@y.com")
    state = _make_state(tmp_path)
    assert state.validate_from(None, "a@x.com") == "a@x.com"
    assert state.validate_from(None, "b@y.com") == "b@y.com"


def test_validate_from_rejects_address_off_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTONMCP_SEND_FROM", "a@x.com")  # only a is allowed
    state = _make_state(tmp_path)
    with pytest.raises(ToolError, match="allowlist"):
        state.validate_from(None, "b@y.com")  # b's default From not allowed
