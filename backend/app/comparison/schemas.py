"""Request/response models for Compare.

**Only what Compare genuinely adds lives here.** ``backend/app/api/schemas.py``
already fixes the document, page and clause payloads four consumers code
against, and a second ``ClauseOut`` would be a second thing to keep in step.
What Compare adds is *change metadata* -- a change type, how the two clauses
were matched, a similarity score with its runner-up, a word-level diff, and a
verified citation for each side -- so those are the models below.

Three conventions are carried over from the existing API deliberately:

* identifiers are always ``public_id`` strings (``DOC-0001``, ``VER-0002``,
  ``CLS-0000123``), never database integers;
* the error envelope is ``api.errors``'s single §12.1 shape, reused, not
  redefined;
* page provenance is reported as ``page_start``/``page_end`` **and** a per-page
  span list, never collapsed onto one page -- a clause spanning two pages
  arrives with both.

One convention is new, and it is the point of the module: **every side of
every change carries an ``evidence`` object with a ``status``.** A citation is
``resolved`` only when the stored character offsets were actually sliced out of
the stored page text and matched the clause. ``partial`` and ``unresolved``
are reported rather than hidden, because ``CLAUDE.md`` §5 forbids showing a
citation whose text does not support the statement, and a UI cannot honour
that rule if the backend has already smoothed the problem away.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CHANGE_TYPE",
    "CONFIDENCE",
    "MATCH_BASIS",
    "SEGMENTATION_METHOD",
    "EvidenceSpan",
    "Evidence",
    "ClauseSide",
    "DiffRun",
    "DiffStats",
    "EntityOut",
    "EntityDelta",
    "ChangeOut",
    "ComparisonSummary",
    "VersionSummary",
    "CompareRequest",
    "CompareResponse",
    "VersionPair",
    "VersionOption",
    "DocumentOption",
    "CompareOptionsResponse",
    "ClauseEntities",
    "EntitiesResponse",
    "EntityLabelsResponse",
]

CHANGE_TYPE = Literal["added", "removed", "modified", "unchanged"]
CONFIDENCE = Literal["high", "medium", "low"]
MATCH_BASIS = Literal["exact_text", "clause_number", "similarity"]
SEGMENTATION_METHOD = Literal["clause_aware", "paragraph"]
EVIDENCE_STATUS = Literal["resolved", "partial", "unresolved"]


class EvidenceSpan(BaseModel):
    """One page's character range for a clause, and what it actually sliced."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(..., ge=1)
    char_start: int
    char_end: int
    quote: Optional[str] = Field(
        None,
        description="The text the offsets sliced out of this page, or null if "
        "they could not be resolved.",
    )
    resolved: bool = Field(
        ..., description="Whether this individual span sliced cleanly."
    )


class Evidence(BaseModel):
    """A verified citation for one side of one change.

    ``status`` is the field to read before showing anything. ``quote`` is
    populated **only** when ``status == 'resolved'``, so a client that renders
    ``quote`` unconditionally still cannot display an unverified citation.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_id: str
    clause_id: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    offset_basis: str = Field(
        ...,
        description="Which page column the character offsets index into "
        "(normally normalized_text). Slicing a different column gives the "
        "wrong span.",
    )
    spans: List[EvidenceSpan] = Field(default_factory=list)
    status: EVIDENCE_STATUS
    resolved: bool
    problems: List[str] = Field(
        default_factory=list,
        description="Why the evidence is not fully resolved. Empty when it is.",
    )
    quote: Optional[str] = None


class EntityOut(BaseModel):
    """One extracted entity mention, with its span in the clause text."""

    model_config = ConfigDict(extra="forbid")

    label: str
    text: str
    start: int
    end: int


class ClauseSide(BaseModel):
    """One clause as it appears on one side of a comparison."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    ordinal: int
    clause_number: Optional[str] = None
    clause_number_ambiguous: bool = False
    heading: Optional[str] = None
    segment_type: str
    level: int = 0
    marker_style: Optional[str] = None
    text: Optional[str] = Field(
        None, description="Omitted when include_text=false was requested."
    )
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    pages: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="One entry per page the clause touches, with the "
        "character and line range on each.",
    )
    fingerprint: Optional[str] = None
    evidence: Evidence
    entities: Optional[List[EntityOut]] = None


class DiffRun(BaseModel):
    """One aligned run of words: kept, removed, inserted or replaced."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["equal", "delete", "insert", "replace"]
    old: str
    new: str


class DiffStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    words_added: int
    words_removed: int
    words_kept: int
    old_word_count: int
    new_word_count: int
    word_count_delta: int
    token_overlap: float


class EntityDelta(BaseModel):
    """How one entity label's mentions differ between the two clauses."""

    model_config = ConfigDict(extra="forbid")

    label: str
    friendly_label: str
    old: List[str]
    new: List[str]
    added: List[str]
    removed: List[str]


