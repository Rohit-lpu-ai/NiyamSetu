"""The PostgreSQL schema, the migration runner, the loader and the queries.

**These tests need a real PostgreSQL server** and skip themselves when one is
not configured, which is a visible "not run" rather than a false pass. Point
them at a throwaway database:

    NIYAMSETU_TEST_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:5432/niyamsetu_test

The database is created if absent, migrated once per session, and **truncated
between tests**. It is never the development database: ``assert_is_test_url``
refuses a name without ``test`` in it, so a mistyped variable cannot delete
real work (``CLAUDE.md`` §24 rule 10).

What is asserted here is mostly *refusal*. A schema is only as good as what it
makes impossible, so the majority of these tests try to write something wrong
and require the database to reject it. A constraint nobody has seen fire is a
constraint nobody knows works.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402

from backend.app.db import engine as engine_module  # noqa: E402
from backend.app.db.config import (  # noqa: E402
    ConfigError,
    assert_is_test_url,
    # Aliased: pytest collects any module-level name starting with 'test_'
    # as a test. Imported bare, this function gets *called* as a test and its
    # return value -- a URL containing the database password -- is printed
    # into the report. Renaming on import is what stops that.
    test_database_url as resolve_test_database_url,
)
from backend.app.db.loader import (  # noqa: E402
    STATUS_LOADED,
    STATUS_REPLACED,
    STATUS_SKIPPED_DUPLICATE,
    LoadError,
    VersionConflict,
    load_processed_tree,
    load_version_directory,
    read_version_bundle,
)
from backend.app.db.migrate import (  # noqa: E402
    MigrationError,
    discover_migrations,
    migrate,
    pending_migrations,
)
from backend.app.db import repository  # noqa: E402

# --------------------------------------------------------------------------- #
# Synthetic artefacts
# --------------------------------------------------------------------------- #
#
# Built here rather than copied from data/processed/, for the same reason the
# Phase 1 tests generate PDFs instead of committing them: a test that reads the
# live corpus starts failing when the corpus changes, and then it is measuring
# the corpus rather than the code. The real corpus is exercised separately, in
# test_db_integration.py, which skips when it is absent.

DOC_FP = "DOC-aaaaaaaaaaaaaaaa"
VER_FP = "VER-bbbbbbbbbbbbbbbb"
CHECKSUM = "c" * 64

PAGE_ONE = "1. The first clause runs to the end of this page and continues\n"
PAGE_TWO = "onto the second page, where it finishes.\n\n2. A second clause.\n"


def _segment(
    fingerprint: str,
    ordinal: int,
    textual: str,
    offsets: List[Dict[str, int]],
    *,
    segment_type: str = "clause",
    clause_number: Optional[str] = None,
    heading: Optional[str] = None,
    parent: Optional[str] = None,
    level: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "segment_id": fingerprint,
        "document_id": DOC_FP,
        "version_id": VER_FP,
        "ordinal": ordinal,
        "segment_type": segment_type,
        "clause_number": clause_number,
        "heading": heading,
        "text": textual,
        "page_start": offsets[0]["page_number"],
        "page_end": offsets[-1]["page_number"],
        "page_offsets": offsets,
        "parent_segment_id": parent,
        "level": level,
        "metadata": metadata or {},
    }


#: Maps the fixture's placeholder parent id to the ordinal it refers to, so a
#: parent reference can be rewritten to the same derived fingerprint its target
#: gets. One entry per segment that is ever used as a parent.
_PARENT_ORDINAL = {"SEG-1111111111111111": 0}


def _fingerprint(version_fingerprint: str, method: str, ordinal: int) -> str:
    """A stable per-(version, method, ordinal) segment id, as the pipeline does."""
    digest = hashlib.sha256(
        f"{version_fingerprint}|{method}|{ordinal}".encode("utf-8")
    ).hexdigest()[:16]
    return f"SEG-{digest}"


def _offset(page: int, start: int, end: int, line: int = 0) -> Dict[str, int]:
    return {
        "page_number": page,
        "char_start": start,
        "char_end": end,
        "line_index_start": line,
        "line_index_end": line,
    }


def write_bundle(
    root: Path,
    *,
    version_fingerprint: str = VER_FP,
    document_fingerprint: str = DOC_FP,
    checksum: str = CHECKSUM,
    version_label: str = "v1",
    extraction_status: str = "ok",
    title: str = "Fixture Directions, 2026",
    mutate_segments=None,
    mutate_extraction=None,
) -> Path:
    """Write a valid four-file version bundle and return its directory."""
    pages = [PAGE_ONE, PAGE_TWO]
    extraction: Dict[str, Any] = {
        "document": {
            "schema_version": "1.0.0",
            "document_id": document_fingerprint,
            "version_id": version_fingerprint,
            "filename": "fixture.pdf",
            "checksum": checksum,
            "file_size_bytes": 4096,
            "page_count": 2,
            "extraction_status": extraction_status,
            "extraction_tool": "pypdf 6.18.1",
            "extracted_at": "2026-11-12T00:00:00+00:00",
            "pdf_metadata": {},
            "source_metadata": {
                "title": title,
                "issuing_department": "Fixture Department",
                "document_type": "circular",
                "reference_no": "FIX/1/2026",
                "publication_date": "2026-11-01",
                "source_url": "https://example.gov.in/fixture.pdf",
                "retrieved_at": "2026-11-12",
                "document_group": "fixture-group",
                "version_label": version_label,
                "supersedes": None,
            },
            "normalization_options": {},
            "warnings": [],
        },
        "pages": [
            {
                "page_number": number,
                "original_text": body,
                "normalized_text": body,
                "char_count": len(body),
                "normalized_char_count": len(body),
                "word_count": len(body.split()),
                "is_empty": False,
                "has_meaningful_text": True,
            }
            for number, body in enumerate(pages, start=1)
        ],
    }
    if mutate_extraction:
        mutate_extraction(extraction)

    # A page-spanning clause plus a single-page one, so every load exercises
    # both the one-row and the two-row segment_pages path.
    spanning = _segment(
        "SEG-1111111111111111",
        0,
        PAGE_ONE[:62] + " " + PAGE_TWO[:40],
        [_offset(1, 0, 62), _offset(2, 0, 40, line=0)],
        clause_number="1.",
        metadata={"marker_style": "numeric_dotted"},
    )
    child = _segment(
        "SEG-2222222222222222",
        1,
        PAGE_TWO[42:62],
        [_offset(2, 42, 62, line=2)],
        clause_number="2.",
        parent="SEG-1111111111111111",
        level=1,
        metadata={"marker_style": "numeric_dotted", "clause_number_ambiguous": True},
    )

    segments_payload = {
        "schema_version": "1.0.0",
        "generated_at": "2026-11-12T00:00:00+00:00",
        "source_extraction": {
            "checksum": checksum,
            "extraction_tool": "pypdf 6.18.1",
            "page_count": 2,
        },
        "document_id": document_fingerprint,
        "version_id": version_fingerprint,
        "offset_basis": "normalized_text",
        "stats": {},
        "warnings": [],
    }

    cleaning = {
        "schema_version": "1.0.0",
        "document_id": document_fingerprint,
        "version_id": version_fingerprint,
        "offset_basis": "normalized_text",
        "source_text_field": "normalized_text",
        "plan": {"page_count": 2, "options": {}, "warnings": []},
        "pages": [
            {
                "page_number": 1,
                "cleaned_text": PAGE_ONE,
                "removed_line_count": 0,
                "removed_lines": [],
                "line_offset_map": [0],
            },
            {
                "page_number": 2,
                "cleaned_text": PAGE_TWO,
                "removed_line_count": 1,
                "removed_lines": [
                    {
                        "page_number": 2,
                        "line_index": 0,
                        "text": "FIXTURE RUNNING HEADER",
                        "reason": "repeated_header",
                        "evidence": "repeated at top position 0 on 2/2 pages (100%)",
                    }
                ],
                "line_offset_map": [1, 2, 3],
            },
        ],
        "totals": {
            "pages": 2,
            "removed_lines": 1,
            "pages_with_removals": 1,
            "reversible": True,
        },
    }

    directory = root / version_fingerprint
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "extraction.json").write_text(
        json.dumps(extraction), encoding="utf-8"
    )
    (directory / "page_cleaning.json").write_text(
        json.dumps(cleaning), encoding="utf-8"
    )
    for method, filename in (
        ("paragraph", "segments_paragraph.json"),
        ("clause_aware", "segments_clause_aware.json"),
    ):
        segments = [dict(spanning)] if method == "paragraph" else [
            dict(spanning), dict(child)
        ]
        for segment in segments:
            segment["segmentation_method"] = method
            # Keep the per-segment identity fields consistent with the bundle
            # this segment is actually being written into.
            segment["document_id"] = document_fingerprint
            segment["version_id"] = version_fingerprint
            if method == "paragraph":
                # The baseline carries no clause structure -- matching both the
                # real corpus (0 of 138 paragraph segments have a clause number)
                # and the schema CHECK that keeps the control condition clean.
                segment["segment_type"] = "paragraph"
                segment["clause_number"] = None
                segment["heading"] = None
                segment["parent_segment_id"] = None
                segment["level"] = 0
                segment["metadata"] = {}
            # `segments.fingerprint` is UNIQUE across the whole table, so a
            # fixture that reuses one cannot load two versions. The real
            # pipeline keys the fingerprint on (version, method, ordinal), so
            # derive it the same way here rather than hand-writing ids.
            parent_fp = segment.get("parent_segment_id")
            if parent_fp:
                segment["parent_segment_id"] = _fingerprint(
                    version_fingerprint, method, _PARENT_ORDINAL[str(parent_fp)]
                )
            segment["segment_id"] = _fingerprint(
                version_fingerprint, method, int(segment["ordinal"])
            )
        payload = dict(segments_payload)
        payload["segmentation_method"] = method
        payload["segment_count"] = len(segments)
        payload["segments"] = segments
        if mutate_segments:
            mutate_segments(payload, method)
        (directory / filename).write_text(json.dumps(payload), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

TABLES = (
    "ingestion_jobs",
    "segment_pages",
    "segments",
    "page_removed_lines",
    "pages",
    "document_versions",
    "documents",
)


@pytest.fixture(scope="session")
def db_url() -> str:
    """The migrated throwaway database, or skip the whole module."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip(
            "NIYAMSETU_TEST_DATABASE_URL is not set; database tests skipped. "
            "See .env.example."
        )
    assert_is_test_url(url)

    _create_database_if_missing(url)
    try:
        migrate(url)
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.skip(f"cannot migrate the test database: {exc}")
    yield url
    engine_module.dispose_engine()


