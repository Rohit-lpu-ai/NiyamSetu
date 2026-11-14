"""Unit tests for the comparison module. No database, no network.

Covers the parts of Compare that are pure functions of their inputs: the
comparison key, the TF-IDF implementation, the four-stage matcher, change
classification, the confidence rules, the word diff and the entity extractor.

The integration tests in ``test_comparison_integration.py`` cover everything
that needs PostgreSQL -- evidence resolution, version validation and the real
RBI version pairs.
"""

from __future__ import annotations

import math
from typing import List, Optional

import pytest

from backend.app.comparison import alignment, entities, explain, similarity
from backend.app.comparison.alignment import (
    CHANGE_TYPES,
    AlignmentConfig,
    Change,
    ClauseRef,
    align,
    confidence_label,
    summarize,
)


def clause(
    ordinal: int,
    text: str,
    number: Optional[str] = None,
    *,
    page: int = 1,
    page_end: Optional[int] = None,
    ambiguous: bool = False,
) -> ClauseRef:
    """A ClauseRef with plausible provenance, so nothing under test gets a None."""
    return ClauseRef(
        clause_id=f"CLS-{ordinal:07d}",
        ordinal=ordinal,
        text=text,
        page_start=page,
        page_end=page_end if page_end is not None else page,
        clause_number=number,
        clause_number_ambiguous=ambiguous,
        pages=({"page_number": page, "char_start": 0, "char_end": len(text)},),
    )


# --------------------------------------------------------------------------- #
# The comparison key: formatting differences must not read as policy changes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "left,right,why",
    [
        ("the  bank shall report", "the bank shall report", "collapsed whitespace"),
        ("the bank\nshall report", "the bank shall report", "a line break"),
        ("co­operative banks", "cooperative banks", "a soft hyphen"),
        ("a – b", "a - b", "an en dash"),
        ("a — b", "a - b", "an em dash"),
        ("“quoted”", '"quoted"', "curly quotes"),
        ("word .", "word.", "a space before a full stop"),
        ("( a )", "(a)", "spaces inside brackets"),
        ("The Bank Shall Report", "the bank shall report", "case"),
        ("no break space", "no break space", "a non-breaking space"),
    ],
)
def test_formatting_differences_compare_as_identical(left, right, why):
    assert similarity.comparison_key(left) == similarity.comparison_key(right), why


@pytest.mark.parametrize(
    "left,right,why",
    [
        ("within 30 days", "within 60 days", "a changed deadline"),
        ("shall report", "shall not report", "a negation"),
        ("Rs. 10 crore", "Rs. 20 crore", "a changed amount"),
        ("7.5 per cent", "8.5 per cent", "a changed rate"),
    ],
)
def test_meaning_bearing_differences_stay_different(left, right, why):
    """The other half of the contract, and the more important one."""
    assert similarity.comparison_key(left) != similarity.comparison_key(right), why


@pytest.mark.parametrize(
    "text,bare",
    [
        ("3. The bank shall report", "the bank shall report"),
        ("3.2 The bank shall report", "the bank shall report"),
        ("(iv) The bank shall report", "the bank shall report"),
        ("(a) The bank shall report", "the bank shall report"),
        ("A.1 The bank shall report", "the bank shall report"),
        ("12. The bank shall report", "the bank shall report"),
    ],
)
def test_a_leading_clause_marker_can_be_dropped(text, bare):
    assert similarity.comparison_key(text, drop_marker=True) == bare


def test_dropping_a_marker_does_not_eat_real_text():
    """A clause that starts with a word must survive marker stripping intact."""
    text = "The bank shall report within 30 days"
    assert similarity.comparison_key(text, drop_marker=True) == text.casefold()


