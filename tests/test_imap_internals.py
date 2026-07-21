"""Search-criteria construction, summary fetching, and draft APPEND internals
against a fake IMAP client — no Bridge needed."""

from datetime import date
from email.message import EmailMessage

import pytest
from fastmcp.exceptions import ToolError

from mcp_proton_email.imap import build_search_criteria, imap_search
from mcp_proton_email.fetch import fetch_summaries, fetch_full
from mcp_proton_email.tools_write import _APPENDUID_RE, _append_draft


# -- search criteria ------------------------------------------------------------


def test_criteria_default_all():
    assert build_search_criteria() == ["ALL"]


def test_criteria_combined():
    criteria = build_search_criteria(
        from_addr="sj.se", subject="receipt", since="2026-06-01", unseen_only=True
    )
    assert criteria == ["FROM", "sj.se", "SUBJECT", "receipt",
                        "SINCE", date(2026, 6, 1), "UNSEEN"]


def test_criteria_rejects_bad_date():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        build_search_criteria(since="June 1st")


def test_criteria_passes_strings_structurally():
    # hostile search terms stay data — the library quotes them, we never
    # build raw command strings
    criteria = build_search_criteria(subject='") DELETE OR (SUBJECT "')
    assert criteria[1] == '") DELETE OR (SUBJECT "'


class _SearchRecorder:
    def __init__(self):
        self.calls = []

    def search(self, criteria, charset=None):
        self.calls.append((list(criteria), charset))
        return []


def test_search_uses_utf8_only_for_non_ascii_terms():
    rec = _SearchRecorder()
    imap_search(rec, ["SUBJECT", "hello"])          # ascii -> default path
    imap_search(rec, ["SUBJECT", "Kvitto för José"])  # non-ascii -> UTF-8
    imap_search(rec, ["FROM", "José <j@x.se>"])       # non-ascii -> UTF-8
    assert rec.calls[0][1] is None
    assert rec.calls[1][1] == "UTF-8"
    assert rec.calls[2][1] == "UTF-8"


# -- fake client ---------------------------------------------------------------


def raw_headers(subject: str, msg_id: str) -> bytes:
    return (
        f"Subject: {subject}\r\nFrom: SJ <no-reply@sj.se>\r\nTo: user@example.com\r\n"
        f"Date: Mon, 01 Jun 2026 10:00:00 +0200\r\nMessage-ID: {msg_id}\r\n\r\n"
    ).encode()


class FakeClient:
    def __init__(self):
        full = EmailMessage()
        full["From"] = "SJ <no-reply@sj.se>"
        full["Subject"] = "Kvitto"
        full.set_content("receipt body")
        self._full = bytes(full)
        self.appended: list[tuple] = []
        self.selected: str | None = None

    def select_folder(self, folder, readonly=False):
        self.selected = folder

    def fetch(self, uids, fields):
        out = {}
        for uid in uids:
            out[uid] = {
                b"BODY[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID IN-REPLY-TO REFERENCES)]":
                    raw_headers(f"Kvitto {uid}", f"<m{uid}@sj.se>"),
                b"BODY[]": self._full,
                b"FLAGS": (b"\\Seen",) if uid % 2 == 0 else (),
                b"RFC822.SIZE": 1000 + uid,
            }
        return out

    def append(self, folder, message, flags=()):
        self.appended.append((folder, message, flags))
        return b"[APPENDUID 1719260401 4711] (Success)"


def test_fetch_summaries_parses_headers_and_flags():
    client = FakeClient()
    summaries = fetch_summaries(client, "INBOX", [3, 4])
    assert [s["subject"] for s in summaries] == ["Kvitto 3", "Kvitto 4"]
    assert summaries[0]["unread"] is True and summaries[1]["unread"] is False
    assert summaries[0]["message_id"] == "<m3@sj.se>"
    assert summaries[0]["from"] == "SJ <no-reply@sj.se>"
    assert summaries[1]["size_bytes"] == 1004


def test_fetch_summaries_empty():
    assert fetch_summaries(FakeClient(), "INBOX", []) == []


def test_fetch_full_missing_uid_is_tool_error():
    class Empty(FakeClient):
        def fetch(self, uids, fields):
            return {}

    with pytest.raises(ToolError, match="not found"):
        fetch_full(Empty(), "INBOX", 99)


def test_append_draft_parses_appenduid():
    client = FakeClient()
    msg = EmailMessage()
    msg["From"] = "user@example.com"
    msg["Subject"] = "draft"
    msg.set_content("x")
    uid = _append_draft(client, msg)
    assert uid == 4711
    folder, _payload, flags = client.appended[0]
    assert folder == "Drafts" and b"\\Draft" in flags


def test_appenduid_regex_variants():
    assert _APPENDUID_RE.search(b"[APPENDUID 5 123] APPEND completed").group(1) == b"123"
    assert _APPENDUID_RE.search(b"no uid here") is None
