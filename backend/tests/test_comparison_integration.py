"""Compare against a real PostgreSQL database. Skips without one.

Two clearly separated halves, and the separation is the point:

**Synthetic fixtures** (``synthetic_db``) have ground truth by construction.
``backend/tests/version_pair_builder.py`` builds pairs that each isolate one
case the brief names -- added, removed, modified, unchanged, renumbered,
reordered, ambiguous, multi-page -- and runs them through the real pipeline
into the throwaway test database. These tests assert exact classifications,
because the answer is known.

**The real RBI version-pair corpus** (``real_pairs_db``) has no clause-level
ground truth: nobody has annotated 288 pages of master circulars. Those tests
therefore assert **invariants** -- every clause is classified, every change is
traceable, evidence resolves, the result is deterministic, renumbering is not
reported as modification -- and never an expected count. A test asserting
"54 clauses were removed from the 2016 SC/ST circular" would be asserting
today's behaviour, not correctness.

Both halves use ``NIYAMSETU_TEST_DATABASE_URL``, which
``backend/app/db/config.py`` requires to contain "test" before anything
destructive runs. Neither half touches ``niyamsetu``, ``niyamsetu_sample`` or
``niyamsetu_compare``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from sqlalchemy import text  # noqa: E402

from backend.app.comparison import repository as compare_repository  # noqa: E402
from backend.app.comparison.alignment import AlignmentConfig  # noqa: E402
from backend.app.comparison.service import (  # noqa: E402
    ComparisonNotFound,
    ComparisonService,
    InvalidComparison,
    comparison_id_for,
)
from backend.app.comparison.similarity import comparison_key  # noqa: E402
from backend.app.db import engine as engine_module  # noqa: E402
from backend.app.db.config import (  # noqa: E402
    assert_is_test_url,
    test_database_url as resolve_test_database_url,
)
from backend.app.db.loader import (  # noqa: E402
    declarations_from_tree,
    link_supersedes,
    load_version_directory,
)
from backend.app.db.migrate import migrate  # noqa: E402
from backend.tests.test_db_schema import (  # noqa: E402
    TABLES,
    _create_database_if_missing,
)
from backend.tests.version_pair_builder import (  # noqa: E402
    PAIRS,
    PairSpec,
    build_and_process,
)

pytestmark = pytest.mark.integration

REAL_PAIRS_PROCESSED = Path("data") / "version_pairs" / "processed"
REAL_PAIRS_RAW = Path("data") / "version_pairs" / "raw"


def _fresh_database() -> str:
    """A migrated, empty test database. Skips when none is configured."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("NIYAMSETU_TEST_DATABASE_URL is not set; see .env.example")
    assert_is_test_url(url)
    _create_database_if_missing(url)
    try:
        migrate(url)
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.skip(f"cannot migrate the test database: {exc}")

    with engine_module.transaction(url) as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        for sequence in (
            "documents_public_id_seq",
            "versions_public_id_seq",
            "segments_public_id_seq",
        ):
            conn.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH 1"))
    return url


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory) -> Dict[str, Any]:
    """Every synthetic pair, built and loaded once.

    Module-scoped: building twelve PDFs and running them through extraction,
    cleaning and both segmentation methods per test would make this module
    take minutes for no extra assurance. Every test here only reads.
    """
    url = _fresh_database()
    workroot = tmp_path_factory.mktemp("version_pairs")

    loaded: Dict[str, Dict[str, str]] = {}
    for pair in PAIRS:
        built = build_and_process(pair, workroot / pair.name)
        processed = built["processed_dir"]
        raw = built["raw_dir"]
        public_ids: Dict[str, str] = {}
        for side, fingerprint in built["version_ids"].items():  # type: ignore[union-attr]
            outcome = load_version_directory(
                processed / fingerprint, url=url, raw_dir=raw  # type: ignore[operator]
            )
            assert outcome.version_public_id, (
                f"{pair.name}/{side} did not load: {outcome.message}"
            )
            public_ids[side] = outcome.version_public_id
        link_supersedes(declarations_from_tree(processed), url=url)  # type: ignore[arg-type]
        loaded[pair.name] = public_ids

    yield {"url": url, "versions": loaded}
    engine_module.dispose_engine()


