"""In-memory attachment text extraction: text, HTML, PDF, ics (spec 5).

Never writes to disk, never returns raw binary. Office formats and OCR are
deliberately out of scope for v1.
"""

import io
import multiprocessing
import os
import queue as queue_mod
import subprocess
import threading
import time

from pypdf import PdfReader

from .config import MAX_ATTACHMENT_SOURCE_BYTES
from .mailmsg import html_to_text

TEXT_LIKE_PREFIXES = ("text/",)
SUPPORTED_HINT = "supported: text/*, text/html, application/pdf, text/calendar (.ics)"

# A malicious PDF under the byte cap can carry FlateDecode streams that inflate
# ~1000x. pypdf 6.14.2 caps single-stream decompression (~75MB) which mitigates
# much of the single-page inflate vector, but we don't rely on that alone: PDF
# text extraction runs in a separate process we actively police — drain its
# result queue cooperatively (avoiding a pipe-buffer deadlock on large results),
# poll its RSS and hard-kill past PDF_CHILD_RSS_LIMIT_MB (the only reliable
# memory bound on macOS, where RLIMIT_AS is a no-op), enforce a wall-clock
# timeout, and cap concurrency. Defense in depth over pypdf's own guards.
MAX_PDF_PAGES = 3000
PDF_EXTRACT_TIMEOUT_S = 10  # legit PDFs extract in <1s
PDF_CHILD_RSS_LIMIT_MB = 1024  # kill the worker if it grows past this
PDF_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # best-effort RLIMIT_AS (Linux/CI)
PDF_MAX_CONCURRENCY = 2  # bound simultaneous worker memory footprints
_PDF_POLL_S = 0.1

_pdf_semaphore = threading.BoundedSemaphore(PDF_MAX_CONCURRENCY)


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


def _pdf_worker(payload: bytes, max_chars: int, out_queue) -> None:
    # Redirect fd 1/2 (not just sys.stdout) to devnull so nothing — including a
    # C-level write or pypdf warning — can reach the MCP stdio protocol stream.
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
    except Exception:
        pass
    try:
        import resource  # best-effort address-space cap (honoured on Linux)

        resource.setrlimit(resource.RLIMIT_AS, (PDF_MEMORY_LIMIT_BYTES, PDF_MEMORY_LIMIT_BYTES))
    except Exception:
        pass
    try:
        text, truncated = _pdf_extract_inproc(payload, max_chars)
        out_queue.put(("ok", text, truncated))
    except MemoryError:
        out_queue.put(("err", "PDF extraction exceeded the memory limit — use save_attachment instead", False))
    except ExtractionError as exc:
        out_queue.put(("err", str(exc), False))
    except Exception as exc:
        out_queue.put(("err", f"PDF parsing failed: {type(exc).__name__}", False))


def _child_rss_mb(pid: int) -> float | None:
    """Resident memory of a child process in MB, or None if unavailable."""
    try:  # Linux fast path
        with open(f"/proc/{pid}/statm") as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        pass
    try:  # macOS / BSD
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return int(out.stdout.strip() or 0) / 1024
    except Exception:
        return None


def _pdf_text(payload: bytes, max_chars: int) -> tuple[str, bool]:
    # Bound how many heavy workers run at once (an inbox of malicious PDFs must
    # not spawn N simultaneous multi-GB processes).
    with _pdf_semaphore:
        return _pdf_text_supervised(payload, max_chars)


def _pdf_text_supervised(payload: bytes, max_chars: int) -> tuple[str, bool]:
    ctx = multiprocessing.get_context("spawn")
    out_queue = ctx.Queue()
    proc = ctx.Process(target=_pdf_worker, args=(payload, max_chars, out_queue), daemon=True)
    proc.start()
    start = time.monotonic()
    result = None
    reason: str | None = None
    try:
        while True:
            try:
                # get() actively receives — draining a large result as the
                # worker writes it, so its feeder thread never deadlocks.
                result = out_queue.get(timeout=_PDF_POLL_S)
                break
            except queue_mod.Empty:
                pass
            if not proc.is_alive():
                break  # exited without a result (e.g. OS-killed)
            rss = _child_rss_mb(proc.pid)
            if rss is not None and rss > PDF_CHILD_RSS_LIMIT_MB:
                reason = "memory"
                break
            if time.monotonic() - start > PDF_EXTRACT_TIMEOUT_S:
                reason = "timeout"
                break
    finally:
        if reason in ("memory", "timeout"):
            # Condemned worker: kill immediately — don't grant a runaway extra
            # runtime to allocate while we wait for a natural exit.
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
        else:
            # Clean success (or the child already exited): reap it.
            proc.join(1)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                    proc.join(5)

    if reason == "memory":
        raise ExtractionError(
            "PDF extraction exceeded the memory limit (possible decompression "
            "bomb). Use save_attachment to save the file instead."
        )
    if reason == "timeout":
        raise ExtractionError(
            "PDF text extraction timed out — the file is too large or malformed "
            "(possible decompression bomb). Use save_attachment to save it instead."
        )
    if result is None:
        raise ExtractionError(
            "PDF extraction failed (worker exited without a result). "
            "Use save_attachment to save the file instead."
        )
    status, value, truncated = result
    if status == "err":
        raise ExtractionError(value)
    return value, truncated
