"""Experiment A — compare PDF text extractors on the real corpus.

``CLAUDE.md`` §15 Experiment A: pypdf is the project's current extractor, chosen
on availability and simplicity rather than on measurement. This script measures
it against PyMuPDF and pdfplumber on the documents in ``data/manifest.csv``, so
that any decision to switch — or to stay — is evidence-backed.

What it measures, per backend per document
------------------------------------------
* **Page coverage** — page count agreement, and which pages came back empty.
* **Text completeness** — characters and words per page, and the ratio against
  the best-performing backend on that same page. A backend that returns 60% of
  the characters another one found on the same page has lost something.
* **Reading order** — measured indirectly. For each page we compute the
  flattened-table proxy from ``text_artifacts`` (a column-by-column read turns a
  table into a stack of one- and two-word lines) and the pairwise token-multiset
  overlap between backends: when two backends agree on *which* words are on a
  page but produce different line structure, the difference is ordering, not
  content.
* **Word spacing and broken words** — the ``'M inimum'``, ``'clean- up'`` and
  ``'‘ the Directions ’'`` artefact counters from ``text_artifacts``, which are
  the defects that break the exact-substring citation rule in
  ``CLAUDE.md`` §12.2.
* **Table structure** — pdfplumber and PyMuPDF both expose a table API; pypdf
  does not. Where tables are found, the row and column counts are recorded, so
  "it found a 4-column table" can be checked against the PDF by eye.
* **Devanagari rendering** — U+FFFD counts and orphaned combining marks, the
  most serious defect in the Phase 1 report.
* **Extraction time** — wall clock for the whole document, averaged over
  ``--repeats`` runs.

Honesty rules this script follows
---------------------------------
* It never declares a winner. It writes numbers; the recommendation is written
  by a human in ``evaluation/experiment-a-extractor-comparison.md`` **after**
  reading them, and must name the documents where the recommendation does not
  hold.
* Character count is reported as a *difference*, never as an accuracy. More
  characters is not automatically better — ``pdfplumber-layout`` pads with
  spaces and wins on raw count while being worse for citation matching.
* Per-page text is written out in full so a claim in the report can be traced
  to the page that produced it.

Usage
-----
::

    python -m evaluation.experiment_a_extractor_comparison --manifest data/manifest.csv

    # one file, two named backends, full page text dumped for inspection
    python -m evaluation.experiment_a_extractor_comparison data/raw/x.pdf \\
        --backends pypdf,pymupdf --dump-text
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.document_processing.extractor_backends import (
    BackendExtraction,
    BackendUnavailableError,
    ExtractorBackend,
    available_backends,
    get_backend,
    registered_backend_names,
)
from backend.app.document_processing.identity import (
    compute_file_checksum,
    version_id_from_checksum,
)
from backend.app.document_processing.manifest import ManifestError, load_manifest

from .text_artifacts import PageArtifacts, measure_page, summarise_pages

#: Where structured results go. The summary JSON is small and committed; the
#: per-page text dumps are large and regenerable, so they go under
#: ``data/processed/`` which is git-ignored.
DEFAULT_OUTPUT_DIR = Path("evaluation/experiment-a")
DEFAULT_TEXT_DUMP_DIR = Path("data/processed")

#: Pages whose text is quoted into the report as a failure example.
_SNIPPET_CHARS = 400


# --------------------------------------------------------------------------- #
# Cross-backend comparison metrics
# --------------------------------------------------------------------------- #


def token_overlap(left: str, right: str) -> float:
    """Return the multiset Jaccard overlap of two texts' whitespace tokens.

    Why a multiset and why tokens: this is the measurement that separates a
    *content* difference from an *ordering* difference. Two backends that read
    the same page in different orders produce the same bag of words (overlap
    near 1.0) with different line structure; a backend that dropped a column
    produces a smaller bag (overlap well below 1.0).

    Returns 1.0 when both texts are empty — two backends that both found nothing
    agree completely, which is the correct reading for a blank page.
    """
    left_tokens = Counter(left.split())
    right_tokens = Counter(right.split())
    if not left_tokens and not right_tokens:
        return 1.0
    intersection = sum((left_tokens & right_tokens).values())
    union = sum((left_tokens | right_tokens).values())
    return intersection / union if union else 1.0


def line_structure_difference(left: str, right: str) -> float:
    """Return the relative difference in non-blank line counts.

    Read together with :func:`token_overlap`: high token overlap plus a large
    line-structure difference is the signature of a reading-order or
    line-breaking disagreement rather than lost text.
    """
    left_lines = len([line for line in left.splitlines() if line.strip()])
    right_lines = len([line for line in right.splitlines() if line.strip()])
    largest = max(left_lines, right_lines)
    if largest == 0:
        return 0.0
    return abs(left_lines - right_lines) / largest


@dataclass
class DocumentComparison:
    """Every backend's result for one document, plus the cross-backend metrics."""

    document_label: str
    filename: str
    checksum: str
    version_id: str
    manifest_title: Optional[str]
    #: ``{backend_name: {...per-backend summary...}}``
    backends: Dict[str, Dict[str, object]] = field(default_factory=dict)
    #: ``{backend_name: {page_number: PageArtifacts}}`` retained for the report.
    page_artifacts: Dict[str, List[PageArtifacts]] = field(default_factory=dict)
    #: ``{"a_vs_b": {...}}`` pairwise reading-order / content agreement.
    pairwise: Dict[str, Dict[str, object]] = field(default_factory=dict)
    #: Page-count disagreements, which would invalidate every per-page compare.
    page_count_disagreement: Optional[Dict[str, int]] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "document_label": self.document_label,
            "filename": self.filename,
            "checksum": self.checksum,
            "version_id": self.version_id,
            "manifest_title": self.manifest_title,
            "page_count_disagreement": self.page_count_disagreement,
            "backends": self.backends,
            "pairwise": self.pairwise,
            "notes": self.notes,
            "pages": {
                name: [page.to_dict() for page in pages]
                for name, pages in self.page_artifacts.items()
            },
        }


