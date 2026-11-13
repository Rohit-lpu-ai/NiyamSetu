"""Library & Search — document discovery over the already-ingested corpus.

**Status: SCAFFOLD. Nothing in this package is implemented.** Every function
below raises :class:`NotImplementedError`, and a test pins that, so no caller
can mistake a stub for a working search.

What this module is for
-----------------------
Person 5's deliverable: *keyword vs semantic/hybrid document search, and a
search-relevance evaluation* (``CLAUDE.md`` §7). It answers one question —
"which documents or clauses match this query, and in what order" — and it
answers it over data that already exists in PostgreSQL.

What this module must never do
------------------------------
It does **not** extract, clean, segment, normalise or ingest anything, and it
does **not** define a second description of a document, version, page or
segment. Those live in ``backend.app.document_processing`` and
``backend.app.db`` respectively, and this package reads through them. A search
module that re-chunks text is a second pipeline whose output will drift from
the stored corpus, and then a citation cannot be trusted.

It is also **read-only**, for the same reason ``db.repository`` is: ``CLAUDE.md``
§9 forbids anything downstream of extraction from writing to the document
tables.

Where it plugs in
-----------------
::

    backend/app/api/routes.py        (Phase 4, built)
        -> library_search.service    this package: ranking + orchestration
            -> library_search.repository   SQL over segments.text_search
                -> backend.app.db.engine   the existing pooled connection

The one piece of unused infrastructure this module exists to light up is
``segments.text_search`` — a stored ``tsvector`` column with a GIN index,
created by ``0001_initial_schema.sql`` and queried by nothing today.

See ``README.md`` in this directory for the reuse map, the planned endpoints
and the open decisions that gate them.
"""

from __future__ import annotations

#: Deliberately empty. Nothing is implemented, so nothing is re-exported --
#: an ``__all__`` listing stubs would make the package look finished to
#: anyone reading ``from backend.app.library_search import *``.
__all__: list[str] = []

#: Bumped when the search contract changes in a way consumers must notice.
#: Starts below 1.0 because no contract is frozen yet.
CONTRACT_VERSION = "0.0.0-scaffold"
