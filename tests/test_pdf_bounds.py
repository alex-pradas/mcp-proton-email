"""PDF extraction must be bounded against decompression bombs: peak memory
~max_chars via streaming early-exit, and a hard page-count cap. A malicious
attachment under the byte cap can still inflate to gigabytes otherwise."""

import io

import pytest
from pypdf import PdfWriter

import mcp_proton_email.extract as extract_module
from mcp_proton_email.extract import ExtractionError, _accumulate_pages, _pdf_text, extract_text


class _CountingPage:
    def __init__(self, text: str, counter: list) -> None:
        self._text = text
        self._counter = counter

    def extract_text(self) -> str:
        self._counter.append(1)
        return self._text


def test_accumulate_stops_early_at_char_budget():
    counter: list = []
    pages = [_CountingPage("x" * 100, counter) for _ in range(1000)]
    text, truncated = _accumulate_pages(pages, max_chars=250)
    assert truncated is True
    # must NOT have called extract_text on all 1000 pages — peak memory bounded
    assert len(counter) <= 4, f"processed {len(counter)} pages; should stop near budget"
    assert len(text) <= 250


def test_accumulate_no_truncation_when_within_budget():
    counter: list = []
    pages = [_CountingPage("hi", counter) for _ in range(3)]
    text, truncated = _accumulate_pages(pages, max_chars=1000)
    assert truncated is False
    assert len(counter) == 3
    assert "hi" in text


def _make_pdf(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_page_cap_refuses_bomb(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 2)
    payload = _make_pdf(5)
    with pytest.raises(ExtractionError, match="page"):
        _pdf_text(payload, max_chars=1000)


def test_pdf_within_page_cap_ok(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 10)
    payload = _make_pdf(3)  # blank pages -> no extractable text
    text, truncated = _pdf_text(payload, max_chars=1000)
    assert "no extractable text" in text
    assert truncated is False


def test_extract_text_pdf_path_is_bounded(monkeypatch):
    # end-to-end through extract_text: a page-capped PDF is refused, not OOM'd
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 1)
    payload = _make_pdf(4)
    with pytest.raises(ExtractionError, match="page"):
        extract_text(payload, "application/pdf", "bomb.pdf", max_chars=100)
