"""Experiment B — score paragraph vs clause-aware segmentation.

``CLAUDE.md`` §15 Experiment B. Both segmentation methods are scored against
the same hand-checked clause annotations in ``data/annotations/segmentation/``,
produced under the guideline in ``data/annotations/README.md``.

Three subcommands, in the order they are meant to be used:

``template``
    Emit a pre-filled annotation template from the segmenter's own clause
    output, so an annotator corrects markers rather than typing them. **The
    template is a starting point, not an annotation** — a file accepted unedited
    measures the segmenter against itself, so :func:`validate` counts how many
    records differ from their template and the report quotes that count.

``validate``
    Check every annotation file before any scoring: markers locatable and
    unique, ``clause_index`` dense and ordered, page ranges sane, every
    ``uncertain`` record carrying a note. A marker that cannot be found is an
    **error**, never a silent skip — a silently skipped annotation inflates
    precision.

``score``
    Compute the metrics and write ``evaluation/experiment-b/results.json``.

What is measured, and how
-------------------------
A **boundary** is the position where a clause starts, expressed as
``(page_number, char_start)``. Annotated boundaries come from locating
``text_start_marker`` in that page's text; predicted boundaries come from each
segment's first ``page_offsets`` entry. A predicted boundary **matches** an
annotated one when they are on the same page and their character positions
differ by no more than ``--tolerance`` characters.

The tolerance exists because ``CLAUDE.md`` §15 requires "a stated tolerance for
whitespace-only offsets": a segment may legitimately start one space or one line
break away from where a marker was copied. It is reported with every result, and
``score`` prints a small sensitivity table across several tolerances so a reader
can see how much the headline number depends on it.

* **Boundary precision** — matched predicted boundaries / all predicted.
* **Boundary recall** — matched annotated boundaries / all annotated.
* **Boundary F1** — their harmonic mean.
* **Exact-match rate** — matched pairs whose predicted text equals the annotated
  span, comparing whitespace-insensitively (the annotated span is reconstructed
  from the start marker to the end marker).
* **Missed clauses** — annotated boundaries with no predicted boundary.
* **Incorrect merges** — one predicted segment covering two or more annotated
  clauses.
* **Incorrect splits** — one annotated clause covered by two or more predicted
  segments.
* **Heading preservation** — of matched pairs where the annotation has a
  heading, how many predictions carry the same heading.
* **Numbering preservation** — of matched pairs where the annotation has a
  clause number, how many predictions carry the same number.
* **Page-provenance correctness** — of matched pairs, how many agree on
  ``page_start`` *and* ``page_end``.

Honesty rules this script follows
---------------------------------
* It computes nothing unless annotation records exist. There is no default, no
  fallback and no synthetic ground truth.
* Records marked ``uncertain`` are excluded from the headline metric and the
  whole metric set is recomputed with them included, so both numbers are
  reported (guideline §6).
* Every result carries the annotator, the guideline version, the review status
  and the sample size, so a number can never be quoted without its caveats.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from backend.app.document_processing.page_cleaning import CleaningOptions, clean_pages
from backend.app.document_processing.segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
    Segment,
    build_source_lines,
    segment_clauses,
    segment_paragraphs,
)
from backend.app.document_processing.segmentation_cli import (
    EXTRACTION_FILENAME,
    discover_version_dirs,
    load_extraction,
)

DEFAULT_ANNOTATION_DIR = Path("data/annotations/segmentation")
DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_OUTPUT_DIR = Path("evaluation/experiment-b")

#: Default match tolerance in characters. Small on purpose: it is meant to
#: absorb a space or a line break, not to make a near-miss count as a hit.
DEFAULT_TOLERANCE = 5

#: Tolerances used for the sensitivity table, so a reader can see whether the
#: headline number survives a stricter or looser rule.
SENSITIVITY_TOLERANCES = (0, 2, 5, 20, 80)

_WHITESPACE_RUN = re.compile(r"\s+")


def _squash(text: str) -> str:
    """Collapse whitespace for whitespace-insensitive text comparison."""
    return _WHITESPACE_RUN.sub(" ", text).strip()


# --------------------------------------------------------------------------- #
# Annotation records
# --------------------------------------------------------------------------- #


class AnnotationError(Exception):
    """An annotation file is malformed, or a marker cannot be located."""


@dataclass
class AnnotatedClause:
    """One hand-checked clause boundary, as stored in the JSONL file."""

    version_id: str
    clause_index: int
    page_start: int
    page_end: int
    text_start_marker: str
    text_end_marker: str
    clause_number: Optional[str] = None
    heading: Optional[str] = None
    uncertain: bool = False
    note: Optional[str] = None
    guideline_version: str = "1.0"
    annotator: str = "unknown"
    review_status: str = "awaiting_owner_review"
    #: Resolved by :func:`locate_annotations`; not stored in the file.
    resolved_char_start: Optional[int] = None
    resolved_char_end: Optional[int] = None

    @classmethod
    def from_json(cls, payload: Dict[str, object], source: Path, line_no: int) -> "AnnotatedClause":
        required = (
            "version_id", "clause_index", "page_start", "page_end",
            "text_start_marker", "text_end_marker",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise AnnotationError(
                f"{source}:{line_no} is missing required field(s): "
                f"{', '.join(missing)}"
            )
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("resolved_")}
        unknown = set(payload) - known
        if unknown:
            raise AnnotationError(
                f"{source}:{line_no} has unrecognised field(s): "
                f"{', '.join(sorted(unknown))}. Check "
                f"data/annotations/README.md §3.1."
            )
        record = cls(**{key: payload[key] for key in payload})  # type: ignore[arg-type]
        if record.page_end < record.page_start:
            raise AnnotationError(
                f"{source}:{line_no} has page_end ({record.page_end}) before "
                f"page_start ({record.page_start})"
            )
        if record.uncertain and not record.note:
            raise AnnotationError(
                f"{source}:{line_no} is marked uncertain but has no note; the "
                f"guideline (§6) requires one"
            )
        return record

    def to_json(self) -> Dict[str, object]:
        payload = asdict(self)
        payload.pop("resolved_char_start")
        payload.pop("resolved_char_end")
        return payload


def load_annotations(path: Path) -> List[AnnotatedClause]:
    """Read one JSONL annotation file, validating shape as it goes."""
    if not path.exists():
        raise AnnotationError(f"No annotation file at {path}")
    records: List[AnnotatedClause] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AnnotationError(f"{path}:{line_no} is not valid JSON: {exc}") from exc
        records.append(AnnotatedClause.from_json(payload, path, line_no))

    indices = [record.clause_index for record in records]
    if indices != list(range(len(records))):
        raise AnnotationError(
            f"{path}: clause_index must be dense and in document order starting "
            f"at 0; got {indices[:12]}{'...' if len(indices) > 12 else ''}"
        )
    return records


def locate_annotations(
    records: Sequence[AnnotatedClause], page_text: Dict[int, str]
) -> None:
    """Resolve each record's markers to character positions, in place.

    Raises rather than skipping. A marker that cannot be found means the
    annotation and the extraction have diverged, and silently dropping the
    record would remove a clause the segmenter was supposed to find — inflating
    precision and recall at the same time.
    """
    for record in records:
        for page_attr, marker_attr in (
            ("page_start", "text_start_marker"),
            ("page_end", "text_end_marker"),
        ):
            page_number = getattr(record, page_attr)
            if page_number not in page_text:
                raise AnnotationError(
                    f"clause_index {record.clause_index}: {page_attr} "
                    f"{page_number} is not a page of {record.version_id}"
                )

        start_page = page_text[record.page_start]
        marker = record.text_start_marker
        occurrences = start_page.count(marker)
        if occurrences == 0:
            raise AnnotationError(
                f"clause_index {record.clause_index}: text_start_marker "
                f"{marker[:48]!r} does not occur on page {record.page_start}. "
                f"Markers must be copied verbatim from the extracted text "
                f"(guideline §3.2)."
            )
        if occurrences > 1:
            raise AnnotationError(
                f"clause_index {record.clause_index}: text_start_marker "
                f"{marker[:48]!r} occurs {occurrences} times on page "
                f"{record.page_start}; lengthen it until it is unique "
                f"(guideline §3.2)."
            )
        record.resolved_char_start = start_page.index(marker)

        end_page = page_text[record.page_end]
        end_marker = record.text_end_marker
        if end_page.count(end_marker) == 0:
            raise AnnotationError(
                f"clause_index {record.clause_index}: text_end_marker "
                f"{end_marker[:48]!r} does not occur on page {record.page_end}"
            )
        record.resolved_char_end = end_page.rindex(end_marker) + len(end_marker)


def annotated_span_text(
    record: AnnotatedClause, page_text: Dict[int, str]
) -> str:
    """Reconstruct the annotated clause's text from its resolved markers."""
    if record.resolved_char_start is None or record.resolved_char_end is None:
        raise AnnotationError("locate_annotations must run before reading spans")
    if record.page_start == record.page_end:
        return page_text[record.page_start][
            record.resolved_char_start : record.resolved_char_end
        ]
    parts = [page_text[record.page_start][record.resolved_char_start :]]
    for page_number in range(record.page_start + 1, record.page_end):
        parts.append(page_text[page_number])
    parts.append(page_text[record.page_end][: record.resolved_char_end])
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Matching and metrics
# --------------------------------------------------------------------------- #


