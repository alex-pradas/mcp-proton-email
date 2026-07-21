"""Send tools — OFF until PROTONMCP_ALLOW_SEND=true, and every real send is
gated by MCP elicitation: the client application renders an approve/decline
prompt to the human. The model cannot answer it. On clients that do not
support elicitation, send tools refuse — there is NO fallback confirmation
path by design (spec 6.4).
"""

import smtplib
from email.message import EmailMessage
from typing import Any

import anyio
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation

from .compose import (
    build_message,
    forward_subject,
    quote_body,
    reply_subject,
    validate_addresses,
)
from .errors import tool_guard_async
from .fetch import fetch_full
from .imap import bridge_ssl_context
from .mailmsg import body_text
from .state import AppState

SEND_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}

_NO_ELICITATION = (
    "send refused: this MCP client does not support elicitation, so a human "
    "cannot approve the send. Create a draft instead and send it from the "
    "Proton app, or use a client with elicitation support (spec 6.4)."
)


def _client_supports_elicitation(ctx: Context) -> bool:
    try:
        capabilities = ctx.session.client_params.capabilities
        return getattr(capabilities, "elicitation", None) is not None
    except Exception:
        return False


async def _confirm_with_human(ctx: Context, msg: EmailMessage, tool: str) -> None:
    if not _client_supports_elicitation(ctx):
        raise ToolError(_NO_ELICITATION)
    preview_body, _ = body_text(msg, 400)
    summary = (
        f"Approve sending this email? (tool: {tool})\n"
        f"From: {msg['From']}\nTo: {msg.get('To', '')}\n"
        + (f"Cc: {msg['Cc']}\n" if msg.get("Cc") else "")
        + (f"Bcc: {msg['Bcc']}\n" if msg.get("Bcc") else "")
        + f"Subject: {msg.get('Subject', '')}\n---\n{preview_body}"
    )
    result = await ctx.elicit(summary, response_type=None)
    if not isinstance(result, AcceptedElicitation):
        raise ToolError("send not approved by the human — nothing was sent.")


def _smtp_send(state: AppState, account: str | None, msg: EmailMessage) -> None:
    username = account or state.config.primary_username
    with smtplib.SMTP(state.config.smtp_host, state.config.smtp_port, timeout=60) as smtp:
        smtp.starttls(context=bridge_ssl_context(state.config.smtp_host, state.config.tls_ca_file))
        smtp.login(username, state.secrets.get_password())
        smtp.send_message(msg)  # strips Bcc header, keeps Bcc recipients


def register_send_tools(mcp: FastMCP, state: AppState) -> None:
    async def _gated_send(ctx: Context, tool: str, msg: EmailMessage, account: str | None,
                          audit_fields: dict[str, Any]) -> dict[str, Any]:
        state.require_send(tool)
        # SMTP login bypasses state.connection(), so validate the account here
        # too — an unknown account must be rejected, not silently used.
        state.validate_account(account)
        await _confirm_with_human(ctx, msg, tool)
        try:
            await anyio.to_thread.run_sync(_smtp_send, state, account, msg)
        except Exception:
            state.audit.record(tool, "error", **audit_fields)
            raise
        state.audit.record(tool, "sent", **audit_fields)
        return {"sent": True, "message_id": msg["Message-ID"], **audit_fields}

    @mcp.tool(annotations=SEND_ANNOTATIONS)
    @tool_guard_async
    async def send_email(
        ctx: Context,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Send an email as the configured sender after human approval via elicitation.

        Requires PROTONMCP_ALLOW_SEND=true and an approving human; otherwise refuses.
        """
        state.require_send("send_email")
        sender = state.validate_from(None)
        msg = build_message(
            sender, validate_addresses(to, "to"), subject, body,
            cc=validate_addresses(cc, "cc"), bcc=validate_addresses(bcc, "bcc"),
        )
        return await _gated_send(ctx, "send_email", msg, account, {
            "account": account or state.config.primary_username,
            "from": sender, "to": to, "cc": cc, "bcc": bcc, "subject": subject,
        })

    async def _reply_impl(ctx: Context, tool: str, folder: str, uid: int, body: str,
                          reply_all: bool, account: str | None) -> dict[str, Any]:
        state.require_send(tool)
        sender = state.validate_from(None)

        def compose():
            client_run = state.connection(account)
            original = client_run.run(lambda c: fetch_full(c, folder, uid))
            orig_text, _ = body_text(original, 3000)
            to = validate_addresses(str(original.get("Reply-To") or original.get("From", "")), "to")
            cc: list[str] = []
            if reply_all:
                own = set(state.config.send_from) | {state.config.primary_username.lower()}
                extra = validate_addresses(str(original.get("To", "")), "to") + validate_addresses(
                    str(original.get("Cc", "")) or None, "cc")
                cc = [a for a in extra if a.lower() not in own and a not in to]
            msg_id = str(original.get("Message-ID", "")).strip()
            references = (str(original.get("References", "")) + " " + msg_id).strip()
            return build_message(
                sender, to, reply_subject(str(original.get("Subject", ""))),
                f"{body}\n\n{quote_body(orig_text)}",
                cc=cc, in_reply_to=msg_id or None, references=references or None,
            ), to, cc

        msg, to, cc = await anyio.to_thread.run_sync(compose)
        return await _gated_send(ctx, tool, msg, account, {
            "account": account or state.config.primary_username,
            "from": sender, "to": to, "cc": cc, "subject": str(msg["Subject"]),
            "source": f"{folder}/{uid}",
        })

    @mcp.tool(annotations=SEND_ANNOTATIONS)
    @tool_guard_async
    async def reply(ctx: Context, folder: str, uid: int, body: str,
                    account: str | None = None) -> dict[str, Any]:
        """Reply to the sender of a message, after human approval via elicitation."""
        return await _reply_impl(ctx, "reply", folder, uid, body, False, account)

    @mcp.tool(annotations=SEND_ANNOTATIONS)
    @tool_guard_async
    async def reply_all(ctx: Context, folder: str, uid: int, body: str,
                        account: str | None = None) -> dict[str, Any]:
        """Reply to all recipients of a message, after human approval via elicitation."""
        return await _reply_impl(ctx, "reply_all", folder, uid, body, True, account)

    @mcp.tool(annotations=SEND_ANNOTATIONS)
    @tool_guard_async
    async def forward(ctx: Context, folder: str, uid: int, to: list[str], body: str = "",
                      account: str | None = None) -> dict[str, Any]:
        """Forward a message (plain text, no attachments in v1), after human approval."""
        state.require_send("forward")
        sender = state.validate_from(None)

        def compose():
            original = state.connection(account).run(lambda c: fetch_full(c, folder, uid))
            orig_text, _ = body_text(original, state.config.max_body_chars)
            forwarded = (
                f"{body}\n\n---------- Forwarded message ----------\n"
                f"From: {original.get('From', '')}\nDate: {original.get('Date', '')}\n"
                f"Subject: {original.get('Subject', '')}\nTo: {original.get('To', '')}\n\n{orig_text}"
            )
            return build_message(sender, validate_addresses(to, "to"),
                                 forward_subject(str(original.get("Subject", ""))), forwarded)

        msg = await anyio.to_thread.run_sync(compose)
        return await _gated_send(ctx, "forward", msg, account, {
            "account": account or state.config.primary_username,
            "from": sender, "to": to, "subject": str(msg["Subject"]),
            "source": f"{folder}/{uid}",
        })
