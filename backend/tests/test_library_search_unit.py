"""Library & Search logic that needs no database and no server.

What is asserted here is everything the module decides *before* SQL runs:
query validation, strategy and option checking, the SQL predicates the filters
build, and the response envelope. Those are the parts a wrong answer comes
from most often, and they are testable without PostgreSQL — so they are, and
the suite stays runnable on a machine with no server.

What is **not** asserted here: that the SQL returns the right rows. That needs
a database and lives in ``test_library_search_integration.py``. The split is
the same one ``test_api.py`` draws, and for the same reason: a mocked test
that looked like an integration test would be the most misleading thing in the
suite.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from backend.app.library_search import repository, service  # noqa: E402
from backend.app.library_search.schemas import (  # noqa: E402
    HIGHLIGHT_CLOSE,
    HIGHLIGHT_OPEN,
)

# --------------------------------------------------------------------------- #
# Query normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("capital adequacy", "capital adequacy"),
        ("  capital   adequacy  ", "capital adequacy"),
        ("\tKYC\n", "KYC"),
        ('"priority sector" lending', '"priority sector" lending'),
        ("kyc -annexure", "kyc -annexure"),
    ],
)
def test_normalize_query_trims_and_collapses(raw, expected):
    assert service.normalize_query(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_blank_queries_are_rejected_not_silently_empty(raw):
    """A blank query is a client mistake, not a search that found nothing."""
    with pytest.raises(service.InvalidQuery) as excinfo:
        service.normalize_query(raw)
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_blank_query_message_points_at_the_listing_endpoint():
    """The error should tell the caller what to use instead."""
    with pytest.raises(service.InvalidQuery) as excinfo:
        service.normalize_query("")
    assert "/api/documents" in str(excinfo.value)


def test_over_long_query_is_rejected_with_its_length():
    with pytest.raises(service.InvalidQuery) as excinfo:
        service.normalize_query("x" * (service.MAX_QUERY_LENGTH + 1))
    assert str(service.MAX_QUERY_LENGTH) in str(excinfo.value)


def test_query_at_the_length_limit_is_accepted():
    """An off-by-one here would reject a legitimately long pasted clause."""
    assert len(service.normalize_query("x" * service.MAX_QUERY_LENGTH)) == (
        service.MAX_QUERY_LENGTH
    )


def test_whitespace_collapsing_matters_for_the_ilike_comparison():
    """Both strategies must receive the same string, or the comparison lies.

    ILIKE matches a literal substring, so 'capital  adequacy' with two spaces
    would find nothing while the full-text condition found plenty -- and the
    measured difference between them would be an artefact of the input.
    """
    assert service.normalize_query("capital  adequacy") == "capital adequacy"


# --------------------------------------------------------------------------- #
# Option validation
# --------------------------------------------------------------------------- #


def test_implemented_strategies_are_exactly_fts_and_ilike():
    assert service.STRATEGIES == ("fts", "ilike")
    assert service.DEFAULT_STRATEGY == "fts"


def test_planned_strategies_are_named_but_not_offered():
    """semantic and hybrid need D-7; the seam exists, the claim does not."""
    assert service.PLANNED_STRATEGIES == ("semantic", "hybrid")
    for planned in service.PLANNED_STRATEGIES:
        assert planned not in service.STRATEGIES


@pytest.mark.parametrize("strategy", ["semantic", "hybrid"])
def test_asking_for_a_planned_strategy_says_it_is_not_implemented(strategy):
    """The error must not read like a typo when the feature simply is not built."""
    with pytest.raises(service.InvalidQuery) as excinfo:
        service.SearchService().search_clauses("insurance", strategy=strategy)
    message = str(excinfo.value)
    assert "not implemented" in message and "D-7" in message


@pytest.mark.parametrize(
    "kwargs",
    [
        {"strategy": "elasticsearch"},
        {"method": "sentence"},
        {"sort": "whatever"},
        {"version_scope": "every"},
    ],
)
def test_unsupported_options_are_refused_before_any_sql(kwargs):
    """Validation happens in the service, so no connection is opened at all."""
    with pytest.raises(service.InvalidQuery):
        service.SearchService().search_clauses("insurance", **kwargs)


def test_validation_precedes_option_checking():
    """A blank query with a bad strategy reports the query, not the strategy."""
    with pytest.raises(service.InvalidQuery) as excinfo:
        service.SearchService().search_documents("", strategy="semantic")
    assert "query is required" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Filters -> SQL
# --------------------------------------------------------------------------- #


def test_no_filters_produce_no_predicate():
    sql, params = repository.SearchFilters().all_sql()
    assert sql == ""
    assert params == {}


def test_every_filter_value_is_bound_never_interpolated():
    """The injection guarantee, asserted rather than assumed."""
    filters = repository.SearchFilters(
        department="'; DROP TABLE segments; --",
        document_type="circular",
        date_from="2026-01-01",
        document_id="DOC-0001",
        segment_type="clause",
    )
    sql, params = filters.all_sql()
    assert "DROP TABLE" not in sql
    assert params["f_department"] == "'; DROP TABLE segments; --"
    for value in params.values():
        assert str(value) not in sql


def test_null_department_becomes_an_is_null_predicate():
    """'null' selects the documented gap, and must not bind a literal string."""
    sql, params = repository.SearchFilters(department="null").document_sql()
    assert "d.department IS NULL" in sql
    assert "f_department" not in params


def test_null_document_type_becomes_an_is_null_predicate():
    sql, params = repository.SearchFilters(document_type="null").document_sql()
    assert "d.document_type IS NULL" in sql
    assert "f_document_type" not in params


def test_document_sql_can_drop_the_version_predicate():
    """The document-level query joins the LATEST version, not the segment's.

    Applying a version filter there would compare the wrong row and silently
    return nothing, which is why include_version exists.
    """
    filters = repository.SearchFilters(version_id="VER-0001")
    with_version, params_with = filters.document_sql(include_version=True)
    without, params_without = filters.document_sql(include_version=False)
    assert "v.public_id" in with_version and "f_version_id" in params_with
    assert "v.public_id" not in without and "f_version_id" not in params_without


def test_page_filters_use_the_overlap_form():
    """A clause spanning pages 3-4 must match a page_from=4 filter.

    page_end >= page_from rather than page_start >= page_from: a spanning
    clause is on both of its pages, and the stricter form would hide it.
    """
    sql, params = repository.SearchFilters(page_from=4, page_to=6).segment_sql()
    assert "s.page_end >= :f_page_from" in sql
    assert "s.page_start <= :f_page_to" in sql
    assert params == {"f_page_from": 4, "f_page_to": 6}


def test_as_dict_reports_only_applied_filters():
    filters = repository.SearchFilters(department="RBI", page_from=2)
    assert filters.as_dict() == {"department": "RBI", "page_from": 2}


def test_filters_split_into_document_and_segment_predicates():
    """The two queries must filter identically, so the parts are built once."""
    filters = repository.SearchFilters(department="RBI", segment_type="clause")
    doc_sql, _ = filters.document_sql()
    seg_sql, _ = filters.segment_sql()
    combined, _ = filters.all_sql()
    assert combined == doc_sql + seg_sql


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "requested, expected",
    [
        (None, repository.DEFAULT_LIMIT),
        (1, 1),
        (50, 50),
        (repository.MAX_LIMIT, repository.MAX_LIMIT),
        (10_000, repository.MAX_LIMIT),
        (0, 1),
        (-5, 1),
    ],
)
def test_limit_is_clamped_not_rejected(requested, expected):
    """Matches db.repository's behaviour: a huge limit gets the cap, not a 422.

    The API still rejects out-of-range values at the boundary; this is the
    defence for a non-HTTP caller.
    """
    assert repository._clamp(requested) == expected


def test_search_caps_agree_with_the_existing_read_layer():
    """Two different caps on two endpoints would be a paginating client's bug."""
    from backend.app.db import repository as db_repository

    assert repository.MAX_LIMIT == db_repository.MAX_LIMIT


