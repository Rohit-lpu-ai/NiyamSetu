"""Detectors for the extraction defects found by hand in the Phase 1 report.

``evaluation/2026-10-28-phase1-extraction-validation.md`` §4 catalogued six
classes of extraction defect by reading output against the original PDFs. Each
one is turned into a counter here, for two reasons:

1. **Experiment A needs them.** "PyMuPDF is better" is not a claim; "PyMuPDF
   produces 0 replacement characters where pypdf produces 44" is. Comparing
   extractors means counting the same defects in each one's output.
2. **They are regression detectors.** Once a count is recorded for a document,
   a later pipeline change that makes extraction worse shows up as a number
   going the wrong way instead of being noticed by chance.

What these are, and are not
---------------------------
These are **heuristics measured against no ground truth.** A count here is
evidence, not a verdict. :func:`count_broken_words`, for instance, flags
``'M inimum'`` by pattern, and it will also flag a legitimate ``'a bc'``. Every
detector therefore returns the offending snippets alongside the count, so a
human can read what was actually matched — and the report quotes them.

None of these functions is allowed to *change* text. They only count.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Sequence

__all__ = [
    "PageArtifacts",
    "count_broken_words",
    "count_hyphen_space_artifacts",
    "count_mismapped_glyph_candidates",
    "count_padded_quotes",
    "count_replacement_characters",
    "devanagari_char_count",
    "find_orphan_combining_marks",
    "is_probably_flattened_table",
    "measure_page",
    "short_line_ratio",
]

#: U+FFFD, inserted by an extractor when a glyph cannot be mapped to a
#: character. Each one is a character of the document that has been lost
#: outright, which makes this the single most severe artefact we count.
REPLACEMENT_CHARACTER = "�"

#: A single capital letter, then a space, then lowercase letters: the signature
#: of ``'M inimum'`` / ``'C astes'``, where the extractor emitted a spurious
#: space after the first glyph. Requiring a word boundary before the capital
#: avoids matching the tail of a longer word, and requiring two or more
#: lowercase letters after the space avoids matching real text like "A bit".
#:
#: Known false positives: a genuine single-letter word followed by a word, as in
#: "I agree" or the article "A notification". Those are counted too, which is
#: why the snippets are returned for inspection.
_BROKEN_WORD_RE = re.compile(r"\b([A-Za-z]) ([a-z]{2,})")

#: A lowercase letter, a hyphen, then a space, then a letter: ``'clean- up'``.
#: The mirror case — space *before* the hyphen, ``'risk -based'`` — is the
#: second alternative.
_HYPHEN_SPACE_RE = re.compile(r"([A-Za-z])- ([A-Za-z])|([A-Za-z]) -([A-Za-z])")

#: A typographic quote with a space on its inner side: ``'‘ the Directions ’'``.
#: Straight quotes are excluded deliberately — they appear legitimately in
#: measurements (``6' 2"``) and in code-like strings, and RBI circulars use the
#: curly forms.
_PADDED_QUOTE_RE = re.compile("[‘“] |[ ][’”]")

#: Devanagari block, plus the Devanagari Extended block used for some marks.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ꣠-ꣿ]")

#: A combining mark (Unicode category Mn/Mc) that starts a whitespace-delimited
#: token, i.e. a vowel sign that has become detached from its consonant — the
#: ``'ीय'`` case in the Phase 1 report §4.1.
_LEADING_COMBINING_RE = re.compile(r"(?:^|\s)([ऀ-ॿ꣠-ꣿ]+)")

#: Default: a line of this many words or fewer counts as "short". Two words is
#: what the Phase 1 report used when it found page 6 of the commercial-banks
#: amendment extracting 49 of 64 lines at <= 2 words, so the threshold is kept
#: identical for comparability.
DEFAULT_SHORT_LINE_WORDS = 2

#: Default: a page where more than this fraction of its non-blank lines are
#: short is flagged as a probably-flattened table. 0.5 is the Phase 1 value
#: ("more than half the lines are two words or fewer").
DEFAULT_FLATTENED_TABLE_RATIO = 0.5

#: A page needs at least this many non-blank lines before the ratio means
#: anything. Without it, a two-line cover page trivially "fails".
_MIN_LINES_FOR_RATIO = 8


def count_replacement_characters(text: str) -> int:
    """Count U+FFFD replacement characters.

    Exact, not heuristic: every occurrence is a glyph the extractor could not
    map, so the character is simply gone.
    """
    return text.count(REPLACEMENT_CHARACTER)


def devanagari_char_count(text: str) -> int:
    """Count Devanagari characters, as a denominator for the mangling rate.

    Needed because "0 replacement characters" means something quite different
    on a page with 200 Devanagari characters than on a page with none.
    """
    return len(_DEVANAGARI_RE.findall(text))


def find_orphan_combining_marks(text: str, limit: int = 10) -> List[str]:
    """Return tokens that *begin* with a Devanagari combining mark.

    A dependent vowel sign cannot legally start a word, so a token that begins
    with one is proof that the extractor emitted the mark separately from its
    base consonant. This is the detector the Phase 1 report §4.1 flagged as too
    narrow; it is kept narrow on purpose (a false positive here would be a
    serious claim) and the report says so.
    """
    found: List[str] = []
    for match in _LEADING_COMBINING_RE.finditer(text):
        token = match.group(1)
        if not token:
            continue
        if unicodedata.category(token[0]) in {"Mn", "Mc"}:
            found.append(token)
            if len(found) >= limit:
                break
    return found


#: Unicode blocks that should not appear in an English/Devanagari government
#: circular, and which an extractor emits when it resolves a glyph through the
#: wrong encoding table. Discovered empirically in Experiment A: where pypdf
#: emits U+FFFD for an unmappable Devanagari glyph, PyMuPDF emits a character
#: from one of these blocks instead - ``į``, ``ȣ``, ``Ǒ``, ``ͪ`` - which *looks*
#: like text and therefore passes silently through every downstream stage.
#:
#: The blocks, with why each is suspect here:
#:   Latin Extended-A/B (U+0100-U+024F)  - ``į`` for a Devanagari vowel sign
#:   IPA Extensions     (U+0250-U+02AF)  - phonetic letters, never in a circular
#:   Combining marks    (U+0300-U+036F)  - Latin combining marks, not Devanagari
#:   Greek              (U+0370-U+03FF)  - but see the false-positive note below
#:   Cyrillic           (U+0400-U+04FF)  - never legitimately present
_SUSPECT_SCRIPT_RANGES = (
    (0x0100, 0x024F),
    (0x0250, 0x02AF),
    (0x0300, 0x036F),
    (0x0370, 0x03FF),
    (0x0400, 0x04FF),
)


def count_mismapped_glyph_candidates(
    text: str, limit: int = 10
) -> tuple[int, List[str]]:
    """Count characters from scripts that should not occur in this corpus.

    This is the counterpart to :func:`count_replacement_characters`, and the two
    must be read **together**. An extractor that cannot resolve a glyph has two
    options: emit U+FFFD, which is honest and countable, or emit its best guess
    from whatever encoding table it fell back to, which is silent. Counting only
    U+FFFD therefore rewards the extractor that corrupts text quietly, which is
    the opposite of what this project wants.

    Known false positive, and it is a real one: ``Σ`` (U+03A3) appears
    legitimately in the capital-adequacy document, where it is a summation sign
    in a formula. Both pypdf and PyMuPDF emit it, and it is correct in both. So
    a non-zero count here is a prompt to look at the samples, never a defect
    count on its own - which is why the matched characters are returned.

    Returns:
        ``(count, sample_characters)`` - the samples are distinct characters,
        not occurrences, because a reader wants to know *which* wrong glyph
        appeared rather than seeing ``į`` thirty-two times.
    """
    matched: List[str] = []
    total = 0
    seen: set[str] = set()
    for char in text:
        codepoint = ord(char)
        if any(low <= codepoint <= high for low, high in _SUSPECT_SCRIPT_RANGES):
            total += 1
            if char not in seen and len(matched) < limit:
                seen.add(char)
                matched.append(char)
    return total, matched


def count_broken_words(text: str, limit: int = 10) -> tuple[int, List[str]]:
    """Count spurious spaces inside words; return ``(count, sample_snippets)``.

    See ``_BROKEN_WORD_RE`` for the known false positives. The snippets exist so
    the report can quote what matched rather than asserting a bare number.
    """
    matches = _BROKEN_WORD_RE.findall(text)
    snippets = [f"{first} {rest}" for first, rest in matches[:limit]]
    return len(matches), snippets


def count_hyphen_space_artifacts(text: str, limit: int = 10) -> tuple[int, List[str]]:
    """Count hyphens with a space on the wrong side: ``'clean- up'``."""
    snippets: List[str] = []
    total = 0
    for match in _HYPHEN_SPACE_RE.finditer(text):
        total += 1
        if len(snippets) < limit:
            snippets.append(match.group(0))
    return total, snippets


def count_padded_quotes(text: str, limit: int = 10) -> tuple[int, List[str]]:
    """Count typographic quotes padded with a space on the inside."""
    snippets: List[str] = []
    total = 0
    for match in _PADDED_QUOTE_RE.finditer(text):
        total += 1
        if len(snippets) < limit:
            start = max(0, match.start() - 12)
            snippets.append(text[start : match.end() + 12].replace("\n", " "))
    return total, snippets


def short_line_ratio(
    text: str, short_line_words: int = DEFAULT_SHORT_LINE_WORDS
) -> tuple[float, int, int]:
    """Return ``(ratio, short_line_count, non_blank_line_count)``.

    The ratio is the Phase 1 report's table-flattening proxy: when a multi-column
    table is read column-by-column, the output becomes a vertical stack of
    one- and two-word lines.
    """
    lines = [line.strip() for line in text.splitlines()]
    non_blank = [line for line in lines if line]
    if not non_blank:
        return 0.0, 0, 0
    short = sum(1 for line in non_blank if len(line.split()) <= short_line_words)
    return short / len(non_blank), short, len(non_blank)


def is_probably_flattened_table(
    text: str,
    short_line_words: int = DEFAULT_SHORT_LINE_WORDS,
    ratio_threshold: float = DEFAULT_FLATTENED_TABLE_RATIO,
    min_lines: int = _MIN_LINES_FOR_RATIO,
) -> bool:
    """Whether a page looks like a table read column-by-column.

    A proxy, and a noisy one: a page that is genuinely a short list of
    single-word items looks the same. It is useful because it is *consistent*
    across extractors, so the comparison is fair even where the absolute
    judgement is wrong.
    """
    ratio, _, non_blank = short_line_ratio(text, short_line_words)
    if non_blank < min_lines:
        return False
    return ratio > ratio_threshold


@dataclass
class PageArtifacts:
    """Every artefact count for one page of one extractor's output.

    One flat record per page keeps the output JSON easy to pivot: by extractor,
    by document or by page, without reshaping.
    """

    page_number: int
    char_count: int
    word_count: int
    line_count: int
    is_empty: bool
    replacement_chars: int
    #: Characters from scripts that do not belong in this corpus. Read together
    #: with ``replacement_chars``: an extractor can lose a glyph loudly (U+FFFD)
    #: or quietly (a plausible-looking wrong character), and only the sum of the
    #: two describes how much text was actually lost.
    mismapped_glyph_candidates: int
    #: ``replacement_chars + mismapped_glyph_candidates``. The honest headline
    #: number for glyph loss, and the one the Experiment A report leads with.
    glyph_loss_signal: int
    devanagari_chars: int
    orphan_combining_marks: int
    broken_words: int
    hyphen_space_artifacts: int
    padded_quotes: int
    short_line_ratio: float
    short_lines: int
    non_blank_lines: int
    probably_flattened_table: bool
    #: Verbatim snippets, so a reader can check what the counters matched.
    samples: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def measure_page(page_number: int, text: str, sample_limit: int = 5) -> PageArtifacts:
    """Compute every artefact count for one page's extracted text."""
    broken_count, broken_samples = count_broken_words(text, limit=sample_limit)
    hyphen_count, hyphen_samples = count_hyphen_space_artifacts(
        text, limit=sample_limit
    )
    quote_count, quote_samples = count_padded_quotes(text, limit=sample_limit)
    orphans = find_orphan_combining_marks(text, limit=sample_limit)
    mismapped_count, mismapped_samples = count_mismapped_glyph_candidates(
        text, limit=sample_limit
    )
    replacements = count_replacement_characters(text)
    ratio, short_lines, non_blank = short_line_ratio(text)

    samples = {
        key: value
        for key, value in (
            ("broken_words", broken_samples),
            ("hyphen_space", hyphen_samples),
            ("padded_quotes", quote_samples),
            ("orphan_combining_marks", orphans),
            ("mismapped_glyphs", mismapped_samples),
        )
        if value
    }

    return PageArtifacts(
        page_number=page_number,
        char_count=len(text),
        word_count=len(text.split()),
        line_count=len(text.splitlines()),
        is_empty=not text.strip(),
        replacement_chars=replacements,
        mismapped_glyph_candidates=mismapped_count,
        glyph_loss_signal=replacements + mismapped_count,
        devanagari_chars=devanagari_char_count(text),
        orphan_combining_marks=len(orphans),
        broken_words=broken_count,
        hyphen_space_artifacts=hyphen_count,
        padded_quotes=quote_count,
        short_line_ratio=round(ratio, 4),
        short_lines=short_lines,
        non_blank_lines=non_blank,
        probably_flattened_table=is_probably_flattened_table(text),
        samples=samples,
    )


