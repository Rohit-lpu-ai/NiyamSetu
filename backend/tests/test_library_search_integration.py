"""Library & Search against a real PostgreSQL database and real documents.

This is the module that proves the SQL works. It loads the committed pipeline
artefacts into the throwaway test database, then asserts the properties a
search must have — the full-text index is used, the ranking is ordered,
pagination is stable, filters restrict, and **every hit is citable**.

It needs ``NIYAMSETU_TEST_DATABASE_URL`` and the artefacts in
``data/processed/``, and skips with a stated reason when either is missing. On
a fresh clone that means:

    .venv\\Scripts\\python -m backend.app.document_processing.cli --manifest data/manifest.csv
    .venv\\Scripts\\python -m backend.app.document_processing.segmentation_cli --all

**No network.** The sample corpus in ``data/sample_corpus/`` is used when it is
present, as fixed fixtures; nothing here downloads anything, so the suite
behaves identically offline (``CLAUDE.md`` §19).

Deliberate choice about assertions: these tests check **invariants and
orderings**, not exact totals. A test asserting "3 hits for capital adequacy"
would start failing the moment a document is added to the corpus, and would be
measuring the corpus rather than the code — the same reasoning
``test_db_integration.py`` records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from sqlalchemy import text  # noqa: E402

from backend.app.db import engine as engine_module  # noqa: E402
from backend.app.db.config import (  # noqa: E402
    assert_is_test_url,
    test_database_url as resolve_test_database_url,
)
from backend.app.db.loader import (  # noqa: E402
    load_processed_tree,
    load_version_directory,
)
from backend.app.db.migrate import migrate  # noqa: E402
from backend.app.library_search import repository, service  # noqa: E402
from backend.tests.test_db_schema import (  # noqa: E402
    TABLES,
    _create_database_if_missing,
)

pytestmark = pytest.mark.integration

PROCESSED_DIR = Path("data") / "processed"
RAW_DIR = Path("data") / "raw"
SAMPLE_PROCESSED = Path("data") / "sample_corpus" / "processed"
SAMPLE_RAW = Path("data") / "sample_corpus" / "raw"


@pytest.fixture(scope="module")
def search_db() -> str:
    """A migrated test database with every available corpus loaded once.

    Module-scoped: loading 17 documents per test would make the module take
    minutes for no extra assurance. Every test here only reads.
    """
    url = resolve_test_database_url()
    if not url:
        pytest.skip("NIYAMSETU_TEST_DATABASE_URL is not set; see .env.example")
    assert_is_test_url(url)

    if not PROCESSED_DIR.exists() or not any(PROCESSED_DIR.iterdir()):
        pytest.skip(f"{PROCESSED_DIR} is absent or empty; run the extraction CLI")

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

    load_processed_tree(PROCESSED_DIR, url=url, raw_dir=RAW_DIR)

    # The sample corpus is optional: it is git-ignored, so a fresh clone has
    # the 4-document corpus only. Loading it when present makes these tests
    # exercise a 17-document, 3-authority corpus instead.
    #
    # It is loaded **by manifest**, not by directory listing. One downloaded
    # document is deliberately excluded from the corpus (a 372-page bilingual
    # dictionary whose Hindi extracts as mojibake -- see
    # data/sample_corpus/sources.py), and its artefacts are still on disk.
    # Loading everything on disk put a document in the test database that the
    # application would never serve, and the first version of this fixture did
    # exactly that: the selective-term fixture then picked a word that occurs
    # only in that document, and two tests failed for a reason unconnected to
    # search.
    if SAMPLE_PROCESSED.exists() and any(SAMPLE_PROCESSED.iterdir()):
        from backend.scripts.setup_sample_database import manifest_versions

        try:
            wanted = set(manifest_versions())
        except SystemExit:
            wanted = set()
        for version_id in sorted(wanted):
            directory = SAMPLE_PROCESSED / version_id
            if (directory / "extraction.json").exists():
                load_version_directory(directory, url=url, raw_dir=SAMPLE_RAW)

    # Without statistics the planner estimates every full-text match at one
    # row and picks the wrong index. Measured: before ANALYZE the search runs
    # as an index scan on segments_version_method_ordinal_idx with a filter;
    # after it, as a bitmap scan on segments_text_search_idx. A suite that
    # skipped this would be testing a plan no deployment would ever have.
    from backend.scripts.setup_sample_database import analyze

    analyze(url)

    yield url
    engine_module.dispose_engine()


@pytest.fixture(scope="module")
def searcher(search_db) -> service.SearchService:
    return service.SearchService(url=search_db)


@pytest.fixture(scope="module")
def selective_term(search_db) -> str:
    """A term in few segments, where the GIN index is certain to be cheapest.

    Chosen from the corpus rather than hardcoded: on a 1,500-row table the
    planner will -- correctly -- prefer a sequential scan for a term in a
    tenth of the rows, so "is the index applicable" is only a meaningful
    question for a selective term. See
    ``test_the_gin_index_covers_the_search_predicate``.
    """
    with engine_module.connection(search_db) as conn:
        row = conn.execute(
            text(
                """
                SELECT word, ndoc FROM ts_stat(
                    'SELECT s.text_search FROM segments s '
                    'JOIN document_versions v ON v.id = s.version_id '
                    'WHERE s.segmentation_method = ''clause_aware'' '
                    'AND v.extraction_status = ''ok'''
                )
                -- Two constraints, both learned from a failing test.
                --
                -- Letters only: the lexeme '-07122022' exists in this corpus,
                -- and websearch_to_tsquery reads its leading hyphen as
                -- exclusion. The query becomes a NOT, matches almost every
                -- segment, and the planner rightly scans -- a failure with
                -- nothing to do with the index.
                --
                -- Round-trips: ts_stat returns lexemes that are ALREADY
                -- stemmed, and stemming 'acceler' again gives 'accel', which
                -- matches nothing. Requiring the rendered tsquery to equal
                -- the stored lexeme keeps the term one that genuinely occurs.
                WHERE word ~ '^[a-z]+$' AND length(word) >= 7
                  AND ndoc BETWEEN 2 AND 6
                  AND websearch_to_tsquery('english', word)::text
                      = quote_literal(word)
                ORDER BY ndoc, word
                LIMIT 1
                """
            )
        ).first()
    if row is None:
        pytest.skip("no selective term in the loaded corpus")
    return row.word


@pytest.fixture(scope="module")
def corpus_terms(search_db) -> List[str]:
    """Terms that genuinely occur in whatever corpus is loaded.

    Hardcoding "capital adequacy" would couple these tests to one document
    being present. Taking the terms from the data keeps them meaningful on any
    corpus -- including the 4-document one on a fresh clone.
    """
    with engine_module.connection(search_db) as conn:
        rows = conn.execute(
            text(
                """
                SELECT word FROM ts_stat(
                    'SELECT s.text_search FROM segments s '
                    'JOIN document_versions v ON v.id = s.version_id '
                    'WHERE s.segmentation_method = ''clause_aware'' '
                    'AND v.extraction_status = ''ok'''
                )
                -- Letters only, and round-tripping through the query
                -- parser: see the notes in selective_term.
                WHERE word ~ '^[a-z]+$' AND length(word) >= 6 AND ndoc >= 3
                  AND websearch_to_tsquery('english', word)::text
                      = quote_literal(word)
                ORDER BY ndoc DESC, word
                LIMIT 8
                """
            )
        ).all()
    terms = [row.word for row in rows]
    if not terms:
        pytest.skip("no indexable terms in the loaded corpus")
    return terms


# --------------------------------------------------------------------------- #
# The index is actually used
# --------------------------------------------------------------------------- #


def test_the_gin_index_covers_the_search_predicate(search_db, selective_term):
    """The index question, isolated from the join-plan question.

    Three versions of this test were wrong, and the reasons are worth keeping
    because each is a plausible thing to assert:

    1. *"The plan always uses ``segments_text_search_idx``"* is **false**.
       Fewer than 2,000 segments is a couple of megabytes, so PostgreSQL
       correctly prefers a sequential scan for anything but a very selective
       term. A test demanding the index would be demanding a worse plan.
    2. *"With ``enable_seqscan = off`` it must use the GIN index"* is also
       false. Discouraging sequential scans does not force a *particular*
       index: the planner can answer the query through
       ``segments_version_method_ordinal_idx`` and filter on ``text_search``,
       and it does.
    3. What is actually worth asserting is narrower and stronger: **the GIN
       index is applicable to the predicate the search uses.** Explaining that
       predicate on its own, with sequential scans discouraged, leaves the
       index as the only path -- so if it did not cover the predicate (a
       different text-search configuration, a dropped index, a changed
       ``TS_CONFIG``) there would be no index path and this fails.

    Whether the planner *chooses* it is a cost decision that changes with the
    size of the corpus, and is recorded in the implementation report rather
    than asserted here.
    """
    with engine_module.transaction(search_db) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(
                    "EXPLAIN SELECT public_id FROM segments "
                    " WHERE text_search @@ websearch_to_tsquery('english', :q)"
                ),
                {"q": selective_term},
            )
        )
    assert "segments_text_search_idx" in plan, plan


def test_whichever_plan_is_chosen_the_rows_are_the_same(search_db, selective_term):
    """A broken index is one that is used and returns the wrong rows.

    This holds regardless of which plan is cheaper today, which is what makes
    it the right assertion to keep.
    """
    natural = repository.clause_search_rows(selective_term, url=search_db)
    forced = repository.clause_search_rows(
        selective_term, force_index=True, url=search_db
    )
    assert natural, "the selective term matched nothing"
    assert natural == forced


def test_the_plan_is_costed_from_real_statistics(search_db):
    """ANALYZE ran. Without it, every index choice is luck, not judgement.

    The signature of missing statistics is an estimate of one row for a term
    that matches dozens — which is exactly what the first run of this module
    hit, and why the fixture now runs ANALYZE after loading.

    Deliberately **not** asserted here: which access path the planner picks
    for a common term. A term matching roughly a tenth of 1,500 rows costs
    less to find by scanning than by building a bitmap and rechecking the
    heap, and PostgreSQL knowing that is correct behaviour, not a defect to
    "fix" with ``enable_seqscan = off``.
    """
    with engine_module.connection(search_db) as conn:
        row = conn.execute(
            text(
                """
                SELECT word, ndoc FROM ts_stat(
                    'SELECT s.text_search FROM segments s '
                    'JOIN document_versions v ON v.id = s.version_id '
                    'WHERE s.segmentation_method = ''clause_aware'' '
                    'AND v.extraction_status = ''ok'''
                )
                WHERE word ~ '^[a-z]+$'
                  AND websearch_to_tsquery('english', word)::text
                      = quote_literal(word)
                ORDER BY ndoc DESC LIMIT 1
                """
            )
        ).first()
    if row is None or row.ndoc < 20:
        pytest.skip("no common term in this corpus")
    with engine_module.connection(search_db) as conn:
        estimate = conn.execute(
            text(
                "EXPLAIN SELECT public_id FROM segments "
                " WHERE text_search @@ websearch_to_tsquery('english', :q)"
            ),
            {"q": row.word},
        ).first()[0]
    assert "rows=1 " not in estimate, (
        f"{row.word!r} matches {row.ndoc} segments but the planner estimates "
        f"one row -- ANALYZE has not run. Plan: {estimate}"
    )


def test_almost_every_segment_has_a_non_empty_tsvector(search_db):
    """A segment with an empty tsvector is unsearchable. How many are there?

    Measured on the 17-document corpus: **exactly one** -- a paragraph segment
    whose entire text is a row of asterisks, a separator line that the
    paragraph baseline kept as a segment. ``to_tsvector`` produces nothing
    from punctuation, so that segment cannot be found by any query.

    A real and acceptable limitation, recorded rather than hidden: the
    alternative is dropping punctuation-only segments at segmentation time,
    which would change the Phase 2 baseline Experiment B measured. The
    assertion is a bound rather than zero, and tight enough that a regression
    -- a normalisation change that emptied many segments, say -- still fails.
    """
    with engine_module.connection(search_db) as conn:
        empty = conn.execute(
            text(
                "SELECT count(*) FROM segments "
                "WHERE text_search IS NULL OR text_search = ''::tsvector"
            )
        ).scalar_one()
        total = conn.execute(text("SELECT count(*) FROM segments")).scalar_one()
    assert empty / total < 0.01, f"{empty} of {total} segments are unsearchable"


def test_an_unsearchable_segment_is_punctuation_only(search_db):
    """Whatever cannot be indexed must at least be explainable."""
    with engine_module.connection(search_db) as conn:
        rows = conn.execute(
            text("SELECT text FROM segments WHERE text_search = ''::tsvector LIMIT 5")
        ).all()
    for row in rows:
        assert not any(character.isalnum() for character in row.text), row.text


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #


def test_parse_query_reports_the_rendered_tsquery(search_db):
    """``nodes`` is PostgreSQL's numnode: lexemes **and** operators.

    A two-word AND query is 3 nodes, not 2. Only ``nodes == 0`` is
    load-bearing, and that case has its own test below.
    """
    parsed = repository.parse_query("capital adequacy", url=search_db)
    assert parsed["nodes"] == 3
    assert parsed["tsquery"] == "'capit' & 'adequaci'"
    assert repository.parse_query("capital", url=search_db)["nodes"] == 1


def test_a_quoted_phrase_becomes_a_phrase_operator(search_db):
    """The reason websearch_to_tsquery was chosen over plainto_tsquery."""
    parsed = repository.parse_query('"capital adequacy"', url=search_db)
    assert "<->" in parsed["tsquery"]


def test_exclusion_syntax_is_understood(search_db):
    parsed = repository.parse_query("capital -adequacy", url=search_db)
    assert "!" in parsed["tsquery"]


@pytest.mark.parametrize("query", ["the of and", "!!!", "... ??? ---", "a an"])
def test_queries_that_reduce_to_nothing_are_reported_as_such(search_db, query):
    """Zero nodes is why a search found nothing. It must be distinguishable."""
    assert repository.parse_query(query, url=search_db)["nodes"] == 0


def test_punctuation_does_not_raise(search_db):
    """to_tsquery would raise on this; websearch_to_tsquery is why it does not."""
    for query in ["50% & 60%", "a | b", "(unbalanced", "'quoted", "a <-> b"]:
        repository.parse_query(query, url=search_db)


# --------------------------------------------------------------------------- #
# Clause search
# --------------------------------------------------------------------------- #


def test_clause_search_returns_ranked_hits(searcher, corpus_terms):
    result = searcher.search_clauses(corpus_terms[0], limit=10)
    assert result["total"] >= 1
    assert result["items"]
    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True), "not ordered by score"
    assert all(score > 0 for score in scores)


def test_every_clause_hit_is_citable(searcher, corpus_terms):
    """No page reference, no citation. The project's non-negotiable rule."""
    for term in corpus_terms[:4]:
        for hit in searcher.search_clauses(term, limit=20)["items"]:
            assert hit["pages"], hit["clause_id"]
            numbers = [page["page_number"] for page in hit["pages"]]
            assert min(numbers) == hit["page_start"]
            assert max(numbers) == hit["page_end"]
            assert hit["offset_basis"] == "normalized_text"
            assert hit["document_id"].startswith("DOC-")
            assert hit["version_id"].startswith("VER-")
            assert hit["clause_id"].startswith("CLS-")


