"""The API against a **real PostgreSQL database and the real pipeline**.

Everything here is marked ``@pytest.mark.integration`` and skips when
``NIYAMSETU_TEST_DATABASE_URL`` is unset. That marker is the answer to "was
this verified against a database, or mocked?" — ``test_api.py`` carries it
nowhere, this module carries it everywhere.

What these tests cover that the mocked ones cannot:

* a real PDF going through validate → extract → clean → segment → persist,
  end to end through the HTTP layer;
* that the payload shapes in ``test_api.py``'s fake match what the real
  repository actually produces — the fake is only useful while that holds;
* that a clause retrieved over HTTP still resolves from its stored offsets,
  which is the project's load-bearing guarantee carried one layer further out.

They run against the throwaway ``niyamsetu_test`` database, which is truncated
at the start of the module. ``assert_is_test_url`` refuses a database whose
name lacks ``test``, so this cannot touch the development corpus.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest

pytest.importorskip("fastapi", reason="FastAPI is a Phase 4 dependency")
pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.app.api.dependencies import (  # noqa: E402
    Repository,
    Settings,
    get_database_status,
    get_repository,
)
from backend.app.api.main import create_app  # noqa: E402
from backend.app.db import engine as engine_module  # noqa: E402
from backend.app.db.config import (  # noqa: E402
    assert_is_test_url,
    test_database_url as resolve_test_database_url,
)
from backend.app.db.migrate import migrate  # noqa: E402
from backend.tests.pdf_builder import build_pdf_bytes  # noqa: E402
from backend.tests.test_db_schema import (  # noqa: E402
    TABLES,
    _create_database_if_missing,
)

pytestmark = pytest.mark.integration


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


#: A small but structurally real document: numbered clauses, a nested
#: sub-clause, and a clause that runs over the page break.
PAGE_1 = [
    "RESERVE BANK OF INDIA",
    "Fixture Circular for API integration testing",
    "",
    "1. Applicability",
    "These directions apply to every scheduled commercial bank operating",
    "in the territory, with effect from the date of publication.",
    "",
    "2. The reporting entity shall submit the prescribed return within",
    "thirty days of the close of each quarter, and shall retain",
]
PAGE_2 = [
    "RESERVE BANK OF INDIA",
    "supporting records for a period of five years from that date.",
    "",
    "3. Penalties",
    "(a) A failure to report attracts a penalty under section 47A.",
    "(b) Repeated failure may attract cancellation of the licence.",
]


@pytest.fixture(scope="module")
def api_db() -> str:
    """A migrated, empty throwaway database."""
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
    yield url
    engine_module.dispose_engine()


@pytest.fixture(scope="module")
def client(api_db: str, tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """An app bound to the test database, writing artefacts to a temp dir.

    The artefact directories are redirected so an ingestion test cannot write
    into the real ``data/processed`` or ``data/raw``.
    """
    workspace: Path = tmp_path_factory.mktemp("api-integration")
    settings = Settings(
        processed_dir=workspace / "processed",
        raw_dir=workspace / "raw",
    )
    app = create_app(settings)
    app.dependency_overrides[get_repository] = lambda: Repository(url=api_db)
    app.dependency_overrides[get_database_status] = lambda: (
        "ok" if engine_module.ping(api_db) else "unreachable"
    )
    return TestClient(app)


def _ingest(client: TestClient, pages, **form: Any):
    return client.post(
        "/api/documents/ingest",
        files={"file": ("fixture.pdf", build_pdf_bytes(pages), "application/pdf")},
        data={
            "title": "Fixture Circular for API integration testing",
            "document_group": "api-integration-fixture",
            "version_label": "v1",
            "document_type": "circular",
            "issuing_department": "Reserve Bank of India",
            **form,
        },
    )


@pytest.fixture(scope="module")
def ingested(client: TestClient) -> Dict[str, Any]:
    """Ingest the fixture document once for the whole module."""
    response = _ingest(client, [PAGE_1, PAGE_2])
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Health, against a database that is genuinely reachable
# --------------------------------------------------------------------------- #


def test_health_reports_a_real_reachable_database(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "database": "ok", "version": "0.1.0"}


# --------------------------------------------------------------------------- #
# Ingestion through the real pipeline
# --------------------------------------------------------------------------- #


def test_a_real_pdf_is_extracted_segmented_and_stored(
    ingested: Dict[str, Any],
) -> None:
    assert ingested["status"] == "loaded"
    assert ingested["document_id"] == "DOC-0001"
    assert ingested["version_id"] == "VER-0001"
    assert ingested["page_count"] == 2
    assert ingested["extraction_status"] == "ok"
    assert ingested["offset_basis"] == "normalized_text"
    assert len(ingested["checksum"]) == 64

    # Both methods ran and produced segments -- the thing that would silently
    # not happen if the API had reimplemented segmentation.
    assert set(ingested["segment_counts"]) == {"paragraph", "clause_aware"}
    assert all(count > 0 for count in ingested["segment_counts"].values())
    assert ingested["segment_total"] == sum(ingested["segment_counts"].values())


def test_the_running_header_was_detected_and_removed(
    ingested: Dict[str, Any],
) -> None:
    """'RESERVE BANK OF INDIA' tops both pages, so cleaning should remove it."""
    assert ingested["removed_lines"] >= 1


def test_re_uploading_the_same_pdf_is_a_recognised_duplicate(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    """Idempotency, through HTTP: 200 rather than 201, and nothing created."""
    before = client.get("/api/documents").json()["total"]
    response = _ingest(client, [PAGE_1, PAGE_2])
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped_duplicate"
    assert body["version_id"] == ingested["version_id"]
    assert client.get("/api/documents").json()["total"] == before


# --------------------------------------------------------------------------- #
# Retrieval of what was just ingested
# --------------------------------------------------------------------------- #


def test_the_ingested_document_appears_in_the_listing(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    body = client.get("/api/documents").json()
    ids = [item["document_id"] for item in body["items"]]
    assert ingested["document_id"] in ids
    item = next(i for i in body["items"] if i["document_id"] == ingested["document_id"])
    assert item["document_type"] == "circular"
    assert item["version_count"] == 1


def test_document_detail_matches_what_ingestion_reported(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    body = client.get(f"/api/documents/{ingested['document_id']}").json()
    version = body["versions"][0]
    assert version["version_id"] == ingested["version_id"]
    assert version["file_checksum"] == ingested["checksum"]
    assert version["page_count"] == ingested["page_count"]
    assert version["extraction_status"] == "ok"
    assert version["extraction_tool"].startswith("pypdf")
    assert version["offset_basis"] == "normalized_text"
    assert version["segment_counts"] == ingested["segment_counts"]


def test_pages_round_trip_with_raw_and_normalized_distinct(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    body = client.get(
        f"/api/documents/{ingested['document_id']}/pages?text=both"
    ).json()
    assert body["total"] == 2
    assert body["offset_basis"] == "normalized_text"
    for page in body["items"]:
        assert page["raw_text"] is not None
        assert page["normalized_text"] is not None
        assert page["char_count"] > 0
        assert page["is_empty"] is False


def test_clauses_are_returned_for_both_methods(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    for method in ("clause_aware", "paragraph"):
        body = client.get(
            f"/api/documents/{ingested['document_id']}/clauses?method={method}&limit=100"
        ).json()
        assert body["segmentation_method"] == method
        assert body["total"] == ingested["segment_counts"][method]
        assert body["offset_basis"] == "normalized_text"


def test_clause_numbers_were_detected_by_the_real_segmenter(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    body = client.get(
        f"/api/documents/{ingested['document_id']}/clauses?method=clause_aware&limit=100"
    ).json()
    numbers = {item["clause_number"] for item in body["items"] if item["clause_number"]}
    assert numbers, "the clause-aware method should recover printed clause numbers"


# --------------------------------------------------------------------------- #
# Provenance, carried all the way out through HTTP
# --------------------------------------------------------------------------- #


def test_every_returned_clause_carries_page_provenance(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    body = client.get(
        f"/api/documents/{ingested['document_id']}/clauses?method=clause_aware&limit=100"
    ).json()
    assert body["items"]
    for item in body["items"]:
        assert item["pages"], f"{item['clause_id']} has no page provenance"
        touched = [page["page_number"] for page in item["pages"]]
        assert touched == sorted(touched)
        assert touched[0] == item["page_start"]
        assert touched[-1] == item["page_end"]
        for page in item["pages"]:
            assert page["char_end"] >= page["char_start"]


def test_every_clause_resolves_from_its_offsets_against_the_served_pages(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    """**The acceptance condition, at the HTTP boundary.**

    Fetch the pages and the clauses as a consumer would, slice the served page
    text at the served offsets, and require the clause text back. If this
    passes, a citation built from this API points at real text.
    """
    document_id = ingested["document_id"]
    pages = client.get(f"/api/documents/{document_id}/pages?limit=100").json()
    page_text = {
        page["page_number"]: page["normalized_text"] for page in pages["items"]
    }

    clauses = client.get(
        f"/api/documents/{document_id}/clauses?method=clause_aware&limit=100"
    ).json()

    unresolved = []
    for item in clauses["items"]:
        rebuilt = " ".join(
            page_text[page["page_number"]][page["char_start"] : page["char_end"]]
            for page in item["pages"]
        )
        if _collapse(rebuilt) != _collapse(item["text"]):
            unresolved.append(item["clause_id"])

    assert not unresolved, (
        f"{len(unresolved)} of {len(clauses['items'])} clauses do not rebuild "
        f"from the offsets this API served: {unresolved[:5]}"
    )


def test_a_page_spanning_clause_keeps_both_pages_through_the_api(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    """The fixture's clause 2 runs over the page break by construction."""
    body = client.get(
        f"/api/documents/{ingested['document_id']}/clauses?method=clause_aware&limit=100"
    ).json()
    spanning = [item for item in body["items"] if item["page_end"] > item["page_start"]]
    assert spanning, "the fixture document should produce a page-spanning clause"
    for item in spanning:
        assert len(item["pages"]) == item["page_end"] - item["page_start"] + 1


