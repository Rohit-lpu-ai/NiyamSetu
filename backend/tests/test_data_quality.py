"""Experiment C data-quality checks.

Each check gets a **positive and a negative fixture case**, as
`backend/docs/backend-roadmap.md` Phase 5 requires: a clean corpus must produce no
error, and a corpus with the specific defect must produce exactly that error.
A check that only ever ran against good data would pass whether or not it
worked.

The critical checks — invalid page references, malformed identifiers, duplicate
segment text, irreversible cleaning — are asserted here precisely so a
regression fails the test suite rather than quietly degrading the corpus
(``data/README.md`` §8).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List

import pytest

from evaluation.data_quality import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    QualityReport,
    format_report,
    main,
    run_checks,
)

VERSION_ID = "VER-0123456789abcdef"
DOCUMENT_ID = "DOC-fedcba9876543210"
SEGMENT_A = "SEG-aaaaaaaaaaaaaaaa"
SEGMENT_B = "SEG-bbbbbbbbbbbbbbbb"

PAGE_ONE = "1. First clause of the fixture document, long enough to be real.\n"
PAGE_TWO = "2. Second clause of the fixture document, also long enough.\n"


def _extraction() -> Dict[str, object]:
    pages = [PAGE_ONE, PAGE_TWO]
    return {
        "document": {
            "schema_version": "1.0.0",
            "document_id": DOCUMENT_ID,
            "version_id": VERSION_ID,
            "filename": "fixture.pdf",
            "checksum": "a" * 64,
            "file_size_bytes": 2048,
            "page_count": 2,
            "extraction_status": "ok",
            "extraction_tool": "pypdf 6.18.1",
            "extracted_at": "2026-11-12T00:00:00+00:00",
            "pdf_metadata": {},
            "source_metadata": {
                "title": "Fixture Directions",
                "source_url": "https://example.gov.in/fixture.pdf",
                "retrieved_at": "2026-11-12",
                "document_group": "fixture-group",
                "version_label": "v1",
                "issuing_department": "Fixture Department",
                "document_type": "circular",
                "reference_no": "FIX/1/2026",
                "publication_date": "2026-11-01",
                "supersedes": None,
            },
            "normalization_options": {},
            "warnings": [],
        },
        "pages": [
            {
                "page_number": index,
                "original_text": text,
                "normalized_text": text,
                "char_count": len(text),
                "normalized_char_count": len(text),
                "word_count": len(text.split()),
                "is_empty": False,
                "has_meaningful_text": True,
            }
            for index, text in enumerate(pages, start=1)
        ],
    }


def _segment(
    segment_id: str, ordinal: int, page: int, text: str, char_end: int
) -> Dict[str, object]:
    return {
        "segment_id": segment_id,
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "segmentation_method": "clause_aware",
        "ordinal": ordinal,
        "segment_type": "clause",
        "clause_number": str(ordinal + 1),
        "heading": None,
        "text": text,
        "page_start": page,
        "page_end": page,
        "page_offsets": [
            {
                "page_number": page,
                "char_start": 0,
                "char_end": char_end,
                "line_index_start": 0,
                "line_index_end": 0,
            }
        ],
        "parent_segment_id": None,
        "level": 1,
        "metadata": {},
    }


def _segments_payload(method: str) -> Dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "generated_at": "2026-11-12T00:00:00+00:00",
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "segmentation_method": method,
        "offset_basis": "normalized_text",
        "segment_count": 2,
        "stats": {},
        "warnings": [],
        "segments": [
            _segment(SEGMENT_A, 0, 1, PAGE_ONE.strip(), len(PAGE_ONE) - 1),
            _segment(SEGMENT_B, 1, 2, PAGE_TWO.strip(), len(PAGE_TWO) - 1),
        ],
    }


def _cleaning() -> Dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "offset_basis": "normalized_text",
        "source_text_field": "normalized_text",
        "plan": {"page_count": 2, "options": {}, "warnings": []},
        "pages": [
            {
                "page_number": 1,
                "cleaned_text": PAGE_ONE,
                "removed_line_count": 1,
                "removed_lines": [
                    {
                        "page_number": 1,
                        "line_index": 0,
                        "text": "A RUNNING HEADER",
                        "reason": "repeated_header",
                        "evidence": "repeated at top position 0 on 2/2 pages",
                    }
                ],
                "line_offset_map": [1, 2],
            }
        ],
        "totals": {
            "pages": 2,
            "removed_lines": 1,
            "pages_with_removals": 1,
            "reversible": True,
        },
    }


MANIFEST_HEADER = (
    "local_filename,original_filename,sha256,title,department,document_type,"
    "reference_no,publication_date,source_url,retrieved_at,document_group,"
    "version_label,supersedes,is_scanned,notes\n"
)
MANIFEST_ROW_V1 = (
    f"fixture.pdf,FIXTURE.PDF,{'a' * 64},Fixture Directions,Fixture Department,"
    "circular,FIX/1/2026,2026-11-01,https://example.gov.in/fixture.pdf,"
    "2026-11-12,fixture-group,v1,,false,\n"
)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A clean, complete one-version corpus. The positive case for every check."""
    processed = tmp_path / "processed"
    version_dir = processed / VERSION_ID
    version_dir.mkdir(parents=True)
    (version_dir / "extraction.json").write_text(
        json.dumps(_extraction()), encoding="utf-8"
    )
    (version_dir / "segments_paragraph.json").write_text(
        json.dumps(_segments_payload("paragraph")), encoding="utf-8"
    )
    (version_dir / "segments_clause_aware.json").write_text(
        json.dumps(_segments_payload("clause_aware")), encoding="utf-8"
    )
    (version_dir / "page_cleaning.json").write_text(
        json.dumps(_cleaning()), encoding="utf-8"
    )
    (tmp_path / "manifest.csv").write_text(
        MANIFEST_HEADER + MANIFEST_ROW_V1, encoding="utf-8"
    )
    return tmp_path