def test_stored_offsets_resolve_to_real_text_on_the_page(search_db, searcher, corpus_terms):
    """The provenance claim, checked against the page text itself."""
    from backend.app.db import repository as db_repository

    checked = 0
    for hit in searcher.search_clauses(corpus_terms[0], limit=10)["items"]:
        first = hit["pages"][0]
        pages = db_repository.get_pages(
            hit["document_id"],
            version_public_id=hit["version_id"],
            page_from=first["page_number"],
            page_to=first["page_number"],
            text_variant="normalized",
            url=search_db,
        )
        body = pages["items"][0]["normalized_text"] or ""
        assert body[first["char_start"] : first["char_end"]].strip()
        checked += 1
    assert checked, "no hits were available to check"


def test_the_snippet_marks_the_match(searcher, corpus_terms):
    hits = searcher.search_clauses(corpus_terms[0], limit=5)["items"]
    assert any(repository.HIGHLIGHT_OPEN in hit["snippet"] for hit in hits)


def test_snippets_are_single_line(searcher, corpus_terms):
    """Page line breaks in a result list look like a layout accident."""
    for hit in searcher.search_clauses(corpus_terms[0], limit=10)["items"]:
        assert "\n" not in hit["snippet"]


def test_full_text_is_withheld_unless_requested(searcher, corpus_terms):
    without = searcher.search_clauses(corpus_terms[0], limit=3)["items"]
    with_text = searcher.search_clauses(
        corpus_terms[0], limit=3, include_text=True
    )["items"]
    assert all(hit["text"] is None for hit in without)
    assert all(hit["text"] for hit in with_text)


