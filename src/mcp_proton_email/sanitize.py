"""Filename path-safety, secret redaction, and error collapsing."""

import re
import unicodedata
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"(?i)((?:password|passwd|pwd|token|secret|api[_-]?key|authorization)\s*[=:]\s*)[^\r\n]+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"://([^/\s:@]+):([^/\s@]+)@"),  # user:pass@host in URLs
    re.compile(r"(?i)(AUTH\s+PLAIN\s+)\S+"),
]

_FILENAME_BAD = re.compile(r'[\x00-\x1f<>:"|?*]')

# Exact secret values registered at runtime (e.g. the Bridge password when it is
# read into memory). The pattern-based redaction below is a best-effort backstop
# for labelled secrets; this set guarantees the *actual* secret is scrubbed from
# any output regardless of the shape it appears in. Short values are ignored to
# avoid over-redacting ordinary text.
_REGISTERED_SECRETS: set[str] = set()
_MIN_SECRET_LEN = 8


def register_secret(value: str | None) -> None:
    if value and len(value) >= _MIN_SECRET_LEN:
        _REGISTERED_SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _REGISTERED_SECRETS:
        if secret in text:
            text = text.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(r"://\1:[redacted]@", text)
        else:
            text = pattern.sub(r"\1[redacted]", text)
    return text


def collapse_error(exc: BaseException) -> dict[str, str]:
    """Reduce an exception to {name, message} with secrets redacted (spec 6.9)."""
    return {"name": type(exc).__name__, "message": redact(str(exc))[:500]}


def sanitize_filename(name: str) -> str:
    """Strip path separators, traversal, control chars; NFC-normalize (spec 6.7)."""
    name = unicodedata.normalize("NFC", name or "")
    # Keep only the final path component regardless of separator style.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _FILENAME_BAD.sub("_", name)
    name = name.replace("..", "_").strip().lstrip(".")
    return name[:200] or "attachment"


def resolve_in_root(root: Path, filename: str) -> Path:
    """Resolve a sanitized filename strictly inside root; dedup instead of overwrite."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"attachment download dir does not exist: {root}")
    candidate = (root / sanitize_filename(filename)).resolve()
    if candidate.parent != root:
        raise ValueError("resolved path escapes the attachment download dir")
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = root / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate
