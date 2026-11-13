"""E4 — how many clause-aware segments are actually table rows?

The worst known segmentation failure (Experiment B §3.1) is that a flattened
table extracts as a vertical stack whose row labels — ``1.``, ``2.``, ``3.`` —
look exactly like top-level clause numbers. On the lead-bank circular that
drove clause-aware precision down to 0.182. This module measures how often it
happens across all 57 pages instead of on the one annotated document.

**This is a proxy, and the number must always be reported as one.** It is not a
false-positive rate. Three reasons, stated here so they travel with the metric:

1. **Detection is PyMuPDF's judgement, not ground truth.** ``find_tables()``
   has its own false positives and negatives.
2. **The intersection is textual, not geometric.** Segments carry character
   offsets into ``normalized_text``; tables carry page coordinates. There is no
   shared coordinate space, so overlap is measured by how much of a segment's
   token content appears in the table's cells.
3. **A table row can legitimately be a clause.** A schedule of rates may be
   both. Flagging is a signal to look, not a verdict.

Per `CLAUDE.md` §24 rule 5 and the evaluation audit, nothing here changes
segmentation. The output is evidence for D-12, not a decision.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

__all__ = [
    "TableOverlapError",
    "DEFAULT_OVERLAP_THRESHOLD",
    "page_table_tokens",
    "token_overlap",
    "analyse_version",
    "run",
    "format_report",
    "main",
]

DEFAULT_PROCESSED_DIR = Path("data") / "processed"
DEFAULT_RAW_DIR = Path("data") / "raw"
DEFAULT_OUTPUT_DIR = Path("evaluation") / "experiment-e4-tables"

#: A segment is flagged when at least this share of its tokens also appear in
#: the page's detected table cells. 0.6 rather than 0.9: a flattened table row
#: picks up stray punctuation and the odd caption word, so demanding near-total
#: overlap would miss the exact segments this is meant to find. Rather than
#: tune it, the sensitivity sweep in the output reports several thresholds.
DEFAULT_OVERLAP_THRESHOLD = 0.6

SENSITIVITY_THRESHOLDS = (0.4, 0.5, 0.6, 0.7, 0.8)

_TOKEN_RE = re.compile(r"[0-9A-Za-zऀ-ॿ]+")


class TableOverlapError(RuntimeError):
    """Table detection is unavailable or an artefact is missing."""

    code = "TABLE_OVERLAP_ERROR"


@dataclass
class VersionTableReport:
    version_id: str
    filename: str
    page_count: int = 0
    pages_with_tables: List[int] = field(default_factory=list)
    tables_detected: int = 0
    clause_segments: int = 0
    clause_segments_on_table_pages: int = 0
    flagged_segments: int = 0
    flagged_examples: List[str] = field(default_factory=list)
    sensitivity: Dict[str, int] = field(default_factory=dict)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        share = (
            self.flagged_segments / self.clause_segments
            if self.clause_segments
            else None
        )
        return {
            "version_id": self.version_id,
            "filename": self.filename,
            "page_count": self.page_count,
            "pages_with_tables": self.pages_with_tables,
            "tables_detected": self.tables_detected,
            "clause_segments": self.clause_segments,
            "clause_segments_on_table_pages": self.clause_segments_on_table_pages,
            "flagged_segments": self.flagged_segments,
            "flagged_share_of_all_clause_segments": share,
            "flagged_examples": self.flagged_examples,
            "sensitivity_by_threshold": self.sensitivity,
            "note": self.note,
        }


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def page_table_tokens(pdf_path: Path) -> Dict[int, Set[str]]:
    """``{page_number: tokens appearing in that page's detected tables}``.

    PyMuPDF is imported lazily: it is an evaluation-only dependency and the
    production path must stay importable without it.
    """
    try:
        import pymupdf  # noqa: PLC0415 - lazy by design
    except ImportError as exc:  # pragma: no cover - environment problem
        raise TableOverlapError(
            "PyMuPDF is required for table detection. It is pinned in "
            "requirements.txt as an evaluation-only dependency."
        ) from exc

    if not Path(pdf_path).exists():
        raise TableOverlapError(f"source PDF not found: {pdf_path}")

    per_page: Dict[int, Set[str]] = {}
    document = pymupdf.open(str(pdf_path))
    try:
        for index, page in enumerate(document, start=1):
            try:
                found = page.find_tables()
            except Exception:  # noqa: BLE001 - detection is best-effort
                continue
            tokens: Set[str] = set()
            count = 0
            for table in found.tables:
                count += 1
                try:
                    rows = table.extract()
                except Exception:  # noqa: BLE001
                    continue
                for row in rows:
                    for cell in row:
                        if cell:
                            tokens.update(_tokens(str(cell)))
            if count:
                per_page[index] = tokens
    finally:
        document.close()
    return per_page


def token_overlap(segment_text: str, table_tokens: Set[str]) -> float:
    """Share of a segment's tokens that also appear in the table's cells.

    Type-based, not count-based: a segment repeating one word many times
    should not score higher for it.
    """
    tokens = set(_tokens(segment_text))
    if not tokens:
        return 0.0
    return len(tokens & table_tokens) / len(tokens)


def analyse_version(
    version_dir: Path,
    raw_dir: Path = DEFAULT_RAW_DIR,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> VersionTableReport:
    """Measure table overlap for one version's clause-aware segments."""
    extraction_path = version_dir / "extraction.json"
    segments_path = version_dir / "segments_clause_aware.json"
    if not extraction_path.exists() or not segments_path.exists():
        raise TableOverlapError(f"{version_dir.name}: missing artefacts")

    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    segments = json.loads(segments_path.read_text(encoding="utf-8"))
    document = extraction["document"]
    filename = str(document["filename"])

    report = VersionTableReport(
        version_id=str(document["version_id"]),
        filename=filename,
        page_count=int(document["page_count"]),
        clause_segments=len(segments["segments"]),
    )

    pdf_path = Path(raw_dir) / filename
    if not pdf_path.exists():
        report.note = (
            f"source PDF {pdf_path} is absent (data/raw is git-ignored); "
            f"table detection skipped for this version"
        )
        return report

    table_tokens = page_table_tokens(pdf_path)
    report.pages_with_tables = sorted(table_tokens)
    report.tables_detected = len(table_tokens)

    counts_by_threshold = {f"{t:.1f}": 0 for t in SENSITIVITY_THRESHOLDS}

    for segment in segments["segments"]:
        touched = {
            int(offset["page_number"]) for offset in segment["page_offsets"]
        }
        relevant = touched & set(table_tokens)
        if not relevant:
            continue
        report.clause_segments_on_table_pages += 1

        best = max(
            token_overlap(segment["text"], table_tokens[page])
            for page in relevant
        )
        for value in SENSITIVITY_THRESHOLDS:
            if best >= value:
                counts_by_threshold[f"{value:.1f}"] += 1
        if best >= threshold:
            report.flagged_segments += 1
            if len(report.flagged_examples) < 8:
                preview = " ".join(str(segment["text"]).split())[:72]
                report.flagged_examples.append(
                    f"p{min(relevant)} [{segment.get('clause_number')}] "
                    f"overlap={best:.2f} {preview!r}"
                )

    report.sensitivity = counts_by_threshold
    return report


def run(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> Dict[str, object]:
    directory = Path(processed_dir)
    if not directory.exists():
        raise TableOverlapError(f"processed directory not found: {directory}")
    version_dirs = sorted(c for c in directory.iterdir() if c.is_dir())
    if not version_dirs:
        raise TableOverlapError(f"no version directories under {directory}")

    reports = [analyse_version(d, raw_dir, threshold) for d in version_dirs]
    total_segments = sum(r.clause_segments for r in reports)
    total_flagged = sum(r.flagged_segments for r in reports)

    return {
        "experiment": "E4 - table-induced false clause PROXY",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "metric": (
            "Share of clause_aware segments whose token content overlaps a "
            "PyMuPDF-detected table region by >= threshold. A PROXY for "
            "table-induced false clauses, NOT a false-positive rate: table "
            "detection is itself a heuristic, the intersection is textual "
            "rather than geometric, and a table row can legitimately be a "
            "clause. Flagged pages need manual confirmation."
        ),
        "overlap_threshold": threshold,
        "sensitivity_thresholds": list(SENSITIVITY_THRESHOLDS),
        "pooled": {
            "clause_segments": total_segments,
            "flagged_segments": total_flagged,
            "flagged_share": (
                total_flagged / total_segments if total_segments else None
            ),
            "pages_with_detected_tables": sum(
                len(r.pages_with_tables) for r in reports
            ),
        },
        "versions": [r.to_dict() for r in reports],
    }


def format_report(payload: Dict[str, object]) -> str:
    pooled = payload["pooled"]  # type: ignore[index]
    share = pooled["flagged_share"]  # type: ignore[index]
    lines = [
        "NiyamSetu table-overlap PROXY (E4)",
        f"generated : {payload['generated_at']}",
        f"threshold : token overlap >= {payload['overlap_threshold']}",
        "",
        f"POOLED: {pooled['flagged_segments']} of "  # type: ignore[index]
        f"{pooled['clause_segments']} clause segments flagged"  # type: ignore[index]
        + (f" ({share:.1%})" if isinstance(share, float) else ""),
        f"        {pooled['pages_with_detected_tables']} page(s) "  # type: ignore[index]
        f"with a detected table",
        "",
        "NOT a false-positive rate - a proxy needing manual confirmation.",
        "",
    ]
    for version in payload["versions"]:  # type: ignore[union-attr]
        lines.append(
            f"{version['filename']}  ({version['page_count']} pages)"
        )
        if version["note"]:
            lines.append(f"    {version['note']}")
            continue
        lines.append(
            f"    table pages {version['pages_with_tables']} | "
            f"clause segments {version['clause_segments']} | "
            f"on table pages {version['clause_segments_on_table_pages']} | "
            f"FLAGGED {version['flagged_segments']}"
        )
        lines.append(f"    sensitivity {version['sensitivity_by_threshold']}")
        for example in version["flagged_examples"][:4]:  # type: ignore[index]
            lines.append(f"      {example}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.table_overlap",
        description=(
            "E4: measure how many clause-aware segments overlap a detected "
            "table region. Reports a PROXY, not a false-positive rate."
        ),
    )
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_OVERLAP_THRESHOLD
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        payload = run(args.processed_dir, args.raw_dir, args.threshold)
    except TableOverlapError as exc:
        print(f"ERROR [{exc.code}] {exc}")
        return 1

    print(format_report(payload))
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / "results.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
