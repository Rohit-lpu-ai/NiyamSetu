"""E5 — extraction fidelity: CER and WER against a hand transcription.

This is the only experiment that would give extraction a real accuracy number.
Everything else about extraction in this project counts *defect signals*
(broken words, U+FFFD, orphan combining marks) — useful, reproducible, and
self-referential: they compare the extractor against detectors we also wrote.

A transcription is the only external reference. It is also expensive, which is
why this experiment is last in the audit's order and why this module **prepares
the package and scores it, but never writes a reference transcription**.
Generating a "reference" from the extractor output would compare the extractor
to itself and score 0.0 error by construction (``CLAUDE.md`` §24 rule 12).

**Sample selection is deliberate, not random.** With 3-5 pages, a random draw
would most likely return three pages of ordinary prose and measure the easy
case. The pages offered are chosen to include the two known-hard conditions:

* a **table-heavy** page — where pypdf flattens cell structure;
* a **Devanagari-heavy** page — where glyphs are lost outright.

Those are where the error concentrates, so the resulting CER/WER is a
*worst-case-weighted* figure, not a corpus average. That has to be said
wherever the number is quoted.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "FidelityError",
    "PageSelection",
    "select_pages",
    "write_transcription_templates",
    "character_error_rate",
    "word_error_rate",
    "score_page",
    "run",
    "format_report",
    "main",
]

DEFAULT_PROCESSED_DIR = Path("data") / "processed"
DEFAULT_TRUTH_DIR = Path("data") / "annotations" / "extraction"
DEFAULT_OUTPUT_DIR = Path("evaluation") / "experiment-e5-fidelity"

TEMPLATE_SUFFIX = ".template.txt"
TRUTH_SUFFIX = ".truth.txt"


class FidelityError(RuntimeError):
    """A transcription is missing or malformed."""

    code = "FIDELITY_ERROR"


@dataclass
class PageSelection:
    """One page chosen for transcription, with why it was chosen."""

    version_id: str
    page_number: int
    reason: str
    devanagari_chars: int
    short_line_ratio: float
    char_count: int

    @property
    def stem(self) -> str:
        return f"{self.version_id}.page{self.page_number:03d}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "version_id": self.version_id,
            "page_number": self.page_number,
            "reason": self.reason,
            "devanagari_chars": self.devanagari_chars,
            "short_line_ratio": round(self.short_line_ratio, 3),
            "char_count": self.char_count,
        }


def _normalise_for_scoring(text: str) -> str:
    """Put both sides on the same footing before comparing.

    NFKC plus whitespace collapsing, applied **identically** to extraction and
    transcription. Without this, a human typing one space where the PDF has two
    would register as an error, and CER would measure typing conventions rather
    than extraction quality.

    Deliberately *not* done: case folding or punctuation stripping. A citation
    quote must match verbatim (``CLAUDE.md`` §12.2), so a case or punctuation
    error is a real error here.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _edit_distance(a: Sequence, b: Sequence) -> int:
    """Levenshtein distance, two-row dynamic programming.

    O(len(a) x len(b)) time, O(min) space. A page is a few thousand characters,
    so this runs in milliseconds and avoids a dependency.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,        # deletion
                    current[j - 1] + 1,     # insertion
                    previous[j - 1] + (item_a != item_b),  # substitution
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> Optional[float]:
    """``edit_distance(ref, hyp) / len(ref)``. ``None`` when the reference is empty.

    Can exceed 1.0 when the extractor emits more text than exists — which is
    exactly what a flattened table does, so the value is **not** clamped.
    """
    ref = _normalise_for_scoring(reference)
    hyp = _normalise_for_scoring(hypothesis)
    if not ref:
        return None
    return _edit_distance(ref, hyp) / len(ref)


def word_error_rate(reference: str, hypothesis: str) -> Optional[float]:
    """Word-level equivalent. Words are whitespace-separated after normalising."""
    ref = _normalise_for_scoring(reference).split()
    hyp = _normalise_for_scoring(hypothesis).split()
    if not ref:
        return None
    return _edit_distance(ref, hyp) / len(ref)


def _devanagari_count(text: str) -> int:
    return sum(1 for ch in text if "ऀ" <= ch <= "ॿ")


def _short_line_ratio(text: str, max_words: int = 4) -> float:
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return 0.0
    return sum(1 for line in lines if len(line.split()) <= max_words) / len(lines)


def select_pages(
    processed_dir: Path = DEFAULT_PROCESSED_DIR, limit: int = 5
) -> List[PageSelection]:
    """Choose the pages to transcribe, worst cases first.

    Ranking: the most Devanagari-heavy page, the most table-like page, then the
    largest prose pages as a baseline to contrast against. One page per
    category per document at most, so the sample is not three pages of the same
    document.
    """
    directory = Path(processed_dir)
    if not directory.exists():
        raise FidelityError(f"processed directory not found: {directory}")

    candidates: List[Tuple[int, float, PageSelection]] = []
    for version_dir in sorted(c for c in directory.iterdir() if c.is_dir()):
        extraction_path = version_dir / "extraction.json"
        if not extraction_path.exists():
            continue
        payload = json.loads(extraction_path.read_text(encoding="utf-8"))
        version_id = str(payload["document"]["version_id"])
        for page in payload["pages"]:
            text = str(page.get("normalized_text") or "")
            candidates.append((
                _devanagari_count(text),
                _short_line_ratio(text),
                PageSelection(
                    version_id=version_id,
                    page_number=int(page["page_number"]),
                    reason="",
                    devanagari_chars=_devanagari_count(text),
                    short_line_ratio=_short_line_ratio(text),
                    char_count=len(text),
                ),
            ))

    if not candidates:
        raise FidelityError("no extracted pages found")

    chosen: List[PageSelection] = []
    used = set()

    def take(selection: PageSelection, reason: str) -> None:
        key = (selection.version_id, selection.page_number)
        if key in used or len(chosen) >= limit:
            return
        used.add(key)
        selection.reason = reason
        chosen.append(selection)

    by_devanagari = sorted(candidates, key=lambda c: -c[0])
    if by_devanagari and by_devanagari[0][0] > 0:
        take(by_devanagari[0][2], "most Devanagari characters in the corpus")

    by_table = sorted(candidates, key=lambda c: -c[1])
    if by_table and by_table[0][1] > 0:
        take(by_table[0][2], "most table-like (highest short-line ratio)")
        if len(by_table) > 1:
            take(by_table[1][2], "second most table-like")

    by_size = sorted(candidates, key=lambda c: -c[2].char_count)
    for _, _, selection in by_size:
        take(selection, "large prose page, as a contrast to the hard cases")
        if len(chosen) >= limit:
            break
    return chosen


def write_transcription_templates(
    selections: Sequence[PageSelection],
    truth_dir: Path = DEFAULT_TRUTH_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
) -> List[Path]:
    """Write one instruction file per selected page.

    The template contains **instructions and metadata only — never the
    extracted text.** Showing the transcriber what the extractor produced would
    anchor them to it, and the resulting "reference" would agree with the
    extractor by suggestion rather than by observation. It is the same
    independence problem as the blind annotation worksheets.
    """
    truth_dir = Path(truth_dir)
    truth_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    for selection in selections:
        path = truth_dir / f"{selection.stem}{TEMPLATE_SUFFIX}"
        path.write_text(
            "\n".join([
                "NiyamSetu - extraction fidelity transcription (E5)",
                "=" * 68,
                f"version_id  : {selection.version_id}",
                f"page_number : {selection.page_number}  (physical PDF page)",
                f"chosen      : {selection.reason}",
                "",
                "INSTRUCTIONS",
                "------------",
                "1. Open the source PDF in data/raw/ and go to this PAGE NUMBER.",
                "2. Type what you SEE on the page, reading top to bottom, left",
                "   to right. Include headers, footers and page numbers.",
                "3. For a table, type it row by row, separating cells with a",
                "   single space - the extractor has no cell structure either,",
                "   so this keeps the comparison about characters, not layout.",
                "4. Type Devanagari text as printed. If a glyph is unreadable in",
                "   the PDF itself, write [?]. Do NOT guess.",
                "5. Do not correct the document's own typos. The reference is",
                "   what the page says, not what it should say.",
                "",
                "DELIBERATELY NOT SHOWN: what the extractor produced for this",
                "page. Seeing it would anchor your transcription to it, and the",
                "reference would then agree with the extractor by suggestion.",
                "",
                f"Save your transcription as: {selection.stem}{TRUTH_SUFFIX}",
                "in this same directory. Everything below this line is ignored;",
                "put the transcription in the .truth.txt file, not here.",
                "=" * 68,
                "",
            ]),
            encoding="utf-8",
        )
        written.append(path)
    return written


def score_page(
    selection: PageSelection,
    truth_dir: Path = DEFAULT_TRUTH_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
) -> Dict[str, object]:
    """Score one page if its transcription exists."""
    truth_path = Path(truth_dir) / f"{selection.stem}{TRUTH_SUFFIX}"
    result: Dict[str, object] = {
        **selection.to_dict(),
        "truth_file": str(truth_path),
    }
    if not truth_path.exists():
        result["status"] = "PENDING - no transcription"
        result["cer"] = None
        result["wer"] = None
        return result

    reference = truth_path.read_text(encoding="utf-8")
    if not reference.strip():
        raise FidelityError(f"{truth_path.name} is empty")

    extraction_path = (
        Path(processed_dir) / selection.version_id / "extraction.json"
    )
    payload = json.loads(extraction_path.read_text(encoding="utf-8"))
    page = next(
        (
            p for p in payload["pages"]
            if int(p["page_number"]) == selection.page_number
        ),
        None,
    )
    if page is None:
        raise FidelityError(
            f"{selection.version_id} has no page {selection.page_number}"
        )

    hypothesis = str(page.get("normalized_text") or "")
    result["status"] = "measured"
    result["cer"] = character_error_rate(reference, hypothesis)
    result["wer"] = word_error_rate(reference, hypothesis)
    result["reference_chars"] = len(_normalise_for_scoring(reference))
    result["hypothesis_chars"] = len(_normalise_for_scoring(hypothesis))
    return result


def run(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    truth_dir: Path = DEFAULT_TRUTH_DIR,
    limit: int = 5,
) -> Dict[str, object]:
    selections = select_pages(processed_dir, limit)
    pages = [score_page(s, truth_dir, processed_dir) for s in selections]
    measured = [p for p in pages if p["status"] == "measured"]

    pooled: Dict[str, object]
    if not measured:
        pooled = {
            "status": "PENDING - no transcriptions",
            "pages_measured": 0,
            "pages_pending": len(pages),
            "cer": None,
            "wer": None,
            "note": (
                "No page has been transcribed. CER and WER cannot be computed, "
                "and no estimate may be substituted."
            ),
        }
    else:
        # Weighted by reference length: a 3,000-character page should not count
        # the same as a 300-character one.
        total_ref = sum(int(p["reference_chars"]) for p in measured)
        pooled = {
            "status": "measured",
            "pages_measured": len(measured),
            "pages_pending": len(pages) - len(measured),
            "cer": (
                sum(float(p["cer"]) * int(p["reference_chars"]) for p in measured)
                / total_ref
            ) if total_ref else None,
            "wer": (
                sum(float(p["wer"]) * int(p["reference_chars"]) for p in measured)
                / total_ref
            ) if total_ref else None,
            "note": (
                "Worst-case weighted: pages were chosen to include the "
                "table-heavy and Devanagari-heavy cases, NOT drawn at random. "
                "This is not a corpus average."
            ),
        }

    return {
        "experiment": "E5 - extraction fidelity (CER/WER)",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "metric": (
            "CER = levenshtein(reference, extracted) / len(reference), on "
            "NFKC-normalised whitespace-collapsed text. WER is the same over "
            "whitespace-separated words. Not clamped at 1.0: a flattened table "
            "can emit more text than the page contains."
        ),
        "pooled": pooled,
        "pages": pages,
    }


def format_report(payload: Dict[str, object]) -> str:
    pooled = payload["pooled"]  # type: ignore[index]
    lines = [
        "NiyamSetu extraction fidelity (E5)",
        f"generated : {payload['generated_at']}",
        "",
    ]
    if pooled["status"] != "measured":  # type: ignore[index]
        lines += [
            "STATUS    : PENDING - no transcriptions",
            f"  {pooled['note']}",  # type: ignore[index]
            "",
            "Pages prepared for transcription:",
        ]
    else:
        lines += [
            f"POOLED CER : {pooled['cer']:.4f}",  # type: ignore[index]
            f"POOLED WER : {pooled['wer']:.4f}",  # type: ignore[index]
            f"  {pooled['note']}",  # type: ignore[index]
            "",
        ]
    for page in payload["pages"]:  # type: ignore[union-attr]
        cer = page["cer"]
        suffix = (
            f"CER {cer:.4f} WER {page['wer']:.4f}"
            if isinstance(cer, float)
            else page["status"]
        )
        lines.append(
            f"  {page['version_id'][:20]} p{page['page_number']:<3} "
            f"{page['reason'][:44]:46} {suffix}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.extraction_fidelity",
        description=(
            "E5: CER/WER against hand transcriptions. Prepares templates; "
            "scores only real transcriptions, never generated ones."
        ),
    )
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--truth-dir", type=Path, default=DEFAULT_TRUTH_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--write-templates", action="store_true",
        help="(Re)write the transcription instruction files.",
    )
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.write_templates:
            selections = select_pages(args.processed_dir, args.limit)
            written = write_transcription_templates(
                selections, args.truth_dir, args.processed_dir
            )
            print(f"wrote {len(written)} transcription template(s):")
            for selection, path in zip(selections, written):
                print(f"  {path.name}  <- {selection.reason}")
            return 0

        payload = run(args.processed_dir, args.truth_dir, args.limit)
    except FidelityError as exc:
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
