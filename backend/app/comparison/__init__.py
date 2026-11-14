"""Compare: clause alignment, change classification and evidence.

The Compare capability of ``CLAUDE.md`` §5.4, integrated into the existing
backend rather than built beside it. It reads the ``documents``,
``document_versions``, ``segments`` and ``segment_pages`` tables the ingestion
pipeline already populates, through the engine ``backend.app.db`` already
pools, and serves its endpoints from the FastAPI application that already
exists.

Layout, and what each module is responsible for::

    similarity.py   formatting-insensitive comparison key + TF-IDF cosine
    alignment.py    the four-stage matcher and change classification
    entities.py     the rule-based entity extractor (the regex baseline)
    explain.py      word-level diff + plain-language description
    repository.py   read-only SQL over the existing tables
    service.py      validation, direction, evidence resolution, filtering
    schemas.py      Pydantic request/response models
    api.py          the router mounted by api/main.py

Two import rules hold inside the package, and tests pin both:

* ``alignment`` must not import ``entities`` or ``explain``. Entity extraction
  and wording must never be able to change which clauses are reported as
  modified.
* nothing here writes. ``CLAUDE.md`` §9 forbids anything downstream of
  extraction from modifying document data.

The teammate's original prototype is preserved unmodified at
``comparision.py`` in the repository root. ``README.md`` §2 maps every part of
it to where it ended up.
"""

from __future__ import annotations

from backend.app.comparison.alignment import (
    CHANGE_TYPES,
    CONFIDENCE_LEVELS,
    MATCH_BASES,
    AlignmentConfig,
    Change,
    ClauseRef,
    align,
    confidence_label,
    summarize,
)
from backend.app.comparison.entities import (
    ENTITY_LABELS,
    Entity,
    extract_entities,
    extract_by_label,
)
from backend.app.comparison.explain import describe, diff_stats, word_diff
from backend.app.comparison.service import (
    DEFAULT_METHOD,
    METHODS,
    ComparisonNotFound,
    ComparisonService,
    InvalidComparison,
    comparison_id_for,
)
from backend.app.comparison.similarity import (
    comparison_key,
    cosine_similarity,
    similarity_matrix,
)

__all__ = [
    # alignment
    "CHANGE_TYPES",
    "CONFIDENCE_LEVELS",
    "MATCH_BASES",
    "AlignmentConfig",
    "Change",
    "ClauseRef",
    "align",
    "confidence_label",
    "summarize",
    # similarity
    "comparison_key",
    "cosine_similarity",
    "similarity_matrix",
    # entities
    "ENTITY_LABELS",
    "Entity",
    "extract_entities",
    "extract_by_label",
    # explanation
    "describe",
    "diff_stats",
    "word_diff",
    # service
    "ComparisonService",
    "ComparisonNotFound",
    "InvalidComparison",
    "comparison_id_for",
    "METHODS",
    "DEFAULT_METHOD",
]