def run(corpus_root: Path) -> QualityReport:
    return run_checks(corpus_root / "processed", corpus_root / "manifest.csv")


def mutate_segments(
    corpus_root: Path, mutate: Callable[[Dict[str, object]], None]
) -> None:
    path = corpus_root / "processed" / VERSION_ID / "segments_clause_aware.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def mutate_extraction(
    corpus_root: Path, mutate: Callable[[Dict[str, object]], None]
) -> None:
    path = corpus_root / "processed" / VERSION_ID / "extraction.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def errors_for(report: QualityReport, check: str) -> List[str]:
    """Return each error finding as its message plus its subjects, as one string.

    Both halves matter when asserting. A finding's ``message`` names the class
    of defect ("segment character offset is negative, reversed, or past the end
    of its page") while its ``subjects`` carry the specific instance ("... p1:
    char_end 99999 beyond the page's 64 characters"). Searching only the message
    makes a test silently unable to distinguish which branch fired.
    """
    return [
        " | ".join([finding.message, *finding.subjects])
        for finding in report.findings
        if finding.check == check and finding.severity == SEVERITY_ERROR
    ]


# --------------------------------------------------------------------------- #
# The positive case
# --------------------------------------------------------------------------- #


def test_a_clean_corpus_produces_no_errors(corpus: Path) -> None:
    report = run(corpus)
    assert report.error_count == 0, [f.to_dict() for f in report.findings]
    assert report.versions_inspected == [VERSION_ID]
    assert len(report.checks_run) >= 11
    assert report.checks_not_applicable == {}


def test_the_report_serialises_and_renders(corpus: Path) -> None:
    report = run(corpus)
    assert json.loads(json.dumps(report.to_dict()))
    rendered = format_report(report)
    assert "data-quality report" in rendered
    assert "never repairs" in rendered or "report only" in rendered


def test_checks_report_not_applicable_rather_than_passing_vacuously(
    corpus: Path,
) -> None:
    """A check with no input must say so. A vacuous pass is worse than no check."""
    version_dir = corpus / "processed" / VERSION_ID
    (version_dir / "segments_paragraph.json").unlink()
    (version_dir / "segments_clause_aware.json").unlink()
    (version_dir / "page_cleaning.json").unlink()

    report = run(corpus)
    assert "segment_provenance" in report.checks_not_applicable
    assert "segment_structure" in report.checks_not_applicable
    assert "cleaning_reversibility" in report.checks_not_applicable
    assert "segmentation_consistency" in report.checks_not_applicable
    assert "segment_provenance" not in report.checks_run


