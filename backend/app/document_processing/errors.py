"""Exception hierarchy for the PDF ingestion pipeline.

Every failure that we can anticipate gets its own exception class carrying a
stable ``code``. Those codes are the same strings the future FastAPI layer will
put in the error envelope (``CLAUDE.md`` §12.1), so Phase 4 can map an exception
straight onto an HTTP response without inventing a second vocabulary.

Design note for beginners: we deliberately avoid a bare ``except Exception`` in
the pipeline. When something unexpected happens we wrap it in
:class:`PdfUnreadableError` and keep the original exception as ``__cause__``, so
the real reason is never swallowed.
"""

from __future__ import annotations


class PdfIngestionError(Exception):
    """Base class for every anticipated ingestion failure.

    Attributes:
        code: Stable machine-readable identifier, reused by the API layer.
        message: Human-readable explanation, safe to show to a caller.
    """

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class SourceFileNotFoundError(PdfIngestionError):
    """The path does not exist, or is not a regular file."""

    code = "SOURCE_FILE_NOT_FOUND"


class EmptyFileError(PdfIngestionError):
    """The file exists but contains zero bytes."""

    code = "EMPTY_FILE"


class FileTooLargeError(PdfIngestionError):
    """The file exceeds the configured size cap."""

    code = "FILE_TOO_LARGE"


class NotAPdfError(PdfIngestionError):
    """The file does not carry a PDF header, whatever its extension says."""

    code = "UNSUPPORTED_FILE_TYPE"


class PdfEncryptedError(PdfIngestionError):
    """The PDF is encrypted and cannot be opened without a password."""

    code = "PDF_ENCRYPTED"


class PdfUnreadableError(PdfIngestionError):
    """The PDF header is present but the file could not be parsed."""

    code = "EXTRACTION_FAILED"


class PdfHasNoPagesError(PdfIngestionError):
    """The PDF parsed, but reports zero pages."""

    code = "PDF_HAS_NO_PAGES"


class SegmentProvenanceError(PdfIngestionError):
    """A segment could not establish, or violated, its page provenance.

    This is the Phase 2 counterpart of the "never produce a clause without a
    page range" rule (``CLAUDE.md`` §10.2). It is raised rather than worked
    around: a segment carrying a guessed page number would be indistinguishable
    from a correct one, and the project's whole claim is that a citation can be
    checked. Failing loudly costs an ingestion run; a wrong page reference costs
    the credibility of every answer built on it.
    """

    code = "SEGMENT_PROVENANCE_ERROR"


class OutputExistsError(PdfIngestionError):
    """Refusing to overwrite an existing output file (see CLI ``--force``)."""

    code = "OUTPUT_EXISTS"
