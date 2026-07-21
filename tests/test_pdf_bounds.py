"""PDF extraction must be bounded against decompression bombs — including the
single-page vector where one page's content stream inflates to gigabytes.
Extraction runs in a separate process with a hard wall-clock timeout so a
runaway page can be terminated instead of OOM-ing the server."""

import io
import zlib

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import mcp_proton_email.extract as extract_module
from mcp_proton_email.extract import (
    ExtractionError,
    _accumulate_pages,
    _pdf_extract_inproc,
    _pdf_text,
    extract_text,
)


class _CountingPage:
    def __init__(self, text: str, counter: list) -> None:
        self._text = text
        self._counter = counter

    def extract_text(self) -> str:
        self._counter.append(1)
        return self._text


# -- streaming between pages (pure, fast) --------------------------------------


def test_accumulate_stops_early_at_char_budget():
    counter: list = []
    pages = [_CountingPage("x" * 100, counter) for _ in range(1000)]
    text, truncated = _accumulate_pages(pages, max_chars=250)
    assert truncated is True
    assert len(counter) <= 4, f"processed {len(counter)} pages; should stop near budget"
    assert len(text) <= 250


def test_accumulate_no_truncation_when_within_budget():
    counter: list = []
    pages = [_CountingPage("hi", counter) for _ in range(3)]
    text, truncated = _accumulate_pages(pages, max_chars=1000)
    assert truncated is False
    assert len(counter) == 3


# -- page-count cap (in-process core) ------------------------------------------


def _blank_pdf(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_page_cap_refuses_bomb(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 2)
    with pytest.raises(ExtractionError, match="page"):
        _pdf_extract_inproc(_blank_pdf(5), max_chars=1000)


def test_pdf_within_page_cap_ok(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 10)
    text, truncated = _pdf_extract_inproc(_blank_pdf(3), max_chars=1000)
    assert "no extractable text" in text
    assert truncated is False


# -- the single-page bomb: bounded by the subprocess timeout -------------------


def _single_page_bomb(n_ops: int) -> bytes:
    """One page whose content stream is n_ops text-show operators against a real
    font — the vector that makes pypdf's extractor do O(n_ops) work far beyond
    max_chars, on a page that stays under the byte cap."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): fref})
    })
    content = b"BT /F1 12 Tf " + b"(A) Tj " * n_ops + b"ET"
    stream = DecodedStreamObject()
    stream.set_data(content)
    ref = writer._add_object(stream)
    page[NameObject("/Contents")] = ref
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_single_page_bomb_is_terminated_by_timeout(monkeypatch):
    # A single page whose extraction runs far longer than the timeout must be
    # TERMINATED, not allowed to run to OOM. ~1M ops ≈ several seconds of parse.
    monkeypatch.setattr(extract_module, "PDF_EXTRACT_TIMEOUT_S", 1)
    bomb = _single_page_bomb(1_000_000)
    assert len(bomb) < extract_module.MAX_ATTACHMENT_SOURCE_BYTES
    with pytest.raises(ExtractionError, match="timed out|memory|too large"):
        _pdf_text(bomb, max_chars=20_000)


def test_pdf_text_normal_path_through_subprocess():
    # The happy path still works end-to-end through the worker process.
    text, truncated = _pdf_text(_blank_pdf(2), max_chars=1000)
    assert "no extractable text" in text
    assert truncated is False


def test_extract_text_pdf_bomb_bounded(monkeypatch):
    monkeypatch.setattr(extract_module, "PDF_EXTRACT_TIMEOUT_S", 1)
    bomb = _single_page_bomb(1_000_000)
    with pytest.raises(ExtractionError):
        extract_text(bomb, "application/pdf", "bomb.pdf", max_chars=20_000)
