"""A2/A11 + spec 6.2: capability gating is structural (unregistered) AND call-time."""

import asyncio
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from mcp_proton_email.audit import AuditLog
from mcp_proton_email.config import Config
from mcp_proton_email.server import build_server
from mcp_proton_email.state import AppState

SEND_TOOLS = {"send_email", "reply", "reply_all", "forward"}
WRITE_TOOLS = {"create_draft", "move_message", "save_attachment", "add_label", "mark_read"}
READ_TOOLS = {"search_messages", "get_message", "list_folders", "get_attachment_text"}


def make_config(tmp_path: Path, allow_send: bool = False, read_only: bool = False) -> Config:
    return Config(
        imap_host="127.0.0.1", imap_port=1143, smtp_host="127.0.0.1", smtp_port=1025,
        usernames=("user@example.com",), pass_vault="Agent", pass_item="proton-bridge", pass_items=None,
        send_from=("user@example.com",), allow_send=allow_send, read_only=read_only,
        attachment_dir=tmp_path, allow_non_loopback=False, tls_ca_file=None,
        max_results=50, max_body_chars=50_000, max_attachment_chars=20_000,
    )


def registered_tools(config: Config) -> set[str]:
    server = build_server(config)
    return {tool.name for tool in asyncio.run(server.list_tools())}


def make_state(tmp_path: Path, **kwargs) -> AppState:
    config = make_config(tmp_path, **kwargs)
    return AppState(config=config, audit=AuditLog(tmp_path / "audit"))


def test_default_registration_no_send(tmp_path):
    tools = registered_tools(make_config(tmp_path))
    assert READ_TOOLS <= tools and WRITE_TOOLS <= tools
    assert not (SEND_TOOLS & tools), "send tools must not exist without ALLOW_SEND (A2)"


def test_allow_send_registers_send(tmp_path):
    tools = registered_tools(make_config(tmp_path, allow_send=True))
    assert SEND_TOOLS <= tools


def test_read_only_strips_all_mutation(tmp_path):
    tools = registered_tools(make_config(tmp_path, read_only=True, allow_send=True))
    assert READ_TOOLS <= tools
    assert not (WRITE_TOOLS & tools) and not (SEND_TOOLS & tools), (
        "READ_ONLY must disable drafts, organize, send and attachment-save together (A11)"
    )


def test_call_time_refusal_read_only(tmp_path):
    state = make_state(tmp_path, read_only=True)
    with pytest.raises(ToolError, match="READ_ONLY"):
        state.require_mutation("create_draft")


def test_call_time_refusal_send_disabled(tmp_path):
    state = make_state(tmp_path)
    with pytest.raises(ToolError, match="ALLOW_SEND"):
        state.require_send("send_email")


def test_from_allowlist_rejects_third_party(tmp_path):
    state = make_state(tmp_path, allow_send=True)
    with pytest.raises(ToolError, match="allowlist"):
        state.validate_from("attacker@evil.com")
    with pytest.raises(ToolError, match="allowlist"):
        state.validate_from("other-alias@example.com")
    assert state.validate_from(None) == "user@example.com"
    assert state.validate_from("USER@example.com") == "user@example.com"


def test_unknown_account_refused(tmp_path):
    state = make_state(tmp_path)
    with pytest.raises(ToolError, match="unknown account"):
        state.connection("stranger@example.com")