def _create_database_if_missing(url: str) -> None:
    """CREATE DATABASE via the maintenance database, if it is not there yet."""
    from sqlalchemy import create_engine

    base, _, database = url.rpartition("/")
    maintenance = f"{base}/postgres"
    try:
        admin = create_engine(maintenance, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).first()
            if exists is None:
                conn.exec_driver_sql(f'CREATE DATABASE "{database}"')
        admin.dispose()
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.skip(f"cannot reach PostgreSQL at {maintenance.rsplit('@', 1)[-1]}: {exc}")


@pytest.fixture
def db(db_url: str) -> str:
    """A migrated, empty database for one test."""
    with engine_module.transaction(db_url) as conn:
        conn.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
        # Restart the public_id sequences too, so ids are predictable per test.
        for sequence in (
            "documents_public_id_seq",
            "versions_public_id_seq",
            "segments_public_id_seq",
        ):
            conn.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH 1"))
    return db_url


@pytest.fixture
def loaded(db: str, tmp_path: Path):
    """One version loaded from a synthetic bundle."""
    directory = write_bundle(tmp_path / "processed")
    outcome = load_version_directory(directory, url=db)
    assert outcome.status == STATUS_LOADED
    return outcome


# --------------------------------------------------------------------------- #
# Config safety - these need no server
# --------------------------------------------------------------------------- #


