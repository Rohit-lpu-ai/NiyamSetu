"""Document processing: PDF validation, page-level extraction and normalisation.

This is Phase 1 of the NiyamSetu backend. It deliberately knows nothing about
FastAPI or PostgreSQL, so it can be imported and tested on its own.

The public entry point is :func:`extract_pdf`. Everything downstream (the API in
Phase 4, segmentation in Phase 2, the NLP modules) consumes
:class:`ExtractionResult` or its JSON form.
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
    SourceFileNotFoundError,
)
from .identity import (
    compute_bytes_checksum,
    compute_file_checksum,
    document_id_from_group,
    version_id_from_checksum,
)
from .normalization import NormalizationOptions, normalize_text
from .pdf_extractor import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS,
    extract_pdf,
    extraction_tool_name,
    result_to_json,
    validate_pdf_file,
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
    "SCHEMA_VERSION",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "DocumentRecord",
    "EmptyFileError",
    "ExtractionResult",
    "FileTooLargeError",
    "NormalizationOptions",
    "NotAPdfError",
    "OutputExistsError",
    "PageRecord",
    "PdfEncryptedError",
    "PdfHasNoPagesError",
    "PdfIngestionError",
    "PdfUnreadableError",
    "SourceFileNotFoundError",
    "SourceMetadata",
    "compute_bytes_checksum",
    "compute_file_checksum",
    "document_id_from_group",
    "extract_pdf",
    "extraction_tool_name",
    "normalize_text",
    "result_to_json",
    "validate_pdf_file",
    "version_id_from_checksum",
]
