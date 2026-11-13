"""The E5 CER/WER scorer.

The arithmetic is tested against cases whose answer can be worked out by hand,
because an error-rate implementation that is subtly wrong produces plausible
numbers rather than obvious failures.

No corpus CER is asserted: nobody has transcribed a page, and the scorer must
report PENDING rather than a figure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.extraction_fidelity import (
    TEMPLATE_SUFFIX,
    TRUTH_SUFFIX,
    FidelityError,
    PageSelection,
    _normalise_for_scoring,
    character_error_rate,
    run,
    score_page,
    select_pages,
    word_error_rate,
    write_transcription_templates,
)

VER = "VER-0123456789abcdef"


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    root = tmp_path / "processed" / VER
    root.mkdir(parents=True)
    (root / "extraction.json").write_text(
        json.dumps({
            "document": {"version_id": VER, "page_count": 2,
                         "filename": "fixture.pdf"},
            "pages": [
                {"page_number": 1, "normalized_text": "the quick brown fox",
                 "original_text": "the quick brown fox", "char_count": 19,
                 "word_count": 4, "is_empty": False,
                 "has_meaningful_text": True},
                {"page_number": 2, "normalized_text": "भारतीय रिज़र्व बैंक",
                 "original_text": "भारतीय रिज़र्व बैंक", "char_count": 19,
                 "word_count": 3, "is_empty": False,
                 "has_meaningful_text": True},
            ],
        }),
        encoding="utf-8",
    )
    return tmp_path / "processed"


# --------------------------------------------------------------------------- #
# CER / WER arithmetic
# --------------------------------------------------------------------------- #


def test_identical_text_has_zero_error() -> None:
    assert character_error_rate("hello world", "hello world") == 0.0
    assert word_error_rate("hello world", "hello world") == 0.0


def test_one_wrong_character_in_ten() -> None:
    #'abcdefghij' vs 'abcdefghiX' -> 1 substitution / 10 reference chars
    assert character_error_rate("abcdefghij", "abcdefghiX") == pytest.approx(0.1)


def test_one_wrong_word_in_four() -> None:
    assert word_error_rate(
        "the quick brown fox", "the quick brown dog"
    ) == pytest.approx(0.25)


def test_a_missing_word_counts_as_one_error() -> None:
    assert word_error_rate("a b c d", "a b c") == pytest.approx(0.25)


def test_an_inserted_word_counts_as_one_error() -> None:
    assert word_error_rate("a b c", "a b c d") == pytest.approx(1 / 3)


def test_error_may_exceed_one_when_the_extractor_emits_extra_text() -> None:
    """A flattened table does exactly this; clamping would hide it."""
    rate = word_error_rate("short", "a b c d e f g h")
    assert rate is not None and rate > 1.0


def test_an_empty_reference_gives_none_not_zero() -> None:
    """No reference means unknown, not perfect."""
    assert character_error_rate("", "anything") is None
    assert word_error_rate("   ", "anything") is None


def test_whitespace_differences_are_normalised_away() -> None:
    """Otherwise CER would measure the transcriber's spacing habits."""
    assert character_error_rate("hello   world", "hello world") == 0.0
    assert character_error_rate("hello\nworld", "hello world") == 0.0


def test_case_and_punctuation_are_real_errors() -> None:
    """A citation quote must match verbatim, so these must not be folded."""
    assert character_error_rate("Hello", "hello") > 0
    assert character_error_rate("end.", "end") > 0


def test_devanagari_is_compared_not_dropped() -> None:
    assert character_error_rate("भारतीय बैंक", "भारतीय बैंक") == 0.0
    assert character_error_rate("भारतीय बैंक", "भारतीय") > 0


def test_normalisation_is_nfkc() -> None:
    # U+FB01 'ﬁ' ligature normalises to 'fi'
    assert _normalise_for_scoring("ﬁle") == "file"


# --------------------------------------------------------------------------- #
# Page selection
# --------------------------------------------------------------------------- #


