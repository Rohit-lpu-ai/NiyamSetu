"""Normalisation must be conservative: tidy the layout, never the content."""

from __future__ import annotations

from backend.app.document_processing import NormalizationOptions, normalize_text
from backend.app.document_processing.normalization import (
    collapse_blank_lines,
    collapse_horizontal_whitespace,
    dehyphenate_line_breaks,
    strip_invisible_characters,
    unify_line_endings,
)

ALL_OFF = NormalizationOptions(
    unicode_nfkc=False,
    unify_line_endings=False,
    strip_invisible_characters=False,
    dehyphenate_line_breaks=False,
    collapse_horizontal_whitespace=False,
    collapse_blank_lines=False,
    strip_trailing_line_whitespace=False,
)


# --------------------------------------------------------------------------- #
# Individual steps
# --------------------------------------------------------------------------- #


def test_ligature_is_repaired_by_nfkc() -> None:
    assert normalize_text("notiﬁcation") == "notification"


def test_line_endings_are_unified() -> None:
    assert unify_line_endings("a\r\nb\rc") == "a\nb\nc"


def test_invisible_characters_are_removed() -> None:
    assert strip_invisible_characters("Form​ 16­") == "Form 16"


def test_line_break_hyphen_is_rejoined() -> None:
    assert dehyphenate_line_breaks("com-\npliance") == "compliance"


def test_hyphen_between_capitalised_words_is_left_alone() -> None:
    """``Centre-\\nState`` is a real compound, not a layout artefact."""
    assert dehyphenate_line_breaks("Centre-\nState") == "Centre-\nState"


def test_runs_of_spaces_collapse_to_one() -> None:
    assert collapse_horizontal_whitespace("a      b\tc") == "a b c"


def test_excess_blank_lines_collapse_to_one_blank_line() -> None:
    assert collapse_blank_lines("a\n\n\n\n\nb") == "a\n\nb"


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #


def test_clause_numbering_survives_normalisation() -> None:
    text = "3.2 Reporting Requirements\n(iv) Every entity shall file Form 16.\n"
    out = normalize_text(text)
    assert "3.2" in out
    assert "(iv)" in out
    assert "Form 16" in out


def test_case_and_punctuation_are_not_altered() -> None:
    text = "SHALL, within thirty (30) days, submit Form 16; failing which..."
    assert normalize_text(text) == text


def test_dates_and_numbers_are_not_rewritten() -> None:
    text = "effective from 1 April 2027, at the rate of 7.5% per annum"
    assert normalize_text(text) == text


def test_single_internal_newlines_are_preserved() -> None:
    """Line structure is the raw material for Phase 2 segmentation."""
    text = "Line one\nLine two\n\nNew paragraph"
    assert normalize_text(text) == text


def test_hindi_text_is_not_stripped() -> None:
    """RBI circulars are bilingual; the Devanagari half must not be discarded."""
    text = "भारतीय रिजर्व बैंक"
    assert normalize_text(text).strip() == text


# --------------------------------------------------------------------------- #
# Options plumbing
# --------------------------------------------------------------------------- #


def test_every_step_can_be_disabled() -> None:
    text = "notiﬁcation  com-\npliance\r\n\n\n\nend   "
    assert normalize_text(text, ALL_OFF) == text


def test_options_round_trip_to_a_dict() -> None:
    options = NormalizationOptions().as_dict()
    assert options["unicode_nfkc"] is True
    assert set(options) == {
        "unicode_nfkc",
        "unify_line_endings",
        "strip_invisible_characters",
        "dehyphenate_line_breaks",
        "collapse_horizontal_whitespace",
        "collapse_blank_lines",
        "strip_trailing_line_whitespace",
    }


def test_empty_input_stays_empty() -> None:
    assert normalize_text("") == ""