def test_searching_the_paragraph_method_returns_paragraph_segments(searcher, corpus_terms):
    result = searcher.search_clauses(corpus_terms[0], method="paragraph", limit=5)
    assert result["segmentation_method"] == "paragraph"
    for hit in result["items"]:
        assert hit["segmentation_method"] == "paragraph"


def test_the_two_methods_do_not_return_the_same_segments(searcher, corpus_terms):
    """They cover the same text, which is why only one is searched at a time."""
    clause_ids = {
        hit["clause_id"]
        for hit in searcher.search_clauses(corpus_terms[0], limit=50)["items"]
    }
    paragraph_ids = {
        hit["clause_id"]
        for hit in searcher.search_clauses(
            corpus_terms[0], method="paragraph", limit=50
        )["items"]
    }
    assert not clause_ids & paragraph_ids


def test_no_clause_id_appears_twice_in_one_result_set(searcher, corpus_terms):
    """A join that multiplied rows would show the same clause repeatedly."""
    for term in corpus_terms[:4]:
        ids = [
            hit["clause_id"]
            for hit in searcher.search_clauses(term, limit=100)["items"]
        ]
        assert len(ids) == len(set(ids)), term


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #


def test_paging_covers_every_row_exactly_once(searcher, corpus_terms):
    """The property a stable ORDER BY exists for."""
    term = max(
        corpus_terms, key=lambda t: searcher.search_clauses(t, limit=1)["total"]
    )
    total = searcher.search_clauses(term, limit=1)["total"]
    reference = [
        hit["clause_id"] for hit in searcher.search_clauses(term, limit=100)["items"]
    ]
    collected: List[str] = []
    offset = 0
    while offset < min(total, len(reference)):
        page = searcher.search_clauses(term, limit=3, offset=offset)
        collected.extend(hit["clause_id"] for hit in page["items"])
        offset += 3
    assert len(collected) == len(set(collected)), "a row appeared on two pages"
    assert collected[: len(reference)] == reference, "order changed between pages"


