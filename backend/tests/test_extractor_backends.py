"""The Experiment A comparison machinery: backends, detectors and metrics.

These tests check that the *measuring* code is sound. They deliberately assert
nothing about which library wins — that is a finding in
``evaluation/experiment-a-extractor-comparison.md``, produced by running the
script on real documents, and it would be circular to bake it into a test.

Every test here runs on a synthetic fixture, so the suite stays fast and does
not depend on the git-ignored corpus being present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.document_processing.extractor_backends import (
    BackendExtraction,
    BackendUnavailableError,
    PypdfBackend,
    available_backends,
    get_backend,
    registered_backend_names,
)
from evaluation.experiment_a_extractor_comparison import (
    compare_document,
    line_structure_difference,
    main,
    token_overlap,
)
from evaluation.text_artifacts import (
    count_broken_words,
    count_hyphen_space_artifacts,
    count_mismapped_glyph_candidates,
    count_padded_quotes,
    count_replacement_characters,
    devanagari_char_count,
    find_orphan_combining_marks,
    is_probably_flattened_table,
    measure_page,
    short_line_ratio,
    summarise_pages,
)


# --------------------------------------------------------------------------- #
# Backend registry and the uniform interface
# --------------------------------------------------------------------------- #


def test_pypdf_backend_matches_the_production_extraction_path(
    three_page_pdf: Path,
) -> None:
    """The baseline backend must read what ``extract_pdf`` reads.

    If these ever diverge, Experiment A stops measuring the library and starts
    measuring the difference between two call sites.
    """
    from backend.app.document_processing import extract_pdf

    backend_result = PypdfBackend().extract(three_page_pdf)
    pipeline_result = extract_pdf(three_page_pdf)

    assert backend_result.page_count == pipeline_result.document.page_count
    for page in pipeline_result.pages:
        assert backend_result.text_for_page(page.page_number) == page.original_text


def test_every_registered_backend_name_is_constructible() -> None:
    for name in registered_backend_names():
        assert get_backend(name).name == name


def test_unknown_backend_name_raises_with_the_available_names() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_backend("ocrmypdf")
    assert "pypdf" in str(excinfo.value)


def test_available_backends_separates_usable_from_missing() -> None:
    """pypdf is always usable; anything missing is reported, never raised."""
    usable, unavailable = available_backends()
    assert "pypdf" in {backend.name for backend in usable}
    assert set(unavailable) & set(registered_backend_names()) == set(unavailable)
    for reason in unavailable.values():
        assert "not installed" in reason


def test_extraction_rejects_misaligned_page_lists() -> None:
    """A result whose page list does not match its page count is unusable.

    Per-page comparison across backends indexes by position, so a length
    mismatch would silently compare page 4 against page 5.
    """
    with pytest.raises(ValueError, match="must align"):
        BackendExtraction(
            backend="fake",
            tool_version="fake 0",
            page_count=3,
            page_texts=["a", "b"],
            elapsed_seconds=0.0,
        )


def test_text_for_page_is_one_based_and_bounds_checked(three_page_pdf: Path) -> None:
    extraction = PypdfBackend().extract(three_page_pdf)
    assert "PAGE 1" in extraction.text_for_page(1)
    assert "PAGE 3" in extraction.text_for_page(3)
    for bad in (0, 4):
        with pytest.raises(ValueError, match="outside"):
            extraction.text_for_page(bad)


def test_pypdf_backend_reports_no_table_support(three_page_pdf: Path) -> None:
    """``None`` means "no table API", which is different from "found none"."""
    backend = PypdfBackend()
    assert backend.supports_tables is False
    assert backend.extract_tables(three_page_pdf, 1) is None


def test_backend_records_a_tool_version_string(three_page_pdf: Path) -> None:
    extraction = PypdfBackend().extract(three_page_pdf)
    assert extraction.tool_version.startswith("pypdf ")
    assert extraction.elapsed_seconds >= 0.0


def test_backend_unavailable_error_is_a_runtime_error() -> None:
    """The experiment runner catches it by type, so the hierarchy is contract."""
    assert issubclass(BackendUnavailableError, RuntimeError)


# --------------------------------------------------------------------------- #
# Artefact detectors
# --------------------------------------------------------------------------- #


def test_replacement_characters_are_counted_exactly() -> None:
    assert count_replacement_characters("a�b�") == 2
    assert count_replacement_characters("clean text") == 0


def test_mismapped_glyphs_catch_the_latin_extended_substitution() -> None:
    """The Experiment A §3 finding, as a regression test.

    ``į`` (U+012F) is what PyMuPDF emits where pypdf emits U+FFFD. Counting
    only U+FFFD would score the silent corruption as perfect.
    """
    loud = "भारतीय �रिज़"
    quiet = "भारतीय įरिज़"

    assert count_replacement_characters(loud) == 1
    assert count_mismapped_glyph_candidates(loud)[0] == 0

    assert count_replacement_characters(quiet) == 0
    quiet_count, quiet_samples = count_mismapped_glyph_candidates(quiet)
    assert quiet_count == 1
    assert "į" in quiet_samples

    # The point of the combined signal: both texts lost exactly one character.
    assert measure_page(1, loud).glyph_loss_signal == 1
    assert measure_page(1, quiet).glyph_loss_signal == 1


def test_mismapped_glyph_samples_are_distinct_characters_not_occurrences() -> None:
    count, samples = count_mismapped_glyph_candidates("į" * 30)
    assert count == 30
    assert samples == ["į"]


def test_devanagari_is_counted_and_never_flagged_as_mismapped() -> None:
    """Correct Devanagari must not be penalised by the suspect-script detector."""
    text = "भारतीय रिज़र्व बैंक"
    assert devanagari_char_count(text) > 10
    assert count_mismapped_glyph_candidates(text)[0] == 0
    assert count_replacement_characters(text) == 0


def test_orphan_combining_mark_detection() -> None:
    """A dependent vowel sign cannot start a word, so a leading one is proof."""
    # U+0940 DEVANAGARI VOWEL SIGN II, detached and starting a token.
    assert find_orphan_combining_marks("text ीय more") == ["ीय"]
    # A properly formed syllable starts with a consonant: not flagged.
    assert find_orphan_combining_marks("नीय") == []


def test_hyphen_space_artifacts_catch_both_sides() -> None:
    count, samples = count_hyphen_space_artifacts("clean- up and risk -based")
    assert count == 2
    assert samples  # the report quotes these
    assert count_hyphen_space_artifacts("well-formed non-performing")[0] == 0


def test_padded_quotes_ignore_straight_quotes() -> None:
    """Straight quotes appear legitimately (measurements, code), curly ones here."""
    assert count_padded_quotes("‘ the Directions ’")[0] == 2
    assert count_padded_quotes("'plain' and \"plain\"")[0] == 0


def test_broken_word_detector_reports_its_known_false_positive() -> None:
    """Documented behaviour, asserted so it cannot change silently.

    ``'M inimum'`` is a real defect and ``'A notification'`` is not, but the
    regex cannot tell them apart. Experiment A §4 therefore does not quote this
    counter as a finding; the test pins the limitation rather than hiding it.
    """
    defect_count, _ = count_broken_words("the M inimum capital")
    legitimate_count, _ = count_broken_words("A notification was issued")
    assert defect_count == 1
    assert legitimate_count == 1  # the false positive, by design


def test_short_line_ratio_and_flattened_table_proxy() -> None:
    flattened = "\n".join(["BI", "constituents", "P&L or", "balance", "sheet",
                           "items", "Description", "Typical", "sub-items"])
    ratio, short, non_blank = short_line_ratio(flattened)
    assert non_blank == 9
    assert short == 9
    assert ratio == 1.0
    assert is_probably_flattened_table(flattened) is True

    prose = "\n".join(
        [f"This is line {n} of ordinary flowing prose with several words." for n in range(10)]
    )
    assert is_probably_flattened_table(prose) is False


def test_short_page_is_not_flagged_as_a_flattened_table() -> None:
    """Without a minimum line count a two-line cover page trivially 'fails'."""
    assert is_probably_flattened_table("Title\nDate") is False


def test_measure_page_and_summarise_round_trip_to_json() -> None:
    pages = [
        measure_page(1, "Ordinary paragraph text on page one."),
        measure_page(2, "clean- up � į"),
    ]
    summary = summarise_pages(pages)
    assert summary["page_count"] == 2
    assert summary["replacement_chars"] == 1
    assert summary["mismapped_glyph_candidates"] == 1
    assert summary["glyph_loss_signal"] == 2
    assert summary["hyphen_space_artifacts"] == 1
    # The whole record must serialise: it is written to results.json.
    assert json.loads(json.dumps([page.to_dict() for page in pages]))


def test_summarise_empty_page_list_does_not_crash() -> None:
    assert summarise_pages([])["page_count"] == 0


def test_empty_page_is_reported_as_empty() -> None:
    artifacts = measure_page(1, "   \n  \n")
    assert artifacts.is_empty is True
    assert artifacts.word_count == 0


# --------------------------------------------------------------------------- #
# Cross-backend comparison metrics
# --------------------------------------------------------------------------- #


def test_token_overlap_separates_reordering_from_content_loss() -> None:
    """The metric Experiment A §5.1 rests on.

    Reordering the same words must score 1.0; dropping words must score below
    it. Without this property "reading order differs" and "text was lost" would
    be indistinguishable in the report.
    """
    assert token_overlap("alpha beta gamma", "gamma alpha beta") == 1.0
    assert token_overlap("alpha beta gamma", "alpha beta") < 1.0
    assert token_overlap("", "") == 1.0
    assert token_overlap("alpha", "") == 0.0


def test_token_overlap_counts_repeats_as_a_multiset() -> None:
    """A backend that emitted a word twice has not matched one that emitted it once."""
    assert token_overlap("a a b", "a b") < 1.0


def test_line_structure_difference_detects_regrouping() -> None:
    same_words_one_line = "alpha beta gamma"
    same_words_three_lines = "alpha\nbeta\ngamma"
    assert token_overlap(same_words_one_line, same_words_three_lines) == 1.0
    assert line_structure_difference(same_words_one_line, same_words_three_lines) > 0.5
    assert line_structure_difference("a\nb", "a\nb") == 0.0
    assert line_structure_difference("", "") == 0.0


def test_compare_document_runs_end_to_end_on_a_fixture(three_page_pdf: Path) -> None:
    """One backend is enough to prove the comparison assembles and serialises."""
    comparison, extractions = compare_document(
        three_page_pdf,
        [PypdfBackend()],
        document_label="fixture",
        repeats=1,
        probe_tables=False,
    )
    assert set(extractions) == {"pypdf"}
    summary = comparison.backends["pypdf"]
    assert summary["page_count"] == 3
    assert summary["total_chars"] > 0
    assert summary["seconds_median"] >= 0
    # A single backend is trivially its own best, on both measures.
    assert summary["char_ratio_vs_best_median"] == 1.0
    assert summary["word_ratio_vs_best_median"] == 1.0
    assert comparison.page_count_disagreement is None
    assert comparison.version_id.startswith("VER-")
    assert json.loads(json.dumps(comparison.to_dict()))


def test_compare_document_detects_a_page_count_disagreement(
    three_page_pdf: Path,
) -> None:
    """A page-count disagreement invalidates per-page comparison, so it is flagged.

    Simulated with a stub backend rather than a real library, because no real
    library disagreed on this corpus and the handling still has to be correct.
    """

    class ShortBackend(PypdfBackend):
        name = "stub-short"

        def _open_page_texts(self, path):  # type: ignore[no-untyped-def]
            return 2, ["page one", "page two"], {}

    comparison, _ = compare_document(
        three_page_pdf,
        [PypdfBackend(), ShortBackend()],
        document_label="fixture",
        repeats=1,
        probe_tables=False,
    )
    assert comparison.page_count_disagreement == {"pypdf": 3, "stub-short": 2}
    assert any("disagree on page count" in note for note in comparison.notes)


def test_compare_document_records_a_backend_that_fails_to_open(
    three_page_pdf: Path,
) -> None:
    """A whole-document failure is a result, not a crash."""

    class BrokenBackend(PypdfBackend):
        name = "stub-broken"

        def _open_page_texts(self, path):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated parser explosion")

    comparison, extractions = compare_document(
        three_page_pdf,
        [PypdfBackend(), BrokenBackend()],
        document_label="fixture",
        repeats=1,
        probe_tables=False,
    )
    assert "stub-broken" not in extractions
    assert any("simulated parser explosion" in note for note in comparison.notes)
    assert comparison.backends["pypdf"]["page_count"] == 3


def test_per_page_extraction_failure_keeps_the_page_sequence_intact(
    three_page_pdf: Path,
) -> None:
    """One bad page must not shorten the document or shift the page numbering."""

    class FlakyBackend(PypdfBackend):
        name = "stub-flaky"

        def _open_page_texts(self, path):  # type: ignore[no-untyped-def]
            def read(index: int) -> str:
                if index == 1:
                    raise ValueError("simulated page 2 failure")
                return f"page {index + 1}"

            from backend.app.document_processing.extractor_backends import (
                _safe_page_loop,
            )

            texts, errors = _safe_page_loop(3, read)
            return 3, texts, errors

    extraction = FlakyBackend().extract(three_page_pdf)
    assert extraction.page_count == 3
    assert extraction.text_for_page(2) == ""
    assert 2 in extraction.page_errors
    assert "simulated page 2 failure" in extraction.page_errors[2]


def test_cli_writes_results_json(three_page_pdf: Path, tmp_path: Path) -> None:
    exit_code = main(
        [
            str(three_page_pdf),
            "--backends",
            "pypdf",
            "--repeats",
            "1",
            "--no-tables",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert exit_code == 0
    payload = json.loads(
        (tmp_path / "out" / "results.json").read_text(encoding="utf-8")
    )
    assert payload["experiment"].startswith("A -")
    assert len(payload["documents"]) == 1
    assert payload["backends_compared"][0]["name"] == "pypdf"


def test_cli_skips_a_missing_file_and_reports_it(
    three_page_pdf: Path, tmp_path: Path
) -> None:
    """One absent download must not block comparison of the rest of the corpus."""
    exit_code = main(
        [
            str(three_page_pdf),
            str(tmp_path / "absent.pdf"),
            "--backends",
            "pypdf",
            "--repeats",
            "1",
            "--no-tables",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert exit_code == 0
    payload = json.loads(
        (tmp_path / "out" / "results.json").read_text(encoding="utf-8")
    )
    assert len(payload["documents"]) == 1
    assert len(payload["documents_missing_on_disk"]) == 1


def test_cli_rejects_being_given_neither_paths_nor_manifest() -> None:
    with pytest.raises(SystemExit):
        main(["--backends", "pypdf"])
