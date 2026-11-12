"""The Experiment B scoring machinery.

These tests check the **scorer**, not the segmenter. They assert nothing about
how well either segmentation method performs — that is a finding in
`evaluation/experiment-b-segmentation.md`, produced by running the scorer on
real annotations, and baking it into a test would make the test a
change-detector and the finding unfalsifiable.

What is asserted here is that the scorer cannot be fooled: a marker that does
not resolve must raise rather than be skipped, a perfect prediction must score
1.0, and a rate with no denominator must come back as ``None`` rather than 0.0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from backend.app.document_processing.segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
    PageOffset,
    Segment,
)
from evaluation.experiment_b_segmentation import (
    AnnotatedClause,
    AnnotationError,
    _f1,
    _ratio,
    annotated_span_text,
    load_annotations,
    locate_annotations,
    main,
    match_boundaries,
    parse_pages,
    score_method,
)

VER = "VER-0123456789abcdef"
DOC = "DOC-fedcba9876543210"

PAGE_ONE = "1. First clause text here.\n\n2. Second clause text here.\n"
PAGES: Dict[int, str] = {1: PAGE_ONE}


def annotation(
    index: int,
    start_marker: str,
    end_marker: str,
    page: int = 1,
    page_end: int | None = None,
    clause_number: str | None = None,
    heading: str | None = None,
    uncertain: bool = False,
    note: str | None = None,
) -> AnnotatedClause:
    return AnnotatedClause(
        version_id=VER,
        clause_index=index,
        page_start=page,
        page_end=page_end if page_end is not None else page,
        text_start_marker=start_marker,
        text_end_marker=end_marker,
        clause_number=clause_number,
        heading=heading,
        uncertain=uncertain,
        note=note,
    )


def segment(
    ordinal: int,
    text: str,
    char_start: int,
    char_end: int,
    page: int = 1,
    page_end: int | None = None,
    clause_number: str | None = None,
    heading: str | None = None,
    extra_offsets: List[PageOffset] | None = None,
) -> Segment:
    offsets = [
        PageOffset(
            page_number=page,
            char_start=char_start,
            char_end=char_end,
            line_index_start=0,
            line_index_end=0,
        )
    ]
    offsets.extend(extra_offsets or [])
    return Segment(
        segment_id=f"SEG-{ordinal:016d}",
        document_id=DOC,
        version_id=VER,
        segmentation_method=METHOD_CLAUSE_AWARE,
        ordinal=ordinal,
        segment_type="clause",
        text=text,
        page_start=page,
        page_end=page_end if page_end is not None else page,
        page_offsets=offsets,
        clause_number=clause_number,
        heading=heading,
    )


# --------------------------------------------------------------------------- #
# Marker resolution - the scorer's integrity depends on this
# --------------------------------------------------------------------------- #


def test_markers_resolve_to_character_positions() -> None:
    records = [annotation(0, "1. First clause", "clause text here.")]
    locate_annotations(records, PAGES)
    assert records[0].resolved_char_start == 0
    assert records[0].resolved_char_end is not None
    assert records[0].resolved_char_end > 0


def test_an_unfindable_marker_raises_rather_than_being_skipped() -> None:
    """Skipping would inflate precision and recall at the same time.

    The skipped clause would disappear from the denominator of recall *and*
    stop counting against precision, so a broken annotation would make the
    system look better rather than worse.
    """
    records = [annotation(0, "This text is not on the page at all", "nor is this")]
    with pytest.raises(AnnotationError, match="does not occur"):
        locate_annotations(records, PAGES)


def test_an_ambiguous_marker_raises() -> None:
    """A marker occurring twice cannot identify a boundary."""
    page = {1: "The clause says X.\n\nThe clause says Y.\n"}
    records = [annotation(0, "The clause says", "says Y.")]
    with pytest.raises(AnnotationError, match="occurs 2 times"):
        locate_annotations(records, page)


def test_a_marker_on_a_page_that_does_not_exist_raises() -> None:
    records = [annotation(0, "1. First clause", "clause text here.", page=9)]
    with pytest.raises(AnnotationError, match="not a page"):
        locate_annotations(records, PAGES)


def test_annotated_span_text_is_reconstructed_from_the_markers() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    assert annotated_span_text(records[0], PAGES) == "1. First clause text here."


def test_annotated_span_text_spans_pages() -> None:
    pages = {1: "4. A clause that runs on\n", 2: "and finishes here.\n"}
    records = [annotation(0, "4. A clause", "finishes here.", page=1, page_end=2)]
    locate_annotations(records, pages)
    span = annotated_span_text(records[0], pages)
    assert "4. A clause that runs on" in span
    assert "and finishes here." in span


def test_reading_a_span_before_locating_raises() -> None:
    with pytest.raises(AnnotationError, match="must run before"):
        annotated_span_text(annotation(0, "a", "b"), PAGES)


# --------------------------------------------------------------------------- #
# File format validation
# --------------------------------------------------------------------------- #


def write_jsonl(path: Path, records: List[Dict[str, object]]) -> Path:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return path


def base_record(index: int = 0, **overrides: object) -> Dict[str, object]:
    record = {
        "version_id": VER,
        "guideline_version": "1.0",
        "annotator": "tester",
        "review_status": "awaiting_owner_review",
        "clause_index": index,
        "page_start": 1,
        "page_end": 1,
        "clause_number": "1",
        "heading": None,
        "text_start_marker": "1. First clause",
        "text_end_marker": "clause text here.",
        "uncertain": False,
        "note": None,
    }
    record.update(overrides)
    return record


def test_a_valid_file_loads(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [base_record(0), base_record(1)])
    records = load_annotations(path)
    assert [record.clause_index for record in records] == [0, 1]
    assert records[0].annotator == "tester"


def test_blank_lines_and_comments_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text(
        f"// a comment\n\n{json.dumps(base_record(0))}\n\n", encoding="utf-8"
    )
    assert len(load_annotations(path)) == 1


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AnnotationError, match="No annotation file"):
        load_annotations(tmp_path / "absent.jsonl")


def test_invalid_json_raises_with_the_line_number(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(AnnotationError, match=":1"):
        load_annotations(path)


def test_a_missing_required_field_raises(tmp_path: Path) -> None:
    record = base_record()
    del record["text_start_marker"]
    path = write_jsonl(tmp_path / "a.jsonl", [record])
    with pytest.raises(AnnotationError, match="text_start_marker"):
        load_annotations(path)


def test_an_unrecognised_field_raises(tmp_path: Path) -> None:
    """A typo'd field name would otherwise be silently ignored."""
    path = write_jsonl(tmp_path / "a.jsonl", [base_record(clasue_number="1")])
    with pytest.raises(AnnotationError, match="unrecognised"):
        load_annotations(path)


