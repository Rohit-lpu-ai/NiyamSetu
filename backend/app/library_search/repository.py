"""Search queries over the stored corpus. Read-only, parameterised SQL.

This module sits beside ``backend.app.db.repository`` rather than inside it:
that module is the frozen Phase 4 read layer four teammates already code
against through the API, and putting experimental ranking SQL behind a stable
contract is how a contract stops being stable. It reuses that module's engine,
its connection helper and its URL resolution — there is one pool and one place
the password is read.

**Everything here reads. Nothing writes.** ``CLAUDE.md`` §9 forbids anything
downstream of extraction from modifying document data, and a test asserts no
write statement appears in this package.

How the search actually works
-----------------------------
``segments.text_search`` is a stored ``tsvector`` generated column with a GIN
index, created by ``0001_initial_schema.sql`` for exactly this purpose:

.. code-block:: sql

    text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
    CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);

So the baseline is a real indexed full-text match — ``text_search @@ query``
ranked by ``ts_rank_cd`` — not a Python loop and not ``ILIKE`` in disguise.
Four choices in it are worth defending:

``websearch_to_tsquery`` **over** ``plainto_tsquery``
    It supports quoted phrases (``"capital adequacy"``), ``or`` and ``-``
    exclusion, and — unlike ``to_tsquery`` — it never raises on punctuation a
    user typed. ``plainto_tsquery`` ANDs every word and cannot express a
    phrase, which is the one thing policy search needs most.

``ts_rank_cd`` **over** ``ts_rank``
    Cover density rewards matches that occur *close together*, so a clause
    containing "capital adequacy ratio" outranks one that mentions capital in
    line 1 and adequacy in line 40.

normalisation flag ``1|32``
    ``1`` divides by the log of the segment length, which stops a 2,000
    character annexure outranking a 90 character clause purely by having more
    words; ``32`` maps the result into ``0 < score < 1`` so a score is
    readable. Both were compared on the real corpus — see
    ``backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md``.

one segmentation method per query
    Paragraph and clause-aware segments cover **the same text**, so searching
    both at once returns every passage twice. The method is a parameter with
    ``clause_aware`` as the default, never a union.

The query function is inlined into the ``WHERE`` clause rather than computed in
a CTE: a materialised CTE hides the constant from the planner and the GIN
index stops being usable. ``EXPLAIN`` output proving the index is used is in
the implementation report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.app.db.engine import connection
from backend.app.library_search.schemas import HIGHLIGHT_CLOSE, HIGHLIGHT_OPEN

__all__ = [
    "MAX_LIMIT",
    "DEFAULT_LIMIT",
    "TS_CONFIG",
    "RANK_NORMALIZATION",
    "SearchFilters",
    "parse_query",
    "search_clauses",
    "search_documents",
    "search_clauses_ilike",
    "facet_counts",
    "suggest",
    "explain_clause_search",
    "clause_search_rows",
]

#: Same cap as ``db.repository.MAX_LIMIT``: a caller asking for more gets this
#: rather than an error, so a paginating client cannot request the corpus.
MAX_LIMIT = 100
DEFAULT_LIMIT = 20

#: The text-search configuration. It must match the one the generated column
#: was built with (``to_tsvector('english', text)``) or the index cannot be
#: used and stemming would disagree with what is stored.
#:
#: 'english' is the wrong stemmer for the Devanagari letterhead text that
#: survives into some preamble segments. That is a recorded limitation, not an
#: oversight: no extractor available to this project reads that Devanagari
#: correctly in the first place (Experiment A §3).
TS_CONFIG = "english"

#: ``1`` = divide by log(length), ``32`` = scale into 0..1. See module docstring.
RANK_NORMALIZATION = 1 | 32

_HEADLINE_OPTIONS = (
    f"StartSel={HIGHLIGHT_OPEN}, StopSel={HIGHLIGHT_CLOSE}, "
    "MaxFragments=2, FragmentDelimiter= … , MinWords=6, MaxWords=24, "
    "ShortWord=3, HighlightAll=FALSE"
)

#: ``DISTINCT ON`` picks one version per document: the one a reader means by
#: "the document". Publication date first, then ingestion order, then id — all
#: three because publication_date is nullable and ties must still resolve the
#: same way on every run.
_LATEST_VERSIONS = """
    SELECT DISTINCT ON (v.document_id) v.id, v.document_id
      FROM document_versions v
     WHERE v.extraction_status = 'ok'
     ORDER BY v.document_id,
              v.publication_date DESC NULLS LAST,
              v.ingested_at DESC,
              v.id DESC