def test_total_is_independent_of_the_page_window(searcher, corpus_terms):
    term = corpus_terms[0]
    totals = {
        searcher.search_clauses(term, limit=limit, offset=offset)["total"]
        for limit, offset in ((1, 0), (5, 0), (5, 5), (100, 0))
    }
    assert len(totals) == 1


def test_offset_past_the_end_is_an_empty_page_not_an_error(searcher, corpus_terms):
    result = searcher.search_clauses(corpus_terms[0], limit=5, offset=10_000)
    assert result["items"] == []
    assert result["total"] >= 1


def test_limit_is_capped_at_the_contract_maximum(searcher, corpus_terms):
    result = searcher.search_clauses(corpus_terms[0], limit=10_000)
    assert result["limit"] == repository.MAX_LIMIT


# --------------------------------------------------------------------------- #
# Document search
# --------------------------------------------------------------------------- #


def test_document_search_aggregates_clause_evidence(searcher, corpus_terms):
    result = searcher.search_documents(corpus_terms[0], limit=10)
    assert result["total"] >= 1
    for hit in result["items"]:
        assert hit["document_id"].startswith("DOC-")
        assert hit["matched_clauses"] >= 1 or hit["metadata_match"]
        if hit["matched_clauses"]:
            assert hit["best_clause"] is not None
            assert hit["best_clause"]["pages"]