def test_selection_prefers_the_devanagari_page(processed: Path) -> None:
    selections = select_pages(processed, limit=2)
    assert any("Devanagari" in s.reason for s in selections)


def test_selection_never_repeats_a_page(processed: Path) -> None:
    selections = select_pages(processed, limit=5)
    keys = [(s.version_id, s.page_number) for s in selections]
    assert len(keys) == len(set(keys))


def test_selection_respects_the_limit(processed: Path) -> None:
    assert len(select_pages(processed, limit=1)) == 1


def test_a_missing_processed_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FidelityError, match="not found"):
        select_pages(tmp_path / "nope")


# --------------------------------------------------------------------------- #
# Templates - independence matters here too
# --------------------------------------------------------------------------- #


def test_the_template_does_not_reveal_the_extracted_text(
    processed: Path, tmp_path: Path
) -> None:
    """Showing it would anchor the transcriber and the 'reference' would
    agree with the extractor by suggestion rather than observation."""
    selections = select_pages(processed, limit=2)
    written = write_transcription_templates(
        selections, tmp_path / "truth", processed
    )
    for path in written:
        body = path.read_text(encoding="utf-8")
        assert "the quick brown fox" not in body
        assert "भारतीय रिज़र्व बैंक" not in body
        assert "DELIBERATELY NOT SHOWN" in body


def test_the_template_names_the_expected_truth_filename(
    processed: Path, tmp_path: Path
) -> None:
    selections = select_pages(processed, limit=1)
    written = write_transcription_templates(
        selections, tmp_path / "truth", processed
    )
    assert TRUTH_SUFFIX in written[0].read_text(encoding="utf-8")
    assert written[0].name.endswith(TEMPLATE_SUFFIX)


# --------------------------------------------------------------------------- #
# Scoring and the PENDING path
# --------------------------------------------------------------------------- #


def test_a_page_without_a_transcription_is_pending(
    processed: Path, tmp_path: Path
) -> None:
    selection = select_pages(processed, limit=1)[0]
    result = score_page(selection, tmp_path / "truth", processed)
    assert result["status"].startswith("PENDING")
    assert result["cer"] is None and result["wer"] is None


def test_a_real_transcription_is_scored(processed: Path, tmp_path: Path) -> None:
    truth = tmp_path / "truth"
    truth.mkdir()
    selection = PageSelection(
        version_id=VER, page_number=1, reason="test",
        devanagari_chars=0, short_line_ratio=0.0, char_count=19,
    )
    # One word wrong out of four.
    (truth / f"{selection.stem}{TRUTH_SUFFIX}").write_text(
        "the quick brown dog", encoding="utf-8"
    )
    result = score_page(selection, truth, processed)
    assert result["status"] == "measured"
    assert result["wer"] == pytest.approx(0.25)


def test_an_empty_transcription_is_refused(processed: Path, tmp_path: Path) -> None:
    truth = tmp_path / "truth"
    truth.mkdir()
    selection = PageSelection(
        version_id=VER, page_number=1, reason="test",
        devanagari_chars=0, short_line_ratio=0.0, char_count=19,
    )
    (truth / f"{selection.stem}{TRUTH_SUFFIX}").write_text("  \n", encoding="utf-8")
    with pytest.raises(FidelityError, match="empty"):
        score_page(selection, truth, processed)


def test_pooled_is_pending_without_any_transcription(
    processed: Path, tmp_path: Path
) -> None:
    payload = run(processed, tmp_path / "truth", limit=2)
    assert payload["pooled"]["status"].startswith("PENDING")
    assert payload["pooled"]["cer"] is None


def test_the_real_corpus_is_pending() -> None:
    """Pinned: a CER appearing here without a .truth.txt means something
    generated a reference, which is forbidden."""
    payload = run()
    if payload["pooled"]["status"] != "measured":
        assert payload["pooled"]["cer"] is None
