"""Score the entity extractor against the committed gold annotations.

Run from the repository root::

    .venv\\Scripts\\python -m evaluation.entity_extraction_eval --validate
    .venv\\Scripts\\python -m evaluation.entity_extraction_eval

The brief asks for "a reproducible benchmark where practical", measuring
precision, recall, F1, per-entity performance and inference time, and comparing
regex against CRF and against a pretrained or fine-tuned transformer. This
module is that benchmark. It measures what can honestly be measured here and
**reports the other two conditions as NOT RUN with the reason**, which
``CLAUDE.md`` §14 and §24 rule 12 require: a fabricated metric is worse than a
missing one, and "BERT scored 0.91" with no model and no training data would
be a fabrication.

Why only one condition has a score
----------------------------------
A CRF or a fine-tuned transformer needs **labelled training data**. The gold
set here is **30 clauses and 88 mentions**, drawn as a *test* sample. Splitting
it would leave roughly 20 clauses to train on and 10 to test on, and a
sequence labeller trained on 20 clauses would be measuring the split rather
than the model. Published Indian-legal NER work trains on thousands of
sentences; this project has, deliberately, a hand-curated corpus.

Neither ``sklearn-crfsuite`` nor ``transformers``/``torch`` is installed in
``.venv``, and neither is in ``requirements.txt``. Installing them would need
the written justification ``CLAUDE.md`` §24 rule 5 demands, and the
justification cannot be written, because the thing they would be used for --
a trained comparison -- is not possible with 88 labelled mentions.

So the honest result is: **the regex condition is measured, and the gap it
leaves is quantified per label.** The AUTHORITY row is the argument for a
statistical model, stated as a number rather than an opinion: a closed list of
patterns cannot find the forty-odd individual bank names in a district
allocation table, and a sequence labeller with enough data could. That is a
finding, and it is more useful than a number nobody can reproduce.

The two match modes
-------------------
``exact``
    the predicted span's start, end and label all equal a gold span's. What a
    span-level NER evaluation normally means.
``relaxed``
    the predicted span overlaps a gold span with the same label. What matters
    for the product: a highlight that covers most of a reference number is
    useful to a reader; a miss is not.

Both are reported because they disagree, and the direction of the disagreement
is itself a finding -- it locates the patterns whose boundaries are wrong as
opposed to the ones that do not fire at all.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.comparison.entities import ENTITY_LABELS, extract_entities

__all__ = [
    "GOLD_PATH",
    "OUTPUT_DIR",
    "CONDITIONS",
    "load_gold",
    "validate_gold",
    "score",
    "run",
    "main",
]

GOLD_PATH = Path("data") / "annotations" / "entities" / "gold.json"
OUTPUT_DIR = Path("evaluation") / "entity-extraction"

#: Every condition the brief names, and its status. A condition that was not
#: run says so here, with the reason, and contributes no numbers anywhere.
CONDITIONS: Dict[str, Dict[str, Any]] = {
    "regex": {
        "status": "MEASURED",
        "implementation": "backend/app/comparison/entities.py",
        "description": (
            "Ordered pattern cascade with precedence-based overlap "
            "resolution. Grown from the teammate's prototype extract_facts."
        ),
    },
    "crf": {
        "status": "NOT RUN",
        "implementation": None,
        "reason": (
            "A CRF is a supervised sequence labeller and needs labelled "
            "training data. The gold set is 30 clauses / 88 mentions and was "
            "drawn as a test sample; training on part of it would leave ~20 "
            "clauses to fit and ~10 to score, which measures the split rather "
            "than the model. sklearn-crfsuite is not installed and is not in "
            "requirements.txt; pinning it would need the written "
            "justification CLAUDE.md §24 rule 5 requires, and that "
            "justification cannot honestly be written for a comparison that "
            "the data cannot support."
        ),
        "what_would_change_this": (
            "Of the order of 1,000 labelled sentences, which is a corpus "
            "decision (D-10) and an annotation effort, not a code change. The "
            "guideline and the scorer are both in place, so the labels could "
            "be produced without redesigning anything."
        ),
    },
    "transformer": {
        "status": "NOT RUN",
        "implementation": None,
        "reason": (
            "Same data problem as the CRF, plus a compute one: neither "
            "transformers nor torch is installed, there is no GPU on this "
            "machine, and a zero-shot pretrained NER would predict PERSON / "
            "ORG / LOC / DATE rather than this project's eight labels -- so "
            "its output would not be comparable to the gold set without a "
            "label-mapping decision that would itself need validating."
        ),
        "what_would_change_this": (
            "A label mapping agreed with the team (ORG -> AUTHORITY, DATE -> "
            "DATE, and an explicit decision about the five labels no "
            "general-purpose NER has) would make a **zero-shot** comparison "
            "possible on AUTHORITY and DATE alone, without any training data. "
            "That is the cheapest honest next step and it is recommended in "
            "the implementation report."
        ),
    },
}


def load_gold(path: Path = GOLD_PATH) -> Dict[str, Any]:
    """Read the gold annotations, resolving each mention to a character span.

    ``gold.json`` stores a mention as a label and a surface string, because a
    human has to be able to read and check it. Resolution happens here: each
    string is matched to its first not-yet-claimed occurrence, in the order
    the mentions are listed. :func:`validate_gold` fails loudly if any string
    cannot be located, so the stored form and the scored form cannot drift.
    """
    if not path.exists():
        raise SystemExit(
            f"no gold annotations at {path}. See "
            f"data/annotations/entities/README.md"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    for clause in payload["clauses"]:
        text = clause["text"]
        cursor: Dict[str, int] = {}
        spans: List[Dict[str, Any]] = []
        for mention in clause["entities"]:
            surface = mention["text"]
            start = text.find(surface, cursor.get(surface, 0))
            if start < 0:
                spans.append(
                    {
                        "label": mention["label"],
                        "text": surface,
                        "start": -1,
                        "end": -1,
                    }
                )
                continue
            cursor[surface] = start + len(surface)
            spans.append(
                {
                    "label": mention["label"],
                    "text": surface,
                    "start": start,
                    "end": start + len(surface),
                }
            )
        clause["spans"] = spans
    return payload


def validate_gold(payload: Dict[str, Any]) -> List[str]:
    """Every problem with the gold file, as a list of sentences.

    Checks that each mention resolves, that resolved spans slice back to their
    own surface string, that no two gold spans overlap, and that every label
    is one the extractor publishes. An empty list means the file is sound.
    """
    problems: List[str] = []
    for clause in payload["clauses"]:
        cid = clause["clause_id"]
        text = clause["text"]
        spans = clause["spans"]
        for span in spans:
            if span["start"] < 0:
                problems.append(
                    f"{cid}: {span['label']} {span['text']!r} does not occur "
                    f"in the clause text (or its occurrences are exhausted)"
                )
                continue
            if text[span["start"] : span["end"]] != span["text"]:
                problems.append(
                    f"{cid}: {span['label']} span {span['start']}:{span['end']} "
                    f"does not slice back to {span['text']!r}"
                )
            if span["label"] not in ENTITY_LABELS:
                problems.append(
                    f"{cid}: {span['label']!r} is not a label the extractor "
                    f"publishes; available: {sorted(ENTITY_LABELS)}"
                )
        ordered = sorted(
            (s for s in spans if s["start"] >= 0), key=lambda s: (s["start"], s["end"])
        )
        for i in range(1, len(ordered)):
            if ordered[i]["start"] < ordered[i - 1]["end"]:
                problems.append(
                    f"{cid}: gold spans overlap: "
                    f"{ordered[i-1]['text']!r} and {ordered[i]['text']!r}"
                )
    return problems


def _match_exact(
    predicted: Sequence[Dict[str, Any]], gold: Sequence[Dict[str, Any]]
) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """Greedy one-to-one exact matching. Returns (pairs, unmatched_pred, unmatched_gold)."""
    used_gold = set()
    pairs: List[Tuple[int, int]] = []
    for pi, prediction in enumerate(predicted):
        for gi, truth in enumerate(gold):
            if gi in used_gold:
                continue
            if (
                prediction["label"] == truth["label"]
                and prediction["start"] == truth["start"]
                and prediction["end"] == truth["end"]
            ):
                used_gold.add(gi)
                pairs.append((pi, gi))
                break
    matched_pred = {pi for pi, _ in pairs}
    return (
        pairs,
        [pi for pi in range(len(predicted)) if pi not in matched_pred],
        [gi for gi in range(len(gold)) if gi not in used_gold],
    )


def _match_relaxed(
    predicted: Sequence[Dict[str, Any]], gold: Sequence[Dict[str, Any]]
) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """One-to-one matching on overlap with the same label, best overlap first.

    Best-overlap-first rather than in order: a pattern that produces one long
    span covering two gold mentions should be credited with the one it
    overlaps most, not with whichever came first in the list.
    """
    candidates: List[Tuple[int, int, int]] = []
    for pi, prediction in enumerate(predicted):
        for gi, truth in enumerate(gold):
            if prediction["label"] != truth["label"]:
                continue
            overlap = min(prediction["end"], truth["end"]) - max(
                prediction["start"], truth["start"]
            )
            if overlap > 0:
                candidates.append((overlap, pi, gi))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_pred = set()
    used_gold = set()
    pairs: List[Tuple[int, int]] = []
    for _, pi, gi in candidates:
        if pi in used_pred or gi in used_gold:
            continue
        used_pred.add(pi)
        used_gold.add(gi)
        pairs.append((pi, gi))
    return (
        pairs,
        [pi for pi in range(len(predicted)) if pi not in used_pred],
        [gi for gi in range(len(gold)) if gi not in used_gold],
    )


def _prf(true_positives: int, false_positives: int, false_negatives: int) -> Dict[str, Any]:
    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else None
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else None
    )
    if precision is None or recall is None or (precision + recall) == 0:
        f1: Optional[float] = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
    }


def score(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run the regex condition over the gold set and compute every metric."""
    clauses = payload["clauses"]

    # Timed separately from scoring: the brief asks for inference time, and a
    # figure that included the scorer's own bookkeeping would not be it.
    predictions: List[List[Dict[str, Any]]] = []
    characters = 0
    started = time.perf_counter()
    for clause in clauses:
        characters += len(clause["text"])
        predictions.append(
            [entity.to_dict() for entity in extract_entities(clause["text"])]
        )
    elapsed = time.perf_counter() - started

    modes: Dict[str, Any] = {}
    for mode, matcher in (("exact", _match_exact), ("relaxed", _match_relaxed)):
        per_label: Dict[str, Dict[str, int]] = {
            label: {"tp": 0, "fp": 0, "fn": 0} for label in ENTITY_LABELS
        }
        false_positives: List[Dict[str, Any]] = []
        false_negatives: List[Dict[str, Any]] = []

        for clause, predicted in zip(clauses, predictions):
            gold = [s for s in clause["spans"] if s["start"] >= 0]
            pairs, unmatched_pred, unmatched_gold = matcher(predicted, gold)
            for _, gi in pairs:
                per_label[gold[gi]["label"]]["tp"] += 1
            for pi in unmatched_pred:
                label = predicted[pi]["label"]
                per_label.setdefault(label, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
                false_positives.append(
                    {
                        "clause_id": clause["clause_id"],
                        "label": label,
                        "text": predicted[pi]["text"],
                        "start": predicted[pi]["start"],
                    }
                )
            for gi in unmatched_gold:
                per_label[gold[gi]["label"]]["fn"] += 1
                false_negatives.append(
                    {
                        "clause_id": clause["clause_id"],
                        "label": gold[gi]["label"],
                        "text": gold[gi]["text"],
                        "start": gold[gi]["start"],
                    }
                )

        labels = {
            label: _prf(counts["tp"], counts["fp"], counts["fn"])
            for label, counts in per_label.items()
            if counts["tp"] or counts["fp"] or counts["fn"]
        }
        totals = _prf(
            sum(c["tp"] for c in per_label.values()),
            sum(c["fp"] for c in per_label.values()),
            sum(c["fn"] for c in per_label.values()),
        )
        # Macro F1 over labels that appear in the gold set, so a label with no
        # gold mentions cannot inflate or deflate it.
        scored = [
            labels[label]["f1"]
            for label in labels
            if labels[label]["f1"] is not None
            and per_label[label]["tp"] + per_label[label]["fn"] > 0
        ]
        totals["macro_f1"] = (
            round(sum(scored) / len(scored), 4) if scored else None
        )
        modes[mode] = {
            "overall": totals,
            "per_label": labels,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
        }

    gold_total = sum(
        1 for clause in clauses for span in clause["spans"] if span["start"] >= 0
    )
    return {
        "condition": "regex",
        "clauses": len(clauses),
        "characters": characters,
        "gold_mentions": gold_total,
        "predicted_mentions": sum(len(p) for p in predictions),
        "timing": {
            "elapsed_seconds": round(elapsed, 6),
            "ms_per_clause": round(elapsed * 1000 / len(clauses), 4) if clauses else None,
            "chars_per_second": round(characters / elapsed, 1) if elapsed > 0 else None,
        },
        "modes": modes,
    }


def _format(result: Dict[str, Any], payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    add = lines.append
    add("NiyamSetu — entity-extraction benchmark")
    add("=" * 72)
    add("")
    add("READ THIS FIRST")
    add("-" * 72)
    add(
        f"  sample                 : {result['clauses']} clauses, "
        f"{result['gold_mentions']} gold mentions"
    )
    add(
        f"  annotator              : {payload['annotator']['count']}, "
        f"independent of system: {payload['annotator']['independent_of_system']}"
    )
    add(f"  agreement              : {payload['annotator']['agreement']}")
    add(
        "  therefore              : DIAGNOSTIC, not a validated accuracy "
        "measurement."
    )
    add(f"  guideline              : {payload['guideline']}")
    add("")
    add("CONDITIONS")
    add("-" * 72)
    for name, meta in CONDITIONS.items():
        add(f"  {name:12} {meta['status']}")
        if meta["status"] != "MEASURED":
            add(f"               reason: {meta['reason'][:200]}")
    add("")
    add("REGEX CONDITION")
    add("-" * 72)
    add(
        f"  predicted {result['predicted_mentions']} mentions against "
        f"{result['gold_mentions']} gold"
    )
    add(
        f"  inference: {result['timing']['ms_per_clause']} ms/clause, "
        f"{result['timing']['chars_per_second']} chars/second"
    )
    for mode in ("exact", "relaxed"):
        block = result["modes"][mode]
        overall = block["overall"]
        add("")
        add(f"  {mode.upper()} span match")
        add(
            f"    overall    P {overall['precision']}  R {overall['recall']}  "
            f"F1 {overall['f1']}   (macro F1 {overall['macro_f1']})"
        )
        add(f"    {'label':14} {'P':>7} {'R':>7} {'F1':>7} {'TP':>4} {'FP':>4} {'FN':>4}")
        for label in sorted(block["per_label"]):
            row = block["per_label"][label]
            add(
                f"    {label:14} {str(row['precision']):>7} "
                f"{str(row['recall']):>7} {str(row['f1']):>7} "
                f"{row['true_positives']:>4} {row['false_positives']:>4} "
                f"{row['false_negatives']:>4}"
            )
    add("")
    add("FAILURE CASES (exact match)")
    add("-" * 72)
    exact = result["modes"]["exact"]
    add(f"  false positives: {len(exact['false_positives'])}")
    for item in exact["false_positives"][:12]:
        add(f"    {item['clause_id']} {item['label']:13} {item['text']!r}")
    if len(exact["false_positives"]) > 12:
        add(f"    ... and {len(exact['false_positives']) - 12} more")
    add(f"  false negatives: {len(exact['false_negatives'])}")
    for item in exact["false_negatives"][:12]:
        add(f"    {item['clause_id']} {item['label']:13} {item['text']!r}")
    if len(exact["false_negatives"]) > 12:
        add(f"    ... and {len(exact['false_negatives']) - 12} more")
    add("")
    return "\n".join(lines)


def run(
    gold_path: Path = GOLD_PATH, output_dir: Path = OUTPUT_DIR, write: bool = True
) -> Dict[str, Any]:
    """Score, and write ``results.json`` plus ``summary.txt``."""
    payload = load_gold(gold_path)
    problems = validate_gold(payload)
    if problems:
        raise SystemExit(
            "the gold annotations do not validate; refusing to report a "
            "number computed against a broken file:\n  "
            + "\n  ".join(problems)
        )
    result = score(payload)
    result["conditions"] = CONDITIONS
    result["annotator"] = payload["annotator"]
    result["sampling"] = payload["sampling"]
    result["guideline"] = payload["guideline"]
    result["corpus"] = payload["corpus"]

    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.json").write_text(
            json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        (output_dir / "summary.txt").write_text(
            _format(result, payload), encoding="utf-8"
        )
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.entity_extraction_eval",
        description=(
            "Score the rule-based entity extractor against the committed gold "
            "annotations. Reports CRF and transformer as NOT RUN, with the "
            "reason."
        ),
    )
    parser.add_argument("--gold", type=Path, default=GOLD_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Check the gold file and exit, computing no metrics.",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    payload = load_gold(args.gold)
    problems = validate_gold(payload)
    if args.validate:
        if problems:
            print(f"{len(problems)} problem(s) in {args.gold}:")
            for problem in problems:
                print(f"  {problem}")
            return 1
        gold = sum(
            1 for clause in payload["clauses"] for span in clause["spans"]
            if span["start"] >= 0
        )
        print(
            f"{args.gold}: OK — {len(payload['clauses'])} clauses, "
            f"{gold} gold mentions, every span resolves and none overlap."
        )
        return 0

    result = run(args.gold, args.output_dir, write=not args.no_write)
    print(_format(result, payload))
    if not args.no_write:
        print(f"written: {args.output_dir / 'results.json'}")
        print(f"written: {args.output_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
