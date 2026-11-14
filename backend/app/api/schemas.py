"""Pydantic request/response models for the NiyamSetu API.

**Every field here maps to something the repository actually returns.** The
models were written against the live payloads from
``backend/app/db/repository.py``, not from the §12 sketch, so what the schema
promises and what the database holds cannot drift. Where §12's illustrative
example named a field the pipeline does not produce, the real field wins and
the difference is noted in ``backend/docs/api.md``.

One deliberate choice worth stating: identifiers are always the ``public_id``
strings (``DOC-0001``, ``VER-0001``, ``CLS-0000123``), never database
integers (``CLAUDE.md`` §12). The row ids never leave the repository layer.

A single file rather than a ``schemas/`` package: these are ~200 lines of
declarations that are read together, and splitting them across four modules
would add imports without adding clarity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ErrorBody",
    "ErrorResponse",
    "HealthResponse",
    "PageOffsetModel",
    "ClauseModel",
    "ClauseListResponse",
    "PageModel",
    "PageListResponse",
    "DocumentSummary",
    "DocumentListResponse",
    "VersionDetail",
    "DocumentDetail",
    "IngestResponse",
    "SEGMENTATION_METHODS",
    "TEXT_VARIANTS",
]

SEGMENTATION_METHODS = Literal["clause_aware", "paragraph"]
TEXT_VARIANTS = Literal["normalized", "raw", "cleaned", "both"]


class ErrorBody(BaseModel):
    code: str = Field(examples=["DOCUMENT_NOT_FOUND"])
    message: str = Field(examples=["No document with id DOC-0042."])
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """The one error shape every non-2xx response uses (``CLAUDE.md`` §12.1)."""

    error: ErrorBody


class HealthResponse(BaseModel):
    """Liveness plus database reachability, reported separately.

    ``status`` is about this process; ``database`` is about PostgreSQL. They
    are two different questions and collapsing them into one field is how a
    health check comes to report "ok" for an application that cannot serve a
    single document.
    """

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]
    version: str


# --------------------------------------------------------------------------- #
# Clauses / segments
# --------------------------------------------------------------------------- #


class PageOffsetModel(BaseModel):
    """One page a segment touches, with the character range on that page.

    A multi-page clause has one of these per page. The range is a half-open
    ``[char_start, char_end)`` slice into the page text named by the version's
    ``offset_basis`` -- which is ``normalized_text`` (D-11).
    """

    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    line_index_start: int = Field(ge=0)
    line_index_end: int = Field(ge=0)


class ClauseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(examples=["CLS-0000123"])
    #: The pipeline's content-derived id (``SEG-`` + 16 hex). Stable across
    #: re-segmentation; ``clause_id`` is what a citation quotes.
    fingerprint: str
    ordinal: int = Field(ge=0)
    segment_type: str = Field(
        description="paragraph | clause | heading | annexure | preamble | unclassified"
    )
    clause_number: Optional[str] = Field(
        default=None, description="As printed: '3.2', '(iv)'. Null when unnumbered."
    )
    clause_number_ambiguous: bool = Field(
        description="'(i)' is both roman one and letter nine. Flagged, not resolved."
    )
    heading: Optional[str] = None
    text: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    pages: List[PageOffsetModel]
    level: int = Field(ge=0)
    marker_style: Optional[str] = None
    parent_clause_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ClauseListResponse(BaseModel):
    document_id: str
    version_id: str
    segmentation_method: str
    offset_basis: str = Field(
        description="Which page text the offsets index into. Always 'normalized_text'."
    )
    total: int
    limit: int
    offset: int
    items: List[ClauseModel]


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


class PageModel(BaseModel):
    """One page. Which text fields appear depends on the ``text`` query param.

    ``raw_text`` is the extractor's verbatim output and is never rewritten;
    ``normalized_text`` is what the offsets are measured against;
    ``cleaned_text`` is that minus running headers and footers.
    """

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    char_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    is_empty: bool
    has_meaningful_text: bool = Field(
        description="Heuristic. False is a signal to look, not a verdict."
    )
    raw_text: Optional[str] = None
    normalized_text: Optional[str] = None
    cleaned_text: Optional[str] = None


class PageListResponse(BaseModel):
    document_id: str
    version_id: str
    offset_basis: str
    page_count: int
    total: int
    limit: int
    offset: int
    items: List[PageModel]


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


class DocumentSummary(BaseModel):
    """One row of the Library listing.

    The last five fields were added for the Library & Search module. They all
    describe the **latest ok-extracted version** -- the one a reader means by
    "the document" -- and they are null when a document has no such version,
    which is a real state (a failed or partial extraction) and not a gap to
    paper over. ``version_count`` counts every version regardless.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(examples=["DOC-0001"])
    title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    version_count: int
    latest_publication_date: Optional[str] = None
    latest_version_id: Optional[str] = None
    latest_version_label: Optional[str] = None
    page_count: Optional[int] = Field(
        default=None, description="Pages in the latest ok-extracted version."
    )
    extraction_status: Optional[str] = Field(
        default=None, description="Of the latest ok-extracted version."
    )
    clause_count: Optional[int] = Field(
        default=None, description="clause_aware segments in that version."
    )


class DocumentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[DocumentSummary]


class VersionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(examples=["VER-0001"])
    version_label: str
    publication_date: Optional[str] = None
    page_count: int
    extraction_status: str = Field(description="pending | ok | partial | failed")
    extraction_tool: str
    offset_basis: str
    source_url: Optional[str] = None
    file_checksum: str = Field(description="SHA-256 of the source PDF.")
    processing_warnings: List[Any] = Field(default_factory=list)
    ingested_at: Optional[str] = None
    supersedes_version_id: Optional[str] = None
    segment_counts: Dict[str, int]


class DocumentDetail(BaseModel):
    document_id: str
    title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    versions: List[VersionDetail]


class IngestResponse(BaseModel):
    """What ingestion reports back.

    ``status`` distinguishes a fresh load from a duplicate that was recognised
    and skipped -- both are 2xx, because neither is an error, but a client that
    cannot tell them apart will double-count its corpus.
    """

    status: Literal["loaded", "skipped_duplicate", "replaced"]
    document_id: Optional[str] = None
    version_id: Optional[str] = None
    checksum: str
    page_count: int
    segment_counts: Dict[str, int]
    segment_total: int
    removed_lines: int
    extraction_status: str
    offset_basis: str
    warnings: List[Any] = Field(default_factory=list)
    message: Optional[str] = None
