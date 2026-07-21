"""Header-injection rejection, HTML stripping, untrusted wrapping, audit log."""

from email.message import EmailMessage

import pytest

import mcp_proton_email.audit as audit_module
from mcp_proton_email.audit import AuditLog
from mcp_proton_email.compose import RecipientError, build_message, validate_addresses
from mcp_proton_email.extract import ExtractionError, extract_text
from mcp_proton_email.mailmsg import (
    UNTRUSTED_HEADER,
    body_text,
    html_to_text,
    list_attachments,
    wrap_untrusted,
)

# -- compose -------------------------------------------------------------------


def test_recipient_newline_rejected():
    with pytest.raises(RecipientError, match="injection"):
        validate_addresses(["victim@x.com\r\nBcc: attacker@evil.com"], "to")


def test_recipient_malformed_rejected():
    with pytest.raises(RecipientError, match="malformed"):
        validate_addresses(["not-an-address"], "to")


def test_subject_newline_rejected():
    with pytest.raises(RecipientError, match="injection"):
        build_message("a@b.se", ["c@d.se"], "hi\r\nBcc: attacker@evil.com", "body")


def test_build_message_roundtrip():
    msg = build_message("user@example.com", ["x@y.se"], "Hello", "Body text", cc=["c@d.se"])
    assert msg["From"] == "user@example.com"
    assert msg["Message-ID"].endswith("@example.com>")
    assert "Body text" in msg.get_content()


# -- parsing ---------------------------------------------------------------------


def test_html_to_text_strips_script_and_tags():
    html = "<html><head><style>x{}</style></head><body><script>evil()</script><p>Hello <b>world</b></p></body></html>"
    text = html_to_text(html)
    assert "Hello world" in text
    assert "evil" not in text and "<p>" not in text


def test_html_to_text_survives_void_meta_in_head():
    # Outlook/Office HTML: void <meta> tags in <head> must not swallow the body.
    html = (
        '<html><head><meta http-equiv="Content-Type" content="text/html">'
        '<meta name="Generator" content="Microsoft Word"></head>'
        "<body><p>Order 332383 confirmed.</p></body></html>"
    )
    text = html_to_text(html)
    assert "Order 332383 confirmed." in text
    assert "Content-Type" not in text and "Microsoft Word" not in text


def test_html_to_text_handles_multiple_void_and_br():
    html = "<head><meta><meta><link></head><body>Line one<br>Line two</body>"
    text = html_to_text(html)
    assert "Line one" in text and "Line two" in text


def test_body_text_prefers_plain():
    msg = EmailMessage()
    msg.set_content("plain version")
    msg.add_alternative("<p>html version</p>", subtype="html")
    text, truncated = body_text(msg, 1000)
    assert text == "plain version"
    assert truncated is False


def test_body_text_truncates():
    msg = EmailMessage()
    msg.set_content("x" * 500)
    text, truncated = body_text(msg, 100)
    assert len(text) == 100 and truncated


def test_wrap_untrusted():
    wrapped = wrap_untrusted("hi")
    assert wrapped.startswith(UNTRUSTED_HEADER) and wrapped.endswith("[END UNTRUSTED CONTENT]")


def test_list_attachments():
    msg = EmailMessage()
    msg.set_content("body")
    msg.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf", filename="receipt.pdf")
    infos = list_attachments(msg)
    assert len(infos) == 1
    assert infos[0].filename == "receipt.pdf"
    assert infos[0].content_type == "application/pdf"
    assert infos[0].size_bytes == len(b"%PDF-fake")


# -- extraction -------------------------------------------------------------------


def test_extract_unsupported_type_refused():
    with pytest.raises(ExtractionError, match="cannot extract"):
        extract_text(b"MZ\x90binary", "application/x-msdownload", "evil.exe", 1000)


def test_extract_oversized_refused():
    big = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ExtractionError, match="byte extraction limit"):
        extract_text(big, "text/plain", "big.txt", 1000)


def test_extract_text_and_html():
    text, _ = extract_text(b"hello receipt 42.50 EUR", "text/plain", "r.txt", 1000)
    assert "42.50" in text
    text, _ = extract_text(b"<p>total <b>99 SEK</b></p>", "text/html", "r.html", 1000)
    assert text == "total 99 SEK"


# -- audit -------------------------------------------------------------------------


def test_audit_record_tail_and_perms(tmp_path):
    log = AuditLog(tmp_path / "auditdir")
    log.record("move_message", "ok", source="INBOX/5", target="Archive")
    entries = log.tail()
    assert entries[-1]["tool"] == "move_message"
    assert entries[-1]["target"] == "Archive"
    assert (tmp_path / "auditdir").stat().st_mode & 0o777 == 0o700
    assert log.path.stat().st_mode & 0o777 == 0o600


def test_audit_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "ROTATE_BYTES", 200)
    log = AuditLog(tmp_path / "auditdir")
    for i in range(20):
        log.record("mark_read", "ok", source=f"INBOX/{i}")
    assert log.path.with_suffix(".log.1").exists()
