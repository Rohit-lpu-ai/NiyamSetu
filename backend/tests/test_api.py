"""The FastAPI layer, tested with a **fake repository and no database**.

Read this first: **nothing in this module proves PostgreSQL integration.**
Every test here replaces the repository with an in-memory fake through
``app.dependency_overrides``, so what is asserted is the API's own behaviour --
routing, validation, status codes, the error envelope, and that the payload a
repository returns survives serialisation with its provenance intact.

Whether the *real* repository returns those payloads is a different question,
answered by ``test_db_schema.py``, ``test_db_integration.py`` and the
``integration``-marked tests in ``test_api_integration.py``. Those need a
server and skip without one.

The split exists because a mocked test that quietly looked like an integration
test would be the most misleading thing in the suite.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("fastapi", reason="FastAPI is a Phase 4 dependency")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api.dependencies import (  # noqa: E402
    Repository,
    Settings,
    get_database_status,
    get_repository,
)
from backend.app.api.main import create_app  # noqa: E402
from backend.app.db.loader import LoadError, VersionConflict  # noqa: E402
from backend.app.db.repository import NotFound  # noqa: E402
from backend.tests.pdf_builder import build_pdf_bytes, page_lines  # noqa: E402

# --------------------------------------------------------------------------- #
# The fake repository
# --------------------------------------------------------------------------- #
#
# Returns payloads shaped exactly like the real repository's. Kept in sync by
# being written from the live output (see docs/api.md) -- and, more usefully,
# by test_api_integration.py asserting the same fields against real data.

CLAUSE: Dict[str, Any] = {
    "clause_id": "CLS-0000253",
    "fingerprint": "SEG-2146f692b738fe91",
    "ordinal": 5,
    "segment_type": "clause",
    "clause_number": "(2)",
    "clause_number_ambiguous": False,
    "heading": None,
    "text": "(2) The minimum leverage ratio shall be 3.5 per cent.",
    "page_start": 1,
    "page_end": 2,
    "pages": [
        {
            "page_number": 1,
            "char_start": 1719,
            "char_end": 1955,
            "line_index_start": 32,
            "line_index_end": 34,
        },
        {
            "page_number": 2,
            "char_start": 119,
            "char_end": 318,
            "line_index_start": 4,
            "line_index_end": 6,
        },
    ],
    "level": 2,
    "marker_style": "paren_number",
    "parent_clause_id": "CLS-0000250",
    "metadata": {"marker_style": "paren_number"},
}

PARAGRAPH_SEGMENT: Dict[str, Any] = {
    **copy.deepcopy(CLAUSE),
    "clause_id": "CLS-0000900",
    "fingerprint": "SEG-aaaaaaaaaaaaaaaa",
    "ordinal": 0,
    "segment_type": "paragraph",
    "clause_number": None,
    "marker_style": None,
    "parent_clause_id": None,
    "level": 0,
    "metadata": {},
}

HEADING_SEGMENT: Dict[str, Any] = {
    **copy.deepcopy(CLAUSE),
    "clause_id": "CLS-0000901",
    "fingerprint": "SEG-bbbbbbbbbbbbbbbb",
    "ordinal": 6,
    "segment_type": "heading",
    "clause_number": None,
    "page_start": 1,
    "page_end": 1,
    "pages": [CLAUSE["pages"][0]],
}

PAGE: Dict[str, Any] = {
    "page_number": 1,
    "char_count": 2038,
    "word_count": 310,
    "is_empty": False,
    "has_meaningful_text": True,
    "normalized_text": "1. The first clause of the fixture document.",
    "raw_text": "1.  The first  clause of the fixture document.",
}

DOCUMENT_DETAIL: Dict[str, Any] = {
    "document_id": "DOC-0001",
    "title": "Fixture Directions, 2026",
    "department": "Fixture Department",
    "document_type": "amendment",
    "reference_no": "FIX/1/2026",
    "versions": [
        {
            "version_id": "VER-0001",
            "version_label": "v1",
            "publication_date": "2026-11-01",
            "page_count": 37,
            "extraction_status": "ok",
            "extraction_tool": "pypdf 6.18.1",
            "offset_basis": "normalized_text",
            "source_url": "https://example.gov.in/fixture.pdf",
            "file_checksum": "c" * 64,
            "processing_warnings": ["page 7 extracted 0 characters"],
            "ingested_at": "2026-11-12T00:00:00+00:00",
            "supersedes_version_id": None,
            "segment_counts": {"clause_aware": 254, "paragraph": 74},
        }
    ],
}


class FakeRepository(Repository):
    """In-memory stand-in. Raises what the real repository raises."""

    def __init__(self, *, fail_with: Optional[Exception] = None) -> None:
        super().__init__(url=None)
        self.fail_with = fail_with
        self.calls: List[tuple] = []

    def _guard(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if self.fail_with is not None:
            raise self.fail_with

    def list_documents(self, **kwargs: Any) -> Dict[str, Any]:
        self._guard("list_documents", kwargs)
        limit = kwargs.get("limit") or 20
        offset = kwargs.get("offset") or 0
        items = [
            {
                "document_id": "DOC-0001",
                "title": "Fixture Directions, 2026",
                "department": "Fixture Department",
                "document_type": "amendment",
                "reference_no": "FIX/1/2026",
                "version_count": 1,
                "latest_publication_date": "2026-11-01",
            }
        ]
        if kwargs.get("q") == "nothing":
            items = []
        return {
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items[offset : offset + limit],
        }

    def get_document(self, document_id: str) -> Dict[str, Any]:
        self._guard("get_document", document_id)
        if document_id != "DOC-0001":
            raise NotFound(f"no document with id {document_id}")
        return copy.deepcopy(DOCUMENT_DETAIL)

    def get_pages(self, document_id: str, **kwargs: Any) -> Dict[str, Any]:
        self._guard("get_pages", document_id, kwargs)
        if document_id != "DOC-0001":
            raise NotFound(f"no document with id {document_id}")
        variant = kwargs.get("text_variant", "normalized")
        page = copy.deepcopy(PAGE)
        if variant == "normalized":
            page.pop("raw_text")
        elif variant == "raw":
            page.pop("normalized_text")
        return {
            "document_id": document_id,
            "version_id": "VER-0001",
            "offset_basis": "normalized_text",
            "page_count": 37,
            "items": [page, {**copy.deepcopy(page), "page_number": 2}],
        }

    def get_segments(self, document_id: str, **kwargs: Any) -> Dict[str, Any]:
        self._guard("get_segments", document_id, kwargs)
        if document_id != "DOC-0001":
            raise NotFound(f"no document with id {document_id}")
        method = kwargs.get("method", "clause_aware")
        items = (
            [copy.deepcopy(CLAUSE), copy.deepcopy(HEADING_SEGMENT)]
            if method == "clause_aware"
            else [copy.deepcopy(PARAGRAPH_SEGMENT)]
        )
        return {
            "document_id": document_id,
            "version_id": "VER-0001",
            "segmentation_method": method,
            "offset_basis": "normalized_text",
            "total": len(items),
            "limit": kwargs.get("limit") or 50,
            "offset": kwargs.get("offset") or 0,
            "items": items,
        }

    def ping(self) -> bool:
        return self.fail_with is None


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(repo: FakeRepository) -> TestClient:
    """An app with the repository replaced and the database reported healthy."""
    app = create_app(Settings())
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_database_status] = lambda: "ok"
    return TestClient(app)


def make_pdf(pages: int = 2) -> bytes:
    return build_pdf_bytes([page_lines(n) for n in range(1, pages + 1)])


# --------------------------------------------------------------------------- #
# Startup and documentation
# --------------------------------------------------------------------------- #


def test_the_application_starts_and_mounts_every_endpoint() -> None:
    app = create_app(Settings())
    paths = {
        route.path for route in app.routes if hasattr(route, "methods")
    }
    assert {
        "/api/health",
        "/api/documents/ingest",
        "/api/documents",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/pages",
        "/api/documents/{document_id}/clauses",
    } <= paths


def test_openapi_and_swagger_are_served(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_docs_can_be_switched_off() -> None:
    app = create_app(Settings(docs_enabled=False))
    assert TestClient(app).get("/docs").status_code == 404


# --------------------------------------------------------------------------- #
# Health - the API and the database are separate claims
# --------------------------------------------------------------------------- #


def test_health_reports_ok_when_the_database_answers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "version": "0.1.0",
    }


def test_health_reports_503_and_degraded_when_the_database_is_down() -> None:
    """The point of the endpoint: an unreachable DB must never read as 'ok'."""
    app = create_app(Settings())
    app.dependency_overrides[get_database_status] = lambda: "unreachable"
    response = TestClient(app).get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["database"] == "unreachable"
    assert body["status"] == "degraded"


def test_health_does_not_raise_when_configuration_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A health check that 500s on bad config tells an operator less."""
    from backend.app.api import dependencies

    def explode(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("no .env anywhere")

    monkeypatch.setattr(dependencies, "database_url", explode)
    assert dependencies.get_database_status() == "unreachable"


# --------------------------------------------------------------------------- #
# Ingestion - validation paths (no pipeline is run in these)
# --------------------------------------------------------------------------- #


def test_an_empty_upload_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_an_oversized_upload_is_rejected_before_it_is_written(
    repo: FakeRepository,
) -> None:
    app = create_app(Settings(max_upload_bytes=500))
    app.dependency_overrides[get_repository] = lambda: repo
    response = TestClient(app).post(
        "/api/documents/ingest",
        files={"file": ("big.pdf", make_pdf(3), "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 413
    body = response.json()["error"]
    assert body["code"] == "FILE_TOO_LARGE"
    assert body["details"]["limit_bytes"] == 500


def test_a_non_pdf_upload_is_rejected(client: TestClient) -> None:
    """An HTML file named .pdf: the content is sniffed, not the extension."""
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("evil.pdf", b"<html><body>not a pdf</body></html>", "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_ingestion_requires_a_title_and_document_group(client: TestClient) -> None:
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    fields = {error["field"] for error in body["details"]["errors"]}
    assert any("title" in field for field in fields)
    assert any("document_group" in field for field in fields)


def test_a_validation_error_never_echoes_the_submitted_value(
    client: TestClient,
) -> None:
    """Echoing a rejected value back is how a message becomes a data leak."""
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", b"%PDF-1.4 secret-content-marker", "application/pdf")},
        data={"title": "", "document_group": "g"},
    )
    assert response.status_code == 422
    assert "secret-content-marker" not in response.text


# --------------------------------------------------------------------------- #
# Ingestion - outcomes, with the pipeline stubbed
# --------------------------------------------------------------------------- #


def _stub_ingestion(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    from backend.app.api import routes

    def fake(*_args: Any, **_kwargs: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(routes, "ingest_pdf_bytes", fake)


def _result(status: str = "loaded", message: Optional[str] = None) -> Any:
    from backend.app.api.ingestion import IngestionResult
    from backend.app.db.loader import LoadOutcome

    return IngestionResult(
        outcome=LoadOutcome(
            version_fingerprint="VER-bbbbbbbbbbbbbbbb",
            status=status,
            document_public_id="DOC-0001",
            version_public_id="VER-0001",
            pages=2,
            removed_lines=1,
            segments={"paragraph": 1, "clause_aware": 2},
            segment_pages=5,
            message=message,
        ),
        checksum="c" * 64,
        page_count=2,
        extraction_status="ok",
        offset_basis="normalized_text",
        warnings=[],
    )


def test_a_successful_ingestion_returns_201_and_the_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ingestion(monkeypatch, _result())
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", make_pdf(), "application/pdf")},
        data={"title": "Fixture Directions", "document_group": "fixture-group"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "loaded"
    assert body["document_id"] == "DOC-0001"
    assert body["version_id"] == "VER-0001"
    assert body["checksum"] == "c" * 64
    assert body["page_count"] == 2
    assert body["segment_counts"] == {"paragraph": 1, "clause_aware": 2}
    assert body["segment_total"] == 3
    assert body["offset_basis"] == "normalized_text"


def test_a_duplicate_upload_returns_200_not_201(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not an error -- but a client that cannot tell will double-count."""
    _stub_ingestion(
        monkeypatch, _result("skipped_duplicate", "already loaded as VER-0001")
    )
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", make_pdf(), "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped_duplicate"
    assert "already loaded" in body["message"]


def test_a_conflicting_version_returns_409_and_says_nothing_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ingestion(
        monkeypatch, VersionConflict("VER-x conflicts with existing VER-0001")
    )
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", make_pdf(), "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "VERSION_CONFLICT"
    assert body["details"]["changed"] is False


def test_a_load_failure_returns_422_in_the_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ingestion(monkeypatch, LoadError("artefact missing"))
    response = client.post(
        "/api/documents/ingest",
        files={"file": ("x.pdf", make_pdf(), "application/pdf")},
        data={"title": "T", "document_group": "g"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOAD_ERROR"


# --------------------------------------------------------------------------- #
# Listing and detail
# --------------------------------------------------------------------------- #


def test_documents_are_listed_with_metadata(client: TestClient) -> None:
    response = client.get("/api/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["document_id"] == "DOC-0001"
    assert item["version_count"] == 1


def test_listing_passes_filters_through(
    client: TestClient, repo: FakeRepository
) -> None:
    client.get("/api/documents?q=Fixture&department=D&document_type=amendment")
    kwargs = repo.calls[-1][1]
    assert kwargs["q"] == "Fixture"
    assert kwargs["department"] == "D"
    assert kwargs["document_type"] == "amendment"


def test_an_empty_result_is_a_200_with_no_items(client: TestClient) -> None:
    body = client.get("/api/documents?q=nothing").json()
    assert body["total"] == 0 and body["items"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_bad_pagination_is_rejected(client: TestClient, query: str) -> None:
    response = client.get(f"/api/documents?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_document_detail_includes_version_and_processing_information(
    client: TestClient,
) -> None:
    body = client.get("/api/documents/DOC-0001").json()
    assert body["document_id"] == "DOC-0001"
    version = body["versions"][0]
    assert version["file_checksum"] == "c" * 64
    assert version["extraction_status"] == "ok"
    assert version["extraction_tool"] == "pypdf 6.18.1"
    assert version["offset_basis"] == "normalized_text"
    assert version["processing_warnings"] == ["page 7 extracted 0 characters"]
    assert version["segment_counts"] == {"clause_aware": 254, "paragraph": 74}


def test_an_unknown_document_is_404_in_the_envelope(client: TestClient) -> None:
    response = client.get("/api/documents/DOC-9999")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "DOC-9999" in body["error"]["message"]


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


def test_pages_are_returned_with_normalized_text_by_default(
    client: TestClient,
) -> None:
    body = client.get("/api/documents/DOC-0001/pages").json()
    assert body["offset_basis"] == "normalized_text"
    assert body["page_count"] == 37
    page = body["items"][0]
    assert page["normalized_text"]
    assert page["raw_text"] is None


def test_raw_text_is_available_and_distinct_from_normalized(
    client: TestClient,
) -> None:
    """The two are different claims; collapsing them loses the audit trail."""
    raw = client.get("/api/documents/DOC-0001/pages?text=raw").json()["items"][0]
    both = client.get("/api/documents/DOC-0001/pages?text=both").json()["items"][0]
    assert raw["raw_text"] and raw["normalized_text"] is None
    assert both["raw_text"] != both["normalized_text"]


def test_page_range_and_pagination_are_passed_and_applied(
    client: TestClient, repo: FakeRepository
) -> None:
    body = client.get(
        "/api/documents/DOC-0001/pages?page_from=1&page_to=2&limit=1&offset=1"
    ).json()
    assert repo.calls[-1][2]["page_from"] == 1
    assert body["limit"] == 1 and body["offset"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["page_number"] == 2


def test_an_inverted_page_range_is_rejected(client: TestClient) -> None:
    response = client.get("/api/documents/DOC-0001/pages?page_from=5&page_to=2")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_unknown_text_variant_is_rejected(client: TestClient) -> None:
    assert client.get("/api/documents/DOC-0001/pages?text=sideways").status_code == 422


def test_pages_for_an_unknown_document_are_404(client: TestClient) -> None:
    assert client.get("/api/documents/DOC-9999/pages").status_code == 404


# --------------------------------------------------------------------------- #
# Clauses - provenance is the thing being protected
# --------------------------------------------------------------------------- #


def test_clauses_are_returned_with_both_methods_selectable(
    client: TestClient,
) -> None:
    clause = client.get("/api/documents/DOC-0001/clauses?method=clause_aware").json()
    paragraph = client.get("/api/documents/DOC-0001/clauses?method=paragraph").json()
    assert clause["segmentation_method"] == "clause_aware"
    assert paragraph["segmentation_method"] == "paragraph"
    assert paragraph["items"][0]["segment_type"] == "paragraph"


def test_a_multi_page_clause_keeps_every_page_and_its_offsets(
    client: TestClient,
) -> None:
    """The load-bearing assertion of this whole API.

    A clause spanning two pages must arrive with both, each with its own
    character range. Collapsing it onto one page would silently break every
    citation into the second half.
    """
    item = client.get("/api/documents/DOC-0001/clauses").json()["items"][0]
    assert item["page_start"] == 1 and item["page_end"] == 2
    assert [page["page_number"] for page in item["pages"]] == [1, 2]
    assert item["pages"][0]["char_start"] == 1719
    assert item["pages"][0]["char_end"] == 1955
    assert item["pages"][1]["char_start"] == 119
    assert item["pages"][1]["line_index_end"] == 6


def test_the_offset_basis_is_reported_on_every_clause_response(
    client: TestClient,
) -> None:
    """D-11. A consumer slicing a different field gets the wrong span."""
    body = client.get("/api/documents/DOC-0001/clauses").json()
    assert body["offset_basis"] == "normalized_text"


def test_clause_structure_fields_survive_serialisation(client: TestClient) -> None:
    item = client.get("/api/documents/DOC-0001/clauses").json()["items"][0]
    assert item["clause_number"] == "(2)"
    assert item["clause_number_ambiguous"] is False
    assert item["level"] == 2
    assert item["marker_style"] == "paren_number"
    assert item["parent_clause_id"] == "CLS-0000250"
    assert item["fingerprint"].startswith("SEG-")


def test_clauses_can_be_filtered_by_segment_type(client: TestClient) -> None:
    body = client.get("/api/documents/DOC-0001/clauses?segment_type=heading").json()
    assert body["total"] == 1
    assert all(item["segment_type"] == "heading" for item in body["items"])


def test_filtering_by_an_absent_type_returns_an_empty_page(
    client: TestClient,
) -> None:
    body = client.get("/api/documents/DOC-0001/clauses?segment_type=annexure").json()
    assert body["total"] == 0 and body["items"] == []


def test_an_unknown_method_is_rejected(client: TestClient) -> None:
    assert (
        client.get("/api/documents/DOC-0001/clauses?method=sentence").status_code == 422
    )


def test_clauses_for_an_unknown_document_are_404(client: TestClient) -> None:
    assert client.get("/api/documents/DOC-9999/clauses").status_code == 404


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #


def test_a_database_failure_becomes_a_500_without_leaking_detail() -> None:
    """A DSN, a file path or a fragment of SQL must never reach the client."""
    secret = "postgresql://postgres:hunter2@localhost:5432/niyamsetu"
    app = create_app(Settings())
    app.dependency_overrides[get_repository] = lambda: FakeRepository(
        fail_with=RuntimeError(f"connection failed: {secret}")
    )
    app.dependency_overrides[get_database_status] = lambda: "ok"
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/documents")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "An internal error occurred."
    assert "hunter2" not in response.text
    assert "postgresql://" not in response.text


def test_every_error_response_uses_the_same_envelope(client: TestClient) -> None:
    for path in (
        "/api/documents/DOC-9999",
        "/api/documents/DOC-0001/clauses?method=bad",
        "/api/documents?limit=0",
        "/api/nope",
    ):
        body = client.get(path).json()
        assert set(body) == {"error"}, path
        assert set(body["error"]) == {"code", "message", "details"}, path
