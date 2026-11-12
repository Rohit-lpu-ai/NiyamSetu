"""Paragraph and clause-aware segmentation, with page provenance throughout.

This is Phase 2. It turns the page-level text that Phase 1 produced into the
units the rest of the team consumes: Person 3 indexes them for retrieval,
Person 4 aligns them across versions, Person 5 searches them, Person 2 displays
them. Four of five team members are blocked on this module's output shape, which
is why the contract (:class:`Segment`) is defined as carefully as the algorithms.

Two methods, both run, both stored
----------------------------------
``segment_paragraphs`` is the **baseline**: blank-line splitting with careful
page-boundary handling and nothing else. ``segment_clauses`` is the
**experimental** method: it detects the numbering and heading structure that
government documents actually use. Both run on every version and are stored side
by side (``CLAUDE.md`` §13.2), so Experiment B can compare them and Person 3 can
compare chunking strategies without re-ingesting anything.

The provenance invariant
------------------------
**No segment may exist without valid page provenance.** Every segment carries
``page_start``, ``page_end`` and a ``page_offsets`` entry per page it touches,
with character offsets into that page's *original* text. A segment whose
provenance cannot be established raises
:class:`~.errors.SegmentProvenanceError` rather than being emitted with a
guessed page. ``CLAUDE.md`` §10.2 is explicit that a guessed page reference is
worse than no clause, because the project's entire claim is that a citation can
be trusted.

How offsets survive header removal
----------------------------------
Segmentation runs on *cleaned* text (headers, footers and page numbers
stripped), but an offset into cleaned text would be useless to a consumer: the
cleaned text is never stored, so there would be nothing for the offset to index.
:class:`SourceLine` bridges that gap. It is produced from the cleaned text but
carries the character range the line occupies in the **uncleaned** page text it
came from, so a segment's offsets address text that actually exists on disk.
Segments are spans of ``SourceLine``, which makes this true by construction,
with no arithmetic to get wrong.

What the offsets are offsets *into* — a contract point for the team
--------------------------------------------------------------------
``CLAUDE.md`` §11.4 specifies ``char_start`` / ``char_end`` as offsets into
``pages.raw_text``. **This module produces offsets into whichever page text it
was given**, and the pipeline gives it ``normalized_text``. The basis is
recorded explicitly on every output as ``offset_basis``, so no consumer has to
guess.

Why normalised rather than raw, stated so the team can overrule it:

* Segmentation has to run on normalised text to work at all. Clause detection
  matches on line starts, and raw extracted text carries collapsed-whitespace
  artefacts, soft hyphens and inconsistent indentation that defeat the patterns.
* ``normalization.collapse_blank_lines`` changes the **number of lines** on a
  page, so raw and normalised text do not share a line numbering. Mapping an
  offset from one to the other needs a per-page offset map, which §11.4
  explicitly puts out of scope for the MVP.
* The API serves ``text=normalized`` by default (``CLAUDE.md`` §12.3). Offsets
  on the same basis as the text being displayed are exactly right for the
  default path, and §11.4's approximation moves to the ``text=raw`` path
  instead — the less-used direction.

So the approximation in §11.4 is not removed, it is **inverted**, and the
trade is now explicit and recorded per segment. Raising ``offset_basis`` to a
stored column is a Phase 3 schema note, not a code change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import SegmentProvenanceError
from .identity import segment_id_from_parts
from .page_cleaning import CleanedPage

logger = logging.getLogger(__name__)

__all__ = [
    "METHOD_CLAUSE_AWARE",
    "METHOD_PARAGRAPH",
    "OFFSET_BASIS_NORMALIZED",
    "OFFSET_BASIS_ORIGINAL",
    "ClauseMarker",
    "PageOffset",
    "Segment",
    "SegmentationResult",
    "SourceLine",
    "build_source_lines",
    "detect_clause_marker",
    "segment_clauses",
    "segment_paragraphs",
]

#: The two ``segmentation_method`` values. These strings go into the JSON, the
#: database column and the API query parameter, so they are defined once.
METHOD_PARAGRAPH = "paragraph"
METHOD_CLAUSE_AWARE = "clause_aware"

#: What ``PageOffset.char_start`` / ``char_end`` are offsets into. Recorded on
#: every :class:`SegmentationResult` so a consumer never has to guess, and so
#: the deviation from ``CLAUDE.md`` §11.4 is visible in the data rather than
#: only in a docstring. See the module docstring for the reasoning.
OFFSET_BASIS_NORMALIZED = "normalized_text"
OFFSET_BASIS_ORIGINAL = "original_text"

#: ``segment_type`` vocabulary. Every segment gets exactly one.
TYPE_PARAGRAPH = "paragraph"
TYPE_CLAUSE = "clause"
TYPE_HEADING = "heading"
TYPE_ANNEXURE = "annexure"
TYPE_PREAMBLE = "preamble"
TYPE_UNCLASSIFIED = "unclassified"


# --------------------------------------------------------------------------- #
# Source lines: the bridge between cleaned text and original offsets
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceLine:
    """One line of cleaned text, with its address in the original page.

    ``char_start`` / ``char_end`` are a half-open range into the page text that
    was handed to cleaning — ``CleanedPage.source_text``, not necessarily the
    PDF's raw extracted text. See ``OFFSET_BASIS_*`` and the module docstring
    for which one the pipeline actually uses and why.

    Carrying the range on every line is what makes a segment's provenance a
    matter of construction rather than reconstruction.
    """

    page_number: int
    line_index: int
    text: str
    char_start: int
    char_end: int

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()

    @property
    def stripped(self) -> str:
        return self.text.strip()


def build_source_lines(cleaned_pages: Sequence[CleanedPage]) -> List[SourceLine]:
    """Flatten cleaned pages into one ordered list of addressable lines.

    Blank lines are **kept**: they are the paragraph-boundary signal, and
    dropping them here would make the baseline method unable to see a paragraph
    break. They are marked by :attr:`SourceLine.is_blank` rather than removed.

    Offsets are computed by walking the *original* page text, so a line's
    address is correct whether or not cleaning removed anything before it.
    """
    lines: List[SourceLine] = []
    for page in cleaned_pages:
        source_lines_raw = page.source_text.split("\n")
        # Character offset of the start of each original line. "+ 1" accounts
        # for the "\n" that split() consumed.
        starts: List[int] = []
        cursor = 0
        for original in source_lines_raw:
            starts.append(cursor)
            cursor += len(original) + 1

        kept = page.cleaned_text.split("\n")
        offset_map = page.line_offset_map
        if len(kept) != len(offset_map):
            # The map is produced alongside cleaned_text by clean_pages, so a
            # mismatch means the two were built separately or one was edited.
            # Offsets would be silently wrong, so refuse.
            raise SegmentProvenanceError(
                f"page {page.page_number}: cleaned text has {len(kept)} lines but "
                f"line_offset_map has {len(offset_map)} entries; offsets cannot "
                f"be trusted"
            )

        for text, original_index in zip(kept, offset_map):
            if original_index >= len(starts):
                raise SegmentProvenanceError(
                    f"page {page.page_number}: line_offset_map points at original "
                    f"line {original_index}, but the page has only {len(starts)}"
                )
            start = starts[original_index]
            lines.append(
                SourceLine(
                    page_number=page.page_number,
                    line_index=original_index,
                    text=text,
                    char_start=start,
                    char_end=start + len(source_lines_raw[original_index]),
                )
            )
    return lines


# --------------------------------------------------------------------------- #
# The segment contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PageOffset:
    """Where a segment sits on one specific page.

    One of these per page a segment touches. A clause spanning pages 3-4 gets
    two, which is what ``CLAUDE.md`` §11.4 requires and what makes precise
    highlighting possible. ``segment_pages`` in the Phase 3 schema is one row
    per instance of this class.
    """

    page_number: int
    char_start: int
    char_end: int
    line_index_start: int
    line_index_end: int

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise SegmentProvenanceError(
                f"page_number must be 1-based, got {self.page_number}"
            )
        if self.char_end < self.char_start:
            raise SegmentProvenanceError(
                f"page {self.page_number}: char_end ({self.char_end}) precedes "
                f"char_start ({self.char_start})"
            )

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass
class Segment:
    """One paragraph or clause. The unit the whole team consumes.

    Field names line up with the proposed ``segments`` table in
    ``CLAUDE.md`` §11.3 and the clause JSON in §12.3, so Phase 3 is an insert
    and Phase 4 can serve this almost unchanged.
    """

    segment_id: str
    document_id: str
    version_id: str
    segmentation_method: str
    ordinal: int
    segment_type: str
    text: str
    page_start: int
    page_end: int
    page_offsets: List[PageOffset]
    clause_number: Optional[str] = None
    heading: Optional[str] = None
    parent_segment_id: Optional[str] = None
    #: Nesting depth, 0 for top-level. Only meaningful for ``clause_aware``.
    level: int = 0
    #: Reserved for downstream annotation (classification labels, entities).
    #: The backend writes detection details here and nothing else
    #: (``CLAUDE.md`` §12.3).
    metadata: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # The provenance invariant, enforced at construction so no code path can
        # produce a segment that violates it.
        if not self.page_offsets:
            raise SegmentProvenanceError(
                f"segment {self.segment_id} (ordinal {self.ordinal}) has no page "
                f"offsets; a segment without provenance must not be emitted"
            )
        if self.page_start < 1 or self.page_end < 1:
            raise SegmentProvenanceError(
                f"segment {self.segment_id}: page range "
                f"{self.page_start}-{self.page_end} is not 1-based"
            )
        if self.page_end < self.page_start:
            raise SegmentProvenanceError(
                f"segment {self.segment_id}: page_end ({self.page_end}) precedes "
                f"page_start ({self.page_start})"
            )
        touched = sorted({offset.page_number for offset in self.page_offsets})
        if touched[0] != self.page_start or touched[-1] != self.page_end:
            raise SegmentProvenanceError(
                f"segment {self.segment_id}: page range "
                f"{self.page_start}-{self.page_end} disagrees with the pages its "
                f"offsets touch ({touched})"
            )
        if not self.text.strip():
            raise SegmentProvenanceError(
                f"segment {self.segment_id}: empty segments must not be emitted"
            )

    @property
    def spans_pages(self) -> bool:
        return self.page_end > self.page_start

    def to_dict(self) -> Dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "segmentation_method": self.segmentation_method,
            "ordinal": self.ordinal,
            "segment_type": self.segment_type,
            "clause_number": self.clause_number,
            "heading": self.heading,
            "text": self.text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "page_offsets": [offset.to_dict() for offset in self.page_offsets],
            "parent_segment_id": self.parent_segment_id,
            "level": self.level,
            "metadata": self.metadata,
        }


@dataclass
class SegmentationResult:
    """Every segment produced by one method for one version, plus run metadata."""

    document_id: str
    version_id: str
    segmentation_method: str
    segments: List[Segment]
    #: Which page text the character offsets refer to. The caller declares it,
    #: because the caller is what chose the text to segment.
    offset_basis: str = OFFSET_BASIS_NORMALIZED
    #: Counts and parameters, for the report and the preview frontend.
    stats: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "document_id": self.document_id,
            "version_id": self.version_id,
            "segmentation_method": self.segmentation_method,
            "offset_basis": self.offset_basis,
            "segment_count": len(self.segments),
            "stats": self.stats,
            "warnings": self.warnings,
            "segments": [segment.to_dict() for segment in self.segments],
        }


# --------------------------------------------------------------------------- #
# Building a segment from source lines
# --------------------------------------------------------------------------- #


def _page_offsets_from_lines(lines: Sequence[SourceLine]) -> List[PageOffset]:
    """Group lines by page into one :class:`PageOffset` per page touched.

    The offsets are the min and max of the lines actually present on that page,
    so a clause that starts halfway down page 3 and ends a third of the way into
    page 4 reports exactly that, rather than claiming whole pages.
    """
    by_page: Dict[int, List[SourceLine]] = {}
    for line in lines:
        by_page.setdefault(line.page_number, []).append(line)

    offsets: List[PageOffset] = []
    for page_number in sorted(by_page):
        page_lines = by_page[page_number]
        offsets.append(
            PageOffset(
                page_number=page_number,
                char_start=min(line.char_start for line in page_lines),
                char_end=max(line.char_end for line in page_lines),
                line_index_start=min(line.line_index for line in page_lines),
                line_index_end=max(line.line_index for line in page_lines),
            )
        )
    return offsets


def _join_lines(lines: Sequence[SourceLine]) -> str:
    """Join a segment's lines into its text.

    Lines are joined with a single space, not a newline. The line breaks in
    extracted PDF text are artefacts of page layout, not of the document's
    meaning, and a clause whose text carries them cannot be matched against a
    user's query. The page-layout information is not lost — it is in
    ``page_offsets`` and in the original page text, both of which are retained.
    """
    return " ".join(line.stripped for line in lines if line.stripped)


def _make_segment(
    lines: Sequence[SourceLine],
    document_id: str,
    version_id: str,
    method: str,
    ordinal: int,
    segment_type: str,
    clause_number: Optional[str] = None,
    heading: Optional[str] = None,
    parent_segment_id: Optional[str] = None,
    level: int = 0,
    metadata: Optional[Dict[str, object]] = None,
) -> Optional[Segment]:
    """Assemble a segment, or return ``None`` if the lines carry no text.

    ``None`` rather than an exception: a run of blank lines is a normal thing to
    encounter, and the caller's job is simply not to emit a segment for it
    ("avoid creating meaningless empty segments"). A *non-empty* segment that
    cannot establish provenance is a different matter and raises.
    """
    text = _join_lines(lines)
    if not text:
        return None
    content_lines = [line for line in lines if line.stripped]
    offsets = _page_offsets_from_lines(content_lines)
    return Segment(
        segment_id=segment_id_from_parts(version_id, method, ordinal),
        document_id=document_id,
        version_id=version_id,
        segmentation_method=method,
        ordinal=ordinal,
        segment_type=segment_type,
        text=text,
        page_start=offsets[0].page_number,
        page_end=offsets[-1].page_number,
        page_offsets=offsets,
        clause_number=clause_number,
        heading=heading,
        parent_segment_id=parent_segment_id,
        level=level,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# Baseline: paragraph segmentation
# --------------------------------------------------------------------------- #

#: Characters that end a sentence. A fragment ending in one of these is treated
#: as complete, so the next page starts a new paragraph.
_SENTENCE_FINAL = ".!?:;"

#: Trailing characters to look past when testing for sentence-final punctuation,
#: so ``(see para 3.)`` and ``"... the Directions."`` are recognised as complete.
_CLOSING_CHARS = ")]}\"'’”"


def _is_incomplete_fragment(text: str) -> bool:
    """Whether a page's last paragraph fragment looks mid-sentence.

    Used only to decide whether to join across a page boundary. Deliberately
    conservative: the default is *not* to join, because wrongly merging two
    paragraphs across a page break produces a segment whose page range spans
    both pages and whose text runs two thoughts together, which is harder for a
    reader to spot than a paragraph that was split one line early.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    tail = stripped.rstrip(_CLOSING_CHARS)
    if not tail:
        return False
    return tail[-1] not in _SENTENCE_FINAL


