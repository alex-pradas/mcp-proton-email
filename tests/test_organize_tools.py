"""Organize tools through the real server with a fake client: folder
validation, move/trash, label apply/remove via Message-ID, audit trail."""

import asyncio
import json

import pytest
from fastmcp import Client

import mcp_proton_email.server as server_module
from mcp_proton_email.server import build_server
from mcp_proton_email.state import AppState

from test_policy import make_config


class OrganizeFakeClient:
    def __init__(self):
        self.folders = ["INBOX", "Archive", "Trash", "All Mail", "Drafts",
                        "Folders/Projects", "Labels/todo"]
        self.moves: list[tuple] = []
        self.copies: list[tuple] = []
        self.flag_ops: list[tuple] = []
        self.deleted: list[int] = []
        self.expunged: list[int] = []
        self.selected = "INBOX"
        self.raw = (
            b"From: SJ <no-reply@sj.se>\r\nSubject: Kvitto\r\n"
            b"Message-ID: <orig-1@sj.se>\r\nContent-Type: text/plain\r\n\r\nbody"
        )

    def list_folders(self):
        return [((), b"/", name) for name in self.folders]

    def select_folder(self, folder, readonly=False):
        self.selected = folder
        self.readonly = readonly

    def fetch(self, uids, fields):
        return {uid: {b"BODY[]": self.raw, b"FLAGS": ()} for uid in uids}

    def move(self, uids, target):
        if self.readonly:  # Bridge rejects MOVE from a read-only selection
            raise RuntimeError("move failed: the mailbox is read-only")
        self.moves.append((self.selected, tuple(uids), target))

    def copy(self, uids, target):
        if self.readonly:  # Bridge rejects COPY from a read-only selection
            raise RuntimeError("copy failed: the mailbox is read-only")
        self.copies.append((self.selected, tuple(uids), target))

    def search(self, criteria):
        return [42] if "Message-ID" in criteria else []

    def add_flags(self, uids, flags):
        self.flag_ops.append(("add", tuple(uids), tuple(flags)))

    def remove_flags(self, uids, flags):
        self.flag_ops.append(("remove", tuple(uids), tuple(flags)))

    def delete_messages(self, uids):
        self.deleted.extend(uids)

    def expunge(self, uids=None):
        self.expunged.extend(uids or [])

    def create_folder(self, name):
        self.folders.append(name)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DIR", tmp_path / "audit")
    fake_client = OrganizeFakeClient()

    class FakeConnection:
        username = "user@example.com"

        def run(self, fn):
            return fn(fake_client)

    monkeypatch.setattr(AppState, "connection", lambda self, account=None: FakeConnection())
    return fake_client


def call(tmp_path, tool, args):
    async def run():
        async with Client(build_server(make_config(tmp_path))) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    return asyncio.run(run())


def test_move_to_existing_folder(tmp_path, fake):
    result = call(tmp_path, "move_message",
                  {"folder": "INBOX", "uid": 7, "target_folder": "Folders/Projects"})
    assert not result.is_error
    assert fake.moves == [("INBOX", (7,), "Folders/Projects")]


def test_move_to_missing_folder_refused(tmp_path, fake):
    result = call(tmp_path, "move_message",
                  {"folder": "INBOX", "uid": 7, "target_folder": "Nope"})
    assert result.is_error and "does not exist" in result.content[0].text
    assert fake.moves == []


def test_trash_and_archive(tmp_path, fake):
    call(tmp_path, "move_to_trash", {"folder": "INBOX", "uid": 1})
    call(tmp_path, "archive_message", {"folder": "INBOX", "uid": 2})
    assert ("INBOX", (1,), "Trash") in fake.moves
    assert ("INBOX", (2,), "Archive") in fake.moves


def test_add_label_copies(tmp_path, fake):
    result = call(tmp_path, "add_label", {"folder": "INBOX", "uid": 5, "label": "todo"})
    assert not result.is_error
    assert fake.copies == [("INBOX", (5,), "Labels/todo")]


def test_add_missing_label_refused(tmp_path, fake):
    result = call(tmp_path, "add_label", {"folder": "INBOX", "uid": 5, "label": "ghost"})
    assert result.is_error and "does not exist" in result.content[0].text


def test_remove_label_matches_by_message_id(tmp_path, fake):
    result = call(tmp_path, "remove_label", {"folder": "INBOX", "uid": 5, "label": "todo"})
    assert not result.is_error and result.data["removed"] is True
    assert fake.deleted == [42] and fake.expunged == [42]
    assert fake.selected == "Labels/todo"


def test_flags_and_audit(tmp_path, fake):
    call(tmp_path, "mark_read", {"folder": "INBOX", "uid": 3})
    call(tmp_path, "star_message", {"folder": "INBOX", "uid": 3})
    assert ("add", (3,), (b"\\Seen",)) in fake.flag_ops
    assert ("add", (3,), (b"\\Flagged",)) in fake.flag_ops
    entries = [json.loads(l) for l in (tmp_path / "audit" / "audit.log").read_text().splitlines()]
    assert {e["tool"] for e in entries} >= {"mark_read", "star_message"}


def test_create_folder_namespaced_and_validated(tmp_path, fake):
    result = call(tmp_path, "create_folder", {"name": "Receipts 2026"})
    assert not result.is_error and "Folders/Receipts 2026" in fake.folders
    bad = call(tmp_path, "create_folder", {"name": "evil/nested"})
    assert bad.is_error and "may not contain" in bad.content[0].text