def test_the_empty_string_is_handled():
    assert similarity.comparison_key("") == ""
    assert similarity.comparison_key(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# TF-IDF
# --------------------------------------------------------------------------- #


def test_identical_text_scores_one():
    """``approx``, because the cosine of a float vector with itself is 1 - eps.

    The exactness that matters is elsewhere and is asserted elsewhere: an
    unchanged clause is decided by comparing :func:`comparison_key` values for
    equality, not by testing a float against 1.0, and ``align`` reports an
    exact-text match as a flat ``1.0`` rather than its computed cosine.
    """
    assert similarity.cosine_similarity(
        "a bank shall report", "a bank shall report"
    ) == pytest.approx(1.0)


def test_a_similarity_score_never_exceeds_one():
    """Floating-point error must not produce a score above 1.0."""
    for text in ("a bank shall report", "x " * 200, "Rs. 25,000 crore at 7.5 per cent"):
        assert similarity.cosine_similarity(text, text) <= 1.0


def test_unrelated_text_scores_zero():
    assert similarity.cosine_similarity("capital adequacy ratio", "scheduled tribes") == 0.0


def test_a_small_wording_change_scores_high_but_not_one():
    score = similarity.cosine_similarity(
        "the bank shall submit the return within 30 days of each quarter",
        "the bank shall submit the return within 60 days of each quarter",
    )
    assert 0.0 < score < 1.0


def test_empty_text_scores_zero_without_raising():
    assert similarity.cosine_similarity("", "anything at all") == 0.0
    assert similarity.cosine_similarity("", "") == 0.0


def test_stop_words_only_text_produces_an_empty_vector():
    """'the of and' carries no signal, and must not match everything."""
    assert similarity.cosine_similarity("the of and", "the of and") == 0.0


def test_bigrams_separate_phrases_that_share_a_token():
    """The reason ngram_range=(1, 2) is not (1, 1)."""
    unigram_only = similarity.TfidfIndex(
        ["scheduled castes credit", "scheduled banks credit"], max_n=1
    ).similarity(0, 1)
    with_bigrams = similarity.TfidfIndex(
        ["scheduled castes credit", "scheduled banks credit"], max_n=2
    ).similarity(0, 1)
    assert with_bigrams < unigram_only


def test_the_matrix_shape_is_old_by_new():
    matrix = similarity.similarity_matrix(["a b c", "d e f"], ["a b c", "x y z", "d e f"])
    assert len(matrix) == 2
    assert all(len(row) == 3 for row in matrix)


def test_the_matrix_shares_one_vocabulary():
    """A pair's score must not depend on which other clauses were passed in.

    It *does* depend on them -- that is what IDF means -- so the assertion is
    the weaker, true one: the diagonal of identical texts is still 1.
    """
    matrix = similarity.similarity_matrix(
        ["alpha provision", "beta provision"], ["alpha provision", "beta provision"]
    )
    assert matrix[0][0] == pytest.approx(1.0)
    assert matrix[1][1] == pytest.approx(1.0)


def test_this_tfidf_agrees_with_scikit_learn_when_it_is_installed():
    """The equivalence claim in the module docstring, checked rather than asserted.

    scikit-learn is deliberately **not** a project dependency (it is not in
    ``requirements.txt`` and not in ``.venv``), so this skips on this machine.
    It exists so that anyone who does have it can verify that reimplementing
    ``TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True,
    stop_words="english")`` was faithful rather than approximate.
    """
    sklearn_text = pytest.importorskip(
        "sklearn.feature_extraction.text",
        reason="scikit-learn is not a project dependency; see similarity.py",
    )
    from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

    corpus = [
        "the bank shall submit the return within 30 days of each quarter",
        "the bank shall submit the return within 60 days of each quarter",
        "credit facilities to scheduled castes and scheduled tribes",
        "the designated officer shall maintain the register",
    ]
    vectorizer = sklearn_text.TfidfVectorizer(
        ngram_range=(1, 2), sublinear_tf=True, stop_words="english"
    )
    reference = sk_cosine(vectorizer.fit_transform(corpus))
    ours = similarity.TfidfIndex(corpus)
    for i in range(len(corpus)):
        for j in range(len(corpus)):
            assert ours.similarity(i, j) == pytest.approx(
                float(reference[i][j]), abs=1e-9
            ), f"pair ({i}, {j})"


def test_jaccard_is_a_ratio_in_range():
    assert similarity.jaccard("a bank", "a bank") == 1.0
    assert similarity.jaccard("bank", "tribe") == 0.0
    assert 0.0 < similarity.jaccard("bank register officer", "bank register") < 1.0


# --------------------------------------------------------------------------- #
# Alignment: the four classifications
# --------------------------------------------------------------------------- #


def test_identical_clauses_are_unchanged():
    old = [clause(1, "1. The bank shall report.", "1.")]
    new = [clause(1, "1. The bank shall report.", "1.")]
    (change,) = align(old, new)
    assert change.change_type == "unchanged"
    assert change.match_basis == "exact_text"
    assert change.similarity == 1.0


def test_a_changed_deadline_is_modified():
    old = [clause(1, "1. Submit the fixture return within 30 days of the quarter.", "1.")]
    new = [clause(1, "1. Submit the fixture return within 60 days of the quarter.", "1.")]
    (change,) = align(old, new)
    assert change.change_type == "modified"


def test_a_clause_only_in_the_new_version_is_added():
    old = [clause(1, "1. Alpha provision about the register.", "1.")]
    new = [
        clause(1, "1. Alpha provision about the register.", "1."),
        clause(2, "2. An entirely unrelated obligation concerning audits.", "2."),
    ]
    changes = align(old, new)
    added = [c for c in changes if c.change_type == "added"]
    assert len(added) == 1
    assert added[0].old is None
    assert added[0].similarity is None


def test_a_clause_only_in_the_old_version_is_removed():
    old = [
        clause(1, "1. Alpha provision about the register.", "1."),
        clause(2, "2. An entirely unrelated obligation concerning audits.", "2."),
    ]
    new = [clause(1, "1. Alpha provision about the register.", "1.")]
    removed = [c for c in align(old, new) if c.change_type == "removed"]
    assert len(removed) == 1
    assert removed[0].new is None


# --------------------------------------------------------------------------- #
# Renumbering and reordering must not be reported as modifications
# --------------------------------------------------------------------------- #


def test_renumbering_alone_is_not_a_modification():
    """The brief's explicit requirement."""
    old = [clause(1, "2. The designated officer shall maintain the register.", "2.")]
    new = [clause(1, "3. The designated officer shall maintain the register.", "3.")]
    (change,) = align(old, new)
    assert change.change_type == "unchanged"
    assert change.renumbered is True
    assert "numbering" in change.notes[0].lower()


def test_an_unnumbered_clause_moved_is_unchanged_and_reordered():
    old = [
        clause(1, "Alpha provision concerning the register and its upkeep."),
        clause(2, "Beta provision concerning the inspection and the officer."),
    ]
    new = [
        clause(1, "Beta provision concerning the inspection and the officer."),
        clause(2, "Alpha provision concerning the register and its upkeep."),
    ]
    changes = align(old, new)
    assert {c.change_type for c in changes} == {"unchanged"}
    assert sum(1 for c in changes if c.reordered) == 1


def test_reordering_is_computed_as_the_complement_of_the_longest_run():
    """One clause promoted to the top must flag one clause, not all of them."""
    texts = [f"Clause {letter} concerning a distinct fixture subject." for letter in "abcde"]
    old = [clause(i + 1, text) for i, text in enumerate(texts)]
    # Move the last clause to the front; the other four keep their order.
    reordered_texts = [texts[-1]] + texts[:-1]
    new = [clause(i + 1, text) for i, text in enumerate(reordered_texts)]
    changes = align(old, new)
    assert sum(1 for c in changes if c.reordered) == 1


def test_everything_in_order_flags_nothing_as_reordered():
    texts = [f"Clause {letter} concerning a distinct fixture subject." for letter in "abc"]
    old = [clause(i + 1, text) for i, text in enumerate(texts)]
    new = [clause(i + 1, text) for i, text in enumerate(texts)]
    assert not any(c.reordered for c in align(old, new))


def test_lis_picks_a_longest_increasing_subsequence():
    assert alignment._lis_indices([]) == []
    assert alignment._lis_indices([5]) == [0]
    assert len(alignment._lis_indices([1, 2, 3, 4])) == 4
    assert len(alignment._lis_indices([4, 3, 2, 1])) == 1
    assert len(alignment._lis_indices([3, 1, 2, 5, 4])) == 3


# --------------------------------------------------------------------------- #
# Matching strategy
# --------------------------------------------------------------------------- #


def test_an_equal_clause_number_matches_at_a_lower_bar():
    """Stage 2: the number is independent evidence, so the bar drops."""
    old = [clause(1, "4.2 The register shall record every exemption claimed.", "4.2")]
    new = [
        clause(
            1,
            "4.2 The register shall record every exemption and every direction "
            "received from the authority, with dates.",
            "4.2",
        )
    ]
    config = AlignmentConfig(match_threshold=0.95, number_match_threshold=0.20)
    (change,) = align(old, new, config)
    assert change.match_basis == "clause_number"
    assert change.change_type == "modified"


def test_an_equal_clause_number_on_unrelated_text_is_not_forced_to_match():
    """'1.' appears in every annexure; the number alone must not be enough."""
    old = [clause(1, "1. Provisions concerning the maintenance of the register.", "1.")]
    new = [clause(1, "1. Tabular statement of district-wise population data.", "1.")]
    changes = align(old, new, AlignmentConfig(number_match_threshold=0.5, match_threshold=0.5))
    assert {c.change_type for c in changes} == {"added", "removed"}


def test_an_ambiguous_clause_number_is_not_used_as_evidence():
    """A marker the segmenter could not disambiguate is not reliable evidence."""
    old = [clause(1, "(i) Provisions concerning the register maintenance.", "(i)", ambiguous=True)]
    new = [clause(1, "(i) A different subject entirely: audit timetables.", "(i)", ambiguous=True)]
    changes = align(old, new, AlignmentConfig(number_match_threshold=0.01, match_threshold=0.9))
    assert {c.change_type for c in changes} == {"added", "removed"}


def test_nothing_is_forced_into_a_match_below_the_threshold():
    old = [clause(1, "Provisions concerning the register and its maintenance.")]
    new = [clause(1, "Tabular statement of district-wise population figures.")]
    changes = align(old, new, AlignmentConfig(match_threshold=0.9))
    assert sorted(c.change_type for c in changes) == ["added", "removed"]


def test_matching_is_one_to_one():
    """Two old clauses cannot both claim the same new clause."""
    old = [
        clause(1, "The authority shall maintain a register of institutions."),
        clause(2, "The authority shall maintain a register of institutions too."),
    ]
    new = [clause(1, "The authority shall maintain a register of institutions.")]
    changes = align(old, new)
    matched = [c for c in changes if c.old is not None and c.new is not None]
    assert len(matched) == 1
    assert sum(1 for c in changes if c.change_type == "removed") == 1


def test_duplicate_identical_text_is_paired_deterministically():
    """Repeated boilerplate must pair the same way on every run."""
    old = [
        clause(1, "Yours faithfully", "1."),
        clause(2, "Yours faithfully", "2."),
    ]
    new = [
        clause(1, "Yours faithfully", "2."),
        clause(2, "Yours faithfully", "1."),
    ]
    first = [(c.old.clause_id, c.new.clause_id) for c in align(old, new)]
    second = [(c.old.clause_id, c.new.clause_id) for c in align(old, new)]
    assert first == second
    # The clause number is preferred over position, so '1.' pairs with '1.'.
    pairs = {c.old.clause_number: c.new.clause_number for c in align(old, new)}
    assert pairs == {"1.": "1.", "2.": "2."}


# --------------------------------------------------------------------------- #
# Ambiguity
# --------------------------------------------------------------------------- #


def test_a_near_tie_is_reported_as_ambiguous():
    old = [clause(1, "The authority shall review the register of institutions annually.")]
    new = [
        clause(1, "The authority shall review the register of institutions every second year."),
        clause(2, "The authority shall review the register of institutions every third year."),
    ]
    changes = align(old, new, AlignmentConfig(ambiguity_margin=0.2))
    matched = [c for c in changes if c.old is not None and c.new is not None]
    assert len(matched) == 1
    assert matched[0].ambiguous is True
    assert matched[0].confidence == "low"
    assert matched[0].runner_up_clause_id is not None
    assert matched[0].runner_up_similarity is not None


def test_a_clear_winner_is_not_ambiguous():
    old = [clause(1, "The authority shall review the register of institutions annually.")]
    new = [
        clause(1, "The authority shall review the register of institutions annually."),
        clause(2, "Entirely unrelated provisions concerning foreign exchange."),
    ]
    matched = [c for c in align(old, new) if c.old is not None]
    assert matched[0].ambiguous is False


def test_the_ambiguity_margin_is_configurable():
    old = [clause(1, "The authority shall review the register of institutions annually.")]
    new = [
        clause(1, "The authority shall review the register of institutions every second year."),
        clause(2, "The authority shall review the register of institutions every third year."),
    ]
    strict = [c for c in align(old, new, AlignmentConfig(ambiguity_margin=0.0)) if c.old]
    loose = [c for c in align(old, new, AlignmentConfig(ambiguity_margin=0.9)) if c.old]
    assert strict[0].ambiguous is False
    assert loose[0].ambiguous is True


# --------------------------------------------------------------------------- #
# Thresholds and config
# --------------------------------------------------------------------------- #


def test_the_threshold_changes_the_outcome():
    old = [clause(1, "The designated officer shall maintain the fixture register.")]
    new = [clause(1, "The designated officer shall maintain the fixture ledger instead.")]
    loose = align(old, new, AlignmentConfig(match_threshold=0.1))
    strict = align(old, new, AlignmentConfig(match_threshold=0.99))
    assert [c.change_type for c in loose] == ["modified"]
    assert sorted(c.change_type for c in strict) == ["added", "removed"]


def test_the_config_round_trips_to_a_dict():
    config = AlignmentConfig(match_threshold=0.6)
    assert config.to_dict()["match_threshold"] == 0.6
    assert set(config.to_dict()) == {
        "match_threshold",
        "number_match_threshold",
        "ambiguity_margin",
        "high_confidence_similarity",
        "fold_clause_numbers",
    }


# --------------------------------------------------------------------------- #
# Degenerate inputs
# --------------------------------------------------------------------------- #


def test_two_empty_versions_produce_no_changes():
    assert align([], []) == []


def test_an_empty_old_version_makes_everything_added():
    changes = align([], [clause(1, "1. A clause.", "1.")])
    assert [c.change_type for c in changes] == ["added"]


def test_an_empty_new_version_makes_everything_removed():
    changes = align([clause(1, "1. A clause.", "1.")], [])
    assert [c.change_type for c in changes] == ["removed"]


# --------------------------------------------------------------------------- #
# Change invariants
# --------------------------------------------------------------------------- #


def test_a_change_must_have_at_least_one_side():
    with pytest.raises(ValueError):
        Change("added")


@pytest.mark.parametrize("change_type", CHANGE_TYPES)
def test_every_change_type_is_constructible_with_the_right_sides(change_type):
    old = clause(1, "old text here") if change_type != "added" else None
    new = clause(1, "new text here") if change_type != "removed" else None
    assert Change(change_type, old=old, new=new).change_type == change_type


def test_an_added_change_cannot_carry_an_old_clause():
    with pytest.raises(ValueError):
        Change("added", old=clause(1, "x"), new=clause(1, "y"))


def test_a_modified_change_needs_both_sides():
    with pytest.raises(ValueError):
        Change("modified", new=clause(1, "y"))


def test_an_unknown_change_type_is_refused():
    with pytest.raises(ValueError):
        Change("rewritten", old=clause(1, "x"), new=clause(1, "y"))


# --------------------------------------------------------------------------- #
# Confidence: a label from stated rules, never a probability
# --------------------------------------------------------------------------- #


def test_an_exact_text_match_is_high_confidence():
    change = Change(
        "unchanged", old=clause(1, "a"), new=clause(1, "a"),
        similarity=1.0, match_basis="exact_text",
    )
    assert confidence_label(change) == "high"


def test_an_ambiguous_match_is_low_confidence_whatever_it_scored():
    change = Change(
        "modified", old=clause(1, "a"), new=clause(1, "b"),
        similarity=0.99, match_basis="similarity", ambiguous=True,
    )
    assert confidence_label(change) == "low"


def test_a_weak_similarity_match_is_medium_confidence():
    change = Change(
        "modified", old=clause(1, "a"), new=clause(1, "b"),
        similarity=0.5, match_basis="similarity",
    )
    assert confidence_label(change) == "medium"


def test_an_addition_is_medium_and_carries_no_score():
    change = Change("added", new=clause(1, "a"))
    assert confidence_label(change) == "medium"
    assert change.similarity is None


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_every_change_once():
    old = [clause(i, f"Clause {i} concerning a distinct fixture subject.") for i in range(1, 5)]
    new = old[:2] + [clause(9, "An entirely new provision about audit timetables.")]
    changes = align(old, new)
    summary = summarize(changes)
    assert sum(summary["counts"].values()) == len(changes)
    assert sum(summary["confidence_counts"].values()) == len(changes)


def test_the_similarity_mean_carries_its_caveat():
    changes = align([clause(1, "1. A clause.", "1.")], [clause(1, "1. A clause.", "1.")])
    summary = summarize(changes)
    assert "not a legal-equivalence score" in summary["similarity_mean_note"]


def test_an_empty_comparison_summarises_without_dividing_by_zero():
    summary = summarize([])
    assert summary["total_changes"] == 0
    assert summary["similarity_mean"] is None
    assert summary["unchanged_ratio"] is None


# --------------------------------------------------------------------------- #
# Word diff
# --------------------------------------------------------------------------- #


def test_the_word_diff_reproduces_both_inputs_exactly():
    """A diff that loses a character would display text not in the document."""
    old = "1. The bank shall  report within 30 days.\nSecond line here."
    new = "1. The bank shall report within 60 days, in the prescribed form."
    runs = explain.word_diff(old, new)
    assert "".join(run["old"] for run in runs) == old
    assert "".join(run["new"] for run in runs) == new


def test_the_word_diff_marks_the_changed_word():
    runs = explain.word_diff("within 30 days", "within 60 days")
    changed = [run for run in runs if run["op"] != "equal"]
    assert changed
    assert any("30" in run["old"] and "60" in run["new"] for run in changed)


def test_the_word_diff_of_identical_text_is_all_equal():
    runs = explain.word_diff("the same text", "the same text")
    assert {run["op"] for run in runs} == {"equal"}


def test_a_case_only_difference_is_equal_in_the_diff():
    runs = explain.word_diff("The Bank", "the bank")
    assert {run["op"] for run in runs} == {"equal"}


def test_diff_stats_ignore_whitespace_tokens():
    stats = explain.diff_stats("one two three", "one  two\nthree")
    assert stats["words_added"] == 0
    assert stats["words_removed"] == 0
    assert stats["words_kept"] == 3


def test_diff_stats_count_an_insertion():
    stats = explain.diff_stats("one two", "one two three four")
    assert stats["words_added"] == 2
    assert stats["words_removed"] == 0
    assert stats["word_count_delta"] == 2


# --------------------------------------------------------------------------- #
# Explanations: descriptive, never legal
# --------------------------------------------------------------------------- #


def test_identical_text_is_explained_as_identical():
    assert "identical" in explain.explain_change("same text", "same text").lower()


def test_a_renumbering_is_explained_as_a_renumbering():
    text = explain.explain_change(
        "3. The officer shall report.", "4. The officer shall report.", 1.0
    )
    assert "numbering" in text.lower()


def test_a_changed_period_is_named_in_the_explanation():
    text = explain.explain_change(
        "Submit the return within 30 days of the quarter end.",
        "Submit the return within 60 days of the quarter end.",
        0.9,
    )
    assert "30 days" in text and "60 days" in text


def test_a_changed_percentage_is_named_in_the_explanation():
    text = explain.explain_change(
        "A target of 40 per cent applies to the sector.",
        "A target of 75 per cent applies to the sector.",
        0.9,
    )
    assert "40 per cent" in text and "75 per cent" in text


def test_every_modified_explanation_points_at_the_source():
    text = explain.explain_change(
        "Submit the return within 30 days.", "Submit the return within 60 days.", 0.9
    )
    assert "read the quoted text" in text.lower()


def test_an_explanation_never_asserts_a_legal_effect():
    """§5's hard rule, as a lexical check over every explanation path."""
    forbidden = ("is now legal", "is illegal", "you must now", "you no longer have to")
    samples = [
        explain.explain_change("within 30 days", "within 60 days", 0.9),
        explain.explain_added("1. A new obligation to report shall apply."),
        explain.explain_removed("1. An obligation that has gone."),
        explain.explain_unchanged(),
        explain.explain_unchanged(renumbered=True),
        explain.explain_unchanged(reordered=True),
    ]
    for text in samples:
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase not in lowered, text


def test_a_removal_explanation_states_the_alternative_readings():
    text = explain.explain_removed("1. A clause.")
    assert "rewritten" in text.lower() or "threshold" in text.lower()


def test_a_whitespace_only_change_is_reported_as_identical():
    """The payoff of the comparison key, stated as a test.

    Two extractions of the same printed line differ in whitespace all the
    time. The key folds that, so this reaches the "identical" branch rather
    than being reported as a modification at all -- which is the behaviour
    the corpus needs. ``explain_change``'s later "only spacing differs"
    branch is defensive: it covers a difference the key does *not* fold but
    the word tokens do, and it is not reachable from whitespace alone.
    """
    text = explain.explain_change("one two three", "one  two\tthree", 0.99)
    assert "identical" in text.lower()


def test_describe_dispatches_on_change_type():
    added = Change("added", new=clause(1, "1. A new obligation shall apply."))
    removed = Change("removed", old=clause(1, "1. A departed obligation."))
    unchanged = Change(
        "unchanged", old=clause(1, "1. Same."), new=clause(1, "1. Same."),
        similarity=1.0, match_basis="exact_text",
    )
    assert "newer version" in explain.describe(added)
    assert "older version" in explain.describe(removed)
    assert "identical" in explain.describe(unchanged)


def test_entity_deltas_are_empty_when_nothing_changed():
    assert explain.entity_deltas("within 30 days", "within 30 days") == []


def test_entity_deltas_report_the_label_that_moved():
    deltas = explain.entity_deltas("within 30 days", "within 60 days")
    assert [delta["label"] for delta in deltas] == ["PERIOD"]
    assert deltas[0]["old"] == ["30 days"]
    assert deltas[0]["new"] == ["60 days"]


# --------------------------------------------------------------------------- #
# Entity extraction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,label,expected",
    [
        ("dated July 01, 2016 and", "DATE", "July 01, 2016"),
        ("on 31/03/2017 at", "DATE", "31/03/2017"),
        ("within 30 days of", "PERIOD", "30 days"),
        ("for three months after", "PERIOD", "three months"),
        ("a sum of Rs. 25,000 crore was", "MONEY", "Rs. 25,000 crore"),
        ("a rate of 7.5 per cent applies", "PERCENT", "7.5 per cent"),
        ("a rate of 12% applies", "PERCENT", "12%"),
        ("the bank shall report", "OBLIGATION", "shall"),
        ("the Reserve Bank of India may", "AUTHORITY", "Reserve Bank of India"),
        ("set out in Annexure II below", "SECTION_REF", "Annexure II"),
        ("under Section 12 of the Act", "SECTION_REF", "Section 12"),
        ("see RBI/2017-18/7 for", "REFERENCE_NO", "RBI/2017-18/7"),
    ],
)
def test_each_label_finds_its_pattern(text, label, expected):
    found = [e.text for e in entities.extract_entities(text) if e.label == label]
    assert expected in found, found