def _starts_as_continuation(text: str) -> bool:
    """Whether a page's first line looks like the middle of a sentence.

    A line that opens a new clause, a heading or a new sentence is not a
    continuation, however incomplete the previous page looked. Checking both
    sides is what keeps the join rule from swallowing a clause marker.
    """
    stripped = text.lstrip()
    if not stripped:
        return False
    if detect_clause_marker(stripped) is not None:
        return False
    first = stripped[0]
    # A lowercase letter cannot begin a sentence in these documents, so it is
    # the clearest positive signal. Digits and openers are ambiguous and
    # therefore treated as "not a continuation".
    return first.islower()


def segment_paragraphs(
    source_lines: Sequence[SourceLine],
    document_id: str,
    version_id: str,
    offset_basis: str = OFFSET_BASIS_NORMALIZED,
) -> SegmentationResult:
    """Split text into paragraphs. The control condition for Experiment B.

    The rules, in order of application:

    1. A blank line within a page ends the current paragraph.
    2. A page boundary ends the current paragraph **unless** the fragment so far
       is clearly incomplete (:func:`_is_incomplete_fragment`) *and* the next
       page's first line of content looks like a continuation
       (:func:`_starts_as_continuation`). Requiring both is what keeps the rule
       from joining a finished paragraph to a new clause.
    3. A paragraph containing no non-whitespace text is not emitted.

    Why the loop is written around *content* lines rather than all lines
    -------------------------------------------------------------------
    A page's extracted text begins and ends with newlines, and header removal
    leaves those behind, so the literal first line of every cleaned page is
    blank. An earlier version of this function applied rule 1 before rule 2 on
    raw line order, which meant the blank line at the page edge always closed
    the paragraph before the boundary was ever considered: ``cross_page_joins``
    was structurally zero and no paragraph could ever span a page. Measured on
    the capital-adequacy document, 32 of 36 page boundaries had an incomplete
    fragment open and were being split anyway.

    So blank runs and page changes are tracked separately, and at a page
    boundary the **page rule wins**. Blank lines adjacent to a page break are
    an artefact of extracting text page by page; they are not evidence that the
    author ended a paragraph there.

    Deliberately layout-agnostic otherwise: no numbering detection, no heading
    detection, no hierarchy. Every segment is ``segment_type="paragraph"`` with
    ``clause_number=None``. That is the point — it is the baseline the
    clause-aware method has to beat, and a baseline that quietly did some clause
    detection would make the comparison meaningless.
    """
    segments: List[Segment] = []
    buffer: List[SourceLine] = []
    cross_page_joins = 0
    page_boundary_splits = 0
    blank_line_splits = 0

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        segment = _make_segment(
            buffer,
            document_id,
            version_id,
            METHOD_PARAGRAPH,
            len(segments),
            TYPE_PARAGRAPH,
        )
        if segment is not None:
            segments.append(segment)
        buffer = []

    previous_page: Optional[int] = None
    blank_run = 0
    for line in source_lines:
        if line.is_blank:
            blank_run += 1
            continue

        page_changed = previous_page is not None and line.page_number != previous_page
        if buffer:
            if page_changed:
                fragment = _join_lines(buffer)
                if _is_incomplete_fragment(fragment) and _starts_as_continuation(
                    line.text
                ):
                    cross_page_joins += 1
                else:
                    page_boundary_splits += 1
                    flush()
            elif blank_run:
                blank_line_splits += 1
                flush()

        previous_page = line.page_number
        blank_run = 0
        buffer.append(line)

    flush()

    spanning = [segment for segment in segments if segment.spans_pages]
    return SegmentationResult(
        document_id=document_id,
        version_id=version_id,
        segmentation_method=METHOD_PARAGRAPH,
        segments=segments,
        offset_basis=offset_basis,
        stats={
            "segment_count": len(segments),
            "cross_page_joins": cross_page_joins,
            "page_boundary_splits": page_boundary_splits,
            "blank_line_splits": blank_line_splits,
            "page_spanning_segments": len(spanning),
            "mean_chars": (
                round(sum(len(s.text) for s in segments) / len(segments), 1)
                if segments
                else 0
            ),
        },
    )


