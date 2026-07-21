"""Mutating-but-reversible tools: drafts, organize, attachment save.

All are disabled together by PROTONMCP_READ_ONLY=true and every action is
audited. There is deliberately no hard-delete tool (spec 3).
"""

import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .compose import (
    build_message,
    forward_subject,
    quote_body,
    reply_subject,
    validate_addresses,
)
from .errors import tool_guard
from .fetch import ARCHIVE, DRAFTS, TRASH, fetch_full, require_folder
from .mailmsg import body_text, get_attachment_payload
from .sanitize import resolve_in_root
from .state import AppState

WRITE_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

_APPENDUID_RE = re.compile(rb"APPENDUID \d+ (\d+)")


def _append_draft(client, msg) -> int:
    response = client.append(DRAFTS, bytes(msg), flags=(b"\\Draft", b"\\Seen"))
    match = _APPENDUID_RE.search(response if isinstance(response, bytes) else bytes(str(response), "ascii"))
    if not match:
        raise ToolError("draft stored but Bridge returned no APPENDUID; check the Drafts folder")
    return int(match.group(1))


def register_write_tools(mcp: FastMCP, state: AppState) -> None:
    # -- drafts ---------------------------------------------------------------

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def create_draft(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft in Proton's Drafts folder (visible/editable in Proton apps).

        Nothing is sent. Returns {folder, uid} of the stored draft.
        """
        state.require_mutation("create_draft")
        sender = state.config.send_from[0]
        msg = build_message(
            sender,
            validate_addresses(to, "to"),
            subject,
            body,
            cc=validate_addresses(cc, "cc"),
            bcc=validate_addresses(bcc, "bcc"),
        )
        uid = state.connection(account).run(lambda c: _append_draft(c, msg))
        state.audit.record(
            "create_draft", "ok", account=account or state.config.primary_username,
            to=to, cc=cc, bcc=bcc, subject=subject, uid=uid,
        )
        return {"folder": DRAFTS, "uid": uid}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def update_draft(
        uid: int,
        to: list[str] | None = None,
        subject: str | None = None,
        body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Replace fields of an existing draft (by Drafts-folder uid).

        Implemented as append-new + expunge-old, so the returned uid changes.
        """
        state.require_mutation("update_draft")

        def run(client):
            old = fetch_full(client, DRAFTS, uid)
            old_body, _ = body_text(old, state.config.max_body_chars)
            msg = build_message(
                state.config.send_from[0],
                validate_addresses(to, "to") if to is not None else validate_addresses(str(old.get("To", "")), "to"),
                subject if subject is not None else str(old.get("Subject", "")),
                body if body is not None else old_body,
                cc=validate_addresses(cc, "cc") if cc is not None else validate_addresses(str(old.get("Cc", "")) or None, "cc"),
                bcc=validate_addresses(bcc, "bcc") if bcc is not None else validate_addresses(str(old.get("Bcc", "")) or None, "bcc"),
            )
            new_uid = _append_draft(client, msg)
            client.select_folder(DRAFTS)
            client.delete_messages([uid])
            client.expunge([uid])
            return new_uid

        new_uid = state.connection(account).run(run)
        state.audit.record(
            "update_draft", "ok", account=account or state.config.primary_username,
            old_uid=uid, uid=new_uid, subject=subject,
        )
        return {"folder": DRAFTS, "uid": new_uid}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def create_reply_draft(
        folder: str,
        uid: int,
        body: str,
        reply_all: bool = False,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Draft a reply to a message, quoting the original. Nothing is sent."""
        state.require_mutation("create_reply_draft")

        def run(client):
            original = fetch_full(client, folder, uid)
            orig_text, _ = body_text(original, 3000)
            to = validate_addresses(str(original.get("Reply-To") or original.get("From", "")), "to")
            cc: list[str] = []
            if reply_all:
                own = set(state.config.send_from) | {state.config.primary_username.lower()}
                extra = validate_addresses(str(original.get("To", "")), "to") + validate_addresses(
                    str(original.get("Cc", "")) or None, "cc"
                )
                cc = [a for a in extra if a.lower() not in own and a not in to]
            msg_id = str(original.get("Message-ID", "")).strip()
            references = (str(original.get("References", "")) + " " + msg_id).strip()
            msg = build_message(
                state.config.send_from[0],
                to,
                reply_subject(str(original.get("Subject", ""))),
                f"{body}\n\n{quote_body(orig_text)}",
                cc=cc,
                in_reply_to=msg_id or None,
                references=references or None,
            )
            return _append_draft(client, msg), to

        new_uid, to = state.connection(account).run(run)
        state.audit.record(
            "create_reply_draft", "ok", account=account or state.config.primary_username,
            source=f"{folder}/{uid}", to=to, uid=new_uid, reply_all=reply_all,
        )
        return {"folder": DRAFTS, "uid": new_uid}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def create_forward_draft(
        folder: str,
        uid: int,
        to: list[str],
        body: str = "",
        account: str | None = None,
    ) -> dict[str, Any]:
        """Draft a forward of a message (plain text; attachments are NOT carried
        over in v1 — mention that to the user if they matter). Nothing is sent."""
        state.require_mutation("create_forward_draft")

        def run(client):
            original = fetch_full(client, folder, uid)
            orig_text, _ = body_text(original, state.config.max_body_chars)
            forwarded = (
                f"{body}\n\n---------- Forwarded message ----------\n"
                f"From: {original.get('From', '')}\nDate: {original.get('Date', '')}\n"
                f"Subject: {original.get('Subject', '')}\nTo: {original.get('To', '')}\n\n{orig_text}"
            )
            msg = build_message(
                state.config.send_from[0],
                validate_addresses(to, "to"),
                forward_subject(str(original.get("Subject", ""))),
                forwarded,
            )
            return _append_draft(client, msg)

        new_uid = state.connection(account).run(run)
        state.audit.record(
            "create_forward_draft", "ok", account=account or state.config.primary_username,
            source=f"{folder}/{uid}", to=to, uid=new_uid,
        )
        return {"folder": DRAFTS, "uid": new_uid}

    # -- organize -------------------------------------------------------------

    def _move(tool: str, folder: str, uid: int, target: str, account: str | None) -> dict[str, Any]:
        state.require_mutation(tool)

        def run(client):
            require_folder(client, target)
            require_folder(client, folder)
            client.select_folder(folder)
            client.move([uid], target)

        state.connection(account).run(run)
        state.audit.record(tool, "ok", account=account or state.config.primary_username,
                           source=f"{folder}/{uid}", target=target)
        return {"moved": f"{folder}/{uid}", "to": target}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def move_message(folder: str, uid: int, target_folder: str, account: str | None = None) -> dict[str, Any]:
        """Move a message to another folder (reversible)."""
        return _move("move_message", folder, uid, target_folder, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def archive_message(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Move a message to Archive (reversible)."""
        return _move("archive_message", folder, uid, ARCHIVE, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def move_to_trash(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Move a message to Trash (reversible — permanent deletion only via Proton UI)."""
        return _move("move_to_trash", folder, uid, TRASH, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def add_label(folder: str, uid: int, label: str, account: str | None = None) -> dict[str, Any]:
        """Apply a Proton label to a message (label must exist; see list_folders)."""
        state.require_mutation("add_label")
        target = f"Labels/{label}"

        def run(client):
            require_folder(client, target)
            # COPY must run from a writable selection — Bridge rejects it from a
            # read-only mailbox. COPY does not modify the source (non-destructive).
            client.select_folder(folder)
            client.copy([uid], target)

        state.connection(account).run(run)
        state.audit.record("add_label", "ok", account=account or state.config.primary_username,
                           source=f"{folder}/{uid}", label=label)
        return {"labeled": f"{folder}/{uid}", "label": label}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def remove_label(folder: str, uid: int, label: str, account: str | None = None) -> dict[str, Any]:
        """Remove a Proton label from a message (the message itself is untouched)."""
        state.require_mutation("remove_label")
        target = f"Labels/{label}"

        def run(client):
            msg = fetch_full(client, folder, uid)
            msg_id = str(msg.get("Message-ID", "")).strip()
            if not msg_id:
                raise ToolError("message has no Message-ID; cannot match it in the label folder")
            require_folder(client, target)
            client.select_folder(target)
            uids = client.search(["HEADER", "Message-ID", msg_id])
            if not uids:
                return False
            client.delete_messages(uids)
            client.expunge(uids)
            return True

        removed = state.connection(account).run(run)
        state.audit.record("remove_label", "ok" if removed else "noop",
                           account=account or state.config.primary_username,
                           source=f"{folder}/{uid}", label=label)
        return {"removed": removed, "label": label}

    def _set_flag(tool: str, folder: str, uid: int, flag: bytes, add: bool, account: str | None) -> dict[str, Any]:
        state.require_mutation(tool)

        def run(client):
            client.select_folder(folder)
            if add:
                client.add_flags([uid], [flag])
            else:
                client.remove_flags([uid], [flag])

        state.connection(account).run(run)
        state.audit.record(tool, "ok", account=account or state.config.primary_username,
                           source=f"{folder}/{uid}")
        return {"message": f"{folder}/{uid}", "tool": tool, "done": True}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def mark_read(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Mark a message as read."""
        return _set_flag("mark_read", folder, uid, b"\\Seen", True, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def mark_unread(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Mark a message as unread."""
        return _set_flag("mark_unread", folder, uid, b"\\Seen", False, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def star_message(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Star a message."""
        return _set_flag("star_message", folder, uid, b"\\Flagged", True, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def unstar_message(folder: str, uid: int, account: str | None = None) -> dict[str, Any]:
        """Unstar a message."""
        return _set_flag("unstar_message", folder, uid, b"\\Flagged", False, account)

    def _create(tool: str, prefix: str, name: str, account: str | None) -> dict[str, Any]:
        state.require_mutation(tool)
        if "/" in name or "\r" in name or "\n" in name:
            raise ToolError(f"{tool}: name may not contain '/' or newlines")
        target = f"{prefix}/{name}"
        state.connection(account).run(lambda c: c.create_folder(target))
        state.audit.record(tool, "ok", account=account or state.config.primary_username, target=target)
        return {"created": target}

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def create_folder(name: str, account: str | None = None) -> dict[str, Any]:
        """Create a Proton folder (appears as Folders/<name>)."""
        return _create("create_folder", "Folders", name, account)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def create_label(name: str, account: str | None = None) -> dict[str, Any]:
        """Create a Proton label (appears as Labels/<name>)."""
        return _create("create_label", "Labels", name, account)

    # -- attachment save (the expense-workflow primitive) ----------------------

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    @tool_guard
    def save_attachment(
        folder: str,
        uid: int,
        attachment_index: int,
        filename: str | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Save one attachment into the allowlisted download directory.

        Writes ONLY inside PROTONMCP_ATTACHMENT_DOWNLOAD_DIR; filenames are
        sanitized and existing files are never overwritten. Every save is audited.
        """
        state.require_mutation("save_attachment")
        msg = state.connection(account).run(lambda c: fetch_full(c, folder, uid))
        info, payload = get_attachment_payload(msg, attachment_index)
        target = resolve_in_root(state.config.attachment_dir, filename or info.filename)
        target.write_bytes(payload)
        target.chmod(0o600)
        state.audit.record(
            "save_attachment", "ok", account=account or state.config.primary_username,
            source=f"{folder}/{uid}", saved_path=str(target), size_bytes=len(payload),
        )
        return {"saved_path": str(target), "size_bytes": len(payload), "content_type": info.content_type}
