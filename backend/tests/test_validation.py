"""Input validation: everything that should be rejected before extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.document_processing import (
    EmptyFileError,
    FileTooLargeError,
    NotAPdfError,
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