def test_an_empty_processed_dir_warns_rather_than_crashing(tmp_path: Path) -> None:
    report = run_checks(tmp_path / "nothing", None)
    assert report.error_count == 0
    assert any(finding.severity == SEVERITY_WARNING for finding in report.findings)


def test_nothing_is_written_by_run_checks(corpus: Path) -> None:
    """The module has no write path; this pins that."""
    before = {
        path: path.read_bytes()
        for path in (corpus / "processed").rglob("*.json")
    }
    run(corpus)
    for path, content in before.items():
        assert path.read_bytes() == content


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def test_a_missing_document_id_is_an_error(corpus: Path) -> None:
    mutate_extraction(corpus, lambda p: p["document"].update(document_id=""))
    assert any("no document_id" in m for m in errors_for(run(corpus), "identifiers"))


@pytest.mark.parametrize("bad", ["DOC-123", "doc-0123456789abcdef", "DOC-ZZZZ456789abcdef"])
def test_a_malformed_document_id_is_an_error(corpus: Path, bad: str) -> None:
    mutate_extraction(corpus, lambda p: p["document"].update(document_id=bad))
    assert any("DOC-" in m for m in errors_for(run(corpus), "identifiers"))


def test_a_malformed_version_id_is_an_error(corpus: Path) -> None:
    mutate_extraction(corpus, lambda p: p["document"].update(version_id="VER-oops"))
    assert any("VER-" in m for m in errors_for(run(corpus), "identifiers"))


def test_a_directory_name_not_matching_its_version_id_is_an_error(
    corpus: Path,
) -> None:
    """The directory name is how every other tool finds a version."""
    mutate_extraction(
        corpus, lambda p: p["document"].update(version_id="VER-ffffffffffffffff")
    )
    assert any(
        "directory name" in m for m in errors_for(run(corpus), "identifiers")
    )


def test_a_malformed_segment_id_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][0].update(segment_id="CLS-1"))
    assert any("SEG-" in m for m in errors_for(run(corpus), "identifiers"))


def test_a_malformed_checksum_is_an_error(corpus: Path) -> None:
    mutate_extraction(corpus, lambda p: p["document"].update(checksum="abc"))
    assert any("checksum" in m for m in errors_for(run(corpus), "identifiers"))


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


def test_a_page_sequence_gap_is_an_error(corpus: Path) -> None:
    mutate_extraction(corpus, lambda p: p["pages"][1].update(page_number=3))
    assert any("dense 1-based" in m for m in errors_for(run(corpus), "pages"))


def test_a_page_count_mismatch_is_an_error(corpus: Path) -> None:
    mutate_extraction(corpus, lambda p: p["document"].update(page_count=9))
    assert any("page_count" in m for m in errors_for(run(corpus), "pages"))


def test_an_empty_page_is_a_warning_not_an_error(corpus: Path) -> None:
    """A scanned page is a documented limitation, not a broken invariant."""
    mutate_extraction(corpus, lambda p: p["pages"][0].update(is_empty=True))
    report = run(corpus)
    assert errors_for(report, "pages") == []
    assert any(
        finding.check == "pages" and finding.severity == SEVERITY_WARNING
        for finding in report.findings
    )


# --------------------------------------------------------------------------- #
# Segment provenance - the load-bearing invariant
# --------------------------------------------------------------------------- #