# --------------------------------------------------------------------------- #
# Clause marker detection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClauseMarker:
    """A detected clause number or structural marker at the start of a line.

    ``style`` is the pattern that matched, recorded so Experiment B can report
    per-style accuracy and so an unexpected document's numbering can be
    diagnosed from the output alone. ``ambiguous`` flags the roman/alphabetic
    collision described in :func:`_classify_paren_token`.
    """

    raw: str
    style: str
    #: Text of the line after the marker and its separator.
    remainder: str
    #: Nesting rank implied by the marker style alone, before context. Dotted
    #: numeric markers know their own depth; bracketed ones do not and get
    #: ``None``, resolved against the enclosing context by the segmenter.
    intrinsic_level: Optional[int] = None
    ambiguous: bool = False
    segment_type: str = TYPE_CLAUSE

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


#: Roman numeral letters that are also ordinary list letters. ``(i)`` is both
#: "roman one" and "letter i", and no amount of regex resolves that.
_AMBIGUOUS_ROMAN_LETTERS = frozenset("ivxlcdm")

_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)

#: Dotted numeric clause numbers: ``2.``, ``3.2``, ``3.2.1``, ``3.2.1.4``.
#:
#: The trailing group is what rejects the false positive found in the real
#: corpus: ``5.5%-6.375% 3.5%-3.625% 100%`` is a row of a flattened table, and
#: it matched an earlier version of this pattern. The marker must be followed by
#: whitespace and then a letter or an opening bracket - i.e. by prose, not by a
#: percent sign or another number.
#: ``[0-9]`` rather than ``\d`` throughout, deliberately. Python's ``\d``
#: matches every Unicode decimal digit, so ``\d`` quietly matched the Devanagari
#: numeral in "२. A line" and produced a clause numbered ``२``. That was an
#: accident of the regex engine, not a decision, and it would hand downstream
#: code a clause number it cannot sort or cite. Devanagari-numbered clauses
#: therefore fall through to an unclassified segment - text preserved, no number
#: invented - which is the documented limitation while multilingual support is
#: paused (``CLAUDE.md`` D-3).
_NUMERIC_CLAUSE_RE = re.compile(
    r"^([0-9]{1,3}(?:\.[0-9]{1,3})*)\.?\s+(?=[A-Za-z(‘“\"'])(.*)$"
)

