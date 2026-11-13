"""Ingestion: one uploaded PDF, through the existing pipeline, into Postgres.

**This module orchestrates; it does not process.** Every step is an existing,
tested function, called in the same order and with the same arguments the
Phase 1 and Phase 2 CLIs use:

    validate_pdf_file   -> type, size and emptiness checks
    extract_pdf         -> per-page text + page provenance   (Phase 1)
    result_to_json      -> the extraction.json contract
    segment_extraction  -> cleaning + BOTH segmentation methods (Phase 2)
    result_payload      -> the segments_*.json contract
    load_version_directory -> one transaction into PostgreSQL  (Phase 3)

Duplicating any of that here would create a second code path whose output
could drift from the CLI's, and the whole corpus would then depend on which
route a document happened to arrive by.

**Why it writes artefacts to disk rather than loading from memory.** The loader
reads a version directory, and that is deliberate -- the JSON files are what
Persons 2-5 read, what `evaluation/data_quality.py` checks, and what makes an
ingestion reproducible after the fact. An in-memory path would save a few file
writes and lose the artefact that makes the run auditable. So an upload
produces exactly what the CLI produces, in the same place, and is then loaded
by the same loader.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.db.loader import (
    CLEANING_FILENAME,
    EXTRACTION_FILENAME,
    SEGMENT_FILENAMES,
    LoadOutcome,
    load_version_directory,
)
from backend.app.document_processing import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    SourceMetadata,
    extract_pdf,
    result_to_json,
    validate_pdf_file,
)
from backend.app.document_processing.segmentation_cli import (
    result_payload,
    segment_extraction,
)

logger = logging.getLogger("niyamsetu.api.ingestion")

__all__ = ["IngestionRequest", "IngestionResult", "ingest_pdf_bytes"]


@dataclass(frozen=True)
class IngestionRequest:
    """Everything the caller supplies. Metadata mirrors ``SourceMetadata``."""

    filename: str
    content: bytes
    title: str
    document_group: str
    version_label: Optional[str] = None
    issuing_department: Optional[str] = None
    document_type: Optional[str] = None
    reference_no: Optional[str] = None
    publication_date: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None

    def to_source_metadata(self) -> SourceMetadata:
        return SourceMetadata(
            title=self.title,
            issuing_department=self.issuing_department,
            document_type=self.document_type,
            reference_no=self.reference_no,
            publication_date=self.publication_date,
            source_url=self.source_url,
            retrieved_at=self.retrieved_at,
            document_group=self.document_group,
            version_label=self.version_label,
            supersedes=None,
        )


@dataclass
class IngestionResult:
    """The load outcome plus the figures the response reports."""

    outcome: LoadOutcome
    checksum: str
    page_count: int
    extraction_status: str
    offset_basis: str
    warnings: list


def ingest_pdf_bytes(
    request: IngestionRequest,
    *,
    processed_dir: Path,
    raw_dir: Path,
    url: Optional[str] = None,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> IngestionResult:
    """Run the full pipeline for one upload and persist it.

    Raises whatever the pipeline raises -- ``NotAPdfError``,
    ``FileTooLargeError``, ``LoadError``, ``VersionConflict`` and so on. The
    API's exception handlers map each stable ``code`` to a status, so this
    function never has to know about HTTP.
    """
    with tempfile.TemporaryDirectory(prefix="niyamsetu-upload-") as staging:
        # Written under a generated name, never the client's filename:
        # a user-supplied name must not reach a filesystem path
        # (``CLAUDE.md`` §20).
        staged = Path(staging) / "upload.pdf"
        staged.write_bytes(request.content)

        # Type, size and emptiness, before anything tries to parse it.
        validate_pdf_file(staged, max_file_size_bytes=max_file_size_bytes)

        extraction = extract_pdf(
            staged,
            source_metadata=request.to_source_metadata(),
            max_file_size_bytes=max_file_size_bytes,
        )
        payload: Dict[str, Any] = json.loads(result_to_json(extraction))

        document = payload["document"]
        version_fingerprint = str(document["version_id"])

        # Both segmentation methods, plus the reversible cleaning report.
        results, cleaning_report = segment_extraction(payload)

        # Artefacts are built in the staging directory and only published into
        # `processed_dir` once the load has actually happened.
        #
        # Writing them into place first looks harmless and is not: a re-upload
        # of a file already stored is correctly skipped by the loader, but the
        # artefacts would already have been overwritten with whatever metadata
        # this request supplied. Found the hard way -- a re-upload with a
        # sparser form body silently replaced a corpus document's
        # source_url/retrieved_at/version_label and broke the data-quality
        # check, while the API reported "nothing changed".
        staged_version_dir = Path(staging) / version_fingerprint
        staged_version_dir.mkdir(parents=True, exist_ok=True)
        _write(staged_version_dir / EXTRACTION_FILENAME, payload)
        _write(staged_version_dir / CLEANING_FILENAME, cleaning_report)
        for method, filename in SEGMENT_FILENAMES.items():
            _write(
                staged_version_dir / filename,
                result_payload(results[method], payload),
            )

        # Keep the source PDF: `document_versions.stored_path` points at it,
        # and a stored path naming a file that was deleted is a broken
        # provenance link. Never overwrite an existing one, for the same
        # reason the artefacts are not overwritten.
        raw_dir.mkdir(parents=True, exist_ok=True)
        stored_pdf = raw_dir / str(document["filename"])
        if not stored_pdf.exists():
            shutil.copyfile(staged, stored_pdf)

        outcome = load_version_directory(
            staged_version_dir, url=url, raw_dir=raw_dir, replace=False
        )

        if outcome.status in ("loaded", "replaced"):
            published = processed_dir / version_fingerprint
            published.mkdir(parents=True, exist_ok=True)
            for artefact in staged_version_dir.iterdir():
                shutil.copyfile(artefact, published / artefact.name)
        logger.info(
            "ingested %s as %s (%s)",
            version_fingerprint,
            outcome.version_public_id,
            outcome.status,
        )

        return IngestionResult(
            outcome=outcome,
            checksum=str(document["checksum"]),
            page_count=int(document["page_count"]),
            extraction_status=str(document["extraction_status"]),
            offset_basis=str(cleaning_report["offset_basis"]),
            warnings=list(document.get("warnings") or []),
        )


def _write(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