def _time_backend(
    backend: ExtractorBackend, path: Path, repeats: int
) -> Tuple[BackendExtraction, float]:
    """Extract once for the result, and ``repeats`` times for a stable timing.

    The first run is kept as the result. Timing is the *median* of all runs,
    because a first run pays for lazy imports and OS file caching, and a mean
    would carry that cost into the comparison.
    """
    extraction = backend.extract(path)
    timings = [extraction.elapsed_seconds]
    for _ in range(max(0, repeats - 1)):
        timings.append(backend.extract(path).elapsed_seconds)
    return extraction, statistics.median(timings)


def _table_summary(
    backend: ExtractorBackend, path: Path, page_numbers: Sequence[int]
) -> Dict[str, object]:
    """Probe the backend's table API on the pages most likely to hold a table.

    Only the flagged pages are probed, because table detection is by far the
    slowest operation in this script and running it over all 57 pages of the
    corpus for every backend adds minutes for no extra information.
    """
    if not backend.supports_tables:
        return {"supported": False, "reason": "library has no table API"}

    per_page: Dict[str, object] = {}
    for page_number in page_numbers:
        try:
            tables = backend.extract_tables(path, page_number)
        except Exception as exc:  # noqa: BLE001 - a failed probe is a finding
            per_page[str(page_number)] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if tables is None:
            per_page[str(page_number)] = {"tables": None}
            continue
        per_page[str(page_number)] = {
            "table_count": len(tables),
            "shapes": [
                {
                    "rows": len(table),
                    "columns": max((len(row) for row in table), default=0),
                    "filled_cells": sum(
                        1 for row in table for cell in row if cell not in (None, "")
                    ),
                }
                for table in tables
            ],
        }
    return {"supported": True, "pages": per_page}