#: Bracketed markers: ``(a)``, ``(A)``, ``(i)``, ``(IV)``.
_PAREN_MARKER_RE = re.compile(r"^\(([A-Za-z]{1,6})\)\s*(.*)$")

#: Bracketed numeric sub-markers: ``(1)``, ``(12)``.
_PAREN_NUMBER_RE = re.compile(r"^\(([0-9]{1,3})\)\s*(.*)$")

#: Spelled-out references the brief asks for: ``Clause 5``, ``Section 7``,
#: ``Para 3``, ``Paragraph 29``. Implemented because the brief requires them,
#: and reported in Experiment B as **not occurring** in this corpus: in these
#: four RBI documents the words appear only mid-sentence ("conferred by
#: Section 35A"), never starting a line. The anchor is what keeps the
#: mid-sentence case from matching.
_SPELLED_CLAUSE_RE = re.compile(
    r"^(Clause|Section|Para|Paragraph|Regulation|Rule|Article)\s+"
    r"([0-9]{1,3}(?:\.[0-9]{1,3})*[A-Za-z]?)\b\.?\s*(.*)$"
)

#: Structural divisions. These outrank clauses and reset numbering.
_STRUCTURAL_RE = re.compile(
    r"^(CHAPTER|Chapter|PART|Part)\s+([IVXLC]{1,6}|[A-Z]|[0-9]{1,3})\b\.?\s*(.*)$"
)

