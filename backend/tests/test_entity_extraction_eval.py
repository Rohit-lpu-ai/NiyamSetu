"""The entity-extraction benchmark: the gold file, the scorer, and its honesty.

Three jobs here, and the third is the one that matters most:

1. **The committed gold file validates.** Every annotated span must resolve to
   real characters in the clause it belongs to, no two gold spans may overlap,
   and every label must be one the extractor publishes. A benchmark computed
   against a broken gold file is worse than no benchmark.
2. **The scorer computes what it says it computes.** Precision, recall and F1
   are checked against hand-worked cases, including the degenerate ones.
3. **The unrun conditions cannot silently start emitting a number.** CRF and
   transformer are declared ``NOT RUN`` with a reason;
   ``CLAUDE.md`` §24 rule 12 and the project's existing ``PENDING`` scorers
   (``evaluation/annotator_agreement.py``,
   ``evaluation/cleaning_correctness.py``, ``evaluation/extraction_fidelity.py``)
   set the precedent that a test pins that status so a fabricated metric
   cannot appear by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import entity_extraction_eval as ev

GOLD = Path("data") / "annotations" / "entities" / "gold.json"


@pytest.fixture(scope="module")
def gold():
    if not GOLD.exists():
        pytest.skip(f"{GOLD} is absent")
    return ev.load_gold(GOLD)


# --------------------------------------------------------------------------- #
# The gold file
# --------------------------------------------------------------------------- #


def test_the_committed_gold_file_validates(gold):
    assert ev.validate_gold(gold) == []


def test_every_gold_span_slices_back_to_its_own_surface_string(gold):
    for clause in gold["clauses"]:
        for span in clause["spans"]:
            assert span["start"] >= 0, (clause["clause_id"], span)
            assert (
                clause["text"][span["start"] : span["end"]] == span["text"]
            ), (clause["clause_id"], span)


def test_the_gold_file_records_its_sampling_and_its_annotator(gold):
    """Rule 6: a metric on a sample states the sample size and how it was drawn."""
    assert gold["sampling"]["seed"]
    assert gold["sampling"]["method"]
    assert gold["sampling"]["sample"] == len(gold["clauses"])
    assert gold["annotator"]["count"] == 1
    assert gold["annotator"]["independent_of_system"] is False
    assert "PENDING" in gold["annotator"]["agreement"]


def test_the_gold_file_points_at_its_guideline(gold):
    guideline = Path(gold["guideline"])
    assert guideline.exists(), guideline
    body = guideline.read_text(encoding="utf-8")
    # The guideline must carry the same caveat the file does; a reader who
    # finds only one of the two has been told half of it.
    assert "not independent of the system" in body
    assert "diagnostic" in body.lower()


def test_the_sample_keeps_its_clauses_with_no_entities(gold):
    """Hand-picking only clauses with entities would flatter precision."""
    empty = [clause for clause in gold["clauses"] if not clause["spans"]]
    assert empty, "a sample with no empty clauses cannot show false positives"


def test_every_gold_label_is_one_the_extractor_publishes(gold):
    from backend.app.comparison.entities import ENTITY_LABELS

    for clause in gold["clauses"]:
        for span in clause["spans"]:
            assert span["label"] in ENTITY_LABELS


def test_an_arguable_annotation_call_is_recorded_not_hidden(gold):
    """The guideline promises calls are recorded in the clause note."""
    notes = " ".join(clause.get("note") or "" for clause in gold["clauses"])
    assert "arguable" in notes.lower()


def test_the_annotation_correction_is_recorded_in_the_file(gold):
    """Two mentions were missed in the first pass and the benchmark found them.

    The fix is recorded in the clause's own note rather than quietly applied,
    because "the gold set was amended after seeing the extractor's output" is
    exactly the kind of thing a reader of a metric needs to know.
    """
    notes = " ".join(clause.get("note") or "" for clause in gold["clauses"])
    assert "ANNOTATION CORRECTION" in notes


# --------------------------------------------------------------------------- #
# The scorer
# --------------------------------------------------------------------------- #


def _span(label: str, start: int, end: int, text: str = "x"):
    return {"label": label, "start": start, "end": end, "text": text}


def test_exact_matching_requires_identical_boundaries_and_label():
    gold_spans = [_span("DATE", 0, 5)]
    pairs, fp, fn = ev._match_exact([_span("DATE", 0, 5)], gold_spans)
    assert (pairs, fp, fn) == ([(0, 0)], [], [])

    pairs, fp, fn = ev._match_exact([_span("DATE", 0, 4)], gold_spans)
    assert pairs == [] and fp == [0] and fn == [0]

    pairs, fp, fn = ev._match_exact([_span("PERIOD", 0, 5)], gold_spans)
    assert pairs == [] and fp == [0] and fn == [0]


def test_relaxed_matching_accepts_an_overlap_with_the_same_label():
    gold_spans = [_span("DATE", 10, 20)]
    pairs, fp, fn = ev._match_relaxed([_span("DATE", 15, 25)], gold_spans)
    assert pairs == [(0, 0)] and fp == [] and fn == []


def test_relaxed_matching_rejects_a_touching_but_non_overlapping_span():
    """Adjacency is not overlap: a span ending where another begins shares no character."""
    pairs, _, _ = ev._match_relaxed([_span("DATE", 0, 10)], [_span("DATE", 10, 20)])
    assert pairs == []


def test_relaxed_matching_is_one_to_one():
    """One long prediction covering two gold spans may only claim one."""
    predicted = [_span("AUTHORITY", 0, 100)]
    gold_spans = [_span("AUTHORITY", 0, 10), _span("AUTHORITY", 50, 60)]
    pairs, fp, fn = ev._match_relaxed(predicted, gold_spans)
    assert len(pairs) == 1
    assert len(fn) == 1


def test_relaxed_matching_credits_the_largest_overlap():
    predicted = [_span("DATE", 40, 60)]
    gold_spans = [_span("DATE", 0, 45), _span("DATE", 45, 70)]
    pairs, _, _ = ev._match_relaxed(predicted, gold_spans)
    # 15 characters overlap with the second gold span, 5 with the first.
    assert pairs == [(0, 1)]


def test_precision_recall_and_f1_are_the_usual_formulas():
    row = ev._prf(true_positives=8, false_positives=2, false_negatives=4)
    assert row["precision"] == pytest.approx(0.8)
    assert row["recall"] == pytest.approx(0.6667, abs=1e-4)
    assert row["f1"] == pytest.approx(0.7273, abs=1e-4)


def test_an_undefined_metric_is_none_rather_than_zero():
    """Predicting nothing when there is nothing is not 0% precision."""
    row = ev._prf(0, 0, 0)
    assert row["precision"] is None
    assert row["recall"] is None
    assert row["f1"] is None


def test_zero_true_positives_gives_zero_not_none():
    row = ev._prf(0, 3, 4)
    assert row["precision"] == 0.0
    assert row["recall"] == 0.0
    assert row["f1"] is None  # 2PR/(P+R) with P+R == 0


# --------------------------------------------------------------------------- #
# Honesty: the conditions that were not run
# --------------------------------------------------------------------------- #


def test_the_brief_s_three_conditions_are_all_declared():
    assert set(ev.CONDITIONS) == {"regex", "crf", "transformer"}


def test_only_the_regex_condition_claims_to_be_measured():
    assert ev.CONDITIONS["regex"]["status"] == "MEASURED"
    assert ev.CONDITIONS["crf"]["status"] == "NOT RUN"
    assert ev.CONDITIONS["transformer"]["status"] == "NOT RUN"


@pytest.mark.parametrize("condition", ["crf", "transformer"])
def test_an_unrun_condition_states_its_reason_and_what_would_change_it(condition):
    """A missing metric must come with the reason, not a blank."""
    meta = ev.CONDITIONS[condition]
    assert len(meta["reason"]) > 120, meta["reason"]
    assert meta["what_would_change_this"]
    assert meta["implementation"] is None


@pytest.mark.parametrize("condition", ["crf", "transformer"])
def test_an_unrun_condition_carries_no_numbers(condition):
    """The guard against a fabricated metric appearing by accident."""
    meta = ev.CONDITIONS[condition]
    for key in ("precision", "recall", "f1", "accuracy", "macro_f1", "score"):
        assert key not in meta, (condition, key)


def test_the_summary_leads_with_the_caveat_not_the_number(gold):
    """Someone who reads only the first lines must still see the caveat."""
    result = ev.score(gold)
    result["conditions"] = ev.CONDITIONS
    text = ev._format(result, gold)
    head = text[: text.index("CONDITIONS")]
    assert "DIAGNOSTIC" in head
    assert "independent of system: False" in head


def test_the_summary_names_every_condition_and_its_status(gold):
    result = ev.score(gold)
    text = ev._format(result, gold)
    for condition in ev.CONDITIONS:
        assert condition in text
    assert "NOT RUN" in text


# --------------------------------------------------------------------------- #
# The measured result, as invariants rather than pinned numbers
# --------------------------------------------------------------------------- #


def test_scoring_the_committed_gold_produces_both_match_modes(gold):
    result = ev.score(gold)
    assert set(result["modes"]) == {"exact", "relaxed"}
    for mode in result["modes"].values():
        assert mode["overall"]["f1"] is not None


def test_relaxed_matching_never_scores_worse_on_precision_than_exact(gold):
    """Relaxed is a superset of exact, so its precision cannot be lower."""
    result = ev.score(gold)
    assert (
        result["modes"]["relaxed"]["overall"]["precision"]
        >= result["modes"]["exact"]["overall"]["precision"]
    )


def test_every_failure_case_is_listed_in_full(gold):
    """Rule 3: every experiment reports its failure cases."""
    result = ev.score(gold)
    exact = result["modes"]["exact"]
    assert len(exact["false_positives"]) == exact["overall"]["false_positives"]
    assert len(exact["false_negatives"]) == exact["overall"]["false_negatives"]
    for item in exact["false_positives"] + exact["false_negatives"]:
        assert item["clause_id"] and item["label"] and item["text"]


def test_inference_time_is_measured_per_clause(gold):
    result = ev.score(gold)
    assert result["timing"]["ms_per_clause"] is not None
    assert result["timing"]["ms_per_clause"] >= 0.0
    assert result["timing"]["chars_per_second"] > 0


def test_the_obligation_and_section_patterns_are_perfect_on_this_sample(gold):
    """The two labels a closed-vocabulary rule genuinely suits.

    Asserted because they are the benchmark's load-bearing positive result: a
    regression that broke either would otherwise only show up as a slightly
    worse overall F1.
    """
    exact = ev.score(gold)["modes"]["exact"]["per_label"]
    for label in ("OBLIGATION", "SECTION_REF"):
        assert exact[label]["f1"] == 1.0, (label, exact[label])


def test_authority_recall_is_the_benchmark_s_headline_weakness(gold):
    """The finding, asserted as a ceiling so it cannot be quietly overstated.

    A closed list of organisation patterns cannot find the individual bank
    names in a district-allocation annexure, and that is the argument for a
    statistical model. If a later change pushes AUTHORITY recall above this
    ceiling, the report's conclusion has changed and this test should be
    updated deliberately rather than deleted.
    """
    exact = ev.score(gold)["modes"]["exact"]["per_label"]["AUTHORITY"]
    assert exact["recall"] < 0.5, exact
    assert exact["false_negatives"] > 20, exact


def test_the_scorer_refuses_to_report_against_a_broken_gold_file(tmp_path):
    broken = tmp_path / "gold.json"
    broken.write_text(
        json.dumps(
            {
                "guideline": "x",
                "corpus": "x",
                "sampling": {},
                "annotator": {},
                "clauses": [
                    {
                        "clause_id": "CLS-1",
                        "version_id": "VER-1",
                        "text": "a clause with no such phrase in it",
                        "entities": [{"label": "DATE", "text": "July 01, 2016"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="do not validate"):
        ev.run(broken, tmp_path / "out", write=False)


def test_validate_reports_an_unknown_label():
    payload = {
        "clauses": [
            {
                "clause_id": "CLS-1",
                "text": "within 30 days",
                "spans": [
                    {"label": "DEADLINE", "text": "30 days", "start": 7, "end": 14}
                ],
            }
        ]
    }
    problems = ev.validate_gold(payload)
    assert any("not a label the extractor publishes" in p for p in problems)


def test_validate_reports_overlapping_gold_spans():
    payload = {
        "clauses": [
            {
                "clause_id": "CLS-1",
                "text": "within 30 days",
                "spans": [
                    {"label": "PERIOD", "text": "30 days", "start": 7, "end": 14},
                    {"label": "DATE", "text": "days", "start": 10, "end": 14},
                ],
            }
        ]
    }
    assert any("overlap" in p for p in ev.validate_gold(payload))
