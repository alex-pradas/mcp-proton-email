"""A2/A4: the send gate end-to-end through the real MCP server.

- A client WITHOUT elicitation support cannot send (refusal, nothing sent).
- A human DECLINE blocks the send (nothing sent).
- A human ACCEPT releases exactly one SMTP send and writes an audit row.
The model never has a path around the elicitation prompt.
"""

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

import mcp_proton_email.server as server_module
import mcp_proton_email.tools_send as tools_send
from mcp_proton_email.server import build_server

from test_policy import make_config

SEND_ARGS = {"to": ["friend@example.org"], "subject": "Hi", "body": "hello from test"}


@pytest.fixture
def sent_messages(monkeypatch):
    """Mock the SMTP layer; record what would have been transmitted."""
    sent: list[dict] = []

    def fake_smtp_send(state, account, msg):
        sent.append({"from": msg["From"], "to": msg["To"], "subject": msg["Subject"]})

    monkeypatch.setattr(tools_send, "_smtp_send", fake_smtp_send)
    return sent


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DIR", tmp_path / "audit")
    return tmp_path / "audit"


def call_send(config, elicitation_handler=None):
    async def run():
        client_kwargs = {}
        if elicitation_handler is not None:
            client_kwargs["elicitation_handler"] = elicitation_handler
        async with Client(build_server(config), **client_kwargs) as client:
            return await client.call_tool("send_email", SEND_ARGS, raise_on_error=False)

    return asyncio.run(run())


def test_no_elicitation_client_refuses(tmp_path, audit_dir, sent_messages):
    result = call_send(make_config(tmp_path, allow_send=True))
    assert result.is_error
    assert "elicitation" in result.content[0].text
    assert sent_messages == [], "nothing may be transmitted without a human (A4)"


def test_human_decline_blocks_send(tmp_path, audit_dir, sent_messages):
    async def decline(message, response_type, params, context):
        return ElicitResult(action="decline")

    result = call_send(make_config(tmp_path, allow_send=True), decline)
    assert result.is_error
    assert "not approved" in result.content[0].text
    assert sent_messages == []


def test_human_accept_releases_send_and_audits(tmp_path, audit_dir, sent_messages):
    prompts: list[str] = []

    async def accept(message, response_type, params, context):
        prompts.append(message)
        return ElicitResult(action="accept", content={})

    result = call_send(make_config(tmp_path, allow_send=True), accept)
    assert not result.is_error, result.content[0].text
    assert result.data["sent"] is True
    assert sent_messages == [
        {"from": "user@example.com", "to": "friend@example.org", "subject": "Hi"}
    ]
    # the human saw what they approved
    assert "friend@example.org" in prompts[0] and "Hi" in prompts[0]
    # and the send is in the audit log
    entries = [json.loads(line) for line in (audit_dir / "audit.log").read_text().splitlines()]
    assert any(e["tool"] == "send_email" and e["status"] == "sent" for e in entries)


def test_send_absent_without_allow_send(tmp_path, audit_dir, sent_messages):
    result = call_send(make_config(tmp_path, allow_send=False))
    assert result.is_error, "send_email must not even exist without ALLOW_SEND (A2)"
    assert sent_messages == []


def test_send_unknown_account_rejected_before_transmit(tmp_path, audit_dir, sent_messages):
    async def accept(message, response_type, params, context):
        return ElicitResult(action="accept", content={})

    async def run():
        async with Client(build_server(make_config(tmp_path, allow_send=True)),
                          elicitation_handler=accept) as client:
            return await client.call_tool(
                "send_email", {**SEND_ARGS, "account": "stranger@example.com"},
                raise_on_error=False,
            )

    result = asyncio.run(run())
    assert result.is_error and "unknown account" in result.content[0].text
    assert sent_messages == [], "an unknown account must never reach SMTP"