#: Annexures, schedules and appendices.
#:
#: ``Schedule`` carries a real false positive in this corpus: page 2-8 of the
#: commercial-banks amendment contain table cells reading ``Schedule 13, 14``
#: and ``Schedule 16``, which are cross-references inside a flattened table, not
#: schedule headings. Requiring the marker to be the *whole* line, or to be
#: followed by a title rather than a bare comma-separated number list, is what
#: separates them.
#: Longest alternatives first: Python's ``|`` is leftmost-first, not
#: longest-match, so listing ``Annex`` before ``Annexure`` would make the line
#: "Annexure" report its marker as "Annex" and leave "ure" in the remainder.
_ANNEXURE_RE = re.compile(
    r"^(ANNEXURE|Annexure|ANNEX|Annex|SCHEDULE|Schedule|APPENDIX|Appendix)"
    r"(?:\s+([IVXLC]{1,6}|[A-Z]|[0-9]{1,3}))?\s*[-–—:]?\s*(.*)$"
)

#: A bare number list, as in the ``Schedule 13, 14`` table cell above.
_BARE_NUMBER_LIST_RE = re.compile(r"^[0-9]{1,3}(\s*,\s*[0-9]{1,3})*\.?$")


def _is_roman(token: str) -> bool:
    return bool(_ROMAN_RE.match(token))


def _roman_to_int(token: str) -> Optional[int]:
    """Convert a lowercase roman numeral to an int, or ``None`` if malformed."""
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    token = token.lower()
    if not all(char in values for char in token):
        return None
    total = 0
    previous = 0
    for char in reversed(token):
        value = values[char]
        total += -value if value < previous else value
        previous = max(previous, value)
    return total


