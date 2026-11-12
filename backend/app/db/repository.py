"""Read queries over the stored corpus.

This is the layer Phase 4's FastAPI endpoints will sit on, and the one
Persons 2–5 consume *through* that API. It is deliberately read-only: there is
no function here that writes to ``documents``, ``document_versions``, ``pages``
or ``segments``, because ``CLAUDE.md`` §9 forbids anything downstream of
extraction from modifying document data.

Every function returns plain dicts keyed the way ``CLAUDE.md`` §12 specifies —
``public_id`` strings, never database integers — so a payload can be handed
straight to a response model without a translation step that could leak an
internal id.

Pagination is ``limit``/``offset`` with a hard cap, matching §12.3.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.app.db.engine import connection

__all__ = [
    "NotFound",
    "MAX_LIMIT",
    "list_documents",
    "get_document",
    "get_pages",
    "get_segments",
    "get_segment",
    "get_citation",
    "corpus_counts",
]

#: §12.3's cap. A caller asking for more gets this, not an error, so a
#: paginating client cannot accidentally request the whole corpus.
MAX_LIMIT = 100
DEFAULT_LIMIT = 20

METHODS = ("clause_aware", "paragraph")


class NotFound(LookupError):
    """A requested document, version or clause does not exist."""

    code = "NOT_FOUND"


def _clamp(limit: Optional[int], default: int = DEFAULT_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


def _resolve_version(
    conn: Connection,
    document_public_id: str,
    version_public_id: Optional[str] = None,
) -> Any:
    """Resolve a document (+ optional version) to the version row to read.

    With no version named, the **latest successfully extracted** version wins —
    not simply the newest row. A version whose extraction was ``partial`` or
    ``failed`` must never become the default answer, because its page text may
    be incomplete and a citation into it would be unsound.

    Ordering is by ``publication_date`` with nulls last, then by id. Publication
    date alone is explicitly not a reliable version order (``CLAUDE.md`` §11.1),
    which is why the id breaks the tie deterministically rather than leaving it
    to the planner.
    """
    document = conn.execute(
        text("SELECT id, public_id FROM documents WHERE public_id = :pid"),
        {"pid": document_public_id},
    ).first()
    if document is None:
        raise NotFound(f"no document with id {document_public_id}")

    if version_public_id:
        version = conn.execute(
            text(
                "SELECT id, public_id, offset_basis, page_count, extraction_status "
                "FROM document_versions "
                "WHERE public_id = :vid AND document_id = :did"
            ),
            {"vid": version_public_id, "did": document.id},
        ).first()
        if version is None:
            raise NotFound(
                f"no version {version_public_id} belonging to "
                f"{document_public_id}"
            )
        return version

    version = conn.execute(
        text(
            "SELECT id, public_id, offset_basis, page_count, extraction_status "
            "FROM document_versions "
            "WHERE document_id = :did AND extraction_status = 'ok' "
            "ORDER BY publication_date DESC NULLS LAST, id DESC "
            "LIMIT 1"
        ),
        {"did": document.id},
    ).first()
    if version is None:
        raise NotFound(
            f"{document_public_id} has no version with extraction_status 'ok'"
        )
    return version


def list_documents(
    *,
    q: Optional[str] = None,
    department: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """The Library listing: §12.3's ``GET /api/documents`` shape."""
    capped = _clamp(limit)
    clauses: List[str] = []
    params: Dict[str, Any] = {"limit": capped, "offset": max(0, int(offset))}

    if q:
        clauses.append("(d.title ILIKE :q OR d.reference_no ILIKE :q)")
        params["q"] = f"%{q}%"
    if department:
        clauses.append("d.department = :department")
        params["department"] = department
    if document_type:
        clauses.append("d.document_type = :document_type")
        params["document_type"] = document_type
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connection(url) as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM documents d {where}"), params
        ).scalar_one()

        rows = conn.execute(
            text(
                f"""
                SELECT d.public_id, d.title, d.department, d.document_type,
                       d.reference_no,
                       count(v.id)            AS version_count,
                       max(v.publication_date) AS latest_publication_date
                  FROM documents d
                  LEFT JOIN document_versions v ON v.document_id = d.id
                  {where}
                 GROUP BY d.id, d.public_id, d.title, d.department,
                          d.document_type, d.reference_no
                 ORDER BY d.public_id
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()

    return {
        "total": int(total),
        "limit": capped,
        "offset": params["offset"],
        "items": [
            {
                "document_id": row.public_id,
                "title": row.title,
                "department": row.department,
                "document_type": row.document_type,
                "reference_no": row.reference_no,
                "version_count": int(row.version_count),
                "latest_publication_date": (
                    row.latest_publication_date.isoformat()
                    if row.latest_publication_date
                    else None
                ),
            }
            for row in rows
        ],
    }


def get_document(
    document_public_id: str, *, url: Optional[str] = None
) -> Dict[str, Any]:
    """Document metadata plus every version, newest first."""
    with connection(url) as conn:
        document = conn.execute(
            text(
                "SELECT id, public_id, title, department, document_type,"
                "       reference_no, created_at "
                "FROM documents WHERE public_id = :pid"
            ),
            {"pid": document_public_id},
        ).first()
        if document is None:
            raise NotFound(f"no document with id {document_public_id}")

        versions = conn.execute(
            text(
                """
                SELECT v.public_id, v.version_label, v.publication_date,
                       v.page_count, v.extraction_status, v.extraction_tool,
                       v.offset_basis, v.source_url, v.file_checksum,
                       v.processing_warnings, v.ingested_at,
                       s.public_id AS supersedes_version_id,
                       (SELECT count(*) FROM segments g
                         WHERE g.version_id = v.id
                           AND g.segmentation_method = 'clause_aware')
                           AS clause_aware_segments,
                       (SELECT count(*) FROM segments g
                         WHERE g.version_id = v.id
                           AND g.segmentation_method = 'paragraph')
                           AS paragraph_segments
                  FROM document_versions v
                  LEFT JOIN document_versions s
                         ON s.id = v.supersedes_version_id
                 WHERE v.document_id = :did
                 ORDER BY v.publication_date DESC NULLS LAST, v.id DESC
                """
            ),
            {"did": document.id},
        ).all()

    return {
        "document_id": document.public_id,
        "title": document.title,
        "department": document.department,
        "document_type": document.document_type,
        "reference_no": document.reference_no,
        "versions": [
            {
                "version_id": row.public_id,
                "version_label": row.version_label,
                "publication_date": (
                    row.publication_date.isoformat() if row.publication_date else None
                ),
                "page_count": int(row.page_count),
                "extraction_status": row.extraction_status,
                "extraction_tool": row.extraction_tool,
                "offset_basis": row.offset_basis,
                "source_url": row.source_url,
                "file_checksum": row.file_checksum,
                # Warnings recorded at extraction time (e.g. a page that
                # yielded 0 characters). Surfaced because "the extraction
                # succeeded" and "the extraction was clean" are different
                # claims, and a consumer deciding whether to trust a page
                # needs the second one.
                "processing_warnings": list(row.processing_warnings or []),
                "ingested_at": (
                    row.ingested_at.isoformat() if row.ingested_at else None
                ),
                "supersedes_version_id": row.supersedes_version_id,
                "segment_counts": {
                    "clause_aware": int(row.clause_aware_segments),
                    "paragraph": int(row.paragraph_segments),
                },
            }
            for row in versions
        ],
    }


def get_pages(
    document_public_id: str,
    *,
    version_public_id: Optional[str] = None,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    text_variant: str = "normalized",
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Page text for a version. ``text_variant``: normalized | raw | cleaned | both."""
    allowed = {"normalized", "raw", "cleaned", "both"}
    if text_variant not in allowed:
        raise ValueError(
            f"text must be one of {sorted(allowed)}, got {text_variant!r}"
        )

    with connection(url) as conn:
        version = _resolve_version(conn, document_public_id, version_public_id)
        params: Dict[str, Any] = {"vid": version.id}
        bounds = ""
        if page_from is not None:
            bounds += " AND page_number >= :page_from"
            params["page_from"] = int(page_from)
        if page_to is not None:
            bounds += " AND page_number <= :page_to"
            params["page_to"] = int(page_to)

        rows = conn.execute(
            text(
                f"SELECT page_number, raw_text, normalized_text, cleaned_text,"
                f"       char_count, word_count, is_empty, has_meaningful_text "
                f"FROM pages WHERE version_id = :vid{bounds} "
                f"ORDER BY page_number"
            ),
            params,
        ).all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {
            "page_number": int(row.page_number),
            "char_count": int(row.char_count),
            "word_count": int(row.word_count),
            "is_empty": bool(row.is_empty),
            "has_meaningful_text": bool(row.has_meaningful_text),
        }
        if text_variant in ("normalized", "both"):
            item["normalized_text"] = row.normalized_text
        if text_variant in ("raw", "both"):
            item["raw_text"] = row.raw_text
        if text_variant == "cleaned":
            item["cleaned_text"] = row.cleaned_text
        items.append(item)

    return {
        "document_id": document_public_id,
        "version_id": version.public_id,
        "offset_basis": version.offset_basis,
        "page_count": int(version.page_count),
        "items": items,
    }


def get_segments(
    document_public_id: str,
    *,
    version_public_id: Optional[str] = None,
    method: str = "clause_aware",
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Clauses or paragraphs for a version, with full page provenance.

    This is §12.3's ``GET /api/documents/{id}/clauses`` shape, and the function
    Persons 3, 4 and 5 ultimately read their corpus through. Each item carries
    its ``pages`` array — one entry per page the segment touches, with the
    character range — so a multi-page clause arrives with both of its pages and
    a consumer never has to guess where the second half lives.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {list(METHODS)}, got {method!r}")
    capped = _clamp(limit, default=50)

    with connection(url) as conn:
        version = _resolve_version(conn, document_public_id, version_public_id)
        params: Dict[str, Any] = {
            "vid": version.id,
            "method": method,
            "limit": capped,
            "offset": max(0, int(offset)),
        }
        bounds = ""
        if page_from is not None:
            bounds += " AND s.page_end >= :page_from"
            params["page_from"] = int(page_from)
        if page_to is not None:
            bounds += " AND s.page_start <= :page_to"
            params["page_to"] = int(page_to)

        total = conn.execute(
            text(
                f"SELECT count(*) FROM segments s "
                f"WHERE s.version_id = :vid AND s.segmentation_method = :method"
                f"{bounds}"
            ),
            params,
        ).scalar_one()

        rows = conn.execute(
            text(
                f"""
                SELECT s.id, s.public_id, s.fingerprint, s.ordinal,
                       s.segment_type, s.clause_number, s.heading, s.text,
                       s.page_start, s.page_end, s.level, s.marker_style,
                       s.clause_number_ambiguous, s.metadata,
                       p.public_id AS parent_clause_id
                  FROM segments s
                  LEFT JOIN segments p ON p.id = s.parent_id
                 WHERE s.version_id = :vid
                   AND s.segmentation_method = :method{bounds}
                 ORDER BY s.ordinal
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()

        # One follow-up query for every page row at once, rather than one per
        # segment: with 254 clauses the per-segment version is 254 round trips.
        offsets_by_segment: Dict[int, List[Dict[str, Any]]] = {}
        if rows:
            page_rows = conn.execute(
                text(
                    "SELECT segment_id, page_number, char_start, char_end,"
                    "       line_index_start, line_index_end "
                    "FROM segment_pages "
                    "WHERE segment_id = ANY(:ids) "
                    "ORDER BY segment_id, page_number"
                ),
                {"ids": [row.id for row in rows]},
            ).all()
            for page_row in page_rows:
                offsets_by_segment.setdefault(page_row.segment_id, []).append(
                    {
                        "page_number": int(page_row.page_number),
                        "char_start": int(page_row.char_start),
                        "char_end": int(page_row.char_end),
                        "line_index_start": int(page_row.line_index_start),
                        "line_index_end": int(page_row.line_index_end),
                    }
                )

    return {
        "document_id": document_public_id,
        "version_id": version.public_id,
        "segmentation_method": method,
        "offset_basis": version.offset_basis,
        "total": int(total),
        "limit": capped,
        "offset": params["offset"],
        "items": [
            {
                "clause_id": row.public_id,
                "fingerprint": row.fingerprint,
                "ordinal": int(row.ordinal),
                "segment_type": row.segment_type,
                "clause_number": row.clause_number,
                "clause_number_ambiguous": bool(row.clause_number_ambiguous),
                "heading": row.heading,
                "text": row.text,
                "page_start": int(row.page_start),
                "page_end": int(row.page_end),
                "pages": offsets_by_segment.get(row.id, []),
                "level": int(row.level),
                "marker_style": row.marker_style,
                "parent_clause_id": row.parent_clause_id,
                "metadata": row.metadata or {},
            }
            for row in rows
        ],
    }


def get_segment(
    clause_public_id: str, *, url: Optional[str] = None
) -> Dict[str, Any]:
    """One clause by its citable id, with provenance and its page text."""
    with connection(url) as conn:
        row = conn.execute(
            text(
                """
                SELECT s.id, s.public_id, s.fingerprint, s.ordinal,
                       s.segment_type, s.clause_number, s.heading, s.text,
                       s.page_start, s.page_end, s.level, s.marker_style,
                       s.clause_number_ambiguous, s.metadata,
                       s.segmentation_method,
                       v.public_id AS version_id, v.offset_basis,
                       d.public_id AS document_id
                  FROM segments s
                  JOIN document_versions v ON v.id = s.version_id
                  JOIN documents d         ON d.id = v.document_id
                 WHERE s.public_id = :pid
                """
            ),
            {"pid": clause_public_id},
        ).first()
        if row is None:
            raise NotFound(f"no clause with id {clause_public_id}")

        pages = conn.execute(
            text(
                "SELECT page_number, char_start, char_end,"
                "       line_index_start, line_index_end "
                "FROM segment_pages WHERE segment_id = :sid ORDER BY page_number"
            ),
            {"sid": row.id},
        ).all()

    return {
        "clause_id": row.public_id,
        "fingerprint": row.fingerprint,
        "document_id": row.document_id,
        "version_id": row.version_id,
        "segmentation_method": row.segmentation_method,
        "offset_basis": row.offset_basis,
        "ordinal": int(row.ordinal),
        "segment_type": row.segment_type,
        "clause_number": row.clause_number,
        "clause_number_ambiguous": bool(row.clause_number_ambiguous),
        "heading": row.heading,
        "text": row.text,
        "page_start": int(row.page_start),
        "page_end": int(row.page_end),
        "level": int(row.level),
        "marker_style": row.marker_style,
        "metadata": row.metadata or {},
        "pages": [
            {
                "page_number": int(page.page_number),
                "char_start": int(page.char_start),
                "char_end": int(page.char_end),
                "line_index_start": int(page.line_index_start),
                "line_index_end": int(page.line_index_end),
            }
            for page in pages
        ],
    }


def get_citation(
    clause_public_id: str, *, url: Optional[str] = None
) -> Dict[str, Any]:
    """A clause in the §12.2 citation shape, read from the committed view.

    Read through ``segment_citations`` rather than assembled here, so Ask and
    Compare cannot drift apart on what a citation is — and so a version whose
    extraction was not fully ``ok`` cannot be cited at all (the view filters
    it).

    ``quote`` is the clause text verbatim, which is what §12.2 requires: a
    citation's quote must be a substring of the clause it names, and the
    identity substring trivially satisfies that. A caller wanting a narrower
    quote must slice this text, not re-extract from the page.
    """
    with connection(url) as conn:
        row = conn.execute(
            text(
                "SELECT clause_id, document_id, version_id, page_start,"
                "       page_end, text, offset_basis "
                "FROM segment_citations WHERE clause_id = :pid"
            ),
            {"pid": clause_public_id},
        ).first()
    if row is None:
        raise NotFound(
            f"no citable clause with id {clause_public_id} "
            f"(it may belong to a version whose extraction was not 'ok')"
        )
    return {
        "document_id": row.document_id,
        "version_id": row.version_id,
        "clause_id": row.clause_id,
        "page_start": int(row.page_start),
        "page_end": int(row.page_end),
        "quote": row.text,
        "offset_basis": row.offset_basis,
    }


def corpus_counts(*, url: Optional[str] = None) -> Dict[str, Any]:
    """Row counts per table. Used by the integration test and the preview app."""
    with connection(url) as conn:
        row = conn.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM documents)          AS documents,
                  (SELECT count(*) FROM document_versions)  AS versions,
                  (SELECT count(*) FROM pages)              AS pages,
                  (SELECT count(*) FROM page_removed_lines) AS removed_lines,
                  (SELECT count(*) FROM segments)           AS segments,
                  (SELECT count(*) FROM segment_pages)      AS segment_pages,
                  (SELECT count(*) FROM segments
                    WHERE segmentation_method = 'paragraph') AS paragraph_segments,
                  (SELECT count(*) FROM segments
                    WHERE segmentation_method = 'clause_aware')
                      AS clause_aware_segments,
                  (SELECT count(*) FROM segments
                    WHERE page_end > page_start)            AS page_spanning_segments,
                  (SELECT count(*) FROM ingestion_jobs)     AS ingestion_jobs
                """
            )
        ).one()
    return {
        "documents": int(row.documents),
        "versions": int(row.versions),
        "pages": int(row.pages),
        "removed_lines": int(row.removed_lines),
        "segments": int(row.segments),
        "segment_pages": int(row.segment_pages),
        "paragraph_segments": int(row.paragraph_segments),
        "clause_aware_segments": int(row.clause_aware_segments),
        "page_spanning_segments": int(row.page_spanning_segments),
        "ingestion_jobs": int(row.ingestion_jobs),
    }