@dataclass
class MethodScore:
    """Every Experiment B metric for one method on one sample."""

    method: str
    tolerance: int
    annotated_clauses: int
    predicted_segments: int
    matched: int
    boundary_precision: Optional[float]
    boundary_recall: Optional[float]
    boundary_f1: Optional[float]
    exact_text_matches: int
    exact_match_rate: Optional[float]
    missed_clause_indices: List[int] = field(default_factory=list)
    spurious_segment_ordinals: List[int] = field(default_factory=list)
    incorrect_merges: int = 0
    incorrect_splits: int = 0
    heading_opportunities: int = 0
    heading_preserved: int = 0
    numbering_opportunities: int = 0
    numbering_preserved: int = 0
    page_provenance_correct: int = 0
    page_provenance_checked: int = 0

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["heading_preservation_rate"] = _ratio(
            self.heading_preserved, self.heading_opportunities
        )
        payload["numbering_preservation_rate"] = _ratio(
            self.numbering_preserved, self.numbering_opportunities
        )
        payload["page_provenance_rate"] = _ratio(
            self.page_provenance_correct, self.page_provenance_checked
        )
        return payload


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    """Return a rate, or ``None`` when the denominator is zero.

    ``None`` rather than ``0.0`` deliberately: "no opportunity to preserve a
    heading" and "failed to preserve every heading" are different findings, and
    reporting the first as 0% would be a fabricated failure.
    """
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _segment_boundary(segment: Segment) -> Tuple[int, int]:
    """Return a segment's start boundary as ``(page_number, char_start)``."""
    first = segment.page_offsets[0]
    return first.page_number, first.char_start


