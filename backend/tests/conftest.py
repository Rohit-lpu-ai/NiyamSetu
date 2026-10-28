"""Shared pytest fixtures.

Every PDF used by the automated tests is generated here from
:mod:`backend.tests.pdf_builder`. They are synthetic fixtures, not government
documents, and no test asserts anything about the real corpus - real-document
checks are a separate, manual validation run (see ``docs/pdf-extraction.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .pdf_builder import build_pdf_bytes, page_lines


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    """A three-page PDF where every page carries meaningful, identifiable text."""
    path = tmp_path / "three_page.pdf"
    path.write_bytes(build_pdf_bytes([page_lines(n) for n in (1, 2, 3)]))
    return path


@pytest.fixture
def pdf_with_blank_middle_page(tmp_path: Path) -> Path:
    """Three pages where page 2 has no text at all.

    This stands in for a scanned or image-only page. It is *not* a real scanned
    document, and the tests that use it only assert how the pipeline reports
    such a page.
    """
    path = tmp_path / "blank_middle.pdf"
    path.write_bytes(build_pdf_bytes([page_lines(1), [], page_lines(3)]))
    return path


@pytest.fixture
def pdf_with_text_poor_page(tmp_path: Path) -> Path:
    """Two pages, the second carrying only a couple of characters."""
    path = tmp_path / "text_poor.pdf"
    path.write_bytes(build_pdf_bytes([page_lines(1), ["ok"]]))
    return path


@pytest.fixture
def pdf_with_all_pages_blank(tmp_path: Path) -> Path:
    """Two pages, neither carrying any text - the ``failed`` status case."""
    path = tmp_path / "all_blank.pdf"
    path.write_bytes(build_pdf_bytes([[], []]))
    return path


@pytest.fixture
def pdf_with_info_metadata(tmp_path: Path) -> Path:
    """A single-page PDF carrying a populated ``/Info`` dictionary."""
    path = tmp_path / "with_info.pdf"
    path.write_bytes(
        build_pdf_bytes(
            [page_lines(1)],
            info={
                "Title": "Fixture Title",
                "Author": "NiyamSetu Test Suite",
                "Creator": "backend.tests.pdf_builder",
            },
        )
    )
    return path


@pytest.fixture
def html_file_named_pdf(tmp_path: Path) -> Path:
    """An HTML page saved with a ``.pdf`` extension.

    This is the real failure mode that prompted the byte-level check: the first
    download attempt against the RBI document host returned an interstitial HTML
    page with ``Content-Type: text/html`` for a ``.PDF`` URL.
    """
    path = tmp_path / "actually_html.pdf"
    path.write_text(
        "<!DOCTYPE html>\n<html><body>Access denied</body></html>\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def truncated_pdf(tmp_path: Path) -> Path:
    """A file with a valid PDF header but a destroyed body."""
    path = tmp_path / "truncated.pdf"
    full = build_pdf_bytes([page_lines(1)])
    path.write_bytes(full[:60])
    return path


@pytest.fixture
def zero_byte_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    return path