def test_a_non_test_database_name_is_refused() -> None:
    """The guard that stands between a typo and the development corpus."""
    with pytest.raises(ConfigError, match="must contain 'test'"):
        assert_is_test_url("postgresql+psycopg://u:p@localhost:5432/niyamsetu")


def test_a_test_database_name_is_accepted() -> None:
    assert_is_test_url("postgresql+psycopg://u:p@localhost:5432/niyamsetu_test")


# --------------------------------------------------------------------------- #
# Migration runner
# --------------------------------------------------------------------------- #


def test_migrations_are_discovered_in_order() -> None:
    names = [migration.filename for migration in discover_migrations()]
    assert names == sorted(names)
    assert names[0].startswith("0001_")


def test_an_edited_applied_migration_is_refused() -> None:
    """Otherwise the database and the repository silently disagree."""
    migrations = discover_migrations()
    applied = {migrations[0].filename: "0" * 64}
    with pytest.raises(MigrationError, match="must not"):
        pending_migrations(migrations, applied)


def test_a_badly_named_migration_is_refused(tmp_path: Path) -> None:
    (tmp_path / "2_oops.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="zero padding"):
        discover_migrations(tmp_path)


def test_migrating_twice_applies_nothing_the_second_time(db: str) -> None:
    assert migrate(db) == []


def test_every_expected_table_exists(db: str) -> None:
    with engine_module.connection(db) as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        ).all()
    present = {row.table_name for row in rows}
    assert set(TABLES) <= present
    assert "schema_migrations" in present


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


