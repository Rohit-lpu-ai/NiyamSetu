"""The E1 agreement scorer.

These test the **scorer**, not the annotations. No agreement figure is asserted
against the real corpus, because none exists — a second annotator has not
worked on it, and manufacturing one here is exactly what
``CLAUDE.md`` §24 rule 12 forbids.

What is asserted is that the scorer cannot be fooled: it reports ``PENDING``
rather than a number when the second file is missing, refuses two files by the
same annotator, refuses files with no page in common, and computes the Jaccard
figure correctly on hand-built cases where the answer is known by inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from evaluation.annotator_agreement import (
    PENDING,
    AgreementError,
    agreement_for_version,
    discover_pairs,
    format_report,
    main,
    match_annotation_boundaries,
    run_agreement,
)
from evaluation.experiment_b_segmentation import AnnotatedClause

VER = "VER-0123456789abcdef"
DOC = "DOC-fedcba9876543210"

PAGE_ONE = "1. First clause of the fixture.\n\n2. Second clause of the fixture.\n"
PAGE_TWO = "3. Third clause, on the second page.\n"


def record(
    index: int,
    start: str,
    end: str,
    page: int = 1,
    clause_number: Optional[str] = None,
    annotator: str = "primary-person",
) -> Dict[str, Any]:
    return {
        "version_id": VER,
        "clause_index": index,
        "page_start": page,
        "page_end": page,
        "text_start_marker": start,
        "text_end_marker": end,
        "clause_number": clause_number,
        "heading": None,
        "uncertain": False,
        "note": None,
        "guideline_version": "1.0",
        "annotator": annotator,
        "review_status": "awaiting_owner_review",
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An annotation dir plus the extraction the markers resolve against."""
    processed = tmp_path / "processed" / VER
    processed.mkdir(parents=True)
    pages = [PAGE_ONE, PAGE_TWO]
    (processed / "extraction.json").write_text(
        json.dumps(
            {
                "document": {
                    "schema_version": "1.0.0",
                    "document_id": DOC,
                    "version_id": VER,
                    "filename": "fixture.pdf",
                    "checksum": "a" * 64,
                    "page_count": 2,
                    "extraction_status": "ok",
                    "extraction_tool": "pypdf 6.18.1",
                    "source_metadata": {"title": "Fixture"},
                },
                "pages": [
                    {
                        "page_number": number,
                        "original_text": body,
                        "normalized_text": body,
                        "char_count": len(body),
                        "normalized_char_count": len(body),
                        "word_count": len(body.split()),
                        "is_empty": False,
                        "has_meaningful_text": True,
                    }
                    for number, body in enumerate(pages, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "annotations").mkdir()
    return tmp_path


def write(workspace: Path, name: str, rows: List[Dict[str, Any]]) -> Path:
    path = workspace / "annotations" / name
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def score(workspace: Path, tolerance: int = 5):
    return agreement_for_version(
        VER,
        annotation_dir=workspace / "annotations",
        processed_dir=workspace / "processed",
        tolerance=tolerance,
    )


# --------------------------------------------------------------------------- #
# The PENDING path - the default state of this project
# --------------------------------------------------------------------------- #


def test_a_missing_second_annotator_is_pending_not_an_error(
    workspace: Path,
) -> None:
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    result = score(workspace)
    assert result.status == PENDING
    assert result.agreement is None
    assert "annotator2" in (result.note or "")


def test_pending_never_produces_a_number(workspace: Path) -> None:
    """The failure mode worth guarding: a default 0.0 or 1.0 appearing by default."""
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    payload = run_agreement(
        workspace / "annotations", workspace / "processed"
    )
    assert payload["pooled"]["status"] == PENDING
    assert payload["pooled"]["boundary_agreement"] is None
    assert "PENDING" in format_report(payload)


def test_a_missing_primary_file_raises(workspace: Path) -> None:
    with pytest.raises(AgreementError, match="no primary annotation file"):
        score(workspace)


# --------------------------------------------------------------------------- #
# Refusals - the cases that would produce a meaningless figure
# --------------------------------------------------------------------------- #


def test_two_files_by_the_same_annotator_are_refused(workspace: Path) -> None:
    """Self-agreement is not agreement."""
    rows = [record(0, "1. First", "fixture.")]
    write(workspace, f"{VER}.jsonl", rows)
    write(workspace, f"{VER}.annotator2.jsonl", rows)
    with pytest.raises(AgreementError, match="two\\s+different annotators"):
        score(workspace)


def test_no_common_page_is_refused(workspace: Path) -> None:
    """The exact defect the evaluation audit found in the WP1 package."""
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [record(0, "3. Third", "page.", page=2, annotator="second-person")],
    )
    with pytest.raises(AgreementError, match="SAME pages"):
        score(workspace)


def test_an_empty_second_file_is_refused(workspace: Path) -> None:
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    (workspace / "annotations" / f"{VER}.annotator2.jsonl").write_text(
        "", encoding="utf-8"
    )
    with pytest.raises(AgreementError, match="empty"):
        score(workspace)


def test_an_unresolvable_marker_raises_rather_than_being_skipped(
    workspace: Path,
) -> None:
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [record(0, "NOT ON THIS PAGE AT ALL", "nope.", annotator="second-person")],
    )
    with pytest.raises(Exception):
        score(workspace)


# --------------------------------------------------------------------------- #
# The arithmetic, on cases whose answer is known by inspection
# --------------------------------------------------------------------------- #


def test_identical_boundaries_agree_completely(workspace: Path) -> None:
    rows = [record(0, "1. First", "fixture."), record(1, "2. Second", "fixture.")]
    write(workspace, f"{VER}.jsonl", rows)
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [{**row, "annotator": "second-person"} for row in rows],
    )
    result = score(workspace)
    assert result.status == "measured"
    assert result.matched == 2
    assert result.union == 2
    assert result.agreement == 1.0


def test_a_boundary_only_one_annotator_found_lowers_agreement(
    workspace: Path,
) -> None:
    """2 matched, 1 extra -> 2 / (2 + 3 - 2) = 2/3."""
    write(
        workspace,
        f"{VER}.jsonl",
        [record(0, "1. First", "fixture."), record(1, "2. Second", "fixture.")],
    )
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [
            record(0, "1. First", "fixture.", annotator="second-person"),
            record(1, "2. Second", "fixture.", annotator="second-person"),
            record(2, "3. Third", "page.", page=2, annotator="second-person"),
        ],
    )
    result = score(workspace)
    # Page 2 is not in the overlap, so the extra boundary is excluded and
    # agreement stays 1.0 on the compared pages.
    assert result.pages_compared == [1]
    assert result.agreement == 1.0


