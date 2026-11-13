"""Document processing: extraction, cleaning and segmentation.

Phases 1 and 2 of the NiyamSetu backend. It deliberately knows nothing about
FastAPI or PostgreSQL, so it can be imported and tested on its own.

The pipeline, in order:

1. :func:`extract_pdf` - validate a PDF and extract page text, raw and
   normalised side by side (Phase 1).
2. :func:`clean_pages` - remove running headers, footers and printed page
   numbers, reversibly and with the evidence recorded (Phase 2).
3. :func:`build_source_lines` - turn cleaned pages into addressable lines that
   still carry their character range in the uncleaned page text.
4. :func:`segment_paragraphs` and :func:`segment_clauses` - the baseline and
   clause-aware segmentation methods, both run on every version and stored side
   by side (Phase 2).

Everything downstream (the API in Phase 4, the four NLP modules) consumes
:class:`ExtractionResult` and :class:`SegmentationResult`, or their JSON forms.
"""

from .errors import (
    EmptyFileError,
    FileTooLargeError,
    NotAPdfError,
    OutputExistsError,
    PdfEncryptedError,
    PdfHasNoPagesError,
    PdfIngestionError,
    PdfUnreadableError,
    SegmentProvenanceError,
    SourceFileNotFoundError,
)
from .identity import (
    compute_bytes_checksum,
    compute_file_checksum,
    document_id_from_group,
    segment_id_from_parts,
    version_id_from_checksum,
)
from .normalization import NormalizationOptions, normalize_text
from .page_cleaning import (
    CleanedPage,
    CleaningOptions,
    CleaningPlan,
    RemovedLine,
    clean_pages,
    detect_running_lines,
)
from .pdf_extractor import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS,
    extract_pdf,
    extraction_tool_name,
    result_to_json,
    validate_pdf_file,
)
from .segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
    OFFSET_BASIS_NORMALIZED,
    OFFSET_BASIS_ORIGINAL,
    ClauseMarker,
    PageOffset,
    Segment,
    SegmentationResult,
    SourceLine,
    build_source_lines,
    detect_clause_marker,
    segment_clauses,
    segment_paragraphs,
)
from .schemas import (
    SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    DocumentRecord,
    ExtractionResult,
    PageRecord,
    SourceMetadata,
)

__all__ = [
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS",
    "METHOD_CLAUSE_AWARE",
    "METHOD_PARAGRAPH",
    "OFFSET_BASIS_NORMALIZED",
    "OFFSET_BASIS_ORIGINAL",
    "SCHEMA_VERSION",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "ClauseMarker",
    "CleanedPage",
    "CleaningOptions",
    "CleaningPlan",
    "DocumentRecord",
    "EmptyFileError",
    "ExtractionResult",
    "FileTooLargeError",
    "NormalizationOptions",
    "NotAPdfError",
    "OutputExistsError",
    "PageOffset",
    "PageRecord",
    "PdfEncryptedError",
    "PdfHasNoPagesError",
    "PdfIngestionError",
    "PdfUnreadableError",
    "SourceFileNotFoundError",
    "RemovedLine",
    "Segment",
    "SegmentProvenanceError",
    "SegmentationResult",
    "SourceLine",
    "SourceMetadata",
    "build_source_lines",
    "clean_pages",
    "compute_bytes_checksum",
    "compute_file_checksum",
    "detect_clause_marker",
    "detect_running_lines",
    "document_id_from_group",
    "extract_pdf",
    "extraction_tool_name",
    "normalize_text",
    "result_to_json",
    "segment_clauses",
    "segment_id_from_parts",
    "segment_paragraphs",
    "validate_pdf_file",
    "version_id_from_checksum",
]