def _classify_paren_token(
    token: str, previous_markers: Sequence[ClauseMarker]
) -> Optional[Tuple[str, bool]]:
    """Decide whether a bracketed letter is roman or alphabetic.

    Returns ``(style, ambiguous)`` where style is ``"paren_roman"`` or
    ``"paren_alpha"``, or ``None`` when the token is not a list marker at all.

    That ``None`` case is a bug fix found by reading the real output. An earlier
    version classified any multi-character token as alphabetic, which made
    ``(STs)`` a clause marker: the master-circular's line *"...credit facilities
    to Scheduled Castes (SCs) & Scheduled Tribes"* wraps so that ``(STs)``
    begins the next line, and the segmenter opened a clause numbered ``(STs)``.
    Multi-character bracketed tokens are only ever roman numerals in these
    documents; ``(ab)`` is not a numbering scheme anyone uses.

    The problem is real and unavoidable: ``(i)`` is the first roman numeral and
    also the ninth letter, and ``(c)``, ``(d)``, ``(l)``, ``(m)``, ``(v)`` and
    ``(x)`` are the same. In the corpus, 109 lines match a roman pattern and 75
    match an alphabetic one, and the overlap is exactly these letters.

    How it is resolved, in order:

    1. **Multi-character tokens are unambiguous.** ``(ii)``, ``(iv)``, ``(vii)``
       are roman; ``(ab)`` is not a list letter anyone uses.
    2. **A single letter outside the roman set is alphabetic.** ``(a)``, ``(b)``,
       ``(e)``, ``(f)`` - no ambiguity.
    3. **For the ambiguous single letters, look at the previous marker.** If the
       last bracketed marker was alphabetic and this token is its alphabetic
       successor (``(h)`` then ``(i)``), this is alphabetic. If it was roman and
       this is the roman successor (``(iii)`` then ``(iv)``), it is roman.
    4. **Otherwise default to roman and flag it ambiguous.** A bracketed ``(i)``
       opening a list is far more often roman "first item" than letter nine. The
       flag is what matters: it propagates to the segment's ``metadata`` so the
       count of ambiguous decisions is reportable rather than hidden.
    """
    lowered = token.lower()
    if len(token) > 1:
        # Only a genuine roman numeral survives here. Anything else - "STs",
        # "SCs", "ibid" - is an abbreviation that happens to sit at the start of
        # a wrapped line, not a clause number.
        return ("paren_roman", False) if _is_roman(token) else None
    if lowered not in _AMBIGUOUS_ROMAN_LETTERS:
        return "paren_alpha", False

    for marker in reversed(previous_markers):
        if marker.style not in {"paren_alpha", "paren_roman"}:
            continue
        previous_token = marker.raw.strip("()")
        if marker.style == "paren_alpha" and len(previous_token) == 1:
            if ord(lowered) == ord(previous_token.lower()) + 1:
                return "paren_alpha", False
        if marker.style == "paren_roman":
            previous_value = _roman_to_int(previous_token)
            current_value = _roman_to_int(lowered)
            if (
                previous_value is not None
                and current_value is not None
                and current_value == previous_value + 1
            ):
                return "paren_roman", False
        break

    return "paren_roman", True


def detect_clause_marker(
    line: str, previous_markers: Sequence[ClauseMarker] = ()
) -> Optional[ClauseMarker]:
    """Detect a clause number or structural marker at the start of a line.

    Args:
        line: One line of text. Leading whitespace is ignored.
        previous_markers: Markers already seen, most recent last. Used only to
            resolve the roman/alphabetic ambiguity; detection is otherwise
            stateless and the function is safe to call on a line in isolation.

    Returns:
        A :class:`ClauseMarker`, or ``None`` when the line does not open a
        numbered or structural unit. ``None`` is a normal, frequent answer, and
        the caller must keep the text as an unclassified segment rather than
        discarding it.
    """
    stripped = line.strip()
    if not stripped:
        return None

    structural = _STRUCTURAL_RE.match(stripped)
    if structural:
        word, number, remainder = structural.groups()
        return ClauseMarker(
            raw=f"{word} {number}",
            style="structural",
            remainder=remainder.strip(),
            intrinsic_level=0,
            segment_type=TYPE_HEADING,
        )

    annexure = _ANNEXURE_RE.match(stripped)
    if annexure:
        word, number, remainder = annexure.groups()
        remainder = remainder.strip()
        # Reject the flattened-table cross-reference: "Schedule 13, 14".
        is_bare_number_list = bool(_BARE_NUMBER_LIST_RE.match(remainder)) or (
            number is not None and _BARE_NUMBER_LIST_RE.match(f"{number} {remainder}".strip())
        )
        if not is_bare_number_list:
            raw = word if number is None else f"{word} {number}"
            return ClauseMarker(
                raw=raw,
                style="annexure",
                remainder=remainder,
                intrinsic_level=0,
                segment_type=TYPE_ANNEXURE,
            )

    spelled = _SPELLED_CLAUSE_RE.match(stripped)
    if spelled:
        word, number, remainder = spelled.groups()
        return ClauseMarker(
            raw=f"{word} {number}",
            style="spelled",
            remainder=remainder.strip(),
            intrinsic_level=number.count(".") + 1,
        )

    numeric = _NUMERIC_CLAUSE_RE.match(stripped)
    if numeric:
        number, remainder = numeric.groups()
        return ClauseMarker(
            raw=number,
            style="numeric_dotted",
            remainder=remainder.strip(),
            intrinsic_level=number.count(".") + 1,
        )

    paren_number = _PAREN_NUMBER_RE.match(stripped)
    if paren_number:
        number, remainder = paren_number.groups()
        return ClauseMarker(
            raw=f"({number})",
            style="paren_number",
            remainder=remainder.strip(),
        )

    paren = _PAREN_MARKER_RE.match(stripped)
    if paren:
        token, remainder = paren.groups()
        classified = _classify_paren_token(token, previous_markers)
        if classified is None:
            return None
        style, ambiguous = classified
        return ClauseMarker(
            raw=f"({token})",
            style=style,
            remainder=remainder.strip(),
            ambiguous=ambiguous,
        )

    return None