def test_a_version_loads_with_every_row_type(loaded) -> None:
    assert loaded.document_public_id == "DOC-0001"
    assert loaded.version_public_id == "VER-0001"
    assert loaded.pages == 2
    assert loaded.removed_lines == 1
    assert loaded.segments == {"paragraph": 1, "clause_aware": 2}
    # 2 offsets for the spanning segment + 1 for the child, per method.
    assert loaded.segment_pages == 2 + 2 + 1


def test_counts_match_what_was_loaded(loaded, db: str) -> None:
    counts = repository.corpus_counts(url=db)
    assert counts["documents"] == 1
    assert counts["versions"] == 1
    assert counts["pages"] == 2
    assert counts["removed_lines"] == 1
    assert counts["segments"] == 3
    assert counts["paragraph_segments"] == 1
    assert counts["clause_aware_segments"] == 2
    assert counts["page_spanning_segments"] == 2
    assert counts["ingestion_jobs"] == 1


def test_pages_round_trip_with_raw_text_preserved(loaded, db: str) -> None:
    payload = repository.get_pages("DOC-0001", text_variant="both", url=db)
    assert payload["page_count"] == 2
    assert payload["offset_basis"] == "normalized_text"
    assert payload["items"][0]["raw_text"] == PAGE_ONE
    assert payload["items"][0]["normalized_text"] == PAGE_ONE


def test_both_methods_coexist_for_one_version(loaded, db: str) -> None:
    """The reason `segmentation_method` is part of the segments key."""
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    paragraphs = repository.get_segments("DOC-0001", method="paragraph", url=db)
    assert clauses["total"] == 2
    assert paragraphs["total"] == 1
    assert clauses["items"][0]["ordinal"] == 0
    assert paragraphs["items"][0]["ordinal"] == 0


def test_every_segment_of_a_version_is_retrievable(loaded, db: str) -> None:
    found = {
        method: repository.get_segments("DOC-0001", method=method, url=db)["total"]
        for method in ("paragraph", "clause_aware")
    }
    assert sum(found.values()) == 3


def test_a_multi_page_segment_returns_both_of_its_pages(loaded, db: str) -> None:
    """The invariant the whole project rests on, read back out of the database."""
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    spanning = clauses["items"][0]
    assert spanning["page_start"] == 1 and spanning["page_end"] == 2
    assert [page["page_number"] for page in spanning["pages"]] == [1, 2]
    assert spanning["pages"][0]["char_start"] == 0


