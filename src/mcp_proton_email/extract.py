"""In-memory attachment text extraction: text, HTML, PDF, ics (spec 5).

Never writes to disk, never returns raw binary. Office formats and OCR are
deliberately out of scope for v1.
"""

import io
import multiprocessing
import os
import sys

from pypdf import PdfReader

from .config import MAX_ATTACHMENT_SOURCE_BYTES
from .mailmsg import html_to_text

TEXT_LIKE_PREFIXES = ("text/",)
SUPPORTED_HINT = "supported: text/*, text/html, application/pdf, text/calendar (.ics)"

# A malicious PDF under the byte cap can carry FlateDecode streams that inflate
# ~1000x. Two vectors: many pages (bounded by MAX_PDF_PAGES + between-page
# streaming) and a SINGLE page whose content stream inflates to gigabytes
# inside pypdf's parser (page.extract_text() materializes it before any char
# check — page/stream limits alone can't bound that). So PDF text extraction
# runs in a separate process we can hard-terminate: a wall-clock timeout bounds
# runaway CPU/memory (reliable on macOS, where RLIMIT_AS is ignored), with a
# best-effort address-space cap where the OS honours it.
MAX_PDF_PAGES = 3000
PDF_EXTRACT_TIMEOUT_S = 15
PDF_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # best-effort (Linux/CI)


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


def _pdf_extract_inproc(payload: bytes, max_chars: int) -> tuple[str, bool]:
    """The actual pypdf work. Runs INSIDE the worker process (see _pdf_text)."""
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


def _pdf_worker(payload: bytes, max_chars: int, queue) -> None:
    # Never write to the inherited stdout (the MCP protocol stream); pypdf may
    # emit warnings. Redirect both streams to devnull.
    devnull = open(os.devnull, "w")
    sys.stdout = devnull
    sys.stderr = devnull
    try:
        import resource  # best-effort address-space cap (honoured on Linux)

        resource.setrlimit(resource.RLIMIT_AS, (PDF_MEMORY_LIMIT_BYTES, PDF_MEMORY_LIMIT_BYTES))
    except Exception:
        pass
    try:
        text, truncated = _pdf_extract_inproc(payload, max_chars)
        queue.put(("ok", text, truncated))
    except MemoryError:
        queue.put(("err", "PDF extraction exceeded the memory limit — use save_attachment instead", False))
    except ExtractionError as exc:
        queue.put(("err", str(exc), False))
    except Exception as exc:
        queue.put(("err", f"PDF parsing failed: {type(exc).__name__}", False))


def _pdf_text(payload: bytes, max_chars: int) -> tuple[str, bool]:
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_pdf_worker, args=(payload, max_chars, queue), daemon=True)
    proc.start()
    proc.join(PDF_EXTRACT_TIMEOUT_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
        raise ExtractionError(
            "PDF text extraction timed out — the file is too large or malformed "
            "(possible decompression bomb). Use save_attachment to save it instead."
        )
    try:
        result = queue.get_nowait()
    except Exception:
        raise ExtractionError(
            "PDF extraction failed (worker exited without a result — likely the "
            "memory limit). Use save_attachment to save the file instead."
        ) from None
    status, value, truncated = result
    if status == "err":
        raise ExtractionError(value)
    return value, truncated