def compare_document(
    path: Path,
    backends: Sequence[ExtractorBackend],
    document_label: str,
    manifest_title: Optional[str] = None,
    repeats: int = 1,
    probe_tables: bool = True,
) -> Tuple[DocumentComparison, Dict[str, BackendExtraction]]:
    """Run every backend over one document and compute the comparison metrics."""
    checksum = compute_file_checksum(path)
    comparison = DocumentComparison(
        document_label=document_label,
        filename=path.name,
        checksum=checksum,
        version_id=version_id_from_checksum(checksum),
        manifest_title=manifest_title,
    )

    extractions: Dict[str, BackendExtraction] = {}
    for backend in backends:
        try:
            extraction, median_seconds = _time_backend(backend, path, repeats)
        except BackendUnavailableError as exc:
            comparison.notes.append(f"{backend.name}: unavailable ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001 - a whole-document failure is a result
            comparison.notes.append(
                f"{backend.name}: failed to open the document "
                f"({type(exc).__name__}: {exc})"
            )
            continue

        extractions[backend.name] = extraction
        pages = [
            measure_page(number, extraction.text_for_page(number))
            for number in range(1, extraction.page_count + 1)
        ]
        comparison.page_artifacts[backend.name] = pages

        summary = summarise_pages(pages)
        summary.update(
            {
                "tool_version": extraction.tool_version,
                "seconds_median": round(median_seconds, 4),
                "seconds_per_page": (
                    round(median_seconds / extraction.page_count, 4)
                    if extraction.page_count
                    else None
                ),
                "page_errors": extraction.page_errors,
                "chars_per_page_median": (
                    statistics.median([p.char_count for p in pages]) if pages else 0
                ),
                "chars_per_page_min": min((p.char_count for p in pages), default=0),
                "chars_per_page_max": max((p.char_count for p in pages), default=0),
            }
        )
        comparison.backends[backend.name] = summary

    if not extractions:
        comparison.notes.append("no backend produced a result for this document")
        return comparison, extractions

    # Page counts must agree before any per-page comparison means anything. A
    # disagreement is a serious finding in its own right, so it is recorded
    # rather than worked around.
    counts = {name: ex.page_count for name, ex in extractions.items()}
    if len(set(counts.values())) > 1:
        comparison.page_count_disagreement = counts
        comparison.notes.append(
            "backends disagree on page count; per-page comparisons below are "
            "limited to the shortest common prefix"
        )

    common_pages = min(ex.page_count for ex in extractions.values())

    # Completeness relative to the best backend on each page. Expressed as a
    # ratio against the page's maximum rather than as an accuracy, because
    # nobody here knows the true character count.
    #
    # Two ratios, and the *word* one is the one to trust. A character ratio is
    # dominated by any backend that pads with whitespace: pdfplumber's layout
    # mode scores 1.0 on every page simply because it inserts spaces to
    # reproduce the visual column positions, which says nothing about how much
    # text it recovered. Word counts come from ``str.split()``, which collapses
    # runs of whitespace, so they measure recovered *content*. The character
    # ratio is kept because it is what makes the padding visible.
    for name, extraction in extractions.items():
        char_ratios, word_ratios = [], []
        for page_number in range(1, common_pages + 1):
            texts = [
                other.text_for_page(page_number) for other in extractions.values()
            ]
            mine = extraction.text_for_page(page_number)
            best_chars = max(len(text) for text in texts)
            best_words = max(len(text.split()) for text in texts)
            if best_chars:
                char_ratios.append(len(mine) / best_chars)
            if best_words:
                word_ratios.append(len(mine.split()) / best_words)
        comparison.backends[name]["char_ratio_vs_best_median"] = (
            round(statistics.median(char_ratios), 4) if char_ratios else None
        )
        comparison.backends[name]["word_ratio_vs_best_median"] = (
            round(statistics.median(word_ratios), 4) if word_ratios else None
        )
        comparison.backends[name]["word_ratio_vs_best_min"] = (
            round(min(word_ratios), 4) if word_ratios else None
        )
        comparison.backends[name]["pages_below_95pct_words_of_best"] = [
            number
            for number, ratio in enumerate(word_ratios, start=1)
            if ratio < 0.95
        ]

    # Pairwise content vs ordering agreement.
    names = list(extractions)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            left, right = extractions[left_name], extractions[right_name]
            overlaps, line_diffs = [], []
            for page_number in range(1, common_pages + 1):
                left_text = left.text_for_page(page_number)
                right_text = right.text_for_page(page_number)
                overlaps.append(token_overlap(left_text, right_text))
                line_diffs.append(line_structure_difference(left_text, right_text))
            comparison.pairwise[f"{left_name}_vs_{right_name}"] = {
                "token_overlap_median": (
                    round(statistics.median(overlaps), 4) if overlaps else None
                ),
                "token_overlap_min": round(min(overlaps), 4) if overlaps else None,
                "line_structure_diff_median": (
                    round(statistics.median(line_diffs), 4) if line_diffs else None
                ),
                "pages_with_token_overlap_below_80pct": [
                    number
                    for number, overlap in enumerate(overlaps, start=1)
                    if overlap < 0.8
                ],
            }

    if probe_tables:
        # Probe the union of pages any backend flagged as a flattened table,
        # plus nothing else. These are precisely the pages where table handling
        # is the question.
        flagged = sorted(
            {
                page.page_number
                for pages in comparison.page_artifacts.values()
                for page in pages
                if page.probably_flattened_table
            }
        )
        if not flagged:
            comparison.notes.append(
                "no page was flagged as a probably-flattened table, so the table "
                "APIs were probed on page 1 only as a sanity check"
            )
            flagged = [1]
        for backend in backends:
            if backend.name not in extractions:
                continue
            comparison.backends[backend.name]["tables"] = _table_summary(
                backend, path, flagged
            )
        comparison.backends.setdefault("_meta", {})
        comparison.notes.append(
            f"table APIs probed on page(s): {', '.join(str(n) for n in flagged)}"
        )

    return comparison, extractions


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def _dump_page_text(
    dump_dir: Path,
    version_id: str,
    extractions: Dict[str, BackendExtraction],
) -> List[Path]:
    """Write every backend's full per-page text, for human inspection.

    One file per backend, pages separated by a visible marker. These are the
    files a report claim is checked against, and they are git-ignored because
    they duplicate the corpus text once per backend.
    """
    target = dump_dir / version_id / "experiment-a-text"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, extraction in extractions.items():
        path = target / f"{name}.txt"
        chunks = [
            f"===== {name} | page {number} / {extraction.page_count} =====\n"
            f"{extraction.text_for_page(number)}\n"
            for number in range(1, extraction.page_count + 1)
        ]
        path.write_text("".join(chunks), encoding="utf-8")
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.experiment_a_extractor_comparison",
        description=(
            "Experiment A: compare pypdf, PyMuPDF and pdfplumber page-level text "
            "extraction on the real corpus. Writes structured JSON; draws no "
            "conclusions."
        ),
    )
    parser.add_argument(
        "pdf_paths", nargs="*", type=Path, metavar="PDF",
        help="PDFs to compare. Omit and pass --manifest to use the corpus.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="Compare every row of this manifest instead of named files.",
    )
    parser.add_argument(
        "--backends", default=",".join(registered_backend_names()),
        help=(
            "Comma-separated backend names. Default: all registered "
            f"({', '.join(registered_backend_names())})."
        ),
    )
    parser.add_argument(
        "--repeats", type=int, default=3,
        help="Extraction runs per document; the median is reported (default: 3).",
    )
    parser.add_argument(
        "--no-tables", action="store_true",
        help="Skip table-API probing, which is the slowest part of the run.",
    )
    parser.add_argument(
        "--dump-text", action="store_true",
        help=(
            "Also write each backend's full per-page text under "
            f"{DEFAULT_TEXT_DUMP_DIR}/<version_id>/experiment-a-text/."
        ),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help=f"Where the results JSON goes (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--text-dump-dir", type=Path, default=DEFAULT_TEXT_DUMP_DIR,
        help=f"Base directory for --dump-text (default: {DEFAULT_TEXT_DUMP_DIR}).",
    )
    return parser


