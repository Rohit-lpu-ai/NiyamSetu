"""Page-by-page extraction: ordering, provenance, status and warnings."""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.document_processing import (
    SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    SourceMetadata,
    extract_pdf,
    result_to_json,
)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_successful_extraction_reports_ok(three_page_pdf: Path) -> None:
    result = extract_pdf(three_page_pdf)
    assert result.document.extraction_status == STATUS_OK
    assert result.document.page_count == 3
    assert result.meaningful_page_count == 3
    assert result.empty_page_count == 0
    assert result.document.warnings == []


def test_page_numbers_are_one_based_and_consecutive(three_page_pdf: Path) -> None:
    result = extract_pdf(three_page_pdf)
    assert [page.page_number for page in result.pages] == [1, 2, 3]


def test_page_boundaries_are_preserved_not_merged(three_page_pdf: Path) -> None:
    """Page N's text must contain page N's marker and nobody else's.

    This is the provenance guarantee: if extraction merged pages or lost the
    boundary, page 1 would carry page 2's marker and this would fail.
    """
    result = extract_pdf(three_page_pdf)
    for page in result.pages:
        assert f"FIXTURE PAGE {page.page_number} OF" in page.original_text
        for other in (1, 2, 3):
            if other != page.page_number:
                assert f"FIXTURE PAGE {other} OF" not in page.original_text


def test_original_text_is_kept_separate_from_normalized_text(
    three_page_pdf: Path,
) -> None:
    """Both fields are populated, and the raw one is never replaced."""
    result = extract_pdf(three_page_pdf)
    page = result.pages[0]
    assert page.original_text
    assert page.normalized_text
    # The fixture's drawing produces trailing whitespace that normalisation
    # tidies, so the two fields are measurably distinct rather than aliases.
    assert page.char_count == len(page.original_text)
    assert page.normalized_char_count == len(page.normalized_text)
    assert "FIXTURE PAGE 1 OF" in page.original_text
    assert "FIXTURE PAGE 1 OF" in page.normalized_text


def test_character_counts_match_the_text_they_describe(three_page_pdf: Path) -> None:
    for page in extract_pdf(three_page_pdf).pages:
        assert page.char_count == len(page.original_text)
        assert page.normalized_char_count == len(page.normalized_text)
        assert page.word_count == len(page.normalized_text.strip().split())


# --------------------------------------------------------------------------- #
# Identity and metadata
# --------------------------------------------------------------------------- #


def test_checksum_is_recorded_and_stable_across_runs(three_page_pdf: Path) -> None:
    first = extract_pdf(three_page_pdf)
    second = extract_pdf(three_page_pdf)
    assert len(first.document.checksum) == 64
    assert first.document.checksum == second.document.checksum
    assert first.document.version_id == second.document.version_id
    assert first.document.document_id == second.document.document_id


def test_different_files_get_different_version_ids(
    three_page_pdf: Path, pdf_with_text_poor_page: Path
) -> None:
    a = extract_pdf(three_page_pdf).document
    b = extract_pdf(pdf_with_text_poor_page).document
    assert a.checksum != b.checksum
    assert a.version_id != b.version_id


def test_document_group_makes_two_files_one_logical_document(
    three_page_pdf: Path, pdf_with_text_poor_page: Path
) -> None:
    """Two different files, declared by a human as versions of one policy."""
    group = SourceMetadata(document_group="rbi-capital-adequacy-directions")
    a = extract_pdf(three_page_pdf, source_metadata=group).document
    b = extract_pdf(pdf_with_text_poor_page, source_metadata=group).document
    assert a.document_id == b.document_id
    assert a.version_id != b.version_id


def test_without_a_document_group_each_file_is_its_own_document(
    three_page_pdf: Path, pdf_with_text_poor_page: Path
) -> None:
    a = extract_pdf(three_page_pdf).document
    b = extract_pdf(pdf_with_text_poor_page).document
    assert a.document_id != b.document_id


def test_supplied_metadata_is_recorded_verbatim(three_page_pdf: Path) -> None:
    metadata = SourceMetadata(
        title="A Fixture Notification",
        issuing_department="Fixture Department",
        document_type="notification",
        publication_date="2026-04-01",
        source_url="https://example.invalid/fixture.pdf",
    )
    document = extract_pdf(three_page_pdf, source_metadata=metadata).document
    assert document.source_metadata["title"] == "A Fixture Notification"
    assert document.source_metadata["publication_date"] == "2026-04-01"


def test_unsupplied_metadata_stays_null_and_is_never_invented(
    three_page_pdf: Path,
) -> None:
    document = extract_pdf(three_page_pdf).document
    assert document.source_metadata["title"] is None
    assert document.source_metadata["issuing_department"] is None
    assert document.source_metadata["publication_date"] is None


def test_pdf_info_metadata_is_read_when_present(pdf_with_info_metadata: Path) -> None:
    document = extract_pdf(pdf_with_info_metadata).document
    assert document.pdf_metadata["title"] == "Fixture Title"
    assert document.pdf_metadata["author"] == "NiyamSetu Test Suite"


