"""The data contract produced by the extraction pipeline.

This is the single most important file for the rest of the team: Persons 2-5
consume *this shape*, first as JSON files and later as PostgreSQL rows and API
responses. The field names here are chosen to line up with the proposed database
columns in ``CLAUDE.md`` §11.3 so that Phase 3 is a straight insert rather than a
translation.

Why plain dataclasses and not Pydantic
--------------------------------------
This package must stay importable with nothing but ``pypdf`` installed, and it
must be usable without FastAPI (the user requirement, and ``CLAUDE.md`` §24
rule 5 on keeping the stack small). Dataclasses plus explicit ``__post_init__``
checks give us the validation we actually need here. Pydantic models belong at
the API boundary in Phase 4, where request parsing and HTTP error mapping are
the job.

Serialisation rule
------------------
``to_dict()`` is the authoritative JSON contract. Nothing else should hand-build
these dictionaries, so there is exactly one place to change when the contract
changes (``CLAUDE.md`` §12.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: The contract version, bumped when a field is added, renamed or removed.
#: Downstream modules should read it rather than assuming a shape.
SCHEMA_VERSION = "1.0.0"

#: Allowed values for ``DocumentRecord.extraction_status``.
#:
#: These match ``document_versions.extraction_status`` in ``CLAUDE.md`` §11.3.
#: ``pending`` exists in the database (a row created before extraction runs) but
#: is never produced by this pipeline, which only returns a finished result.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
VALID_STATUSES = frozenset({STATUS_OK, STATUS_PARTIAL, STATUS_FAILED})


@dataclass
class SourceMetadata:
    """Metadata supplied by a human, from ``data/manifest.csv``.

    Every field is optional, and nothing here is ever invented by the pipeline.
    An absent value stays ``None`` and is counted as a documented gap by the
    data-quality checks (``CLAUDE.md`` §15, Experiment C). A guessed value would
    silently corrupt Person 2's classification labels and Person 5's filters,
    which is strictly worse than a blank.
    """

    title: Optional[str] = None
    issuing_department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    publication_date: Optional[str] = None  # ISO-8601 'YYYY-MM-DD' when known
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None  # ISO-8601 date or timestamp
    document_group: Optional[str] = None  # human-asserted key shared by versions
    version_label: Optional[str] = None  # 'v1', '2026-amendment', ...
    supersedes: Optional[str] = None  # the version_label this one replaces

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "title": self.title,
            "issuing_department": self.issuing_department,
            "document_type": self.document_type,
            "reference_no": self.reference_no,
            "publication_date": self.publication_date,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "document_group": self.document_group,
            "version_label": self.version_label,
            "supersedes": self.supersedes,
        }


@dataclass
class PageRecord:
    """One page of one version. The unit of provenance for the whole project.

    ``page_number`` is 1-based and follows the PDF's physical page order. It is
    deliberately *not* the page number printed on the page, which can differ
    (cover pages, annexures restarting at 1). Where they differ, the discrepancy
    is a note for the manifest, not a correction applied here.
    """

    page_number: int
    original_text: str
    normalized_text: str
    char_count: int
    normalized_char_count: int
    word_count: int
    is_empty: bool
    has_meaningful_text: bool

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError(
                f"page_number must be 1-based and >= 1, got {self.page_number}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "char_count": self.char_count,
            "normalized_char_count": self.normalized_char_count,
            "word_count": self.word_count,
            "is_empty": self.is_empty,
            "has_meaningful_text": self.has_meaningful_text,
        }


@dataclass
class DocumentRecord:
    """Document- and version-level facts about one extracted PDF.

    Document identity (``document_id``) is kept separate from version identity
    (``version_id``) even though this phase has no database: a policy has many
    versions, and conflating them would make Person 4's Compare feature
    impossible later. See :mod:`identity` for how each is derived.
    """

    document_id: str
    version_id: str
    filename: str
    checksum: str
    file_size_bytes: int
    page_count: int
    extraction_status: str
    extraction_tool: str
    extracted_at: str
    schema_version: str = SCHEMA_VERSION
    # Metadata read out of the PDF's own /Info dictionary. Recorded for
    # provenance, never trusted as the document's real title - RBI circulars,
    # for instance, ship an empty /Title.
    pdf_metadata: Dict[str, Optional[str]] = field(default_factory=dict)
    # Metadata asserted by a human.
    source_metadata: Dict[str, Optional[str]] = field(default_factory=dict)
    normalization_options: Dict[str, bool] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.extraction_status not in VALID_STATUSES:
            raise ValueError(
                f"extraction_status must be one of {sorted(VALID_STATUSES)}, "
                f"got {self.extraction_status!r}"
            )
        if self.page_count < 0:
            raise ValueError(f"page_count cannot be negative, got {self.page_count}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "filename": self.filename,
            "checksum": self.checksum,
            "file_size_bytes": self.file_size_bytes,
            "page_count": self.page_count,
            "extraction_status": self.extraction_status,
            "extraction_tool": self.extraction_tool,
            "extracted_at": self.extracted_at,
            "pdf_metadata": self.pdf_metadata,
            "source_metadata": self.source_metadata,
            "normalization_options": self.normalization_options,
            "warnings": self.warnings,
        }


@dataclass
class ExtractionResult:
    """The complete result of extracting one PDF: the contract downstream reads."""

    document: DocumentRecord
    pages: List[PageRecord]

    def __post_init__(self) -> None:
        expected = list(range(1, len(self.pages) + 1))
        actual = [page.page_number for page in self.pages]
        if actual != expected:
            shown = actual[:10]
            suffix = "..." if len(actual) > 10 else ""
            raise ValueError(
                "pages must be a complete 1-based consecutive sequence; "
                f"got {shown}{suffix}"
            )
        if self.document.page_count != len(self.pages):
            raise ValueError(
                f"document.page_count ({self.document.page_count}) does not match "
                f"the number of page records ({len(self.pages)})"
            )

    # -- convenience accessors used by the CLI and the validation report --

    @property
    def meaningful_page_count(self) -> int:
        return sum(1 for page in self.pages if page.has_meaningful_text)

    @property
    def empty_page_count(self) -> int:
        return sum(1 for page in self.pages if page.is_empty)

    @property
    def text_poor_page_numbers(self) -> List[int]:
        """Pages that parsed but carry too little text to be useful.

        This excludes fully empty pages, which are reported separately: a page
        holding only a signature block, a stamp or a single image is a
        legitimate page, not necessarily an error.
        """
        return [
            page.page_number
            for page in self.pages
            if not page.has_meaningful_text and not page.is_empty
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "pages": [page.to_dict() for page in self.pages],
        }