def _jobs_from_args(args: argparse.Namespace) -> List[Tuple[Path, str, Optional[str]]]:
    """Return ``(path, label, manifest_title)`` for every document to compare."""
    if args.manifest is not None:
        entries = load_manifest(args.manifest)
        return [
            (
                entry.pdf_path,
                entry.pdf_path.stem,
                entry.source_metadata.title,
            )
            for entry in entries
        ]
    return [(path, path.stem, None) for path in args.pdf_paths]


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.manifest is None and not args.pdf_paths:
        parser.error("give at least one PDF path, or --manifest data/manifest.csv")
    if args.manifest is not None and args.pdf_paths:
        parser.error("--manifest covers the whole corpus; do not also name PDFs")

    requested = [name.strip() for name in args.backends.split(",") if name.strip()]
    try:
        backends, unavailable = available_backends(requested)
    except KeyError as exc:
        parser.error(str(exc))
        return 2

    if not backends:
        print(
            "No extraction backend is available. Install the project "
            "requirements into .venv first.",
            file=sys.stderr,
        )
        return 1
    for name, reason in unavailable.items():
        print(f"SKIPPING backend {name}: {reason}", file=sys.stderr)

    try:
        jobs = _jobs_from_args(args)
    except ManifestError as exc:
        print(f"FAILED [MANIFEST_ERROR] {exc}", file=sys.stderr)
        return 1

    comparisons: List[DocumentComparison] = []
    missing: List[str] = []
    for path, label, title in jobs:
        if not path.exists():
            missing.append(str(path))
            print(f"SKIPPING {path}: file not found", file=sys.stderr)
            continue
        print(f"comparing {label} ({path}) ...", flush=True)
        comparison, extractions = compare_document(
            path,
            backends,
            document_label=label,
            manifest_title=title,
            repeats=args.repeats,
            probe_tables=not args.no_tables,
        )
        comparisons.append(comparison)
        if args.dump_text and extractions:
            for written in _dump_page_text(
                args.text_dump_dir, comparison.version_id, extractions
            ):
                print(f"  text dump: {written}")

    if not comparisons:
        print("No document could be compared.", file=sys.stderr)
        return 1

    payload = {
        "experiment": "A - PDF extractor comparison",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "python": sys.version.split()[0],
        "backends_compared": [
            {"name": backend.name, "tool_version": backend.tool_version()}
            for backend in backends
        ],
        "backends_unavailable": unavailable,
        "repeats_per_document": args.repeats,
        "documents_missing_on_disk": missing,
        "documents": [comparison.to_dict() for comparison in comparisons],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.json"
    results_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {results_path}")

    _print_console_summary(comparisons)
    return 0


def _print_console_summary(comparisons: Iterable[DocumentComparison]) -> None:
    """Print the headline table. Counts only — no verdict."""
    print("\n--- per document, per backend ---")
    header = (
        f"{'document':<30} {'backend':<18} {'pg':>3} {'chars':>7} {'words':>6} "
        f"{'FFFD':>5} {'mismap':>6} {'loss':>5} {'hyph':>5} {'quot':>5} "
        f"{'flatT':>5} {'wordRat':>7} {'sec':>7}"
    )
    print(header)
    print("-" * len(header))
    for comparison in comparisons:
        for name, summary in comparison.backends.items():
            if name == "_meta":
                continue
            print(
                f"{comparison.document_label[:30]:<30} {name:<18} "
                f"{summary.get('page_count', 0):>3} "
                f"{summary.get('total_chars', 0):>7} "
                f"{summary.get('total_words', 0):>6} "
                f"{summary.get('replacement_chars', 0):>5} "
                f"{summary.get('mismapped_glyph_candidates', 0):>6} "
                f"{summary.get('glyph_loss_signal', 0):>5} "
                f"{summary.get('hyphen_space_artifacts', 0):>5} "
                f"{summary.get('padded_quotes', 0):>5} "
                f"{len(summary.get('flattened_table_pages', [])):>5} "
                f"{summary.get('word_ratio_vs_best_median') or 0:>7.3f} "
                f"{summary.get('seconds_median', 0):>7.3f}"
            )
        print()


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
