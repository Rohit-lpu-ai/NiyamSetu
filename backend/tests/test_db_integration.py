"""End-to-end: the real RBI corpus, through the whole pipeline, into Postgres.

This is the test that answers the work package's acceptance condition —

    take an official government PDF, process it into paragraphs and clauses,
    preserve every source-page relationship, store it in PostgreSQL, and
    retrieve it reliably with its correct provenance

— against the actual four documents rather than a fixture.

It needs **both** a test database and the extraction artefacts in
``data/processed/``, and skips with a stated reason when either is absent.
``data/processed/`` is git-ignored, so on a fresh clone this skips until the
pipeline has been run:

    .venv\\Scripts\\python -m backend.app.document_processing.cli --manifest data/manifest.csv
    .venv\\Scripts\\python -m backend.app.document_processing.segmentation_cli --all

The deliberate difference from ``test_db_schema.py``: that module asserts exact
counts against a synthetic bundle, because it controls the input. This one
asserts *invariants* — every segment resolves, every page reference exists,
nothing is lost — because the corpus is allowed to change and a test that
pinned 501 would then be measuring the corpus rather than the code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from backend.app.db import engine as engine_module  # noqa: E402
from backend.app.db import repository  # noqa: E402
from backend.app.db.config import (  # noqa: E402
    assert_is_test_url,
    # Aliased so pytest does not collect it as a test; see test_db_schema.py.
    test_database_url as resolve_test_database_url,
)
from backend.app.db.loader import load_processed_tree  # noqa: E402
from backend.app.db.migrate import migrate  # noqa: E402
from backend.tests.test_db_schema import (  # noqa: E402
    TABLES,
    _create_database_if_missing,
)
from sqlalchemy import text  # noqa: E402

#: Every test in this module needs a real server, so the marker is
#: module-wide. `-m integration` then selects exactly the tests whose
#: result depends on PostgreSQL.
pytestmark = pytest.mark.integration

PROCESSED_DIR = Path("data") / "processed"


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@pytest.fixture(scope="module")
def real_corpus_db():
    """A migrated test database with the real corpus loaded once."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("NIYAMSETU_TEST_DATABASE_URL is not set; see .env.example")
    assert_is_test_url(url)

    if not PROCESSED_DIR.exists():
        pytest.skip(f"{PROCESSED_DIR} is absent; run the extraction CLI first")
    directories = [child for child in PROCESSED_DIR.iterdir() if child.is_dir()]
    if not directories:
        pytest.skip(f"no version directories under {PROCESSED_DIR}")

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

    summary = load_processed_tree(PROCESSED_DIR, url=url)
    if summary.failed:
        failures = [o.message for o in summary.outcomes if o.message]
        pytest.fail(f"real corpus failed to load: {failures}")

    yield url, summary
    engine_module.dispose_engine()


def _artefact_totals() -> Dict[str, int]:
    """What the JSON on disk says, so the database can be compared to it."""
    pages = 0
    segments = 0
    removed = 0
    for directory in sorted(
        child for child in PROCESSED_DIR.iterdir() if child.is_dir()
    ):
        extraction = json.loads(
            (directory / "extraction.json").read_text(encoding="utf-8")
        )
        pages += len(extraction["pages"])
        cleaning = json.loads(
            (directory / "page_cleaning.json").read_text(encoding="utf-8")
        )
        removed += sum(
            int(page["removed_line_count"]) for page in cleaning["pages"]
        )
        for filename in ("segments_paragraph.json", "segments_clause_aware.json"):
            payload = json.loads((directory / filename).read_text(encoding="utf-8"))
            segments += len(payload["segments"])
    return {"pages": pages, "segments": segments, "removed_lines": removed}


# --------------------------------------------------------------------------- #
# Nothing is lost between the artefacts and the database
# --------------------------------------------------------------------------- #


def test_the_whole_corpus_loads(real_corpus_db) -> None:
    url, summary = real_corpus_db
    assert summary.failed == 0
    assert summary.loaded >= 1


def test_the_database_holds_exactly_what_the_artefacts_hold(real_corpus_db) -> None:
    """A count mismatch means the loader dropped or duplicated something."""
    url, _ = real_corpus_db
    counts = repository.corpus_counts(url=url)
    expected = _artefact_totals()
    assert counts["pages"] == expected["pages"]
    assert counts["segments"] == expected["segments"]
    assert counts["removed_lines"] == expected["removed_lines"]


def test_every_version_is_present_once(real_corpus_db) -> None:
    url, summary = real_corpus_db
    counts = repository.corpus_counts(url=url)
    assert counts["versions"] == summary.loaded