def test_a_segment_with_no_page_offsets_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][0].update(page_offsets=[]))
    assert any(
        "no page_offsets" in m for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_page_reference_outside_the_document_is_an_error(corpus: Path) -> None:
    def mutate(payload: Dict[str, object]) -> None:
        segment = payload["segments"][0]  # type: ignore[index]
        segment["page_start"] = 9
        segment["page_end"] = 9
        segment["page_offsets"][0]["page_number"] = 9

    mutate_segments(corpus, mutate)
    assert any(
        "outside 1..page_count" in m
        for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_reversed_page_range_is_an_error(corpus: Path) -> None:
    def mutate(payload: Dict[str, object]) -> None:
        segment = payload["segments"][1]  # type: ignore[index]
        segment["page_start"] = 2
        segment["page_end"] = 1

    mutate_segments(corpus, mutate)
    assert any(
        "precedes" in m for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_page_range_disagreeing_with_its_offsets_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][0].update(page_end=2))
    assert any(
        "disagrees" in m for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_character_offset_past_the_end_of_the_page_is_an_error(
    corpus: Path,
) -> None:
    mutate_segments(
        corpus, lambda p: p["segments"][0]["page_offsets"][0].update(char_end=99999)
    )
    assert any(
        "beyond the page" in m
        for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_reversed_character_offset_is_an_error(corpus: Path) -> None:
    def mutate(payload: Dict[str, object]) -> None:
        offset = payload["segments"][0]["page_offsets"][0]  # type: ignore[index]
        offset["char_start"] = 50
        offset["char_end"] = 10

    mutate_segments(corpus, mutate)
    assert any(
        "reversed" in m for m in errors_for(run(corpus), "segment_provenance")
    )


def test_a_page_spanning_segment_with_one_offset_is_an_error(corpus: Path) -> None:
    """``CLAUDE.md`` §11.4: a spanning clause is never collapsed onto one page."""

    def mutate(payload: Dict[str, object]) -> None:
        segment = payload["segments"][0]  # type: ignore[index]
        segment["page_start"] = 1
        segment["page_end"] = 2
        # Only one offset entry, claiming a two-page span.

    mutate_segments(corpus, mutate)
    assert any(
        "fewer than two page_offsets" in m
        for m in errors_for(run(corpus), "segment_provenance")
    )


def test_an_empty_segment_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][0].update(text="   "))
    assert any(
        "empty text" in m for m in errors_for(run(corpus), "segment_provenance")
    )


# --------------------------------------------------------------------------- #
# Segment structure
# --------------------------------------------------------------------------- #


def test_non_dense_ordinals_are_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][1].update(ordinal=7))
    assert any("ordinals" in m for m in errors_for(run(corpus), "segment_structure"))


def test_a_duplicate_segment_id_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][1].update(segment_id=SEGMENT_A))
    assert any(
        "duplicate segment_id" in m
        for m in errors_for(run(corpus), "segment_structure")
    )


def test_a_dangling_parent_pointer_is_an_error(corpus: Path) -> None:
    mutate_segments(
        corpus,
        lambda p: p["segments"][1].update(parent_segment_id="SEG-cccccccccccccccc"),
    )
    assert any(
        "does not exist" in m for m in errors_for(run(corpus), "segment_structure")
    )


def test_a_self_parent_is_an_error(corpus: Path) -> None:
    mutate_segments(
        corpus, lambda p: p["segments"][1].update(parent_segment_id=SEGMENT_B)
    )
    assert any(
        "its own parent" in m for m in errors_for(run(corpus), "segment_structure")
    )


def test_a_declared_method_not_matching_the_filename_is_an_error(
    corpus: Path,
) -> None:
    mutate_segments(corpus, lambda p: p.update(segmentation_method="paragraph"))
    assert any(
        "does not match its filename" in m
        for m in errors_for(run(corpus), "segment_structure")
    )


def test_duplicate_segment_text_is_a_warning(corpus: Path) -> None:
    """Segmentation emitted the same text twice (``data/README.md`` §8)."""
    long_text = "The same long clause text repeated verbatim, well over eighty characters in length."
    mutate_segments(
        corpus,
        lambda p: [
            p["segments"][0].update(text=long_text),
            p["segments"][1].update(text=long_text),
        ],
    )
    report = run(corpus)
    assert any(
        finding.check == "segment_structure"
        and finding.severity == SEVERITY_WARNING
        and "identical segment text" in finding.message
        for finding in report.findings
    )