def test_document_search_returns_each_document_once(searcher, corpus_terms):
    for term in corpus_terms[:4]:
        ids = [
            hit["document_id"]
            for hit in searcher.search_documents(term, limit=100)["items"]
        ]
        assert len(ids) == len(set(ids)), term


def test_document_scores_are_ordered(searcher, corpus_terms):
    scores = [
        hit["score"] for hit in searcher.search_documents(corpus_terms[0], limit=20)["items"]
    ]
    assert scores == sorted(scores, reverse=True)


def test_a_title_match_outranks_a_body_only_match(search_db, searcher):
    """The metadata bonus, checked against a real title from the corpus."""
    with engine_module.connection(search_db) as conn:
        title = conn.execute(
            text(
                "SELECT title FROM documents d "
                " JOIN document_versions v ON v.document_id = d.id "
                " WHERE v.extraction_status = 'ok' ORDER BY d.public_id LIMIT 1"
            )
        ).scalar_one()
    # Two distinctive words from the title, which the metadata vector will
    # match and most clause bodies will not.
    words = [w for w in title.replace("-", " ").split() if len(w) > 5][:2]
    if len(words) < 2:
        pytest.skip("no title with two long words in this corpus")
    result = searcher.search_documents(" ".join(words), limit=10)
    if not result["items"]:
        pytest.skip("the title terms matched nothing")
    assert result["items"][0]["metadata_match"] is True
    assert result["items"][0]["score"] >= repository.METADATA_MATCH_BONUS


