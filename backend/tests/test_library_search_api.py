"""The four search endpoints, tested with a fake service and no database.

Same contract as ``test_api.py``, and the same warning: **nothing here proves
PostgreSQL integration.** The service is replaced through
``app.dependency_overrides``, so what is asserted is the API's own behaviour —
routing, query validation, status codes, the error envelope, and that a
payload survives serialisation with its provenance intact.

Whether the real SQL returns those payloads is answered by
``test_library_search_integration.py``, which needs a server and skips without
one.

The fake records the keyword arguments it was called with, because half of
what these endpoints do is *forwarding* — and a filter that is accepted by the
route and then dropped before the query is a defect no response-shape
assertion would catch.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

import pytest

pytest.importorskip("fastapi", reason="FastAPI is a Phase 4 dependency")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api.main import create_app  # noqa: E402
from backend.app.library_search.api import get_search_service  # noqa: E402
from backend.app.library_search.service import InvalidQuery  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixture payloads, shaped exactly like the repository's real output
# --------------------------------------------------------------------------- #

CLAUSE_HIT: Dict[str, Any] = {
    "clause_id": "CLS-0000248",
    "score": 0.0457,
    "snippet": "Prudential Norms on [[hl]]Capital[[/hl]] [[hl]]Adequacy[[/hl]]",
    "segment_type": "preamble",
    "segmentation_method": "clause_aware",
    "ordinal": 0,
    "clause_number": None,
    "clause_number_ambiguous": False,
    "heading": None,
    "level": 0,
    "text": None,
    "page_start": 1,
    "page_end": 2,
    "pages": [
        {
            "page_number": 1,
            "char_start": 0,
            "char_end": 420,
            "line_index_start": 0,
            "line_index_end": 9,
        },
        {
            "page_number": 2,
            "char_start": 0,
            "char_end": 118,
            "line_index_start": 0,
            "line_index_end": 2,
        },
    ],
    "offset_basis": "normalized_text",
    "document_id": "DOC-0004",
    "document_title": "RBI (Commercial Banks - Capital Adequacy) Eleventh Amendment",
    "department": None,
    "document_type": "amendment",
    "reference_no": "RBI/2026-27/297",
    "version_id": "VER-0004",
    "version_label": "2026-11th-amendment",
    "publication_date": "2026-10-23",
}

DOCUMENT_HIT: Dict[str, Any] = {
    "document_id": "DOC-0004",
    "title": "RBI (Commercial Banks - Capital Adequacy) Eleventh Amendment",
    "department": None,
    "document_type": "amendment",
    "reference_no": "RBI/2026-27/297",
    "version_id": "VER-0004",
    "version_label": "2026-11th-amendment",
    "publication_date": "2026-10-23",
    "page_count": 37,
    "version_count": 1,
    "extraction_status": "ok",
    "score": 1.0457,
    "matched_clauses": 2,
    "metadata_match": True,
    "best_clause": CLAUSE_HIT,
}

FACETS: Dict[str, Any] = {
    "documents": 17,
    "versions": 17,
    "pages": 148,
    "segments": {"clause_aware": 728, "paragraph": 775},
    "departments": [
        {"value": "IRDAI", "label": "IRDAI", "count": 12},
        {"value": None, "label": "Not stated", "count": 1},
    ],
    "document_types": [{"value": "circular", "label": "circular", "count": 8}],
    "publication_years": [{"value": "2026", "label": "2026", "count": 10}],
    "extraction_statuses": [{"value": "ok", "label": "ok", "count": 17}],
    "date_range": {"earliest": "2021-05-20", "latest": "2026-11-02"},
}


class FakeSearchService:
    """Records its calls and returns fixed payloads. No database anywhere."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.raise_invalid: str | None = None

    def _record(self, name: str, query, kwargs) -> None:
        self.calls.append({"name": name, "query": query, **kwargs})

    def _envelope(self, items: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        return {
            "query": kwargs.get("query", "capital adequacy"),
            "strategy": kwargs.get("strategy", "fts"),
            "parsed_query": "'capit' & 'adequaci'",
            "sort": kwargs.get("sort", "relevance"),
            "segmentation_method": kwargs.get("method", "clause_aware"),
            "version_scope": kwargs.get("version_scope", "latest"),
            "total": len(items),
            "limit": kwargs.get("limit", 20),
            "offset": kwargs.get("offset", 0),
            "took_ms": 3.5,
            "warnings": [],
            "items": copy.deepcopy(items),
        }

    def search_documents(self, query, **kwargs):
        self._record("search_documents", query, kwargs)
        if self.raise_invalid:
            raise InvalidQuery(self.raise_invalid)
        return self._envelope([DOCUMENT_HIT], query=query, **kwargs)

    def search_clauses(self, query, **kwargs):
        self._record("search_clauses", query, kwargs)
        if self.raise_invalid:
            raise InvalidQuery(self.raise_invalid)
        return self._envelope([CLAUSE_HIT], query=query, **kwargs)

    def facets(self):
        self._record("facets", None, {})
        return copy.deepcopy(FACETS)

    def suggest(self, prefix, **kwargs):
        self._record("suggest", prefix, kwargs)
        return {
            "prefix": prefix,
            "items": [
                {
                    "value": "RBI (Commercial Banks - Capital Adequacy)",
                    "kind": "title",
                    "document_id": "DOC-0004",
                }
            ],
        }


@pytest.fixture
def fake() -> FakeSearchService:
    return FakeSearchService()


@pytest.fixture
def client(fake) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: fake
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def test_document_search_returns_the_envelope_and_a_hit(client):
    response = client.get("/api/search", params={"q": "capital adequacy"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["strategy"] == "fts"
    assert body["parsed_query"] == "'capit' & 'adequaci'"
    assert body["items"][0]["document_id"] == "DOC-0004"
    assert body["items"][0]["metadata_match"] is True


def test_clause_search_preserves_multi_page_provenance(client):
    """The project's load-bearing guarantee, asserted at the HTTP boundary."""
    response = client.get("/api/search/clauses", params={"q": "capital adequacy"})
    assert response.status_code == 200
    hit = response.json()["items"][0]
    assert hit["page_start"] == 1 and hit["page_end"] == 2
    assert [page["page_number"] for page in hit["pages"]] == [1, 2]
    assert hit["offset_basis"] == "normalized_text"


def test_clause_hit_carries_the_character_range_on_each_page(client):
    hit = client.get("/api/search/clauses", params={"q": "x"}).json()["items"][0]
    for page in hit["pages"]:
        assert page["char_end"] >= page["char_start"]
        assert {"page_number", "char_start", "char_end", "line_index_start", "line_index_end"} <= set(page)


def test_snippet_is_delivered_with_sentinels_not_html(client):
    """A client must receive text it can mark up itself, never markup."""
    hit = client.get("/api/search/clauses", params={"q": "x"}).json()["items"][0]
    assert "[[hl]]" in hit["snippet"]
    assert "<mark>" not in hit["snippet"]


def test_document_hit_embeds_its_best_clause_with_provenance(client):
    hit = client.get("/api/search", params={"q": "x"}).json()["items"][0]
    assert hit["best_clause"]["clause_id"] == "CLS-0000248"
    assert hit["best_clause"]["pages"]


def test_facets_expose_the_null_bucket(client):
    """A facet list that hides nulls makes those documents unfilterable."""
    body = client.get("/api/library/facets").json()
    nulls = [f for f in body["departments"] if f["value"] is None]
    assert len(nulls) == 1
    assert nulls[0]["label"] == "Not stated"


def test_facets_report_both_segmentation_methods(client):
    body = client.get("/api/library/facets").json()
    assert set(body["segments"]) == {"clause_aware", "paragraph"}


def test_suggest_returns_titles_with_their_document_id(client):
    body = client.get("/api/search/suggest", params={"q": "capital"}).json()
    assert body["items"][0]["kind"] == "title"
    assert body["items"][0]["document_id"] == "DOC-0004"


def test_suggest_accepts_an_empty_prefix_without_erroring(client):
    """Autocomplete fires on every keystroke; the first one is not a mistake."""
    assert client.get("/api/search/suggest", params={"q": ""}).status_code == 200


# --------------------------------------------------------------------------- #
# Forwarding: a filter accepted and then dropped is the defect to catch
# --------------------------------------------------------------------------- #


def test_every_clause_filter_reaches_the_service(client, fake):
    client.get(
        "/api/search/clauses",
        params={
            "q": "insurance",
            "strategy": "ilike",
            "method": "paragraph",
            "sort": "date_desc",
            "version_scope": "all",
            "document_id": "DOC-0001",
            "version_id": "VER-0001",
            "department": "IRDAI",
            "document_type": "circular",
            "segment_type": "clause",
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
            "page_from": 2,
            "page_to": 5,
            "include_text": "true",
            "limit": 7,
            "offset": 14,
        },
    )
    call = fake.calls[-1]
    assert call["name"] == "search_clauses"
    assert call["query"] == "insurance"
    assert call["strategy"] == "ilike"
    assert call["method"] == "paragraph"
    assert call["sort"] == "date_desc"
    assert call["version_scope"] == "all"
    assert call["document_id"] == "DOC-0001"
    assert call["version_id"] == "VER-0001"
    assert call["department"] == "IRDAI"
    assert call["document_type"] == "circular"
    assert call["segment_type"] == "clause"
    assert call["date_from"] == "2026-01-01"
    assert call["date_to"] == "2026-12-31"
    assert call["page_from"] == 2
    assert call["page_to"] == 5
    assert call["include_text"] is True
    assert call["limit"] == 7
    assert call["offset"] == 14


def test_document_search_forwards_its_filters(client, fake):
    client.get(
        "/api/search",
        params={
            "q": "insurance",
            "department": "null",
            "document_type": "notification",
            "date_from": "2024-01-01",
            "limit": 3,
        },
    )
    call = fake.calls[-1]
    assert call["department"] == "null"
    assert call["document_type"] == "notification"
    assert call["date_from"] == "2024-01-01"
    assert call["limit"] == 3


def test_defaults_are_the_documented_ones(client, fake):
    client.get("/api/search/clauses", params={"q": "insurance"})
    call = fake.calls[-1]
    assert call["strategy"] == "fts"
    assert call["method"] == "clause_aware"
    assert call["sort"] == "relevance"
    assert call["version_scope"] == "latest"
    assert call["include_text"] is False
    assert call["limit"] == 20
    assert call["offset"] == 0


# --------------------------------------------------------------------------- #
# Validation and the error envelope
# --------------------------------------------------------------------------- #


def _assert_envelope(response, code: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert isinstance(body["error"]["details"], dict)


@pytest.mark.parametrize("path", ["/api/search", "/api/search/clauses"])
def test_a_missing_query_is_a_validation_error(client, path):
    _assert_envelope(client.get(path), "VALIDATION_ERROR")


@pytest.mark.parametrize("path", ["/api/search", "/api/search/clauses"])
def test_an_empty_query_is_a_validation_error(client, path):
    _assert_envelope(client.get(path, params={"q": ""}), "VALIDATION_ERROR")


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x", "strategy": "semantic"},
        {"q": "x", "strategy": "elastic"},
        {"q": "x", "method": "sentence"},
        {"q": "x", "sort": "random"},
        {"q": "x", "version_scope": "every"},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 101},
        {"q": "x", "offset": -1},
        {"q": "x" * 400},
    ],
)
def test_out_of_contract_options_are_refused(client, params):
    _assert_envelope(client.get("/api/search/clauses", params=params), "VALIDATION_ERROR")


@pytest.mark.parametrize("value", ["2026-13-01", "01-01-2026", "yesterday", "2026"])
def test_a_malformed_date_is_a_422_not_a_500(client, value):
    """Letting SQL raise on a bad date turns a client mistake into our bug."""
    response = client.get("/api/search", params={"q": "x", "date_from": value})
    _assert_envelope(response, "VALIDATION_ERROR")
    assert value in response.text


def test_an_inverted_date_range_is_refused(client):
    response = client.get(
        "/api/search", params={"q": "x", "date_from": "2026-06-01", "date_to": "2026-01-01"}
    )
    _assert_envelope(response, "VALIDATION_ERROR")


def test_an_inverted_page_range_is_refused(client):
    response = client.get(
        "/api/search/clauses", params={"q": "x", "page_from": 9, "page_to": 2}
    )
    _assert_envelope(response, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == {"page_from": 9, "page_to": 2}


def test_a_service_level_invalid_query_becomes_the_same_envelope(client, fake):
    """The service's own domain error must not surface as a 500."""
    fake.raise_invalid = "something the service refuses"
    _assert_envelope(client.get("/api/search", params={"q": "x"}), "VALIDATION_ERROR")


def test_a_valid_date_passes_validation(client):
    assert (
        client.get("/api/search", params={"q": "x", "date_from": "2026-01-01"}).status_code
        == 200
    )


# --------------------------------------------------------------------------- #
# Regression: the pre-existing API must be unchanged
# --------------------------------------------------------------------------- #


def test_the_four_search_paths_are_mounted_under_api():
    paths = set(create_app().openapi()["paths"])
    assert {
        "/api/search",
        "/api/search/clauses",
        "/api/search/suggest",
        "/api/library/facets",
    } <= paths


def test_the_six_original_endpoints_are_still_present():
    """Library & Search is additive. Removing a path would break four modules."""
    paths = set(create_app().openapi()["paths"])
    assert {
        "/api/health",
        "/api/documents",
        "/api/documents/ingest",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/pages",
        "/api/documents/{document_id}/clauses",
    } <= paths


def test_the_api_surface_is_exactly_fourteen_paths():
    """A count, so an accidentally exposed route is a failing test.

    Ten when Library & Search was built; fourteen since
    ``backend/app/comparison/`` added ``/api/compare``,
    ``/api/compare/options``, ``/api/compare/entities`` and
    ``/api/compare/entities/labels``. The count is deliberately exact rather
    than a lower bound, so a route added by accident still fails here.
    ``test_library_search_scaffold.py`` pins the full set by name.
    """
    assert len(create_app().openapi()["paths"]) == 14


def test_search_endpoints_document_the_error_envelope_in_openapi():
    spec = create_app().openapi()
    for path in ("/api/search", "/api/search/clauses"):
        assert "422" in spec["paths"][path]["get"]["responses"]


def test_unknown_search_subpath_is_a_404_envelope(client):
    response = client.get("/api/search/nonexistent")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