def test_parent_child_hierarchy_survives_the_round_trip(loaded, db: str) -> None:
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    child = clauses["items"][1]
    assert child["level"] == 1
    assert child["parent_clause_id"] == clauses["items"][0]["clause_id"]


def test_segment_metadata_and_ambiguity_flag_survive(loaded, db: str) -> None:
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    child = clauses["items"][1]
    assert child["marker_style"] == "numeric_dotted"
    assert child["clause_number_ambiguous"] is True
    assert child["metadata"]["marker_style"] == "numeric_dotted"


def test_a_document_lists_its_versions(loaded, db: str) -> None:
    document = repository.get_document("DOC-0001", url=db)
    assert document["title"] == "Fixture Directions, 2026"
    assert len(document["versions"]) == 1
    version = document["versions"][0]
    assert version["version_label"] == "v1"
    assert version["offset_basis"] == "normalized_text"
    assert version["segment_counts"] == {"clause_aware": 2, "paragraph": 1}


def test_the_library_listing_paginates_and_filters(loaded, db: str) -> None:
    assert repository.list_documents(url=db)["total"] == 1
    assert repository.list_documents(q="Fixture", url=db)["total"] == 1
    assert repository.list_documents(q="nothing here", url=db)["total"] == 0
    assert repository.list_documents(document_type="circular", url=db)["total"] == 1
    assert repository.list_documents(document_type="scheme", url=db)["total"] == 0


def test_the_limit_is_capped_rather_than_erroring(loaded, db: str) -> None:
    assert repository.list_documents(limit=10_000, url=db)["limit"] == repository.MAX_LIMIT


def test_a_citation_quotes_the_clause_verbatim(loaded, db: str) -> None:
    """§12.2: a citation's quote must be a substring of the clause it names."""
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    clause_id = clauses["items"][0]["clause_id"]
    citation = repository.get_citation(clause_id, url=db)
    assert citation["clause_id"] == clause_id
    assert citation["document_id"] == "DOC-0001"
    assert citation["version_id"] == "VER-0001"
    assert citation["quote"] in clauses["items"][0]["text"]
    assert citation["page_start"] == 1 and citation["page_end"] == 2


def test_a_clause_is_retrievable_on_its_own_with_provenance(loaded, db: str) -> None:
    clauses = repository.get_segments("DOC-0001", method="clause_aware", url=db)
    clause_id = clauses["items"][0]["clause_id"]
    clause = repository.get_segment(clause_id, url=db)
    assert clause["document_id"] == "DOC-0001"
    assert clause["offset_basis"] == "normalized_text"
    assert len(clause["pages"]) == 2


def test_an_unknown_document_raises_not_found(db: str) -> None:
    with pytest.raises(repository.NotFound):
        repository.get_document("DOC-9999", url=db)


def test_an_unknown_clause_raises_not_found(db: str) -> None:
    with pytest.raises(repository.NotFound):
        repository.get_segment("CLS-9999999", url=db)


def test_an_unknown_method_is_rejected(loaded, db: str) -> None:
    with pytest.raises(ValueError, match="method must be one of"):
        repository.get_segments("DOC-0001", method="sentence", url=db)


# --------------------------------------------------------------------------- #
# Idempotency and conflict
# --------------------------------------------------------------------------- #


def test_loading_the_same_version_twice_creates_no_duplicates(
    db: str, tmp_path: Path
) -> None:
    directory = write_bundle(tmp_path / "processed")
    first = load_version_directory(directory, url=db)
    second = load_version_directory(directory, url=db)

    assert first.status == STATUS_LOADED
    assert second.status == STATUS_SKIPPED_DUPLICATE
    assert "already loaded" in (second.message or "")

    counts = repository.corpus_counts(url=db)
    assert counts["versions"] == 1
    assert counts["segments"] == 3
    assert counts["pages"] == 2


def test_the_same_file_under_a_different_version_id_is_a_conflict(
    db: str, tmp_path: Path
) -> None:
    """Same bytes, different id. Refused, not silently loaded twice."""
    load_version_directory(write_bundle(tmp_path / "a"), url=db)
    other = write_bundle(
        tmp_path / "b", version_fingerprint="VER-dddddddddddddddd"
    )
    with pytest.raises(VersionConflict, match="conflicts with existing"):
        load_version_directory(other, url=db)
    assert repository.corpus_counts(url=db)["versions"] == 1