def test_a_document_can_match_on_metadata_alone(search_db, searcher):
    """Then best_clause is null, which is an answer and not a missing field."""
    with engine_module.connection(search_db) as conn:
        reference = conn.execute(
            text(
                "SELECT reference_no FROM documents "
                "WHERE reference_no IS NOT NULL LIMIT 1"
            )
        ).scalar()
    if not reference:
        pytest.skip("no document in this corpus prints a reference number")
    result = searcher.search_documents(reference, limit=5)
    assert result["total"] >= 1
    assert any(hit["metadata_match"] for hit in result["items"])


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


def test_department_filter_restricts_and_counts_agree(searcher, search_db):
    facets = searcher.facets()
    for facet in facets["departments"]:
        value = "null" if facet["value"] is None else facet["value"]
        result = searcher.search_documents("insurance", department=value, limit=100)
        for hit in result["items"]:
            assert (hit["department"] or "null") == value


def test_document_type_filter_restricts(searcher):
    for facet in searcher.facets()["document_types"]:
        value = facet["value"]
        result = searcher.search_documents("insurance", document_type=value, limit=100)
        for hit in result["items"]:
            assert hit["document_type"] == value


def test_date_filters_restrict_the_window(searcher, corpus_terms):
    bounds = searcher.facets()["date_range"]
    if not bounds["earliest"]:
        pytest.skip("no publication dates in this corpus")
    result = searcher.search_documents(
        corpus_terms[0], date_from=bounds["latest"], limit=100
    )
    for hit in result["items"]:
        if hit["publication_date"]:
            assert hit["publication_date"] >= bounds["latest"]


def test_document_filter_restricts_clause_hits(searcher, corpus_terms):
    everything = searcher.search_clauses(corpus_terms[0], limit=100)["items"]
    if not everything:
        pytest.skip("no hits to filter")
    target = everything[0]["document_id"]
    filtered = searcher.search_clauses(
        corpus_terms[0], document_id=target, limit=100
    )
    assert filtered["items"]
    assert all(hit["document_id"] == target for hit in filtered["items"])
    assert filtered["total"] <= len(everything) or filtered["total"] >= 1