# --------------------------------------------------------------------------- #
# Snippets and configuration
# --------------------------------------------------------------------------- #


def test_highlight_sentinels_are_not_html():
    """Markup assembled from PDF text is how a document becomes a script."""
    for sentinel in (HIGHLIGHT_OPEN, HIGHLIGHT_CLOSE):
        assert "<" not in sentinel and ">" not in sentinel


def test_headline_options_name_both_sentinels():
    options = repository._HEADLINE_OPTIONS
    assert f"StartSel={HIGHLIGHT_OPEN}" in options
    assert f"StopSel={HIGHLIGHT_CLOSE}" in options


def test_snippet_whitespace_is_collapsed_for_display():
    """Segment text keeps the source document's line breaks; a snippet must not."""
    assert repository._tidy_snippet("a\n  b\tc  ") == "a b c"
    assert repository._tidy_snippet(None) == ""
    assert repository._tidy_snippet("") == ""


def test_text_search_configuration_matches_the_generated_column():
    """If these diverge the GIN index cannot be used and stemming disagrees.

    ``segments.text_search`` is ``to_tsvector('english', text)``, fixed by
    ``0001_initial_schema.sql``. Changing TS_CONFIG without a migration would
    silently fall back to a sequential scan.
    """
    migration = (
        __import__("pathlib")
        .Path("backend/app/db/migrations/0001_initial_schema.sql")
        .read_text(encoding="utf-8")
    )
    assert f"to_tsvector('{repository.TS_CONFIG}', text)" in migration


