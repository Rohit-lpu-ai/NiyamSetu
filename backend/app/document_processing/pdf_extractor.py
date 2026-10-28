"""PDF validation and page-by-page text extraction.

This module is the library. It has no command-line handling, no FastAPI import
and no database import, so it can be called from a test, a script, a future API
endpoint or a notebook without dragging anything else along.

Why pypdf
---------
``pypdf`` is a pure-Python PDF library, already present in this project's
toolchain, and it gives exactly the two things this phase needs: indexed access
to individual pages (``reader.pages[i]``), which is what makes page-level
provenance possible, and the document's ``/Info`` metadata dictionary. It pulls
in no compiled extension and no transitive dependency tree, which matters for a
five-person student project that has to run on five machines.

The honest trade-off: pypdf's text extraction is weaker than PyMuPDF's on
multi-column layouts and on tables, and it has no layout-analysis model. We
accept that for Phase 1 because the alternative is a heavier dependency chosen
before we have measured anything. Experiment A (``CLAUDE.md`` §15) is where
extractors get compared on our real corpus; the single boundary function
:func:`_extract_page_text` is the seam a second backend would plug into.

Pipeline order
--------------
1. Validate the path and the bytes (:func:`validate_pdf_file`).
2. Open the document, handling encryption and parse failures.
3. For every page, in order: extract raw text, normalise into a separate field,
   measure it, and classify it as empty / text-poor / meaningful.
4. Collect warnings and derive an overall ``extraction_status``.
5. Return an :class:`~.schemas.ExtractionResult`.

The raw text is never overwritten by the normalised text, and no page is ever
dropped or merged - the page list is always a complete 1..page_count sequence
(``CLAUDE.md`` §10.2).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import pypdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .errors import (
    EmptyFileError,
    FileTooLargeError,
    NotAPdfError,
    PdfEncryptedError,
    PdfHasNoPagesError,
    PdfUnreadableError,
    SourceFileNotFoundError,
)
from .identity import (
    compute_file_checksum,
    document_id_from_group,
    version_id_from_checksum,
)
from .normalization import NormalizationOptions, normalize_text
from .schemas import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    DocumentRecord,
    ExtractionResult,
    PageRecord,
    SourceMetadata,
)

logger = logging.getLogger(__name__)

#: Default upload / ingestion size cap, matching the proposal in
#: ``CLAUDE.md`` §12.3. 25 MB comfortably covers a long text notification while
#: still refusing something pathological.
DEFAULT_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

#: How many bytes to read when sniffing for the PDF header. The header should be
#: at byte 0, but real-world files sometimes carry a few junk bytes in front of
#: it, and readers tolerate that, so we scan a small window rather than
#: demanding an exact prefix. The *extension* is never consulted.
_HEADER_SNIFF_BYTES = 1024
_PDF_MAGIC = b"%PDF-"

#: A page needs at least this many letters or digits in its normalised text to
#: count as carrying meaningful text. Tuned by looking at real RBI circulars,
#: where even a short covering page runs to several hundred characters; a page
#: below this is either scanned, or image/signature only.
#:
#: This is a heuristic and it is reported as such. ``has_meaningful_text=False``
#: is a *signal to look*, not a verdict that the page is broken.
DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS = 30

#: PDF ``/Info`` keys we record. Anything else in the dictionary is ignored
#: rather than stored, so the output stays predictable.
_PDF_INFO_KEYS = (
    "/Title",
    "/Author",
    "/Subject",
    "/Keywords",
    "/Creator",
    "/Producer",
    "/CreationDate",
    "/ModDate",
    "/Company",
)


def extraction_tool_name() -> str:
    """Return the extractor identification string recorded in every output.

    ``CLAUDE.md`` §10.1 requires the *library and version actually used* to be
    stored, so a later re-run with a different pypdf can be told apart from this
    one.
    """
    return f"pypdf {pypdf.__version__}"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_pdf_file(
    path: Path,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> int:
    """Check that ``path`` is a plausible, readable PDF file.

    This checks the *bytes*, not the filename. A file called ``notice.pdf`` that
    actually contains an HTML error page is rejected here - which is not a
    hypothetical: the first attempt to download the RBI circulars in this
    project returned exactly that.

    Args:
        path: Location of the candidate file.
        max_file_size_bytes: Reject anything larger than this.

    Returns:
        The file size in bytes.

    Raises:
        SourceFileNotFoundError: Path missing, or not a regular file.
        EmptyFileError: Zero-byte file.
        FileTooLargeError: Larger than ``max_file_size_bytes``.
        NotAPdfError: No PDF header in the first bytes of the file.
    """
    if not path.exists():
        raise SourceFileNotFoundError(f"No such file: {path}")
    if not path.is_file():
        raise SourceFileNotFoundError(f"Not a regular file: {path}")

    size = path.stat().st_size
    if size == 0:
        raise EmptyFileError(f"File is empty (0 bytes): {path}")
    if size > max_file_size_bytes:
        raise FileTooLargeError(
            f"File is {size} bytes, which exceeds the "
            f"{max_file_size_bytes}-byte limit: {path}"
        )

    with path.open("rb") as handle:
        head = handle.read(_HEADER_SNIFF_BYTES)
    if _PDF_MAGIC not in head:
        raise NotAPdfError(
            f"File does not contain a PDF header in its first "
            f"{_HEADER_SNIFF_BYTES} bytes, so it is not a PDF whatever its "
            f"extension says: {path}"
        )
    return size


def _open_reader(path: Path) -> PdfReader:
    """Open a validated PDF and return a reader, or raise a typed error.

    Encryption is handled by attempting the empty password, which is what opens
    the common case of a PDF that is "encrypted" only to set permission flags.
    If that fails we stop: silently returning a document with no text would be
    worse than a clear error.
    """
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise PdfUnreadableError(f"pypdf could not parse {path}: {exc}") from exc
    except OSError as exc:
        raise PdfUnreadableError(f"Could not read {path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
        # pypdf raises a variety of parsing exceptions on malformed input and
        # does not guarantee they all derive from PdfReadError. We refuse to let
        # one escape untyped, but the original is kept as __cause__ so nothing
        # is hidden.
        raise PdfUnreadableError(
            f"Unexpected {type(exc).__name__} while opening {path}: {exc}"
        ) from exc

    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise PdfEncryptedError(
                f"{path} is encrypted and could not be opened with an empty "
                f"password ({type(exc).__name__}: {exc})"
            ) from exc
        if not opened:
            raise PdfEncryptedError(
                f"{path} is encrypted and requires a password, which this "
                f"pipeline does not accept."
            )
    return reader


def _page_count(reader: PdfReader, path: Path) -> int:
    try:
        count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
        raise PdfUnreadableError(
            f"Could not read the page tree of {path} "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if count < 1:
        raise PdfHasNoPagesError(f"{path} reports zero pages.")
    return count


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def _read_pdf_metadata(reader: PdfReader) -> Dict[str, Optional[str]]:
    """Read the PDF's own ``/Info`` dictionary, defensively.

    Values are stringified and blank strings are stored as ``None``, because an
    empty ``/Title`` is an absence, not a title. Nothing here is promoted to the
    document's real metadata; that comes from the manifest.
    """
    collected: Dict[str, Optional[str]] = {}
    try:
        info = reader.metadata
    except Exception as exc:  # noqa: BLE001 - metadata is optional, not fatal
        logger.warning("Could not read PDF /Info dictionary: %s", exc)
        return collected
    if not info:
        return collected
    for key in _PDF_INFO_KEYS:
        try:
            raw = info.get(key)
        except Exception:  # noqa: BLE001 - a single bad key must not stop us
            continue
        if raw is None:
            continue
        text = str(raw).strip()
        collected[key.lstrip("/").lower()] = text or None
    return collected


def _extract_page_text(reader: PdfReader, page_index: int) -> str:
    """Extract the text of one page. The single seam for swapping extractors.

    Returns an empty string when the page carries no extractable text, and also
    when extraction of that one page fails - the failure is turned into a
    warning by the caller rather than aborting the whole document, because one
    unreadable page out of thirty-seven is still a usable document.
    """
    page = reader.pages[page_index]
    text = page.extract_text()
    return text or ""


def _measure(normalized: str, min_alnum: int) -> tuple[int, bool, bool]:
    """Return ``(word_count, is_empty, has_meaningful_text)`` for a page.

    ``is_empty`` means literally nothing but whitespace came out. ``has_meaningful
    _text`` is the heuristic: enough letters or digits to be worth indexing.
    """
    stripped = normalized.strip()
    is_empty = not stripped
    word_count = len(stripped.split()) if stripped else 0
    alnum_count = sum(1 for ch in normalized if ch.isalnum())
    return word_count, is_empty, alnum_count >= min_alnum


def extract_pdf(
    path: Path | str,
    source_metadata: Optional[SourceMetadata] = None,
    normalization_options: Optional[NormalizationOptions] = None,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    min_meaningful_alnum_chars: int = DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS,
) -> ExtractionResult:
    """Validate and extract one PDF into the structured contract.

    Args:
        path: The PDF to process. It is opened read-only and never modified.
        source_metadata: Human-asserted metadata from ``data/manifest.csv``.
            Absent fields stay ``None``; nothing is guessed.
        normalization_options: Which normalisation steps to apply. Defaults to
            the documented conservative set.
        max_file_size_bytes: Validation size cap.
        min_meaningful_alnum_chars: Threshold for ``has_meaningful_text``.

    Returns:
        An :class:`~.schemas.ExtractionResult` with one page record per physical
        page, in order, numbered from 1.

    Raises:
        PdfIngestionError: Any anticipated validation or parse failure. The
            subclass and its ``code`` say which.
    """
    path = Path(path)
    source_metadata = source_metadata or SourceMetadata()
    normalization_options = normalization_options or NormalizationOptions()

    file_size = validate_pdf_file(path, max_file_size_bytes=max_file_size_bytes)
    checksum = compute_file_checksum(path)
    version_id = version_id_from_checksum(checksum)
    document_id = document_id_from_group(source_metadata.document_group, checksum)

    logger.info(
        "extract.start version_id=%s document_id=%s file=%s bytes=%d",
        version_id,
        document_id,
        path.name,
        file_size,
    )

    reader = _open_reader(path)
    page_count = _page_count(reader, path)
    pdf_metadata = _read_pdf_metadata(reader)

    warnings: List[str] = []
    pages: List[PageRecord] = []

    for index in range(page_count):
        page_number = index + 1
        try:
            original_text = _extract_page_text(reader, index)
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            # One bad page must not discard the other thirty-six. We record the
            # failure as a warning and store the page with empty text, so the
            # page sequence stays complete and provenance is not lost.
            original_text = ""
            warnings.append(
                f"page {page_number}: text extraction failed "
                f"({type(exc).__name__}: {exc})"
            )
            logger.warning(
                "extract.page_failed version_id=%s page=%d error=%s",
                version_id,
                page_number,
                exc,
            )

        normalized = normalize_text(original_text, normalization_options)
        word_count, is_empty, meaningful = _measure(
            normalized, min_meaningful_alnum_chars
        )

        if is_empty:
            warnings.append(
                f"page {page_number}: extracted 0 characters - likely a scanned "
                f"or image-only page (no OCR in this phase)"
            )
        elif not meaningful:
            warnings.append(
                f"page {page_number}: extracted only {word_count} words / "
                f"{len(normalized.strip())} characters - text-poor, inspect "
                f"manually before relying on it"
            )

        pages.append(
            PageRecord(
                page_number=page_number,
                original_text=original_text,
                normalized_text=normalized,
                char_count=len(original_text),
                normalized_char_count=len(normalized),
                word_count=word_count,
                is_empty=is_empty,
                has_meaningful_text=meaningful,
            )
        )

    status = _derive_status(pages)
    if status == STATUS_FAILED:
        warnings.append(
            "no page yielded meaningful text - this document is probably "
            "scanned and is not usable by the MVP corpus"
        )

    document = DocumentRecord(
        document_id=document_id,
        version_id=version_id,
        filename=path.name,
        checksum=checksum,
        file_size_bytes=file_size,
        page_count=page_count,
        extraction_status=status,
        extraction_tool=extraction_tool_name(),
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        pdf_metadata=pdf_metadata,
        source_metadata=source_metadata.as_dict(),
        normalization_options=normalization_options.as_dict(),
        warnings=warnings,
    )

    logger.info(
        "extract.done version_id=%s pages=%d meaningful=%d status=%s warnings=%d",
        version_id,
        page_count,
        sum(1 for page in pages if page.has_meaningful_text),
        status,
        len(warnings),
    )
    return ExtractionResult(document=document, pages=pages)


def _derive_status(pages: List[PageRecord]) -> str:
    """Map per-page outcomes onto the version-level ``extraction_status``.

    ``ok``       every page carries meaningful text.
    ``partial``  some pages do, some do not - the usual outcome for a document
                 with an image-only annexure or a signature page.
    ``failed``   no page carries meaningful text; the document is unusable.
    """
    meaningful = sum(1 for page in pages if page.has_meaningful_text)
    if meaningful == 0:
        return STATUS_FAILED
    if meaningful < len(pages):
        return STATUS_PARTIAL
    return STATUS_OK


def result_to_json(result: ExtractionResult, indent: int = 2) -> str:
    """Serialise a result to a JSON string.

    ``ensure_ascii=False`` keeps Devanagari and typographic punctuation readable
    in the file instead of turning it into escape sequences. The file is written
    as UTF-8 by the caller.
    """
    return json.dumps(result.to_dict(), indent=indent, ensure_ascii=False)
