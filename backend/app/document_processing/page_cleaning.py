"""Reversible removal of running headers, footers and printed page numbers.

Why this is a separate stage, and not part of normalisation
----------------------------------------------------------
``normalization.py`` works on one page at a time and is deliberately incapable
of this: deciding that a line is a *running* header requires comparing it
against the other pages of the same document. That is why ``CLAUDE.md`` §13.1
puts header/footer removal in Phase 2 rather than Phase 1.

The rules this module obeys
---------------------------
1. **The input text is never modified.** The cleaned text is a new field, and
   every removed line is recorded with the index it came from, so
   :meth:`CleanedPage.restore` reconstructs the original byte-for-byte. That
   round-trip is a test, not an aspiration — if removal cannot be undone, the
   project has lost source evidence, which is the one thing it must not do.
2. **Removal is conservative and must justify itself.** Every
   :class:`RemovedLine` carries a ``reason`` string naming the rule and the
   evidence ("repeated at line 0 on 36 of 37 pages"). A line removed without a
   reason would be indistinguishable from a bug.
3. **Nothing is removed from the middle of a page.** Candidates must form a
   contiguous run from the first or last non-blank line. A repeated phrase in
   body text is not a header, whatever its frequency.

The frequency threshold, and why it is 0.90 rather than 0.60
------------------------------------------------------------
``CLAUDE.md`` §13.1 proposed "a line repeating in the same position on >= 60% of
pages", and the Phase 1 validation report §4.6 confirmed that catches the two
documents in the sample that have real running headers. What the Phase 1 report
did **not** check is whether 60% also removes things it should not. Measured on
the same four documents, it does:

====================================  ==============  ====================
Repeated line                         Pages           What it actually is
====================================  ==============  ====================
"Reserve Bank of India (Commercial    36 of 37 (97%)  running header
Banks - Prudential Norms"
RBI bilingual letterhead block        2 of 2 (100%)   running letterhead
"BI" / "constituents" / "balance" /   7 of 10 (70%)   **table column
"sheet" / "items"                                     headers - content**
====================================  ==============  ====================

The third row is page 2-8 of the commercial-banks amendment, whose four-column
table pypdf flattens into a vertical stack of single words (Experiment A §5.2).
Those words are the table's own column headings, repeated because the table
spans seven pages. At a 60% threshold they are deleted from seven pages of a
ten-page document, which destroys exactly the clause content Phase 2 exists to
segment.

A 0.90 threshold separates the two cases cleanly on this corpus: 97% and 100%
are removed, 70% is kept. The value is a **tuned parameter reported as such**,
not a law — it is configurable, the detection plan records the evidence for
every decision, and a document whose running header genuinely appears on only
80% of its pages will be missed. That is the trade accepted here: a missed
header leaves noise in a segment, a wrongly removed line loses policy text.

Known limitation, inherited from the data
-----------------------------------------
``master-circular-272mc`` prints its letterhead on **page 1 only**. No
frequency rule can see it, because it does not repeat. This module does not
attempt to guess at page-1-only letterheads: page 1 is also where the title,
reference number and date live, so a positional rule there would remove the
document's most important metadata. It is reported as an uncleaned artefact
instead. The printed page number on that document *is* removed, by the separate
rule below.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "CleaningOptions",
    "CleaningPlan",
    "CleanedPage",
    "RemovedLine",
    "clean_pages",
    "detect_running_lines",
]

#: Reason codes recorded on every removed line. Stable strings, because they end
#: up in the segment JSON and in the data-quality report.
REASON_REPEATED_HEADER = "repeated_header"
REASON_REPEATED_FOOTER = "repeated_footer"
REASON_PAGE_NUMBER = "printed_page_number"

#: A line that is nothing but a page number, in any of the forms seen in
#: government PDFs. Anchored at both ends: a line containing a number plus other
#: words is content, not a page number.
_PAGE_NUMBER_PATTERNS = (
    re.compile(r"^(\d{1,4})$"),                      # "32"
    re.compile(r"^[-–—]\s*(\d{1,4})\s*[-–—]$"),      # "- 32 -"
    re.compile(r"^[Pp]age\s+(\d{1,4})$"),            # "Page 32"
    re.compile(r"^[Pp]age\s+(\d{1,4})\s+of\s+\d{1,4}$"),  # "Page 32 of 37"
    re.compile(r"^(\d{1,4})\s*/\s*\d{1,4}$"),        # "32/37"
    re.compile(r"^(\d{1,4})\s+of\s+\d{1,4}$"),       # "32 of 37"
)


@dataclass(frozen=True)
class CleaningOptions:
    """Switches and thresholds for header/footer removal.

    Every value is a tuned parameter, and the tuning is reported in
    ``backend/docs/phase2-segmentation.md`` and ``evaluation/experiment-b-segmentation.md``.
    Turning a rule off is how an experiment isolates its effect — the same
    pattern as :class:`~.normalization.NormalizationOptions`.
    """

    #: Fraction of pages a line must occupy, at the same position, to count as
    #: a running header or footer. See the module docstring for why 0.90.
    frequency_threshold: float = 0.90
    #: Below this page count, frequency carries almost no information: on a
    #: 2-page document any repeated line trivially scores 100%. Detection still
    #: runs (the RBI letterhead case is real and worth cleaning) but the plan
    #: records a warning so the result is never read as confident.
    low_confidence_below_pages: int = 4
    #: Hard floor. A single page cannot have a *running* anything.
    min_pages_for_frequency: int = 2
    #: How many non-blank lines from the top / bottom may be considered. A cap,
    #: not a target: the contiguity rule usually stops much earlier.
    max_header_lines: int = 20
    max_footer_lines: int = 8
    #: Safety valve. If the rules want more than this fraction of a page's
    #: non-blank lines, the page is left alone and the plan records why. A page
    #: that is *mostly* header is far more likely to be a detection failure
    #: than a real page.
    max_removed_fraction_per_page: float = 0.5
    remove_repeated_headers: bool = True
    remove_repeated_footers: bool = True
    remove_page_numbers: bool = True

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RemovedLine:
    """One line removed from one page, with enough detail to put it back.

    ``line_index`` is the index into ``original_text.split("\\n")``, including
    blank lines. That exactness is what makes removal reversible.
    """

    page_number: int
    line_index: int
    text: str
    reason: str
    #: Human-readable evidence, e.g. "repeated at top position 0 on 36/37 pages".
    evidence: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class CleanedPage:
    """One page's text with running furniture removed, and the audit trail.

    Attributes:
        source_text: Verbatim copy of the text handed to cleaning. Never
            written to. Named ``source_text`` rather than ``original_text``
            deliberately: a caller may clean either a page's raw extracted text
            or its normalised text, and the character offsets downstream are
            offsets into *whichever one was passed*. Reusing
            ``PageRecord.original_text``'s name here would make it look as
            though offsets were always into raw text, which is the mistake this
            rename exists to prevent.
        cleaned_text: The same text with ``removed_lines`` dropped.
        removed_lines: What was taken out, and why.
        line_offset_map: ``cleaned line index -> original line index``. This is
            what lets a segment built from cleaned text still report an offset
            into the original page, which is the whole point of doing removal
            reversibly (see ``backend/docs/phase2-segmentation.md``).
    """

    page_number: int
    source_text: str
    cleaned_text: str
    removed_lines: List[RemovedLine] = field(default_factory=list)
    line_offset_map: List[int] = field(default_factory=list)

    @property
    def removed_line_count(self) -> int:
        return len(self.removed_lines)

    def restore(self) -> str:
        """Reconstruct ``source_text`` from ``cleaned_text`` plus the record.

        The reversibility guarantee, as executable code. Asserted against the
        real corpus in the tests: if this ever stops returning the original, the
        cleaning stage is destroying evidence.
        """
        lines = self.cleaned_text.split("\n")
        # Reinsert in ascending index order so each insertion lands correctly.
        for removed in sorted(self.removed_lines, key=lambda item: item.line_index):
            lines.insert(removed.line_index, removed.text)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, object]:
        return {
            "page_number": self.page_number,
            "cleaned_text": self.cleaned_text,
            "removed_line_count": len(self.removed_lines),
            "removed_lines": [line.to_dict() for line in self.removed_lines],
            "line_offset_map": self.line_offset_map,
        }


@dataclass
class CleaningPlan:
    """What detection decided, before any text was touched.

    Separating the plan from its application is what makes the stage
    explainable: the plan can be printed, inspected and argued about without
    running removal, and the preview frontend shows it directly.
    """

    page_count: int
    options: Dict[str, object]
    #: ``{page_number: [line_index, ...]}`` - lines marked for removal.
    marked: Dict[int, List[int]] = field(default_factory=dict)
    #: Parallel to ``marked``: the reason and evidence for each line.
    reasons: Dict[int, Dict[int, Tuple[str, str]]] = field(default_factory=dict)
    #: Candidate header/footer lines that were *detected* - reported whether or
    #: not they were removed, so a near-miss is visible.
    header_candidates: List[Dict[str, object]] = field(default_factory=list)
    footer_candidates: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "page_count": self.page_count,
            "options": self.options,
            "header_candidates": self.header_candidates,
            "footer_candidates": self.footer_candidates,
            "marked_line_counts": {
                str(page): len(indices) for page, indices in self.marked.items()
            },
            "warnings": self.warnings,
        }


def _non_blank_positions(text: str) -> List[Tuple[int, str]]:
    """Return ``[(line_index, stripped_text), ...]`` for non-blank lines only.

    Blank lines are skipped for *positional* purposes - a header is the first
    thing on the page whether or not a blank line precedes it - but the original
    index is carried along so removal and restoration stay exact.
    """
    return [
        (index, line.strip())
        for index, line in enumerate(text.split("\n"))
        if line.strip()
    ]


def _looks_like_page_number(line: str) -> Optional[int]:
    """Return the number if the line is nothing but a page number, else ``None``."""
    for pattern in _PAGE_NUMBER_PATTERNS:
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def _detect_page_numbers(
    pages: Sequence[Tuple[int, str]],
    options: CleaningOptions,
    plan: CleaningPlan,
) -> None:
    """Mark printed page numbers near the top or bottom of each page.

    Two conditions, either of which is sufficient, because printed numbering
    does not always match physical numbering (a cover page, an annexure that
    restarts at 1):

    * the number **equals the physical page number** — unambiguous; or
    * bare-number lines appear in the same zone on at least
      ``frequency_threshold`` of pages, and the numbers **increase** across
      those pages — i.e. it is a numbering sequence, not a coincidence.

    The second condition is what catches an offset sequence. Requiring the
    numbers to increase is what stops a column of figures in a table from being
    read as page numbering.
    """
    if not options.remove_page_numbers:
        return

    # Candidates: (page_number, line_index, printed_value, zone)
    candidates: List[Tuple[int, int, int, str]] = []
    for page_number, text in pages:
        positions = _non_blank_positions(text)
        head = positions[:3]
        tail = positions[-3:] if len(positions) > 3 else []
        for index, line in head:
            value = _looks_like_page_number(line)
            if value is not None:
                candidates.append((page_number, index, value, "top"))
        for index, line in tail:
            value = _looks_like_page_number(line)
            if value is not None:
                candidates.append((page_number, index, value, "bottom"))

    if not candidates:
        return

    # Group by zone to test the sequence condition per zone.
    by_zone: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    for page_number, index, value, zone in candidates:
        by_zone[zone].append((page_number, index, value))

    for zone, entries in by_zone.items():
        pages_with_candidate = {page_number for page_number, _, _ in entries}
        coverage = len(pages_with_candidate) / len(pages)
        values_in_page_order = [
            value for _, _, value in sorted(entries, key=lambda item: item[0])
        ]
        is_increasing = all(
            later >= earlier
            for earlier, later in zip(values_in_page_order, values_in_page_order[1:])
        )
        sequence_ok = coverage >= options.frequency_threshold and is_increasing

        for page_number, index, value in entries:
            matches_physical = value == page_number
            if not (matches_physical or sequence_ok):
                continue
            evidence = (
                f"bare number {value} at {zone} of page {page_number}; "
                + (
                    "matches the physical page number"
                    if matches_physical
                    else f"part of an increasing sequence on "
                    f"{len(pages_with_candidate)}/{len(pages)} pages"
                )
            )
            plan.marked.setdefault(page_number, []).append(index)
            plan.reasons.setdefault(page_number, {})[index] = (
                REASON_PAGE_NUMBER,
                evidence,
            )


def _detect_repeated_block(
    pages: Sequence[Tuple[int, str]],
    options: CleaningOptions,
    plan: CleaningPlan,
    from_bottom: bool,
) -> None:
    """Mark a contiguous run of repeated lines at the top or bottom of pages.

    The algorithm:

    1. For every page, list its non-blank lines, **skipping anything already
       marked** (page numbers). This matters: the capital-adequacy document
       prints its page number *between* the second header line and the body, so
       without this step the header block would not be contiguous and would be
       truncated to its first line.
    2. Index them by distance from the top (or bottom) and count, for each
       ``(position, text)`` pair, how many pages share it.
    3. Walk outward from position 0. Keep going while the pair meets the
       frequency threshold; **stop at the first position that does not**. That
       contiguity requirement is what keeps a repeated phrase in body text from
       being treated as a header.
    """
    enabled = (
        options.remove_repeated_footers if from_bottom else options.remove_repeated_headers
    )
    if not enabled:
        return

    page_total = len(pages)
    if page_total < options.min_pages_for_frequency:
        plan.warnings.append(
            f"only {page_total} page(s): a running header cannot be distinguished "
            f"from content, so frequency detection was skipped"
        )
        return

    limit = options.max_footer_lines if from_bottom else options.max_header_lines

    # position -> text -> set of page numbers
    occurrences: Dict[int, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    # (page_number, position) -> line_index, for marking later
    index_lookup: Dict[Tuple[int, int], int] = {}

    for page_number, text in pages:
        already_marked = set(plan.marked.get(page_number, []))
        positions = [
            (index, line)
            for index, line in _non_blank_positions(text)
            if index not in already_marked
        ]
        ordered = list(reversed(positions)) if from_bottom else positions
        for position, (line_index, line) in enumerate(ordered[:limit]):
            occurrences[position][line].add(page_number)
            index_lookup[(page_number, position)] = line_index

    required = options.frequency_threshold * page_total
    zone = "bottom" if from_bottom else "top"
    reason = REASON_REPEATED_FOOTER if from_bottom else REASON_REPEATED_HEADER
    candidate_sink = plan.footer_candidates if from_bottom else plan.header_candidates

    for position in range(limit):
        if position not in occurrences:
            break
        text, pages_sharing = max(
            occurrences[position].items(), key=lambda item: len(item[1])
        )
        share = len(pages_sharing) / page_total
        candidate_sink.append(
            {
                "position": position,
                "zone": zone,
                "text": text,
                "pages": len(pages_sharing),
                "page_total": page_total,
                "share": round(share, 4),
                "removed": len(pages_sharing) >= required,
            }
        )
        if len(pages_sharing) < required:
            # Contiguity: the block ends here. Anything repeated further in is
            # body text that happens to recur.
            break

        evidence = (
            f"repeated at {zone} position {position} on "
            f"{len(pages_sharing)}/{page_total} pages ({share:.0%}, "
            f"threshold {options.frequency_threshold:.0%})"
        )
        for page_number in pages_sharing:
            line_index = index_lookup.get((page_number, position))
            if line_index is None:
                continue
            plan.marked.setdefault(page_number, []).append(line_index)
            plan.reasons.setdefault(page_number, {})[line_index] = (reason, evidence)


def detect_running_lines(
    pages: Sequence[Tuple[int, str]],
    options: Optional[CleaningOptions] = None,
) -> CleaningPlan:
    """Decide which lines are running furniture, without changing any text.

    Args:
        pages: ``[(page_number, text), ...]`` in document order. Page numbers
            must be the real 1-based physical numbers; the page-number rule
            compares against them.
        options: Thresholds and switches.

    Returns:
        A :class:`CleaningPlan` recording every decision and its evidence.
    """
    options = options or CleaningOptions()
    plan = CleaningPlan(page_count=len(pages), options=options.as_dict())

    if not pages:
        return plan
    if len(pages) < options.low_confidence_below_pages:
        plan.warnings.append(
            f"{len(pages)} page(s) is below the {options.low_confidence_below_pages}-page "
            f"confidence floor: any line repeated on all pages reaches 100% "
            f"trivially, so these removals are low confidence and should be "
            f"reviewed by eye"
        )

    # Page numbers first - see the note in _detect_repeated_block step 1.
    _detect_page_numbers(pages, options, plan)
    _detect_repeated_block(pages, options, plan, from_bottom=False)
    _detect_repeated_block(pages, options, plan, from_bottom=True)

    # Safety valve: refuse to gut a page.
    for page_number, text in pages:
        marked = plan.marked.get(page_number)
        if not marked:
            continue
        non_blank_total = len(_non_blank_positions(text))
        if not non_blank_total:
            continue
        fraction = len(marked) / non_blank_total
        if fraction > options.max_removed_fraction_per_page:
            plan.warnings.append(
                f"page {page_number}: detection marked {len(marked)} of "
                f"{non_blank_total} non-blank lines ({fraction:.0%}), above the "
                f"{options.max_removed_fraction_per_page:.0%} safety limit, so "
                f"nothing was removed from this page"
            )
            plan.marked.pop(page_number, None)
            plan.reasons.pop(page_number, None)

    return plan


def clean_pages(
    pages: Sequence[Tuple[int, str]],
    options: Optional[CleaningOptions] = None,
    plan: Optional[CleaningPlan] = None,
) -> Tuple[List[CleanedPage], CleaningPlan]:
    """Apply header/footer removal, reversibly.

    Args:
        pages: ``[(page_number, text), ...]`` in document order.
        options: Thresholds and switches.
        plan: A plan from :func:`detect_running_lines`. Supplying one lets a
            caller inspect or override detection before it is applied; omitted,
            it is computed here.

    Returns:
        ``(cleaned_pages, plan)``. Every :class:`CleanedPage` satisfies
        ``page.restore() == page.source_text``.
    """
    options = options or CleaningOptions()
    plan = plan or detect_running_lines(pages, options)

    cleaned: List[CleanedPage] = []
    for page_number, text in pages:
        lines = text.split("\n")
        marked = set(plan.marked.get(page_number, []))
        reasons = plan.reasons.get(page_number, {})

        kept_lines: List[str] = []
        offset_map: List[int] = []
        removed: List[RemovedLine] = []
        for index, line in enumerate(lines):
            if index in marked:
                reason, evidence = reasons.get(index, (REASON_REPEATED_HEADER, ""))
                removed.append(
                    RemovedLine(
                        page_number=page_number,
                        line_index=index,
                        text=line,
                        reason=reason,
                        evidence=evidence,
                    )
                )
                continue
            kept_lines.append(line)
            offset_map.append(index)

        cleaned.append(
            CleanedPage(
                page_number=page_number,
                source_text=text,
                cleaned_text="\n".join(kept_lines),
                removed_lines=removed,
                line_offset_map=offset_map,
            )
        )
        if removed:
            logger.info(
                "clean.page page=%d removed_lines=%d reasons=%s",
                page_number,
                len(removed),
                sorted({line.reason for line in removed}),
            )

    return cleaned, plan