def summarise_pages(pages: Sequence[PageArtifacts]) -> Dict[str, object]:
    """Aggregate per-page artefact records into document-level totals."""
    if not pages:
        return {
            "page_count": 0,
            "total_chars": 0,
            "empty_pages": 0,
            "replacement_chars": 0,
            "mismapped_glyph_candidates": 0,
            "glyph_loss_signal": 0,
            "devanagari_chars": 0,
            "orphan_combining_marks": 0,
            "broken_words": 0,
            "hyphen_space_artifacts": 0,
            "padded_quotes": 0,
            "flattened_table_pages": [],
        }
    return {
        "page_count": len(pages),
        "total_chars": sum(p.char_count for p in pages),
        "total_words": sum(p.word_count for p in pages),
        "empty_pages": sum(1 for p in pages if p.is_empty),
        "empty_page_numbers": [p.page_number for p in pages if p.is_empty],
        "replacement_chars": sum(p.replacement_chars for p in pages),
        "mismapped_glyph_candidates": sum(
            p.mismapped_glyph_candidates for p in pages
        ),
        "glyph_loss_signal": sum(p.glyph_loss_signal for p in pages),
        "devanagari_chars": sum(p.devanagari_chars for p in pages),
        "orphan_combining_marks": sum(p.orphan_combining_marks for p in pages),
        "broken_words": sum(p.broken_words for p in pages),
        "hyphen_space_artifacts": sum(p.hyphen_space_artifacts for p in pages),
        "padded_quotes": sum(p.padded_quotes for p in pages),
        "flattened_table_pages": [
            p.page_number for p in pages if p.probably_flattened_table
        ],
    }
