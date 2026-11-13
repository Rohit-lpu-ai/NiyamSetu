"""Command-line front end for Phase 2 segmentation.

Reads the ``extraction.json`` files Phase 1 produced and writes, per version:

.. code-block:: text

    data/processed/<version_id>/segments_paragraph.json
    data/processed/<version_id>/segments_clause_aware.json
    data/processed/<version_id>/page_cleaning.json

The extraction JSON is **read only** and never rewritten
(``CLAUDE.md`` §24 rule 10). Running segmentation again after a pipeline change
regenerates only the segment files.

As with the extraction CLI, all the real work is in the library modules
(:mod:`page_cleaning`, :mod:`segmentation`); this file parses arguments, prints
a summary and chooses an exit code, so the pipeline stays testable without a
shell.

Why cleaning is written out too
-------------------------------
``page_cleaning.json`` records every removed line with the evidence for its
removal, which is what makes header/footer stripping auditable and reversible
from the artefacts alone. Without it, a reader of the segment files could see
that text was missing but not why — and the preview frontend could not show the
removal plan.

Determinism
-----------
Given the same ``extraction.json`` and the same options, the output is
byte-identical: segment ids are derived from ``(version_id, method, ordinal)``
and nothing in the pipeline consults a clock, a random source or dictionary
ordering. ``--check-deterministic`` asserts this by running the whole
segmentation twice and comparing, which is cheap and catches an accidental
dependency on iteration order.

Usage
-----
::

    # every version already extracted under data/processed/
    python -m backend.app.document_processing.segmentation_cli --all

    # one version, with a sample of the output printed
    python -m backend.app.document_processing.segmentation_cli \\
        --version-id VER-fd6e1537c1a42574 --preview

Exit codes: ``0`` all versions segmented; ``1`` at least one failed or was
skipped; ``2`` bad arguments.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import OutputExistsError, PdfIngestionError, SegmentProvenanceError
from .page_cleaning import CleaningOptions, clean_pages
from .schemas import SCHEMA_VERSION
from .segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
    OFFSET_BASIS_NORMALIZED,
    OFFSET_BASIS_ORIGINAL,
    SegmentationResult,
    build_source_lines,
    segment_clauses,
    segment_paragraphs,
)

logger = logging.getLogger(__name__)

DEFAULT_PROCESSED_DIR = Path("data/processed")
EXTRACTION_FILENAME = "extraction.json"
CLEANING_FILENAME = "page_cleaning.json"

#: ``{method: filename}``. The names are part of the contract Persons 2-5 read
#: (``data/README.md`` §3), so they live in one place.
SEGMENT_FILENAMES = {
    METHOD_PARAGRAPH: "segments_paragraph.json",
    METHOD_CLAUSE_AWARE: "segments_clause_aware.json",
}

_PREVIEW_SEGMENTS = 6
_PREVIEW_CHARS = 180


class SegmentationInputError(PdfIngestionError):
    """The extraction JSON is missing, unreadable or does not match the contract."""

    code = "SEGMENTATION_INPUT_ERROR"


def load_extraction(path: Path) -> Dict[str, object]:
    """Read and sanity-check one ``extraction.json``.

    The checks are deliberately about *shape*, not content: segmentation cannot
    do anything useful with a file that lacks pages or identifiers, and failing
    here with a clear message is better than raising somewhere in the middle of
    the segmenter.
    """
    if not path.exists():
        raise SegmentationInputError(f"No extraction output at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SegmentationInputError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SegmentationInputError(f"{path}: expected a JSON object at the top level")
    document = payload.get("document")
    pages = payload.get("pages")
    if not isinstance(document, dict):
        raise SegmentationInputError(f"{path}: missing the 'document' object")
    if not isinstance(pages, list) or not pages:
        raise SegmentationInputError(f"{path}: missing or empty 'pages' array")
    for key in ("document_id", "version_id"):
        if not document.get(key):
            raise SegmentationInputError(f"{path}: document.{key} is missing or blank")
    return payload


def _page_inputs(
    payload: Dict[str, object], use_raw_text: bool
) -> Tuple[List[Tuple[int, str]], str]:
    """Return ``([(page_number, text), ...], offset_basis)``.

    Which text field is used determines what the character offsets mean, so the
    two decisions are made together here and the basis is returned alongside
    the text rather than inferred later.
    """
    field = "original_text" if use_raw_text else "normalized_text"
    basis = OFFSET_BASIS_ORIGINAL if use_raw_text else OFFSET_BASIS_NORMALIZED
    pages: List[Tuple[int, str]] = []
    for entry in payload["pages"]:  # type: ignore[index]
        if not isinstance(entry, dict):
            raise SegmentationInputError("pages[] must contain objects")
        number = entry.get("page_number")
        if not isinstance(number, int) or number < 1:
            raise SegmentationInputError(
                f"page_number must be a 1-based integer, got {number!r}"
            )
        text = entry.get(field)
        if text is None:
            raise SegmentationInputError(
                f"page {number} has no '{field}' field; re-run extraction"
            )
        pages.append((number, str(text)))
    pages.sort(key=lambda item: item[0])
    return pages, basis


def segment_extraction(
    payload: Dict[str, object],
    cleaning_options: Optional[CleaningOptions] = None,
    use_raw_text: bool = False,
) -> Tuple[Dict[str, SegmentationResult], Dict[str, object]]:
    """Run cleaning and both segmentation methods over one extraction payload.

    Returns:
        ``({method: SegmentationResult}, cleaning_report)``.
    """
    document = payload["document"]  # type: ignore[index]
    document_id = str(document["document_id"])  # type: ignore[index]
    version_id = str(document["version_id"])  # type: ignore[index]

    pages, offset_basis = _page_inputs(payload, use_raw_text)
    cleaned, plan = clean_pages(pages, cleaning_options)
    source_lines = build_source_lines(cleaned)

    results = {
        METHOD_PARAGRAPH: segment_paragraphs(
            source_lines, document_id, version_id, offset_basis
        ),
        METHOD_CLAUSE_AWARE: segment_clauses(
            source_lines, document_id, version_id, offset_basis
        ),
    }

    cleaning_report = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "version_id": version_id,
        "offset_basis": offset_basis,
        "source_text_field": "original_text" if use_raw_text else "normalized_text",
        "plan": plan.to_dict(),
        "pages": [page.to_dict() for page in cleaned],
        "totals": {
            "pages": len(cleaned),
            "removed_lines": sum(page.removed_line_count for page in cleaned),
            "pages_with_removals": sum(
                1 for page in cleaned if page.removed_line_count
            ),
            "reversible": all(
                page.restore() == page.source_text for page in cleaned
            ),
        },
    }
    if not cleaning_report["totals"]["reversible"]:  # type: ignore[index]
        # The whole point of recording removals is that they can be undone. If
        # the round-trip fails, the artefact would be a lie about what was
        # removed, so refuse to write it.
        raise SegmentProvenanceError(
            f"{version_id}: header/footer removal is not reversible for at least "
            f"one page; refusing to write segments built on it"
        )
    return results, cleaning_report


def result_payload(
    result: SegmentationResult, payload: Dict[str, object]
) -> Dict[str, object]:
    """Wrap a segmentation result in the JSON envelope written to disk."""
    document = payload["document"]  # type: ignore[index]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "source_extraction": {
            "checksum": document.get("checksum"),  # type: ignore[union-attr]
            "extraction_tool": document.get("extraction_tool"),  # type: ignore[union-attr]
            "page_count": document.get("page_count"),  # type: ignore[union-attr]
        },
        **result.to_dict(),
    }


def write_json(payload: Dict[str, object], path: Path, force: bool = False) -> Path:
    """Write UTF-8 JSON, refusing to overwrite unless ``force``.

    Same policy as the extraction CLI: re-running is cheap, but silently
    clobbering a file somebody has annotated or inspected is not recoverable.
    """
    if path.exists() and not force:
        raise OutputExistsError(
            f"{path} already exists. Re-run with --force to overwrite it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def discover_version_dirs(processed_dir: Path) -> List[Path]:
    """Return every directory under ``processed_dir`` holding an extraction.json."""
    if not processed_dir.exists():
        return []
    return sorted(
        child
        for child in processed_dir.iterdir()
        if child.is_dir() and (child / EXTRACTION_FILENAME).exists()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.document_processing.segmentation_cli",
        description=(
            "Phase 2: clean running headers/footers and segment page text into "
            "paragraphs and clauses, preserving page provenance. Reads the "
            "extraction JSON produced by the extraction CLI; never modifies it."
        ),
    )
    selection = parser.add_argument_group("what to segment")
    selection.add_argument(
        "--all", action="store_true",
        help="Segment every version found under --processed-dir.",
    )
    selection.add_argument(
        "--version-id", action="append", default=[], metavar="VER-xxxx",
        help="Segment this version id. Repeatable.",
    )
    selection.add_argument(
        "--extraction", type=Path, action="append", default=[], metavar="PATH",
        help="Segment this extraction.json directly. Repeatable.",
    )
    selection.add_argument(
        "--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR,
        help=f"Where version directories live (default: {DEFAULT_PROCESSED_DIR}).",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--force", action="store_true",
        help="Overwrite existing segment files.",
    )
    output.add_argument(
        "--no-write", action="store_true",
        help="Segment and summarise, but write nothing.",
    )
    output.add_argument(
        "--preview", action="store_true",
        help=f"Print the first {_PREVIEW_SEGMENTS} clause segments.",
    )
    output.add_argument(
        "--check-deterministic", action="store_true",
        help=(
            "Segment twice and assert the output is identical. Cheap, and it "
            "catches an accidental dependency on iteration order."
        ),
    )

    tuning = parser.add_argument_group("tuning")
    tuning.add_argument(
        "--frequency-threshold", type=float, default=None, metavar="F",
        help=(
            "Fraction of pages a line must repeat on to count as a running "
            f"header (default: {CleaningOptions().frequency_threshold}). See "
            "backend/docs/phase2-segmentation.md for why it is not 0.60."
        ),
    )
    tuning.add_argument(
        "--no-clean", action="store_true",
        help=(
            "Disable all header/footer/page-number removal. Useful for measuring "
            "what cleaning contributes to segmentation quality."
        ),
    )
    tuning.add_argument(
        "--use-raw-text", action="store_true",
        help=(
            "Segment each page's original_text instead of normalized_text. "
            "Character offsets then refer to raw text, matching CLAUDE.md "
            "§11.4; segmentation quality is usually worse. Recorded in the "
            "output as offset_basis."
        ),
    )
    tuning.add_argument(
        "--verbose", action="store_true", help="Show INFO log lines."
    )
    return parser


def _cleaning_options_from_args(args: argparse.Namespace) -> CleaningOptions:
    if args.no_clean:
        return CleaningOptions(
            remove_repeated_headers=False,
            remove_repeated_footers=False,
            remove_page_numbers=False,
        )
    if args.frequency_threshold is not None:
        return CleaningOptions(frequency_threshold=args.frequency_threshold)
    return CleaningOptions()


def format_summary(
    version_id: str,
    results: Dict[str, SegmentationResult],
    cleaning_report: Dict[str, object],
    written: Sequence[Path],
) -> str:
    totals = cleaning_report["totals"]  # type: ignore[index]
    plan = cleaning_report["plan"]  # type: ignore[index]
    lines = [
        f"{version_id}",
        f"  pages                 : {totals['pages']}",  # type: ignore[index]
        f"  offset_basis          : {cleaning_report['offset_basis']}",
        f"  removed lines         : {totals['removed_lines']}"  # type: ignore[index]
        f" on {totals['pages_with_removals']} page(s)",  # type: ignore[index]
        f"  removal reversible    : {totals['reversible']}",  # type: ignore[index]
    ]
    for method, result in results.items():
        stats = result.stats
        lines.append(
            f"  {method:<20}: {stats['segment_count']} segments, "
            f"{stats.get('page_spanning_segments', 0)} spanning a page break, "
            f"mean {stats.get('mean_chars', 0)} chars"
        )
        if method == METHOD_CLAUSE_AWARE:
            lines.append(f"      marker styles     : {stats.get('marker_styles')}")
            lines.append(f"      segment types     : {stats.get('type_counts')}")
            lines.append(
                f"      max nesting level : {stats.get('max_level')}, "
                f"with parent: {stats.get('segments_with_parent')}"
            )
    for warning in plan.get("warnings", []):  # type: ignore[union-attr]
        lines.append(f"  CLEANING WARNING      : {warning}")
    for result in results.values():
        for warning in result.warnings:
            lines.append(f"  SEGMENTATION WARNING  : {warning}")
    for path in written:
        lines.append(f"  written               : {path}")
    if not written:
        lines.append("  written               : nothing (--no-write)")
    return "\n".join(lines)


def _format_preview(result: SegmentationResult) -> str:
    lines = [f"  preview ({result.segmentation_method}):"]
    for segment in result.segments[:_PREVIEW_SEGMENTS]:
        pages = (
            f"p{segment.page_start}"
            if segment.page_start == segment.page_end
            else f"p{segment.page_start}-{segment.page_end}"
        )
        marker = segment.clause_number or "-"
        lines.append(
            f"      [{segment.ordinal:>3}] {pages:<8} lvl{segment.level} "
            f"{segment.segment_type:<12} {marker:<10} "
            f"{segment.text[:_PREVIEW_CHARS]}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    if not (args.all or args.version_id or args.extraction):
        parser.error("choose --all, --version-id VER-xxxx, or --extraction PATH")

    targets: List[Path] = []
    if args.all:
        targets.extend(
            directory / EXTRACTION_FILENAME
            for directory in discover_version_dirs(args.processed_dir)
        )
        if not targets:
            print(
                f"No extraction output found under {args.processed_dir}. Run the "
                f"extraction CLI first (see data/README.md §9).",
                file=sys.stderr,
            )
            return 1
    targets.extend(
        args.processed_dir / version_id / EXTRACTION_FILENAME
        for version_id in args.version_id
    )
    targets.extend(args.extraction)

    cleaning_options = _cleaning_options_from_args(args)
    failures = 0

    for extraction_path in targets:
        try:
            payload = load_extraction(extraction_path)
            results, cleaning_report = segment_extraction(
                payload, cleaning_options, use_raw_text=args.use_raw_text
            )
        except PdfIngestionError as exc:
            print(
                f"{extraction_path}\n  FAILED [{exc.code}] {exc.message}",
                file=sys.stderr,
            )
            failures += 1
            continue

        version_id = str(cleaning_report["version_id"])

        if args.check_deterministic:
            repeat_results, repeat_cleaning = segment_extraction(
                payload, cleaning_options, use_raw_text=args.use_raw_text
            )
            first = {
                method: result.to_dict() for method, result in results.items()
            }
            second = {
                method: result.to_dict() for method, result in repeat_results.items()
            }
            if first != second or repeat_cleaning["plan"] != cleaning_report["plan"]:
                print(
                    f"{extraction_path}\n  FAILED [NON_DETERMINISTIC] segmenting "
                    f"the same input twice produced different output",
                    file=sys.stderr,
                )
                failures += 1
                continue
            print(f"{version_id}: deterministic check passed")

        written: List[Path] = []
        if not args.no_write:
            output_dir = extraction_path.parent
            to_write = [
                (result_payload(result, payload), output_dir / SEGMENT_FILENAMES[method])
                for method, result in results.items()
            ]
            to_write.append((cleaning_report, output_dir / CLEANING_FILENAME))
            try:
                for file_payload, target in to_write:
                    written.append(write_json(file_payload, target, force=args.force))
            except OutputExistsError as exc:
                print(
                    f"{extraction_path}\n  SKIPPED [{exc.code}] {exc.message}",
                    file=sys.stderr,
                )
                failures += 1
                continue
            except OSError as exc:
                print(
                    f"{extraction_path}\n  FAILED [WRITE_ERROR] {exc}",
                    file=sys.stderr,
                )
                failures += 1
                continue

        print(format_summary(version_id, results, cleaning_report, written))
        if args.preview:
            print(_format_preview(results[METHOD_CLAUSE_AWARE]))
        print()

    if failures:
        print(f"{failures} version(s) failed or were skipped.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