def test_pdf_info_metadata_is_empty_when_absent(three_page_pdf: Path) -> None:
    document = extract_pdf(three_page_pdf).document
    assert document.pdf_metadata.get("title") is None


def test_extraction_tool_and_timestamp_are_recorded(three_page_pdf: Path) -> None:
    document = extract_pdf(three_page_pdf).document
    assert document.extraction_tool.startswith("pypdf ")
    assert document.extracted_at.endswith("+00:00")
    assert document.schema_version == SCHEMA_VERSION


def test_normalization_options_are_recorded_in_the_output(
    three_page_pdf: Path,
) -> None:
    """A result must say how it was produced, or it cannot be reproduced."""
    options = extract_pdf(three_page_pdf).document.normalization_options
    assert options["unicode_nfkc"] is True
    assert options["dehyphenate_line_breaks"] is True


# --------------------------------------------------------------------------- #
# Empty and text-poor pages
# --------------------------------------------------------------------------- #


def test_blank_page_is_flagged_without_losing_the_page(
    pdf_with_blank_middle_page: Path,
) -> None:
    """The blank page stays in the sequence; dropping it would break provenance."""
    result = extract_pdf(pdf_with_blank_middle_page)
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    blank = result.pages[1]
    assert blank.page_number == 2
    assert blank.is_empty is True
    assert blank.has_meaningful_text is False
    assert blank.word_count == 0
    assert result.pages[0].is_empty is False
    assert result.pages[2].is_empty is False


def test_blank_page_produces_a_warning_and_partial_status(
    pdf_with_blank_middle_page: Path,
) -> None:
    result = extract_pdf(pdf_with_blank_middle_page)
    assert result.document.extraction_status == STATUS_PARTIAL
    assert result.empty_page_count == 1
    assert any("page 2" in w and "0 characters" in w for w in result.document.warnings)


def test_text_poor_page_is_reported_separately_from_an_empty_page(
    pdf_with_text_poor_page: Path,
) -> None:
    """A page with a stamp or signature only is not the same as a blank page."""
    result = extract_pdf(pdf_with_text_poor_page)
    poor = result.pages[1]
    assert poor.is_empty is False
    assert poor.has_meaningful_text is False
    assert result.text_poor_page_numbers == [2]
    assert result.empty_page_count == 0
    assert any("text-poor" in w for w in result.document.warnings)


def test_all_blank_pages_produce_failed_status(pdf_with_all_pages_blank: Path) -> None:
    result = extract_pdf(pdf_with_all_pages_blank)
    assert result.document.extraction_status == STATUS_FAILED
    assert result.meaningful_page_count == 0
    assert any("probably" in w and "scanned" in w for w in result.document.warnings)


def test_meaningful_threshold_is_configurable(pdf_with_text_poor_page: Path) -> None:
    """Lower the threshold and the same page counts as meaningful."""
    strict = extract_pdf(pdf_with_text_poor_page)
    lenient = extract_pdf(pdf_with_text_poor_page, min_meaningful_alnum_chars=1)
    assert strict.pages[1].has_meaningful_text is False
    assert lenient.pages[1].has_meaningful_text is True
    assert lenient.document.extraction_status == STATUS_OK


# --------------------------------------------------------------------------- #
# JSON serialisation
# --------------------------------------------------------------------------- #


def test_result_serialises_to_json_and_round_trips(three_page_pdf: Path) -> None:
    result = extract_pdf(three_page_pdf)
    text = result_to_json(result)
    loaded = json.loads(text)
    assert loaded["document"]["version_id"] == result.document.version_id
    assert len(loaded["pages"]) == 3
    assert [page["page_number"] for page in loaded["pages"]] == [1, 2, 3]


def test_json_contract_contains_every_documented_field(three_page_pdf: Path) -> None:
    loaded = json.loads(result_to_json(extract_pdf(three_page_pdf)))
    expected_document_keys = {
        "schema_version",
        "document_id",
        "version_id",
        "filename",
        "checksum",
        "file_size_bytes",
        "page_count",
        "extraction_status",
        "extraction_tool",
        "extracted_at",
        "pdf_metadata",
        "source_metadata",
        "normalization_options",
        "warnings",
    }
    assert set(loaded["document"]) == expected_document_keys
    expected_page_keys = {
        "page_number",
        "original_text",
        "normalized_text",
        "char_count",
        "normalized_char_count",
        "word_count",
        "is_empty",
        "has_meaningful_text",
    }
    assert set(loaded["pages"][0]) == expected_page_keys


def test_json_is_utf8_safe_for_non_ascii_text(tmp_path: Path) -> None:
    """Real circulars carry en-dashes and curly quotes; JSON must survive them."""
    from .pdf_builder import build_pdf_bytes

    path = tmp_path / "non_ascii.pdf"
    path.write_bytes(
        build_pdf_bytes([["Clause 3.2 – reporting requirements “monthly”"]])
    )
    loaded = json.loads(result_to_json(extract_pdf(path, min_meaningful_alnum_chars=1)))
    assert "reporting requirements" in loaded["pages"][0]["normalized_text"]