def test_a_conflicting_load_changes_nothing(db: str, tmp_path: Path) -> None:
    load_version_directory(write_bundle(tmp_path / "a"), url=db)
    before = repository.corpus_counts(url=db)
    with pytest.raises(VersionConflict):
        load_version_directory(
            write_bundle(tmp_path / "b", version_fingerprint="VER-dddddddddddddddd"),
            url=db,
        )
    assert repository.corpus_counts(url=db) == before


def test_replace_reloads_a_version_without_duplicating_it(
    db: str, tmp_path: Path
) -> None:
    directory = write_bundle(tmp_path / "processed")
    load_version_directory(directory, url=db)
    outcome = load_version_directory(directory, url=db, replace=True)
    assert outcome.status == STATUS_REPLACED
    counts = repository.corpus_counts(url=db)
    assert counts["versions"] == 1
    assert counts["segments"] == 3


def test_a_second_version_attaches_to_the_same_document(
    db: str, tmp_path: Path
) -> None:
    """D-9: the document_group fingerprint is what joins them, not the title."""
    load_version_directory(write_bundle(tmp_path / "a"), url=db)
    second = write_bundle(
        tmp_path / "b",
        version_fingerprint="VER-eeeeeeeeeeeeeeee",
        checksum="e" * 64,
        version_label="v2",
    )
    outcome = load_version_directory(second, url=db)
    assert outcome.status == STATUS_LOADED
    assert outcome.document_public_id == "DOC-0001"

    counts = repository.corpus_counts(url=db)
    assert counts["documents"] == 1
    assert counts["versions"] == 2
    assert len(repository.get_document("DOC-0001", url=db)["versions"]) == 2


def test_loading_a_tree_reports_per_version_outcomes(db: str, tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    write_bundle(processed)
    write_bundle(
        processed,
        version_fingerprint="VER-eeeeeeeeeeeeeeee",
        checksum="e" * 64,
        version_label="v2",
    )
    summary = load_processed_tree(processed, url=db)
    assert summary.loaded == 2
    assert summary.failed == 0
    assert summary.total_pages == 4
    assert "versions loaded  : 2" in summary.format()


# --------------------------------------------------------------------------- #
# Constraints - what the schema makes impossible
# --------------------------------------------------------------------------- #


def test_a_duplicate_checksum_is_rejected_by_the_database(
    loaded, db: str
) -> None:
    """The loader catches this first; the constraint is the backstop."""
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO document_versions ("
                    "  fingerprint, document_id, version_label, original_filename,"
                    "  stored_path, file_checksum, file_size_bytes, page_count,"
                    "  extraction_status, extraction_tool"
                    ") VALUES ("
                    "  'VER-ffffffffffffffff', 1, 'v-dup', 'x.pdf', 'x.pdf',"
                    f" '{CHECKSUM}', 1, 1, 'ok', 'pypdf')"
                )
            )


def test_a_version_pointing_at_no_document_is_rejected(db: str) -> None:
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO document_versions ("
                    "  fingerprint, document_id, version_label, original_filename,"
                    "  stored_path, file_checksum, file_size_bytes, page_count,"
                    "  extraction_status, extraction_tool"
                    ") VALUES ("
                    "  'VER-ffffffffffffffff', 424242, 'v1', 'x.pdf', 'x.pdf',"
                    "  'f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0',"
                    "  1, 1, 'ok', 'pypdf')"
                )
            )


def test_a_reversed_page_range_is_rejected(loaded, db: str) -> None:
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO segments ("
                    "  fingerprint, version_id, segmentation_method, ordinal,"
                    "  segment_type, text, page_start, page_end"
                    ") VALUES ("
                    "  'SEG-9999999999999999', 1, 'clause_aware', 99, 'clause',"
                    "  'backwards', 2, 1)"
                )
            )


