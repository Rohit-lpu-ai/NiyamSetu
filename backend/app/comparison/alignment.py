"""Clause alignment and change classification.

Input: two lists of clauses, in document order, each already carrying its real
page provenance from the database. Output: one list of change records, every
one of which names which clause on each side it came from, or states that there
is no counterpart.

The algorithm, and why it is staged
-----------------------------------
The teammate's prototype used a single stage: score every old clause against
every new clause with TF-IDF cosine, then greedily take the best pairs above a
threshold. That stage is kept **as the last one**, because it is the right
answer for clauses whose wording changed. What it cannot do on its own is the
problem the brief names explicitly -- it reports a clause as *modified* when
only its number changed, and it has no way to say "I am not sure".

So the matcher runs four stages, strongest evidence first. Each stage only
sees clauses the earlier stages left unmatched, and matches are one-to-one
throughout.

**Stage 1 -- identical text.** Clauses whose :func:`~.similarity.comparison_key`
is equal are the same clause, whatever happened to their number or position.
This is the stage that makes renumbering and reordering survive: it does not
look at the number or the ordinal at all. When the same text occurs more than
once on a side (repeated boilerplate -- "Yours faithfully", a table header),
candidates are resolved by closest clause number, then closest relative
position, so the pairing is deterministic rather than whichever came first.

**Stage 2 -- same clause number, related text.** A printed clause number is
strong evidence, but not proof: "1." occurs once per annexure. So a
number match is accepted only when the texts are at least
``number_match_threshold`` similar. Below that, the number is treated as a
coincidence and the clauses go on to stage 3.

**Stage 3 -- similarity.** The prototype's greedy best-first matching over
everything left, accepting pairs at or above ``match_threshold``. Greedy
rather than optimal (Hungarian) on purpose: greedy is O(n log n) after
scoring, needs no dependency, and is explainable in a sentence. The cost is
that a globally better assignment can be missed, which is why the runner-up
score is carried into the output as ambiguity rather than discarded.

**Stage 4 -- no counterpart.** Unmatched old clauses are ``removed``,
unmatched new clauses are ``added``. Nothing is forced into a match: the
brief requires that unmatched clauses be reported as unmatched, and a
threshold that matched everything would be a threshold that matched the wrong
things.

Classification
--------------
``unchanged``  the two comparison keys are equal. The flags ``renumbered``
               and ``reordered`` say whether the number or the position moved;
               either on its own is **not** a modification.
``modified``   matched, but the keys differ.
``added``      present in the new version only.
``removed``    present in the old version only.

Reordering is detected with a longest-increasing-subsequence over the matched
pairs' ordinals. The LIS is the set of clauses that kept their relative order;
anything outside it genuinely moved. Comparing each pair against its immediate
neighbour instead would flag a whole run of clauses as reordered when a single
clause was promoted to the top.

Confidence
----------
``confidence`` is a **label computed from stated rules**, never a probability
-- the system has no calibration data, and a number like "0.83 confident"
would be an invention. The rules are in :func:`confidence_label` and are
tested. The raw ``similarity`` and the ``runner_up_similarity`` are always
exposed alongside it, so a reader can apply their own judgement.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.comparison.similarity import comparison_key, similarity_matrix

__all__ = [
    "ClauseRef",
    "AlignmentConfig",
    "Change",
    "CHANGE_TYPES",
    "CONFIDENCE_LEVELS",
    "MATCH_BASES",
    "align",
    "confidence_label",
    "summarize",
]

#: Every change record carries exactly one of these.
CHANGE_TYPES = ("added", "removed", "modified", "unchanged")

#: How a matched pair was established, strongest first.
MATCH_BASES = ("exact_text", "clause_number", "similarity")

CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass(frozen=True)
class ClauseRef:
    """One clause on one side of the comparison, with its provenance intact.

    A plain value object, built by ``comparison.repository`` from the
    ``segments`` and ``segment_pages`` rows. It is **not** a second model of a
    segment: it carries no fields the database does not already hold, and
    nothing here writes one back. ``pages`` is the list of
    ``{page_number, char_start, char_end, line_index_start, line_index_end}``
    dictionaries exactly as ``db.repository.get_segments`` returns them, so a
    change record can be traced to a character range on a page without this
    module re-deriving anything.
    """

    clause_id: str
    ordinal: int
    text: str
    page_start: int
    page_end: int
    clause_number: Optional[str] = None
    heading: Optional[str] = None
    segment_type: str = "unclassified"
    level: int = 0
    marker_style: Optional[str] = None
    clause_number_ambiguous: bool = False
    pages: Tuple[Dict[str, Any], ...] = ()
    fingerprint: Optional[str] = None

    @property
    def key(self) -> str:
        """Formatting-insensitive comparison key for the full text."""
        return comparison_key(self.text)

    @property
    def key_without_marker(self) -> str:
        """Comparison key with a leading clause number removed."""
        return comparison_key(self.text, drop_marker=True)

    def to_dict(self) -> Dict[str, Any]:
        """The side payload used in a change record."""
        return {
            "clause_id": self.clause_id,
            "ordinal": self.ordinal,
            "clause_number": self.clause_number,
            "clause_number_ambiguous": self.clause_number_ambiguous,
            "heading": self.heading,
            "segment_type": self.segment_type,
            "level": self.level,
            "marker_style": self.marker_style,
            "text": self.text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "pages": [dict(page) for page in self.pages],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class AlignmentConfig:
    """Every threshold the matcher uses, in one place and all overridable.

    The brief requires thresholds to be "configurable and testable". They are
    constructor arguments with documented defaults rather than module
    constants, so a test can drive the matcher to a specific behaviour and the
    API can expose them in the response -- a result whose thresholds are not
    reported cannot be reproduced.

    The defaults were chosen on the real RBI version pairs (see
    ``backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md``), not guessed:

    ``match_threshold = 0.45``
        Below this, matched pairs on the real pairs were overwhelmingly
        unrelated clauses that happened to share policy boilerplate
        ("banks are advised to...", "the Chairman / Managing Director").
        The prototype's 0.25 default produced those false matches; it is kept
        selectable because a user may want a looser pass.

    ``number_match_threshold = 0.20``
        A deliberately lower bar than ``match_threshold``, because an equal
        printed clause number is independent evidence. It is not zero: "1."
        appears in every annexure, so a number match on unrelated text must
        still be rejected.

    ``ambiguity_margin = 0.05``
        If the runner-up scores within this of the winner, the match is
        reported as ambiguous. The brief requires uncertain matches to be
        visually distinguishable from confident ones (``CLAUDE.md`` §5.4), and
        this is the signal the UI uses.

    ``high_confidence_similarity = 0.75``
        Where a similarity match stops being a guess. Also the prototype's own
        cut-off for "mostly similar, some wording changed".
    """

    match_threshold: float = 0.45
    number_match_threshold: float = 0.20
    ambiguity_margin: float = 0.05
    high_confidence_similarity: float = 0.75
    #: Compare clause numbers case- and whitespace-insensitively; '(A)' and
    #: '(a)' are the same printed marker read by two extractions.
    fold_clause_numbers: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_threshold": self.match_threshold,
            "number_match_threshold": self.number_match_threshold,
            "ambiguity_margin": self.ambiguity_margin,
            "high_confidence_similarity": self.high_confidence_similarity,
            "fold_clause_numbers": self.fold_clause_numbers,
        }


@dataclass
class Change:
    """One row of the comparison: what happened to one clause.

    ``old`` and ``new`` are ``ClauseRef`` or ``None``; at least one is always
    present. Everything else is derived and explained in the module docstring.
    """

    change_type: str
    old: Optional[ClauseRef] = None
    new: Optional[ClauseRef] = None
    similarity: Optional[float] = None
    runner_up_similarity: Optional[float] = None
    match_basis: Optional[str] = None
    ambiguous: bool = False
    renumbered: bool = False
    reordered: bool = False
    #: Set on an ambiguous match: the clause id the matcher nearly chose
    #: instead. Shown so a reviewer can check the alternative themselves.
    runner_up_clause_id: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.change_type not in CHANGE_TYPES:
            raise ValueError(
                f"change_type must be one of {CHANGE_TYPES}, "
                f"got {self.change_type!r}"
            )
        if self.old is None and self.new is None:
            raise ValueError("a change must have an old clause, a new clause, or both")
        if self.change_type == "added" and self.old is not None:
            raise ValueError("an 'added' change cannot have an old clause")
        if self.change_type == "removed" and self.new is not None:
            raise ValueError("a 'removed' change cannot have a new clause")
        if self.change_type in ("modified", "unchanged") and (
            self.old is None or self.new is None
        ):
            raise ValueError(f"a {self.change_type!r} change needs both sides")

    @property
    def confidence(self) -> str:
        return confidence_label(self)

    @property
    def sort_key(self) -> Tuple[int, int]:
        """Document order, reading the new version where there is one.

        A removed clause has no position in the new version, so it is placed
        at its old ordinal. That keeps a removal next to the clauses it used
        to sit between, which is where a reader looks for it.
        """
        if self.new is not None:
            return (self.new.ordinal, 0)
        assert self.old is not None
        return (self.old.ordinal, 1)


def confidence_label(change: Change) -> str:
    """``high`` / ``medium`` / ``low`` from stated rules. Not a probability.

    * ``added`` and ``removed`` are **not** scored at all -- they are
      "no counterpart was found", and attaching a confidence to an absence
      would invite reading it as "probably really added".  They return
      ``medium``, and the API exposes ``similarity = null`` beside it so the
      absence of a score is visible.
    * An ambiguous match is always ``low``, whatever it scored: the matcher
      nearly chose a different clause, and that is the single most useful
      thing to tell a reviewer.
    * An exact-text match is ``high``: the keys are equal, so there is nothing
      to be uncertain about. ``alignment.align`` never marks one ambiguous for
      the same reason.
    * A similarity match is ``high`` at or above
      ``high_confidence_similarity``, otherwise ``medium``.
    * A clause-number match whose text similarity is low is ``medium``: the
      number is real evidence, but the wording has diverged enough that a
      human should look. It becomes ``low`` if it was also ambiguous, which a
      clause-number match can be -- see the note in :func:`align`.
    """
    if change.ambiguous:
        return "low"
    if change.change_type in ("added", "removed"):
        return "medium"
    if change.match_basis == "exact_text":
        return "high"
    if change.similarity is not None and change.similarity >= 0.75:
        return "high"
    return "medium"


def _fold_number(value: Optional[str], *, fold: bool) -> Optional[str]:
    """Canonical form of a printed clause number, or ``None``."""
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if fold:
        text = text.casefold().strip(" .)](['\"")
    return text or None


def _lis_indices(sequence: Sequence[int]) -> List[int]:
    """Indices of one longest strictly increasing subsequence of ``sequence``.

    Patience sorting, O(n log n). Used to decide which matched clauses kept
    their relative order: anything outside the subsequence moved.

    When several subsequences tie for longest, this returns the one patience
    sorting finds, which is deterministic for a given input -- the property
    that matters, since "which clauses are reported as reordered" must not
    change between runs.
    """
    if not sequence:
        return []
    tails: List[int] = []        # tails[k] = smallest tail of a (k+1)-length run
    tail_indices: List[int] = []
    predecessor: List[int] = [-1] * len(sequence)
    for i, value in enumerate(sequence):
        position = bisect.bisect_left(tails, value)
        if position == len(tails):
            tails.append(value)
            tail_indices.append(i)
        else:
            tails[position] = value
            tail_indices[position] = i
        predecessor[i] = tail_indices[position - 1] if position > 0 else -1
    out: List[int] = []
    cursor = tail_indices[-1]
    while cursor != -1:
        out.append(cursor)
        cursor = predecessor[cursor]
    out.reverse()
    return out


def _resolve_duplicate_candidates(
    old_index: int,
    candidates: Sequence[int],
    old: Sequence[ClauseRef],
    new: Sequence[ClauseRef],
    *,
    fold: bool,
) -> int:
    """Pick one new clause from several with identical text. Deterministic.

    Order of preference: the one whose printed clause number is equal, then
    the one closest in relative document position, then the lowest ordinal.
    Relative position rather than absolute, because the two versions have
    different lengths -- clause 40 of 45 and clause 36 of 40 are the same place
    in the document, and an absolute comparison would say they are four apart.
    """
    old_clause = old[old_index]
    old_number = _fold_number(old_clause.clause_number, fold=fold)
    if old_number is not None:
        same_number = [
            j
            for j in candidates
            if _fold_number(new[j].clause_number, fold=fold) == old_number
        ]
        if len(same_number) == 1:
            return same_number[0]
        if same_number:
            candidates = same_number

    old_position = old_index / max(1, len(old) - 1) if len(old) > 1 else 0.0

    def distance(j: int) -> Tuple[float, int]:
        new_position = j / max(1, len(new) - 1) if len(new) > 1 else 0.0
        return (abs(new_position - old_position), new[j].ordinal)

    return min(candidates, key=distance)


def align(
    old: Sequence[ClauseRef],
    new: Sequence[ClauseRef],
    config: Optional[AlignmentConfig] = None,
) -> List[Change]:
    """Align two versions' clauses and classify every one of them.

    Returns changes in new-version document order, with removals positioned at
    their old ordinal (see :attr:`Change.sort_key`). The result is a pure
    function of the inputs and the config -- no database, no clock, no
    randomness -- which is what lets the API return a deterministic
    ``comparison_id`` without persisting anything.
    """
    cfg = config or AlignmentConfig()
    old = list(old)
    new = list(new)

    if not old and not new:
        return []
    if not new:
        return [Change("removed", old=clause) for clause in old]
    if not old:
        return [Change("added", new=clause) for clause in new]

    matched_old: Dict[int, int] = {}      # old index -> new index
    matched_new: Dict[int, int] = {}      # new index -> old index
    basis: Dict[int, str] = {}            # old index -> match basis

    # -- Stage 1: identical comparison keys -------------------------------
    new_by_key: Dict[str, List[int]] = {}
    for j, clause in enumerate(new):
        new_by_key.setdefault(clause.key, []).append(j)

    for i, clause in enumerate(old):
        candidates = [j for j in new_by_key.get(clause.key, ()) if j not in matched_new]
        if not candidates:
            continue
        chosen = (
            candidates[0]
            if len(candidates) == 1
            else _resolve_duplicate_candidates(
                i, candidates, old, new, fold=cfg.fold_clause_numbers
            )
        )
        matched_old[i] = chosen
        matched_new[chosen] = i
        basis[i] = "exact_text"

    # Stage 1b: identical once a leading clause number is removed. This is
    # the renumbered-but-unchanged case, and it is a separate pass so that an
    # exact full-text match always wins over a marker-stripped one.
    unmatched_new_by_bare_key: Dict[str, List[int]] = {}
    for j, clause in enumerate(new):
        if j in matched_new:
            continue
        unmatched_new_by_bare_key.setdefault(clause.key_without_marker, []).append(j)

    for i, clause in enumerate(old):
        if i in matched_old:
            continue
        bare = clause.key_without_marker
        if not bare:
            continue
        candidates = [
            j for j in unmatched_new_by_bare_key.get(bare, ()) if j not in matched_new
        ]
        if not candidates:
            continue
        chosen = (
            candidates[0]
            if len(candidates) == 1
            else _resolve_duplicate_candidates(
                i, candidates, old, new, fold=cfg.fold_clause_numbers
            )
        )
        matched_old[i] = chosen
        matched_new[chosen] = i
        basis[i] = "exact_text"

    # The similarity matrix is needed from stage 2 onward, and is computed
    # over every clause on both sides so IDF is shared. Skipped entirely when
    # stage 1 matched everything, which is the common case for two issues of a
    # circular that differ in a handful of clauses.
    need_scores = len(matched_old) < len(old) or len(matched_new) < len(new)
    scores: List[List[float]] = (
        similarity_matrix([c.text for c in old], [c.text for c in new])
        if need_scores
        else []
    )

    # -- Stage 2: equal printed clause number, related text ----------------
    if need_scores:
        new_by_number: Dict[str, List[int]] = {}
        for j, clause in enumerate(new):
            number = _fold_number(clause.clause_number, fold=cfg.fold_clause_numbers)
            if number and not clause.clause_number_ambiguous:
                new_by_number.setdefault(number, []).append(j)

        for i, clause in enumerate(old):
            if i in matched_old or clause.clause_number_ambiguous:
                continue
            number = _fold_number(clause.clause_number, fold=cfg.fold_clause_numbers)
            if not number:
                continue
            candidates = [
                j
                for j in new_by_number.get(number, ())
                if j not in matched_new
                and scores[i][j] >= cfg.number_match_threshold
            ]
            if not candidates:
                continue
            chosen = max(candidates, key=lambda j: (scores[i][j], -new[j].ordinal))
            matched_old[i] = chosen
            matched_new[chosen] = i
            basis[i] = "clause_number"

    # -- Stage 3: greedy best-first similarity ------------------------------
    if need_scores:
        pending = [
            (scores[i][j], i, j)
            for i in range(len(old))
            if i not in matched_old
            for j in range(len(new))
            if j not in matched_new and scores[i][j] >= cfg.match_threshold
        ]
        # Sort by score descending, then by ordinals, so equal scores resolve
        # the same way on every run rather than by dictionary order.
        pending.sort(key=lambda item: (-item[0], item[1], item[2]))
        for score, i, j in pending:
            if i in matched_old or j in matched_new:
                continue
            matched_old[i] = j
            matched_new[j] = i
            basis[i] = "similarity"

    # -- Reordering: which matched pairs kept their relative order? ---------
    ordered_pairs = sorted(matched_old.items())
    kept_order = set()
    if ordered_pairs:
        lis = _lis_indices([j for _, j in ordered_pairs])
        kept_order = {ordered_pairs[position][0] for position in lis}

    # -- Build the change records ------------------------------------------
    changes: List[Change] = []
    for i, clause in enumerate(old):
        if i not in matched_old:
            changes.append(Change("removed", old=clause))
            continue
        j = matched_old[i]
        counterpart = new[j]
        how = basis[i]
        score = scores[i][j] if need_scores and scores else 1.0
        if how == "exact_text":
            # Equal keys: the cosine is not what established the match, and
            # reporting a computed 0.98 next to "identical" invites the reader
            # to believe the text differs. State it as 1.0.
            score = 1.0

        runner_up, runner_up_id = _runner_up(
            i, j, scores, new, matched_new, need_scores
        )
        # Ambiguity applies to both *scored* bases, not only to stage 3.
        # A clause-number match can be the more uncertain of the two: the
        # number is accepted at a lower bar, so the runner-up can score
        # *higher* than the clause that won -- which is precisely the case a
        # reviewer most needs flagged. An exact-text match is never ambiguous,
        # because the comparison keys are equal and there is nothing to be
        # uncertain about.
        #
        # Found on the ``ambiguous_alignment`` fixture, where clause "1." was
        # matched to clause "1." at 0.774 while clause "2." scored 0.839. The
        # first version of this check only looked at stage 3 and reported that
        # pairing as confident.
        ambiguous = (
            how in ("similarity", "clause_number")
            and runner_up is not None
            and (score - runner_up) <= config_margin(cfg)
        )

        same_text = clause.key == counterpart.key
        bare_same = clause.key_without_marker == counterpart.key_without_marker
        numbers_differ = _fold_number(
            clause.clause_number, fold=cfg.fold_clause_numbers
        ) != _fold_number(counterpart.clause_number, fold=cfg.fold_clause_numbers)

        if same_text or bare_same:
            change_type = "unchanged"
        else:
            change_type = "modified"

        notes: List[str] = []
        if bare_same and not same_text:
            notes.append(
                "The clause text is identical once the printed clause number "
                "is removed: only the numbering changed."
            )

        changes.append(
            Change(
                change_type,
                old=clause,
                new=counterpart,
                similarity=round(float(score), 6),
                runner_up_similarity=(
                    round(float(runner_up), 6) if runner_up is not None else None
                ),
                match_basis=how,
                ambiguous=ambiguous,
                renumbered=numbers_differ,
                reordered=i not in kept_order,
                runner_up_clause_id=runner_up_id if ambiguous else None,
                notes=notes,
            )
        )

    for j, clause in enumerate(new):
        if j not in matched_new:
            changes.append(Change("added", new=clause))

    changes.sort(key=lambda change: change.sort_key)
    return changes


def config_margin(cfg: AlignmentConfig) -> float:
    """The ambiguity margin, as its own function so a test can pin it."""
    return cfg.ambiguity_margin


def _runner_up(
    i: int,
    chosen: int,
    scores: Sequence[Sequence[float]],
    new: Sequence[ClauseRef],
    matched_new: Dict[int, int],
    need_scores: bool,
) -> Tuple[Optional[float], Optional[str]]:
    """The best score this old clause had against a clause it did *not* get.

    Candidates exclude clauses matched to someone else, because "I nearly
    chose a clause that was already spoken for" is not a useful ambiguity --
    the matcher was never going to take it.
    """
    if not need_scores or not scores:
        return (None, None)
    best: Optional[float] = None
    best_j: Optional[int] = None
    for j in range(len(new)):
        if j == chosen or matched_new.get(j) not in (None, i):
            continue
        value = scores[i][j]
        if best is None or value > best:
            best, best_j = value, j
    if best is None or best_j is None:
        return (None, None)
    return (round(float(best), 6), new[best_j].clause_id)


def summarize(changes: Sequence[Change]) -> Dict[str, Any]:
    """Counts a UI can render without walking the list itself.

    ``similarity_mean`` covers **matched pairs only** and is explicitly not a
    policy-similarity score: it is the mean cosine of the pairs the matcher
    chose, which says how confidently things lined up, not how alike the two
    documents are. The prototype carried the same caveat in its caption and it
    is kept here, in the payload, so it travels with the number.
    """
    counts = {change_type: 0 for change_type in CHANGE_TYPES}
    confidences = {level: 0 for level in CONFIDENCE_LEVELS}
    bases = {name: 0 for name in MATCH_BASES}
    ambiguous = renumbered = reordered = 0
    scored: List[float] = []

    for change in changes:
        counts[change.change_type] += 1
        confidences[change.confidence] += 1
        if change.match_basis:
            bases[change.match_basis] += 1
        if change.ambiguous:
            ambiguous += 1
        if change.renumbered:
            renumbered += 1
        if change.reordered:
            reordered += 1
        if change.similarity is not None:
            scored.append(change.similarity)

    matched = counts["modified"] + counts["unchanged"]
    total = len(changes)
    return {
        "total_changes": total,
        "counts": counts,
        "matched_pairs": matched,
        "confidence_counts": confidences,
        "match_basis_counts": bases,
        "ambiguous_matches": ambiguous,
        "renumbered_matches": renumbered,
        "reordered_matches": reordered,
        "similarity_mean": (
            round(sum(scored) / len(scored), 6) if scored else None
        ),
        "similarity_mean_note": (
            "Mean TF-IDF cosine over matched clause pairs only. It measures "
            "how confidently clauses lined up, not how similar the two "
            "policies are, and it is not a legal-equivalence score."
        ),
        "unchanged_ratio": (
            round(counts["unchanged"] / total, 6) if total else None
        ),
    }
