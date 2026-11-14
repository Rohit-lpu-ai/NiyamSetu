"""Library & Search — document discovery over the already-ingested corpus.

**Status: `[IMPLEMENTED]`.** PostgreSQL full-text search over
``segments.text_search`` (a stored ``tsvector`` with a GIN index), exposed as
four endpoints and reusing the existing engine, configuration, error envelope
and provenance guarantees.

What this module is for
-----------------------
Person 5's deliverable: *keyword search with a measured comparison condition,
and a search-relevance evaluation* (``CLAUDE.md`` §7). It answers one
question — "which documents and clauses match this query, and in what order" —
over data that already exists in PostgreSQL.

The files
---------
``repository.py``
    The SQL. Indexed full-text match, ranking, headline snippets, facets,
    suggestions, and the ILIKE comparison baseline. Read-only.
``service.py``
    Query validation, strategy choice, timing, and the response envelope that
    makes every result attributable to the condition that produced it.
``api.py``
    The two routers, mounted by ``api/main.py`` under ``/api``.
``schemas.py``
    Only the genuinely new shapes — ranking metadata and facets. Document,
    page and clause payloads stay in ``api/schemas.py``, with one definition.

What this module must never do
------------------------------
It does **not** extract, clean, segment, normalise or ingest anything, and it
does **not** define a second description of a document, version, page or
segment. Those live in ``backend.app.document_processing`` and
``backend.app.db``. A search module that re-chunks text produces offsets that
no longer match ``segment_pages``, and then a hit cannot be turned into a
citation — which is the project's whole differentiator (``CLAUDE.md`` §6).

It is also **read-only**, for the reason ``db.repository`` is: ``CLAUDE.md``
§9 forbids anything downstream of extraction from writing document data. A
test asserts that no write statement appears anywhere in this package.

Where it plugs in
-----------------
::

    backend/app/api/main.py          mounts both routers under /api
      └─ library_search.api          FastAPI routes + validation
          └─ library_search.service  strategy, validation, timing
              └─ library_search.repository   SQL over segments.text_search
                  └─ backend.app.db.engine   the EXISTING pooled connection

See ``README.md`` in this directory for the endpoint reference and the reuse
map, and ``backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`` for the
measured search-quality results.
"""

from __future__ import annotations

from backend.app.library_search.schemas import HIGHLIGHT_CLOSE, HIGHLIGHT_OPEN
from backend.app.library_search.service import (
    DEFAULT_STRATEGY,
    PLANNED_STRATEGIES,
    STRATEGIES,
    InvalidQuery,
    SearchService,
)

__all__ = [
    "SearchService",
    "InvalidQuery",
    "STRATEGIES",
    "PLANNED_STRATEGIES",
    "DEFAULT_STRATEGY",
    "HIGHLIGHT_OPEN",
    "HIGHLIGHT_CLOSE",
    "CONTRACT_VERSION",
]

#: Bumped when the search contract changes in a way consumers must notice.
#: 1.0.0 is the first implemented contract: four endpoints, the response
#: envelope in ``schemas.py``, and the provenance guarantee that every clause
#: hit carries its real pages.
CONTRACT_VERSION = "1.0.0"
