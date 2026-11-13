"""The six endpoints.

All six are thin: validate the query, call the repository or the ingestion
service, return the payload. There is no business logic here, because any
logic in a route is logic the CLI path does not share.

Routers are grouped in one module because together they are ~250 lines of
declarations. The services they call live in their own modules, which is where
the separation that matters actually is.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from backend.app.api.dependencies import (
    Repository,
    Settings,
    get_database_status,
    get_repository,
    get_settings,
)
from backend.app.api.errors import ApiError
from backend.app.api.ingestion import IngestionRequest, ingest_pdf_bytes
from backend.app.api.schemas import (
    ClauseListResponse,
    DocumentDetail,
    DocumentListResponse,
    ErrorResponse,
    HealthResponse,
    IngestResponse,
    PageListResponse,
)

__all__ = ["health_router", "documents_router"]

#: Reused on every documented route so Swagger shows the real error shape.
ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Validation or processing failure"},
    500: {"model": ErrorResponse, "description": "Internal error"},
}

health_router = APIRouter(tags=["health"])
documents_router = APIRouter(prefix="/documents", tags=["documents"])


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@health_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and database reachability",
)
def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    # Injected rather than called directly: a route that calls its collaborator
    # cannot have that collaborator replaced, which would make the
    # database-down branch untestable without a broken database.
    database: str = Depends(get_database_status),
) -> HealthResponse:
    """Report process health and database health **separately**.

    Returns 200 when both are fine and **503** when the database is
    unreachable. The API still answers in that state -- which is why ``status``
    becomes ``degraded`` rather than disappearing -- but it cannot serve a
    document, so reporting a flat ``ok`` would be a false green light.
    """
    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,  # type: ignore[arg-type]
        version=settings.api_version,
    )


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


@documents_router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF and ingest it as a document version",
    responses={
        **ERROR_RESPONSES,
        409: {"model": ErrorResponse, "description": "Conflicts with existing data"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Not a PDF"},
    },
)
async def ingest(
    response: Response,
    file: UploadFile = File(..., description="The source PDF."),
    title: str = Form(..., min_length=1, description="Document title."),
    document_group: str = Form(
        ...,
        min_length=1,
        description=(
            "Which logical document this version belongs to. A HUMAN decides "
            "this (D-9); it is never inferred from title similarity. Reusing "
            "an existing group attaches this upload as a new version of that "
            "document."
        ),
    ),
    version_label: Optional[str] = Form(None),
    issuing_department: Optional[str] = Form(None),
    document_type: Optional[str] = Form(None),
    reference_no: Optional[str] = Form(None),
    publication_date: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    retrieved_at: Optional[str] = Form(None),
    settings: Settings = Depends(get_settings),
    repository: Repository = Depends(get_repository),
) -> IngestResponse:
    """Extract, clean, segment and store one PDF, in one transaction.

    Synchronous (D-5): for a 10-40 page text PDF this is seconds, and the
    response stays meaningful. ``ingestion_jobs`` already exists in the schema
    so moving to a background task later is additive.

    A re-upload of a file already present is **not** an error -- it returns
    ``status="skipped_duplicate"`` and changes nothing. A file that conflicts
    with existing data (same id, different bytes) is a 409 and also changes
    nothing; it is never overwritten.
    """
    content = await file.read()
    if not content:
        raise ApiError(
            "EMPTY_FILE",
            "The uploaded file is empty.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if len(content) > settings.max_upload_bytes:
        # Checked before touching the disk. validate_pdf_file checks again on
        # the staged file; this one keeps an oversized body from being written
        # out at all.
        raise ApiError(
            "FILE_TOO_LARGE",
            f"File exceeds the {settings.max_upload_bytes} byte limit.",
            status.HTTP_413_CONTENT_TOO_LARGE,
            {"limit_bytes": settings.max_upload_bytes, "received_bytes": len(content)},
        )

    result = ingest_pdf_bytes(
        IngestionRequest(
            filename=file.filename or "upload.pdf",
            content=content,
            title=title,
            document_group=document_group,
            version_label=version_label,
            issuing_department=issuing_department,
            document_type=document_type,
            reference_no=reference_no,
            publication_date=publication_date,
            source_url=source_url,
            retrieved_at=retrieved_at,
        ),
        processed_dir=settings.processed_dir,
        raw_dir=settings.raw_dir,
        url=repository.url,
        max_file_size_bytes=settings.max_upload_bytes,
    )

    outcome = result.outcome
    if outcome.status == "skipped_duplicate":
        # 200, not 201: nothing was created. The distinction matters to a
        # client deciding whether it just added to the corpus.
        response.status_code = status.HTTP_200_OK

    return IngestResponse(
        status=outcome.status,  # type: ignore[arg-type]
        document_id=outcome.document_public_id,
        version_id=outcome.version_public_id,
        checksum=result.checksum,
        page_count=result.page_count,
        segment_counts=dict(outcome.segments),
        segment_total=outcome.total_segments,
        removed_lines=outcome.removed_lines,
        extraction_status=result.extraction_status,
        offset_basis=result.offset_basis,
        warnings=result.warnings,
        message=outcome.message,
    )


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


@documents_router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents",
    responses=ERROR_RESPONSES,
)
def list_documents(
    q: Optional[str] = Query(None, description="Substring of title or reference no."),
    department: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repository: Repository = Depends(get_repository),
) -> DocumentListResponse:
    """Paginated document list with per-document version counts.

    Returns metadata only -- no page text and no segments. The counts come
    from an aggregate, so listing a 37-page document costs the same as listing
    a 2-page one.
    """
    payload = repository.list_documents(
        q=q,
        department=department,
        document_type=document_type,
        limit=limit,
        offset=offset,
    )
    return DocumentListResponse(**payload)


@documents_router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="Document metadata and all its versions",
    responses=ERROR_RESPONSES,
)
def get_document(
    document_id: str,
    repository: Repository = Depends(get_repository),
) -> DocumentDetail:
    """Metadata plus every version, newest first.

    Each version carries its checksum, extraction status, extraction tool,
    offset basis, processing warnings and per-method segment counts -- enough
    for a consumer to decide whether to trust it before fetching any text.
    """
    return DocumentDetail(**repository.get_document(document_id))


@documents_router.get(
    "/{document_id}/pages",
    response_model=PageListResponse,
    summary="Page text for a version",
    responses=ERROR_RESPONSES,
)
def get_pages(
    document_id: str,
    version_id: Optional[str] = Query(
        None, description="Defaults to the latest version with extraction_status='ok'."
    ),
    page_from: Optional[int] = Query(None, ge=1),
    page_to: Optional[int] = Query(None, ge=1),
    text: str = Query(
        "normalized",
        pattern="^(normalized|raw|cleaned|both)$",
        description=(
            "Which text to return. 'raw' is the extractor's verbatim output; "
            "'normalized' is the offset basis; 'cleaned' has running "
            "headers/footers removed."
        ),
    ),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repository: Repository = Depends(get_repository),
) -> PageListResponse:
    """Pages for one version, paginated.

    ``page_from``/``page_to`` filter by printed position; ``limit``/``offset``
    paginate the filtered result. Both exist because they answer different
    questions -- "pages 3-5 of this document" and "the next 50 pages".
    """
    if page_from is not None and page_to is not None and page_to < page_from:
        raise ApiError(
            "VALIDATION_ERROR",
            "page_to must be greater than or equal to page_from.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"page_from": page_from, "page_to": page_to},
        )

    payload = repository.get_pages(
        document_id,
        version_public_id=version_id,
        page_from=page_from,
        page_to=page_to,
        text_variant=text,
    )
    items = payload["items"]
    total = len(items)
    window = items[offset : offset + limit]
    return PageListResponse(
        document_id=payload["document_id"],
        version_id=payload["version_id"],
        offset_basis=payload["offset_basis"],
        page_count=payload["page_count"],
        total=total,
        limit=limit,
        offset=offset,
        items=window,
    )


@documents_router.get(
    "/{document_id}/clauses",
    response_model=ClauseListResponse,
    summary="Clauses or paragraphs for a version, with page provenance",
    responses=ERROR_RESPONSES,
)
def get_clauses(
    document_id: str,
    version_id: Optional[str] = Query(None),
    method: str = Query(
        "clause_aware",
        pattern="^(clause_aware|paragraph)$",
        description="Both methods are stored for every version.",
    ),
    segment_type: Optional[str] = Query(
        None,
        description="Filter by type: clause, paragraph, heading, annexure, preamble, unclassified.",
    ),
    page_from: Optional[int] = Query(None, ge=1),
    page_to: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repository: Repository = Depends(get_repository),
) -> ClauseListResponse:
    """Segments for one version under one method.

    Every item carries its full page provenance: ``page_start``/``page_end``
    for citation, and a ``pages`` array with one entry per page the segment
    touches, each with the character range. A clause spanning pages 3-4
    arrives with **both** pages, never collapsed onto one.

    ``offset_basis`` on the response names which page text those character
    ranges index into. It is ``normalized_text`` (D-11), and a consumer that
    slices a different field will get the wrong span.
    """
    if page_from is not None and page_to is not None and page_to < page_from:
        raise ApiError(
            "VALIDATION_ERROR",
            "page_to must be greater than or equal to page_from.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"page_from": page_from, "page_to": page_to},
        )

    if segment_type is None:
        payload = repository.get_segments(
            document_id,
            version_public_id=version_id,
            method=method,
            page_from=page_from,
            page_to=page_to,
            limit=limit,
            offset=offset,
        )
        return ClauseListResponse(**payload)

    # Type filtering is not a repository capability, so it is done here over
    # the full method result and then paginated. Honest about the cost: this
    # reads every segment of the version (254 at most in the current corpus).
    # Pushing it into SQL is the right fix if the corpus grows; inventing a
    # repository function for it now would mean a second place defining the
    # clause payload.
    everything = repository.get_segments(
        document_id,
        version_public_id=version_id,
        method=method,
        page_from=page_from,
        page_to=page_to,
        limit=100,
        offset=0,
    )
    matching = [
        item for item in everything["items"] if item["segment_type"] == segment_type
    ]
    return ClauseListResponse(
        document_id=everything["document_id"],
        version_id=everything["version_id"],
        segmentation_method=everything["segmentation_method"],
        offset_basis=everything["offset_basis"],
        total=len(matching),
        limit=limit,
        offset=offset,
        items=matching[offset : offset + limit],
    )