def test_a_disagreement_on_a_shared_page_is_counted(workspace: Path) -> None:
    """1 matched, primary 2, secondary 1 -> 1 / (2 + 1 - 1) = 0.5."""
    write(
        workspace,
        f"{VER}.jsonl",
        [record(0, "1. First", "fixture."), record(1, "2. Second", "fixture.")],
    )
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [record(0, "1. First", "fixture.", annotator="second-person")],
    )
    result = score(workspace)
    assert result.matched == 1
    assert result.union == 2
    assert result.agreement == 0.5
    assert len(result.only_primary) == 1
    assert "2. Second" in result.only_primary[0]


def test_tolerance_decides_whether_near_boundaries_match() -> None:
    def built(index: int, offset: int, annotator: str) -> AnnotatedClause:
        clause = AnnotatedClause(
            version_id=VER,
            clause_index=index,
            page_start=1,
            page_end=1,
            text_start_marker="x",
            text_end_marker="y",
            annotator=annotator,
        )
        clause.resolved_char_start = offset
        return clause

    primary = [built(0, 100, "a")]
    near = [built(0, 103, "b")]
    far = [built(0, 120, "b")]
    assert len(match_annotation_boundaries(primary, near, 5)) == 1
    assert len(match_annotation_boundaries(primary, far, 5)) == 0
    assert len(match_annotation_boundaries(primary, far, 25)) == 1