def test_short_repeated_segment_text_is_not_flagged(corpus: Path) -> None:
    """A one-word "Annexure" heading repeating is expected, not a defect."""
    mutate_segments(
        corpus,
        lambda p: [
            p["segments"][0].update(text="Annexure"),
            p["segments"][1].update(text="Annexure"),
        ],
    )
    report = run(corpus)
    assert not any(
        "identical segment text" in finding.message for finding in report.findings
    )


def test_one_clause_covering_the_whole_document_is_a_warning(tmp_path: Path) -> None:
    """``data/README.md`` §8's "segmentation anomaly" case."""
    processed = tmp_path / "processed"
    version_dir = processed / VERSION_ID
    version_dir.mkdir(parents=True)
    extraction = _extraction()
    extraction["document"]["page_count"] = 10  # type: ignore[index]
    extraction["pages"] = [  # type: ignore[index]
        {
            "page_number": index,
            "original_text": f"Page {index} text.\n",
            "normalized_text": f"Page {index} text.\n",
            "char_count": 20,
            "normalized_char_count": 20,
            "word_count": 4,
            "is_empty": False,
            "has_meaningful_text": True,
        }
        for index in range(1, 11)
    ]
    (version_dir / "extraction.json").write_text(
        json.dumps(extraction), encoding="utf-8"
    )
    payload = _segments_payload("clause_aware")
    payload["segments"] = [  # type: ignore[index]
        {
            **_segment(SEGMENT_A, 0, 1, "One clause to rule them all.", 18),
            "page_start": 1,
            "page_end": 10,
            "page_offsets": [
                {
                    "page_number": index,
                    "char_start": 0,
                    "char_end": 18,
                    "line_index_start": 0,
                    "line_index_end": 0,
                }
                for index in range(1, 11)
            ],
        }
    ]
    (version_dir / "segments_clause_aware.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    report = run_checks(processed, None)
    assert any(
        "almost the whole document" in finding.message
        for finding in report.findings
    )


# --------------------------------------------------------------------------- #
# Cross-method consistency and cleaning
# --------------------------------------------------------------------------- #


def test_methods_disagreeing_about_the_version_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p.update(offset_basis="original_text"))
    assert any(
        "disagree about what they segmented" in m
        for m in errors_for(run(corpus), "segmentation_consistency")
    )


def test_one_method_producing_nothing_is_an_error(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p.update(segments=[]))
    assert any(
        "produced nothing" in m
        for m in errors_for(run(corpus), "segmentation_consistency")
    )


def test_irreversible_cleaning_is_an_error(corpus: Path) -> None:
    """The one check that would mean source evidence has been lost."""
    path = corpus / "processed" / VERSION_ID / "page_cleaning.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["totals"]["reversible"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert any(
        "not reversible" in m
        for m in errors_for(run(corpus), "cleaning_reversibility")
    )


def test_a_removed_line_without_evidence_is_an_error(corpus: Path) -> None:
    path = corpus / "processed" / VERSION_ID / "page_cleaning.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pages"][0]["removed_lines"][0]["evidence"] = ""
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert any(
        "no reason or evidence" in m
        for m in errors_for(run(corpus), "cleaning_reversibility")
    )


def test_unparseable_json_is_an_error(corpus: Path) -> None:
    (corpus / "processed" / VERSION_ID / "segments_paragraph.json").write_text(
        "{broken", encoding="utf-8"
    )
    assert any(
        "could not be parsed" in m
        for m in errors_for(run(corpus), "artifact_readability")
    )


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


def test_a_duplicate_manifest_checksum_is_an_error(corpus: Path) -> None:
    (corpus / "manifest.csv").write_text(
        MANIFEST_HEADER + MANIFEST_ROW_V1 + MANIFEST_ROW_V1.replace(
            "fixture.pdf", "fixture-copy.pdf"
        ),
        encoding="utf-8",
    )
    assert any("same sha256" in m for m in errors_for(run(corpus), "manifest"))


def test_a_self_superseding_version_is_an_error(corpus: Path) -> None:
    row = MANIFEST_ROW_V1.replace("fixture-group,v1,,false", "fixture-group,v1,v1,false")
    (corpus / "manifest.csv").write_text(MANIFEST_HEADER + row, encoding="utf-8")
    assert any("supersedes itself" in m for m in errors_for(run(corpus), "manifest"))