def test_rank_normalisation_is_length_aware_and_bounded():
    """1 = divide by log(length), 32 = scale into 0..1. Both, deliberately."""
    assert repository.RANK_NORMALIZATION & 1, "length normalisation is off"
    assert repository.RANK_NORMALIZATION & 32, "0..1 scaling is off"
    assert repository.RANK_NORMALIZATION == 33


def test_metadata_bonus_outranks_any_possible_text_score():
    """A title match must beat a body match; with 32-scaling rank < 1 always."""
    assert repository.METADATA_MATCH_BONUS >= 1.0


def test_latest_version_scope_orders_deterministically():
    """publication_date is nullable, so the tie-break chain must be complete."""
    sql = repository._LATEST_VERSIONS
    assert "DISTINCT ON (v.document_id)" in sql
    assert "publication_date DESC NULLS LAST" in sql
    assert "ingested_at DESC" in sql
    assert "v.id DESC" in sql


def test_version_scope_all_adds_no_restriction():
    filters = repository.SearchFilters()
    assert repository._version_scope_sql("all", filters) == ""
    assert "lv" in repository._version_scope_sql("latest", filters)


def test_an_explicit_version_filter_overrides_the_latest_scope():
    """Asking for a version means asking for that version, old or not."""
    filters = repository.SearchFilters(version_id="VER-0002")
    assert repository._version_scope_sql("latest", filters) == ""


def test_one_segmentation_method_per_query():
    """Both methods cover the same text; a union would duplicate every hit."""
    assert "s.segmentation_method = :method" in repository._MATCH_CORE
    assert repository._MATCH_CORE.count("segmentation_method") == 1


def test_only_ok_extractions_are_searchable():
    """A partial extraction must not surface as evidence (schema's view rule)."""
    assert "v.extraction_status = 'ok'" in repository._MATCH_CORE


def test_every_clause_sort_ends_in_a_unique_tie_break():
    """An ORDER BY with ties cannot paginate without repeating or skipping."""
    for name, clause in repository._CLAUSE_SORTS.items():
        assert clause.strip().endswith("s.public_id ASC"), name


# --------------------------------------------------------------------------- #
# The response envelope
# --------------------------------------------------------------------------- #


def _payload(**overrides):
    base = {
        "parsed_query": "'insur'",
        "total": 3,
        "limit": 20,
        "offset": 0,
        "items": [],
        "no_searchable_terms": False,
    }
    base.update(overrides)
    return base


def test_envelope_reports_the_strategy_that_produced_it():
    enveloped = service.SearchService()._envelope(
        _payload(),
        query="insurance",
        strategy="fts",
        sort="relevance",
        method="clause_aware",
        version_scope="latest",
        took_ms=4.2,
        filters=repository.SearchFilters(),
    )
    assert enveloped["strategy"] == "fts"
    assert enveloped["query"] == "insurance"
    assert enveloped["took_ms"] == 4.2
    assert enveloped["warnings"] == []


def test_a_query_that_parsed_to_nothing_is_explained_not_just_empty():
    """'the of and' is a successful search, not a broken one. Say which."""
    enveloped = service.SearchService()._envelope(
        _payload(total=0, no_searchable_terms=True, parsed_query=""),
        query="the of and",
        strategy="fts",
        sort="relevance",
        method="clause_aware",
        version_scope="latest",
        took_ms=1.0,
        filters=repository.SearchFilters(),
    )
    assert enveloped["total"] == 0
    assert any("no searchable terms" in w for w in enveloped["warnings"])


def test_applied_filters_are_echoed_in_the_warnings():
    """A result list that silently had a filter on it is a support ticket."""
    enveloped = service.SearchService()._envelope(
        _payload(),
        query="insurance",
        strategy="fts",
        sort="relevance",
        method="clause_aware",
        version_scope="latest",
        took_ms=1.0,
        filters=repository.SearchFilters(department="RBI"),
    )
    assert any("department=RBI" in w for w in enveloped["warnings"])


def test_timing_uses_a_monotonic_clock():
    """time.time() can step backwards; a negative duration discredits the rest."""
    result, elapsed = service._timed(lambda: "done")
    assert result == "done"
    assert elapsed >= 0
