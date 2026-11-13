"""E2 — is header/footer cleaning removing the *right* lines?

Cleaning is the only stage in this pipeline that **deletes** text. Its
reversibility is proven (all 151 removals round-trip exactly, and the
segmentation CLI refuses to write if they do not) and its auditability is
proven (every removal carries a non-blank evidence string, enforced in the
schema). Neither of those says the right lines were removed.

This module builds the package for measuring that, and scores it once a human
has labelled. It does **not** label anything itself: classifying lines
automatically and calling the result ground truth would measure the heuristic
against itself (``CLAUDE.md`` §24 rule 12).

Two directions have to be checked, because they fail differently:

* **removed lines** — was each one really boilerplate? A wrongly removed line
  is *deleted content*, the worst outcome this pipeline can produce.
* **retained edge lines** — the top and bottom few lines of each page that
  cleaning left alone. Looking only at removals would score recall as 1.0 by
  construction, because the lines cleaning missed are exactly the ones not in
  the removal list.

Distinct **strings** are labelled, not occurrences: the RBI letterhead appears
on 37 pages but is one judgement, so ~30 labels cover 151 removals.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "LABELS",
    "BOILERPLATE_LABELS",
    "CleaningLabelError",
    "LineCandidate",
    "build_candidates",
    "write_template",
    "load_labels",
    "score_cleaning",
    "format_report",
    "main",
]

DEFAULT_PROCESSED_DIR = Path("data") / "processed"
DEFAULT_LABEL_DIR = Path("data") / "annotations" / "cleaning"
DEFAULT_OUTPUT_DIR = Path("evaluation") / "experiment-e2-cleaning"

TEMPLATE_NAME = "removed-lines.template.csv"
LABELLED_NAME = "removed-lines.labelled.csv"

#: The label vocabulary. Deliberately small: a labeller choosing between four
#: options is consistent; one choosing between twelve is not.
LABELS = ("header", "footer", "page_number", "content", "unsure")

#: Labels meaning "this line is boilerplate and SHOULD be removed".
BOILERPLATE_LABELS = frozenset({"header", "footer", "page_number"})

#: How many lines at each page edge are offered for missed-boilerplate review.
EDGE_LINES = 5


class CleaningLabelError(RuntimeError):
    """The label file is missing, malformed, or inconsistent."""

    code = "CLEANING_LABEL_ERROR"


@dataclass
class LineCandidate:
    """One distinct line string, with every place it occurs."""

    text: str
    #: 'removed' (cleaning deleted it) or 'retained_edge' (left in place).
    disposition: str
    occurrences: int = 0
    versions: List[str] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)
    line_indices: List[int] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.disposition, self.text)

    def position_summary(self) -> str:
        """Where on the page this line sits, as a hint for the labeller."""
        if not self.line_indices:
            return ""
        low, high = min(self.line_indices), max(self.line_indices)
        return f"line {low}" if low == high else f"lines {low}-{high}"


def _version_dirs(processed_dir: Path) -> List[Path]:
    directory = Path(processed_dir)
    if not directory.exists():
        raise CleaningLabelError(f"processed directory not found: {directory}")
    dirs = sorted(child for child in directory.iterdir() if child.is_dir())
    if not dirs:
        raise CleaningLabelError(f"no version directories under {directory}")
    return dirs


def build_candidates(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    edge_lines: int = EDGE_LINES,
) -> List[LineCandidate]:
    """Collect every distinct removed line, plus retained page-edge lines.

    Grouped by exact string. A line removed from 37 pages is one candidate
    with ``occurrences == 37``, so the labeller makes one judgement rather
    than 37 identical ones.
    """
    grouped: Dict[Tuple[str, str], LineCandidate] = {}

    def add(
        text: str,
        disposition: str,
        version: str,
        page: int,
        line_index: int,
        reason: str = "",
        evidence: str = "",
    ) -> None:
        if not text.strip():
            return  # a blank line is not a labelling decision
        key = (disposition, text)
        candidate = grouped.get(key)
        if candidate is None:
            candidate = LineCandidate(text=text, disposition=disposition)
            grouped[key] = candidate
        candidate.occurrences += 1
        if version not in candidate.versions:
            candidate.versions.append(version)
        candidate.pages.append(page)
        candidate.line_indices.append(line_index)
        if reason and reason not in candidate.reasons:
            candidate.reasons.append(reason)
        if evidence and evidence not in candidate.evidence:
            candidate.evidence.append(evidence)

    for version_dir in _version_dirs(processed_dir):
        cleaning_path = version_dir / "page_cleaning.json"
        if not cleaning_path.exists():
            continue
        payload = json.loads(cleaning_path.read_text(encoding="utf-8"))
        version_id = str(payload.get("version_id", version_dir.name))

        for page in payload.get("pages", []):
            page_number = int(page["page_number"])
            for removed in page.get("removed_lines", []):
                add(
                    str(removed["text"]),
                    "removed",
                    version_id,
                    page_number,
                    int(removed["line_index"]),
                    str(removed.get("reason", "")),
                    str(removed.get("evidence", "")),
                )

            # Retained edge lines: what cleaning chose to keep at the margins.
            # Without these, recall is 1.0 by construction.
            kept = [
                line for line in str(page.get("cleaned_text", "")).split("\n")
            ]
            for offset, line in enumerate(kept[:edge_lines]):
                add(line, "retained_edge", version_id, page_number, offset)
            tail_start = max(len(kept) - edge_lines, edge_lines)
            for offset, line in enumerate(kept[tail_start:], start=tail_start):
                add(line, "retained_edge", version_id, page_number, offset)

    # Filter the retained set down to plausible MISSED boilerplate.
    #
    # Offering every page-edge line makes the task impractical (434 rows on
    # this corpus) and most of them are content by construction: a line that
    # appears once, in the middle of a document, at a page edge, is simply the
    # text that happened to land there. Two kinds survive:
    #
    #   * repeated across pages - what a running header or footer looks like;
    #   * anything on page 1 - where a one-off letterhead lives. That case is
    #     a known real failure (Experiment B section 3.9: the capital-adequacy
    #     letterhead appears on 1 of 37 pages, scores 3%, and survives), so
    #     excluding single-occurrence lines would hide the very defect recall
    #     is supposed to catch.
    candidates = [
        candidate
        for candidate in grouped.values()
        if candidate.disposition == "removed"
        or candidate.occurrences > 1
        or 1 in candidate.pages
    ]

    # Removed first, then by how many pages they affect: the labeller meets
    # the highest-impact decisions first.
    return sorted(
        candidates,
        key=lambda c: (c.disposition != "removed", -c.occurrences, c.text),
    )


TEMPLATE_HEADER = [
    "label",
    "disposition",
    "occurrences",
    "versions",
    "pages",
    "position",
    "pipeline_reason",
    "pipeline_evidence",
    "line_text",
]


def write_template(
    candidates: Sequence[LineCandidate], path: Path
) -> Path:
    """Write the CSV a human fills in. The ``label`` column starts empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TEMPLATE_HEADER)
        for candidate in candidates:
            pages = sorted(set(candidate.pages))
            page_summary = (
                f"{pages[0]}-{pages[-1]} ({len(pages)} pages)"
                if len(pages) > 3
                else ",".join(str(p) for p in pages)
            )
            writer.writerow([
                "",  # label - for the human
                candidate.disposition,
                candidate.occurrences,
                ";".join(candidate.versions),
                page_summary,
                candidate.position_summary(),
                ";".join(candidate.reasons),
                " | ".join(candidate.evidence)[:300],
                candidate.text,
            ])
    return path