def test_non_dense_clause_indices_raise(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [base_record(0), base_record(5)])
    with pytest.raises(AnnotationError, match="dense"):
        load_annotations(path)


def test_a_reversed_page_range_raises(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "a.jsonl", [base_record(page_start=3, page_end=1)])
    with pytest.raises(AnnotationError, match="before"):
        load_annotations(path)


def test_an_uncertain_record_without_a_note_raises(tmp_path: Path) -> None:
    """Guideline §6 requires the note; it is the whole value of the flag."""
    path = write_jsonl(tmp_path / "a.jsonl", [base_record(uncertain=True)])
    with pytest.raises(AnnotationError, match="no note"):
        load_annotations(path)


def test_an_uncertain_record_with_a_note_is_accepted(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "a.jsonl",
        [base_record(uncertain=True, note="table structure lost")],
    )
    assert load_annotations(path)[0].uncertain is True


# --------------------------------------------------------------------------- #
# Boundary matching
# --------------------------------------------------------------------------- #


def test_an_exact_boundary_matches() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1. First clause text here.", 0, 26)]
    assert match_boundaries(records, segments, tolerance=0) == [(0, 0)]


def test_a_boundary_outside_the_tolerance_does_not_match() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [segment(0, "text", 40, 60)]
    assert match_boundaries(records, segments, tolerance=5) == []
    assert match_boundaries(records, segments, tolerance=50) == [(0, 0)]


def test_a_boundary_on_a_different_page_never_matches() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [segment(0, "text", 0, 26, page=2)]
    assert match_boundaries(records, segments, tolerance=1000) == []


def test_each_boundary_is_used_at_most_once() -> None:
    """Two adjacent annotations must not both claim one prediction."""
    records = [
        annotation(0, "1. First clause", "First clause text here."),
        annotation(1, "2. Second clause", "Second clause text here."),
    ]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1. First clause text here.", 0, 26)]
    pairs = match_boundaries(records, segments, tolerance=1000)
    assert len(pairs) == 1
    # The closest annotation wins, not the first one encountered.
    assert pairs[0][0] == 0