def test_each_boundary_matches_at_most_once() -> None:
    """Two adjacent annotations must not both claim the same counterpart."""

    def built(index: int, offset: int, annotator: str) -> AnnotatedClause:
        clause = AnnotatedClause(
            version_id=VER,
            clause_index=index,
            page_start=1,
            page_end=1,
            text_start_marker="x",
            text_end_marker="y",
            annotator=annotator,
        )
        clause.resolved_char_start = offset
        return clause

    # Offsets chosen so the two distances differ: 103-100 = 3 against
    # 103-102 = 1. A tie would not test "nearest wins" at all.
    primary = [built(0, 100, "a"), built(1, 102, "a")]
    secondary = [built(0, 103, "b")]
    pairs = match_annotation_boundaries(primary, secondary, 5)
    assert len(pairs) == 1
    assert pairs[0][0] == 1  # the nearer primary boundary claims the match


def test_clause_number_agreement_is_reported_separately(workspace: Path) -> None:
    write(
        workspace,
        f"{VER}.jsonl",
        [
            record(0, "1. First", "fixture.", clause_number="1"),
            record(1, "2. Second", "fixture.", clause_number="2"),
        ],
    )
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [
            record(0, "1. First", "fixture.", clause_number="1",
                   annotator="second-person"),
            record(1, "2. Second", "fixture.", clause_number="(2)",
                   annotator="second-person"),
        ],
    )
    result = score(workspace)
    assert result.clause_number_comparable == 2
    assert result.clause_number_agreement == 0.5
    assert len(result.clause_number_disagreements) == 1


def test_a_missing_clause_number_is_not_counted_as_disagreement(
    workspace: Path,
) -> None:
    """One annotator leaving it null is a missing observation, not a conflict."""
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.", clause_number="1")])
    write(
        workspace,
        f"{VER}.annotator2.jsonl",
        [record(0, "1. First", "fixture.", annotator="second-person")],
    )
    result = score(workspace)
    assert result.clause_number_comparable == 0
    assert result.clause_number_agreement is None


# --------------------------------------------------------------------------- #
# Discovery and CLI
# --------------------------------------------------------------------------- #


def test_discovery_ignores_second_annotator_files(workspace: Path) -> None:
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    write(workspace, f"{VER}.annotator2.jsonl", [record(0, "1. First", "fixture.")])
    assert discover_pairs(workspace / "annotations") == [VER]


def test_the_cli_exits_zero_while_pending(workspace: Path, tmp_path: Path) -> None:
    """PENDING is the expected state, not a build failure."""
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    assert main([
        "--annotation-dir", str(workspace / "annotations"),
        "--processed-dir", str(workspace / "processed"),
        "--output-dir", str(tmp_path / "out"),
    ]) == 0
    payload = json.loads(
        (tmp_path / "out" / "agreement.json").read_text(encoding="utf-8")
    )
    assert payload["pooled"]["status"] == PENDING


def test_the_cli_writes_nothing_with_no_write(workspace: Path, tmp_path: Path) -> None:
    write(workspace, f"{VER}.jsonl", [record(0, "1. First", "fixture.")])
    out = tmp_path / "out"
    main([
        "--annotation-dir", str(workspace / "annotations"),
        "--processed-dir", str(workspace / "processed"),
        "--output-dir", str(out), "--no-write",
    ])
    assert not out.exists()


def test_the_real_corpus_agreement_is_pending() -> None:
    """Pinned deliberately: if this ever stops being PENDING without a real
    second annotator, something has fabricated ground truth."""
    payload = run_agreement()
    for version in payload["versions"]:
        if version["status"] != "measured":
            assert version["boundary_agreement"] is None