def test_supersedes_naming_an_unknown_label_is_an_error(corpus: Path) -> None:
    row = MANIFEST_ROW_V1.replace(
        "fixture-group,v1,,false", "fixture-group,v1,v0-nonexistent,false"
    )
    (corpus / "manifest.csv").write_text(MANIFEST_HEADER + row, encoding="utf-8")
    assert any(
        "not a version_label" in m for m in errors_for(run(corpus), "manifest")
    )


def test_a_corpus_with_no_version_pair_is_warned_about(corpus: Path) -> None:
    """The real corpus's actual state, and Person 4's blocker (D-10)."""
    report = run(corpus)
    assert any(
        "no document_group has two or more versions" in finding.message
        for finding in report.findings
    )


def test_a_corpus_with_a_version_pair_is_not_warned_about(corpus: Path) -> None:
    second = MANIFEST_ROW_V1.replace("fixture.pdf", "fixture-v2.pdf").replace(
        "a" * 64, "b" * 64
    ).replace("fixture-group,v1,,false", "fixture-group,v2,v1,false")
    (corpus / "manifest.csv").write_text(
        MANIFEST_HEADER + MANIFEST_ROW_V1 + second, encoding="utf-8"
    )
    report = run(corpus)
    assert not any(
        "no document_group has two or more versions" in finding.message
        for finding in report.findings
    )
    assert errors_for(report, "manifest") == []


def test_a_missing_mandatory_metadata_field_is_an_error(corpus: Path) -> None:
    mutate_extraction(
        corpus, lambda p: p["document"]["source_metadata"].update(title=None)
    )
    assert any(
        "mandatory" in m for m in errors_for(run(corpus), "metadata_completeness")
    )


def test_a_missing_optional_metadata_field_is_only_info(corpus: Path) -> None:
    """"Empty is better than wrong" (``data/README.md`` §4)."""
    mutate_extraction(
        corpus,
        lambda p: p["document"]["source_metadata"].update(issuing_department=None),
    )
    report = run(corpus)
    assert errors_for(report, "metadata_completeness") == []
    assert any(
        finding.check == "metadata_completeness"
        and finding.severity == SEVERITY_INFO
        for finding in report.findings
    )


def test_a_failed_extraction_is_an_error(corpus: Path) -> None:
    mutate_extraction(
        corpus, lambda p: p["document"].update(extraction_status="failed")
    )
    assert any("unusable" in m for m in errors_for(run(corpus), "extraction_status"))


def test_a_partial_extraction_is_a_warning(corpus: Path) -> None:
    mutate_extraction(
        corpus, lambda p: p["document"].update(extraction_status="partial")
    )
    report = run(corpus)
    assert errors_for(report, "extraction_status") == []
    assert any(
        finding.check == "extraction_status"
        and finding.severity == SEVERITY_WARNING
        for finding in report.findings
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_exits_zero_on_a_clean_corpus_and_writes_both_files(
    corpus: Path,
) -> None:
    output = corpus / "out"
    exit_code = main([
        "--processed-dir", str(corpus / "processed"),
        "--manifest", str(corpus / "manifest.csv"),
        "--output-dir", str(output),
    ])
    assert exit_code == 0
    assert json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert "data-quality report" in (output / "summary.txt").read_text(
        encoding="utf-8"
    )


def test_cli_exits_non_zero_when_an_error_is_found(corpus: Path) -> None:
    mutate_segments(corpus, lambda p: p["segments"][0].update(page_offsets=[]))
    exit_code = main([
        "--processed-dir", str(corpus / "processed"),
        "--manifest", str(corpus / "manifest.csv"),
        "--output-dir", str(corpus / "out"),
        "--no-write",
    ])
    assert exit_code == 1


def test_cli_fail_on_warning_turns_a_warning_into_a_failure(corpus: Path) -> None:
    exit_code = main([
        "--processed-dir", str(corpus / "processed"),
        "--manifest", str(corpus / "manifest.csv"),
        "--no-write",
        "--fail-on-warning",
    ])
    # The clean fixture still carries the "no version pair" warning.
    assert exit_code == 1


def test_cli_no_manifest_skips_the_manifest_checks(corpus: Path) -> None:
    exit_code = main([
        "--processed-dir", str(corpus / "processed"),
        "--no-manifest",
        "--no-write",
    ])
    assert exit_code == 0


# --------------------------------------------------------------------------- #
# The real corpus, when it is present
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (Path("data/processed").exists() and any(Path("data/processed").iterdir())),
    reason="data/processed is git-ignored; run the extraction and segmentation CLIs",
)
def test_the_real_corpus_has_no_data_quality_errors() -> None:
    """Runs only when the regenerable artefacts are present locally.

    Skipped in a fresh clone rather than failing, because ``data/processed`` is
    git-ignored by design (``data/README.md`` §1). When it is there, this is the
    check that a pipeline change has not broken the actual corpus.
    """
    report = run_checks(Path("data/processed"), Path("data/manifest.csv"))
    assert report.error_count == 0, [
        finding.to_dict()
        for finding in report.findings
        if finding.severity == SEVERITY_ERROR
    ]