@pytest.fixture(scope="module")
def synthetic_service(synthetic_db) -> ComparisonService:
    return ComparisonService(url=synthetic_db["url"])


@pytest.fixture(scope="module")
def real_pairs_db() -> str:
    """The nine real RBI master-circular issues, loaded into the test database.

    Skips when the corpus is absent, which it is in a fresh clone: the PDFs and
    the pipeline output are git-ignored, and ``data/version_pairs/README.md``
    has the two commands that rebuild them.
    """
    if not REAL_PAIRS_PROCESSED.exists() or not any(REAL_PAIRS_PROCESSED.iterdir()):
        pytest.skip(
            f"{REAL_PAIRS_PROCESSED} is absent; run "
            "data/version_pairs/fetch_version_pairs.py then the extraction "
            "and segmentation CLIs (see data/version_pairs/README.md)"
        )
    url = _fresh_database()
    for directory in sorted(
        child for child in REAL_PAIRS_PROCESSED.iterdir() if child.is_dir()
    ):
        if not (directory / "extraction.json").exists():
            continue
        outcome = load_version_directory(directory, url=url, raw_dir=REAL_PAIRS_RAW)
        assert outcome.version_public_id, f"{directory.name}: {outcome.message}"
    result = link_supersedes(declarations_from_tree(REAL_PAIRS_PROCESSED), url=url)
    assert result["linked_count"] >= 1, (
        "no supersedes link was written; the real pairs would then be "
        "indistinguishable from guessed ones"
    )
    yield url
    engine_module.dispose_engine()


@pytest.fixture(scope="module")
def real_service(real_pairs_db) -> ComparisonService:
    return ComparisonService(url=real_pairs_db)


def _pair(synthetic_db, name: str) -> Dict[str, str]:
    return synthetic_db["versions"][name]


def _spec(name: str) -> PairSpec:
    return next(pair for pair in PAIRS if pair.name == name)


def _compare(service: ComparisonService, versions: Dict[str, str], **kwargs: Any):
    return service.compare(versions["base"], versions["compare"], limit=500, **kwargs)


# --------------------------------------------------------------------------- #
# SYNTHETIC: the four classifications
# --------------------------------------------------------------------------- #


def test_the_core_fixture_produces_all_four_change_types(
    synthetic_service, synthetic_db
):
    result = _compare(synthetic_service, _pair(synthetic_db, "added_removed_modified_unchanged"))
    counts = result["summary"]["counts"]
    for change_type in ("added", "removed", "modified", "unchanged"):
        assert counts[change_type] >= 1, counts


def test_the_changed_deadline_is_found_as_an_entity_delta(
    synthetic_service, synthetic_db
):
    """Not merely 'modified': the report must name 30 days -> 60 days."""
    result = _compare(
        synthetic_service,
        _pair(synthetic_db, "added_removed_modified_unchanged"),
        change_types=["modified"],
    )
    periods = [
        delta
        for item in result["items"]
        for delta in (item.get("entity_deltas") or [])
        if delta["label"] == "PERIOD"
    ]
    assert periods, "no PERIOD delta was reported"
    assert any("30 days" in delta["old"] for delta in periods)
    assert any("60 days" in delta["new"] for delta in periods)


def test_the_explanation_of_the_changed_deadline_names_both_values(
    synthetic_service, synthetic_db
):
    result = _compare(
        synthetic_service,
        _pair(synthetic_db, "added_removed_modified_unchanged"),
        change_types=["modified"],
    )
    explanations = " ".join(item["explanation"] for item in result["items"])
    assert "30 days" in explanations and "60 days" in explanations


# --------------------------------------------------------------------------- #
# SYNTHETIC: renumbering and reordering are not modifications
# --------------------------------------------------------------------------- #


def test_renumbering_alone_reports_no_modified_clause(synthetic_service, synthetic_db):
    """The brief's explicit requirement, end to end through the database."""
    result = _compare(synthetic_service, _pair(synthetic_db, "renumbered_only"))
    counts = result["summary"]["counts"]
    assert counts["modified"] == 0, [
        (i["change_type"], i["old"]["text"][:60] if i["old"] else None)
        for i in result["items"]
    ]
    assert counts["added"] == 1
    assert counts["removed"] == 0
    assert result["summary"]["renumbered_matches"] >= 3