"""


def _clamp(limit: Optional[int], default: int = DEFAULT_LIMIT) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


class SearchFilters:
    """The filters a search accepts, and the SQL they become.

    A small class rather than six keyword arguments threaded through four
    functions: the clause-level and document-level queries must filter
    *identically* or their totals disagree, and that is only guaranteeable if
    the predicate is built in one place.

    Every value is bound, never interpolated — the fragments below contain
    only fixed SQL and ``:name`` placeholders.
    """

    def __init__(
        self,
        *,
        department: Optional[str] = None,
        document_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        document_id: Optional[str] = None,
        version_id: Optional[str] = None,
        segment_type: Optional[str] = None,
        page_from: Optional[int] = None,
        page_to: Optional[int] = None,
    ) -> None:
        self.department = department
        self.document_type = document_type
        self.date_from = date_from
        self.date_to = date_to
        self.document_id = document_id
        self.version_id = version_id
        self.segment_type = segment_type
        self.page_from = page_from
        self.page_to = page_to

    # -- the two predicate builders ----------------------------------------
    def document_sql(self, *, include_version: bool = True) -> Tuple[str, Dict[str, Any]]:
        """Predicates over ``documents d`` / ``document_versions v``.

        ``include_version=False`` drops the ``version_id`` predicate. The
        document-level query needs that: there ``v`` is the *latest* version
        joined on for display, while the version filter has already been
        applied to the segments themselves, and comparing it again against a
        different row would silently exclude every result.
        """
        parts: List[str] = []
        params: Dict[str, Any] = {}
        if self.department is not None:
            # A literal 'null' asks for documents whose department is not
            # stated. Without this the null bucket in the facets would be
            # unclickable, and 1 of the 4 original documents lives in it.
            if self.department == "null":
                parts.append("d.department IS NULL")
            else:
                parts.append("d.department = :f_department")
                params["f_department"] = self.department
        if self.document_type is not None:
            if self.document_type == "null":
                parts.append("d.document_type IS NULL")
            else:
                parts.append("d.document_type = :f_document_type")
                params["f_document_type"] = self.document_type
        if self.document_id is not None:
            parts.append("d.public_id = :f_document_id")
            params["f_document_id"] = self.document_id
        if self.version_id is not None and include_version:
            parts.append("v.public_id = :f_version_id")
            params["f_version_id"] = self.version_id
        if self.date_from is not None:
            parts.append("v.publication_date >= CAST(:f_date_from AS DATE)")
            params["f_date_from"] = self.date_from
        if self.date_to is not None:
            parts.append("v.publication_date <= CAST(:f_date_to AS DATE)")
            params["f_date_to"] = self.date_to
        return ("".join(f" AND {part}" for part in parts), params)

    def segment_sql(self) -> Tuple[str, Dict[str, Any]]:
        """Predicates over ``segments s``."""
        parts: List[str] = []
        params: Dict[str, Any] = {}
        if self.segment_type is not None:
            parts.append("s.segment_type = :f_segment_type")
            params["f_segment_type"] = self.segment_type
        if self.page_from is not None:
            parts.append("s.page_end >= :f_page_from")
            params["f_page_from"] = int(self.page_from)
        if self.page_to is not None:
            parts.append("s.page_start <= :f_page_to")
            params["f_page_to"] = int(self.page_to)
        return ("".join(f" AND {part}" for part in parts), params)

    def all_sql(self) -> Tuple[str, Dict[str, Any]]:
        doc_sql, doc_params = self.document_sql()
        seg_sql, seg_params = self.segment_sql()
        return doc_sql + seg_sql, {**doc_params, **seg_params}

    def as_dict(self) -> Dict[str, Any]:
        """What was actually applied, for echoing back in a response."""
        return {
            key: value
            for key, value in {
                "department": self.department,
                "document_type": self.document_type,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "document_id": self.document_id,
                "version_id": self.version_id,
                "segment_type": self.segment_type,
                "page_from": self.page_from,
                "page_to": self.page_to,
            }.items()
            if value is not None
        }


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #

def parse_query(query: str, *, url: Optional[str] = None) -> Dict[str, Any]:
    """Ask PostgreSQL what it makes of ``query``, before searching with it.

    Returns the rendered ``tsquery`` and its ``numnode``. ``numnode`` counts
    **nodes, not words**: it includes the operators, so ``capital adequacy``
    (two lexemes joined by ``&``) is 3, and a single word is 1. Only one value
    is load-bearing — **0 means the query has nothing searchable in it**.
    ``"the of and"`` and ``"!!!"`` both reduce to an empty tsquery, which
    would otherwise silently return zero results and look like "no such
    clause exists".

    Surfacing this is what lets the API say *why* a search found nothing, and
    it is why the response carries ``parsed_query``.
    """
    with connection(url) as conn:
        return _parse_query(conn, query)


def _parse_query(conn: Connection, query: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            f"SELECT websearch_to_tsquery('{TS_CONFIG}', :q)::text AS rendered, "
            f"       numnode(websearch_to_tsquery('{TS_CONFIG}', :q)) AS nodes"
        ),
        {"q": query},
    ).one()
    return {"tsquery": row.rendered or "", "nodes": int(row.nodes or 0)}


# The ranked-match core, shared by the clause query and its count. One
# definition, so "total" can never describe a different set than "items".
_MATCH_CORE = f"""
      FROM segments s
      JOIN document_versions v ON v.id = s.version_id
      JOIN documents d         ON d.id = v.document_id
     WHERE s.text_search @@ websearch_to_tsquery('{TS_CONFIG}', :q)
       AND s.segmentation_method = :method
       AND v.extraction_status = 'ok'