def test_a_duplicate_ordinal_for_one_method_is_rejected(loaded, db: str) -> None:
    """`UNIQUE (version_id, segmentation_method, ordinal)` keeps order sane."""
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO segments ("
                    "  fingerprint, version_id, segmentation_method, ordinal,"
                    "  segment_type, text, page_start, page_end"
                    ") VALUES ("
                    "  'SEG-9999999999999999', 1, 'clause_aware', 0, 'clause',"
                    "  'duplicate ordinal', 1, 1)"
                )
            )


def test_a_duplicate_page_number_within_a_version_is_rejected(
    loaded, db: str
) -> None:
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO pages ("
                    "  version_id, page_number, raw_text, char_count, word_count,"
                    "  is_empty, has_meaningful_text"
                    ") VALUES (1, 1, 'again', 5, 1, false, true)"
                )
            )


def test_an_unknown_segmentation_method_is_rejected(loaded, db: str) -> None:
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO segments ("
                    "  fingerprint, version_id, segmentation_method, ordinal,"
                    "  segment_type, text, page_start, page_end"
                    ") VALUES ("
                    "  'SEG-9999999999999999', 1, 'sentence', 99, 'clause',"
                    "  'wrong method', 1, 1)"
                )
            )


def test_an_invalid_offset_basis_is_rejected(loaded, db: str) -> None:
    """D-11's vocabulary, enforced rather than documented."""
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "UPDATE document_versions SET offset_basis = 'cleaned_text' "
                    "WHERE id = 1"
                )
            )


def test_a_segment_with_no_page_row_fails_at_commit(loaded, db: str) -> None:
    """The deferred provenance trigger: the project's load-bearing guarantee.

    A segment is inserted with no `segment_pages` row. Nothing complains until
    COMMIT, which is exactly when the answer is knowable, and then the whole
    transaction fails.
    """
    with pytest.raises(DBAPIError, match="no segment_pages row"):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO segments ("
                    "  fingerprint, version_id, segmentation_method, ordinal,"
                    "  segment_type, text, page_start, page_end"
                    ") VALUES ("
                    "  'SEG-9999999999999999', 1, 'clause_aware', 99, 'clause',"
                    "  'orphan with no provenance', 1, 1)"
                )
            )
    assert repository.corpus_counts(url=db)["segments"] == 3


def test_a_page_row_disagreeing_with_its_page_is_rejected(loaded, db: str) -> None:
    """`segment_pages.page_number` must equal the page it references."""
    with pytest.raises(DBAPIError, match="does not match"):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO segment_pages ("
                    "  segment_id, page_id, page_number, char_start, char_end,"
                    "  line_index_start, line_index_end"
                    ") VALUES (2, 1, 7, 0, 5, 0, 0)"
                )
            )


def test_an_offset_past_the_end_of_the_page_is_rejected(loaded, db: str) -> None:
    with pytest.raises(DBAPIError, match="past the end"):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "UPDATE segment_pages SET char_end = 99999 "
                    "WHERE segment_id = 2 AND page_number = 2"
                )
            )


def test_raw_text_cannot_be_overwritten(loaded, db: str) -> None:
    """`CLAUDE.md` §10.2, enforced by a trigger rather than by convention."""
    with pytest.raises(DBAPIError, match="immutable"):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text("UPDATE pages SET raw_text = 'rewritten' WHERE id = 1")
            )
    payload = repository.get_pages("DOC-0001", text_variant="raw", url=db)
    assert payload["items"][0]["raw_text"] == PAGE_ONE


def test_normalized_text_can_still_be_updated(loaded, db: str) -> None:
    """The immutability trigger must not freeze the whole row."""
    with engine_module.transaction(db) as conn:
        conn.execute(
            text("UPDATE pages SET normalized_text = 'renormalised' WHERE id = 1")
        )
    payload = repository.get_pages("DOC-0001", url=db)
    assert payload["items"][0]["normalized_text"] == "renormalised"