def test_a_renumbered_clause_says_so_in_its_explanation(synthetic_service, synthetic_db):
    result = _compare(synthetic_service, _pair(synthetic_db, "renumbered_only"))
    renumbered = [item for item in result["items"] if item["renumbered"]]
    assert renumbered
    assert all("numbering" in item["explanation"].lower() for item in renumbered)


def test_reordering_alone_reports_no_modified_clause(synthetic_service, synthetic_db):
    result = _compare(synthetic_service, _pair(synthetic_db, "reordered_only"))
    counts = result["summary"]["counts"]
    assert counts["modified"] == 0
    assert counts["added"] == 0
    assert counts["removed"] == 0
    assert result["summary"]["reordered_matches"] >= 1


def test_two_identical_issues_are_entirely_unchanged(synthetic_service, synthetic_db):
    """A circular reissued without amendment. Must succeed, not error."""
    result = _compare(synthetic_service, _pair(synthetic_db, "identical_versions"))
    counts = result["summary"]["counts"]
    assert counts["modified"] == 0
    assert counts["added"] == 0
    assert counts["removed"] == 0
    assert counts["unchanged"] == result["summary"]["total_changes"]
    assert result["summary"]["unchanged_ratio"] == 1.0


# --------------------------------------------------------------------------- #
# SYNTHETIC: ambiguity
# --------------------------------------------------------------------------- #


def test_an_ambiguous_alignment_is_flagged_rather_than_hidden(
    synthetic_service, synthetic_db
):
    result = _compare(
        synthetic_service,
        _pair(synthetic_db, "ambiguous_alignment"),
        config=AlignmentConfig(ambiguity_margin=0.2),
    )
    ambiguous = [item for item in result["items"] if item["ambiguous"]]
    assert ambiguous, [
        (i["change_type"], i["similarity"], i["runner_up_similarity"])
        for i in result["items"]
    ]
    assert ambiguous[0]["confidence"] == "low"
    assert ambiguous[0]["runner_up_clause_id"]


def test_only_ambiguous_filters_to_the_uncertain_matches(
    synthetic_service, synthetic_db
):
    result = _compare(
        synthetic_service,
        _pair(synthetic_db, "ambiguous_alignment"),
        config=AlignmentConfig(ambiguity_margin=0.2),
        only_ambiguous=True,
    )
    assert result["items"]
    assert all(item["ambiguous"] for item in result["items"])
    assert result["total"] < result["total_unfiltered"]


# --------------------------------------------------------------------------- #
# SYNTHETIC: multi-page provenance
# --------------------------------------------------------------------------- #


def test_a_clause_spanning_a_page_break_keeps_both_pages(
    synthetic_service, synthetic_db
):
    result = _compare(synthetic_service, _pair(synthetic_db, "multi_page_clause"))
    spanning = [
        item
        for item in result["items"]
        for side in ("old", "new")
        if item[side] is not None
        and item[side]["page_end"] > item[side]["page_start"]
    ]
    assert spanning, "the multi-page fixture produced no spanning clause"
    for item in spanning:
        for side in ("old", "new"):
            payload = item[side]
            if payload is None:
                continue
            if payload["page_end"] > payload["page_start"]:
                numbers = [page["page_number"] for page in payload["pages"]]
                assert len(numbers) >= 2, numbers
                assert numbers == sorted(numbers)


def test_a_spanning_clause_resolves_evidence_on_every_page_it_touches(
    synthetic_service, synthetic_db
):
    result = _compare(synthetic_service, _pair(synthetic_db, "multi_page_clause"))
    checked = 0
    for item in result["items"]:
        for side in ("old", "new"):
            payload = item[side]
            if payload is None or payload["page_end"] == payload["page_start"]:
                continue
            evidence = payload["evidence"]
            assert len(evidence["spans"]) >= 2
            assert all(span["resolved"] for span in evidence["spans"]), evidence
            checked += 1
    assert checked, "no spanning clause was checked"


