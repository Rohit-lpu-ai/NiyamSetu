"""Read queries for Compare. Read-only, parameterised SQL, no new tables.

Why this module exists beside ``backend.app.db.repository`` rather than inside
it: that module is the Phase 4 read layer four teammates already code against
through the API, and adding comparison-specific queries behind a frozen
contract is how a contract stops being frozen. The same reasoning
``library_search/repository.py`` records for itself, and the same reuse: this
module takes its engine, its connection helper and its URL resolution from
``backend.app.db.engine``, so there is one pool and one place the password is
read.

**Everything here reads. Nothing writes.** ``CLAUDE.md`` §9 forbids anything
downstream of extraction from modifying document data, and a test asserts that
no write statement appears anywhere in this package.

What it does *not* do, deliberately
-----------------------------------
* **No new tables and no migration.** A comparison is a pure function of two
  stored versions and a set of thresholds, so it is computed on demand. See
  ``README.md`` §5 for the full argument and for how a result stays
  referenceable without being stored.
* **No second segment model.** :class:`~.alignment.ClauseRef` is built from the
  ``segments`` and ``segment_pages`` rows and carries no field the database
  does not already hold.
* **No ingestion.** Compare consumes what the existing pipeline produced. If a
  version is not in the database, Compare says so; it does not go and extract
  one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.app.comparison.alignment import ClauseRef
from backend.app.db.engine import connection

__all__ = [
    "NotFound",
    "VersionInfo",
    "load_version",
    "load_clauses",
    "comparable_documents",
    "page_text",
    "page_texts",
    "corpus_version_count",
]

#: Which page-text column the stored character offsets index into, keyed by
#: the version's ``offset_basis``. D-11 settled this (answered and verified):
#: offsets are into ``normalized_text``. The mapping is here rather than
#: hard-coded so that a version ingested with ``--use-raw-text`` -- which
#: stores ``offset_basis = 'raw_text'`` -- resolves against the right column
#: instead of silently slicing the wrong one.
OFFSET_BASIS_COLUMNS = {
    "normalized_text": "normalized_text",
    "cleaned_text": "cleaned_text",
    "raw_text": "raw_text",
}


class NotFound(LookupError):
    """A document, version or clause that does not exist. Carries a code."""

    code = "VERSION_NOT_FOUND"


@dataclass(frozen=True)
class VersionInfo:
    """Everything Compare needs to know about one version it is comparing.

    ``document_public_id`` is carried so the service can check that two
    versions belong to the same document without a second query, and
    ``offset_basis`` so every evidence span says which page column it indexes.
    """

    version_id: int
    version_public_id: str
    document_id: int
    document_public_id: str
    document_title: str
    department: Optional[str]
    document_type: Optional[str]
    #: The **document's** official reference number, not this version's.
    #: ``reference_no`` is a column on ``documents`` (0001_initial_schema.sql),
    #: so every issue of a master circular shares one value -- even though each
    #: issue is printed with its own (RBI/2016-17/05, RBI/2017-18/7, ...). The
    #: name says ``document_`` so a consumer cannot read it as version-level:
    #: a browser session found the 2019 issue displayed carrying the 2026
    #: issue's number. See COMPARISON_IMPLEMENTATION_REPORT.md.
    document_reference_no: Optional[str]
    version_label: str
    publication_date: Optional[Any]
    page_count: int
    extraction_status: str
    offset_basis: str
    supersedes_version_id: Optional[int]
    supersedes_public_id: Optional[str]
    source_url: Optional[str]
    file_checksum: str
    fingerprint: str
    ingested_at: Optional[Any]
    original_filename: str

    def to_dict(self) -> Dict[str, Any]:
        """The version summary carried on both sides of a comparison."""
        return {
            "version_id": self.version_public_id,
            "document_id": self.document_public_id,
            "document_title": self.document_title,
            "department": self.department,
            "document_type": self.document_type,
            "document_reference_no": self.document_reference_no,
            "version_label": self.version_label,
            "publication_date": (
                self.publication_date.isoformat() if self.publication_date else None
            ),
            "page_count": self.page_count,
            "extraction_status": self.extraction_status,
            "offset_basis": self.offset_basis,
            "supersedes_version_id": self.supersedes_public_id,
            "source_url": self.source_url,
            "file_checksum": self.file_checksum,
            "original_filename": self.original_filename,
        }


_VERSION_SELECT = """
    SELECT v.id                    AS version_id,
           v.public_id             AS version_public_id,
           v.document_id           AS document_id,
           d.public_id             AS document_public_id,
           d.title                 AS document_title,
           d.department            AS department,
           d.document_type         AS document_type,
           d.reference_no          AS reference_no,
           v.version_label         AS version_label,
           v.publication_date      AS publication_date,
           v.page_count            AS page_count,
           v.extraction_status     AS extraction_status,
           v.offset_basis          AS offset_basis,
           v.supersedes_version_id AS supersedes_version_id,
           s.public_id             AS supersedes_public_id,
           v.source_url            AS source_url,
           v.file_checksum         AS file_checksum,
           v.fingerprint           AS fingerprint,
           v.ingested_at           AS ingested_at,
           v.original_filename     AS original_filename
      FROM document_versions v
      JOIN documents d ON d.id = v.document_id
      LEFT JOIN document_versions s ON s.id = v.supersedes_version_id
