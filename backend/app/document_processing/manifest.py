"""Reading ``data/manifest.csv`` - the human-maintained record of provenance.

The manifest, not the pipeline, is the authority on what a document *is*: its
title, its issuing department, which policy it is a version of, and where it
came from. The pipeline's job is to carry that record faithfully into the output
and never to improve on it (``data/README.md`` §4).

This module only reads and validates the file. It does no network access; the
corpus is downloaded by hand or by a separate fetch step, so that an ingestion
run is reproducible offline.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .schemas import SourceMetadata

#: Columns the manifest must have, from ``data/README.md`` §4.
REQUIRED_COLUMNS = frozenset(
    {
        "local_filename",
        "original_filename",
        "sha256",
        "title",
        "source_url",
        "retrieved_at",
        "document_group",
        "version_label",
    }
)

#: Columns that may be blank. A blank value becomes ``None``, which is recorded
#: as a documented gap - never filled in with a guess.
OPTIONAL_COLUMNS = frozenset(
    {
        "department",
        "document_type",
        "reference_no",
        "publication_date",
        "supersedes",
        "is_scanned",
        "notes",
    }
)


class ManifestError(Exception):
    """The manifest is missing, malformed, or missing a required column."""


@dataclass
class ManifestEntry:
    """One manifest row, resolved to an on-disk path."""

    pdf_path: Path
    source_metadata: SourceMetadata
    expected_sha256: Optional[str]
    original_filename: Optional[str]
    is_scanned: bool
    notes: Optional[str]


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def load_manifest(manifest_path: Path, raw_dir: Optional[Path] = None) -> List[ManifestEntry]:
    """Read the manifest and return one entry per row.

    Args:
        manifest_path: Path to ``manifest.csv``.
        raw_dir: Where ``local_filename`` values live. Defaults to the ``raw``
            directory beside the manifest, which is the layout in
            ``data/README.md`` §1.

    Returns:
        The manifest entries, in file order.

    Raises:
        ManifestError: The file is absent, has no header, or omits a required
            column. A row whose PDF is missing on disk is *not* an error here -
            the caller reports it per file, so one absent download does not
            block the rest of the corpus.
    """
    if not manifest_path.exists():
        raise ManifestError(f"No manifest at {manifest_path}")
    raw_dir = raw_dir or (manifest_path.parent / "raw")

    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ManifestError(f"{manifest_path} has no header row")
        present = {name.strip() for name in reader.fieldnames}
        missing = REQUIRED_COLUMNS - present
        if missing:
            raise ManifestError(
                f"{manifest_path} is missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        rows: List[Dict[str, str]] = list(reader)

    entries: List[ManifestEntry] = []
    for line_number, row in enumerate(rows, start=2):
        local_filename = _blank_to_none(row.get("local_filename"))
        if local_filename is None:
            raise ManifestError(
                f"{manifest_path}:{line_number} has a blank local_filename"
            )
        entries.append(
            ManifestEntry(
                pdf_path=raw_dir / local_filename,
                source_metadata=SourceMetadata(
                    title=_blank_to_none(row.get("title")),
                    issuing_department=_blank_to_none(row.get("department")),
                    document_type=_blank_to_none(row.get("document_type")),
                    reference_no=_blank_to_none(row.get("reference_no")),
                    publication_date=_blank_to_none(row.get("publication_date")),
                    source_url=_blank_to_none(row.get("source_url")),
                    retrieved_at=_blank_to_none(row.get("retrieved_at")),
                    document_group=_blank_to_none(row.get("document_group")),
                    version_label=_blank_to_none(row.get("version_label")),
                    supersedes=_blank_to_none(row.get("supersedes")),
                ),
                expected_sha256=_blank_to_none(row.get("sha256")),
                original_filename=_blank_to_none(row.get("original_filename")),
                is_scanned=(row.get("is_scanned") or "").strip().lower() == "true",
                notes=_blank_to_none(row.get("notes")),
            )
        )
    return entries
