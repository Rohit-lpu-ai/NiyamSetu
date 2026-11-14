"""The Library & Search routers.

Mounted by ``backend/app/api/main.py`` under ``/api``, alongside the existing
health and documents routers. The module owns its own endpoints rather than
adding them to ``api/routes.py``, so everything search-related is in one
directory and ``api/main.py`` stays a composition point.

It reuses the existing API's machinery instead of repeating it:
``api.errors.ApiError`` for the one error envelope, FastAPI's ``Query``
validation for bounds, and the same dependency-injection pattern as
``api.dependencies.get_repository`` — so these routes get mocked tests that
need no database, exactly like the 39 that already exist.

**There is no new document listing or document detail endpoint.** The Library's
browse view is served by ``GET /api/documents`` and ``GET /api/documents/{id}``,
which already exist and were extended additively with ``date_from``,
``date_to`` and ``sort`` for this module. A second listing endpoint would be
two places defining what a document summary is.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from backend.app.api.errors import ApiError
from backend.app.api.schemas import ErrorResponse
from backend.app.library_search.schemas import (
    ClauseSearchResponse,
    DocumentSearchResponse,
    FacetsResponse,
    SuggestResponse,
)
from backend.app.library_search.service import (
    DEFAULT_SORT,
    DEFAULT_STRATEGY,
    InvalidQuery,
    SearchService,
)

__all__ = ["search_router", "library_router", "get_search_service"]


def get_search_service() -> SearchService:
    """The default service, bound to the configured database.

    Overridden wholesale in tests via ``app.dependency_overrides``, which is
    what lets the search endpoints be tested without PostgreSQL.
    """
    return SearchService()


search_router = APIRouter(prefix="/search", tags=["search"])
library_router = APIRouter(prefix="/library", tags=["library"])

#: Reuses the existing error model so the OpenAPI document shows one envelope.
ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "Validation failed."},
    500: {"model": ErrorResponse, "description": "Internal error."},
}

_Q = Query(
    ...,
    min_length=1,
    max_length=300,
    description="The search query. Supports quoted phrases, OR and -exclusion "
    "(websearch_to_tsquery syntax).",
    examples=['capital adequacy', '"priority sector" lending', 'kyc -annexure'],
)

_STRATEGY = Query(
    DEFAULT_STRATEGY,
    pattern="^(fts|ilike)$",
    description="fts = PostgreSQL full-text over the GIN index (default). "
    "ilike = substring match, provided as the measured comparison baseline. "
    "semantic and hybrid are planned, not implemented (D-7).",
)

_SORT = Query(
    DEFAULT_SORT,
    pattern="^(relevance|date_desc|date_asc|title)$",
    description="Every order tie-breaks on a unique id, so pagination is stable.",
)

_METHOD = Query(
    "clause_aware",
    pattern="^(clause_aware|paragraph)$",
    description="Which segmentation to search. Both cover the same text, so "
    "one is searched at a time — never both, which would duplicate every hit.",
)

_SCOPE = Query(
    "latest",
    pattern="^(latest|all)$",
    description="latest = one version per document (default), so an amended "
    "policy does not return the same clause twice. all = every ok version.",
)


def _raise_invalid(exc: InvalidQuery) -> None:
    """Translate the service's domain error into the §12.1 envelope."""
    raise ApiError(
        getattr(exc, "code", "VALIDATION_ERROR"),
        str(exc),
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


@search_router.get(
    "",
    response_model=DocumentSearchResponse,
    summary="Search documents, ranked by their best matching clause",
    responses=ERROR_RESPONSES,
)
def search_documents(
    q: str = _Q,
    strategy: str = _STRATEGY,
    method: str = _METHOD,
    sort: str = _SORT,
    version_scope: str = _SCOPE,
    department: Optional[str] = Query(
        None, description="Exact match. Pass 'null' for documents with none stated."
    ),
    document_type: Optional[str] = Query(
        None, description="notification | circular | scheme | guideline | amendment | other"
    ),
    date_from: Optional[str] = Query(None, description="ISO date, inclusive."),
    date_to: Optional[str] = Query(None, description="ISO date, inclusive."),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: SearchService = Depends(get_search_service),
) -> DocumentSearchResponse:
    """Document-level search: which policies are about this, and how strongly.

    A document matches if one of its clauses matches **or** its own title,
    reference number or department matches. Each hit carries
    ``matched_clauses`` and its single strongest clause as ``best_clause``,
    with that clause's real page provenance — so a result can be opened at
    the page the evidence is on.

    ``metadata_match`` distinguishes the two ways of matching. A document
    found by name alone comes back with ``best_clause = null``, which is a
    meaningful answer, not a missing field.
    """
    _check_dates(date_from, date_to)
    try:
        payload = service.search_documents(
            q,
            strategy=strategy,
            method=method,
            sort=sort,
            version_scope=version_scope,
            limit=limit,
            offset=offset,
            department=department,
            document_type=document_type,
            date_from=date_from,
            date_to=date_to,
        )
    except InvalidQuery as exc:
        _raise_invalid(exc)
    return DocumentSearchResponse(**payload)


@search_router.get(
    "/clauses",
    response_model=ClauseSearchResponse,
    summary="Search clauses and paragraphs, with page provenance on every hit",
    responses=ERROR_RESPONSES,
)
def search_clauses(
    q: str = _Q,
    strategy: str = _STRATEGY,
    method: str = _METHOD,
    sort: str = _SORT,
    version_scope: str = _SCOPE,
    document_id: Optional[str] = Query(
        None, description="Restrict to one document, e.g. DOC-0001."
    ),
    version_id: Optional[str] = Query(
        None, description="Restrict to one version. Overrides version_scope."
    ),
    department: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    segment_type: Optional[str] = Query(
        None,
        description="clause | paragraph | heading | annexure | preamble | unclassified",
    ),
    date_from: Optional[str] = Query(None, description="ISO date, inclusive."),
    date_to: Optional[str] = Query(None, description="ISO date, inclusive."),
    page_from: Optional[int] = Query(None, ge=1),
    page_to: Optional[int] = Query(None, ge=1),
    include_text: bool = Query(
        False,
        description="Include each segment's full text as well as the snippet. "
        "Off by default: a page of full clause texts is a large response.",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: SearchService = Depends(get_search_service),
) -> ClauseSearchResponse:
    """Clause-level search. Every result is traceable to a document and page.

    Each item carries ``page_start``/``page_end`` and a ``pages`` array with
    the character range on each page the segment touches, read from
    ``segment_pages`` — the same provenance ``GET /clauses`` serves, not a
    reconstruction. ``offset_basis`` names which page text those ranges index
    into (``normalized_text``); slicing a different field gives the wrong span.

    ``snippet`` wraps matched terms in ``[[hl]]`` / ``[[/hl]]`` sentinels. It
    is plain text, not HTML: the text comes out of a PDF, and handing a
    browser markup assembled from document content is how a document becomes
    a script.
    """
    _check_dates(date_from, date_to)
    if page_from is not None and page_to is not None and page_to < page_from:
        raise ApiError(
            "VALIDATION_ERROR",
            "page_to must be greater than or equal to page_from.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"page_from": page_from, "page_to": page_to},
        )
    try:
        payload = service.search_clauses(
            q,
            strategy=strategy,
            method=method,
            sort=sort,
            version_scope=version_scope,
            limit=limit,
            offset=offset,
            include_text=include_text,
            document_id=document_id,
            version_id=version_id,
            department=department,
            document_type=document_type,
            segment_type=segment_type,
            date_from=date_from,
            date_to=date_to,
            page_from=page_from,
            page_to=page_to,
        )
    except InvalidQuery as exc:
        _raise_invalid(exc)
    return ClauseSearchResponse(**payload)


@search_router.get(
    "/suggest",
    response_model=SuggestResponse,
    summary="Type-ahead suggestions from titles, reference numbers and departments",
    responses=ERROR_RESPONSES,
)
def suggest(
    q: str = Query(
        "",
        max_length=120,
        description="What the user has typed so far. Fewer than 2 characters "
        "returns an empty list rather than an error.",
    ),
    limit: int = Query(10, ge=1, le=25),
    service: SearchService = Depends(get_search_service),
) -> SuggestResponse:
    """Suggestions for the search box — never clause text.

    A clause fragment offered as a suggestion looks like an answer and carries
    no page reference to check it against, so only document identity fields
    are suggested.
    """
    return SuggestResponse(**service.suggest(q, limit=limit))


@library_router.get(
    "/facets",
    response_model=FacetsResponse,
    summary="Filter values and corpus counts, read from the database",
    responses=ERROR_RESPONSES,
)
def facets(
    service: SearchService = Depends(get_search_service),
) -> FacetsResponse:
    """Everything the Library needs to render its filters and counts.

    Every value and count is a ``GROUP BY`` over the real tables — no
    hardcoded vocabulary. ``null`` is a real bucket in ``departments`` and
    ``document_types``, because those columns are nullable precisely so that a
    document which does not print its department is recorded honestly; the UI
    shows it as "Not stated" and filtering on it works.
    """
    return FacetsResponse(**service.facets())


def _check_dates(date_from: Optional[str], date_to: Optional[str]) -> None:
    """Validate ISO dates here, not in SQL.

    A malformed date reaching PostgreSQL becomes a ``DataError`` and then a
    500 — an internal error for what is plainly a bad request. Parsing it
    first turns that into the 422 it should always have been.
    """
    from datetime import date

    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value is None:
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ApiError(
                "VALIDATION_ERROR",
                f"{name} must be an ISO-8601 date (YYYY-MM-DD), got {value!r}.",
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {name: value},
            ) from None
    if date_from and date_to and date_from > date_to:
        raise ApiError(
            "VALIDATION_ERROR",
            "date_from must not be later than date_to.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"date_from": date_from, "date_to": date_to},
        )
