"""Measure Compare over every real version pair in the corpus.

Run from the repository root, with the version-pair database built::

    .venv\\Scripts\\python -m evaluation.comparison_eval
    .venv\\Scripts\\python -m evaluation.comparison_eval --database niyamsetu_compare

What this measures, and what it deliberately does not
-----------------------------------------------------
There is **no clause-level ground truth** for the nine real RBI master-circular
issues: nobody has annotated 288 pages saying which clause of the 2017 issue
corresponds to which clause of the 2016 one. So this script reports **no
accuracy, no precision and no F1 for alignment**. It would be easy to print
one and it would mean nothing (``CLAUDE.md`` §14 rule 1, §24 rule 12).

What it does report is three things that are measurable without ground truth,
and each one is load-bearing:

**1. Invariants that must hold on every pair.** Conservation (every clause is
classified exactly once), evidence resolution (every citation verifies against
its stored page offsets), determinism (the same request twice gives the same
answer), and direction (a declared ``supersedes`` link fixes which side is
newer). A violation is a defect, with no interpretation needed.

**2. Descriptive statistics per pair.** How many clauses were added, removed,
modified and unchanged; how the matcher established each pair; how often it was
ambiguous. These are **descriptions of behaviour, not scores**: "54 clauses
were removed between the 2016 and 2017 SC/ST circulars" is a fact about what
the system reported, and whether 54 is *correct* is exactly what no number here
can tell you.

**3. One cross-check that needs no annotation.** The same circular appears in
the corpus twice in near-identical form -- ``CLS-0003017`` and ``CLS-0005066``
are the same RBI circular index row, one cleanly extracted and one with stray
spaces inside its reference number. Comparing a version against *itself*
must produce 100% unchanged, and that is asserted: a matcher that cannot
recognise a document as identical to itself cannot be trusted on two documents
that differ.

Reads only. Writes ``evaluation/comparison/`` and nothing else.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.app.comparison.alignment import AlignmentConfig
from backend.app.comparison.service import ComparisonService

__all__ = ["OUTPUT_DIR", "evaluate", "main"]

OUTPUT_DIR = Path("evaluation") / "comparison"


def _pair_rows(service: ComparisonService) -> List[Dict[str, Any]]:
    options = service.options()
    rows: List[Dict[str, Any]] = []
    for document in options["documents"]:
        for pair in document["pairs"]:
            rows.append(
                {
                    "document_id": document["document_id"],
                    "title": document["title"],
                    **pair,
                }
            )
    return rows


def _evaluate_pair(
    service: ComparisonService, pair: Dict[str, Any], method: str
) -> Dict[str, Any]:
    started = time.perf_counter()
    result = service.compare(
        pair["base_version_id"],
        pair["compare_version_id"],
        method=method,
        limit=5000,
    )
    wall_ms = (time.perf_counter() - started) * 1000.0

    summary = result["summary"]
    counts = summary["counts"]
    base_clauses = result["clause_counts"]["base"]
    compare_clauses = result["clause_counts"]["compare"]
    matched = counts["modified"] + counts["unchanged"]

    sides = [
        item[side]
        for item in result["items"]
        for side in ("old", "new")
        if item[side] is not None
    ]
    by_status: Dict[str, int] = {}
    problems: List[str] = []
    for side in sides:
        status = side["evidence"]["status"]
        by_status[status] = by_status.get(status, 0) + 1
        if status != "resolved":
            problems.extend(
                f"{side['clause_id']}: {reason}"
                for reason in side["evidence"]["problems"]
            )

    quotes_without_resolution = sum(
        1
        for side in sides
        if side["evidence"]["quote"] is not None
        and side["evidence"]["status"] != "resolved"
    )

    # Determinism: the same request again must give the same id and the same
    # ordered change ids.
    repeat = service.compare(
        pair["base_version_id"],
        pair["compare_version_id"],
        method=method,
        limit=5000,
    )
    deterministic = result["comparison_id"] == repeat["comparison_id"] and [
        item["change_id"] for item in result["items"]
    ] == [item["change_id"] for item in repeat["items"]]

    invariants = {
        "conservation_base": matched + counts["removed"] == base_clauses,
        "conservation_compare": matched + counts["added"] == compare_clauses,
        "every_change_has_a_side": all(
            item["old"] is not None or item["new"] is not None
            for item in result["items"]
        ),
        "no_quote_without_resolution": quotes_without_resolution == 0,
        "deterministic": deterministic,
        "direction_as_requested": (
            result["base_version"]["version_id"] == pair["base_version_id"]
        ),
        "same_document": result["same_document"],
    }

    return {
        "document_id": pair["document_id"],
        "base_version_id": pair["base_version_id"],
        "compare_version_id": pair["compare_version_id"],
        "base_label": pair["base_label"],
        "compare_label": pair["compare_label"],
        "consecutive": pair["consecutive"],
        "supersedes_declared": pair["supersedes_declared"],
        "method": method,
        "comparison_id": result["comparison_id"],
        "clauses": {"base": base_clauses, "compare": compare_clauses},
        "counts": counts,
        "match_basis_counts": summary["match_basis_counts"],
        "confidence_counts": summary["confidence_counts"],
        "ambiguous_matches": summary["ambiguous_matches"],
        "renumbered_matches": summary["renumbered_matches"],
        "reordered_matches": summary["reordered_matches"],
        "unchanged_ratio": summary["unchanged_ratio"],
        "similarity_mean": summary["similarity_mean"],
        "evidence_sides": len(sides),
        "evidence_by_status": by_status,
        "evidence_problems": problems[:20],
        "warnings": result["warnings"],
        "invariants": invariants,
        "align_ms": result["took_ms"],
        "wall_ms": round(wall_ms, 1),
    }


def _self_comparison_check(
    service: ComparisonService, version_id: str, method: str
) -> Dict[str, Any]:
    """Compare a version with itself. Must be 100% unchanged.

    The one correctness check available without annotation. ``compare`` refuses
    the same version twice -- rightly, it is a client mistake -- so this runs
    the matcher directly on the same clause list twice, which is the same code
    path the service uses.
    """
    from backend.app.comparison import repository
    from backend.app.comparison.alignment import align, summarize
    from backend.app.db.engine import connection

    with connection(service.url) as conn:
        version = repository.load_version(version_id, conn=conn)
        clauses = repository.load_clauses(version.version_id, method=method, conn=conn)

    changes = align(clauses, list(clauses), AlignmentConfig())
    summary = summarize(changes)
    counts = summary["counts"]
    return {
        "version_id": version_id,
        "clauses": len(clauses),
        "counts": counts,
        "all_unchanged": (
            counts["unchanged"] == len(clauses)
            and counts["added"] == 0
            and counts["removed"] == 0
            and counts["modified"] == 0
        ),
        "no_spurious_reordering": summary["reordered_matches"] == 0,
        "no_spurious_renumbering": summary["renumbered_matches"] == 0,
    }


def _threshold_sweep(
    service: ComparisonService, pair: Dict[str, Any], method: str
) -> List[Dict[str, Any]]:
    """How the result moves with ``match_threshold``. A sensitivity check.

    Not a tuning exercise -- there is no ground truth to tune against. It
    exists so the report can say *how much* the headline counts depend on the
    threshold, which is the honest way to present a number that has one.
    """
    out = []
    for threshold in (0.20, 0.35, 0.45, 0.60, 0.80):
        result = service.compare(
            pair["base_version_id"],
            pair["compare_version_id"],
            method=method,
            config=AlignmentConfig(match_threshold=threshold),
            limit=5000,
            include_text=False,
            include_diff=False,
            include_explanation=False,
        )
        out.append(
            {
                "match_threshold": threshold,
                "counts": result["summary"]["counts"],
                "ambiguous": result["summary"]["ambiguous_matches"],
                "match_basis_counts": result["summary"]["match_basis_counts"],
            }
        )
    return out


def evaluate(
    url: Optional[str] = None, method: str = "clause_aware"
) -> Dict[str, Any]:
    service = ComparisonService(url=url)
    options = service.options()
    pairs = _pair_rows(service)
    if not pairs:
        raise SystemExit(
            "this database has no comparable version pairs. Build the "
            "version-pair corpus first:\n"
            "  .venv\\Scripts\\python data/version_pairs/fetch_version_pairs.py\n"
            "  .venv\\Scripts\\python -m backend.scripts.setup_compare_database"
        )

    results = [_evaluate_pair(service, pair, method) for pair in pairs]

    declared = [row for row in results if row["supersedes_declared"]]
    consecutive = [row for row in results if row["consecutive"]]

    failed_invariants = [
        {
            "pair": f"{row['base_version_id']}->{row['compare_version_id']}",
            "failed": [name for name, ok in row["invariants"].items() if not ok],
        }
        for row in results
        if not all(row["invariants"].values())
    ]

    total_sides = sum(row["evidence_sides"] for row in results)
    resolved_sides = sum(
        row["evidence_by_status"].get("resolved", 0) for row in results
    )

    # The self-comparison check, on the longest version in the corpus: if a
    # matcher is going to fail to recognise a document as itself, it will fail
    # on the one with the most repeated boilerplate.
    longest = max(
        (
            version
            for document in options["documents"]
            for version in document["versions"]
        ),
        key=lambda version: version["clause_count"],
    )
    self_check = _self_comparison_check(service, longest["version_id"], method)

    sweep_pair = declared[0] if declared else results[0]
    sweep = _threshold_sweep(service, sweep_pair, method)

    return {
        "generated_at": time.strftime("%Y-%m-%d"),
        "database": "(from the environment; name not recorded here)",
        "method": method,
        "corpus": options["corpus"],
        "pairs_evaluated": len(results),
        "declared_pairs": len(declared),
        "consecutive_pairs": len(consecutive),
        "ground_truth": (
            "NONE. No clause-level annotation exists for these documents, so "
            "this report contains no alignment accuracy, precision, recall or "
            "F1. Every count below describes what the system reported, not "
            "whether it was right."
        ),
        "invariants": {
            "pairs_passing_all": len(results) - len(failed_invariants),
            "pairs_failing": len(failed_invariants),
            "failures": failed_invariants,
        },
        "evidence": {
            "sides_checked": total_sides,
            "sides_resolved": resolved_sides,
            "resolution_rate": (
                round(resolved_sides / total_sides, 6) if total_sides else None
            ),
            "by_status": _merge_counters(row["evidence_by_status"] for row in results),
        },
        "self_comparison": self_check,
        "threshold_sensitivity": {
            "pair": f"{sweep_pair['base_version_id']}->{sweep_pair['compare_version_id']}",
            "sweep": sweep,
        },
        "timing": {
            "align_ms_median": round(
                statistics.median(row["align_ms"] for row in results), 1
            ),
            "align_ms_max": round(max(row["align_ms"] for row in results), 1),
            "largest_pair": max(
                results, key=lambda row: row["clauses"]["base"] * row["clauses"]["compare"]
            )["base_version_id"],
        },
        "unchanged_ratio": {
            "declared_consecutive_median": (
                round(
                    statistics.median(
                        row["unchanged_ratio"] or 0.0
                        for row in results
                        if row["supersedes_declared"] and row["consecutive"]
                    ),
                    4,
                )
                if any(r["supersedes_declared"] and r["consecutive"] for r in results)
                else None
            ),
        },
        "pairs": results,
    }


def _merge_counters(dicts) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in dicts:
        for key, value in item.items():
            out[key] = out.get(key, 0) + value
    return out


def _format(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    add = lines.append
    add("NiyamSetu — Compare evaluation over the real version-pair corpus")
    add("=" * 78)
    add("")
    add("READ THIS FIRST")
    add("-" * 78)
    for line in _wrap(report["ground_truth"], 74):
        add(f"  {line}")
    add("")
    add("CORPUS")
    add("-" * 78)
    for key, value in report["corpus"].items():
        add(f"  {key:24} {value}")
    add(f"  {'pairs evaluated':24} {report['pairs_evaluated']}")
    add(f"  {'authority-declared':24} {report['declared_pairs']}")
    add(f"  {'consecutive':24} {report['consecutive_pairs']}")
    add("")
    add("INVARIANTS (a failure here is a defect, not a judgement call)")
    add("-" * 78)
    inv = report["invariants"]
    add(f"  pairs passing every invariant : {inv['pairs_passing_all']} / {report['pairs_evaluated']}")
    if inv["failures"]:
        for failure in inv["failures"]:
            add(f"    FAILED {failure['pair']}: {', '.join(failure['failed'])}")
    add("")
    add("EVIDENCE RESOLUTION")
    add("-" * 78)
    ev = report["evidence"]
    add(f"  clause sides checked          : {ev['sides_checked']}")
    add(f"  verified against page offsets : {ev['sides_resolved']}")
    add(f"  resolution rate               : {ev['resolution_rate']}")
    add(f"  by status                     : {json.dumps(ev['by_status'])}")
    add("")
    add("SELF-COMPARISON (the one check that needs no annotation)")
    add("-" * 78)
    sc = report["self_comparison"]
    add(f"  {sc['version_id']} against itself, {sc['clauses']} clauses")
    add(f"  all unchanged                 : {sc['all_unchanged']}")
    add(f"  no spurious reordering        : {sc['no_spurious_reordering']}")
    add(f"  no spurious renumbering       : {sc['no_spurious_renumbering']}")
    add(f"  counts                        : {json.dumps(sc['counts'])}")
    add("")
    add("PER-PAIR BEHAVIOUR (descriptions, not scores)")
    add("-" * 78)
    add(
        f"  {'pair':26} {'clauses':>13} {'+':>5} {'-':>5} {'~':>5} {'=':>5} "
        f"{'unch%':>7} {'amb':>4} {'renum':>6} {'ms':>7}"
    )
    for row in report["pairs"]:
        tag = "*" if row["supersedes_declared"] else " "
        pair = f"{tag}{row['base_label']}->{row['compare_label']}"
        counts = row["counts"]
        add(
            f"  {pair[:26]:26} "
            f"{row['clauses']['base']:>6}->{row['clauses']['compare']:<6} "
            f"{counts['added']:>5} {counts['removed']:>5} {counts['modified']:>5} "
            f"{counts['unchanged']:>5} "
            f"{(row['unchanged_ratio'] or 0) * 100:>6.1f}% "
            f"{row['ambiguous_matches']:>4} {row['renumbered_matches']:>6} "
            f"{row['align_ms']:>7.0f}"
        )
    add("  * = the newer issue's own metadata declares it supersedes the older")
    add("")
    add("HOW THE MATCHER ESTABLISHED ITS PAIRS")
    add("-" * 78)
    bases = _merge_counters(row["match_basis_counts"] for row in report["pairs"])
    total_basis = sum(bases.values()) or 1
    for name in ("exact_text", "clause_number", "similarity"):
        value = bases.get(name, 0)
        add(f"  {name:16} {value:>7}  ({value / total_basis * 100:.1f}%)")
    add("")
    add("THRESHOLD SENSITIVITY")
    add("-" * 78)
    add(f"  pair: {report['threshold_sensitivity']['pair']}")
    add(f"  {'match_threshold':>16} {'+':>6} {'-':>6} {'~':>6} {'=':>6} {'amb':>5}")
    for row in report["threshold_sensitivity"]["sweep"]:
        counts = row["counts"]
        add(
            f"  {row['match_threshold']:>16} {counts['added']:>6} "
            f"{counts['removed']:>6} {counts['modified']:>6} "
            f"{counts['unchanged']:>6} {row['ambiguous']:>5}"
        )
    add("")
    add("TIMING")
    add("-" * 78)
    add(f"  median alignment : {report['timing']['align_ms_median']} ms")
    add(f"  slowest pair     : {report['timing']['align_ms_max']} ms")
    add("")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        if sum(len(w) + 1 for w in current) + len(word) > width and current:
            lines.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.comparison_eval",
        description=(
            "Measure Compare over every real version pair. Reports invariants "
            "and descriptive statistics; reports no alignment accuracy, "
            "because no clause-level ground truth exists."
        ),
    )
    parser.add_argument(
        "--database",
        default="niyamsetu_compare",
        help="Database holding the version-pair corpus.",
    )
    parser.add_argument("--method", default="clause_aware",
                        choices=["clause_aware", "paragraph"])
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    from backend.scripts.setup_compare_database import compare_database_url

    url = compare_database_url(args.database)
    report = evaluate(url, method=args.method)
    report["database"] = args.database

    text = _format(report)
    print(text)

    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "results.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        (args.output_dir / "summary.txt").write_text(text, encoding="utf-8")
        print(f"written: {args.output_dir / 'results.json'}")
        print(f"written: {args.output_dir / 'summary.txt'}")

    ok = report["invariants"]["pairs_failing"] == 0 and report["self_comparison"][
        "all_unchanged"
    ]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