def match_boundaries(
    records: Sequence[AnnotatedClause],
    segments: Sequence[Segment],
    tolerance: int,
) -> List[Tuple[int, int]]:
    """Greedily pair annotated and predicted boundaries.

    Returns ``[(clause_index, segment_ordinal), ...]``.

    Greedy nearest-first, each boundary used at most once. Candidate pairs are
    sorted by distance so the closest pairing wins, which keeps two adjacent
    annotated clauses from both matching the same prediction. A globally optimal
    assignment would need the Hungarian algorithm; with a tolerance of a few
    characters the candidate sets barely overlap, so the simpler method gives
    the same answer and stays explainable in a viva.
    """
    candidates: List[Tuple[int, int, int]] = []
    for record in records:
        if record.resolved_char_start is None:
            continue
        for segment in segments:
            page_number, char_start = _segment_boundary(segment)
            if page_number != record.page_start:
                continue
            distance = abs(char_start - record.resolved_char_start)
            if distance <= tolerance:
                candidates.append((distance, record.clause_index, segment.ordinal))

    candidates.sort()
    used_records: set[int] = set()
    used_segments: set[int] = set()
    pairs: List[Tuple[int, int]] = []
    for _, clause_index, ordinal in candidates:
        if clause_index in used_records or ordinal in used_segments:
            continue
        used_records.add(clause_index)
        used_segments.add(ordinal)
        pairs.append((clause_index, ordinal))
    return sorted(pairs)


