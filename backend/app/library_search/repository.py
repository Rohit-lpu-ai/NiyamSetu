"""Search queries over the stored corpus. **SCAFFOLD — not implemented.**

This sits beside ``backend.app.db.repository`` rather than inside it on
purpose: that module is the frozen Phase 4 read layer that four teammates
already code against through the API, and adding experimental ranking SQL to
it would put an unstable surface behind a stable contract. The split is the
same one ``CLAUDE.md`` §9 draws between the storage layer and an NLP module.

Everything here is expected to be plain SQL executed through SQLAlchemy Core
with bound parameters (D-4), reusing ``backend.app.db.engine.connection`` so
there is exactly one pool and one place where the URL is resolved.

The SQL these functions will use already has its index
--------------------------------------------------------
``segments`` carries a generated column::

    text_search tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
    CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);

so the keyword baseline is a ``@@ plainto_tsquery`` match with
``ts_rank_cd`` ordering — no new table, no new column, no re-indexing step.

**Known defect to measure, not to hide:** the configuration is ``'english'``.
That is the wrong stemmer for the Devanagari text in the corpus, and the schema
records this (see ``0001_initial_schema.sql`` and its ``COMMENT``). The
keyword baseline must report it as a limitation rather than pretend coverage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "search_documents",
    "search_segments",
    "facet_counts",
]

_NOT_BUILT = (
    "library_search is a scaffold: no search is implemented yet. "
    "See backend/app/library_search/README.md for the plan and the open "
    "decisions that gate it."
)


def search_documents(
    query: str,
    *,
    department: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank *documents* for a free-text query.

    Planned: aggregate clause-level ``ts_rank_cd`` scores up to the document,
    so a document matching in several clauses outranks one matching in a
    single clause, and return the §12.3 ``GET /api/documents`` item shape plus
    a ``score`` and the best-matching clause as evidence.

    Returns the same ``{total, limit, offset, items}`` envelope as
    ``db.repository.list_documents`` so the Library UI needs no second parser.
    """
    raise NotImplementedError(_NOT_BUILT)


def search_segments(
    query: str,
    *,
    document_id: Optional[str] = None,
    method: str = "clause_aware",
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank *clauses* for a free-text query.

    Planned: ``text_search @@ plainto_tsquery(:q)`` ordered by
    ``ts_rank_cd``, returning each hit in the existing clause shape — so every
    result already carries ``clause_id``, ``page_start``/``page_end``, its
    per-page ``char_start``/``char_end`` and ``offset_basis``, and a citation
    is derivable without a second query.
    """
    raise NotImplementedError(_NOT_BUILT)


def facet_counts(
    *,
    url: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Available filter values and their document counts.

    Planned: ``department``, ``document_type`` and publication-year buckets,
    read from ``documents`` with the ``documents_department_idx`` and
    ``documents_type_idx`` indexes that already exist. The Library UI needs
    this to render filters instead of guessing which values exist — and both
    columns are nullable in real documents, so a ``null`` bucket is part of
    the contract, not an error.
    """
    raise NotImplementedError(_NOT_BUILT)
