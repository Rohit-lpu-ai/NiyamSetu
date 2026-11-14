"""Load the pipeline's JSON artefacts into PostgreSQL.

The JSON under ``data/processed/<VERSION_ID>/`` is the source of truth for this
import: ``extraction.json`` (pages), ``page_cleaning.json`` (the removal audit
trail), and ``segments_paragraph.json`` / ``segments_clause_aware.json``
(both segmentation methods). Nothing here re-reads a PDF, so a load is
reproducible and cannot disagree with the artefacts the rest of the team reads.

Three properties this module is built around:

**One version per transaction.** Every row for a version — document, version,
pages, removed lines, both methods' segments, and every ``segment_pages`` row —
goes in inside a single transaction. A failure anywhere rolls the whole thing
back, so a half-ingested version is not merely unlikely, it is unrepresentable
(``CLAUDE.md`` §10.2). The schema's deferred provenance trigger fires at that
COMMIT, which is the moment "no segment without a page" is actually proved.

**Idempotency, by fingerprint and checksum.** Loading the same version twice
must not duplicate it. The version's fingerprint (``VER-`` + the first 16 hex
of the file's SHA-256) and the full checksum are both unique in the schema, so
a repeat load is *detected* and skipped rather than relying on the loader
remembering.

**Conflicts are reported, never overwritten.** If a version already exists with
a different checksum, or a document fingerprint already carries a different
title, the load stops and says so. Replacing existing data requires an explicit
``replace=True`` (``CLAUDE.md`` §24 rule 10).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.config import database_url
from backend.app.db.engine import transaction

__all__ = [
    "LoadError",
    "VersionConflict",
    "LoadOutcome",
    "LoadSummary",
    "load_version_directory",
    "load_processed_tree",
    "link_supersedes",
    "main",
]

EXTRACTION_FILENAME = "extraction.json"
CLEANING_FILENAME = "page_cleaning.json"
SEGMENT_FILENAMES = {
    "paragraph": "segments_paragraph.json",
    "clause_aware": "segments_clause_aware.json",
}

DEFAULT_PROCESSED_DIR = Path("data") / "processed"
DEFAULT_RAW_DIR = Path("data") / "raw"

#: Status values for one version's load attempt.
STATUS_LOADED = "loaded"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_REPLACED = "replaced"
STATUS_FAILED = "failed"


class LoadError(RuntimeError):
    """An artefact is missing or malformed, so nothing was loaded."""

    code = "LOAD_ERROR"


class VersionConflict(LoadError):
    """Existing data disagrees with what is being loaded. Nothing was changed."""

    code = "VERSION_CONFLICT"


@dataclass
class LoadOutcome:
    """What happened to one version."""

    version_fingerprint: str
    status: str
    document_public_id: Optional[str] = None
    version_public_id: Optional[str] = None
    pages: int = 0
    removed_lines: int = 0
    segments: Dict[str, int] = field(default_factory=dict)
    segment_pages: int = 0
    message: Optional[str] = None

    @property
    def total_segments(self) -> int:
        return sum(self.segments.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_fingerprint": self.version_fingerprint,
            "status": self.status,
            "document_public_id": self.document_public_id,
            "version_public_id": self.version_public_id,
            "pages": self.pages,
            "removed_lines": self.removed_lines,
            "segments": dict(self.segments),
            "segment_pages": self.segment_pages,
            "message": self.message,
        }


@dataclass
class LoadSummary:
    """Every version's outcome from one run."""

    outcomes: List[LoadOutcome] = field(default_factory=list)
    #: What :func:`link_supersedes` did at the end of the run, or ``None`` when
    #: it was not called (a single-directory load does not run it, because one
    #: version cannot establish a chain on its own).
    supersedes_links: Optional[Dict[str, Any]] = None

    def add(self, outcome: LoadOutcome) -> None:
        self.outcomes.append(outcome)

    def _count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def loaded(self) -> int:
        return self._count(STATUS_LOADED) + self._count(STATUS_REPLACED)

    @property
    def skipped(self) -> int:
        return self._count(STATUS_SKIPPED_DUPLICATE)

    @property
    def failed(self) -> int:
        return self._count(STATUS_FAILED)

    @property
    def total_pages(self) -> int:
        return sum(o.pages for o in self.outcomes)

    @property
    def total_segments(self) -> int:
        return sum(o.total_segments for o in self.outcomes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "versions_loaded": self.loaded,
            "versions_skipped": self.skipped,
            "versions_failed": self.failed,
            "pages_loaded": self.total_pages,
            "segments_loaded": self.total_segments,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }

    def format(self) -> str:
        lines = [
            "NiyamSetu PostgreSQL load summary",
            f"versions loaded  : {self.loaded}",
            f"versions skipped : {self.skipped} (already present)",
            f"versions failed  : {self.failed}",
            f"pages            : {self.total_pages}",
            f"segments         : {self.total_segments}",
            "",
        ]
        for outcome in self.outcomes:
            segs = ", ".join(
                f"{method}={count}" for method, count in sorted(outcome.segments.items())
            )
            lines.append(
                f"  [{outcome.status}] {outcome.version_fingerprint}"
                f" {outcome.version_public_id or ''}"
            )
            if outcome.status in (STATUS_LOADED, STATUS_REPLACED):
                lines.append(
                    f"      pages={outcome.pages} removed_lines={outcome.removed_lines} "
                    f"{segs} segment_pages={outcome.segment_pages}"
                )
            if outcome.message:
                lines.append(f"      {outcome.message}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reading the artefacts
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise LoadError(f"required artefact missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoadError(f"{path} is not valid JSON: {exc}") from exc


@dataclass
class VersionBundle:
    """The four artefacts for one version, already parsed and cross-checked."""

    directory: Path
    extraction: Dict[str, Any]
    cleaning: Dict[str, Any]
    segments: Dict[str, Dict[str, Any]]

    @property
    def document(self) -> Dict[str, Any]:
        return self.extraction["document"]

    @property
    def version_fingerprint(self) -> str:
        return str(self.document["version_id"])

    @property
    def document_fingerprint(self) -> str:
        return str(self.document["document_id"])


def read_version_bundle(directory: Path) -> VersionBundle:
    """Read and cross-check one version directory.

    The cross-checks are here rather than in the database because a mismatch
    between two artefact files is a *pipeline* bug, and the error should name
    the files rather than surface later as a constraint violation.
    """
    extraction = _read_json(directory / EXTRACTION_FILENAME)
    cleaning = _read_json(directory / CLEANING_FILENAME)
    segments: Dict[str, Dict[str, Any]] = {}
    for method, filename in SEGMENT_FILENAMES.items():
        segments[method] = _read_json(directory / filename)

    document = extraction.get("document")
    if not isinstance(document, dict):
        raise LoadError(f"{directory / EXTRACTION_FILENAME} has no 'document' object")

    version_fingerprint = str(document.get("version_id", ""))
    if directory.name != version_fingerprint:
        raise LoadError(
            f"directory name {directory.name!r} does not match the version_id "
            f"{version_fingerprint!r} inside {EXTRACTION_FILENAME}"
        )

    for method, payload in segments.items():
        if str(payload.get("version_id")) != version_fingerprint:
            raise LoadError(
                f"{SEGMENT_FILENAMES[method]} is for version "
                f"{payload.get('version_id')!r} but sits in {directory.name}"
            )
        if str(payload.get("segmentation_method")) != method:
            raise LoadError(
                f"{SEGMENT_FILENAMES[method]} declares method "
                f"{payload.get('segmentation_method')!r}"
            )
        basis = str(payload.get("offset_basis"))
        if basis != "normalized_text":
            raise LoadError(
                f"{SEGMENT_FILENAMES[method]} declares offset_basis {basis!r}; "
                f"the agreed contract is 'normalized_text' (D-11). Loading it "
                f"would store offsets the schema would then misinterpret."
            )
    if str(cleaning.get("version_id")) != version_fingerprint:
        raise LoadError(
            f"{CLEANING_FILENAME} is for version {cleaning.get('version_id')!r}"
        )

    return VersionBundle(
        directory=directory,
        extraction=extraction,
        cleaning=cleaning,
        segments=segments,
    )


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _existing_version(
    conn: Connection, fingerprint: str, checksum: str
) -> Optional[Any]:
    """Find an already-loaded version, by fingerprint or by file checksum.

    Both are checked because they answer different questions: the fingerprint
    says "this version id is present", the checksum says "these exact bytes are
    present under some other id". Either is a duplicate.
    """
    return conn.execute(
        text(
            "SELECT id, public_id, fingerprint, file_checksum "
            "FROM document_versions "
            "WHERE fingerprint = :fingerprint OR file_checksum = :checksum"
        ),
        {"fingerprint": fingerprint, "checksum": checksum},
    ).first()


def _upsert_document(conn: Connection, bundle: VersionBundle) -> Tuple[int, str]:
    """Find or create the logical document. Returns ``(id, public_id)``.

    Matching is on the fingerprint, which the pipeline derives from the
    manifest's ``document_group`` — a value a human wrote down. That is exactly
    D-9: a new upload joins an existing document because someone said so, never
    because two titles looked similar.
    """
    source = bundle.document.get("source_metadata") or {}
    fingerprint = bundle.document_fingerprint
    title = str(source.get("title") or "").strip()
    if not title:
        raise LoadError(f"{bundle.directory}: source_metadata.title is empty")

    existing = conn.execute(
        text(
            "SELECT id, public_id, title FROM documents WHERE fingerprint = :fp"
        ),
        {"fp": fingerprint},
    ).first()
    if existing is not None:
        return existing.id, existing.public_id

    row = conn.execute(
        text(
            "INSERT INTO documents "
            "  (fingerprint, title, department, document_type, reference_no) "
            "VALUES (:fingerprint, :title, :department, :document_type, :reference_no) "
            "RETURNING id, public_id"
        ),
        {
            "fingerprint": fingerprint,
            "title": title,
            "department": source.get("issuing_department"),
            "document_type": source.get("document_type"),
            "reference_no": source.get("reference_no"),
        },
    ).one()
    return row.id, row.public_id


def _insert_version(
    conn: Connection, bundle: VersionBundle, document_id: int, raw_dir: Path
) -> Tuple[int, str]:
    document = bundle.document
    source = document.get("source_metadata") or {}
    filename = str(document["filename"])

    row = conn.execute(
        text(
            "INSERT INTO document_versions ("
            "  fingerprint, document_id, version_label, publication_date,"
            "  source_url, retrieved_at, original_filename, stored_path,"
            "  file_checksum, file_size_bytes, page_count, extraction_status,"
            "  extraction_tool, offset_basis, processing_warnings"
            ") VALUES ("
            "  :fingerprint, :document_id, :version_label, :publication_date,"
            "  :source_url, :retrieved_at, :original_filename, :stored_path,"
            "  :file_checksum, :file_size_bytes, :page_count, :extraction_status,"
            "  :extraction_tool, :offset_basis, CAST(:processing_warnings AS JSONB)"
            ") RETURNING id, public_id"
        ),
        {
            "fingerprint": bundle.version_fingerprint,
            "document_id": document_id,
            # Falls back to the fingerprint rather than inventing 'v1': a
            # version label is human-assigned (CLAUDE.md §11.1) and a guessed
            # one would collide the moment a real second version arrived.
            "version_label": source.get("version_label") or bundle.version_fingerprint,
            "publication_date": source.get("publication_date"),
            "source_url": source.get("source_url"),
            "retrieved_at": source.get("retrieved_at"),
            "original_filename": filename,
            "stored_path": str(raw_dir / filename),
            "file_checksum": str(document["checksum"]),
            "file_size_bytes": int(document["file_size_bytes"]),
            "page_count": int(document["page_count"]),
            "extraction_status": str(document["extraction_status"]),
            "extraction_tool": str(document["extraction_tool"]),
            "offset_basis": str(
                bundle.segments["clause_aware"].get("offset_basis", "normalized_text")
            ),
            "processing_warnings": json.dumps(document.get("warnings") or []),
        },
    ).one()
    return row.id, row.public_id


def _insert_pages(
    conn: Connection, bundle: VersionBundle, version_id: int
) -> Tuple[Dict[int, int], int]:
    """Insert pages and their removal audit trail.

    Returns ``({page_number: page_id}, removed_line_count)``. The map is what
    ``segment_pages`` needs: the schema references ``pages.id``, not a page
    number, so a segment cannot be attached to a page of a different version
    even by accident.
    """
    cleaned_by_page = {
        int(page["page_number"]): page
        for page in bundle.cleaning.get("pages", [])
    }

    page_ids: Dict[int, int] = {}
    removed_total = 0

    for page in bundle.extraction["pages"]:
        number = int(page["page_number"])
        cleaned = cleaned_by_page.get(number)
        row = conn.execute(
            text(
                "INSERT INTO pages ("
                "  version_id, page_number, raw_text, normalized_text,"
                "  cleaned_text, char_count, word_count, is_empty,"
                "  has_meaningful_text"
                ") VALUES ("
                "  :version_id, :page_number, :raw_text, :normalized_text,"
                "  :cleaned_text, :char_count, :word_count, :is_empty,"
                "  :has_meaningful_text"
                ") RETURNING id"
            ),
            {
                "version_id": version_id,
                "page_number": number,
                # raw_text is the extractor's verbatim output. The schema makes
                # it immutable with a trigger; this is the only place it is
                # ever written.
                "raw_text": page["original_text"],
                "normalized_text": page.get("normalized_text"),
                "cleaned_text": (cleaned or {}).get("cleaned_text"),
                "char_count": int(page["char_count"]),
                "word_count": int(page["word_count"]),
                "is_empty": bool(page["is_empty"]),
                "has_meaningful_text": bool(page["has_meaningful_text"]),
            },
        ).one()
        page_ids[number] = row.id

        for line in (cleaned or {}).get("removed_lines", []):
            conn.execute(
                text(
                    "INSERT INTO page_removed_lines "
                    "  (page_id, line_index, line_text, reason, evidence) "
                    "VALUES (:page_id, :line_index, :line_text, :reason, :evidence)"
                ),
                {
                    "page_id": row.id,
                    "line_index": int(line["line_index"]),
                    "line_text": line["text"],
                    "reason": line["reason"],
                    "evidence": line["evidence"],
                },
            )
            removed_total += 1

    return page_ids, removed_total


def _insert_segments(
    conn: Connection,
    bundle: VersionBundle,
    version_id: int,
    page_ids: Dict[int, int],
) -> Tuple[Dict[str, int], int]:
    """Insert both methods' segments and every ``segment_pages`` row.

    Segments are inserted in ordinal order so a parent is always present before
    the child that references it. ``parent_segment_id`` in the JSON is a
    fingerprint; the schema's ``parent_id`` is a row id, so the map built here
    translates between them. A parent fingerprint that does not resolve raises
    rather than being written as NULL — a silently orphaned clause would lose
    the hierarchy the segmenter measured.
    """
    counts: Dict[str, int] = {}
    segment_pages_total = 0

    for method, payload in bundle.segments.items():
        fingerprint_to_id: Dict[str, int] = {}
        segments = sorted(
            payload.get("segments", []), key=lambda item: int(item["ordinal"])
        )
        for segment in segments:
            metadata = segment.get("metadata") or {}
            parent_fingerprint = segment.get("parent_segment_id")
            parent_id: Optional[int] = None
            if parent_fingerprint:
                parent_id = fingerprint_to_id.get(str(parent_fingerprint))
                if parent_id is None:
                    raise LoadError(
                        f"{bundle.directory.name}/{method}: segment "
                        f"{segment.get('segment_id')} names parent "
                        f"{parent_fingerprint} which has not been inserted; "
                        f"the file's ordinal order does not put parents first"
                    )

            row = conn.execute(
                text(
                    "INSERT INTO segments ("
                    "  fingerprint, version_id, segmentation_method, ordinal,"
                    "  segment_type, clause_number, heading, text,"
                    "  page_start, page_end, parent_id, level, marker_style,"
                    "  clause_number_ambiguous, metadata"
                    ") VALUES ("
                    "  :fingerprint, :version_id, :segmentation_method, :ordinal,"
                    "  :segment_type, :clause_number, :heading, :text,"
                    "  :page_start, :page_end, :parent_id, :level, :marker_style,"
                    "  :clause_number_ambiguous, CAST(:metadata AS JSONB)"
                    ") RETURNING id"
                ),
                {
                    "fingerprint": str(segment["segment_id"]),
                    "version_id": version_id,
                    "segmentation_method": method,
                    "ordinal": int(segment["ordinal"]),
                    "segment_type": str(segment["segment_type"]),
                    "clause_number": segment.get("clause_number"),
                    "heading": segment.get("heading"),
                    "text": segment["text"],
                    "page_start": int(segment["page_start"]),
                    "page_end": int(segment["page_end"]),
                    "parent_id": parent_id,
                    "level": int(segment.get("level") or 0),
                    "marker_style": metadata.get("marker_style"),
                    "clause_number_ambiguous": bool(
                        metadata.get("clause_number_ambiguous", False)
                    ),
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                },
            ).one()
            fingerprint_to_id[str(segment["segment_id"])] = row.id

            offsets = segment.get("page_offsets") or []
            if not offsets:
                # Unreachable via the pipeline (Segment.__post_init__ forbids
                # it) and still checked here, because this loader also reads
                # files that could have been hand-edited.
                raise LoadError(
                    f"{bundle.directory.name}/{method}: segment "
                    f"{segment.get('segment_id')} has no page_offsets"
                )
            for offset in offsets:
                page_number = int(offset["page_number"])
                page_id = page_ids.get(page_number)
                if page_id is None:
                    raise LoadError(
                        f"{bundle.directory.name}/{method}: segment "
                        f"{segment.get('segment_id')} references page "
                        f"{page_number}, which was not extracted"
                    )
                conn.execute(
                    text(
                        "INSERT INTO segment_pages ("
                        "  segment_id, page_id, page_number, char_start,"
                        "  char_end, line_index_start, line_index_end"
                        ") VALUES ("
                        "  :segment_id, :page_id, :page_number, :char_start,"
                        "  :char_end, :line_index_start, :line_index_end"
                        ")"
                    ),
                    {
                        "segment_id": row.id,
                        "page_id": page_id,
                        "page_number": page_number,
                        "char_start": int(offset["char_start"]),
                        "char_end": int(offset["char_end"]),
                        "line_index_start": int(offset["line_index_start"]),
                        "line_index_end": int(offset["line_index_end"]),
                    },
                )
                segment_pages_total += 1

        counts[method] = len(segments)

    return counts, segment_pages_total


def _delete_version(conn: Connection, version_id: int) -> None:
    """Remove a version and everything cascading from it.

    Only reachable through ``replace=True``. ``pages`` and ``segments`` cascade
    from ``document_versions``, and ``segment_pages`` / ``page_removed_lines``
    cascade from those, so this one statement is complete. The ``documents``
    row is deliberately left alone: it is the logical policy identity and may
    have other versions (``ON DELETE RESTRICT`` on the FK says so).
    """
    conn.execute(
        text("DELETE FROM document_versions WHERE id = :id"), {"id": version_id}
    )


def load_version_directory(
    directory: Path,
    *,
    url: Optional[str] = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
    replace: bool = False,
) -> LoadOutcome:
    """Load one version directory inside a single transaction."""
    bundle = read_version_bundle(directory)
    fingerprint = bundle.version_fingerprint
    checksum = str(bundle.document["checksum"])

    try:
        with transaction(url) as conn:
            existing = _existing_version(conn, fingerprint, checksum)
            if existing is not None:
                if not replace:
                    same = (
                        existing.fingerprint == fingerprint
                        and existing.file_checksum == checksum
                    )
                    if same:
                        # Report the document too, not just the version. A
                        # caller handed a bare duplicate response would
                        # otherwise have to search for the document the
                        # version it just uploaded belongs to.
                        owning = conn.execute(
                            text(
                                "SELECT d.public_id FROM documents d "
                                "JOIN document_versions v ON v.document_id = d.id "
                                "WHERE v.id = :vid"
                            ),
                            {"vid": existing.id},
                        ).scalar_one_or_none()
                        return LoadOutcome(
                            version_fingerprint=fingerprint,
                            status=STATUS_SKIPPED_DUPLICATE,
                            document_public_id=owning,
                            version_public_id=existing.public_id,
                            pages=int(bundle.document["page_count"]),
                            segments={
                                method: len(payload.get("segments", []))
                                for method, payload in bundle.segments.items()
                            },
                            message=(
                                f"already loaded as {existing.public_id}; "
                                f"nothing changed"
                            ),
                        )
                    # Same id, different bytes (or vice versa). Not a duplicate
                    # -- a disagreement. Refusing is the point: overwriting
                    # here would destroy whichever one was right.
                    raise VersionConflict(
                        f"{fingerprint} conflicts with existing "
                        f"{existing.public_id}: stored fingerprint "
                        f"{existing.fingerprint} / checksum "
                        f"{existing.file_checksum[:12]}..., incoming "
                        f"{fingerprint} / {checksum[:12]}.... Nothing was "
                        f"changed. Pass replace=True only if the incoming "
                        f"version is known to be correct."
                    )
                _delete_version(conn, existing.id)

            document_id, document_public_id = _upsert_document(conn, bundle)
            version_id, version_public_id = _insert_version(
                conn, bundle, document_id, raw_dir
            )
            page_ids, removed_lines = _insert_pages(conn, bundle, version_id)
            counts, segment_pages = _insert_segments(
                conn, bundle, version_id, page_ids
            )

            conn.execute(
                text(
                    "INSERT INTO ingestion_jobs "
                    "  (version_id, status, finished_at, stats) "
                    "VALUES (:version_id, 'succeeded', now(), CAST(:stats AS JSONB))"
                ),
                {
                    "version_id": version_id,
                    "stats": json.dumps(
                        {
                            "source": str(directory),
                            "pages": len(page_ids),
                            "removed_lines": removed_lines,
                            "segments": counts,
                            "segment_pages": segment_pages,
                        }
                    ),
                },
            )

            return LoadOutcome(
                version_fingerprint=fingerprint,
                status=STATUS_REPLACED if existing is not None else STATUS_LOADED,
                document_public_id=document_public_id,
                version_public_id=version_public_id,
                pages=len(page_ids),
                removed_lines=removed_lines,
                segments=counts,
                segment_pages=segment_pages,
            )
    except SQLAlchemyError as exc:
        # The transaction has already rolled back by the time this runs, so
        # there is nothing to clean up -- which is the whole point of putting
        # the version inside one.
        raise LoadError(
            f"{directory.name} failed to load and was rolled back: {exc}"
        ) from exc


def load_processed_tree(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    *,
    url: Optional[str] = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
    replace: bool = False,
    stop_on_error: bool = False,
) -> LoadSummary:
    """Load every version directory under ``processed_dir``.

    Each version is independent: one failing does not roll back a previous
    success, because each had its own transaction. By default the run continues
    and records the failure, so one broken artefact does not block the corpus.
    """
    summary = LoadSummary()
    if not processed_dir.exists():
        raise LoadError(f"processed directory not found: {processed_dir}")

    directories = sorted(
        child for child in processed_dir.iterdir() if child.is_dir()
    )
    if not directories:
        raise LoadError(f"no version directories under {processed_dir}")

    for directory in directories:
        try:
            summary.add(
                load_version_directory(
                    directory, url=url, raw_dir=raw_dir, replace=replace
                )
            )
        except LoadError as exc:
            summary.add(
                LoadOutcome(
                    version_fingerprint=directory.name,
                    status=STATUS_FAILED,
                    message=f"[{exc.code}] {exc}",
                )
            )
            if stop_on_error:
                break

    # Version order is resolved after every version is in, not during the
    # load: a manifest row can name a predecessor whose directory sorts later,
    # and a one-pass loader would have to either reject it or guess.
    summary.supersedes_links = link_supersedes(
        declarations_from_tree(processed_dir), url=url
    )
    return summary


def declarations_from_tree(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
) -> Dict[str, str]:
    """``{version_fingerprint: declared predecessor version_label}``.

    Read straight out of the ``extraction.json`` files, because that is where
    the value already is: ``data/manifest.csv`` carries a ``supersedes``
    column holding **the ``version_label`` this version replaces**
    (``data/README.md`` §4), the extraction CLI copies it into
    ``source_metadata``, and nothing had ever read it back out.

    Doing it this way means no new column and no migration -- the declaration
    lives in the artefact, exactly as the publication date and the source URL
    already do, and only the *resolved* link lands in the database.
    """
    out: Dict[str, str] = {}
    if not processed_dir.exists():
        return out
    for directory in sorted(child for child in processed_dir.iterdir() if child.is_dir()):
        extraction = directory / EXTRACTION_FILENAME
        if not extraction.exists():
            continue
        try:
            payload = _read_json(extraction)
        except LoadError:
            continue
        document = payload.get("document") or {}
        source = document.get("source_metadata") or {}
        declared = source.get("supersedes")
        fingerprint = document.get("version_id") or directory.name
        if declared and str(declared).strip():
            out[str(fingerprint)] = str(declared).strip()
    return out


def link_supersedes(
    declarations: Dict[str, str], *, url: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve declared predecessors into ``supersedes_version_id``.

    ``declarations`` maps a version fingerprint to the ``version_label`` that
    version's manifest row says it replaces, as
    :func:`declarations_from_tree` reads it out of the artefacts.

    Why this exists at all: until now nothing wrote
    ``document_versions.supersedes_version_id``, so the one statement in the
    database about version order that is *asserted by a human rather than
    inferred* was being dropped at the last step of ingestion.

    That matters specifically for Compare. Without the column, "which of these
    two versions is the newer one" can only be guessed from publication date --
    which ``CLAUDE.md`` §11.1 says explicitly is not a reliable version order,
    and which is nullable besides. With it,
    ``GET /api/compare/options`` can mark a pair ``supersedes_declared`` and
    the direction of a diff becomes a fact rather than an assumption.

    Four rules, enforced here rather than left to the schema:

    * **Within one document only.** A label is looked up among the *same
      document's* versions, so a chain can never cross documents -- which the
      schema also has a trigger against, but a refusal reported by name is
      more useful than a constraint violation.
    * **No self-reference**, and **no cycle**: a link that would close one is
      refused and reported.
    * **Nothing is guessed.** An unresolvable label is reported as unresolved
      and no link is written. Title similarity is never used (D-9).
    * **Idempotent.** A link already correct is left alone, so this is safe to
      call after every load.
    """
    linked: List[str] = []
    refused: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []

    if not declarations:
        return {
            "linked": linked,
            "linked_count": 0,
            "unresolved": unresolved,
            "refused": refused,
        }

    with transaction(url) as conn:
        rows = conn.execute(
            text(
                "SELECT v.id, v.public_id, v.fingerprint, v.document_id,"
                "       v.version_label, v.supersedes_version_id"
                "  FROM document_versions v"
                " ORDER BY v.id"
            )
        ).all()
        by_label: Dict[Tuple[int, str], int] = {
            (int(row.document_id), str(row.version_label)): int(row.id)
            for row in rows
        }

        for row in rows:
            declared = declarations.get(str(row.fingerprint))
            if not declared:
                continue
            target = by_label.get((int(row.document_id), declared))
            if target is None:
                unresolved.append(
                    {
                        "version": row.public_id,
                        "supersedes": declared,
                        "reason": (
                            "no version carries that version_label within the "
                            "same document; it may not be ingested yet, or the "
                            "manifest's supersedes value may not match any "
                            "version_label"
                        ),
                    }
                )
                continue
            if target == int(row.id):
                refused.append(
                    {
                        "version": row.public_id,
                        "supersedes": declared,
                        "reason": "a version cannot supersede itself",
                    }
                )
                continue
            if _would_cycle(conn, int(row.id), target):
                refused.append(
                    {
                        "version": row.public_id,
                        "supersedes": declared,
                        "reason": "the link would close a supersedes cycle",
                    }
                )
                continue
            if row.supersedes_version_id == target:
                continue
            conn.execute(
                text(
                    "UPDATE document_versions SET supersedes_version_id = :target"
                    " WHERE id = :vid"
                ),
                {"target": target, "vid": int(row.id)},
            )
            linked.append(f"{row.public_id} supersedes {declared}")

    return {
        "linked": linked,
        "linked_count": len(linked),
        "unresolved": unresolved,
        "refused": refused,
    }


def _would_cycle(conn: Connection, version_id: int, target_id: int) -> bool:
    """Would pointing ``version_id`` at ``target_id`` close a cycle?

    Walks the existing chain upward from ``target_id``; if it reaches
    ``version_id`` the new link would make the chain circular. Bounded by the
    number of versions, so a chain that is *already* circular cannot hang this.
    """
    seen = {version_id}
    cursor: Optional[int] = target_id
    for _ in range(1000):
        if cursor is None:
            return False
        if cursor in seen:
            return True
        seen.add(cursor)
        cursor = conn.execute(
            text(
                "SELECT supersedes_version_id FROM document_versions"
                " WHERE id = :vid"
            ),
            {"vid": cursor},
        ).scalar_one_or_none()
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.db.loader",
        description=(
            "Load the pipeline's JSON artefacts into PostgreSQL. One "
            "transaction per version; re-running is idempotent."
        ),
    )
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument(
        "--raw-dir", type=Path, default=DEFAULT_RAW_DIR,
        help="Where the source PDFs live, recorded as stored_path.",
    )
    parser.add_argument(
        "--version-id", default=None,
        help="Load only this version directory.",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help=(
            "Delete and reload a version that is already present. Destructive; "
            "off by default."
        ),
    )
    parser.add_argument(
        "--stop-on-error", action="store_true",
        help="Stop at the first failing version instead of continuing.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the summary as JSON here as well as printing it.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    url = database_url()

    try:
        if args.version_id:
            summary = LoadSummary()
            summary.add(
                load_version_directory(
                    args.processed_dir / args.version_id,
                    url=url,
                    raw_dir=args.raw_dir,
                    replace=args.replace,
                )
            )
        else:
            summary = load_processed_tree(
                args.processed_dir,
                url=url,
                raw_dir=args.raw_dir,
                replace=args.replace,
                stop_on_error=args.stop_on_error,
            )
    except LoadError as exc:
        print(f"ERROR [{exc.code}] {exc}")
        return 1

    print(summary.format())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {args.output}")
    return 1 if summary.failed else 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