def load_labels(path: Path) -> Dict[Tuple[str, str], str]:
    """Read the labelled CSV into ``{(disposition, line_text): label}``."""
    if not path.exists():
        raise CleaningLabelError(
            f"no labelled file at {path}. Fill in {TEMPLATE_NAME} and save it "
            f"as {LABELLED_NAME}."
        )
    labels: Dict[Tuple[str, str], str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(TEMPLATE_HEADER) - set(reader.fieldnames or [])
        if missing:
            raise CleaningLabelError(
                f"{path.name} is missing column(s): {sorted(missing)}"
            )
        for number, row in enumerate(reader, start=2):
            label = (row.get("label") or "").strip().lower()
            if not label:
                continue  # unlabelled rows are simply not scored
            if label not in LABELS:
                raise CleaningLabelError(
                    f"{path.name} line {number}: label {label!r} is not one of "
                    f"{list(LABELS)}"
                )
            labels[((row.get("disposition") or "").strip(), row.get("line_text") or "")] = label
    if not labels:
        raise CleaningLabelError(
            f"{path.name} has no labelled rows; nothing to score"
        )
    return labels


def score_cleaning(
    candidates: Sequence[LineCandidate],
    labels: Dict[Tuple[str, str], str],
) -> Dict[str, object]:
    """Score removal decisions against human labels.

    Counted by **occurrence**, not by distinct string: removing a content line
    from 37 pages is 37 deletions of real text, and a per-string count would
    report it as one.

    * precision = correctly removed / removed
    * recall    = correctly removed / (correctly removed + missed boilerplate)
    * content_destroyed = occurrences of lines labelled ``content`` that were
      removed. Reported as an **absolute count**, never a rate: the only
      acceptable value is 0, and a percentage invites "99.3% is fine".
    """
    removed_correct = 0
    removed_wrong = 0
    removed_unsure = 0
    missed = 0
    retained_correct = 0
    retained_unsure = 0
    unlabelled_removed = 0
    unlabelled_retained = 0

    content_destroyed: List[str] = []
    missed_boilerplate: List[str] = []

    for candidate in candidates:
        label = labels.get(candidate.key)
        if label is None:
            if candidate.disposition == "removed":
                unlabelled_removed += candidate.occurrences
            else:
                unlabelled_retained += candidate.occurrences
            continue

        if candidate.disposition == "removed":
            if label in BOILERPLATE_LABELS:
                removed_correct += candidate.occurrences
            elif label == "content":
                removed_wrong += candidate.occurrences
                content_destroyed.append(
                    f"{candidate.occurrences}x {candidate.text[:70]!r}"
                )
            else:
                removed_unsure += candidate.occurrences
        else:
            if label in BOILERPLATE_LABELS:
                missed += candidate.occurrences
                missed_boilerplate.append(
                    f"{candidate.occurrences}x {candidate.text[:70]!r}"
                )
            elif label == "content":
                retained_correct += candidate.occurrences
            else:
                retained_unsure += candidate.occurrences

    removed_scored = removed_correct + removed_wrong
    precision = removed_correct / removed_scored if removed_scored else None
    recall_denominator = removed_correct + missed
    recall = removed_correct / recall_denominator if recall_denominator else None

    return {
        "removed_occurrences_scored": removed_scored,
        "removed_correct": removed_correct,
        "removed_wrong": removed_wrong,
        "removed_unsure": removed_unsure,
        "missed_boilerplate": missed,
        "retained_content_correct": retained_correct,
        "retained_unsure": retained_unsure,
        "unlabelled_removed_occurrences": unlabelled_removed,
        "unlabelled_retained_occurrences": unlabelled_retained,
        "removal_precision": precision,
        "removal_recall": recall,
        "content_destruction_count": removed_wrong,
        "content_destroyed_examples": content_destroyed[:10],
        "missed_boilerplate_examples": missed_boilerplate[:10],
    }


def format_report(payload: Dict[str, object]) -> str:
    lines = [
        "NiyamSetu cleaning correctness (E2)",
        f"generated : {payload['generated_at']}",
        "",
    ]
    if payload["status"] != "measured":  # type: ignore[index]
        lines += [
            "STATUS    : PENDING - no human labels",
            f"  {payload['note']}",  # type: ignore[index]
            "",
            f"candidates prepared : {payload['candidates_total']}",  # type: ignore[index]
            f"  removed (distinct): {payload['candidates_removed']}"  # type: ignore[index]
            f" covering {payload['removed_occurrences']} occurrence(s)",  # type: ignore[index]
            f"  retained edges    : {payload['candidates_retained']}",  # type: ignore[index]
        ]
        return "\n".join(lines)

    scores = payload["scores"]  # type: ignore[index]
    def pct(value):
        return f"{value:.3f}" if isinstance(value, float) else "n/a"
    lines += [
        f"removal precision      : {pct(scores['removal_precision'])}"
        f"  ({scores['removed_correct']}/{scores['removed_occurrences_scored']})",
        f"removal recall         : {pct(scores['removal_recall'])}"
        f"  (missed {scores['missed_boilerplate']})",
        f"CONTENT DESTROYED      : {scores['content_destruction_count']}"
        f"   <- must be 0",
        f"unlabelled (removed)   : {scores['unlabelled_removed_occurrences']}",
        f"unlabelled (retained)  : {scores['unlabelled_retained_occurrences']}",
        "",
    ]
    for subject in scores["content_destroyed_examples"]:
        lines.append(f"  DESTROYED : {subject}")
    for subject in scores["missed_boilerplate_examples"]:
        lines.append(f"  MISSED    : {subject}")
    return "\n".join(lines)


def run(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    label_dir: Path = DEFAULT_LABEL_DIR,
    edge_lines: int = EDGE_LINES,
) -> Dict[str, object]:
    """Build candidates and score them if labels exist."""
    candidates = build_candidates(processed_dir, edge_lines)
    removed = [c for c in candidates if c.disposition == "removed"]
    retained = [c for c in candidates if c.disposition == "retained_edge"]

    payload: Dict[str, object] = {
        "experiment": "E2 - cleaning correctness",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "candidates_total": len(candidates),
        "candidates_removed": len(removed),
        "candidates_retained": len(retained),
        "removed_occurrences": sum(c.occurrences for c in removed),
        "label_vocabulary": list(LABELS),
        "edge_lines_per_page": edge_lines,
    }

    labelled_path = Path(label_dir) / LABELLED_NAME
    if not labelled_path.exists():
        payload["status"] = "PENDING - no human labels"
        payload["note"] = (
            f"Fill in {Path(label_dir) / TEMPLATE_NAME} and save it as "
            f"{labelled_path}. Removal precision, recall and the "
            f"content-destruction count cannot be computed without it."
        )
        payload["scores"] = None
        return payload

    payload["status"] = "measured"
    payload["labels_file"] = str(labelled_path)
    payload["scores"] = score_cleaning(candidates, load_labels(labelled_path))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.cleaning_correctness",
        description=(
            "E2: measure whether header/footer cleaning removes the right "
            "lines. Emits a labelling template; scores only human labels."
        ),
    )
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--label-dir", type=Path, default=DEFAULT_LABEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--edge-lines", type=int, default=EDGE_LINES)
    parser.add_argument(
        "--write-template", action="store_true",
        help="(Re)write the labelling template CSV.",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="Print only; write no files."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.write_template:
            candidates = build_candidates(args.processed_dir, args.edge_lines)
            path = write_template(
                candidates, Path(args.label_dir) / TEMPLATE_NAME
            )
            removed = sum(
                1 for c in candidates if c.disposition == "removed"
            )
            print(
                f"wrote {path}\n"
                f"  {len(candidates)} distinct line(s) to label "
                f"({removed} removed, {len(candidates) - removed} retained edges)\n"
                f"  Fill in the 'label' column with one of {list(LABELS)} and "
                f"save as {LABELLED_NAME}."
            )
            return 0

        payload = run(args.processed_dir, args.label_dir, args.edge_lines)
    except CleaningLabelError as exc:
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