def test_every_entity_span_slices_back_to_its_own_text():
    """An offset that does not resolve cannot be highlighted on a page."""
    text = (
        "Please refer to FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17 dated July 01, "
        "2016. Banks shall report within 30 days at 7.5 per cent."
    )
    for entity in entities.extract_entities(text):
        assert text[entity.start : entity.end].strip() == entity.text


def test_entities_do_not_overlap():
    text = "RBI/2017-18/7 dated July 01, 2017 requires 30 days at 7.5 per cent."
    spans = [(e.start, e.end) for e in entities.extract_entities(text)]
    for i, (start, end) in enumerate(spans):
        for other_start, other_end in spans[i + 1 :]:
            assert not (start < other_end and other_start < end)


def test_a_reference_number_is_not_also_read_as_a_date():
    """Pattern precedence, and the defect it was written to fix."""
    text = "FIDD.GSSD.BC.No.05/09.10.01/2017-18 July 01, 2017"
    dates = [e.text for e in entities.extract_entities(text) if e.label == "DATE"]
    assert dates == ["July 01, 2017"]


def test_filtering_by_label_does_not_change_pattern_precedence():
    """The real bug: asking for DATE alone must not reveal a reference number.

    Found on the 2017 Minority Communities circular, whose file number
    ``FIDD.GSSD.BC.No.05/09.10.01/2017-18`` was reported as the dates
    ``05/09.10`` and ``01/09.10`` when the caller filtered for DATE.
    """
    text = "FIDD.GSSD.BC.No.05/09.10.01/2017-18 July 01, 2017"
    unfiltered = [e.text for e in entities.extract_entities(text) if e.label == "DATE"]
    filtered = [e.text for e in entities.extract_entities(text, labels=["DATE"])]
    assert filtered == unfiltered == ["July 01, 2017"]


