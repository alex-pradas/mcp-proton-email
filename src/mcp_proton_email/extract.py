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

# A malicious PDF under the byte cap can still carry FlateDecode streams that
# inflate ~1000x. We bound the many-pages vector two ways: refuse absurd page
# counts up front, and stream page text with early-exit at the char budget so
# peak memory stays ~max_chars instead of materializing every page.
MAX_PDF_PAGES = 3000


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
        return _pdf_text(payload, max_chars)
    if content_type == "text/html" or name.endswith((".html", ".htm")):
        text = html_to_text(payload.decode("utf-8", errors="replace"))
    elif content_type.startswith(TEXT_LIKE_PREFIXES) or name.endswith((".txt", ".csv", ".ics", ".md")):
        text = payload.decode("utf-8", errors="replace")
    else:
        raise ExtractionError(f"cannot extract text from {content_type!r} ({SUPPORTED_HINT})")

    text = text.strip()
    truncated = len(text) > max_chars
    return text[:max_chars], truncated


def _accumulate_pages(pages, max_chars: int) -> tuple[str, bool]:
    """Stream page text, stopping once the char budget is reached so a
    many-page bomb cannot force us to hold every page in memory at once."""
    parts: list[str] = []
    total = 0
    truncated = False
    for page in pages:
        try:
            chunk = page.extract_text() or ""
        except Exception:
            chunk = ""
        parts.append(chunk.strip())
        total += len(chunk)
        if total >= max_chars:
            truncated = True
            break
    text = "\n\n".join(p for p in parts if p).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, truncated


def _pdf_text(payload: bytes, max_chars: int) -> tuple[str, bool]:
    try:
        reader = PdfReader(io.BytesIO(payload))
        page_count = len(reader.pages)
    except Exception as exc:
        raise ExtractionError(f"PDF parsing failed: {type(exc).__name__}") from exc
    if page_count > MAX_PDF_PAGES:
        raise ExtractionError(
            f"PDF has {page_count} pages, over the {MAX_PDF_PAGES}-page extraction "
            "limit — use save_attachment to save the file instead"
        )
    text, truncated = _accumulate_pages(reader.pages, max_chars)
    if not text:
        return (
            "[PDF contains no extractable text — likely scanned/image-only. "
            "Use save_attachment to save the file.]",
            False,
        )
    return text, truncated