def test_the_change_inside_a_spanning_clause_is_still_found(
    synthetic_service, synthetic_db
):
    """The changed figure is on page 2, past the page break."""
    result = _compare(synthetic_service, _pair(synthetic_db, "multi_page_clause"))
    deltas = [
        delta
        for item in result["items"]
        for delta in (item.get("entity_deltas") or [])
        if delta["label"] == "PERIOD"
    ]
    assert any("5 years" in delta["old"] for delta in deltas), deltas
    assert any("8 years" in delta["new"] for delta in deltas), deltas


# --------------------------------------------------------------------------- #
# SYNTHETIC: evidence resolution is verified, not assumed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pair_name", [pair.name for pair in PAIRS])
def test_every_side_of_every_change_resolves_its_evidence(
    synthetic_service, synthetic_db, pair_name
):
    """The feature's central claim, over every fixture.

    'Resolved' means the stored character offsets were sliced out of the
    stored page text and the result matched the clause under the comparison
    key. A failure here is a provenance defect, not a matcher defect.
    """
    result = _compare(synthetic_service, _pair(synthetic_db, pair_name))
    problems: List[str] = []
    for item in result["items"]:
        for side in ("old", "new"):
            payload = item[side]
            if payload is None:
                continue
            evidence = payload["evidence"]
            if evidence["status"] != "resolved":
                problems.append(
                    f"{pair_name} {side} {payload['clause_id']}: "
                    f"{evidence['status']} {evidence['problems']}"
                )
    assert not problems, problems


@pytest.mark.parametrize("pair_name", [pair.name for pair in PAIRS])
def test_every_quote_is_a_verbatim_substring_of_its_clause(
    synthetic_service, synthetic_db, pair_name
):
    """§12.2's rule: a citation's quote must come from the clause it cites."""
    result = _compare(synthetic_service, _pair(synthetic_db, pair_name))
    for item in result["items"]:
        for side in ("old", "new"):
            payload = item[side]
            if payload is None or payload["evidence"]["quote"] is None:
                continue
            quote = comparison_key(payload["evidence"]["quote"])
            clause = comparison_key(payload["text"])
            assert quote in clause or clause in quote, (
                f"{pair_name} {side} {payload['clause_id']}: "
                f"quote is not part of the clause"
            )


# --------------------------------------------------------------------------- #
# SYNTHETIC: version validation
# --------------------------------------------------------------------------- #


