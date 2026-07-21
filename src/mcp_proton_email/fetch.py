"""Shared IMAP fetch helpers used by the tool modules."""

from email.message import EmailMessage
from typing import Any

from fastmcp.exceptions import ToolError
from imapclient import IMAPClient

from .mailmsg import parse_message

ALL_MAIL = "All Mail"
DRAFTS = "Drafts"
TRASH = "Trash"
ARCHIVE = "Archive"

_HEADER_FIELDS = b"BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID IN-REPLY-TO REFERENCES)]"


def _fetch_value(data: dict[bytes, Any], prefix: bytes) -> Any:
    for key, value in data.items():
        if key.upper().startswith(prefix.upper()):
            return value
    return None


def fetch_summaries(client: IMAPClient, folder: str, uids: list[int]) -> list[dict[str, Any]]:
    """Header-level summaries (no bodies) for a list of UIDs in the selected folder."""
    if not uids:
        return []
    response = client.fetch(uids, [_HEADER_FIELDS, b"FLAGS", b"RFC822.SIZE", b"INTERNALDATE"])
    summaries = []
    for uid in uids:
        data = response.get(uid)
        if data is None:
            continue
        raw_headers = _fetch_value(data, b"BODY[HEADER.FIELDS") or b""
        msg = parse_message(raw_headers)
        flags = data.get(b"FLAGS", ())
        summaries.append(
            {
                "folder": folder,
                "uid": uid,
                "subject": str(msg.get("Subject", "")),
                "from": str(msg.get("From", "")),
                "to": str(msg.get("To", "")),
                "date": str(msg.get("Date", "")),
                "message_id": str(msg.get("Message-ID", "")).strip() or None,
                "unread": b"\\Seen" not in flags,
                "starred": b"\\Flagged" in flags,
                "size_bytes": data.get(b"RFC822.SIZE"),
            }
        )
    return summaries


def fetch_full(client: IMAPClient, folder: str, uid: int) -> EmailMessage:
    client.select_folder(folder, readonly=True)
    response = client.fetch([uid], [b"BODY.PEEK[]"])
    data = response.get(uid)
    raw = _fetch_value(data, b"BODY[]") if data else None
    if not raw:
        raise ToolError(f"message uid={uid} not found in folder {folder!r}")
    return parse_message(raw)


def folder_names(client: IMAPClient) -> list[str]:
    return [name for _flags, _delim, name in client.list_folders()]


def require_folder(client: IMAPClient, folder: str) -> str:
    names = folder_names(client)
    if folder not in names:
        raise ToolError(f"folder {folder!r} does not exist; available: {', '.join(sorted(names))}")
    return folder
