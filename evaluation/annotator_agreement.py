"""E1 — inter-annotator agreement on segmentation boundaries.

**This module computes nothing until a second human has annotated.** It reads
``<VERSION>.jsonl`` (the primary annotator) and ``<VERSION>.annotator2.jsonl``
(an independent annotator) and compares them. When the second file is absent
the result is the literal string ``PENDING`` — never an estimate, never a
default, and never a figure derived from the primary annotations alone
(``CLAUDE.md`` §24 rule 12).

**Why Jaccard and not Cohen's κ.** κ corrects for agreement expected by chance,
which needs a fixed category set and a defensible chance model. Boundary
detection has neither: the "categories" are character positions in a document,
and there is no principled probability that two annotators place a boundary at
the same offset by accident. A κ computed here would have an arbitrary
denominator and look more rigorous than it is. So the reported figure is the
Jaccard index over matched boundaries,

    agreement = matched / (|A| + |B| - matched)

which is interpretable without a chance model: "of all the boundaries either
annotator marked, this fraction were marked by both."

The ±5-character tolerance is the same one Experiment B scores with, for the
same reason: two annotators marking the same boundary may land on either side
of a space.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from evaluation.experiment_b_segmentation import (
    DEFAULT_ANNOTATION_DIR,
    DEFAULT_PROCESSED_DIR,
    EXTRACTION_FILENAME,
    AnnotatedClause,
    AnnotationError,
    load_annotations,
    locate_annotations,
    segments_for_version,
)

__all__ = [
    "PENDING",
    "AgreementError",
    "VersionAgreement",
    "match_annotation_boundaries",
    "agreement_for_version",
    "discover_pairs",
    "run_agreement",
    "format_report",
    "main",
]

#: The only value reported when no second annotator exists. A string, not a
#: number, so it cannot be averaged, plotted or mistaken for a measurement.
PENDING = "PENDING - no second annotator"

DEFAULT_TOLERANCE = 5
DEFAULT_OUTPUT_DIR = Path("evaluation") / "experiment-b"

#: Infix marking a second annotator's file. Must match the exclusion in
#: ``experiment_b_segmentation.annotation_files`` so adding one can never
#: silently move the primary Experiment B metrics.
ANNOTATOR2_INFIX = ".annotator2"


class AgreementError(RuntimeError):
    """An annotation pair cannot be compared. Carries a stable code."""

    code = "AGREEMENT_ERROR"


@dataclass
class VersionAgreement:
    """Agreement for one version, or the reason there is none."""

    version_id: str
    status: str
    tolerance: int = DEFAULT_TOLERANCE
    annotator_primary: Optional[str] = None
    annotator_secondary: Optional[str] = None
    pages_primary: List[int] = field(default_factory=list)
    pages_secondary: List[int] = field(default_factory=list)
    pages_compared: List[int] = field(default_factory=list)
    boundaries_primary: int = 0
    boundaries_secondary: int = 0
    matched: int = 0
    union: int = 0
    agreement: Optional[float] = None
    clause_number_agreement: Optional[float] = None
    clause_number_comparable: int = 0
    only_primary: List[str] = field(default_factory=list)
    only_secondary: List[str] = field(default_factory=list)
    clause_number_disagreements: List[str] = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "version_id": self.version_id,
            "status": self.status,
            "tolerance_chars": self.tolerance,
            "annotator_primary": self.annotator_primary,
            "annotator_secondary": self.annotator_secondary,
            "pages_primary": self.pages_primary,
            "pages_secondary": self.pages_secondary,
            "pages_compared": self.pages_compared,
            "boundaries_primary": self.boundaries_primary,
            "boundaries_secondary": self.boundaries_secondary,
            "matched_boundaries": self.matched,
            "union_boundaries": self.union,
            "boundary_agreement": self.agreement,
            "clause_number_agreement": self.clause_number_agreement,
            "clause_number_comparable": self.clause_number_comparable,
            "boundaries_only_primary": self.only_primary,
            "boundaries_only_secondary": self.only_secondary,
            "clause_number_disagreements": self.clause_number_disagreements,
            "note": self.note,
        }


def match_annotation_boundaries(
    primary: Sequence[AnnotatedClause],
    secondary: Sequence[AnnotatedClause],
    tolerance: int,
) -> List[Tuple[int, int]]:
    """Pair two annotators' boundaries, greedily, nearest first.

    Deliberately the same algorithm as
    ``experiment_b_segmentation.match_boundaries`` — annotation-to-annotation
    instead of annotation-to-segment. Reusing the *shape* matters: an agreement
    figure computed by a more generous matcher than the one scoring the system
    would flatter the annotations relative to the segmenter.

    Each boundary is used at most once, so two adjacent clauses cannot both
    match the same counterpart. Only boundaries on the same page are candidates.
    """
    candidates: List[Tuple[int, int, int]] = []
    for record in primary:
        if record.resolved_char_start is None:
            continue
        for other in secondary:
            if other.resolved_char_start is None:
                continue
            if other.page_start != record.page_start:
                continue
            distance = abs(other.resolved_char_start - record.resolved_char_start)
            if distance <= tolerance:
                candidates.append(
                    (distance, record.clause_index, other.clause_index)
                )

    candidates.sort()
    used_primary: set = set()
    used_secondary: set = set()
    pairs: List[Tuple[int, int]] = []
    for _, primary_index, secondary_index in candidates:
        if primary_index in used_primary or secondary_index in used_secondary:
            continue
        used_primary.add(primary_index)
        used_secondary.add(secondary_index)
        pairs.append((primary_index, secondary_index))
    return sorted(pairs)


def _describe(record: AnnotatedClause) -> str:
    marker = record.text_start_marker[:48]
    number = f" [{record.clause_number}]" if record.clause_number else ""
    return f"p{record.page_start}{number} {marker!r}"


def agreement_for_version(
    version_id: str,
    annotation_dir: Path = DEFAULT_ANNOTATION_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    tolerance: int = DEFAULT_TOLERANCE,
) -> VersionAgreement:
    """Compare two annotators on one version.

    Returns a ``PENDING`` result rather than raising when the second file is
    simply absent — that is the expected state, not an error. Malformed or
    non-overlapping files **do** raise, because those are mistakes someone must
    fix rather than conditions to wait out.
    """
    primary_path = Path(annotation_dir) / f"{version_id}.jsonl"
    secondary_path = Path(annotation_dir) / f"{version_id}{ANNOTATOR2_INFIX}.jsonl"

    if not primary_path.exists():
        raise AgreementError(f"no primary annotation file: {primary_path}")

    if not secondary_path.exists():
        return VersionAgreement(
            version_id=version_id,
            status=PENDING,
            tolerance=tolerance,
            note=(
                f"{secondary_path.name} does not exist. A second annotator must "
                f"complete it; see data/annotations/independent-review/README.md"
            ),
        )

    primary = load_annotations(primary_path)
    secondary = load_annotations(secondary_path)
    if not primary or not secondary:
        raise AgreementError(
            f"{version_id}: an annotation file is empty; nothing to compare"
        )

    primary_annotator = primary[0].annotator
    secondary_annotator = secondary[0].annotator
    if primary_annotator == secondary_annotator:
        # The whole point is independence. Two files by the same annotator
        # would produce a self-agreement figure that means nothing.
        raise AgreementError(
            f"{version_id}: both files declare annotator "
            f"{primary_annotator!r}. Inter-annotator agreement requires two "
            f"different annotators; set 'annotator' to your own name."
        )

    pages_primary = sorted({record.page_start for record in primary})
    pages_secondary = sorted({record.page_start for record in secondary})
    compared = sorted(set(pages_primary) & set(pages_secondary))
    if not compared:
        raise AgreementError(
            f"{version_id}: the two annotators covered no common page "
            f"(primary {pages_primary}, secondary {pages_secondary}). "
            f"Agreement needs the SAME pages annotated twice."
        )

    # Resolve markers to character offsets on the union of pages, then compare
    # only on the overlap.
    _, page_text, _ = segments_for_version(
        Path(processed_dir) / version_id / EXTRACTION_FILENAME,
        sorted(set(pages_primary) | set(pages_secondary)),
    )
    locate_annotations(primary, page_text)
    locate_annotations(secondary, page_text)

    primary_on_overlap = [r for r in primary if r.page_start in compared]
    secondary_on_overlap = [r for r in secondary if r.page_start in compared]

    pairs = match_annotation_boundaries(
        primary_on_overlap, secondary_on_overlap, tolerance
    )
    matched = len(pairs)
    count_primary = len(primary_on_overlap)
    count_secondary = len(secondary_on_overlap)
    union = count_primary + count_secondary - matched

    by_index_primary = {r.clause_index: r for r in primary_on_overlap}
    by_index_secondary = {r.clause_index: r for r in secondary_on_overlap}
    matched_primary = {p for p, _ in pairs}
    matched_secondary = {s for _, s in pairs}

    # Clause-number agreement, on boundaries both annotators found. Compared
    # only where BOTH recorded a number: one annotator leaving it null is a
    # missing observation, not a disagreement.
    comparable = 0
    same_number = 0
    number_disagreements: List[str] = []
    for primary_index, secondary_index in pairs:
        a = by_index_primary[primary_index]
        b = by_index_secondary[secondary_index]
        if a.clause_number is None or b.clause_number is None:
            continue
        comparable += 1
        if a.clause_number.strip() == b.clause_number.strip():
            same_number += 1
        else:
            number_disagreements.append(
                f"p{a.page_start}: primary {a.clause_number!r} vs "
                f"secondary {b.clause_number!r}"
            )

    return VersionAgreement(
        version_id=version_id,
        status="measured",
        tolerance=tolerance,
        annotator_primary=primary_annotator,
        annotator_secondary=secondary_annotator,
        pages_primary=pages_primary,
        pages_secondary=pages_secondary,
        pages_compared=compared,
        boundaries_primary=count_primary,
        boundaries_secondary=count_secondary,
        matched=matched,
        union=union,
        agreement=(matched / union) if union else None,
        clause_number_agreement=(same_number / comparable) if comparable else None,
        clause_number_comparable=comparable,
        only_primary=[
            _describe(by_index_primary[i])
            for i in sorted(set(by_index_primary) - matched_primary)
        ],
        only_secondary=[
            _describe(by_index_secondary[i])
            for i in sorted(set(by_index_secondary) - matched_secondary)
        ],
        clause_number_disagreements=number_disagreements,
    )


def discover_pairs(annotation_dir: Path = DEFAULT_ANNOTATION_DIR) -> List[str]:
    """Version ids that have a primary annotation file."""
    directory = Path(annotation_dir)
    if not directory.exists():
        return []
    return sorted(
        path.name[: -len(".jsonl")]
        for path in directory.glob("*.jsonl")
        if ANNOTATOR2_INFIX not in path.name
    )


def run_agreement(
    annotation_dir: Path = DEFAULT_ANNOTATION_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    tolerance: int = DEFAULT_TOLERANCE,
) -> Dict[str, object]:
    """Agreement for every version that has a primary annotation file."""
    versions = discover_pairs(annotation_dir)
    results = [
        agreement_for_version(version, annotation_dir, processed_dir, tolerance)
        for version in versions
    ]
    measured = [r for r in results if r.status == "measured"]

    pooled: Dict[str, object]
    if not measured:
        pooled = {
            "status": PENDING,
            "versions_measured": 0,
            "versions_pending": len(results),
            "boundary_agreement": None,
            "note": (
                "No second annotator has completed any version. No agreement "
                "figure exists, and none may be quoted."
            ),
        }
    else:
        matched = sum(r.matched for r in measured)
        union = sum(r.union for r in measured)
        comparable = sum(r.clause_number_comparable for r in measured)
        same = sum(
            round((r.clause_number_agreement or 0) * r.clause_number_comparable)
            for r in measured
        )
        pooled = {
            "status": "measured",
            "versions_measured": len(measured),
            "versions_pending": len(results) - len(measured),
            "pages_compared": sum(len(r.pages_compared) for r in measured),
            "matched_boundaries": matched,
            "union_boundaries": union,
            "boundary_agreement": (matched / union) if union else None,
            "clause_number_agreement": (same / comparable) if comparable else None,
            "clause_number_comparable": comparable,
        }

    return {
        "experiment": "E1 - inter-annotator agreement on segmentation boundaries",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "tolerance_chars": tolerance,
        "metric": (
            "Jaccard over matched boundaries: matched / (|A| + |B| - matched). "
            "Not Cohen's kappa - boundary detection has no fixed category set "
            "and no defensible chance model; see the module docstring."
        ),
        "pooled": pooled,
        "versions": [r.to_dict() for r in results],
    }


def format_report(payload: Dict[str, object]) -> str:
    lines = [
        "NiyamSetu inter-annotator agreement (E1)",
        f"generated : {payload['generated_at']}",
        f"tolerance : +/-{payload['tolerance_chars']} characters",
        "",
    ]
    pooled = payload["pooled"]  # type: ignore[index]
    if pooled["status"] != "measured":  # type: ignore[index]
        lines += [
            "POOLED    : PENDING",
            f"  {pooled['note']}",  # type: ignore[index]
            "",
        ]
    else:
        agreement = pooled["boundary_agreement"]  # type: ignore[index]
        lines += [
            f"POOLED boundary agreement : {agreement:.3f}"
            if agreement is not None
            else "POOLED boundary agreement : n/a",
            f"  matched {pooled['matched_boundaries']} of "  # type: ignore[index]
            f"{pooled['union_boundaries']} union boundaries over "  # type: ignore[index]
            f"{pooled['pages_compared']} page(s)",  # type: ignore[index]
            "",
        ]

    for version in payload["versions"]:  # type: ignore[union-attr]
        lines.append(f"{version['version_id']}: {version['status']}")
        if version["status"] != "measured":
            lines.append(f"    {version['note']}")
            continue
        lines.append(
            f"    annotators      : {version['annotator_primary']} vs "
            f"{version['annotator_secondary']}"
        )
        lines.append(f"    pages compared  : {version['pages_compared']}")
        lines.append(
            f"    boundaries      : primary {version['boundaries_primary']}, "
            f"secondary {version['boundaries_secondary']}, "
            f"matched {version['matched_boundaries']}"
        )
        value = version["boundary_agreement"]
        lines.append(
            f"    agreement       : {value:.3f}" if value is not None else
            "    agreement       : n/a"
        )
        for subject in version["boundaries_only_primary"][:5]:  # type: ignore[index]
            lines.append(f"      only primary  : {subject}")
        for subject in version["boundaries_only_secondary"][:5]:  # type: ignore[index]
            lines.append(f"      only secondary: {subject}")
        for subject in version["clause_number_disagreements"][:5]:  # type: ignore[index]
            lines.append(f"      number differs: {subject}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.annotator_agreement",
        description=(
            "E1: inter-annotator agreement on segmentation boundaries. Reports "
            "PENDING until a second annotator's file exists; never estimates."
        ),
    )
    parser.add_argument("--annotation-dir", type=Path, default=DEFAULT_ANNOTATION_DIR)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--no-write", action="store_true", help="Print only; write no files."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        payload = run_agreement(
            args.annotation_dir, args.processed_dir, args.tolerance
        )
    except (AgreementError, AnnotationError) as exc:
        print(f"ERROR [{getattr(exc, 'code', 'ANNOTATION_ERROR')}] {exc}")
        return 1

    print(format_report(payload))
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / "agreement.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nwrote {path}")
    # Exit 0 even when PENDING: "not yet measured" is the expected state, not a
    # build failure. A malformed pair exits 1 above.
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
