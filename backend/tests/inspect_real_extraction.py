"""Manual inspection helper for the real-document validation run.

This is **not** a pytest module - it asserts nothing and computes no accuracy
score. It surfaces the specific artefacts that are worth a human looking at, so
that the findings written into ``evaluation/`` come from inspection rather than
from the script having exited zero.

Run it after a corpus extraction::

    python -m backend.tests.inspect_real_extraction
    python -m backend.tests.inspect_real_extraction --dir data/processed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

#: A Devanagari codepoint whose combining marks have been reordered by
#: extraction shows up as a dependent vowel sign or virama with nothing to
#: attach to - i.e. at the very start of a run. Crude, but it reliably flags the
#: bilingual-letterhead damage in RBI circulars.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

#: A single letter separated from the rest of its word by a space:
#: "M inimum", "f inancial". A classic pypdf artefact around kerned text.
#:
#: Two exclusions, both found by inspecting this detector's own false positives:
#: ``a``/``A``/``I`` are real one-letter English words ("a bank"), and a letter
#: preceded by an apostrophe is a possessive, not a split word ("bank's own").
_INTRA_WORD_SPACE_RE = re.compile(
    r"(?<![’'])\b(?![aAI]\b)([A-Za-z])\s([a-z]{3,})\b"
)

#: A space on the wrong side of a hyphen: "regular -way", "clean- up",
#: "multi- level". Harmless to read, but it breaks exact-string matching, which
#: is what the citation check in Person 3's Ask feature depends on.
_HYPHEN_SPACING_RE = re.compile(r"[a-z]\s-[a-z]|[a-z]-\s[a-z]")

#: A page whose lines are overwhelmingly one or two words long is a table that
#: has been flattened column-by-column rather than row-by-row.
_SHORT_LINE_WORD_LIMIT = 2
_FLATTENED_TABLE_FRACTION = 0.5

#: A space immediately inside a quotation mark or bracket: "' the Directions '".
_PADDED_QUOTE_RE = re.compile(r"[‘“]\s|\s[’”]")


def load_results(directory: Path) -> List[Tuple[Path, dict]]:
    results = []
    for path in sorted(directory.rglob("extraction.json")):
        results.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return results


def repeated_lines(pages: Iterable[dict], min_fraction: float = 0.6) -> List[Tuple[str, int]]:
    """Lines appearing on at least ``min_fraction`` of pages: header/footer candidates.

    This is the measurement that Phase 2's header/footer removal will be built
    on (``CLAUDE.md`` §13.1 proposes the same 60% threshold). Reporting it here
    tells us whether that threshold is right for our real documents.
    """
    pages = list(pages)
    counts: Dict[str, int] = {}
    for page in pages:
        seen = set()
        for line in page["normalized_text"].splitlines():
            stripped = line.strip()
            if len(stripped) < 4:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            counts[stripped] = counts.get(stripped, 0) + 1
    threshold = max(2, int(len(pages) * min_fraction))
    return sorted(
        ((line, n) for line, n in counts.items() if n >= threshold),
        key=lambda item: -item[1],
    )


def devanagari_damage(text: str) -> List[str]:
    """Return Devanagari fragments whose combining marks are clearly misplaced.

    A dependent vowel sign (Mn/Mc category) or virama at the start of a token
    has nothing to combine with, which means extraction emitted the glyphs in
    visual rather than logical order.
    """
    damaged = []
    for token in text.split():
        if not _DEVANAGARI_RE.search(token):
            continue
        first = token[0]
        if unicodedata.category(first) in {"Mn", "Mc"}:
            damaged.append(token)
        elif "्" in token and token.index("्") == 0:
            damaged.append(token)
    return damaged


def describe(path: Path, payload: dict) -> str:
    document = payload["document"]
    pages = payload["pages"]
    lines: List[str] = []
    add = lines.append

    add("=" * 78)
    add(f"{document['filename']}")
    add(f"  output            : {path}")
    add(f"  source_url        : {document['source_metadata']['source_url']}")
    add(f"  reference_no      : {document['source_metadata']['reference_no']}")
    add(f"  publication_date  : {document['source_metadata']['publication_date']}")
    add(f"  department        : {document['source_metadata']['issuing_department']}")
    add(f"  extraction_status : {document['extraction_status']}")
    add(f"  pages             : {document['page_count']}")
    add(f"  pages with text   : {sum(1 for p in pages if p['has_meaningful_text'])}")
    add(f"  empty pages       : {sum(1 for p in pages if p['is_empty'])}")
    poor = [p["page_number"] for p in pages if not p["has_meaningful_text"] and not p["is_empty"]]
    add(f"  text-poor pages   : {poor or 'none'}")
    add(f"  warnings          : {document['warnings'] or 'none'}")

    counts = [p["char_count"] for p in pages]
    add(f"  chars per page    : min {min(counts)}, median "
        f"{sorted(counts)[len(counts) // 2]}, max {max(counts)}")

    add("")
    add("  -- repeating lines (header/footer candidates, >=60% of pages) --")
    repeats = repeated_lines(pages)
    if not repeats:
        add("     none")
    for line, count in repeats[:6]:
        add(f"     {count:>3}/{len(pages)}  {line[:90]}")

    add("")
    add("  -- Devanagari combining-mark damage --")
    all_damage: List[Tuple[int, str]] = []
    for page in pages:
        for token in devanagari_damage(page["normalized_text"])[:3]:
            all_damage.append((page["page_number"], token))
    if not all_damage:
        add("     none detected")
    for page_number, token in all_damage[:6]:
        add(f"     page {page_number}: {token!r}")

    add("")
    add("  -- intra-word spacing artefacts --")
    found = 0
    for page in pages:
        for match in _INTRA_WORD_SPACE_RE.finditer(page["normalized_text"]):
            if found >= 6:
                break
            add(f"     page {page['page_number']}: {match.group(0)!r}")
            found += 1
        if found >= 6:
            break
    if not found:
        add("     none detected")

    add("")
    add("  -- hyphen spacing artefacts --")
    found = 0
    for page in pages:
        for match in _HYPHEN_SPACING_RE.finditer(page["normalized_text"]):
            if found >= 5:
                break
            start = max(0, match.start() - 12)
            add(f"     page {page['page_number']}: {page['normalized_text'][start:match.end() + 12]!r}")
            found += 1
        if found >= 5:
            break
    if not found:
        add("     none detected")

    add("")
    add("  -- pages that look like flattened tables --")
    flattened = []
    for page in pages:
        body = [ln.strip() for ln in page["normalized_text"].splitlines() if ln.strip()]
        if len(body) < 8:
            continue
        short = sum(1 for ln in body if len(ln.split()) <= _SHORT_LINE_WORD_LIMIT)
        fraction = short / len(body)
        if fraction >= _FLATTENED_TABLE_FRACTION:
            flattened.append((page["page_number"], short, len(body)))
    if not flattened:
        add("     none detected")
    for page_number, short, total in flattened[:8]:
        add(f"     page {page_number}: {short}/{total} lines are <= "
            f"{_SHORT_LINE_WORD_LIMIT} words - row structure lost")

    add("")
    add("  -- padded quotation marks --")
    found = 0
    for page in pages:
        for match in _PADDED_QUOTE_RE.finditer(page["normalized_text"]):
            if found >= 4:
                break
            start = max(0, match.start() - 30)
            add(f"     page {page['page_number']}: ...{page['normalized_text'][start:match.end() + 30]!r}")
            found += 1
        if found >= 4:
            break
    if not found:
        add("     none detected")

    add("")
    add("  -- sample: first 2 meaningful lines of 3 pages --")
    sample_pages = [pages[0]]
    if len(pages) > 2:
        sample_pages.append(pages[len(pages) // 2])
    if len(pages) > 1:
        sample_pages.append(pages[-1])
    for page in sample_pages:
        body = [ln.strip() for ln in page["normalized_text"].splitlines() if len(ln.strip()) > 25]
        add(f"     page {page['page_number']}:")
        for line in body[:2]:
            add(f"        {line[:110]}")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args(argv)

    results = load_results(args.dir)
    if not results:
        print(f"No extraction.json found under {args.dir}", file=sys.stderr)
        return 1
    for path, payload in results:
        print(describe(path, payload))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