class ChangeOut(BaseModel):
    """One row of the comparison.

    ``similarity`` is ``null`` for ``added`` and ``removed``: there is no pair
    to score, and a number there would read as "probably really added".
    ``confidence`` is a label computed from stated rules, never a probability
    -- see ``alignment.confidence_label``.

    **A field withheld by an ``include_*`` flag comes back as ``null``, not
    absent.** ``include_diff=false`` yields ``"word_diff": null`` rather than
    omitting the key, and likewise for ``diff_stats``, ``entity_deltas``,
    ``explanation``, ``entities`` and a clause side's ``text``. That is
    deliberate: a consumer then has one thing to test (`is it null?`) instead
    of two (`is the key there, and is it null?`), and the OpenAPI schema
    stays an accurate description of every response rather than of the
    fullest one. The flags exist to cut response *size* on the wire, not to
    vary the response *shape*.
    """

    model_config = ConfigDict(extra="forbid")

    change_id: str
    change_type: CHANGE_TYPE
    similarity: Optional[float] = None
    runner_up_similarity: Optional[float] = None
    runner_up_clause_id: Optional[str] = None
    match_basis: Optional[MATCH_BASIS] = None
    confidence: CONFIDENCE
    ambiguous: bool
    renumbered: bool
    reordered: bool
    notes: List[str] = Field(default_factory=list)
    old: Optional[ClauseSide] = None
    new: Optional[ClauseSide] = None
    explanation: Optional[str] = None
    word_diff: Optional[List[DiffRun]] = None
    diff_stats: Optional[DiffStats] = None
    entity_deltas: Optional[List[EntityDelta]] = None

    @model_validator(mode="after")
    def _at_least_one_side(self) -> "ChangeOut":
        if self.old is None and self.new is None:
            raise ValueError("a change must carry an old clause, a new one, or both")
        return self


class ComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_changes: int
    counts: Dict[str, int]
    matched_pairs: int
    confidence_counts: Dict[str, int]
    match_basis_counts: Dict[str, int]
    ambiguous_matches: int
    renumbered_matches: int
    reordered_matches: int
    similarity_mean: Optional[float] = None
    similarity_mean_note: str
    unchanged_ratio: Optional[float] = None


class VersionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    document_id: str
    document_title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    document_reference_no: Optional[str] = Field(
        None,
        description="The **document's** reference number, not this "
        "version's. The schema stores reference_no on `documents`, so "
        "every issue of one circular shares a single value even though "
        "each is printed with its own. Named document_* so it cannot be "
        "read as version-level; the per-issue number is visible in the "
        "clause text, not in this field.",
    )
    version_label: str
    publication_date: Optional[str] = None
    page_count: int
    extraction_status: str
    offset_basis: str
    supersedes_version_id: Optional[str] = None
    source_url: Optional[str] = None
    file_checksum: str
    original_filename: str


class CompareRequest(BaseModel):
    """The POST body for ``/api/compare``.

    ``base_version_id`` is the older side and ``compare_version_id`` the
    newer one. If the stored evidence contradicts that, the service swaps them
    and the response carries a ``DIRECTION_SWAPPED`` warning -- it never
    reverses a diff silently, because that would invert every added and
    removed label without saying so.
    """

    model_config = ConfigDict(extra="forbid")

    base_version_id: str = Field(
        ..., description="The older version, e.g. VER-0002.", min_length=1
    )
    compare_version_id: str = Field(
        ..., description="The newer version, e.g. VER-0003.", min_length=1
    )
    document_id: Optional[str] = Field(
        None,
        description="Optional cross-check: fails with 422 if neither version "
        "belongs to this document.",
    )
    method: SEGMENTATION_METHOD = "clause_aware"
    allow_cross_document: bool = Field(
        False,
        description="Compare two versions that have not been declared "
        "versions of one another. Off by default; the result carries a "
        "CROSS_DOCUMENT warning when it is on.",
    )

    match_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    number_match_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    ambiguity_margin: Optional[float] = Field(None, ge=0.0, le=1.0)
    high_confidence_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)

    change_types: Optional[List[CHANGE_TYPE]] = None
    confidence: Optional[List[CONFIDENCE]] = None
    min_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    only_ambiguous: bool = False

    include_text: bool = True
    include_diff: bool = True
    include_entities: bool = False
    include_explanation: bool = True

    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


class CompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: str = Field(
        ...,
        description="Deterministic hash of the two version fingerprints, the "
        "method and every threshold. The same inputs always produce the same "
        "id, so a result can be cited and re-derived without being stored.",
    )
    base_version: VersionSummary
    compare_version: VersionSummary
    same_document: bool
    segmentation_method: SEGMENTATION_METHOD
    config: Dict[str, Any]
    summary: ComparisonSummary
    clause_counts: Dict[str, int]
    total: int
    total_unfiltered: int
    limit: int
    offset: int
    filters: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)
    took_ms: float
    items: List[ChangeOut]


class VersionPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: str
    compare_version_id: str
    base_label: str
    compare_label: str
    base_publication_date: Optional[str] = None
    compare_publication_date: Optional[str] = None
    consecutive: bool = Field(
        ..., description="True when no other version sits between the two."
    )
    supersedes_declared: bool = Field(
        ...,
        description="True when the newer version's own metadata names the "
        "older one as the version it supersedes. Asserted by a human at "
        "ingestion (D-9), never inferred from titles.",
    )


class VersionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    version_label: str
    publication_date: Optional[str] = None
    page_count: int
    clause_count: int
    supersedes_version_id: Optional[str] = None


class DocumentOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    version_count: Optional[int] = None
    versions: List[VersionOption]
    pairs: List[VersionPair]


class CompareOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: List[DocumentOption]
    corpus: Dict[str, int] = Field(
        ...,
        description="Corpus shape, so an empty list can explain itself: "
        "'17 documents, none with a second version' is usable, an empty "
        "array is indistinguishable from a broken query.",
    )
    comparable_pairs: int
    declared_supersedes_pairs: int
    methods: List[str]
    default_method: str
    defaults: Dict[str, Any]
    note: str


class ClauseEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    ordinal: int
    clause_number: Optional[str] = None
    page_start: int
    page_end: int
    entities: List[EntityOut]


class EntitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    document_id: str
    segmentation_method: SEGMENTATION_METHOD
    condition: str
    total: int
    limit: int
    offset: int
    mentions_by_label: Dict[str, int]
    took_ms: float
    note: str
    items: List[ClauseEntities]


class EntityLabelsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: Dict[str, str] = Field(
        ..., description="Label name -> what it covers, published so a "
        "consumer never has to guess."
    )
    condition: str
    limitations: List[str]
