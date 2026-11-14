"""Rule-based entity extraction over clause text. The regex baseline.

Deliberately separate from :mod:`backend.app.comparison.alignment`, as the
brief requires: alignment must not depend on extraction, so the two can evolve
independently and a change to a date pattern cannot alter which clauses are
reported as modified. Alignment never imports this module. The *explanation*
layer does, and the API exposes it on its own endpoint.

What this is, and what it is not
--------------------------------
This is the **regex condition** of the entity-extraction benchmark: the
baseline every later condition (CRF, a pretrained or fine-tuned transformer)
has to beat. It is grown from the teammate's prototype ``extract_facts``,
which recognised four groups -- dates, time periods, amounts/percentages and
obligation language -- and returned bare strings.

Two changes were needed before it could serve as a benchmark condition or feed
the evidence system:

1. **Character spans.** The prototype returned ``["30 days", "60 days"]`` with
   no positions. An entity with no offset cannot be highlighted on the source
   page, cannot be scored against a span-level annotation, and cannot be
   checked by a reader. Every match here carries ``start`` and ``end`` offsets
   into the exact string it was given.
2. **Separate labels instead of merged groups.** The prototype put amounts and
   percentages in one bucket ("Amounts / percentages"), which makes per-entity
   precision and recall unmeasurable for either. They are distinct labels.

Honest limits, stated here because they belong with the code
------------------------------------------------------------
* ``AUTHORITY`` matches a closed list of organisation names observed in this
  corpus plus a few generic patterns. It will not find an authority it has
  never seen. That is the defining weakness of a rule-based NER and the
  reason the benchmark exists.
* ``OBLIGATION`` matches *modal language* ("shall", "must", "is required
  to"), not obligations. "Banks shall not be required to report" matches, and
  is a release from an obligation rather than one. Treated as a lexical cue,
  and labelled that way in the output.
* Nothing here resolves a relative date ("within 30 days of the circular")
  to a calendar date. A deadline is reported as the period it is written as.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "ENTITY_LABELS",
    "Entity",
    "extract_entities",
    "extract_by_label",
    "entity_texts",
    "benchmark_regex",
]

#: Every label this extractor can emit, with what it means. The API publishes
#: this mapping, so a consumer never has to guess what a label covers.
ENTITY_LABELS: Dict[str, str] = {
    "DATE": "A calendar date as printed, in any of the formats Indian "
            "government circulars use.",
    "PERIOD": "A duration or time period, e.g. '30 days', 'three months'.",
    "MONEY": "A rupee or lakh/crore amount.",
    "PERCENT": "A percentage, including 'per cent' spelled out.",
    "OBLIGATION": "Modal/obligation language ('shall', 'must', 'is required "
                  "to'). A lexical cue, not a verified obligation.",
    "AUTHORITY": "A named issuing or supervising body.",
    "REFERENCE_NO": "An official circular or notification reference number.",
    "SECTION_REF": "A cross-reference to a section, clause, paragraph or "
                   "annexure.",
}

_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|"
    r"Nov|Dec)"
)

#: Ordered, and the order matters: the first pattern to claim a character
#: range wins, so the more specific pattern must come first. DATE before
#: PERIOD means "April 1, 2026" is a date and not a stray number; MONEY
#: before PERCENT is irrelevant (they cannot overlap) but REFERENCE_NO before
#: DATE matters, because a reference number contains a year.
_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (
        "REFERENCE_NO",
        # RBI/2017-18/7, FIDD.CO.GSSD.BC.No.06/09.09.001/2017-18,
        # IRDAI/F&A/CIR/MISC/171/10/2026
        # The segment class includes '-' so a financial year inside the
        # number ('.../2016-17') is consumed as part of the reference rather
        # than truncated at '2016' -- which also stops the trailing '-17'
        # being re-read as something else.
        r"\b(?:[A-Z]{2,}[A-Za-z&]*(?:[./][A-Za-z0-9&()\-]+){2,}"
        r"|RBI/\d{4}-\d{2,4}/\d+)",
    ),
    (
        "DATE",
        r"\b(?:"
        rf"\d{{1,2}}\s+{_MONTHS}\.?,?\s+\d{{4}}"
        rf"|{_MONTHS}\.?\s+\d{{1,2}},?\s+\d{{4}}"
        rf"|{_MONTHS}\.?\s+\d{{4}}"
        r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r")\b",
    ),
    (
        "PERCENT",
        r"(?:\d+(?:\.\d+)?\s*(?:%|per\s*cent(?:um)?|percent(?:age)?\s*points?))",
    ),
    (
        "MONEY",
        r"(?:(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)?(?:\s*(?:lakh|lakhs|crore|"
        r"crores|million|billion))?"
        r"|\b[\d,]+(?:\.\d+)?\s*(?:lakh|lakhs|crore|crores)\b)",
    ),
    (
        "PERIOD",
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
        r"twelve|fifteen|thirty|sixty|ninety)\s*"
        r"(?:working\s+|calendar\s+|financial\s+)?"
        r"(?:day|days|week|weeks|fortnight|fortnights|month|months|quarter|"
        r"quarters|year|years)\b",
    ),
    (
        "OBLIGATION",
        r"\b(?:shall\s+not|shall|must\s+not|must|may\s+not|is\s+required\s+to|"
        r"are\s+required\s+to|will\s+have\s+to|is\s+obliged\s+to|"
        r"are\s+obliged\s+to|should\s+not|should|ought\s+to|"
        r"is\s+not\s+permitted|are\s+not\s+permitted|is\s+prohibited|"
        r"are\s+prohibited)\b",
    ),
    (
        "AUTHORITY",
        r"(?:Reserve\s+Bank\s+of\s+India|Reserve\s+Bank\b|\bRBI\b"
        r"|Insurance\s+Regulatory\s+and\s+Development\s+Authority"
        r"(?:\s+of\s+India)?|\bIRDAI?\b"
        r"|National\s+Bank\s+for\s+Agriculture\s+and\s+Rural\s+Development"
        r"|\bNABARD\b|\bSEBI\b"
        r"|Securities\s+and\s+Exchange\s+Board\s+of\s+India"
        r"|Ministry\s+of\s+[A-Z][A-Za-z]*(?:\s+(?:of|and|&)?\s*[A-Z][A-Za-z]*)*"
        r"|Department\s+of\s+[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*"
        r"|Government\s+of\s+India"
        r"|(?:State|Central)\s+Government"
        r"|All\s+Scheduled\s+Commercial\s+Banks"
        r"|Regional\s+Rural\s+Banks?|\bRRBs?\b"
        r"|Small\s+Finance\s+Banks?|Payments\s+Banks?"
        # Plural: the circulars write "State Level Bankers Committees (SLBCs)"
        # more often than the singular, and a span ending one character short
        # of the gold span is an exact-match miss. Found by
        # evaluation/entity_extraction_eval.py.
        r"|State\s+Level\s+Bankers'?\s+Committees?|\bSLBCs?\b"
        # "Lead Bank" but not "Lead Bank Scheme": the scheme is the thing
        # being administered, not the body administering it (see
        # data/annotations/entities/README.md §1), and without this lookahead
        # the pattern fired on ten occurrences of the scheme's name across the
        # gold sample -- the single largest source of false positives.
        # The lookahead accepts either case because the circulars print both
        # "Lead Bank Scheme" and "Lead Bank scheme-"; the AUTHORITY pattern as
        # a whole is case-sensitive, so the alternative has to be spelled out.
        r"|Lead\s+Banks?(?!\s+[Ss]cheme)"
        r"|Chairman\s*/\s*Managing\s+Director)",
    ),
    (
        "SECTION_REF",
        r"\b(?:[Ss]ection|[Ss]ub-?section|[Cc]lause|[Pp]ara(?:graph)?|"
        r"[Aa]nnex(?:ure|)|[Aa]ppendix|[Ss]chedule|[Cc]hapter|[Pp]art)\s+"
        r"(?:[0-9]+(?:\.[0-9]+)*|[IVXLCDM]+|[A-Za-z])\b",
    ),
)

_COMPILED: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE if label == "PERIOD" else 0))
    for label, pattern in _PATTERNS
)


@dataclass(frozen=True)
class Entity:
    """One extracted mention, with the span it occupies.

    ``start``/``end`` are offsets into the **string that was passed to the
    extractor**. The API documents that this is the clause's ``text`` field,
    which is itself a slice of the page's ``normalized_text`` -- so a consumer
    can map a mention all the way back to a page without this module knowing
    about pages at all.
    """

    label: str
    text: str
    start: int
    end: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "text": self.text,
            "start": self.start,
            "end": self.end,
        }


def _overlaps(start: int, end: int, taken: Sequence[Tuple[int, int]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in taken)


def extract_entities(
    text: str, *, labels: Optional[Iterable[str]] = None
) -> List[Entity]:
    """Every entity mention in ``text``, in document order, non-overlapping.

    Overlaps are resolved by **pattern precedence, not by match length**: the
    patterns are tried in the declared order and a later pattern cannot claim
    a character an earlier one already took. That is what keeps the ``05/09``
    inside ``FIDD.GSSD.BC.No.05/09.10.01/2017-18`` from also being reported as
    a DATE, and it is a rule a reader can apply by hand to check the output.

    ``labels`` filters the **result**, not the cascade. Every pattern still
    runs, so precedence is identical whether or not a filter was asked for.
    Skipping the un-wanted patterns instead would change the answer: asking
    for DATE alone would let the date pattern claim the ``05/09.10`` that
    REFERENCE_NO holds in the unfiltered run, and the caller would be told a
    circular's file number is a date. That was a real defect, found on the
    2017 Minority Communities circular's letterhead.

    Unknown labels in ``labels`` raise, rather than being ignored: silently
    accepting a typo would return an empty list that looks like "this clause
    has no dates".
    """
    if not text:
        return []
    wanted = None
    if labels is not None:
        wanted = set(labels)
        unknown = wanted - set(ENTITY_LABELS)
        if unknown:
            raise ValueError(
                f"unknown entity label(s): {sorted(unknown)}. "
                f"Available: {sorted(ENTITY_LABELS)}"
            )

    found: List[Entity] = []
    taken: List[Tuple[int, int]] = []
    for label, pattern in _COMPILED:
        for match in pattern.finditer(text):
            start, end = match.span()
            if start == end or _overlaps(start, end, taken):
                continue
            taken.append((start, end))
            found.append(Entity(label, match.group(0).strip(), start, end))
    if wanted is not None:
        found = [entity for entity in found if entity.label in wanted]
    found.sort(key=lambda entity: (entity.start, entity.end))
    return found


def extract_by_label(
    text: str, *, labels: Optional[Iterable[str]] = None
) -> Dict[str, List[Entity]]:
    """:func:`extract_entities` grouped by label, preserving document order."""
    grouped: Dict[str, List[Entity]] = {}
    for entity in extract_entities(text, labels=labels):
        grouped.setdefault(entity.label, []).append(entity)
    return grouped


def entity_texts(text: str, label: str) -> List[str]:
    """Distinct surface forms for one label, in order of first appearance.

    The shape the explanation layer wants ("dates changed from X to Y"), and
    the closest thing here to the prototype's ``extract_facts`` return value.
    """
    seen: Dict[str, None] = {}
    for entity in extract_entities(text, labels=[label]):
        seen.setdefault(entity.text, None)
    return list(seen)


def benchmark_regex(texts: Sequence[str]) -> Dict[str, Any]:
    """Run the extractor over ``texts`` and report counts and timing.

    Not an accuracy measurement -- there is no ground truth in this function,
    and a count is not a score. ``evaluation/entity_extraction_eval.py``
    computes precision, recall and F1 against committed annotations. This
    exists so inference time, which the brief asks for, is measured on the
    same code path the API uses rather than estimated.
    """
    counts: Dict[str, int] = {label: 0 for label in ENTITY_LABELS}
    clauses_with_any = 0
    total_chars = 0
    started = time.perf_counter()
    for text in texts:
        total_chars += len(text or "")
        entities = extract_entities(text or "")
        if entities:
            clauses_with_any += 1
        for entity in entities:
            counts[entity.label] += 1
    elapsed = time.perf_counter() - started
    clause_count = len(texts)
    return {
        "condition": "regex",
        "clauses": clause_count,
        "characters": total_chars,
        "mentions_by_label": counts,
        "mentions_total": sum(counts.values()),
        "clauses_with_any_entity": clauses_with_any,
        "elapsed_seconds": round(elapsed, 6),
        "ms_per_clause": (
            round(elapsed * 1000 / clause_count, 6) if clause_count else None
        ),
        "chars_per_second": (
            round(total_chars / elapsed, 1) if elapsed > 0 else None
        ),
    }