def test_comparing_a_version_with_itself_is_refused(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(InvalidComparison, match="nothing to compare"):
        synthetic_service.compare(versions["base"], versions["base"])


def test_an_unknown_version_is_not_found(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(ComparisonNotFound):
        synthetic_service.compare(versions["base"], "VER-does-not-exist")


def test_a_missing_version_id_is_refused(synthetic_service):
    with pytest.raises(InvalidComparison, match="required"):
        synthetic_service.compare(None, None)


def test_versions_of_different_documents_are_refused_by_default(
    synthetic_service, synthetic_db
):
    """Each fixture pair is its own document, so these two never belong together."""
    left = _pair(synthetic_db, "renumbered_only")["base"]
    right = _pair(synthetic_db, "reordered_only")["base"]
    with pytest.raises(InvalidComparison, match="allow_cross_document"):
        synthetic_service.compare(left, right)


def test_a_cross_document_comparison_is_allowed_but_warned_about(
    synthetic_service, synthetic_db
):
    left = _pair(synthetic_db, "renumbered_only")["base"]
    right = _pair(synthetic_db, "reordered_only")["base"]
    result = synthetic_service.compare(left, right, allow_cross_document=True)
    assert result["same_document"] is False
    assert any(w.startswith("CROSS_DOCUMENT") for w in result["warnings"])


def test_a_document_id_that_matches_neither_version_is_refused(
    synthetic_service, synthetic_db
):
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(InvalidComparison, match="matches neither version"):
        synthetic_service.compare(
            versions["base"], versions["compare"], document_id="DOC-9999"
        )


def test_an_unknown_method_is_refused(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(InvalidComparison, match="not supported"):
        synthetic_service.compare(
            versions["base"], versions["compare"], method="sentence"
        )


def test_a_number_threshold_above_the_match_threshold_is_refused(
    synthetic_service, synthetic_db
):
    """An equal clause number must lower the bar, never raise it."""
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(InvalidComparison, match="number_match_threshold"):
        synthetic_service.compare(
            versions["base"],
            versions["compare"],
            config=AlignmentConfig(match_threshold=0.2, number_match_threshold=0.9),
        )


def test_an_inverted_similarity_range_is_refused(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "identical_versions")
    with pytest.raises(InvalidComparison, match="min_similarity"):
        synthetic_service.compare(
            versions["base"],
            versions["compare"],
            min_similarity=0.9,
            max_similarity=0.1,
        )


# --------------------------------------------------------------------------- #
# SYNTHETIC: direction
# --------------------------------------------------------------------------- #


def test_a_declared_supersedes_link_fixes_the_direction(synthetic_service, synthetic_db):
    """Asked the wrong way round, the service swaps and says it swapped."""
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = synthetic_service.compare(versions["compare"], versions["base"], limit=500)
    assert result["base_version"]["version_id"] == versions["base"]
    assert result["compare_version"]["version_id"] == versions["compare"]
    assert any(w.startswith("DIRECTION_SWAPPED") for w in result["warnings"])


def test_the_right_way_round_produces_no_direction_warning(
    synthetic_service, synthetic_db
):
    result = _compare(
        synthetic_service, _pair(synthetic_db, "added_removed_modified_unchanged")
    )
    assert not any(w.startswith("DIRECTION") for w in result["warnings"])


def test_swapping_the_sides_does_not_invert_the_labels(synthetic_service, synthetic_db):
    """The payoff: the same pair asked either way gives the same answer."""
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    forward = synthetic_service.compare(versions["base"], versions["compare"], limit=500)
    backward = synthetic_service.compare(versions["compare"], versions["base"], limit=500)
    assert forward["summary"]["counts"] == backward["summary"]["counts"]
    assert forward["comparison_id"] == backward["comparison_id"]


# --------------------------------------------------------------------------- #
# SYNTHETIC: filters, pagination and determinism
# --------------------------------------------------------------------------- #


def test_filtering_by_change_type_narrows_the_result(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    everything = _compare(synthetic_service, versions)
    only_added = _compare(synthetic_service, versions, change_types=["added"])
    assert only_added["total"] < everything["total"]
    assert {item["change_type"] for item in only_added["items"]} == {"added"}
    assert only_added["total_unfiltered"] == everything["total"]


def test_a_similarity_filter_does_not_silently_drop_unscored_changes(
    synthetic_service, synthetic_db
):
    """An added clause has no score; a min_similarity must not hide it."""
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = _compare(synthetic_service, versions, min_similarity=0.99)
    assert any(item["change_type"] == "added" for item in result["items"])


def test_pagination_covers_the_result_without_repeating(
    synthetic_service, synthetic_db
):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    whole = _compare(synthetic_service, versions)
    first = synthetic_service.compare(versions["base"], versions["compare"], limit=2, offset=0)
    second = synthetic_service.compare(versions["base"], versions["compare"], limit=2, offset=2)
    seen = [item["change_id"] for item in first["items"] + second["items"]]
    assert len(seen) == len(set(seen))
    assert set(seen) <= {item["change_id"] for item in whole["items"]}


def test_include_text_false_withholds_the_clause_text(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = _compare(synthetic_service, versions, include_text=False)
    for item in result["items"]:
        for side in ("old", "new"):
            if item[side] is not None:
                assert "text" not in item[side] or item[side]["text"] is None


def test_include_entities_adds_mentions_to_both_sides(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = _compare(synthetic_service, versions, include_entities=True)
    assert any(
        item[side] is not None and item[side].get("entities")
        for item in result["items"]
        for side in ("old", "new")
    )


def test_include_diff_false_omits_the_word_diff(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = _compare(synthetic_service, versions, include_diff=False)
    assert all("word_diff" not in item for item in result["items"])


def test_the_same_request_twice_produces_an_identical_result(
    synthetic_service, synthetic_db
):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    first = _compare(synthetic_service, versions)
    second = _compare(synthetic_service, versions)
    assert first["comparison_id"] == second["comparison_id"]
    assert [item["change_id"] for item in first["items"]] == [
        item["change_id"] for item in second["items"]
    ]
    assert first["summary"]["counts"] == second["summary"]["counts"]


def test_a_different_threshold_produces_a_different_comparison_id(
    synthetic_service, synthetic_db
):
    """The id must change when anything that shaped the result changed."""
    versions = _pair(synthetic_db, "ambiguous_alignment")
    default = _compare(synthetic_service, versions)
    strict = _compare(synthetic_service, versions, config=AlignmentConfig(match_threshold=0.9))
    assert default["comparison_id"] != strict["comparison_id"]


def test_the_paragraph_method_is_also_comparable(synthetic_service, synthetic_db):
    versions = _pair(synthetic_db, "added_removed_modified_unchanged")
    result = _compare(synthetic_service, versions, method="paragraph")
    assert result["segmentation_method"] == "paragraph"
    assert result["items"]


# --------------------------------------------------------------------------- #
# SYNTHETIC: options
# --------------------------------------------------------------------------- #


def test_options_lists_every_fixture_document_as_a_pair(synthetic_service):
    options = synthetic_service.options()
    assert options["comparable_pairs"] >= len(PAIRS)
    assert options["corpus"]["documents_with_pairs"] >= len(PAIRS)


def test_options_marks_the_declared_pairs(synthetic_service):
    """Every fixture declares ``supersedes``, so every pair must be declared."""
    options = synthetic_service.options()
    declared = [
        pair
        for document in options["documents"]
        for pair in document["pairs"]
        if pair["supersedes_declared"]
    ]
    assert len(declared) >= len(PAIRS)


def test_options_offers_only_ok_versions(synthetic_service):
    note = synthetic_service.options()["note"]
    assert "extraction_status = 'ok'" in note


# --------------------------------------------------------------------------- #
# REAL CORPUS: invariants only, never an expected count
# --------------------------------------------------------------------------- #


def test_the_real_corpus_has_genuine_version_pairs(real_service):
    """The thing D-10 blocked: Compare now has real data to run on."""
    options = real_service.options()
    assert options["corpus"]["documents_with_pairs"] >= 3, options["corpus"]
    assert options["declared_supersedes_pairs"] >= 3, options


def test_every_real_declared_pair_compares_without_error(real_service):
    """Twelve pairs over 288 pages of master circulars, end to end."""
    options = real_service.options()
    pairs = [
        pair
        for document in options["documents"]
        for pair in document["pairs"]
        if pair["supersedes_declared"]
    ]
    assert pairs
    for pair in pairs:
        result = real_service.compare(
            pair["base_version_id"], pair["compare_version_id"], limit=500
        )
        assert result["summary"]["total_changes"] > 0, pair
        assert result["base_version"]["version_id"] == pair["base_version_id"]
        assert result["compare_version"]["version_id"] == pair["compare_version_id"]


def test_every_real_clause_is_accounted_for_exactly_once(real_service):
    """Conservation: no clause may be dropped, and none may be counted twice."""
    options = real_service.options()
    pair = next(
        pair
        for document in options["documents"]
        for pair in document["pairs"]
        if pair["supersedes_declared"]
    )
    result = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    counts = result["summary"]["counts"]
    base = result["clause_counts"]["base"]
    compare = result["clause_counts"]["compare"]
    matched = counts["modified"] + counts["unchanged"]
    assert matched + counts["removed"] == base, (counts, result["clause_counts"])
    assert matched + counts["added"] == compare, (counts, result["clause_counts"])


def test_every_real_change_has_at_least_one_side(real_service):
    options = real_service.options()
    pair = options["documents"][0]["pairs"][0]
    result = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    for item in result["items"]:
        assert item["old"] is not None or item["new"] is not None


def test_real_evidence_resolves_for_the_overwhelming_majority(real_service):
    """A measurement, asserted as a floor rather than an exact figure.

    Real PDFs can and do produce clauses whose stored offsets do not rebuild
    byte-for-byte -- that is what ``status = 'partial'`` is for. The assertion
    is that it stays rare: a regression that broke provenance would push this
    far below the floor, while a single awkward clause will not.

    The exact figure for the current corpus is recorded in
    ``backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md``, measured by
    ``evaluation/comparison_eval.py``, and is **not** asserted here -- a test
    pinning today's number would fail on a corpus change that is not a defect.
    """
    options = real_service.options()
    pair = next(
        pair
        for document in options["documents"]
        for pair in document["pairs"]
        if pair["supersedes_declared"]
    )
    result = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    sides = [
        item[side]
        for item in result["items"]
        for side in ("old", "new")
        if item[side] is not None
    ]
    assert sides
    resolved = sum(1 for side in sides if side["evidence"]["status"] == "resolved")
    assert resolved / len(sides) >= 0.95, (
        f"{resolved}/{len(sides)} sides resolved; "
        f"problems: "
        + str(
            [
                side["evidence"]["problems"]
                for side in sides
                if side["evidence"]["status"] != "resolved"
            ][:3]
        )
    )


def test_no_real_quote_is_offered_unless_it_verified(real_service):
    """The rule that protects the citation: no quote without a resolved slice."""
    options = real_service.options()
    pair = options["documents"][0]["pairs"][0]
    result = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    for item in result["items"]:
        for side in ("old", "new"):
            payload = item[side]
            if payload is None:
                continue
            evidence = payload["evidence"]
            if evidence["quote"] is not None:
                assert evidence["status"] == "resolved", evidence["problems"]


def test_a_real_renumbered_clause_is_never_reported_as_modified(real_service):
    """Across the whole corpus: no clause is both unchanged-text and modified."""
    options = real_service.options()
    for document in options["documents"]:
        for pair in document["pairs"][:1]:
            result = real_service.compare(
                pair["base_version_id"], pair["compare_version_id"], limit=5000
            )
            for item in result["items"]:
                if item["change_type"] != "modified":
                    continue
                old, new = item["old"], item["new"]
                assert comparison_key(old["text"]) != comparison_key(new["text"]), (
                    f"{item['change_id']} is reported as modified but its text "
                    f"is identical under the comparison key"
                )


def test_a_real_comparison_is_deterministic(real_service):
    options = real_service.options()
    pair = options["documents"][0]["pairs"][0]
    first = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    second = real_service.compare(
        pair["base_version_id"], pair["compare_version_id"], limit=5000
    )
    assert first["comparison_id"] == second["comparison_id"]
    assert [item["change_id"] for item in first["items"]] == [
        item["change_id"] for item in second["items"]
    ]


def test_a_real_pair_of_consecutive_issues_is_mostly_unchanged(real_service):
    """A sanity invariant, not a metric.

    Two consecutive annual issues of one master circular should share most of
    their text -- that is what "consolidating the instructions issued since
    the last issue" means. If a matcher reported them as almost entirely
    rewritten, something would be badly wrong, and a floor catches that
    without pinning a number.
    """
    options = real_service.options()
    candidates = [
        pair
        for document in options["documents"]
        for pair in document["pairs"]
        if pair["supersedes_declared"] and pair["consecutive"]
    ]
    assert candidates
    ratios = []
    for pair in candidates:
        result = real_service.compare(
            pair["base_version_id"], pair["compare_version_id"], limit=5000
        )
        ratios.append(result["summary"]["unchanged_ratio"] or 0.0)
    assert max(ratios) >= 0.5, ratios


def test_real_entity_extraction_runs_over_a_whole_version(real_service):
    options = real_service.options()
    version_id = options["documents"][0]["versions"][0]["version_id"]
    payload = real_service.entities(version_id, limit=500)
    assert payload["condition"] == "regex"
    assert payload["total"] > 0
    assert payload["mentions_by_label"]
    for item in payload["items"]:
        for entity in item["entities"]:
            assert entity["start"] < entity["end"]


def test_real_entity_spans_slice_back_out_of_the_clause_text(real_service, real_pairs_db):
    """An offset that does not resolve cannot be highlighted."""
    options = real_service.options()
    version_id = options["documents"][0]["versions"][0]["version_id"]
    with engine_module.connection(real_pairs_db) as conn:
        version = compare_repository.load_version(version_id, conn=conn)
        clauses = {
            clause.clause_id: clause.text
            for clause in compare_repository.load_clauses(
                version.version_id, conn=conn
            )
        }
    payload = real_service.entities(version_id, limit=200)
    checked = 0
    for item in payload["items"]:
        body = clauses[item["clause_id"]]
        for entity in item["entities"]:
            assert body[entity["start"] : entity["end"]].strip() == entity["text"]
            checked += 1
    assert checked > 0


# --------------------------------------------------------------------------- #
# The repository layer, directly
# --------------------------------------------------------------------------- #


def test_loading_clauses_returns_them_in_document_order(real_service, real_pairs_db):
    options = real_service.options()
    version_id = options["documents"][0]["versions"][0]["version_id"]
    with engine_module.connection(real_pairs_db) as conn:
        version = compare_repository.load_version(version_id, conn=conn)
        clauses = compare_repository.load_clauses(version.version_id, conn=conn)
    assert clauses
    assert [clause.ordinal for clause in clauses] == sorted(
        clause.ordinal for clause in clauses
    )


def test_every_loaded_clause_has_a_page_range(real_service, real_pairs_db):
    """§10.2: a clause without a page range must never exist."""
    options = real_service.options()
    version_id = options["documents"][0]["versions"][0]["version_id"]
    with engine_module.connection(real_pairs_db) as conn:
        version = compare_repository.load_version(version_id, conn=conn)
        clauses = compare_repository.load_clauses(version.version_id, conn=conn)
    for clause in clauses:
        assert clause.page_start >= 1
        assert clause.page_end >= clause.page_start
        assert clause.pages, clause.clause_id


def test_an_unknown_offset_basis_is_refused_rather_than_interpolated(real_pairs_db):
    """The column name cannot be a bound parameter, so it comes from a closed set."""
    with pytest.raises(ValueError, match="unknown offset_basis"):
        compare_repository.page_texts(1, basis="raw_text; DROP TABLE pages", url=real_pairs_db)


def test_loading_an_unknown_version_raises_not_found(real_pairs_db):
    with pytest.raises(compare_repository.NotFound):
        compare_repository.load_version("VER-nope", url=real_pairs_db)


def test_the_supersedes_linker_is_idempotent(real_pairs_db):
    """Running it twice must change nothing the second time."""
    declarations = declarations_from_tree(REAL_PAIRS_PROCESSED)
    first = link_supersedes(declarations, url=real_pairs_db)
    second = link_supersedes(declarations, url=real_pairs_db)
    assert second["linked_count"] == 0, second
    assert first["refused"] == [] and second["refused"] == []


def test_the_supersedes_linker_refuses_a_self_reference(real_pairs_db):
    """A manifest naming a version's own label must be refused, not written."""
    with engine_module.connection(real_pairs_db) as conn:
        row = conn.execute(
            text(
                "SELECT fingerprint, version_label FROM document_versions "
                "ORDER BY id LIMIT 1"
            )
        ).one()
    result = link_supersedes({row.fingerprint: row.version_label}, url=real_pairs_db)
    assert result["linked_count"] == 0
    assert result["refused"]
    assert "itself" in result["refused"][0]["reason"]


def test_the_supersedes_linker_ignores_an_unresolvable_label(real_pairs_db):
    with engine_module.connection(real_pairs_db) as conn:
        fingerprint = conn.execute(
            text("SELECT fingerprint FROM document_versions ORDER BY id LIMIT 1")
        ).scalar_one()
    result = link_supersedes({fingerprint: "no-such-label"}, url=real_pairs_db)
    assert result["linked_count"] == 0
    assert result["unresolved"]


def test_the_comparison_id_depends_on_every_input():
    """Unit-ish, but it belongs with the determinism tests above."""
    config = AlignmentConfig()
    base = comparison_id_for("fp-a", "fp-b", "clause_aware", config)
    assert base == comparison_id_for("fp-a", "fp-b", "clause_aware", config)
    assert base != comparison_id_for("fp-b", "fp-a", "clause_aware", config)
    assert base != comparison_id_for("fp-a", "fp-b", "paragraph", config)
    assert base != comparison_id_for(
        "fp-a", "fp-b", "clause_aware", AlignmentConfig(match_threshold=0.9)
    )