"""


def _row_to_version(row: Any) -> VersionInfo:
    return VersionInfo(
        version_id=int(row.version_id),
        version_public_id=row.version_public_id,
        document_id=int(row.document_id),
        document_public_id=row.document_public_id,
        document_title=row.document_title,
        department=row.department,
        document_type=row.document_type,
        document_reference_no=row.reference_no,
        version_label=row.version_label,
        publication_date=row.publication_date,
        page_count=int(row.page_count),
        extraction_status=row.extraction_status,
        offset_basis=row.offset_basis,
        supersedes_version_id=(
            int(row.supersedes_version_id)
            if row.supersedes_version_id is not None
            else None
        ),
        supersedes_public_id=row.supersedes_public_id,
        source_url=row.source_url,
        file_checksum=row.file_checksum,
        fingerprint=row.fingerprint,
        ingested_at=row.ingested_at,
        original_filename=row.original_filename,
    )


def load_version(
    version_public_id: str,
    *,
    url: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> VersionInfo:
    """One version by its public id, with its document's metadata joined.

    Raises :class:`NotFound` rather than returning ``None``: every caller
    needs the version, and an optional return value only moves the error to
    somewhere less able to explain it.

    ``conn`` lets the service run several of these reads on one connection, so
    a comparison does not take three connections out of a five-connection
    pool.
    """
    if conn is not None:
        row = conn.execute(
            text(_VERSION_SELECT + " WHERE v.public_id = :vid"),
            {"vid": version_public_id},
        ).first()
    else:
        with connection(url) as opened:
            row = opened.execute(
                text(_VERSION_SELECT + " WHERE v.public_id = :vid"),
                {"vid": version_public_id},
            ).first()
    if row is None:
        raise NotFound(f"no version with id {version_public_id}")
    return _row_to_version(row)


def load_clauses(
    version_id: int,
    *,
    method: str = "clause_aware",
    conn: Optional[Connection] = None,
    url: Optional[str] = None,
) -> List[ClauseRef]:
    """Every clause of one version, in document order, with page provenance.

    Two queries, not one per clause: the segments, then every
    ``segment_pages`` row for all of them at once. With 254 clauses the
    per-clause version is 254 round trips, which is the mistake
    ``db.repository.get_segments`` already documents avoiding -- the same
    shape is used here on purpose.

    There is **no pagination**. Alignment needs both documents in full: a
    matcher shown half of one version would report the other half as removed.
    The cap on cost is the page count of a government circular, which the
    corpus puts between one and 37.
    """

    def _run(active: Connection) -> List[ClauseRef]:
        rows = active.execute(
            text(
                """
                SELECT s.id, s.public_id, s.fingerprint, s.ordinal,
                       s.segment_type, s.clause_number, s.heading, s.text,
                       s.page_start, s.page_end, s.level, s.marker_style,
                       s.clause_number_ambiguous
                  FROM segments s
                 WHERE s.version_id = :vid
                   AND s.segmentation_method = :method
                 ORDER BY s.ordinal
                """
            ),
            {"vid": version_id, "method": method},
        ).all()
        if not rows:
            return []

        pages_by_segment: Dict[int, List[Dict[str, Any]]] = {}
        page_rows = active.execute(
            text(
                "SELECT segment_id, page_number, char_start, char_end,"
                "       line_index_start, line_index_end "
                "  FROM segment_pages "
                " WHERE segment_id = ANY(:ids) "
                " ORDER BY segment_id, page_number"
            ),
            {"ids": [row.id for row in rows]},
        ).all()
        for page_row in page_rows:
            pages_by_segment.setdefault(page_row.segment_id, []).append(
                {
                    "page_number": int(page_row.page_number),
                    "char_start": int(page_row.char_start),
                    "char_end": int(page_row.char_end),
                    "line_index_start": int(page_row.line_index_start),
                    "line_index_end": int(page_row.line_index_end),
                }
            )

        return [
            ClauseRef(
                clause_id=row.public_id,
                ordinal=int(row.ordinal),
                text=row.text,
                page_start=int(row.page_start),
                page_end=int(row.page_end),
                clause_number=row.clause_number,
                heading=row.heading,
                segment_type=row.segment_type,
                level=int(row.level),
                marker_style=row.marker_style,
                clause_number_ambiguous=bool(row.clause_number_ambiguous),
                pages=tuple(pages_by_segment.get(row.id, ())),
                fingerprint=row.fingerprint,
            )
            for row in rows
        ]

    if conn is not None:
        return _run(conn)
    with connection(url) as opened:
        return _run(opened)


def comparable_documents(
    *,
    include_single_version: bool = False,
    url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Documents and their comparable versions, newest version first.

    A version is comparable only when ``extraction_status = 'ok'``: a
    ``partial`` version's page text may be incomplete, and a clause missing
    because extraction failed would be reported as *removed* -- an extraction
    defect presented to the user as a policy change. That is the single worst
    failure this feature could have, so the filter is in the SQL rather than
    left to a caller.

    ``include_single_version=True`` returns documents with one version too,
    each with an empty ``pairs`` list. The temporary frontend asks for that,
    because "this document exists but has only one version" is the honest
    empty state, and hiding such documents would make the corpus look smaller
    than it is.
    """
    with connection(url) as conn:
        rows = conn.execute(
            text(
                """
                SELECT d.public_id      AS document_public_id,
                       d.title          AS title,
                       d.department     AS department,
                       d.document_type  AS document_type,
                       d.reference_no   AS reference_no,
                       v.public_id      AS version_public_id,
                       v.version_label  AS version_label,
                       v.publication_date,
                       v.page_count,
                       v.ingested_at,
                       v.id             AS version_id,
                       v.supersedes_version_id,
                       s.public_id      AS supersedes_public_id,
                       (SELECT count(*) FROM segments sg
                         WHERE sg.version_id = v.id
                           AND sg.segmentation_method = 'clause_aware')
                                        AS clause_count
                  FROM documents d
                  JOIN document_versions v ON v.document_id = d.id
                  LEFT JOIN document_versions s
                         ON s.id = v.supersedes_version_id
                 WHERE v.extraction_status = 'ok'
                 ORDER BY d.public_id,
                          v.publication_date DESC NULLS LAST,
                          v.ingested_at DESC,
                          v.id DESC
                """
            )
        ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row.document_public_id,
            {
                "document_id": row.document_public_id,
                "title": row.title,
                "department": row.department,
                "document_type": row.document_type,
                "reference_no": row.reference_no,
                "versions": [],
                "pairs": [],
            },
        )
        entry["versions"].append(
            {
                "version_id": row.version_public_id,
                "version_label": row.version_label,
                "publication_date": (
                    row.publication_date.isoformat() if row.publication_date else None
                ),
                "page_count": int(row.page_count),
                "clause_count": int(row.clause_count),
                "supersedes_version_id": row.supersedes_public_id,
            }
        )

    out: List[Dict[str, Any]] = []
    for entry in grouped.values():
        versions = entry["versions"]
        if len(versions) < 2 and not include_single_version:
            continue
        # Every ordered pair older -> newer. The list is already newest first,
        # so for indices i < j, j is the older version.
        pairs: List[Dict[str, Any]] = []
        for newer_index in range(len(versions)):
            for older_index in range(newer_index + 1, len(versions)):
                newer = versions[newer_index]
                older = versions[older_index]
                pairs.append(
                    {
                        "base_version_id": older["version_id"],
                        "compare_version_id": newer["version_id"],
                        "base_label": older["version_label"],
                        "compare_label": newer["version_label"],
                        "base_publication_date": older["publication_date"],
                        "compare_publication_date": newer["publication_date"],
                        "consecutive": older_index == newer_index + 1,
                        # The strongest possible statement about a pair: the
                        # newer version's own metadata names the older one as
                        # the version it supersedes. Asserted by a human at
                        # ingestion (D-9), never inferred from titles.
                        "supersedes_declared": (
                            newer["supersedes_version_id"] == older["version_id"]
                        ),
                    }
                )
        entry["pairs"] = pairs
        entry["version_count"] = len(versions)
        out.append(entry)
    return out