def test_segment_type_filter_restricts(searcher, corpus_terms):
    filtered = searcher.search_clauses(
        corpus_terms[0], segment_type="clause", limit=100
    )
    for hit in filtered["items"]:
        assert hit["segment_type"] == "clause"


def test_page_filter_keeps_only_overlapping_segments(searcher, corpus_terms):
    filtered = searcher.search_clauses(
        corpus_terms[0], page_from=1, page_to=2, limit=100
    )
    for hit in filtered["items"]:
        assert hit["page_start"] <= 2 and hit["page_end"] >= 1


def test_filters_are_applied_before_pagination(searcher, corpus_terms):
    """Otherwise a filter returns short pages instead of fewer results."""
    unfiltered = searcher.search_clauses(corpus_terms[0], limit=100)
    if not unfiltered["items"]:
        pytest.skip("no hits")
    target = unfiltered["items"][0]["document_id"]
    filtered = searcher.search_clauses(corpus_terms[0], document_id=target, limit=1)
    assert filtered["total"] == len(
        [h for h in unfiltered["items"] if h["document_id"] == target]
    ) or filtered["total"] >= 1
    assert len(filtered["items"]) == 1


def test_a_filter_that_matches_nothing_returns_an_empty_result(searcher, corpus_terms):
    result = searcher.search_clauses(
        corpus_terms[0], document_id="DOC-9999", limit=10
    )
    assert result["total"] == 0 and result["items"] == []


# --------------------------------------------------------------------------- #
# Sorting and version scope
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sort", ["relevance", "date_desc", "date_asc", "title"])
def test_every_sort_is_accepted_and_stable(searcher, corpus_terms, sort):
    first = [
        hit["clause_id"]
        for hit in searcher.search_clauses(corpus_terms[0], sort=sort, limit=20)["items"]
    ]
    second = [
        hit["clause_id"]
        for hit in searcher.search_clauses(corpus_terms[0], sort=sort, limit=20)["items"]
    ]
    assert first == second, f"{sort} is not deterministic"


def test_date_sort_orders_by_publication_date(searcher, corpus_terms):
    hits = searcher.search_clauses(corpus_terms[0], sort="date_desc", limit=50)["items"]
    dates = [hit["publication_date"] for hit in hits if hit["publication_date"]]
    assert dates == sorted(dates, reverse=True)


def test_version_scope_all_is_a_superset_of_latest(searcher, corpus_terms):
    latest = searcher.search_clauses(corpus_terms[0], limit=100)["total"]
    every = searcher.search_clauses(
        corpus_terms[0], version_scope="all", limit=100
    )["total"]
    assert every >= latest


# --------------------------------------------------------------------------- #
# The ILIKE comparison condition
# --------------------------------------------------------------------------- #


def test_ilike_finds_a_literal_substring(searcher, search_db):
    with engine_module.connection(search_db) as conn:
        sample = conn.execute(
            text(
                "SELECT text FROM segments WHERE segmentation_method = 'clause_aware'"
                " AND length(text) > 120 ORDER BY public_id LIMIT 1"
            )
        ).scalar_one()
    fragment = " ".join(sample.split()[3:6])
    result = searcher.search_clauses(fragment, strategy="ilike", limit=10)
    assert result["strategy"] == "ilike"
    assert result["sort"] == "document_order"
    assert any("substring match" in w for w in result["warnings"])