def test_both_methods_are_stored_for_every_version(real_corpus_db) -> None:
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        rows = conn.execute(
            text(
                "SELECT v.public_id, s.segmentation_method, count(*) AS n "
                "FROM segments s JOIN document_versions v ON v.id = s.version_id "
                "GROUP BY v.public_id, s.segmentation_method"
            )
        ).all()
    by_version: Dict[str, Dict[str, int]] = {}
    for row in rows:
        by_version.setdefault(row.public_id, {})[row.segmentation_method] = int(row.n)
    assert by_version
    for version, methods in by_version.items():
        assert set(methods) == {"paragraph", "clause_aware"}, version
        assert all(count > 0 for count in methods.values()), version


# --------------------------------------------------------------------------- #
# Provenance, read back out of the database
# --------------------------------------------------------------------------- #


def test_every_stored_segment_has_at_least_one_page_row(real_corpus_db) -> None:
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        orphans = conn.execute(
            text(
                "SELECT count(*) FROM segments s "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM segment_pages sp WHERE sp.segment_id = s.id)"
            )
        ).scalar_one()
    assert orphans == 0


def test_no_segment_page_row_points_outside_its_version(real_corpus_db) -> None:
    """A segment attached to another version's page would be a false citation."""
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        crossed = conn.execute(
            text(
                "SELECT count(*) FROM segment_pages sp "
                "JOIN segments s ON s.id = sp.segment_id "
                "JOIN pages p    ON p.id = sp.page_id "
                "WHERE p.version_id <> s.version_id"
            )
        ).scalar_one()
    assert crossed == 0


def test_every_page_reference_is_within_the_version(real_corpus_db) -> None:
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        outside = conn.execute(
            text(
                "SELECT count(*) FROM segments s "
                "JOIN document_versions v ON v.id = s.version_id "
                "WHERE s.page_start < 1 OR s.page_end > v.page_count"
            )
        ).scalar_one()
    assert outside == 0


def test_a_segments_page_range_matches_the_pages_it_touches(real_corpus_db) -> None:
    """`page_start`/`page_end` must not drift from the `segment_pages` rows."""
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        disagreeing = conn.execute(
            text(
                "SELECT count(*) FROM ("
                "  SELECT s.id, s.page_start, s.page_end,"
                "         min(sp.page_number) AS lo, max(sp.page_number) AS hi"
                "    FROM segments s"
                "    JOIN segment_pages sp ON sp.segment_id = s.id"
                "   GROUP BY s.id, s.page_start, s.page_end"
                ") t WHERE t.lo <> t.page_start OR t.hi <> t.page_end"
            )
        ).scalar_one()
    assert disagreeing == 0


def test_page_spanning_segments_keep_every_page(real_corpus_db) -> None:
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        rows = conn.execute(
            text(
                "SELECT s.id, s.page_start, s.page_end, count(sp.page_number) AS n "
                "FROM segments s JOIN segment_pages sp ON sp.segment_id = s.id "
                "WHERE s.page_end > s.page_start "
                "GROUP BY s.id, s.page_start, s.page_end"
            )
        ).all()
    assert rows, "the corpus should contain page-spanning segments"
    for row in rows:
        expected = row.page_end - row.page_start + 1
        assert int(row.n) == expected, (
            f"segment {row.id} spans {expected} pages but has {row.n} page rows"
        )


