"""Draft lifecycle through the real server with a fake IMAP connection:
create, update-merge semantics, reply composition (threading headers, quoting)."""

import asyncio
from email import message_from_bytes
from email.policy import default as default_policy

import pytest
from fastmcp import Client

import mcp_proton_email.server as server_module
from mcp_proton_email.server import build_server
from mcp_proton_email.state import AppState

from test_policy import make_config


class DraftFakeClient:
    """Enough IMAP to host a Drafts folder and one INBOX message."""

    def __init__(self):
        self.messages: dict[str, dict[int, bytes]] = {"Drafts": {}, "INBOX": {}}
        self.next_uid = 100
        self.expunged: list[int] = []
        original = (
            b"From: Rail <no-reply@rail.example>\r\nTo: user@example.com\r\nCc: other@x.se\r\n"
            b"Subject: Receipt June\r\nMessage-ID: <orig-1@rail.example>\r\n"
            b"Content-Type: text/plain\r\n\r\nYour receipt: 42.00 EUR train ticket."
        )
        self.messages["INBOX"][1] = original
        self.selected = "INBOX"

    def select_folder(self, folder, readonly=False):
        self.selected = folder

    def fetch(self, uids, fields):
        out = {}
        for uid in uids:
            raw = self.messages[self.selected].get(uid)
            if raw is not None:
                out[uid] = {b"BODY[]": raw, b"FLAGS": ()}
        return out

    def append(self, folder, message, flags=()):
        self.next_uid += 1
        self.messages[folder][self.next_uid] = message
        return f"[APPENDUID 1 {self.next_uid}] ok".encode()

    def delete_messages(self, uids):
        for uid in uids:
            self.messages[self.selected].pop(uid, None)

    def expunge(self, uids=None):
        self.expunged.extend(uids or [])


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DIR", tmp_path / "audit")
    fake_client = DraftFakeClient()

    class FakeConnection:
        username = "user@example.com"

        def run(self, fn):
            return fn(fake_client)

    monkeypatch.setattr(AppState, "connection", lambda self, account=None: FakeConnection())
    return fake_client


def call(config, tool, args):
    async def run():
        async with Client(build_server(config)) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    return asyncio.run(run())


def parse_draft(fake_client, uid):
    return message_from_bytes(fake_client.messages["Drafts"][uid], policy=default_policy)


def test_create_draft_lands_in_drafts(tmp_path, fake):
    result = call(make_config(tmp_path), "create_draft",
                  {"to": ["x@y.se"], "subject": "Plan", "body": "See you Monday."})
    assert not result.is_error, result.content[0].text
    draft = parse_draft(fake, result.data["uid"])
    assert draft["From"] == "user@example.com"
    assert draft["To"] == "x@y.se"
    assert "See you Monday." in draft.get_content()


def test_update_draft_merges_unspecified_fields(tmp_path, fake):
    created = call(make_config(tmp_path), "create_draft",
                   {"to": ["x@y.se"], "subject": "Plan", "body": "Original body."})
    old_uid = created.data["uid"]
    updated = call(make_config(tmp_path), "update_draft",
                   {"uid": old_uid, "subject": "Plan v2"})
    assert not updated.is_error, updated.content[0].text
    draft = parse_draft(fake, updated.data["uid"])
    assert draft["Subject"] == "Plan v2"
    assert draft["To"] == "x@y.se", "unspecified fields must carry over"
    assert "Original body." in draft.get_content()
    assert old_uid in fake.expunged, "old draft version must be expunged"


def test_reply_draft_threads_and_quotes(tmp_path, fake):
    result = call(make_config(tmp_path), "create_reply_draft",
                  {"folder": "INBOX", "uid": 1, "body": "Thanks, received!"})
    assert not result.is_error, result.content[0].text
    draft = parse_draft(fake, result.data["uid"])
    assert draft["To"] == "no-reply@rail.example"
    assert draft["Subject"] == "Re: Receipt June"
    assert draft["In-Reply-To"] == "<orig-1@rail.example>"
    assert "<orig-1@rail.example>" in draft["References"]
    content = draft.get_content()
    assert content.startswith("Thanks, received!")
    assert "> Your receipt: 42.00 EUR" in content


def test_reply_all_excludes_own_address(tmp_path, fake):
    result = call(make_config(tmp_path), "create_reply_draft",
                  {"folder": "INBOX", "uid": 1, "body": "ok", "reply_all": True})
    draft = parse_draft(fake, result.data["uid"])
    assert draft["Cc"] == "other@x.se", "own address must not be CC'd back"


def test_forward_draft_wraps_original(tmp_path, fake):
    result = call(make_config(tmp_path), "create_forward_draft",
                  {"folder": "INBOX", "uid": 1, "to": ["colleague@example.org"],
                   "body": "FYI for the claim."})
    draft = parse_draft(fake, result.data["uid"])
    assert draft["Subject"] == "Fwd: Receipt June"
    content = draft.get_content()
    assert "FYI for the claim." in content
    assert "Forwarded message" in content and "42.00 EUR" in content