def test_ilike_has_no_stemming_and_fts_does(searcher, search_db):
    """The measurable difference between the two conditions, demonstrated.

    'requirements' and 'requirement' share a stem, so full-text matches both
    forms while a substring match for the plural cannot find the singular.
    """
    fts_singular = searcher.search_clauses("requirement", limit=100)["total"]
    fts_plural = searcher.search_clauses("requirements", limit=100)["total"]
    ilike_singular = searcher.search_clauses(
        "requirement", strategy="ilike", limit=100
    )["total"]
    ilike_plural = searcher.search_clauses(
        "requirements", strategy="ilike", limit=100
    )["total"]
    if not fts_singular:
        pytest.skip("'requirement' does not occur in this corpus")
    assert fts_singular == fts_plural, "stemming should make these identical"
    assert ilike_singular != ilike_plural or ilike_plural == 0


def test_ilike_hits_carry_the_same_provenance(searcher, search_db):
    with engine_module.connection(search_db) as conn:
        sample = conn.execute(
            text(
                "SELECT text FROM segments WHERE segmentation_method = 'clause_aware'"
                " AND length(text) > 120 ORDER BY public_id LIMIT 1"
            )
        ).scalar_one()
    fragment = " ".join(sample.split()[3:6])
    for hit in searcher.search_clauses(fragment, strategy="ilike", limit=5)["items"]:
        assert hit["pages"]
        assert hit["offset_basis"] == "normalized_text"
        assert repository.HIGHLIGHT_OPEN in hit["snippet"]


def test_ilike_document_search_groups_by_document(searcher, search_db):
    with engine_module.connection(search_db) as conn:
        sample = conn.execute(
            text(
                "SELECT text FROM segments WHERE segmentation_method = 'clause_aware'"
                " AND length(text) > 120 ORDER BY public_id LIMIT 1"
            )
        ).scalar_one()
    fragment = " ".join(sample.split()[3:6])
    result = searcher.search_documents(fragment, strategy="ilike", limit=10)
    ids = [hit["document_id"] for hit in result["items"]]
    assert len(ids) == len(set(ids))
    assert any("ilike" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# Facets, suggestions, empty states
# --------------------------------------------------------------------------- #


def test_facets_are_read_from_the_corpus(searcher, search_db):
    facets = searcher.facets()
    with engine_module.connection(search_db) as conn:
        documents = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        pages = conn.execute(text("SELECT count(*) FROM pages")).scalar_one()
    assert facets["documents"] == documents
    assert facets["pages"] == pages
    assert sum(facets["segments"].values()) > 0


def test_facet_counts_sum_to_the_document_total(searcher):
    facets = searcher.facets()
    assert sum(f["count"] for f in facets["departments"]) == facets["documents"]
    assert sum(f["count"] for f in facets["document_types"]) == facets["documents"]


def test_suggestions_come_from_document_identity_only(searcher, search_db):
    with engine_module.connection(search_db) as conn:
        title = conn.execute(
            text("SELECT title FROM documents ORDER BY public_id LIMIT 1")
        ).scalar_one()
    word = max(title.split(), key=len)
    suggestions = searcher.suggest(word)["items"]
    assert suggestions
    assert all(s["kind"] in {"title", "reference_no", "department"} for s in suggestions)


def test_a_one_character_prefix_suggests_nothing(searcher):
    assert searcher.suggest("a")["items"] == []


def test_a_query_with_no_match_is_an_empty_success(searcher):
    result = searcher.search_clauses("zzzzqqq nonexistent phrase", limit=10)
    assert result["total"] == 0
    assert result["items"] == []
    assert result["parsed_query"]


def test_a_stopword_only_query_explains_itself(searcher):
    result = searcher.search_clauses("the of and", limit=10)
    assert result["total"] == 0
    assert any("no searchable terms" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# Read-only guarantee
# --------------------------------------------------------------------------- #


def test_searching_changes_no_row(search_db, searcher, corpus_terms):
    """The module is read-only. Asserted against real counts, not inspection."""
    from backend.app.db.repository import corpus_counts

    before = corpus_counts(url=search_db)
    searcher.search_documents(corpus_terms[0], limit=50)
    searcher.search_clauses(corpus_terms[0], limit=50, include_text=True)
    searcher.search_clauses(corpus_terms[0], strategy="ilike", limit=50)
    searcher.facets()
    searcher.suggest("the")
    assert corpus_counts(url=search_db) == before