#: A heading with no number: ALL-CAPS, or short Title Case with no final stop.
#: Kept narrow on purpose - over-eager heading detection fragments body text.
_ALLCAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,.&()\-/']{4,80}$")
_MAX_HEADING_WORDS = 12


def _is_unnumbered_heading(line: str) -> bool:
    """Whether a line looks like a heading carrying no clause number.

    Three conditions, all required: it matches the ALL-CAPS shape, it is short
    enough to be a title rather than a sentence, and it does not end in a
    sentence-final stop. The reference-number line (``RBI/2026-27/297``) matches
    the shape, which is correct - it *is* a heading-like standalone line - and is
    distinguished from a real heading by having no letters-and-spaces content,
    so it is excluded here and kept as preamble instead.
    """
    stripped = line.strip()
    if not stripped or len(stripped.split()) > _MAX_HEADING_WORDS:
        return False
    if not _ALLCAPS_HEADING_RE.match(stripped):
        return False
    if stripped.rstrip(_CLOSING_CHARS)[-1:] in ".!?":
        return False
    # A reference number like "RBI/2026-27/297" is not a section heading.
    letters = sum(1 for char in stripped if char.isalpha())
    digits = sum(1 for char in stripped if char.isdigit())
    return letters > digits


# --------------------------------------------------------------------------- #
# Clause-aware segmentation
# --------------------------------------------------------------------------- #


def _resolve_level(
    marker: ClauseMarker, open_stack: Sequence[Tuple[int, ClauseMarker]]
) -> int:
    """Work out a marker's nesting depth from its style and what encloses it.

    Dotted numeric markers state their own depth: ``3.2.1`` is level 3. The
    bracketed styles do not - ``(a)`` is level 2 under ``3.`` but level 3 under
    ``3.2`` - so their depth is one below the innermost enclosing marker of a
    shallower *kind*. The ordering used, which is what these documents follow:

        structural / annexure  <  numeric dotted  <  paren number
                               <  paren alpha     <  paren roman

    A bracketed marker found with nothing open defaults to level 2, i.e. it is
    assumed to sit under an unseen parent rather than at the top level. That is
    the conservative reading: treating ``(a)`` as top-level would make every
    following clause its sibling.
    """
    if marker.intrinsic_level is not None:
        return marker.intrinsic_level

    kind_rank = {
        "structural": 0,
        "annexure": 0,
        "numeric_dotted": 1,
        "spelled": 1,
        "paren_number": 2,
        "paren_alpha": 3,
        "paren_roman": 4,
    }
    own_rank = kind_rank.get(marker.style, 3)
    for level, open_marker in reversed(list(open_stack)):
        if kind_rank.get(open_marker.style, 3) < own_rank:
            return level + 1
    return 2


