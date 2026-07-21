"""In-memory attachment text extraction: text, HTML, PDF, ics (spec 5).

Never writes to disk, never returns raw binary. Office formats and OCR are
deliberately out of scope for v1.
"""

import io

from pypdf import PdfReader

from .config import MAX_ATTACHMENT_SOURCE_BYTES
from .mailmsg import html_to_text

TEXT_LIKE_PREFIXES = ("text/",)
SUPPORTED_HINT = "supported: text/*, text/html, application/pdf, text/calendar (.ics)"


class ExtractionError(Exception):
    pass


def extract_text(payload: bytes, content_type: str, filename: str, max_chars: int) -> tuple[str, bool]:
    """Extract readable text from an attachment. Returns (text, truncated)."""
    if len(payload) > MAX_ATTACHMENT_SOURCE_BYTES:
        raise ExtractionError(
            f"attachment is {len(payload)} bytes, over the "
            f"{MAX_ATTACHMENT_SOURCE_BYTES} byte extraction limit — use save_attachment instead"
        )
    content_type = content_type.lower()
    name = filename.lower()

    if content_type == "application/pdf" or name.endswith(".pdf"):
        text = _pdf_text(payload)
    elif content_type == "text/html" or name.endswith((".html", ".htm")):
        text = html_to_text(payload.decode("utf-8", errors="replace"))
    elif content_type.startswith(TEXT_LIKE_PREFIXES) or name.endswith((".txt", ".csv", ".ics", ".md")):
        text = payload.decode("utf-8", errors="replace")
    else:
        raise ExtractionError(f"cannot extract text from {content_type!r} ({SUPPORTED_HINT})")

    text = text.strip()
    truncated = len(text) > max_chars
    return text[:max_chars], truncated


def _pdf_text(payload: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"PDF parsing failed: {type(exc).__name__}") from exc
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text:
        return "[PDF contains no extractable text — likely scanned/image-only. Use save_attachment to save the file.]"
    return text
