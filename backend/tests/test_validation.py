"""Input validation: everything that should be rejected before extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.document_processing import (
    EmptyFileError,
    FileTooLargeError,
    NotAPdfError,
    PdfEncryptedError,
    PdfHasNoPagesError,
    PdfUnreadableError,
    SourceFileNotFoundError,
    extract_pdf,
    validate_pdf_file,
)


def test_missing_file_raises_source_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(SourceFileNotFoundError) as excinfo:
        validate_pdf_file(tmp_path / "nope.pdf")
    assert excinfo.value.code == "SOURCE_FILE_NOT_FOUND"


def test_directory_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "a_directory.pdf"
    directory.mkdir()
    with pytest.raises(SourceFileNotFoundError):
        validate_pdf_file(directory)


def test_zero_byte_file_raises_empty_file(zero_byte_pdf: Path) -> None:
    with pytest.raises(EmptyFileError) as excinfo:
        validate_pdf_file(zero_byte_pdf)
    assert excinfo.value.code == "EMPTY_FILE"


def test_oversized_file_raises_file_too_large(three_page_pdf: Path) -> None:
    with pytest.raises(FileTooLargeError) as excinfo:
        validate_pdf_file(three_page_pdf, max_file_size_bytes=10)
    assert excinfo.value.code == "FILE_TOO_LARGE"
    assert "exceeds" in excinfo.value.message


def test_html_named_pdf_is_rejected_on_bytes_not_extension(
    html_file_named_pdf: Path,
) -> None:
    """The extension says PDF; the bytes say otherwise. The bytes win."""
    assert html_file_named_pdf.suffix == ".pdf"
    with pytest.raises(NotAPdfError) as excinfo:
        validate_pdf_file(html_file_named_pdf)
    assert excinfo.value.code == "UNSUPPORTED_FILE_TYPE"


def test_valid_pdf_passes_validation_and_returns_size(three_page_pdf: Path) -> None:
    size = validate_pdf_file(three_page_pdf)
    assert size == three_page_pdf.stat().st_size
    assert size > 0


def test_truncated_pdf_raises_unreadable(truncated_pdf: Path) -> None:
    """A valid header is not a valid document; the parse failure must be typed."""
    with pytest.raises(PdfUnreadableError) as excinfo:
        extract_pdf(truncated_pdf)
    assert excinfo.value.code == "EXTRACTION_FAILED"


def test_extract_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceFileNotFoundError):
        extract_pdf(tmp_path / "absent.pdf")


def test_extract_pdf_accepts_a_string_path(three_page_pdf: Path) -> None:
    result = extract_pdf(str(three_page_pdf))
    assert result.document.page_count == 3


def test_pdf_with_zero_pages_raises_has_no_pages(pdf_with_no_pages: Path) -> None:
    """A file can be a perfectly valid PDF and still carry nothing to extract.

    The distinction from ``truncated_pdf`` matters: that one fails to parse, so
    ``EXTRACTION_FAILED`` is right. This one parses fine and simply has an empty
    page tree, which is a different diagnosis and gets its own code.
    """
    assert validate_pdf_file(pdf_with_no_pages) > 0  # byte-level validation passes
    with pytest.raises(PdfHasNoPagesError) as excinfo:
        extract_pdf(pdf_with_no_pages)
    assert excinfo.value.code == "PDF_HAS_NO_PAGES"


def test_password_protected_pdf_raises_encrypted(password_protected_pdf: Path) -> None:
    """An encrypted PDF must fail loudly, not extract as a stack of blank pages.

    Silently returning empty pages would be indistinguishable from a scanned
    document and would enter the corpus looking like a successful ingestion.
    """
    with pytest.raises(PdfEncryptedError) as excinfo:
        extract_pdf(password_protected_pdf)
    assert excinfo.value.code == "PDF_ENCRYPTED"


def test_encrypted_pdf_error_does_not_leak_a_password_guess(
    password_protected_pdf: Path,
) -> None:
    """The message names the file and the cause, and nothing about credentials.

    ``CLAUDE.md`` §20 requires error text that is safe to show a caller; the
    pipeline also has a standing rule that it never accepts a password, so the
    message must say that rather than inviting a retry.
    """
    with pytest.raises(PdfEncryptedError) as excinfo:
        extract_pdf(password_protected_pdf)
    assert "fixture-password" not in excinfo.value.message
    assert "encrypted" in excinfo.value.message.lower()