def segment_clauses(
    source_lines: Sequence[SourceLine],
    document_id: str,
    version_id: str,
    offset_basis: str = OFFSET_BASIS_NORMALIZED,
) -> SegmentationResult:
    """Segment on clause numbering, headings and structural markers.

    The algorithm is a single pass with a nesting stack:

    1. For each non-blank line, ask :func:`detect_clause_marker` whether it
       opens a new unit.
    2. If it does, close the current segment and open a new one, resolving the
       marker's level against the stack (:func:`_resolve_level`) and setting
       ``parent_segment_id`` to the innermost open segment at a shallower level.
    3. If it does not, the line continues the current segment — **including
       across a page boundary**, because a clause that continues onto the next
       page is still one clause. This is the main behavioural difference from
       the paragraph baseline, and it is what produces page-spanning segments
       with two ``page_offsets`` entries.
    4. A blank line does **not** close a clause. Numbered clauses in these
       documents routinely contain blank lines between their own paragraphs, and
       splitting on them would reduce this method to the baseline.
    5. Text appearing before the first marker becomes ``preamble``; text that
       cannot be attributed to any open clause becomes ``unclassified``. Neither
       is discarded and neither is given an invented clause number.
    """
    segments: List[Segment] = []
    buffer: List[SourceLine] = []
    seen_markers: List[ClauseMarker] = []
    # (level, marker, segment_id) for currently open ancestors.
    open_stack: List[Tuple[int, ClauseMarker, str]] = []

    current_marker: Optional[ClauseMarker] = None
    current_level = 0
    current_type = TYPE_PREAMBLE
    current_heading: Optional[str] = None
    current_parent: Optional[str] = None

    style_counts: Dict[str, int] = {}
    ambiguous_markers = 0
    pending_heading: Optional[str] = None

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        ordinal = len(segments)
        metadata: Dict[str, object] = {}
        if current_marker is not None:
            metadata["marker_style"] = current_marker.style
            if current_marker.ambiguous:
                metadata["clause_number_ambiguous"] = True
                metadata["ambiguity"] = (
                    "bracketed single letter that is both a roman numeral and a "
                    "list letter; resolved as roman by default"
                )
        segment = _make_segment(
            buffer,
            document_id,
            version_id,
            METHOD_CLAUSE_AWARE,
            ordinal,
            current_type,
            clause_number=current_marker.raw if current_marker else None,
            heading=current_heading,
            parent_segment_id=current_parent,
            level=current_level,
            metadata=metadata,
        )
        if segment is not None:
            segments.append(segment)
        buffer = []

    for line in source_lines:
        if line.is_blank:
            # Rule 4: a blank line is not a clause boundary. It is still dropped
            # from the buffer's text by _join_lines, so nothing is lost.
            continue

        marker = detect_clause_marker(line.stripped, seen_markers)

        if marker is None and _is_unnumbered_heading(line.stripped):
            # A standalone heading: close what is open, and hold the heading so
            # it attaches to the clause that follows rather than becoming a
            # one-line segment of its own.
            flush()
            pending_heading = line.stripped
            current_marker = None
            current_level = 0
            current_type = TYPE_HEADING
            current_heading = line.stripped
            current_parent = open_stack[-1][2] if open_stack else None
            buffer = [line]
            continue

        if marker is None:
            buffer.append(line)
            continue

        # A new unit starts here.
        flush()
        seen_markers.append(marker)
        style_counts[marker.style] = style_counts.get(marker.style, 0) + 1
        if marker.ambiguous:
            ambiguous_markers += 1

        level = _resolve_level(marker, [(lv, mk) for lv, mk, _ in open_stack])
        while open_stack and open_stack[-1][0] >= level:
            open_stack.pop()

        current_marker = marker
        current_level = level
        current_type = marker.segment_type
        current_parent = open_stack[-1][2] if open_stack else None
        current_heading = pending_heading
        pending_heading = None

        # The id this segment *will* get, so children can point at it. Derived
        # from the ordinal it is about to occupy, which is len(segments).
        prospective_id = segment_id_from_parts(
            version_id, METHOD_CLAUSE_AWARE, len(segments)
        )
        open_stack.append((level, marker, prospective_id))
        buffer = [line]

    flush()

    # Anything still typed as preamble after the first marker was found is text
    # we could not attribute. Relabel it honestly rather than leaving it looking
    # like a document preamble.
    first_clause_ordinal = next(
        (
            segment.ordinal
            for segment in segments
            if segment.segment_type in {TYPE_CLAUSE, TYPE_ANNEXURE}
        ),
        None,
    )
    if first_clause_ordinal is not None:
        for segment in segments:
            if (
                segment.ordinal > first_clause_ordinal
                and segment.segment_type == TYPE_PREAMBLE
            ):
                segment.segment_type = TYPE_UNCLASSIFIED

    spanning = [segment for segment in segments if segment.spans_pages]
    warnings: List[str] = []
    if segments and not style_counts:
        warnings.append(
            "no clause marker of any known style was detected; this document's "
            "numbering is either absent or in a style this method does not "
            "handle, and the output is effectively one unclassified block"
        )
    if ambiguous_markers:
        warnings.append(
            f"{ambiguous_markers} bracketed marker(s) were ambiguous between a "
            f"roman numeral and a list letter and were resolved as roman; see "
            f"each segment's metadata.clause_number_ambiguous"
        )

    return SegmentationResult(
        document_id=document_id,
        version_id=version_id,
        segmentation_method=METHOD_CLAUSE_AWARE,
        segments=segments,
        offset_basis=offset_basis,
        stats={
            "segment_count": len(segments),
            "marker_styles": style_counts,
            "ambiguous_markers": ambiguous_markers,
            "page_spanning_segments": len(spanning),
            "max_level": max((s.level for s in segments), default=0),
            "segments_with_heading": sum(1 for s in segments if s.heading),
            "segments_with_parent": sum(1 for s in segments if s.parent_segment_id),
            "type_counts": _count_types(segments),
            "mean_chars": (
                round(sum(len(s.text) for s in segments) / len(segments), 1)
                if segments
                else 0
            ),
        },
        warnings=warnings,
    )


def _count_types(segments: Iterable[Segment]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for segment in segments:
        counts[segment.segment_type] = counts.get(segment.segment_type, 0) + 1
    return counts