def test_the_closest_pairing_wins() -> None:
    records = [annotation(0, "2. Second clause", "Second clause text here.")]
    locate_annotations(records, PAGES)
    true_start = records[0].resolved_char_start
    assert true_start is not None
    segments = [
        segment(0, "far", true_start - 20, true_start - 10),
        segment(1, "near", true_start, true_start + 10),
    ]
    assert match_boundaries(records, segments, tolerance=100) == [(0, 1)]


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def test_a_perfect_prediction_scores_one() -> None:
    records = [
        annotation(0, "1. First clause", "First clause text here.", clause_number="1"),
        annotation(1, "2. Second clause", "Second clause text here.", clause_number="2"),
    ]
    locate_annotations(records, PAGES)
    segments = [
        segment(0, "1. First clause text here.", 0, 26, clause_number="1"),
        segment(1, "2. Second clause text here.", 28, 55, clause_number="2"),
    ]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.boundary_precision == 1.0
    assert score.boundary_recall == 1.0
    assert score.boundary_f1 == 1.0
    assert score.matched == 2
    assert score.missed_clause_indices == []
    assert score.spurious_segment_ordinals == []
    assert score.numbering_preserved == 2
    assert score.to_dict()["numbering_preservation_rate"] == 1.0
    assert score.page_provenance_correct == 2


def test_a_missed_clause_lowers_recall_and_is_listed() -> None:
    records = [
        annotation(0, "1. First clause", "First clause text here."),
        annotation(1, "2. Second clause", "Second clause text here."),
    ]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1. First clause text here.", 0, 26)]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.boundary_recall == 0.5
    assert score.boundary_precision == 1.0
    assert score.missed_clause_indices == [1]


def test_a_spurious_segment_lowers_precision_and_is_listed() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [
        segment(0, "1. First clause text here.", 0, 26),
        segment(1, "2. Second clause text here.", 28, 55),
    ]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.boundary_precision == 0.5
    assert score.boundary_recall == 1.0
    assert score.spurious_segment_ordinals == [1]


def test_exact_text_match_is_whitespace_insensitive() -> None:
    """Line breaks in extracted text are layout, not meaning."""
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1.  First   clause\ntext here.", 0, 26)]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.exact_text_matches == 1


def test_a_merge_is_counted_when_one_segment_covers_two_clauses() -> None:
    records = [
        annotation(0, "1. First clause", "First clause text here."),
        annotation(1, "2. Second clause", "Second clause text here."),
    ]
    locate_annotations(records, PAGES)
    segments = [segment(0, PAGE_ONE.strip(), 0, len(PAGE_ONE))]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.incorrect_merges == 1


def test_a_split_is_counted_when_two_segments_cover_one_clause() -> None:
    records = [annotation(0, "1. First clause", "Second clause text here.")]
    locate_annotations(records, PAGES)
    segments = [
        segment(0, "1. First clause text here.", 0, 26),
        segment(1, "2. Second clause text here.", 28, 55),
    ]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.incorrect_splits == 1


def test_numbering_preservation_counts_only_where_there_is_a_number() -> None:
    records = [
        annotation(0, "1. First clause", "First clause text here.", clause_number="1"),
        annotation(1, "2. Second clause", "Second clause text here.", clause_number=None),
    ]
    locate_annotations(records, PAGES)
    segments = [
        segment(0, "1. First clause text here.", 0, 26, clause_number="1"),
        segment(1, "2. Second clause text here.", 28, 55, clause_number="2"),
    ]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.numbering_opportunities == 1
    assert score.numbering_preserved == 1


def test_a_rate_with_no_opportunity_is_none_not_zero() -> None:
    """"No heading to preserve" and "preserved no headings" are different.

    Reporting the first as 0% would be a fabricated failure.
    """
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1. First clause text here.", 0, 26)]
    score = score_method(METHOD_PARAGRAPH, records, segments, PAGES, 5).to_dict()
    assert score["heading_opportunities"] == 0
    assert score["heading_preservation_rate"] is None
    assert score["numbering_preservation_rate"] is None


def test_a_wrong_clause_number_is_not_counted_as_preserved() -> None:
    records = [
        annotation(0, "1. First clause", "First clause text here.", clause_number="1")
    ]
    locate_annotations(records, PAGES)
    segments = [segment(0, "1. First clause text here.", 0, 26, clause_number="1.1")]
    score = score_method(METHOD_CLAUSE_AWARE, records, segments, PAGES, 5)
    assert score.numbering_opportunities == 1
    assert score.numbering_preserved == 0


def test_page_provenance_requires_both_ends_to_agree() -> None:
    pages = {1: "4. A clause that runs on\n", 2: "and finishes here.\n"}
    records = [annotation(0, "4. A clause", "finishes here.", page=1, page_end=2)]
    locate_annotations(records, pages)
    wrong = [segment(0, "4. A clause that runs on", 0, 24, page=1, page_end=1)]
    assert score_method(METHOD_CLAUSE_AWARE, records, wrong, pages, 5).page_provenance_correct == 0

    right = [
        segment(
            0, "4. A clause that runs on and finishes here.", 0, 24,
            page=1, page_end=2,
            extra_offsets=[
                PageOffset(
                    page_number=2, char_start=0, char_end=19,
                    line_index_start=0, line_index_end=0,
                )
            ],
        )
    ]
    assert score_method(METHOD_CLAUSE_AWARE, records, right, pages, 5).page_provenance_correct == 1