# --------------------------------------------------------------------------- #
# Offset resolution - can a stored offset be turned back into the clause?
# --------------------------------------------------------------------------- #
#
# The provenance checks above assert the offsets are well formed. These assert
# they are *usable*: that slicing the page at the stored range returns the
# segment. That is the operation a citation performs, so these are the tests
# standing between the project and a citation that points at the wrong text.


def test_a_clean_corpus_resolves_every_offset(corpus: Path) -> None:
    report = run(corpus)
    assert not errors_for(report, "offset_resolution")
    assert any(
        finding.check == "offset_resolution"
        and finding.severity == SEVERITY_INFO
        and "rebuild exactly" in finding.message
        for finding in report.findings
    )


def test_an_offset_shifted_off_by_one_is_an_error(corpus: Path) -> None:
    """The defect a bounds check cannot see: legal range, wrong characters."""

    def mutate(payload: Dict[str, object]) -> None:
        offset = payload["segments"][0]["page_offsets"][0]  # type: ignore[index]
        offset["char_start"] = int(offset["char_start"]) + 1

    mutate_segments(corpus, mutate)
    assert any(
        "cannot be rebuilt" in m
        for m in errors_for(run(corpus), "offset_resolution")
    )


def test_text_edited_away_from_its_offsets_is_an_error(corpus: Path) -> None:
    mutate_segments(
        corpus, lambda p: p["segments"][0].update(text="text that was never on the page")
    )
    assert any(
        "cannot be rebuilt" in m
        for m in errors_for(run(corpus), "offset_resolution")
    )


def test_a_declared_offset_basis_other_than_normalized_is_an_error(
    corpus: Path,
) -> None:
    """D-11 is a contract, so a file quietly asserting the other basis fails."""
    mutate_segments(corpus, lambda p: p.update(offset_basis="original_text"))
    assert any(
        "offset_basis" in m for m in errors_for(run(corpus), "offset_resolution")
    )


def test_an_offset_naming_a_page_that_was_never_extracted_is_an_error(
    corpus: Path,
) -> None:
    def mutate(payload: Dict[str, object]) -> None:
        pages = payload["pages"]  # type: ignore[index]
        payload["pages"] = pages[:1]
        payload["document"]["page_count"] = 1  # type: ignore[index]

    mutate_extraction(corpus, mutate)
    report = run(corpus)
    assert any(
        "not in extraction.json" in m
        for m in errors_for(report, "offset_resolution")
    )


def test_whitespace_reflow_alone_is_tolerated(corpus: Path) -> None:
    """A segment joins wrapped lines with a space; that must not be a defect.

    Pinned deliberately: if someone later tightens the comparison to exact
    equality, every real multi-line clause in the corpus starts failing, and
    this test says why that tolerance exists.
    """

    def mutate(payload: Dict[str, object]) -> None:
        segment = payload["segments"][0]  # type: ignore[index]
        segment["text"] = str(segment["text"]).replace(" ", "\n  ")

    mutate_segments(corpus, mutate)
    assert not errors_for(run(corpus), "offset_resolution")
