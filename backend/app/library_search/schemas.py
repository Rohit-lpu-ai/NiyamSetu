"""Request/response models for Library & Search.

**Only what is genuinely new lives here.** ``backend/app/api/schemas.py``
already defines the document, page and clause payloads that ``CLAUDE.md`` §12
fixes for four consumers, and a second ``ClauseOut`` would be a second thing to
keep in step. What search adds is *ranking metadata* — a score, which strategy
produced it, a highlighted snippet, and the parsed query — so those are the
models below.

Two conventions carried over from the existing API, deliberately:

* identifiers are always ``public_id`` strings (``DOC-0001``, ``CLS-0000123``);
* ``extra="forbid"`` on every model whose payload comes straight from the
  repository, so a key the repository starts returning cannot slip silently
  into a response that nothing documents.

One convention that is new, and the reason it exists: a snippet is **not**
HTML. ``ts_headline`` wraps matches in markers of our choosing and escapes
nothing, so returning ``<mark>`` would mean handing a browser markup built
from document text. The markers here are sentinels (:data:`HIGHLIGHT_OPEN` /
:data:`HIGHLIGHT_CLOSE`) that a client splits on and renders as text nodes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "HIGHLIGHT_OPEN",
    "HIGHLIGHT_CLOSE",
    "SEARCH_STRATEGIES",
    "SEARCH_SORTS",
    "SEGMENTATION_METHODS",
    "VERSION_SCOPES",
    "PageOffset",
    "ClauseHit",
    "ClauseSearchResponse",
    "DocumentHit",
    "DocumentSearchResponse",
    "FacetValue",
    "FacetsResponse",
    "Suggestion",
    "SuggestResponse",
]

#: Sentinels ``ts_headline`` wraps a matched term in. Chosen because neither
#: appears anywhere in the corpus (verified against all 501 segments) and
#: neither is HTML, so a client cannot be tricked into rendering markup that
#: came out of a government PDF.
HIGHLIGHT_OPEN = "[[hl]]"
HIGHLIGHT_CLOSE = "[[/hl]]"

SEARCH_STRATEGIES = Literal["fts", "ilike"]
SEARCH_SORTS = Literal["relevance", "date_desc", "date_asc", "title"]
SEGMENTATION_METHODS = Literal["clause_aware", "paragraph"]
VERSION_SCOPES = Literal["latest", "all"]


class PageOffset(BaseModel):
    """One page a segment touches, with the character range on that page.

    Identical in shape to ``api.schemas.PageOffsetModel`` because it is the
    same data from the same table — a search hit is citable on exactly the
    terms ``GET /clauses`` already guarantees.
    """

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    line_index_start: int = Field(ge=0)
    line_index_end: int = Field(ge=0)


class ClauseHit(BaseModel):
    """One matched clause or paragraph, with everything needed to cite it."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(examples=["CLS-0000123"])
    score: float = Field(
        description="Rank from ts_rank_cd, normalisation 1|32, so 0 < score < 1. "
        "Comparable within one result set only — never across queries."
    )
    snippet: str = Field(
        description=f"Matched text with terms wrapped in {HIGHLIGHT_OPEN}…"
        f"{HIGHLIGHT_CLOSE}. Plain text, not HTML."
    )

    segment_type: str = Field(
        description="clause | paragraph | heading | annexure | preamble | unclassified"
    )
    segmentation_method: str
    ordinal: int = Field(ge=0)
    clause_number: Optional[str] = Field(
        default=None, description="As printed. Null when the segment is unnumbered."
    )
    clause_number_ambiguous: bool = Field(
        description="'(i)' is both roman one and letter nine. Flagged, not resolved."
    )
    heading: Optional[str] = None
    level: int = Field(ge=0)

    #: Full segment text, present only when ``include_text=true`` was asked
    #: for. Omitted by default: 363 clause texts is a large response for a
    #: result list that only displays snippets.
    text: Optional[str] = None

    # --- provenance: never derived, always from segments / segment_pages ---
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    pages: List[PageOffset] = Field(
        description="One entry per page the segment actually touches. A clause "
        "spanning two pages arrives with both."
    )
    offset_basis: str = Field(
        description="Which page text the character ranges index into."
    )

    # --- the document this came from, so a hit renders without a second call ---
    document_id: str
    document_title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    version_id: str
    version_label: str
    publication_date: Optional[str] = None


class _SearchEnvelope(BaseModel):
    """Fields every search response carries, so a result is attributable."""

    query: str
    strategy: str = Field(description="Which retrieval condition produced this.")
    parsed_query: Optional[str] = Field(
        default=None,
        description="The tsquery PostgreSQL actually ran, or null for ILIKE. "
        "Shown because 'why did that match?' is otherwise unanswerable.",
    )
    sort: str
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    took_ms: float = Field(ge=0)
    warnings: List[str] = Field(default_factory=list)


class ClauseSearchResponse(_SearchEnvelope):
    model_config = ConfigDict(extra="forbid")

    segmentation_method: str
    version_scope: str
    items: List[ClauseHit]


class DocumentHit(BaseModel):
    """One matched document, with its best piece of clause evidence."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None

    version_id: Optional[str] = Field(
        default=None, description="The version the evidence came from."
    )
    version_label: Optional[str] = None
    publication_date: Optional[str] = None
    page_count: Optional[int] = None
    version_count: int = Field(ge=0)
    extraction_status: Optional[str] = None

    score: float = Field(
        description="The best clause score in this document, plus a flat "
        "bonus when the title or reference number matches. See the service "
        "docstring for the exact rule."
    )
    matched_clauses: int = Field(
        ge=0, description="How many clauses in the document matched."
    )
    metadata_match: bool = Field(
        description="True when the title, reference number or department "
        "matched — so a document can be found by name with no clause hit."
    )
    best_clause: Optional[ClauseHit] = Field(
        default=None,
        description="The strongest matching clause, with full provenance. "
        "Null when the document matched on metadata alone.",
    )


class DocumentSearchResponse(_SearchEnvelope):
    model_config = ConfigDict(extra="forbid")

    segmentation_method: str
    version_scope: str
    items: List[DocumentHit]


class FacetValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = Field(
        description="Null is a real bucket: department and document_type are "
        "nullable in the schema because real documents omit them."
    )
    label: str = Field(description="Display form. 'Not stated' for null.")
    count: int = Field(ge=0)


class FacetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: int = Field(ge=0)
    versions: int = Field(ge=0)
    pages: int = Field(ge=0)
    segments: Dict[str, int] = Field(
        description="Segment counts per segmentation method."
    )
    departments: List[FacetValue]
    document_types: List[FacetValue]
    publication_years: List[FacetValue]
    date_range: Dict[str, Optional[str]] = Field(
        description="Earliest and latest publication_date present, or nulls."
    )
    extraction_statuses: List[FacetValue]


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    kind: str = Field(description="title | reference_no | department")
    document_id: Optional[str] = None


class SuggestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    items: List[Suggestion]


#: Not a model: the shape the service hands back for an invalid request is the
#: API's error envelope, built in ``api/errors.py``. Recorded here so a reader
#: looking for a ``SearchError`` model knows there deliberately is not one.
_ERROR_SHAPE_LIVES_IN: Dict[str, Any] = {"module": "backend.app.api.errors"}