def test_scoring_against_no_predictions_gives_zero_recall_not_a_crash() -> None:
    records = [annotation(0, "1. First clause", "First clause text here.")]
    locate_annotations(records, PAGES)
    score = score_method(METHOD_CLAUSE_AWARE, records, [], PAGES, 5)
    assert score.boundary_recall == 0.0
    assert score.boundary_precision is None  # no predictions = no denominator
    assert score.missed_clause_indices == [0]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_ratio_returns_none_for_a_zero_denominator() -> None:
    assert _ratio(0, 0) is None
    assert _ratio(1, 2) == 0.5


def test_f1_handles_missing_and_zero_inputs() -> None:
    assert _f1(None, 0.5) is None
    assert _f1(0.0, 0.0) is None
    assert _f1(1.0, 1.0) == 1.0
    assert _f1(0.5, 1.0) == pytest.approx(0.6667, abs=1e-4)


@pytest.mark.parametrize(
    "spec,expected",
    [("1-3", [1, 2, 3]), ("2,4,6", [2, 4, 6]), ("1-2,5", [1, 2, 5]), ("7", [7])],
)
def test_parse_pages(spec: str, expected: List[int]) -> None:
    assert parse_pages(spec) == expected


@pytest.mark.parametrize("spec", ["", "0", "0-2", "abc"])
def test_parse_pages_rejects_nonsense(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_pages(spec)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_score_with_no_annotations_refuses_and_says_why(tmp_path: Path) -> None:
    """The central honesty rule: no annotations, no metric."""
    exit_code = main([
        "--annotation-dir", str(tmp_path / "empty"),
        "--processed-dir", str(tmp_path),
        "score",
    ])
    assert exit_code == 1


def test_validate_with_no_annotations_fails(tmp_path: Path) -> None:
    exit_code = main([
        "--annotation-dir", str(tmp_path / "empty"),
        "--processed-dir", str(tmp_path),
        "validate",
    ])
    assert exit_code == 1


# --------------------------------------------------------------------------- #
# The committed annotations, when the regenerable artefacts are present
# --------------------------------------------------------------------------- #

_ANNOTATION_DIR = Path("data/annotations/segmentation")
_PROCESSED_DIR = Path("data/processed")


@pytest.mark.skipif(
    not (_ANNOTATION_DIR.exists() and _PROCESSED_DIR.exists()),
    reason="data/processed is git-ignored; run the extraction and segmentation CLIs",
)
def test_the_committed_annotations_validate() -> None:
    """Every committed marker must still resolve against current extraction.

    This is the regression test that matters for the annotations: a change to
    extraction or normalisation that moves the text will break marker
    resolution, and this fails rather than letting the next scoring run quietly
    drop records.
    """
    exit_code = main([
        "--annotation-dir", str(_ANNOTATION_DIR),
        "--processed-dir", str(_PROCESSED_DIR),
        "validate",
    ])
    assert exit_code == 0


@pytest.mark.skipif(
    not (_ANNOTATION_DIR.exists() and _PROCESSED_DIR.exists()),
    reason="data/processed is git-ignored; run the extraction and segmentation CLIs",
)
def test_scoring_the_committed_annotations_runs_end_to_end(tmp_path: Path) -> None:
    """Required by the roadmap: "evaluation script runs end to end".

    Asserts that it runs and produces a well-formed result for both methods. It
    deliberately does **not** assert any metric value - that belongs in the
    report, where it can be read with its caveats.
    """
    exit_code = main([
        "--annotation-dir", str(_ANNOTATION_DIR),
        "--processed-dir", str(_PROCESSED_DIR),
        "score",
        "--output-dir", str(tmp_path / "out"),
    ])
    assert exit_code == 0
    payload = json.loads(
        (tmp_path / "out" / "results.json").read_text(encoding="utf-8")
    )
    assert payload["versions"]
    assert payload["inter_annotator_agreement"].startswith("PENDING")
    for version in payload["versions"]:
        for method in (METHOD_PARAGRAPH, METHOD_CLAUSE_AWARE):
            score = version["headline_excluding_uncertain"][method]
            assert score["annotated_clauses"] > 0
            assert "boundary_f1" in score
        assert version["annotator"]
        assert version["review_status"]
    for method in (METHOD_PARAGRAPH, METHOD_CLAUSE_AWARE):
        assert method in payload["aggregate"]