# --------------------------------------------------------------------------- #
# The mocked suite's fixtures must keep matching reality
# --------------------------------------------------------------------------- #


def test_the_real_payloads_have_the_fields_the_mocked_tests_assume(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    """Guards the fake in ``test_api.py`` against drifting from the database.

    A fake that silently stops matching the real payload turns 39 passing
    tests into 39 tests of nothing.
    """
    from backend.tests.test_api import CLAUSE, DOCUMENT_DETAIL, PAGE

    document_id = ingested["document_id"]

    real_clause = client.get(
        f"/api/documents/{document_id}/clauses?limit=1"
    ).json()["items"][0]
    assert set(real_clause) == set(CLAUSE)
    assert set(real_clause["pages"][0]) == set(CLAUSE["pages"][0])

    real_page = client.get(
        f"/api/documents/{document_id}/pages?text=both&limit=1"
    ).json()["items"][0]
    assert set(real_page) >= set(PAGE)

    real_version = client.get(f"/api/documents/{document_id}").json()["versions"][0]
    assert set(real_version) == set(DOCUMENT_DETAIL["versions"][0])


def test_a_duplicate_response_still_identifies_the_document(
    client: TestClient, ingested: Dict[str, Any]
) -> None:
    """A duplicate must say which document it is, not just that it existed.

    Found by running a real upload through uvicorn: the response carried
    `document_id: null`, so a client re-uploading a known file could not tell
    what it had hit without a second lookup.
    """
    body = _ingest(client, [PAGE_1, PAGE_2]).json()
    assert body["status"] == "skipped_duplicate"
    assert body["document_id"] == ingested["document_id"]
    assert body["version_id"] == ingested["version_id"]
    assert body["page_count"] == ingested["page_count"]
    assert body["segment_counts"] == ingested["segment_counts"]


def test_a_duplicate_upload_does_not_overwrite_stored_artefacts(
    client: TestClient, ingested: Dict[str, Any], tmp_path: Path
) -> None:
    """A skipped duplicate must leave `data/processed` exactly as it was.

    This is a regression test for a defect found by running a real upload
    through uvicorn: artefacts were written into place *before* the loader
    decided the version was a duplicate, so a re-upload with a sparser form
    body silently replaced a corpus document's source_url, retrieved_at and
    version_label -- while the response said "nothing changed".
    """
    from backend.app.api.dependencies import get_settings

    settings = client.app.dependency_overrides.get(get_settings, get_settings)()
    # Resolved by fingerprint glob rather than public id: the directory is
    # named by the content-derived VER-<hex>, not by VER-0001.
    candidates = list(settings.processed_dir.glob("VER-*"))
    assert candidates, "the first ingestion should have published artefacts"
    extraction = candidates[0] / "extraction.json"
    before = extraction.read_text(encoding="utf-8")

    response = _ingest(client, [PAGE_1, PAGE_2], source_url="", version_label="")
    assert response.status_code == 200
    assert response.json()["status"] == "skipped_duplicate"

    assert extraction.read_text(encoding="utf-8") == before, (
        "a duplicate upload overwrote the stored artefacts"
    )
