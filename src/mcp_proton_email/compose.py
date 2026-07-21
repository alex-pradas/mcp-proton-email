"""MIME composition and recipient validation (header-injection safe)."""

import email.utils
import re
from email.message import EmailMessage

_ADDR_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


class RecipientError(Exception):
    pass


def validate_addresses(raw: list[str] | str | None, field: str) -> list[str]:
    """Normalize a recipient list; reject header injection and malformed addresses."""
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    addresses: list[str] = []
    for item in items:
        if "\r" in item or "\n" in item:
            raise RecipientError(f"{field}: newline in address — refused (header injection)")
        for _name, addr in email.utils.getaddresses([item]):
            addr = addr.strip()
            if not addr:
                continue
            if not _ADDR_RE.match(addr):
                raise RecipientError(f"{field}: malformed address {addr!r}")
            addresses.append(addr)
    return addresses


def build_message(
    from_addr: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    if "\r" in subject or "\n" in subject:
        raise RecipientError("subject: newline — refused (header injection)")
    msg = EmailMessage()
    msg["From"] = from_addr
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # kept in drafts for editing; smtplib.send_message strips Bcc on transmit
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=from_addr.rsplit("@", 1)[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    return msg


def quote_body(text: str, limit: int = 3000) -> str:
    quoted = "\n".join("> " + line for line in text[:limit].splitlines())
    if len(text) > limit:
        quoted += "\n> [... truncated]"
    return quoted


def reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def forward_subject(subject: str) -> str:
    return subject if subject.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subject}"