"""

_RANK = (
    f"ts_rank_cd(s.text_search, websearch_to_tsquery('{TS_CONFIG}', :q), "
    f"{RANK_NORMALIZATION})"
)

_HEADLINE = (
    f"ts_headline('{TS_CONFIG}', s.text, "
    f"websearch_to_tsquery('{TS_CONFIG}', :q), :headline_options)"
)

#: ``relevance`` is the default and the only one that uses the score. The
#: others still tie-break on ``s.public_id`` so that page 2 of a result set
#: never repeats or skips a row — an ``ORDER BY`` with ties is not a stable
#: paginator.
_CLAUSE_SORTS = {
    "relevance": "rank DESC, s.public_id ASC",
    "date_desc": "v.publication_date DESC NULLS LAST, rank DESC, s.public_id ASC",
    "date_asc": "v.publication_date ASC NULLS LAST, rank DESC, s.public_id ASC",
    "title": "d.title ASC, s.ordinal ASC, s.public_id ASC",
}


def _version_scope_sql(scope: str, filters: SearchFilters) -> str:
    """Restrict to one version per document unless told otherwise.

    Without this, a policy ingested twice (v1 and an amendment) returns both
    copies of an unchanged clause and the result list looks duplicated. An
    explicit ``version_id`` filter overrides the scope, because asking for a
    version means asking for that version.
    """
    if scope == "all" or filters.version_id is not None:
        return ""
    return f" AND s.version_id IN (SELECT id FROM ({_LATEST_VERSIONS}) lv)"


def search_clauses(
    query: str,
    *,
    method: str = "clause_aware",
    filters: Optional[SearchFilters] = None,
    sort: str = "relevance",
    version_scope: str = "latest",
    limit: Optional[int] = None,
    offset: int = 0,
    include_text: bool = False,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Ranked clause-level full-text search, with page provenance per hit.

    The returned items are citable on the same terms as
    ``GET /api/documents/{id}/clauses``: ``page_start``/``page_end`` plus a
    ``pages`` array with the character range on each page the segment touches.
    Nothing in here derives or guesses a page number.
    """
    filters = filters or SearchFilters()
    capped = _clamp(limit)
    order_by = _CLAUSE_SORTS.get(sort, _CLAUSE_SORTS["relevance"])

    filter_sql, filter_params = filters.all_sql()
    params: Dict[str, Any] = {
        "q": query,
        "method": method,
        "limit": capped,
        "offset": max(0, int(offset)),
        "headline_options": _HEADLINE_OPTIONS,
        **filter_params,
    }

    with connection(url) as conn:
        parsed = _parse_query(conn, query)
        if parsed["nodes"] == 0:
            return {
                "parsed_query": parsed["tsquery"],
                "total": 0,
                "limit": capped,
                "offset": params["offset"],
                "items": [],
                "no_searchable_terms": True,
            }

        scope_sql = _version_scope_sql(version_scope, filters)
        where = _MATCH_CORE + filter_sql + scope_sql

        total = conn.execute(
            text(f"SELECT count(*) {where}"), params
        ).scalar_one()

        text_column = "s.text AS full_text," if include_text else "NULL AS full_text,"
        rows = conn.execute(
            text(
                f"""
                SELECT s.id, s.public_id, s.ordinal, s.segment_type,
                       s.segmentation_method, s.clause_number,
                       s.clause_number_ambiguous, s.heading, s.level,
                       s.page_start, s.page_end,
                       {text_column}
                       {_RANK} AS rank,
                       {_HEADLINE} AS snippet,
                       d.public_id AS document_id, d.title, d.department,
                       d.document_type, d.reference_no,
                       v.public_id AS version_id, v.version_label,
                       v.publication_date, v.offset_basis
                  {where}
                 ORDER BY {order_by}
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()

        offsets = _page_offsets(conn, [row.id for row in rows])

    return {
        "parsed_query": parsed["tsquery"],
        "total": int(total),
        "limit": capped,
        "offset": params["offset"],
        "items": [_clause_hit(row, offsets) for row in rows],
        "no_searchable_terms": False,
    }


def _page_offsets(
    conn: Connection, segment_ids: Sequence[int]
) -> Dict[int, List[Dict[str, Any]]]:
    """Every page row for these segments, in one query.

    One round trip for the whole page, not one per hit — the pattern
    ``db.repository.get_segments`` already uses, and for the same reason.
    """
    if not segment_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT segment_id, page_number, char_start, char_end, "
            "       line_index_start, line_index_end "
            "  FROM segment_pages "
            " WHERE segment_id = ANY(:ids) "
            " ORDER BY segment_id, page_number"
        ),
        {"ids": list(segment_ids)},
    ).all()
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.segment_id, []).append(
            {
                "page_number": int(row.page_number),
                "char_start": int(row.char_start),
                "char_end": int(row.char_end),
                "line_index_start": int(row.line_index_start),
                "line_index_end": int(row.line_index_end),
            }
        )
    return grouped


def _clause_hit(row: Any, offsets: Dict[int, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """One result row as the payload the schema describes."""
    return {
        "clause_id": row.public_id,
        "score": float(row.rank),
        "snippet": _tidy_snippet(row.snippet),
        "segment_type": row.segment_type,
        "segmentation_method": row.segmentation_method,
        "ordinal": int(row.ordinal),
        "clause_number": row.clause_number,
        "clause_number_ambiguous": bool(row.clause_number_ambiguous),
        "heading": row.heading,
        "level": int(row.level),
        "text": row.full_text,
        "page_start": int(row.page_start),
        "page_end": int(row.page_end),
        "pages": offsets.get(row.id, []),
        "offset_basis": row.offset_basis,
        "document_id": row.document_id,
        "document_title": row.title,
        "department": row.department,
        "document_type": row.document_type,
        "reference_no": row.reference_no,
        "version_id": row.version_id,
        "version_label": row.version_label,
        "publication_date": (
            row.publication_date.isoformat() if row.publication_date else None
        ),
    }


def _tidy_snippet(snippet: Optional[str]) -> str:
    """Collapse the whitespace ``ts_headline`` preserves from the page text.

    Segment text contains the source document's line breaks, and a snippet
    rendered with them looks like a layout accident in a result list. The
    *stored* text is untouched; this only affects the display string.
    """
    if not snippet:
        return ""
    return " ".join(snippet.split())


# --------------------------------------------------------------------------- #
# Document-level search
# --------------------------------------------------------------------------- #

#: Metadata search runs ``to_tsvector`` over three columns at query time
#: rather than from a stored column. Deliberate, and cheap: ``documents`` is
#: the smallest table in the schema (tens of rows, not thousands), and a
#: generated column plus index would mean a migration for a sequential scan
#: that costs nothing. If the corpus ever reaches thousands of documents this
#: is the first thing to turn into a stored tsvector.
_METADATA_VECTOR = (
    "to_tsvector('"
    + TS_CONFIG
    + "', coalesce(d.title, '') || ' ' || coalesce(d.reference_no, '')"
    " || ' ' || coalesce(d.department, ''))"
)

#: How a document's score is formed. Stated as a constant because "why is
#: this document first?" must have one answer: the strongest clause in it,
#: plus a flat bonus if its own name matched. The bonus is larger than any
#: possible ts_rank_cd value (which the 32 normalisation caps below 1), so a
#: document whose *title* matches the query always outranks one that merely
#: mentions the words somewhere in its text.
METADATA_MATCH_BONUS = 1.0


def search_documents(
    query: str,
    *,
    method: str = "clause_aware",
    filters: Optional[SearchFilters] = None,
    sort: str = "relevance",
    version_scope: str = "latest",
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Ranked document-level search: clause evidence aggregated per document.

    A document matches if **either** one of its clauses matches **or** its
    own title / reference number / department matches. The second half is what
    makes "capital adequacy directions" find the document by name even when
    the phrase never appears in a clause, and it is why a hit can come back
    with ``best_clause = null``.
    """
    filters = filters or SearchFilters()
    capped = _clamp(limit)
    filter_sql, filter_params = filters.all_sql()
    doc_filter_sql, _ = filters.document_sql(include_version=False)

    params: Dict[str, Any] = {
        "q": query,
        "method": method,
        "limit": capped,
        "offset": max(0, int(offset)),
        "headline_options": _HEADLINE_OPTIONS,
        "bonus": METADATA_MATCH_BONUS,
        **filter_params,
    }

    with connection(url) as conn:
        parsed = _parse_query(conn, query)
        if parsed["nodes"] == 0:
            return {
                "parsed_query": parsed["tsquery"],
                "total": 0,
                "limit": capped,
                "offset": params["offset"],
                "items": [],
                "no_searchable_terms": True,
            }

        scope_sql = _version_scope_sql(version_scope, filters)

        # scored: every matching segment with its rank, filters already applied.
        # per_doc: the aggregate. best: the single strongest segment per
        # document, chosen with DISTINCT ON so the snippet shown is the one
        # the score came from.
        core = f"""
        WITH scored AS (
            SELECT s.id, s.public_id, v.document_id, {_RANK} AS rank
              {_MATCH_CORE}{filter_sql}{scope_sql}
        ),
        per_doc AS (
            SELECT document_id, max(rank) AS best_rank, count(*) AS matched_clauses
              FROM scored GROUP BY document_id
        ),
        best AS (
            SELECT DISTINCT ON (document_id) document_id, id AS segment_id
              FROM scored ORDER BY document_id, rank DESC, public_id ASC
        ),
        meta AS (
            SELECT d.id AS document_id
              FROM documents d
             WHERE {_METADATA_VECTOR} @@ websearch_to_tsquery('{TS_CONFIG}', :q)
        ),
        latest AS ({_LATEST_VERSIONS}),
        matched AS (
            SELECT d.id            AS doc_row_id,
                   d.public_id     AS document_id,
                   d.title, d.department, d.document_type, d.reference_no,
                   coalesce(per_doc.best_rank, 0)::float
                       + CASE WHEN meta.document_id IS NOT NULL
                              THEN :bonus ELSE 0 END          AS score,
                   coalesce(per_doc.matched_clauses, 0)        AS matched_clauses,
                   (meta.document_id IS NOT NULL)              AS metadata_match,
                   best.segment_id                             AS best_segment_id,
                   latest.id                                   AS latest_version_row_id
              FROM documents d
              LEFT JOIN per_doc ON per_doc.document_id = d.id
              LEFT JOIN best    ON best.document_id    = d.id
              LEFT JOIN meta    ON meta.document_id    = d.id
              LEFT JOIN latest  ON latest.document_id  = d.id
              LEFT JOIN document_versions v ON v.id = latest.id
             WHERE (per_doc.document_id IS NOT NULL OR meta.document_id IS NOT NULL)
               {doc_filter_sql}
        )
        """

        total = conn.execute(
            text(core + "SELECT count(*) FROM matched"), params
        ).scalar_one()

        order_by = {
            "relevance": "m.score DESC, m.matched_clauses DESC, m.document_id ASC",
            "date_desc": "v.publication_date DESC NULLS LAST, m.score DESC, m.document_id ASC",
            "date_asc": "v.publication_date ASC NULLS LAST, m.score DESC, m.document_id ASC",
            "title": "m.title ASC, m.document_id ASC",
        }.get(sort, "m.score DESC, m.matched_clauses DESC, m.document_id ASC")

        rows = conn.execute(
            text(
                core
                + f"""
                SELECT m.document_id, m.title, m.department, m.document_type,
                       m.reference_no, m.score, m.matched_clauses,
                       m.metadata_match, m.best_segment_id,
                       v.public_id AS version_id, v.version_label,
                       v.publication_date, v.page_count, v.extraction_status,
                       v.offset_basis,
                       (SELECT count(*) FROM document_versions av
                         WHERE av.document_id = m.doc_row_id) AS version_count
                  FROM matched m
                  LEFT JOIN document_versions v ON v.id = m.latest_version_row_id
                 ORDER BY {order_by}
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()

        best_clauses = _best_clause_details(
            conn, [row.best_segment_id for row in rows if row.best_segment_id], params
        )

    return {
        "parsed_query": parsed["tsquery"],
        "total": int(total),
        "limit": capped,
        "offset": params["offset"],
        "items": [
            {
                "document_id": row.document_id,
                "title": row.title,
                "department": row.department,
                "document_type": row.document_type,
                "reference_no": row.reference_no,
                "version_id": row.version_id,
                "version_label": row.version_label,
                "publication_date": (
                    row.publication_date.isoformat() if row.publication_date else None
                ),
                "page_count": int(row.page_count) if row.page_count else None,
                "version_count": int(row.version_count or 0),
                "extraction_status": row.extraction_status,
                "score": round(float(row.score), 6),
                "matched_clauses": int(row.matched_clauses),
                "metadata_match": bool(row.metadata_match),
                "best_clause": best_clauses.get(row.best_segment_id),
            }
            for row in rows
        ],
        "no_searchable_terms": False,
    }


def _best_clause_details(
    conn: Connection, segment_ids: Sequence[int], params: Dict[str, Any]
) -> Dict[int, Dict[str, Any]]:
    """Fetch the winning clause of each document hit, with its provenance.

    A second query rather than more joins in the first one: the aggregate
    query answers "which documents and how strongly", and this answers "show
    me the evidence". Keeping them apart is what keeps either readable.
    """
    if not segment_ids:
        return {}
    rows = conn.execute(
        text(
            f"""
            SELECT s.id, s.public_id, s.ordinal, s.segment_type,
                   s.segmentation_method, s.clause_number,
                   s.clause_number_ambiguous, s.heading, s.level,
                   s.page_start, s.page_end, NULL AS full_text,
                   {_RANK} AS rank,
                   {_HEADLINE} AS snippet,
                   d.public_id AS document_id, d.title, d.department,
                   d.document_type, d.reference_no,
                   v.public_id AS version_id, v.version_label,
                   v.publication_date, v.offset_basis
              FROM segments s
              JOIN document_versions v ON v.id = s.version_id
              JOIN documents d         ON d.id = v.document_id
             WHERE s.id = ANY(:ids)
            """
        ),
        {
            "ids": list(segment_ids),
            "q": params["q"],
            "headline_options": params["headline_options"],
        },
    ).all()
    offsets = _page_offsets(conn, [row.id for row in rows])
    return {row.id: _clause_hit(row, offsets) for row in rows}


# --------------------------------------------------------------------------- #
# The ILIKE comparison baseline
# --------------------------------------------------------------------------- #

def search_clauses_ilike(
    query: str,
    *,
    method: str = "clause_aware",
    filters: Optional[SearchFilters] = None,
    version_scope: str = "latest",
    limit: Optional[int] = None,
    offset: int = 0,
    include_text: bool = False,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Substring search, as the **measured comparison condition** only.

    This exists so the full-text baseline can be compared against something,
    not as a fallback. It is deliberately honest about what it is:

    * no stemming — "requirement" does not match "requirements";
    * no ranking — results come back in document order, because a substring
      match has no score to sort by. Ordering by ``s.public_id`` keeps
      pagination stable;
    * no phrase handling beyond the literal string;
    * a sequential scan: there is no index for ``%term%``.

    The API never silently falls back to this. A caller gets it only by
    asking for ``strategy=ilike``, and the response says so.
    """
    filters = filters or SearchFilters()
    capped = _clamp(limit)
    filter_sql, filter_params = filters.all_sql()
    scope_sql = _version_scope_sql(version_scope, filters)

    params: Dict[str, Any] = {
        "pattern": f"%{query.strip()}%",
        "method": method,
        "limit": capped,
        "offset": max(0, int(offset)),
        **filter_params,
    }

    where = f"""
      FROM segments s
      JOIN document_versions v ON v.id = s.version_id
      JOIN documents d         ON d.id = v.document_id
     WHERE s.text ILIKE :pattern
       AND s.segmentation_method = :method
       AND v.extraction_status = 'ok'
    {filter_sql}{scope_sql}
    """

    with connection(url) as conn:
        total = conn.execute(text(f"SELECT count(*) {where}"), params).scalar_one()
        rows = conn.execute(
            text(
                f"""
                SELECT s.id, s.public_id, s.ordinal, s.segment_type,
                       s.segmentation_method, s.clause_number,
                       s.clause_number_ambiguous, s.heading, s.level,
                       s.page_start, s.page_end, s.text AS full_text,
                       0.0 AS rank, NULL AS snippet,
                       d.public_id AS document_id, d.title, d.department,
                       d.document_type, d.reference_no,
                       v.public_id AS version_id, v.version_label,
                       v.publication_date, v.offset_basis
                  {where}
                 ORDER BY s.public_id ASC
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()
        offsets = _page_offsets(conn, [row.id for row in rows])

    items = []
    for row in rows:
        hit = _clause_hit(row, offsets)
        # No ts_headline for a substring match, so build the window here and
        # mark it with the same sentinels, so a client renders one format.
        hit["snippet"] = _substring_snippet(row, query)
        if not include_text:
            hit["text"] = None
        items.append(hit)

    return {
        "parsed_query": None,
        "total": int(total),
        "limit": capped,
        "offset": params["offset"],
        "items": items,
        "no_searchable_terms": False,
    }


def _substring_snippet(row: Any, query: str, window: int = 90) -> str:
    """A window around the first literal occurrence, marked like a headline."""
    source = " ".join((row.full_text or "").split())
    if not source:
        # Cannot happen for a row that matched ILIKE on its own text, but a
        # snippet builder that assumes its input exists is a 500 waiting for a
        # schema change.
        return ""
    lowered = source.lower()
    position = lowered.find(query.strip().lower())
    if position < 0:
        return source[: window * 2]
    start = max(0, position - window)
    end = min(len(source), position + len(query) + window)
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(source) else ""
    matched = source[position : position + len(query)]
    return (
        prefix
        + source[start:position]
        + HIGHLIGHT_OPEN
        + matched
        + HIGHLIGHT_CLOSE
        + source[position + len(query) : end]
        + suffix
    )


# --------------------------------------------------------------------------- #
# Facets and suggestions
# --------------------------------------------------------------------------- #

def facet_counts(*, url: Optional[str] = None) -> Dict[str, Any]:
    """Filter values and counts, read from the corpus — never hardcoded.

    Counts are over **documents**, not versions, because that is the unit the
    Library lists. Nulls are returned as real buckets: ``department`` and
    ``document_type`` are nullable in the schema because real documents omit
    them, and a facet list that hides the null bucket makes those documents
    unreachable by filtering.
    """
    with connection(url) as conn:
        totals = conn.execute(
            text(
                """
                SELECT (SELECT count(*) FROM documents)          AS documents,
                       (SELECT count(*) FROM document_versions)  AS versions,
                       (SELECT count(*) FROM pages)              AS pages
                """
            )
        ).one()

        segments = {
            row.segmentation_method: int(row.n)
            for row in conn.execute(
                text(
                    "SELECT segmentation_method, count(*) AS n "
                    "FROM segments GROUP BY segmentation_method "
                    "ORDER BY segmentation_method"
                )
            )
        }

        departments = _facet(
            conn,
            "SELECT d.department AS value, count(*) AS n FROM documents d "
            "GROUP BY d.department ORDER BY n DESC, d.department ASC NULLS LAST",
        )
        document_types = _facet(
            conn,
            "SELECT d.document_type AS value, count(*) AS n FROM documents d "
            "GROUP BY d.document_type ORDER BY n DESC, d.document_type ASC NULLS LAST",
        )
        years = _facet(
            conn,
            "SELECT to_char(v.publication_date, 'YYYY') AS value, "
            "       count(DISTINCT v.document_id) AS n "
            "  FROM document_versions v "
            " GROUP BY to_char(v.publication_date, 'YYYY') "
            " ORDER BY value DESC NULLS LAST",
        )
        statuses = _facet(
            conn,
            "SELECT v.extraction_status AS value, count(*) AS n "
            "FROM document_versions v GROUP BY v.extraction_status ORDER BY n DESC",
        )
        bounds = conn.execute(
            text(
                "SELECT min(publication_date) AS earliest, "
                "       max(publication_date) AS latest FROM document_versions"
            )
        ).one()

    return {
        "documents": int(totals.documents),
        "versions": int(totals.versions),
        "pages": int(totals.pages),
        "segments": segments,
        "departments": departments,
        "document_types": document_types,
        "publication_years": years,
        "extraction_statuses": statuses,
        "date_range": {
            "earliest": bounds.earliest.isoformat() if bounds.earliest else None,
            "latest": bounds.latest.isoformat() if bounds.latest else None,
        },
    }


def _facet(conn: Connection, sql: str) -> List[Dict[str, Any]]:
    return [
        {
            "value": row.value,
            "label": row.value if row.value is not None else "Not stated",
            "count": int(row.n),
        }
        for row in conn.execute(text(sql))
    ]


def suggest(
    prefix: str, *, limit: int = 10, url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Type-ahead values for the search box.

    **Titles, reference numbers and departments only — never clause text.**
    Suggesting a fragment of a clause invites a reader to treat it as an
    answer, and a suggestion carries no page reference to check it against.

    ``ILIKE`` here is the right tool, not a compromise: ``documents`` is a
    tens-of-rows table, the match is a prefix-ish substring over three short
    columns, and a tsvector would stem "capital" into something that no longer
    completes what the user is typing.
    """
    cleaned = prefix.strip()
    if len(cleaned) < 2:
        return []
    params = {"pattern": f"%{cleaned}%", "limit": max(1, min(int(limit), 25))}
    with connection(url) as conn:
        rows = conn.execute(
            text(
                """
                SELECT value, kind, document_id FROM (
                    SELECT d.title AS value, 'title' AS kind,
                           d.public_id AS document_id, 1 AS priority
                      FROM documents d WHERE d.title ILIKE :pattern
                    UNION ALL
                    SELECT d.reference_no, 'reference_no', d.public_id, 2
                      FROM documents d WHERE d.reference_no ILIKE :pattern
                    UNION ALL
                    SELECT DISTINCT d.department, 'department', NULL, 3
                      FROM documents d WHERE d.department ILIKE :pattern
                ) s
                ORDER BY priority, value
                LIMIT :limit
                """
            ),
            params,
        ).all()
    return [
        {"value": row.value, "kind": row.kind, "document_id": row.document_id}
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Index verification
# --------------------------------------------------------------------------- #

def explain_clause_search(
    query: str,
    *,
    method: str = "clause_aware",
    force_index: bool = False,
    url: Optional[str] = None,
) -> str:
    """``EXPLAIN ANALYZE`` for the clause query, as a string.

    Not an endpoint — a tool for the engineering review and for the viva.
    "Is the GIN index actually used?" is a question with an answer, and this
    is how it is obtained rather than assumed.

    ``force_index`` sets ``enable_seqscan = off`` for the statement, and it
    answers a *different* question. The distinction matters:

    * without it — **which plan does the planner choose?** On the current
      corpus (fewer than 2,000 segments) the answer is often a sequential
      scan, and that is PostgreSQL being right: the whole table is a couple of
      megabytes and every row is needed for the joins anyway.
    * with it — **is the index correct and applicable at all?** If the forced
      plan uses ``segments_text_search_idx`` and returns the same rows, the
      index matches the query and will be chosen as soon as the corpus is big
      enough for it to pay. That is the claim worth testing, because the first
      answer changes with the size of the corpus.

    The setting is ``SET LOCAL`` inside a transaction, so it cannot leak to
    another connection or outlive the call.
    """
    params = {"q": query, "method": method, "headline_options": _HEADLINE_OPTIONS}
    statement = text(
        f"""
        EXPLAIN (ANALYZE, BUFFERS)
        SELECT s.public_id, {_RANK} AS rank
          {_MATCH_CORE}
         ORDER BY rank DESC, s.public_id ASC
         LIMIT 20
        """
    )

    if not force_index:
        with connection(url) as conn:
            rows = conn.execute(statement, params).all()
        return "\n".join(row[0] for row in rows)

    from backend.app.db.engine import transaction

    with transaction(url) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        rows = conn.execute(statement, params).all()
    return "\n".join(row[0] for row in rows)


def clause_search_rows(
    query: str,
    *,
    method: str = "clause_aware",
    force_index: bool = False,
    url: Optional[str] = None,
) -> list:
    """The clause ids the explained query returns, in order.

    Exists so a forced-index plan can be checked for *correctness* and not
    only for which index it touched: an index that is used but returns
    different rows is worse than one that is ignored.
    """
    params = {"q": query, "method": method, "headline_options": _HEADLINE_OPTIONS}
    statement = text(
        f"""
        SELECT s.public_id, {_RANK} AS rank
          {_MATCH_CORE}
         ORDER BY rank DESC, s.public_id ASC
         LIMIT 20
        """
    )
    if not force_index:
        with connection(url) as conn:
            return [row.public_id for row in conn.execute(statement, params)]

    from backend.app.db.engine import transaction

    with transaction(url) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        return [row.public_id for row in conn.execute(statement, params)]