def test_a_removed_line_without_evidence_is_rejected(loaded, db: str) -> None:
    """A line deleted without a stated reason is indistinguishable from a bug."""
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO page_removed_lines "
                    "  (page_id, line_index, line_text, reason, evidence) "
                    "VALUES (1, 5, 'some header', 'repeated_header', '   ')"
                )
            )


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #


def test_a_failed_transaction_leaves_nothing_behind(db: str) -> None:
    """"One version per transaction" (`CLAUDE.md` §10.2), demonstrated."""
    with pytest.raises(IntegrityError):
        with engine_module.transaction(db) as conn:
            conn.execute(
                text(
                    "INSERT INTO documents (fingerprint, title) "
                    "VALUES ('DOC-1234567890abcdef', 'Half-written')"
                )
            )
            # Violates the fingerprint shape check, so the whole block aborts.
            conn.execute(
                text(
                    "INSERT INTO documents (fingerprint, title) "
                    "VALUES ('not-a-fingerprint', 'Second')"
                )
            )
    assert repository.corpus_counts(url=db)["documents"] == 0


def test_a_load_that_fails_midway_rolls_back_the_whole_version(
    db: str, tmp_path: Path
) -> None:
    """The failure is injected at the *last* segment, so pages are already in."""

    def break_last_segment(payload, method):
        if method == "clause_aware":
            payload["segments"][-1]["page_offsets"] = [
                {
                    "page_number": 9,  # no such page
                    "char_start": 0,
                    "char_end": 1,
                    "line_index_start": 0,
                    "line_index_end": 0,
                }
            ]
            payload["segments"][-1]["page_start"] = 9
            payload["segments"][-1]["page_end"] = 9

    directory = write_bundle(
        tmp_path / "processed", mutate_segments=break_last_segment
    )
    with pytest.raises(LoadError, match="was not extracted"):
        load_version_directory(directory, url=db)

    counts = repository.corpus_counts(url=db)
    assert counts["documents"] == 0
    assert counts["versions"] == 0
    assert counts["pages"] == 0
    assert counts["segments"] == 0
    assert counts["segment_pages"] == 0
    assert counts["removed_lines"] == 0


def test_a_broken_version_does_not_stop_the_rest_of_the_tree(
    db: str, tmp_path: Path
) -> None:
    processed = tmp_path / "processed"
    write_bundle(processed)
    broken = write_bundle(
        processed,
        version_fingerprint="VER-eeeeeeeeeeeeeeee",
        checksum="e" * 64,
        version_label="v2",
    )
    (broken / "segments_clause_aware.json").write_text("{ not json", encoding="utf-8")

    summary = load_processed_tree(processed, url=db)
    assert summary.loaded == 1
    assert summary.failed == 1
    assert repository.corpus_counts(url=db)["versions"] == 1


# --------------------------------------------------------------------------- #
# Artefact validation, before anything is written
# --------------------------------------------------------------------------- #


def test_a_segment_file_declaring_the_wrong_basis_is_refused(tmp_path: Path) -> None:
    """Loading raw-text offsets into a normalised-text column would be silent."""

    def wrong_basis(payload, method):
        payload["offset_basis"] = "original_text"

    directory = write_bundle(tmp_path / "processed", mutate_segments=wrong_basis)
    with pytest.raises(LoadError, match="D-11"):
        read_version_bundle(directory)


def test_a_directory_name_not_matching_its_version_is_refused(
    tmp_path: Path,
) -> None:
    directory = write_bundle(tmp_path / "processed")
    renamed = directory.parent / "VER-0000000000000000"
    directory.rename(renamed)
    with pytest.raises(LoadError, match="does not match"):
        read_version_bundle(renamed)


def test_a_missing_artefact_is_refused(tmp_path: Path) -> None:
    directory = write_bundle(tmp_path / "processed")
    (directory / "page_cleaning.json").unlink()
    with pytest.raises(LoadError, match="required artefact missing"):
        read_version_bundle(directory)


def test_an_empty_title_is_refused(db: str, tmp_path: Path) -> None:
    directory = write_bundle(tmp_path / "processed", title="   ")
    with pytest.raises(LoadError, match="title is empty"):
        load_version_directory(directory, url=db)