def score_method(
    method: str,
    records: Sequence[AnnotatedClause],
    segments: Sequence[Segment],
    page_text: Dict[int, str],
    tolerance: int,
) -> MethodScore:
    """Compute every metric for one method against one annotation sample."""
    by_index = {record.clause_index: record for record in records}
    by_ordinal = {segment.ordinal: segment for segment in segments}

    pairs = match_boundaries(records, segments, tolerance)
    matched_records = {clause_index for clause_index, _ in pairs}
    matched_segments = {ordinal for _, ordinal in pairs}

    exact = 0
    heading_opportunities = heading_preserved = 0
    numbering_opportunities = numbering_preserved = 0
    provenance_correct = 0

    for clause_index, ordinal in pairs:
        record = by_index[clause_index]
        segment = by_ordinal[ordinal]

        if _squash(segment.text) == _squash(annotated_span_text(record, page_text)):
            exact += 1
        if record.heading:
            heading_opportunities += 1
            if segment.heading and _squash(segment.heading) == _squash(record.heading):
                heading_preserved += 1
        if record.clause_number:
            numbering_opportunities += 1
            if segment.clause_number == record.clause_number:
                numbering_preserved += 1
        if (
            segment.page_start == record.page_start
            and segment.page_end == record.page_end
        ):
            provenance_correct += 1

    # Merges and splits, computed from page-span overlap rather than from the
    # boundary pairing: a merge is visible precisely when one prediction covers
    # two annotated starts, which the pairing (one-to-one) cannot express.
    merges = 0
    for segment in segments:
        starts_inside = sum(
            1
            for record in records
            if record.resolved_char_start is not None
            and record.page_start == segment.page_start
            and segment.page_offsets[0].char_start
            <= record.resolved_char_start
            < segment.page_offsets[0].char_end
        )
        if starts_inside >= 2:
            merges += 1

    splits = 0
    for record in records:
        if record.resolved_char_start is None or record.resolved_char_end is None:
            continue
        covering = sum(
            1
            for segment in segments
            if segment.page_start == record.page_start
            and record.resolved_char_start
            <= segment.page_offsets[0].char_start
            < record.resolved_char_end
        )
        if covering >= 2:
            splits += 1

    return MethodScore(
        method=method,
        tolerance=tolerance,
        annotated_clauses=len(records),
        predicted_segments=len(segments),
        matched=len(pairs),
        boundary_precision=_ratio(len(pairs), len(segments)),
        boundary_recall=_ratio(len(pairs), len(records)),
        boundary_f1=_f1(_ratio(len(pairs), len(segments)), _ratio(len(pairs), len(records))),
        exact_text_matches=exact,
        exact_match_rate=_ratio(exact, len(records)),
        missed_clause_indices=sorted(
            record.clause_index
            for record in records
            if record.clause_index not in matched_records
        ),
        spurious_segment_ordinals=sorted(
            segment.ordinal
            for segment in segments
            if segment.ordinal not in matched_segments
        ),
        incorrect_merges=merges,
        incorrect_splits=splits,
        heading_opportunities=heading_opportunities,
        heading_preserved=heading_preserved,
        numbering_opportunities=numbering_opportunities,
        numbering_preserved=numbering_preserved,
        page_provenance_correct=provenance_correct,
        page_provenance_checked=len(pairs),
    )