def test_an_unknown_label_raises_rather_than_returning_nothing():
    with pytest.raises(ValueError, match="unknown entity label"):
        entities.extract_entities("any text", labels=["DEADLINE"])


def test_empty_text_yields_no_entities():
    assert entities.extract_entities("") == []


def test_extract_by_label_groups_without_losing_anything():
    text = "Banks shall report within 30 days and within 60 days."
    flat = entities.extract_entities(text)
    grouped = entities.extract_by_label(text)
    assert sum(len(v) for v in grouped.values()) == len(flat)
    assert [e.text for e in grouped["PERIOD"]] == ["30 days", "60 days"]


def test_entity_texts_deduplicates_in_order_of_appearance():
    text = "within 30 days, and again within 30 days, then within 60 days"
    assert entities.entity_texts(text, "PERIOD") == ["30 days", "60 days"]


def test_the_obligation_label_is_documented_as_a_cue_not_a_fact():
    """'shall not be required to' matching OBLIGATION is a known limit."""
    found = [e.text for e in entities.extract_entities("banks shall not be required to report")]
    assert any("shall not" in text for text in found)
    assert "lexical cue" in entities.ENTITY_LABELS["OBLIGATION"]


def test_every_label_in_the_vocabulary_is_documented():
    for label in entities.ENTITY_LABELS:
        assert entities.ENTITY_LABELS[label].strip()


