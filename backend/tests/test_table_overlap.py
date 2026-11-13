"""The E4 table-overlap proxy.

Tests the scorer's arithmetic and its honesty about being a proxy. The real
corpus figure is not asserted here — it is a finding, and pinning it would make
the test a change-detector for the corpus rather than for the code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from evaluation.table_overlap import (
    DEFAULT_OVERLAP_THRESHOLD,
    SENSITIVITY_THRESHOLDS,
    TableOverlapError,
    analyse_version,
    format_report,
    run,
    token_overlap,
)

VER = "VER-0123456789abcdef"


def _version_dir(tmp_path: Path, *, filename: str = "absent.pdf") -> Path:
    root = tmp_path / "processed" / VER
    root.mkdir(parents=True)
    (root / "extraction.json").write_text(
        json.dumps({
            "document": {
                "document_id": "DOC-fedcba9876543210",
                "version_id": VER,
                "filename": filename,
                "page_count": 2,
            },
            "pages": [],
        }),
        encoding="utf-8",
    )
    (root / "segments_clause_aware.json").write_text(
        json.dumps({
            "version_id": VER,
            "segmentation_method": "clause_aware",
            "segments": [
                {
                    "segment_id": "SEG-1111111111111111",
                    "ordinal": 0,
                    "segment_type": "clause",
                    "clause_number": "1",
                    "text": "1. Old Delhi Canara Bank 03E",
                    "page_start": 1,
                    "page_end": 1,
                    "page_offsets": [{"page_number": 1, "char_start": 0,
                                      "char_end": 28, "line_index_start": 0,
                                      "line_index_end": 0}],
                    "metadata": {},
                }
            ],
        }),
        encoding="utf-8",
    )
    return root


# --------------------------------------------------------------------------- #
# Overlap arithmetic
# --------------------------------------------------------------------------- #


def test_full_overlap_scores_one() -> None:
    assert token_overlap("Old Delhi Canara Bank", {"old", "delhi", "canara", "bank"}) == 1.0


def test_no_overlap_scores_zero() -> None:
    assert token_overlap("These directions apply", {"old", "delhi"}) == 0.0


def test_partial_overlap_is_the_share_of_distinct_tokens() -> None:
    # tokens: {these, apply, canara, bank} -> 2 of 4 in the table
    assert token_overlap("These apply Canara Bank", {"canara", "bank"}) == 0.5


def test_overlap_is_type_based_not_count_based() -> None:
    """Repeating one word must not inflate the score."""
    assert token_overlap("bank bank bank other", {"bank"}) == 0.5


def test_empty_text_scores_zero_rather_than_dividing_by_zero() -> None:
    assert token_overlap("", {"bank"}) == 0.0
    assert token_overlap("   ", {"bank"}) == 0.0


def test_overlap_ignores_case_and_punctuation() -> None:
    assert token_overlap("BANK, (Canara).", {"bank", "canara"}) == 1.0


def test_devanagari_tokens_are_counted() -> None:
    """The tokenizer must not silently drop the corpus's Devanagari text."""
    assert token_overlap("भारतीय बैंक", {"भारतीय", "बैंक"}) == 1.0


# --------------------------------------------------------------------------- #
# Reporting honesty
# --------------------------------------------------------------------------- #


def test_the_metric_is_labelled_a_proxy_everywhere() -> None:
    """If this wording is ever softened, the number starts being over-read."""
    payload = run()
    assert "PROXY" in payload["experiment"]
    assert "NOT a false-positive rate" in payload["metric"]
    assert "NOT a false-positive rate" in format_report(payload)


def test_a_sensitivity_sweep_is_reported_rather_than_one_tuned_number() -> None:
    payload = run()
    for version in payload["versions"]:
        if version["note"]:
            continue
        assert set(version["sensitivity_by_threshold"]) == {
            f"{t:.1f}" for t in SENSITIVITY_THRESHOLDS
        }


def test_the_threshold_is_recorded_in_the_output() -> None:
    payload = run()
    assert payload["overlap_threshold"] == DEFAULT_OVERLAP_THRESHOLD


# --------------------------------------------------------------------------- #
# Missing inputs
# --------------------------------------------------------------------------- #


def test_an_absent_source_pdf_is_reported_not_crashed(tmp_path: Path) -> None:
    """data/raw is git-ignored, so this is the fresh-clone case."""
    version_dir = _version_dir(tmp_path)
    report = analyse_version(version_dir, raw_dir=tmp_path / "raw")
    assert report.flagged_segments == 0
    assert "absent" in (report.note or "")


def test_missing_artefacts_raise(tmp_path: Path) -> None:
    empty = tmp_path / "processed" / VER
    empty.mkdir(parents=True)
    with pytest.raises(TableOverlapError, match="missing artefacts"):
        analyse_version(empty)


def test_a_missing_processed_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(TableOverlapError, match="not found"):
        run(tmp_path / "nope")


def test_flagged_count_never_exceeds_segments_on_table_pages() -> None:
    payload = run()
    for version in payload["versions"]:
        assert (
            version["flagged_segments"]
            <= version["clause_segments_on_table_pages"]
            <= version["clause_segments"]
        )