def _f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 4)


# --------------------------------------------------------------------------- #
# Running a version
# --------------------------------------------------------------------------- #


def segments_for_version(
    extraction_path: Path, annotated_pages: Sequence[int]
) -> Tuple[Dict[str, List[Segment]], Dict[int, str], Dict[str, object]]:
    """Re-run both methods and keep only segments starting on annotated pages.

    Restricting to the annotated pages is what keeps precision honest. Scoring a
    whole 37-page document's segments against three annotated pages would count
    every segment on the other 34 pages as a false positive, which measures the
    size of the sample rather than the quality of the method.
    """
    payload = load_extraction(extraction_path)
    document = payload["document"]  # type: ignore[index]
    pages = [
        (entry["page_number"], entry["normalized_text"])  # type: ignore[index]
        for entry in payload["pages"]  # type: ignore[index]
    ]
    pages.sort(key=lambda item: item[0])
    page_text = dict(pages)

    cleaned, _ = clean_pages(pages, CleaningOptions())
    source_lines = build_source_lines(cleaned)
    document_id = str(document["document_id"])  # type: ignore[index]
    version_id = str(document["version_id"])  # type: ignore[index]

    wanted = set(annotated_pages)
    results = {
        METHOD_PARAGRAPH: segment_paragraphs(source_lines, document_id, version_id),
        METHOD_CLAUSE_AWARE: segment_clauses(source_lines, document_id, version_id),
    }
    restricted = {
        method: [
            segment
            for segment in result.segments
            if segment.page_start in wanted
        ]
        for method, result in results.items()
    }
    return restricted, page_text, document  # type: ignore[return-value]


def score_version(
    annotation_path: Path,
    processed_dir: Path,
    tolerance: int = DEFAULT_TOLERANCE,
) -> Dict[str, object]:
    """Score one version's annotations against both methods."""
    records = load_annotations(annotation_path)
    if not records:
        raise AnnotationError(f"{annotation_path} contains no records")

    version_id = records[0].version_id
    if any(record.version_id != version_id for record in records):
        raise AnnotationError(
            f"{annotation_path} mixes version_ids; one file per version"
        )

    extraction_path = processed_dir / version_id / EXTRACTION_FILENAME
    annotated_pages = sorted(
        {
            page
            for record in records
            for page in range(record.page_start, record.page_end + 1)
        }
    )
    segments, page_text, document = segments_for_version(
        extraction_path, annotated_pages
    )
    locate_annotations(records, page_text)

    confident = [record for record in records if not record.uncertain]
    uncertain_count = len(records) - len(confident)

    headline = {
        method: score_method(
            method, confident, method_segments, page_text, tolerance
        ).to_dict()
        for method, method_segments in segments.items()
    }
    including_uncertain = {
        method: score_method(
            method, records, method_segments, page_text, tolerance
        ).to_dict()
        for method, method_segments in segments.items()
    }
    sensitivity = {
        method: {
            str(value): {
                key: score_method(
                    method, confident, method_segments, page_text, value
                ).to_dict()[key]
                for key in ("boundary_precision", "boundary_recall", "boundary_f1")
            }
            for value in SENSITIVITY_TOLERANCES
        }
        for method, method_segments in segments.items()
    }

    return {
        "version_id": version_id,
        "document_title": (document.get("source_metadata") or {}).get("title"),
        "page_count": document.get("page_count"),
        "annotated_pages": annotated_pages,
        "annotation_file": str(annotation_path),
        "annotator": records[0].annotator,
        "guideline_version": records[0].guideline_version,
        "review_status": sorted({record.review_status for record in records}),
        "annotated_clauses_total": len(records),
        "annotated_clauses_confident": len(confident),
        "annotated_clauses_uncertain": uncertain_count,
        "tolerance_chars": tolerance,
        "headline_excluding_uncertain": headline,
        "including_uncertain": including_uncertain,
        "tolerance_sensitivity": sensitivity,
        "inter_annotator_agreement": "PENDING - no second annotator",
    }