def test_the_benchmark_reports_counts_and_timing_but_no_accuracy():
    report = entities.benchmark_regex(
        ["Banks shall report within 30 days.", "No entities in this sentence at all."]
    )
    assert report["condition"] == "regex"
    assert report["clauses"] == 2
    assert report["mentions_total"] > 0
    assert report["ms_per_clause"] is not None
    # A count is not a score, and the benchmark must not pretend otherwise.
    assert not any(
        key in report for key in ("precision", "recall", "f1", "accuracy")
    )


def test_the_benchmark_handles_an_empty_corpus():
    report = entities.benchmark_regex([])
    assert report["clauses"] == 0
    assert report["ms_per_clause"] is None


# --------------------------------------------------------------------------- #
# Module boundaries
# --------------------------------------------------------------------------- #


def test_alignment_does_not_import_entity_extraction_or_wording():
    """Entity patterns must never be able to change a classification."""
    source = (alignment.__file__,)
    text = open(source[0], encoding="utf-8").read()
    assert "from backend.app.comparison.entities" not in text
    assert "from backend.app.comparison.explain" not in text


def test_no_write_statement_appears_in_the_comparison_package():
    """§9: nothing downstream of extraction may modify document data."""
    import pathlib

    package = pathlib.Path("backend/app/comparison")
    forbidden = ("insert into", "update ", "delete from", "drop ", "truncate", "alter ")
    for path in sorted(package.glob("*.py")):
        body = path.read_text(encoding="utf-8").lower()
        # Strip docstrings and comments crudely: this is a guard, not a parser,
        # and a false positive here is a prompt to look rather than a problem.
        statements = [
            line
            for line in body.splitlines()
            if not line.lstrip().startswith("#")
        ]
        joined = "\n".join(statements)
        for phrase in forbidden:
            assert f'"{phrase}' not in joined and f"'{phrase}" not in joined, (
                f"{path.name} appears to contain a write statement: {phrase!r}"
            )