def page_text(
    version_id: int,
    page_number: int,
    *,
    basis: str = "normalized_text",
    conn: Optional[Connection] = None,
    url: Optional[str] = None,
) -> Optional[str]:
    """The text of one page, from the column the offsets actually index into.

    ``basis`` comes from ``document_versions.offset_basis`` and is validated
    against :data:`OFFSET_BASIS_COLUMNS` before it reaches the query -- the
    column name cannot be parameterised, so it must come from a closed set
    rather than from the row, even though the schema already ``CHECK``-s it.
    """
    column = OFFSET_BASIS_COLUMNS.get(basis)
    if column is None:
        raise ValueError(
            f"unknown offset_basis {basis!r}; "
            f"expected one of {sorted(OFFSET_BASIS_COLUMNS)}"
        )
    statement = text(
        f"SELECT {column} AS body FROM pages "
        f"WHERE version_id = :vid AND page_number = :page"
    )
    params = {"vid": version_id, "page": page_number}
    if conn is not None:
        row = conn.execute(statement, params).first()
    else:
        with connection(url) as opened:
            row = opened.execute(statement, params).first()
    return None if row is None else row.body


def page_texts(
    version_id: int,
    *,
    basis: str = "normalized_text",
    conn: Optional[Connection] = None,
    url: Optional[str] = None,
) -> Dict[int, str]:
    """Every page of a version, keyed by page number. One query.

    Used by evidence verification, which checks that each clause's stored
    offsets really do slice its own text out of its own page. Doing that per
    clause would be one query per clause.
    """
    column = OFFSET_BASIS_COLUMNS.get(basis)
    if column is None:
        raise ValueError(
            f"unknown offset_basis {basis!r}; "
            f"expected one of {sorted(OFFSET_BASIS_COLUMNS)}"
        )
    statement = text(
        f"SELECT page_number, {column} AS body FROM pages "
        f"WHERE version_id = :vid ORDER BY page_number"
    )
    params = {"vid": version_id}
    if conn is not None:
        rows = conn.execute(statement, params).all()
    else:
        with connection(url) as opened:
            rows = opened.execute(statement, params).all()
    return {int(row.page_number): (row.body or "") for row in rows}


def corpus_version_count(*, url: Optional[str] = None) -> Dict[str, int]:
    """Enough corpus shape for the options endpoint to explain an empty state.

    When no document has two versions, the UI must be able to say *why* --
    "17 documents, none with a second version" is a usable message, while an
    empty list is indistinguishable from a broken query.
    """
    with connection(url) as conn:
        row = conn.execute(
            text(
                """
                SELECT (SELECT count(*) FROM documents) AS documents,
                       (SELECT count(*) FROM document_versions) AS versions,
                       (SELECT count(*) FROM document_versions
                         WHERE extraction_status = 'ok') AS ok_versions,
                       (SELECT count(*) FROM (
                            SELECT document_id
                              FROM document_versions
                             WHERE extraction_status = 'ok'
                             GROUP BY document_id
                            HAVING count(*) > 1
                        ) AS multi) AS documents_with_pairs
                """
            )
        ).one()
    return {
        "documents": int(row.documents),
        "versions": int(row.versions),
        "ok_versions": int(row.ok_versions),
        "documents_with_pairs": int(row.documents_with_pairs),
    }
