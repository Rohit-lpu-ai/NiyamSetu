"""Command-line front end for the extraction pipeline.

All the real work lives in :mod:`pdf_extractor`. This file only parses
arguments, prints a short human-readable summary, writes the JSON and chooses an
exit code - so the pipeline stays testable without a shell.

Usage
-----
::

    python -m backend.app.document_processing.cli data/raw/example.pdf

    python -m backend.app.document_processing.cli data/raw/*.pdf \\
        --output-dir data/processed

    python -m backend.app.document_processing.cli data/raw/example.pdf \\
        --output data/processed/example.json --force

Exit codes
----------
``0`` every file processed; ``1`` at least one file failed; ``2`` bad arguments
(argparse's own convention).

By default the summary prints page counts, warnings and a short preview of one
page - never the whole document, which for a 37-page circular would flood the
terminal.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .errors import OutputExistsError, PdfIngestionError
from .manifest import ManifestError, load_manifest
from .normalization import NormalizationOptions
from .pdf_extractor import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS,
    extract_pdf,
    result_to_json,
)
from .schemas import ExtractionResult, SourceMetadata

#: Where outputs go when no path is given. One JSON file per version id, so
#: re-running on the same PDF targets the same file rather than piling up
#: copies (see ``data/README.md`` §3).
DEFAULT_OUTPUT_DIR = Path("data/processed")

#: Characters of page text shown in the terminal preview.
_PREVIEW_CHARS = 300


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.document_processing.cli",
        description=(
            "Extract page-by-page text from one or more government policy PDFs "
            "and write the structured JSON that later phases load into "
            "PostgreSQL."
        ),
    )
    parser.add_argument(
        "pdf_paths",
        nargs="*",
        type=Path,
        metavar="PDF",
        help="One or more PDF files to process. The files are never modified.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Process every row of data/manifest.csv instead of named files, "
            "taking each document's metadata from its manifest row. This is the "
            "reproducible way to ingest the corpus; the flags below are for "
            "one-off runs."
        ),
    )
    parser.add_argument(
        "--verify-checksums",
        action="store_true",
        help=(
            "With --manifest, fail any file whose SHA-256 does not match the "
            "recorded value. A mismatch means the document was re-published or "
            "altered - report it, do not silently update the manifest."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Write the JSON to this exact path. Only valid with a single input "
            "file. Default: <output-dir>/<version_id>/extraction.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated JSON (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing output file. Without this flag an existing "
            "output is left untouched and the file is reported as skipped."
        ),
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Process and summarise, but do not write any file.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help=(
            f"Show the first {_PREVIEW_CHARS} characters of the first page that "
            f"carries meaningful text."
        ),
    )

    metadata = parser.add_argument_group(
        "source metadata",
        "Human-asserted provenance. Anything you do not supply stays null - "
        "the pipeline never guesses these values.",
    )
    metadata.add_argument("--title", default=None, help="Title as printed on the document.")
    metadata.add_argument("--department", default=None, help="Issuing department or authority.")
    metadata.add_argument(
        "--document-type",
        default=None,
        help="notification | circular | scheme | guideline | amendment | other",
    )
    metadata.add_argument("--reference-no", default=None, help="Official reference number, as printed.")
    metadata.add_argument("--publication-date", default=None, help="ISO-8601 date (YYYY-MM-DD).")
    metadata.add_argument("--source-url", default=None, help="Official source URL.")
    metadata.add_argument("--retrieved-at", default=None, help="ISO-8601 date of download.")
    metadata.add_argument(
        "--document-group",
        default=None,
        help=(
            "Key shared by every version of the same policy. Supplying it is how "
            "a human asserts that this file is another version of an existing "
            "document; without it the file becomes its own logical document."
        ),
    )
    metadata.add_argument("--version-label", default=None, help="e.g. v1, 2026-amendment.")
    metadata.add_argument("--supersedes", default=None, help="The version_label this one replaces.")

    tuning = parser.add_argument_group("tuning")
    tuning.add_argument(
        "--max-file-size-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE_BYTES,
        help=f"Validation size cap (default: {DEFAULT_MAX_FILE_SIZE_BYTES}).",
    )
    tuning.add_argument(
        "--min-meaningful-chars",
        type=int,
        default=DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS,
        help=(
            "Letters/digits a page needs before has_meaningful_text is true "
            f"(default: {DEFAULT_MIN_MEANINGFUL_ALNUM_CHARS})."
        ),
    )
    tuning.add_argument(
        "--no-normalize",
        action="store_true",
        help=(
            "Disable every normalisation step. normalized_text then equals the "
            "raw extracted text. Useful for Experiment A comparisons."
        ),
    )
    tuning.add_argument(
        "--verbose",
        action="store_true",
        help="Show the pipeline's INFO log lines.",
    )
    return parser


def metadata_from_args(args: argparse.Namespace) -> SourceMetadata:
    """Build the human-asserted metadata record from parsed arguments."""
    return SourceMetadata(
        title=args.title,
        issuing_department=args.department,
        document_type=args.document_type,
        reference_no=args.reference_no,
        publication_date=args.publication_date,
        source_url=args.source_url,
        retrieved_at=args.retrieved_at,
        document_group=args.document_group,
        version_label=args.version_label,
        supersedes=args.supersedes,
    )


def default_output_path(output_dir: Path, version_id: str) -> Path:
    """Return the deterministic output path for a version id."""
    return output_dir / version_id / "extraction.json"


def write_result(
    result: ExtractionResult,
    output_path: Path,
    force: bool = False,
) -> Path:
    """Write the result as UTF-8 JSON.

    The overwrite policy is deliberate: an existing output is never replaced
    unless ``force`` is set. Re-running extraction is cheap, but silently
    clobbering a file that somebody has already annotated or inspected is not
    recoverable.

    Raises:
        OutputExistsError: The path exists and ``force`` is false.
    """
    if output_path.exists() and not force:
        raise OutputExistsError(
            f"{output_path} already exists. Re-run with --force to overwrite it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result_to_json(result), encoding="utf-8")
    return output_path


def format_summary(
    result: ExtractionResult,
    source_path: Path,
    output_path: Optional[Path],
    show_preview: bool = False,
) -> str:
    """Build the short terminal summary for one processed file."""
    document = result.document
    empty_pages = [page.page_number for page in result.pages if page.is_empty]
    lines = [
        f"{source_path}",
        f"  document_id        : {document.document_id}",
        f"  version_id         : {document.version_id}",
        f"  checksum (sha256)  : {document.checksum}",
        f"  extraction_tool    : {document.extraction_tool}",
        f"  extraction_status  : {document.extraction_status}",
        f"  pages              : {document.page_count}",
        f"  pages with text    : {result.meaningful_page_count}"
        f" / {document.page_count}",
        f"  empty pages        : {result.empty_page_count}"
        + (f"  (pages {_join(empty_pages)})" if empty_pages else ""),
        f"  text-poor pages    : {len(result.text_poor_page_numbers)}"
        + (
            f"  (pages {_join(result.text_poor_page_numbers)})"
            if result.text_poor_page_numbers
            else ""
        ),
    ]

    if document.warnings:
        lines.append(f"  warnings           : {len(document.warnings)}")
        for warning in document.warnings[:10]:
            lines.append(f"      - {warning}")
        if len(document.warnings) > 10:
            lines.append(f"      ... and {len(document.warnings) - 10} more (see the JSON)")
    else:
        lines.append("  warnings           : none")

    if show_preview:
        preview_page = next(
            (page for page in result.pages if page.has_meaningful_text), None
        )
        if preview_page is not None:
            snippet = preview_page.normalized_text.strip()[:_PREVIEW_CHARS]
            lines.append(f"  preview (page {preview_page.page_number}):")
            for line in snippet.splitlines():
                lines.append(f"      {line}")
            lines.append("      [...]")

    if output_path is not None:
        lines.append(f"  written            : {output_path}")
    else:
        lines.append("  written            : nothing (--no-write)")
    return "\n".join(lines)


def _join(numbers: Sequence[int], limit: int = 12) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    if len(numbers) > limit:
        shown += f", ... (+{len(numbers) - limit})"
    return shown


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns the process exit code rather than calling sys.exit."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    if args.manifest is None and not args.pdf_paths:
        parser.error("give at least one PDF path, or --manifest data/manifest.csv")
    if args.manifest is not None and args.pdf_paths:
        parser.error("--manifest processes the whole manifest; do not also name PDFs")
    if args.verify_checksums and args.manifest is None:
        parser.error("--verify-checksums only makes sense with --manifest")

    # A job is one PDF plus the metadata that belongs to it. In manifest mode
    # the metadata comes per row; otherwise one set of flags applies to every
    # named file.
    jobs: List[tuple[Path, SourceMetadata, Optional[str]]]
    if args.manifest is not None:
        try:
            entries = load_manifest(args.manifest)
        except ManifestError as exc:
            print(f"FAILED [MANIFEST_ERROR] {exc}", file=sys.stderr)
            return 1
        jobs = [
            (entry.pdf_path, entry.source_metadata, entry.expected_sha256)
            for entry in entries
        ]
    else:
        shared = metadata_from_args(args)
        jobs = [(path, shared, None) for path in args.pdf_paths]

    if args.output is not None and len(jobs) > 1:
        parser.error("--output accepts a single input file; use --output-dir for several.")

    normalization_options = (
        NormalizationOptions(
            unicode_nfkc=False,
            unify_line_endings=False,
            strip_invisible_characters=False,
            dehyphenate_line_breaks=False,
            collapse_horizontal_whitespace=False,
            collapse_blank_lines=False,
            strip_trailing_line_whitespace=False,
        )
        if args.no_normalize
        else NormalizationOptions()
    )

    failures = 0
    for pdf_path, source_metadata, expected_sha256 in jobs:
        try:
            result = extract_pdf(
                pdf_path,
                source_metadata=source_metadata,
                normalization_options=normalization_options,
                max_file_size_bytes=args.max_file_size_bytes,
                min_meaningful_alnum_chars=args.min_meaningful_chars,
            )
        except PdfIngestionError as exc:
            print(f"{pdf_path}\n  FAILED [{exc.code}] {exc.message}", file=sys.stderr)
            failures += 1
            continue

        if args.verify_checksums and expected_sha256:
            actual = result.document.checksum
            if actual.lower() != expected_sha256.lower():
                print(
                    f"{pdf_path}\n  FAILED [CHECKSUM_MISMATCH] manifest records "
                    f"{expected_sha256}, file is {actual}. The document has been "
                    f"re-published or altered - investigate before overwriting "
                    f"the manifest.",
                    file=sys.stderr,
                )
                failures += 1
                continue

        output_path: Optional[Path] = None
        if not args.no_write:
            target = args.output or default_output_path(
                args.output_dir, result.document.version_id
            )
            try:
                output_path = write_result(result, target, force=args.force)
            except OutputExistsError as exc:
                print(
                    f"{pdf_path}\n  SKIPPED [{exc.code}] {exc.message}",
                    file=sys.stderr,
                )
                failures += 1
                continue
            except OSError as exc:
                print(
                    f"{pdf_path}\n  FAILED [WRITE_ERROR] could not write {target}: {exc}",
                    file=sys.stderr,
                )
                failures += 1
                continue

        print(
            format_summary(
                result, pdf_path, output_path, show_preview=args.preview
            )
        )
        print()

    if failures:
        print(f"{failures} file(s) failed or were skipped.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
