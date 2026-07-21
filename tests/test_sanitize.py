"""A9: crafted filenames cannot escape the download dir; secrets get redacted."""

import pytest

from mcp_proton_email.sanitize import collapse_error, redact, resolve_in_root, sanitize_filename


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\evil.exe", "evil.exe"),
        ("/etc/cron.d/job", "job"),
        ("....//receipt.pdf", "receipt.pdf"),
        (".hidden", "hidden"),
        ("", "attachment"),
        ("con\x00trol\x1f.pdf", "con_trol_.pdf"),
        ("recibo — junio.pdf", "recibo — junio.pdf"),
    ],
)
def test_sanitize_filename(raw, safe):
    result = sanitize_filename(raw)
    assert result == safe
    assert "/" not in result and ".." not in result


def test_resolve_in_root_stays_inside(tmp_path):
    target = resolve_in_root(tmp_path, "../../../etc/passwd")
    assert target.parent == tmp_path.resolve()


def test_resolve_in_root_never_overwrites(tmp_path):
    (tmp_path / "receipt.pdf").write_bytes(b"existing")
    target = resolve_in_root(tmp_path, "receipt.pdf")
    assert target.name == "receipt-1.pdf"


def test_resolve_in_root_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.pdf").symlink_to(outside / "victim.pdf")
    # the symlink resolves outside the root -> hard refusal, never followed
    with pytest.raises(ValueError, match="escapes"):
        resolve_in_root(root, "link.pdf")


def test_resolve_in_root_requires_existing_dir(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        resolve_in_root(tmp_path / "missing", "a.pdf")


@pytest.mark.parametrize(
    "text",
    [
        "login failed password=hunter2 for user",
        "imap://user:hunter2@127.0.0.1/",
        "Authorization: Bearer abc.def.ghi",
        "b'AUTH PLAIN aGVsbG8='",
        "token: hunter2",
    ],
)
def test_redact_hides_secrets(text):
    assert "hunter2" not in redact(text)
    assert "aGVsbG8" not in redact(text)
    assert "abc.def.ghi" not in redact(text)


def test_collapse_error_redacts():
    err = RuntimeError("LOGIN failed: password=hunter2")
    collapsed = collapse_error(err)
    assert collapsed["name"] == "RuntimeError"
    assert "hunter2" not in collapsed["message"]


def test_registered_secret_scrubbed_even_bare(monkeypatch):
    # A bare secret with no label must still be scrubbed once registered —
    # closes the "redaction is label-shape-only" gap.
    from mcp_proton_email import sanitize

    monkeypatch.setattr(sanitize, "_REGISTERED_SECRETS", set())
    sanitize.register_secret("Xk9-BridgePass-Zq2wv")
    # every shape: bare, in an IMAP command echo, inside a URL
    for text in [
        "A1 LOGIN alex@proton.me Xk9-BridgePass-Zq2wv",
        "unexpected token Xk9-BridgePass-Zq2wv near end",
        "connect imap://alex@127.0.0.1/ with Xk9-BridgePass-Zq2wv",
    ]:
        assert "Xk9-BridgePass-Zq2wv" not in sanitize.redact(text)


def test_register_secret_ignores_short_values(monkeypatch):
    # Don't register trivially-short strings (would over-redact common text).
    from mcp_proton_email import sanitize

    monkeypatch.setattr(sanitize, "_REGISTERED_SECRETS", set())
    sanitize.register_secret("abc")
    assert sanitize.redact("the abc of it") == "the abc of it"
