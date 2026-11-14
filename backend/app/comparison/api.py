"""The Compare router.

Mounted by ``backend/app/api/main.py`` under ``/api``, alongside the health,
documents and search routers. The module owns its own endpoints rather than
adding them to ``api/routes.py``, so everything comparison-related is in one
directory and ``api/main.py`` stays a composition point -- the convention
``library_search/api.py`` established.

It reuses the existing API's machinery instead of repeating it:
``api.errors.ApiError`` for the one §12.1 error envelope, FastAPI's ``Query``
and Pydantic for validation, and the same dependency-injection seam as
``api.dependencies.get_repository`` -- so these routes get mocked tests that
need no database, exactly like the ones that already exist.

**There is no new version-listing endpoint.** The brief proposed
``GET /api/documents/{document_id}/versions``; ``GET /api/documents/{id}``
already returns a document with its full version list, and a second endpoint
would be two places defining what a version summary is.
``GET /api/compare/options`` adds only what Compare needs on top of that: the
*pairs*, and whether each pair's order is declared or merely inferred.

**``GET /api/compare/{comparison_id}`` is deliberately absent.** A comparison
is a pure function of two stored versions and a set of thresholds, so there is
nothing to persist that could not drift out of date, and
``CLAUDE.md`` §24 rule 5 would require a written justification for a new table.
The useful half of persistence is kept: every response carries a
``comparison_id`` that is a deterministic hash of its inputs, so a result can
be cited in a report and re-derived exactly by POSTing the same body. The
reasoning is in ``README.md`` §5.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query, status

from backend.app.api.errors import ApiError
from backend.app.api.schemas import ErrorResponse
from backend.app.comparison.alignment import AlignmentConfig
from backend.app.comparison.entities import ENTITY_LABELS
from backend.app.comparison.schemas import (
    CompareOptionsResponse,
    CompareRequest,
    CompareResponse,
    EntitiesResponse,
    EntityLabelsResponse,
)
from backend.app.comparison.service import (
    DEFAULT_METHOD,
    ComparisonNotFound,
    ComparisonService,
    InvalidComparison,
)

__all__ = ["compare_router", "get_comparison_service"]


def get_comparison_service() -> ComparisonService:
    """The default service, bound to the configured database.

    Overridden wholesale in tests via ``app.dependency_overrides``, which is
    what lets the compare endpoints be tested without PostgreSQL.
    """
    return ComparisonService()


compare_router = APIRouter(prefix="/compare", tags=["compare"])

#: Reuses the existing error model so the OpenAPI document shows one envelope.
ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Document or version not found."},
    422: {"model": ErrorResponse, "description": "Validation failed."},
    500: {"model": ErrorResponse, "description": "Internal error."},
}

#: Entity-extraction limits, published on the labels endpoint so a consumer
#: reads them from the API rather than from a document it may not have.
ENTITY_LIMITATIONS = [
    "Rule-based. AUTHORITY matches a closed list of bodies observed in this "
    "corpus and will not find one it has never seen.",
    "OBLIGATION matches modal language ('shall', 'must', 'is required to'), "
    "not verified obligations: 'banks shall not be required to report' "
    "matches, and is a release from an obligation rather than one.",
    "No relative date is resolved to a calendar date. A deadline is reported "
    "as the period it is written as.",
    "CRF and transformer conditions are not implemented. See "
    "backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md for the measured regex "
    "scores and for why the other two conditions were not run.",
]


def _raise_invalid(exc: InvalidComparison) -> None:
    """Translate the service's domain error into the §12.1 envelope."""
    raise ApiError(
        getattr(exc, "code", "VALIDATION_ERROR"),
        str(exc),
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def _raise_missing(exc: ComparisonNotFound) -> None:
    raise ApiError(
        getattr(exc, "code", "VERSION_NOT_FOUND"),
        str(exc),
        status.HTTP_404_NOT_FOUND,
    )


@compare_router.get(
    "/options",
    response_model=CompareOptionsResponse,
    summary="Which comparisons this corpus can actually support",
    responses=ERROR_RESPONSES,
)
def compare_options(
    include_single_version: bool = Query(
        False,
        description="Also return documents that have only one version, each "
        "with an empty pairs list. The temporary frontend asks for this: "
        "'this document exists but has only one version' is the honest empty "
        "state, and hiding such documents makes the corpus look smaller than "
        "it is.",
    ),
    service: ComparisonService = Depends(get_comparison_service),
) -> CompareOptionsResponse:
    """Documents, their comparable versions, and every older/newer pair.

    Only versions with ``extraction_status = 'ok'`` are offered. A partial
    extraction can lose a clause, and a lost clause would be reported as
    *removed* -- an extraction defect presented to the user as a policy
    change, which is the worst failure this feature could have.

    ``supersedes_declared`` on a pair is the strongest statement the database
    can make about version order: the newer version's own metadata names the
    older one as the version it supersedes, asserted by a human at ingestion
    (D-9) and never inferred from title similarity.
    """
    return CompareOptionsResponse(
        **service.options(include_single_version=include_single_version)
    )


@compare_router.post(
    "",
    response_model=CompareResponse,
    summary="Compare two versions clause by clause, with evidence on both sides",
    responses=ERROR_RESPONSES,
)
def run_comparison(
    request: CompareRequest = Body(
        ...,
        openapi_examples={
            "consecutive_issues": {
                "summary": "Two issues of one master circular",
                "value": {
                    "base_version_id": "VER-0018",
                    "compare_version_id": "VER-0019",
                    "method": "clause_aware",
                },
            },
            "only_substantive_changes": {
                "summary": "Hide the unchanged clauses",
                "value": {
                    "base_version_id": "VER-0018",
                    "compare_version_id": "VER-0019",
                    "change_types": ["added", "removed", "modified"],
                },
            },
            "review_the_uncertain_matches": {
                "summary": "Only the pairs the matcher was unsure about",
                "value": {
                    "base_version_id": "VER-0018",
                    "compare_version_id": "VER-0019",
                    "only_ambiguous": True,
                },
            },
        },
    ),
    service: ComparisonService = Depends(get_comparison_service),
) -> CompareResponse:
    """Align the two versions' clauses and classify every one of them.

    Each item is one change, and every side of every change carries an
    ``evidence`` object that was **verified**: the stored character offsets
    were sliced out of the stored page text and checked against the clause.
    ``evidence.status`` is ``resolved``, ``partial`` or ``unresolved``, and
    ``quote`` is populated only when it resolved -- so a client that renders
    ``quote`` unconditionally still cannot show an unverified citation.

    Four behaviours are worth knowing before reading a result:

    * **Renumbering is not a modification.** A clause whose text is identical
      once its printed number is removed comes back as ``unchanged`` with
      ``renumbered = true``.
    * **Reordering is not a modification either.** ``reordered = true`` marks
      a clause that kept its wording and moved, computed as the complement of
      a longest-increasing-subsequence over the matched ordinals.
    * **Nothing is forced into a match.** Clauses below ``match_threshold``
      are reported as ``added`` or ``removed`` rather than paired with the
      least-bad candidate.
    * **Ambiguity is reported, not resolved.** When the runner-up scored
      within ``ambiguity_margin`` of the winner, the change is
      ``ambiguous = true``, ``confidence = "low"``, and
      ``runner_up_clause_id`` names the clause the matcher nearly chose
      instead.
    """
    overrides = {
        name: value
        for name, value in (
            ("match_threshold", request.match_threshold),
            ("number_match_threshold", request.number_match_threshold),
            ("ambiguity_margin", request.ambiguity_margin),
            ("high_confidence_similarity", request.high_confidence_similarity),
        )
        if value is not None
    }
    config = AlignmentConfig(**overrides) if overrides else None

    try:
        payload = service.compare(
            request.base_version_id,
            request.compare_version_id,
            document_id=request.document_id,
            method=request.method,
            config=config,
            allow_cross_document=request.allow_cross_document,
            change_types=request.change_types,
            confidence=request.confidence,
            min_similarity=request.min_similarity,
            max_similarity=request.max_similarity,
            only_ambiguous=request.only_ambiguous,
            include_text=request.include_text,
            include_diff=request.include_diff,
            include_entities=request.include_entities,
            include_explanation=request.include_explanation,
            limit=request.limit,
            offset=request.offset,
        )
    except InvalidComparison as exc:
        _raise_invalid(exc)
    except ComparisonNotFound as exc:
        _raise_missing(exc)
    return CompareResponse(**payload)


@compare_router.get(
    "/entities",
    response_model=EntitiesResponse,
    summary="Rule-based entity extraction over one version's clauses",
    responses=ERROR_RESPONSES,
)
def version_entities(
    version_id: str = Query(..., description="e.g. VER-0003.", min_length=1),
    method: str = Query(DEFAULT_METHOD, pattern="^(clause_aware|paragraph)$"),
    clause_id: Optional[str] = Query(
        None, description="Restrict to one clause, e.g. CLS-0000123."
    ),
    labels: Optional[List[str]] = Query(
        None,
        description="Restrict to these labels. See GET /api/compare/entities/"
        "labels for the list; an unknown label is a 422, not a silent empty "
        "result.",
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: ComparisonService = Depends(get_comparison_service),
) -> EntitiesResponse:
    """Dates, periods, amounts, percentages, authorities and obligation cues.

    A separate endpoint on purpose: the brief requires entity extraction to
    stay separable from the comparison logic, so nothing in alignment depends
    on it and a change to a date pattern cannot alter which clauses are
    reported as modified.

    Every mention carries ``start``/``end`` offsets into the clause's own
    ``text``, which is itself a slice of the page's ``normalized_text`` -- so a
    mention is traceable to a page without this endpoint knowing about pages.
    """
    try:
        payload = service.entities(
            version_id,
            method=method,
            clause_id=clause_id,
            labels=labels,
            limit=limit,
            offset=offset,
        )
    except InvalidComparison as exc:
        _raise_invalid(exc)
    except ComparisonNotFound as exc:
        _raise_missing(exc)
    return EntitiesResponse(**payload)


@compare_router.get(
    "/entities/labels",
    response_model=EntityLabelsResponse,
    summary="What the extractor can find, and what it cannot",
)
def entity_labels() -> EntityLabelsResponse:
    """The label vocabulary and its stated limitations.

    Published through the API so a consumer reads the limits from the running
    system rather than from a document they may not have. The limitations are
    not a disclaimer: each one names a specific, reproducible failure of the
    rule-based condition.
    """
    return EntityLabelsResponse(
        labels=dict(ENTITY_LABELS),
        condition="regex",
        limitations=list(ENTITY_LIMITATIONS),
    )