def test_the_package_exports_its_public_surface():
    import backend.app.comparison as package

    for name in (
        "align",
        "summarize",
        "AlignmentConfig",
        "ClauseRef",
        "Change",
        "comparison_key",
        "extract_entities",
        "word_diff",
        "ComparisonService",
        "comparison_id_for",
    ):
        assert hasattr(package, name), name


# --------------------------------------------------------------------------- #
# evaluation/comparison_eval.py: the honesty guards, without a database
# --------------------------------------------------------------------------- #


def test_the_comparison_evaluation_declares_that_it_has_no_ground_truth():
    """The guard against an invented alignment accuracy appearing later.

    There is no clause-level annotation for the real RBI corpus, so the
    evaluation reports invariants and descriptive counts and nothing that
    reads as a score. If someone adds annotations and a real metric, this
    test should be updated deliberately -- not deleted because it failed.
    """
    from evaluation import comparison_eval

    source = open(comparison_eval.__file__, encoding="utf-8").read()
    assert "no alignment accuracy" in source
    for forbidden in ("alignment_precision", "alignment_recall", "alignment_f1"):
        assert forbidden not in source, forbidden


def test_the_comparison_evaluation_summary_leads_with_the_caveat():
    from evaluation import comparison_eval

    report = {
        "ground_truth": "NONE. No clause-level annotation exists for these documents.",
        "corpus": {"documents": 6},
        "pairs_evaluated": 0,
        "declared_pairs": 0,
        "consecutive_pairs": 0,
        "invariants": {"pairs_passing_all": 0, "pairs_failing": 0, "failures": []},
        "evidence": {
            "sides_checked": 0,
            "sides_resolved": 0,
            "resolution_rate": None,
            "by_status": {},
        },
        "self_comparison": {
            "version_id": "VER-0001",
            "clauses": 0,
            "counts": {},
            "all_unchanged": True,
            "no_spurious_reordering": True,
            "no_spurious_renumbering": True,
        },
        "threshold_sensitivity": {"pair": "x->y", "sweep": []},
        "timing": {"align_ms_median": 0.0, "align_ms_max": 0.0, "largest_pair": "x"},
        "pairs": [],
    }
    text = comparison_eval._format(report)
    head = text[: text.index("CORPUS")]
    assert "READ THIS FIRST" in head
    assert "NONE" in head


def test_the_evaluation_counters_merge_without_losing_a_key():
    from evaluation import comparison_eval

    merged = comparison_eval._merge_counters(
        [{"a": 1, "b": 2}, {"b": 3, "c": 4}, {}]
    )
    assert merged == {"a": 1, "b": 5, "c": 4}


def test_the_evaluation_wrapper_never_drops_a_word():
    from evaluation import comparison_eval

    text = " ".join(f"word{i}" for i in range(40))
    assert " ".join(comparison_eval._wrap(text, 30)).split() == text.split()
