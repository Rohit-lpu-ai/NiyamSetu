"""The E2 cleaning-correctness scorer.

As with E1, these test the **scorer**, not the cleaning. No precision or recall
is asserted against the real corpus, because no human has labelled it. What is
asserted is that the scorer reports `PENDING` rather than a number when labels
are absent, counts by occurrence rather than by distinct string, and reports
content destruction as an absolute count that cannot be diluted into a
percentage.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from evaluation.cleaning_correctness import (
    LABELLED_NAME,
    LABELS,
    TEMPLATE_HEADER,
    TEMPLATE_NAME,
    CleaningLabelError,
    LineCandidate,
    build_candidates,
    load_labels,
    main,
    run,
    score_cleaning,
    write_template,
)

VER = "VER-0123456789abcdef"

HEADER_LINE = "RESERVE BANK OF INDIA"
CONTENT_LINE = "1. These directions apply to every scheduled bank."
TAIL_LINE = "Yours faithfully, Chief General Manager"


def _cleaning_payload(pages: int = 3) -> Dict[str, Any]:
    """A cleaning report where a header was removed from every page."""
    return {
        "schema_version": "1.0.0",
        "document_id": "DOC-fedcba9876543210",
        "version_id": VER,
        "offset_basis": "normalized_text",
        "source_text_field": "normalized_text",
        "plan": {"page_count": pages, "options": {}, "warnings": []},
        "pages": [
            {
                "page_number": number,
                "cleaned_text": f"{CONTENT_LINE}\nbody of page {number}\n{TAIL_LINE}",
                "removed_line_count": 1,
                "removed_lines": [
                    {
                        "page_number": number,
                        "line_index": 0,
                        "text": HEADER_LINE,
                        "reason": "repeated_header",
                        "evidence": f"repeated at top on {pages}/{pages} pages (100%)",
                    }
                ],
                "line_offset_map": [],
            }
            for number in range(1, pages + 1)
        ],
        "totals": {
            "pages": pages,
            "removed_lines": pages,
            "pages_with_removals": pages,
            "reversible": True,
        },
    }


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    root = tmp_path / "processed" / VER
    root.mkdir(parents=True)
    (root / "page_cleaning.json").write_text(
        json.dumps(_cleaning_payload()), encoding="utf-8"
    )
    return tmp_path / "processed"


def _label_file(path: Path, rows: List[Dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEMPLATE_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in TEMPLATE_HEADER})
    return path


# --------------------------------------------------------------------------- #
# Candidate building
# --------------------------------------------------------------------------- #


def test_removed_lines_are_grouped_by_distinct_string(processed: Path) -> None:
    """One header on 3 pages is ONE labelling decision, not three."""
    candidates = build_candidates(processed)
    removed = [c for c in candidates if c.disposition == "removed"]
    assert len(removed) == 1
    assert removed[0].text == HEADER_LINE
    assert removed[0].occurrences == 3


def test_retained_edge_lines_are_offered_for_missed_boilerplate(
    processed: Path,
) -> None:
    """Without these, recall would be 1.0 by construction."""
    candidates = build_candidates(processed)
    retained = {c.text for c in candidates if c.disposition == "retained_edge"}
    assert CONTENT_LINE in retained
    assert TAIL_LINE in retained


def test_a_one_off_page_1_edge_line_is_kept_for_review(tmp_path: Path) -> None:
    """Experiment B section 3.9: a page-1-only letterhead survives cleaning.

    Filtering retained lines to 'repeats on 2+ pages' would hide exactly that
    failure, so page 1 is always offered.
    """
    payload = _cleaning_payload()
    payload["pages"][0]["cleaned_text"] = (
        f"ONE OFF LETTERHEAD\n{CONTENT_LINE}\nbody\n{TAIL_LINE}"
    )
    root = tmp_path / "processed" / VER
    root.mkdir(parents=True)
    (root / "page_cleaning.json").write_text(json.dumps(payload), encoding="utf-8")

    texts = {c.text for c in build_candidates(tmp_path / "processed")}
    assert "ONE OFF LETTERHEAD" in texts


def test_blank_lines_are_not_labelling_decisions(processed: Path) -> None:
    assert all(c.text.strip() for c in build_candidates(processed))


def test_a_missing_processed_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(CleaningLabelError, match="not found"):
        build_candidates(tmp_path / "nope")


# --------------------------------------------------------------------------- #
# The PENDING path
# --------------------------------------------------------------------------- #


def test_without_labels_the_result_is_pending_with_no_numbers(
    processed: Path, tmp_path: Path
) -> None:
    payload = run(processed, tmp_path / "labels")
    assert payload["status"].startswith("PENDING")
    assert payload["scores"] is None
    assert payload["removed_occurrences"] == 3


def test_the_template_round_trips_through_csv(
    processed: Path, tmp_path: Path
) -> None:
    path = write_template(build_candidates(processed), tmp_path / TEMPLATE_NAME)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows and all(row["label"] == "" for row in rows)
    assert {"disposition", "line_text", "pipeline_evidence"} <= set(rows[0])


def test_an_unlabelled_file_is_refused(processed: Path, tmp_path: Path) -> None:
    path = write_template(build_candidates(processed), tmp_path / LABELLED_NAME)
    with pytest.raises(CleaningLabelError, match="no labelled rows"):
        load_labels(path)


def test_an_invalid_label_is_refused(processed: Path, tmp_path: Path) -> None:
    _label_file(
        tmp_path / LABELLED_NAME,
        [{"label": "probably-a-header", "disposition": "removed",
          "line_text": HEADER_LINE}],
    )
    with pytest.raises(CleaningLabelError, match="is not one of"):
        load_labels(tmp_path / LABELLED_NAME)


def test_a_missing_column_is_refused(tmp_path: Path) -> None:
    path = tmp_path / LABELLED_NAME
    path.write_text("label,line_text\nheader,x\n", encoding="utf-8")
    with pytest.raises(CleaningLabelError, match="missing column"):
        load_labels(path)


# --------------------------------------------------------------------------- #
# Scoring arithmetic
# --------------------------------------------------------------------------- #


def _candidate(text: str, disposition: str, occurrences: int) -> LineCandidate:
    return LineCandidate(
        text=text, disposition=disposition, occurrences=occurrences,
        pages=[1] * occurrences, line_indices=[0] * occurrences,
    )


def test_a_correct_removal_scores_precision_one() -> None:
    candidates = [_candidate(HEADER_LINE, "removed", 3)]
    scores = score_cleaning(candidates, {("removed", HEADER_LINE): "header"})
    assert scores["removal_precision"] == 1.0
    assert scores["content_destruction_count"] == 0


def test_destroyed_content_is_counted_by_occurrence_not_by_string() -> None:
    """Deleting a content line from 36 pages is 36 deletions, not one."""
    candidates = [_candidate(CONTENT_LINE, "removed", 36)]
    scores = score_cleaning(candidates, {("removed", CONTENT_LINE): "content"})
    assert scores["content_destruction_count"] == 36
    assert scores["removal_precision"] == 0.0
    assert scores["content_destroyed_examples"]


def test_missed_boilerplate_lowers_recall_without_touching_precision() -> None:
    candidates = [
        _candidate(HEADER_LINE, "removed", 3),
        _candidate("MISSED RUNNING HEADER", "retained_edge", 3),
    ]
    scores = score_cleaning(
        candidates,
        {
            ("removed", HEADER_LINE): "header",
            ("retained_edge", "MISSED RUNNING HEADER"): "header",
        },
    )
    assert scores["removal_precision"] == 1.0
    assert scores["removal_recall"] == 0.5  # 3 / (3 + 3)
    assert scores["missed_boilerplate"] == 3


def test_correctly_retained_content_does_not_affect_recall() -> None:
    candidates = [
        _candidate(HEADER_LINE, "removed", 2),
        _candidate(CONTENT_LINE, "retained_edge", 5),
    ]
    scores = score_cleaning(
        candidates,
        {
            ("removed", HEADER_LINE): "header",
            ("retained_edge", CONTENT_LINE): "content",
        },
    )
    assert scores["removal_recall"] == 1.0
    assert scores["retained_content_correct"] == 5


def test_unsure_labels_are_excluded_from_both_rates() -> None:
    """An 'unsure' must not be silently counted as either outcome."""
    candidates = [
        _candidate(HEADER_LINE, "removed", 2),
        _candidate("AMBIGUOUS", "removed", 4),
    ]
    scores = score_cleaning(
        candidates,
        {("removed", HEADER_LINE): "header", ("removed", "AMBIGUOUS"): "unsure"},
    )
    assert scores["removed_unsure"] == 4
    assert scores["removed_occurrences_scored"] == 2
    assert scores["removal_precision"] == 1.0


def test_unlabelled_rows_are_reported_not_assumed() -> None:
    candidates = [
        _candidate(HEADER_LINE, "removed", 2),
        _candidate("NOT LABELLED", "removed", 7),
    ]
    scores = score_cleaning(candidates, {("removed", HEADER_LINE): "header"})
    assert scores["unlabelled_removed_occurrences"] == 7
    assert scores["removed_occurrences_scored"] == 2


def test_no_scored_removals_gives_none_not_zero() -> None:
    """A rate with no denominator is unknown, not 0.0."""
    scores = score_cleaning([], {})
    assert scores["removal_precision"] is None
    assert scores["removal_recall"] is None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_the_cli_writes_a_template(processed: Path, tmp_path: Path) -> None:
    assert main([
        "--processed-dir", str(processed),
        "--label-dir", str(tmp_path / "labels"),
        "--write-template",
    ]) == 0
    assert (tmp_path / "labels" / TEMPLATE_NAME).exists()


def test_the_cli_exits_zero_while_pending(processed: Path, tmp_path: Path) -> None:
    assert main([
        "--processed-dir", str(processed),
        "--label-dir", str(tmp_path / "labels"),
        "--output-dir", str(tmp_path / "out"),
    ]) == 0
    payload = json.loads(
        (tmp_path / "out" / "results.json").read_text(encoding="utf-8")
    )
    assert payload["status"].startswith("PENDING")


def test_the_real_corpus_package_is_pending() -> None:
    """Pinned: if this ever reports 'measured' without a human-labelled CSV,
    something has invented ground truth."""
    payload = run()
    if payload["status"] != "measured":
        assert payload["scores"] is None