# --------------------------------------------------------------------------- #
# Template generation
# --------------------------------------------------------------------------- #


def build_template(
    extraction_path: Path,
    pages: Sequence[int],
    annotator: str,
    marker_chars: int = 50,
) -> List[Dict[str, object]]:
    """Emit a pre-filled annotation template from the clause method's output.

    A **starting point only**. Accepting it unedited would score the segmenter
    against its own output; ``validate --against-template`` reports how many
    records were actually changed, and the report quotes that number
    (guideline §9.1).
    """
    segments, page_text, document = segments_for_version(extraction_path, pages)
    clause_segments = segments[METHOD_CLAUSE_AWARE]
    version_id = str(document["version_id"])  # type: ignore[index]

    records: List[Dict[str, object]] = []
    for index, segment in enumerate(clause_segments):
        squashed = _squash(segment.text)
        start_marker = squashed[:marker_chars]
        end_marker = squashed[-min(marker_chars, len(squashed)) :]
        records.append(
            AnnotatedClause(
                version_id=version_id,
                clause_index=index,
                page_start=segment.page_start,
                page_end=segment.page_end,
                text_start_marker=start_marker,
                text_end_marker=end_marker,
                clause_number=segment.clause_number,
                heading=segment.heading,
                uncertain=False,
                note="TEMPLATE - verify markers against the PDF and correct",
                annotator=annotator,
            ).to_json()
        )
    return records


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.experiment_b_segmentation",
        description=(
            "Experiment B: score paragraph vs clause-aware segmentation against "
            "committed annotations. Computes nothing without annotation records."
        ),
    )
    parser.add_argument(
        "--annotation-dir", type=Path, default=DEFAULT_ANNOTATION_DIR,
        help=f"Where the JSONL files live (default: {DEFAULT_ANNOTATION_DIR}).",
    )
    parser.add_argument(
        "--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR,
        help=f"Where extraction output lives (default: {DEFAULT_PROCESSED_DIR}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser(
        "template",
        help="Emit a pre-filled annotation template to correct by hand.",
    )
    template.add_argument("--version-id", required=True)
    template.add_argument(
        "--pages", required=True,
        help="Page range, e.g. '1-3' or '2,4,6'.",
    )
    template.add_argument("--annotator", default="person1")
    template.add_argument(
        "--output", type=Path, default=None,
        help="Write here instead of stdout.",
    )

    validate = sub.add_parser(
        "validate", help="Check every annotation file before scoring."
    )
    validate.add_argument(
        "--against-template", action="store_true",
        help=(
            "Also report how many records differ from a freshly generated "
            "template, i.e. how much human correction actually happened."
        ),
    )

    score = sub.add_parser("score", help="Compute the metrics.")
    score.add_argument(
        "--tolerance", type=int, default=DEFAULT_TOLERANCE,
        help=(
            "Boundary match tolerance in characters "
            f"(default: {DEFAULT_TOLERANCE})."
        ),
    )
    score.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def parse_pages(spec: str) -> List[int]:
    """Parse ``'1-3'`` or ``'2,4,6'`` into a sorted list of page numbers."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, _, high = part.partition("-")
            pages.update(range(int(low), int(high) + 1))
        else:
            pages.add(int(part))
    if not pages or min(pages) < 1:
        raise ValueError(f"invalid page specification: {spec!r}")
    return sorted(pages)


def annotation_files(annotation_dir: Path) -> List[Path]:
    """Return the primary annotation files, excluding second-annotator files."""
    if not annotation_dir.exists():
        return []
    return sorted(
        path
        for path in annotation_dir.glob("*.jsonl")
        if ".annotator2" not in path.name
    )


def _command_template(args: argparse.Namespace) -> int:
    extraction_path = args.processed_dir / args.version_id / EXTRACTION_FILENAME
    records = build_template(
        extraction_path, parse_pages(args.pages), args.annotator
    )
    text = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(records)} template records to {args.output}")
        print(
            "NOTE: this is a template, not an annotation. Correct every marker "
            "against the PDF before scoring (guideline §9.1)."
        )
    else:
        print(text)
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    paths = annotation_files(args.annotation_dir)
    if not paths:
        print(
            f"No annotation files in {args.annotation_dir}. Nothing to validate.",
            file=sys.stderr,
        )
        return 1

    failures = 0
    for path in paths:
        try:
            records = load_annotations(path)
            version_id = records[0].version_id
            extraction_path = args.processed_dir / version_id / EXTRACTION_FILENAME
            annotated_pages = sorted(
                {
                    page
                    for record in records
                    for page in range(record.page_start, record.page_end + 1)
                }
            )
            _, page_text, _ = segments_for_version(extraction_path, annotated_pages)
            locate_annotations(records, page_text)
        except Exception as exc:  # noqa: BLE001 - reported per file, not hidden
            print(f"{path}\n  INVALID: {exc}", file=sys.stderr)
            failures += 1
            continue

        uncertain = sum(1 for record in records if record.uncertain)
        line = (
            f"{path.name}: {len(records)} records OK, "
            f"pages {annotated_pages}, {uncertain} uncertain, "
            f"annotator={records[0].annotator}, "
            f"review={sorted({r.review_status for r in records})}"
        )
        if args.against_template:
            template = build_template(
                extraction_path, annotated_pages, records[0].annotator
            )
            template_markers = {
                (record["clause_index"], record["text_start_marker"])
                for record in template
            }
            changed = sum(
                1
                for record in records
                if (record.clause_index, record.text_start_marker)
                not in template_markers
            )
            line += f", {changed}/{len(records)} records differ from template"
        print(line)

    return 1 if failures else 0


def _command_score(args: argparse.Namespace) -> int:
    paths = annotation_files(args.annotation_dir)
    if not paths:
        print(
            f"No annotation files in {args.annotation_dir}.\n"
            f"Experiment B computes nothing without annotations - that is "
            f"deliberate (CLAUDE.md §14 rule 1). Produce them with the "
            f"'template' subcommand and the guideline in "
            f"data/annotations/README.md.",
            file=sys.stderr,
        )
        return 1

    versions: List[Dict[str, object]] = []
    failures = 0
    for path in paths:
        try:
            versions.append(score_version(path, args.processed_dir, args.tolerance))
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            print(f"{path}\n  FAILED: {exc}", file=sys.stderr)
            failures += 1
    if not versions:
        return 1

    payload = {
        "experiment": "B - paragraph vs clause-aware segmentation",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "tolerance_chars": args.tolerance,
        "tolerance_note": (
            "A predicted boundary matches an annotated one when it is on the "
            "same page and within this many characters. See the module "
            "docstring; a sensitivity table across tolerances is included per "
            "version."
        ),
        "inter_annotator_agreement": "PENDING - no second annotator",
        "aggregate": _aggregate(versions),
        "versions": versions,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.json"
    results_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {results_path}")
    _print_score_table(payload)
    return 1 if failures else 0


def _aggregate(versions: Sequence[Dict[str, object]]) -> Dict[str, object]:
    """Pool counts across versions, then derive rates from the pooled counts.

    Pooling counts rather than averaging per-version rates: a 3-page sample and
    a 37-page document should not carry equal weight in a mean, and the pooled
    figure is the one a reader can check against the totals.
    """
    aggregate: Dict[str, object] = {}
    for method in (METHOD_PARAGRAPH, METHOD_CLAUSE_AWARE):
        totals = {
            "annotated_clauses": 0, "predicted_segments": 0, "matched": 0,
            "exact_text_matches": 0, "incorrect_merges": 0, "incorrect_splits": 0,
            "heading_opportunities": 0, "heading_preserved": 0,
            "numbering_opportunities": 0, "numbering_preserved": 0,
            "page_provenance_correct": 0, "page_provenance_checked": 0,
        }
        for version in versions:
            score = version["headline_excluding_uncertain"][method]  # type: ignore[index]
            for key in totals:
                totals[key] += score[key]
        precision = _ratio(totals["matched"], totals["predicted_segments"])
        recall = _ratio(totals["matched"], totals["annotated_clauses"])
        aggregate[method] = {
            **totals,
            "boundary_precision": precision,
            "boundary_recall": recall,
            "boundary_f1": _f1(precision, recall),
            "exact_match_rate": _ratio(
                totals["exact_text_matches"], totals["annotated_clauses"]
            ),
            "heading_preservation_rate": _ratio(
                totals["heading_preserved"], totals["heading_opportunities"]
            ),
            "numbering_preservation_rate": _ratio(
                totals["numbering_preserved"], totals["numbering_opportunities"]
            ),
            "page_provenance_rate": _ratio(
                totals["page_provenance_correct"], totals["page_provenance_checked"]
            ),
        }
    aggregate["versions_scored"] = len(versions)
    aggregate["pages_annotated"] = sum(
        len(version["annotated_pages"]) for version in versions  # type: ignore[arg-type]
    )
    return aggregate


def _fmt(value: object) -> str:
    if value is None:
        return "  n/a"
    if isinstance(value, float):
        return f"{value:5.3f}"
    return str(value)


def _print_score_table(payload: Dict[str, object]) -> None:
    print(
        "\nExperiment B - boundary scores "
        f"(tolerance {payload['tolerance_chars']} chars, "
        f"uncertain records excluded)"
    )
    print(f"agreement: {payload['inter_annotator_agreement']}")
    header = (
        f"{'version':<22} {'method':<14} {'ann':>4} {'pred':>5} {'match':>5} "
        f"{'P':>6} {'R':>6} {'F1':>6} {'exact':>6} {'merge':>5} {'split':>5} "
        f"{'num%':>6} {'prov%':>6}"
    )
    print(header)
    print("-" * len(header))
    for version in payload["versions"]:  # type: ignore[union-attr]
        for method, score in version["headline_excluding_uncertain"].items():
            print(
                f"{str(version['version_id'])[:22]:<22} {method:<14} "
                f"{score['annotated_clauses']:>4} {score['predicted_segments']:>5} "
                f"{score['matched']:>5} "
                f"{_fmt(score['boundary_precision']):>6} "
                f"{_fmt(score['boundary_recall']):>6} "
                f"{_fmt(score['boundary_f1']):>6} "
                f"{_fmt(score['exact_match_rate']):>6} "
                f"{score['incorrect_merges']:>5} {score['incorrect_splits']:>5} "
                f"{_fmt(score['numbering_preservation_rate']):>6} "
                f"{_fmt(score['page_provenance_rate']):>6}"
            )
    print("-" * len(header))
    for method, score in payload["aggregate"].items():  # type: ignore[union-attr]
        if not isinstance(score, dict):
            continue
        print(
            f"{'POOLED':<22} {method:<14} "
            f"{score['annotated_clauses']:>4} {score['predicted_segments']:>5} "
            f"{score['matched']:>5} "
            f"{_fmt(score['boundary_precision']):>6} "
            f"{_fmt(score['boundary_recall']):>6} "
            f"{_fmt(score['boundary_f1']):>6} "
            f"{_fmt(score['exact_match_rate']):>6} "
            f"{score['incorrect_merges']:>5} {score['incorrect_splits']:>5} "
            f"{_fmt(score['numbering_preservation_rate']):>6} "
            f"{_fmt(score['page_provenance_rate']):>6}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "template":
        return _command_template(args)
    if args.command == "validate":
        return _command_validate(args)
    if args.command == "score":
        return _command_score(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
