"""Schema-level invariants that protect page provenance."""

from __future__ import annotations

import pytest

from backend.app.document_processing import (
    DocumentRecord,
    ExtractionResult,
    PageRecord,
)


def make_page(page_number: int) -> PageRecord:
    return PageRecord(
        page_number=page_number,
        original_text="text",
        normalized_text="text",
        char_count=4,
        normalized_char_count=4,
        word_count=1,
        is_empty=False,
        has_meaningful_text=True,
    )


def make_document(page_count: int, status: str = "ok") -> DocumentRecord:
    return DocumentRecord(
        document_id="DOC-0000000000000000",
        version_id="VER-0000000000000000",
        filename="f.pdf",
        checksum="0" * 64,
        file_size_bytes=1,
        page_count=page_count,
        extraction_status=status,
        extraction_tool="pypdf test",
        extracted_at="2026-10-28T00:00:00+00:00",
    )


def test_page_number_zero_is_rejected() -> None:
    """Page numbering is 1-based everywhere in this project."""
    with pytest.raises(ValueError, match="1-based"):
        make_page(0)


def test_negative_page_number_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_page(-1)


def test_unknown_extraction_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="extraction_status"):
        make_document(1, status="success")


def test_page_sequence_must_start_at_one() -> None:
    with pytest.raises(ValueError, match="1-based consecutive"):
        ExtractionResult(document=make_document(2), pages=[make_page(2), make_page(3)])


def test_page_sequence_must_not_have_gaps() -> None:
    """A gap would mean a page was silently dropped - exactly what we forbid."""
    with pytest.raises(ValueError, match="1-based consecutive"):
        ExtractionResult(document=make_document(2), pages=[make_page(1), make_page(3)])


def test_page_count_must_match_the_number_of_page_records() -> None:
    with pytest.raises(ValueError, match="page_count"):
        ExtractionResult(document=make_document(5), pages=[make_page(1)])


def test_a_consistent_result_is_accepted() -> None:
    result = ExtractionResult(
        document=make_document(2), pages=[make_page(1), make_page(2)]
    )
    assert result.meaningful_page_count == 2
    assert result.empty_page_count == 0
    assert result.text_poor_page_numbers == []