def test_every_stored_segment_resolves_from_its_stored_offsets(
    real_corpus_db,
) -> None:
    """**The acceptance condition.**

    For every segment in the database, slice the stored page text at the stored
    character offsets and require the clause back. This is the same check
    ``evaluation/data_quality.py`` runs on the JSON, re-run against what
    PostgreSQL actually holds — so it would catch a loader that stored the
    right numbers against the wrong column.
    """
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.id, s.public_id, s.text, v.offset_basis,
                       sp.page_number, sp.char_start, sp.char_end,
                       CASE v.offset_basis
                           WHEN 'original_text' THEN p.raw_text
                           ELSE coalesce(p.normalized_text, p.raw_text)
                       END AS basis_text
                  FROM segments s
                  JOIN document_versions v ON v.id = s.version_id
                  JOIN segment_pages sp    ON sp.segment_id = s.id
                  JOIN pages p             ON p.id = sp.page_id
                 ORDER BY s.id, sp.page_number
                """
            )
        ).all()

    assert rows, "no segment rows to check"
    fragments: Dict[int, List[str]] = {}
    stored: Dict[int, str] = {}
    labels: Dict[int, str] = {}
    for row in rows:
        fragments.setdefault(row.id, []).append(
            row.basis_text[row.char_start : row.char_end]
        )
        stored[row.id] = row.text
        labels[row.id] = row.public_id

    unresolved = [
        labels[segment_id]
        for segment_id, parts in fragments.items()
        if _collapse(" ".join(parts)) != _collapse(stored[segment_id])
    ]
    assert not unresolved, (
        f"{len(unresolved)} of {len(fragments)} stored segments do not rebuild "
        f"from their offsets: {unresolved[:5]}"
    )


def test_every_version_declares_the_agreed_offset_basis(real_corpus_db) -> None:
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        bases = conn.execute(
            text("SELECT DISTINCT offset_basis FROM document_versions")
        ).scalars().all()
    assert bases == ["normalized_text"]


# --------------------------------------------------------------------------- #
# Retrieval, the way a consumer will do it
# --------------------------------------------------------------------------- #


def test_a_clause_traces_back_to_its_version_page_and_text(real_corpus_db) -> None:
    """One concrete clause, followed all the way back by hand."""
    url, _ = real_corpus_db
    listing = repository.list_documents(limit=100, url=url)
    assert listing["total"] >= 1
    document_id = listing["items"][0]["document_id"]

    clauses = repository.get_segments(
        document_id, method="clause_aware", limit=50, url=url
    )
    assert clauses["total"] > 0
    clause = clauses["items"][0]

    detail = repository.get_segment(clause["clause_id"], url=url)
    assert detail["document_id"] == document_id
    assert detail["version_id"] == clauses["version_id"]
    assert detail["pages"], "a retrieved clause must carry its page provenance"

    pages = repository.get_pages(
        document_id, version_public_id=detail["version_id"], url=url
    )
    page_text = {
        page["page_number"]: page["normalized_text"] for page in pages["items"]
    }
    rebuilt = " ".join(
        page_text[page["page_number"]][page["char_start"] : page["char_end"]]
        for page in detail["pages"]
    )
    assert _collapse(rebuilt) == _collapse(detail["text"])


def test_a_citation_is_available_for_every_clause_of_an_ok_version(
    real_corpus_db,
) -> None:
    url, _ = real_corpus_db
    listing = repository.list_documents(limit=100, url=url)
    document_id = listing["items"][0]["document_id"]
    clauses = repository.get_segments(
        document_id, method="clause_aware", limit=20, url=url
    )
    for clause in clauses["items"]:
        citation = repository.get_citation(clause["clause_id"], url=url)
        assert citation["quote"] in clause["text"]
        assert citation["page_start"] == clause["page_start"]
        assert citation["document_id"] == document_id


def test_page_filtering_narrows_the_clause_list(real_corpus_db) -> None:
    url, _ = real_corpus_db
    listing = repository.list_documents(limit=100, url=url)
    document_id = listing["items"][0]["document_id"]
    everything = repository.get_segments(
        document_id, method="clause_aware", limit=100, url=url
    )
    first_page_only = repository.get_segments(
        document_id, method="clause_aware", page_from=1, page_to=1, limit=100, url=url
    )
    assert first_page_only["total"] <= everything["total"]
    for clause in first_page_only["items"]:
        assert clause["page_start"] <= 1 <= clause["page_end"]


def test_pagination_covers_every_clause_exactly_once(real_corpus_db) -> None:
    url, _ = real_corpus_db
    listing = repository.list_documents(limit=100, url=url)
    document_id = listing["items"][0]["document_id"]
    first = repository.get_segments(
        document_id, method="clause_aware", limit=5, offset=0, url=url
    )
    second = repository.get_segments(
        document_id, method="clause_aware", limit=5, offset=5, url=url
    )
    ids = [item["clause_id"] for item in first["items"] + second["items"]]
    assert len(ids) == len(set(ids))
    assert first["total"] == second["total"]


def test_reloading_the_whole_corpus_is_idempotent(real_corpus_db) -> None:
    """Run the loader again over the same artefacts: nothing should change."""
    url, _ = real_corpus_db
    before = repository.corpus_counts(url=url)
    summary = load_processed_tree(PROCESSED_DIR, url=url)
    assert summary.loaded == 0
    assert summary.skipped >= 1
    assert repository.corpus_counts(url=url) == before


def test_the_removal_audit_trail_is_stored_with_evidence(real_corpus_db) -> None:
    """Cleaning is the only stage that deletes text; the trail must survive."""
    url, _ = real_corpus_db
    with engine_module.connection(url) as conn:
        total, blank = conn.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE btrim(evidence) = '') "
                "FROM page_removed_lines"
            )
        ).one()
    assert int(total) > 0
    assert int(blank) == 0
