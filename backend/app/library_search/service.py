"""Search orchestration and the baseline-vs-comparison seam.
**SCAFFOLD — not implemented.**

The API layer must never choose a retrieval strategy. It calls one function
here; this module decides which backend answers, so Person 5's experiment
(keyword vs semantic vs hybrid) is a parameter and not a branch scattered
through the routes. That is the same seam
``document_processing.extractor_backends`` gives Experiment A, and it exists
for the same reason: a comparison is only honest if both conditions run
through the identical call path.

Planned strategies
------------------
======================  ==========================================
``keyword``             PostgreSQL full-text (``tsvector`` + ``ts_rank_cd``).
                        **The baseline.** No new dependency, no index build.
``semantic``            Sentence embeddings over clause text, index held
                        inside the NLP module. **Needs D-7 answered and a
                        pinned dependency** (``CLAUDE.md`` §24 rule 5).
``hybrid``              Score fusion over the two above.
======================  ==========================================

``keyword`` is implementable today against the existing schema. The other two
are blocked on a written, agreed dependency justification, so this module must
work — and be measurable — with ``keyword`` alone first.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

__all__ = [
    "STRATEGIES",
    "DEFAULT_STRATEGY",
    "search",
    "suggest",
]

#: The conditions Person 5's evaluation compares. ``keyword`` is the baseline
#: and the only one that needs no new dependency.
STRATEGIES: Tuple[str, ...] = ("keyword", "semantic", "hybrid")

DEFAULT_STRATEGY = "keyword"

_NOT_BUILT = (
    "library_search is a scaffold: no search is implemented yet. "
    "See backend/app/library_search/README.md for the plan and the open "
    "decisions that gate it."
)


def search(
    query: str,
    *,
    strategy: str = DEFAULT_STRATEGY,
    scope: str = "documents",
    limit: Optional[int] = None,
    offset: int = 0,
    url: Optional[str] = None,
    **filters: Any,
) -> Dict[str, Any]:
    """Run one query under one strategy and return ranked results.

    ``scope`` selects documents or clauses; ``strategy`` selects the retrieval
    condition. The response is planned to name the strategy that produced it,
    so a screenshot or a logged result can always be attributed to a condition
    — a requirement of the evaluation, not a nicety.

    An empty or whitespace-only query will be a ``VALIDATION_ERROR`` (§12.1),
    not an implicit "return everything": the Library already has an unfiltered
    listing endpoint for that.
    """
    raise NotImplementedError(_NOT_BUILT)


def suggest(
    prefix: str,
    *,
    limit: int = 10,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Type-ahead suggestions for the Library search box.

    Planned source: ``documents.title`` and ``documents.reference_no`` only —
    never clause text. Suggesting a fragment of a clause invites a user to
    treat it as an answer, and this module has no evidence layer.
    """
    raise NotImplementedError(_NOT_BUILT)
