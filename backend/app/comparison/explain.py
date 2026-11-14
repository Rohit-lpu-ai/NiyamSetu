"""What changed, in words a reader can act on, and as a word-level diff.

Two outputs, serving two different needs on the Compare screen:

:func:`word_diff`
    The mechanical answer: which runs of words were kept, removed, inserted or
    replaced. Built on :class:`difflib.SequenceMatcher` (standard library) over
    *tokens*, not characters -- a character diff of policy text produces
    unreadable fragments mid-word, while a word diff lines up with how the
    clause is read. This is what the UI highlights.

:func:`explain_change`
    The interpretive answer: a sentence saying what kind of change this is.
    Grown from the teammate's prototype ``explain_change``, which compared
    dates, time periods and amounts between the two clauses and fell back to
    a similarity-banded sentence. That structure is kept and extended with
    percentages, authorities, cross-references, and the word-count delta.

The hard rule this module obeys
-------------------------------
**It never asserts a legal effect.** ``CLAUDE.md`` §5 forbids the Compare view
from presenting a speculative change as a verified fact, so every sentence
here describes *what the text says* ("the period changed from 30 days to 60
days") and never what it means ("banks now have longer to comply"). The
difference matters: the first is checkable against the quoted text, the second
is an opinion the system is not qualified to hold. Every explanation of a
modified clause ends by pointing the reader at the source wording.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Sequence

from backend.app.comparison.entities import entity_texts
from backend.app.comparison.similarity import comparison_key, jaccard

__all__ = [
    "word_diff",
    "diff_stats",
    "explain_change",
    "explain_added",
    "explain_removed",
    "explain_unchanged",
    "describe",
]

#: Tokens are words *with* their trailing punctuation attached and whitespace
#: kept as its own token, so the diff can be re-joined into the original text
#: exactly. A diff that cannot reproduce its input is a diff that will quietly
#: drop a word from a displayed clause.
_TOKEN = re.compile(r"\s+|[^\s]+")

#: Labels whose change is worth a sentence of its own, and how to name them.
_WATCHED_LABELS = (
    ("DATE", "date"),
    ("PERIOD", "time period"),
    ("MONEY", "amount"),
    ("PERCENT", "percentage"),
    ("SECTION_REF", "cross-reference"),
    ("AUTHORITY", "named authority"),
)


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall(text or "")


def _is_space(token: str) -> bool:
    return not token.strip()


def word_diff(old_text: str, new_text: str) -> List[Dict[str, Any]]:
    """Aligned runs of words between two clause texts.

    Each run is ``{"op": "equal"|"delete"|"insert"|"replace", "old": str,
    "new": str}``. ``delete`` carries only ``old``, ``insert`` only ``new``,
    ``replace`` carries both, and ``equal`` carries the same text in both --
    which is what lets a UI render two columns from one list and keep the rows
    aligned.

    Matching is done on **case-folded tokens** so that a word whose only
    change is capitalisation is reported as equal, while the *displayed* text
    comes from the originals. Concatenating all ``old`` sides reproduces
    ``old_text`` exactly, and likewise for ``new`` -- asserted by a test,
    because a diff that loses a character would put text on screen that is not
    in the document.
    """
    old_tokens = _tokens(old_text)
    new_tokens = _tokens(new_text)
    matcher = difflib.SequenceMatcher(
        a=[token.casefold() for token in old_tokens],
        b=[token.casefold() for token in new_tokens],
        autojunk=False,
    )
    runs: List[Dict[str, Any]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        old_run = "".join(old_tokens[i1:i2])
        new_run = "".join(new_tokens[j1:j2])
        if op == "equal":
            # Show the new version's spelling where they differ only by case:
            # the newer document is the authoritative one for how it is now
            # printed.
            runs.append({"op": "equal", "old": old_run, "new": new_run})
        elif op == "delete":
            runs.append({"op": "delete", "old": old_run, "new": ""})
        elif op == "insert":
            runs.append({"op": "insert", "old": "", "new": new_run})
        else:
            runs.append({"op": "replace", "old": old_run, "new": new_run})
    return runs


def diff_stats(old_text: str, new_text: str) -> Dict[str, Any]:
    """Word counts added, removed and kept, plus the ratio difflib reports.

    Whitespace tokens are excluded from the counts: "3 words added" must not
    become "5" because two line breaks moved.
    """
    added = removed = kept = 0
    for run in word_diff(old_text, new_text):
        old_words = [t for t in _tokens(run["old"]) if not _is_space(t)]
        new_words = [t for t in _tokens(run["new"]) if not _is_space(t)]
        if run["op"] == "equal":
            kept += len(new_words)
        elif run["op"] == "delete":
            removed += len(old_words)
        elif run["op"] == "insert":
            added += len(new_words)
        else:
            removed += len(old_words)
            added += len(new_words)
    old_total = len([t for t in _tokens(old_text) if not _is_space(t)])
    new_total = len([t for t in _tokens(new_text) if not _is_space(t)])
    return {
        "words_added": added,
        "words_removed": removed,
        "words_kept": kept,
        "old_word_count": old_total,
        "new_word_count": new_total,
        "word_count_delta": new_total - old_total,
        "token_overlap": round(jaccard(old_text, new_text), 6),
    }


def _format_list(values: Sequence[str]) -> str:
    cleaned = [" ".join(value.split()) for value in values if value and value.strip()]
    if not cleaned:
        return "not stated"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f" and {cleaned[-1]}"


def entity_deltas(old_text: str, new_text: str) -> List[Dict[str, Any]]:
    """Per-label differences in the extracted mentions between two clauses.

    Structured rather than prose, so the API can expose it for a UI to render
    as a table and the prose sentence can be built from the same data. Only
    labels that actually differ appear.
    """
    deltas: List[Dict[str, Any]] = []
    for label, friendly in _WATCHED_LABELS:
        before = entity_texts(old_text, label)
        after = entity_texts(new_text, label)
        if before == after:
            continue
        before_set = {" ".join(v.split()).casefold() for v in before}
        after_set = {" ".join(v.split()).casefold() for v in after}
        if before_set == after_set:
            # Same mentions, different order or spacing. Not a change.
            continue
        deltas.append(
            {
                "label": label,
                "friendly_label": friendly,
                "old": before,
                "new": after,
                "added": [v for v in after if " ".join(v.split()).casefold() not in before_set],
                "removed": [v for v in before if " ".join(v.split()).casefold() not in after_set],
            }
        )
    return deltas


def explain_change(
    old_text: str,
    new_text: str,
    similarity: Optional[float] = None,
    *,
    renumbered: bool = False,
    reordered: bool = False,
    ambiguous: bool = False,
    high_confidence_similarity: float = 0.75,
) -> str:
    """One paragraph describing a matched pair. Descriptive, never legal.

    The order of the checks is the order of usefulness to a reader: an
    identical clause is said to be identical first; then the specific,
    checkable changes (a date, a period, an amount); then, only if none of
    those fired, the generic similarity statement the prototype used.
    """
    if comparison_key(old_text) == comparison_key(new_text):
        if renumbered:
            return (
                "The wording is identical. Only the printed clause number "
                "changed, so this is a renumbering and not a change of "
                "substance."
            )
        if reordered:
            return (
                "The wording is identical, but the clause appears at a "
                "different position in the document."
            )
        return "The extracted wording is identical in both versions."

    sentences: List[str] = []

    if comparison_key(old_text, drop_marker=True) == comparison_key(
        new_text, drop_marker=True
    ):
        return (
            "The clause text is identical once the printed clause number is "
            "removed. Only the numbering changed."
        )

    deltas = entity_deltas(old_text, new_text)
    for delta in deltas:
        if delta["label"] == "AUTHORITY":
            if delta["added"] and delta["removed"]:
                sentences.append(
                    f"the named authority changed from "
                    f"{_format_list(delta['removed'])} to "
                    f"{_format_list(delta['added'])}"
                )
            elif delta["added"]:
                sentences.append(
                    f"{_format_list(delta['added'])} is now named, and was not before"
                )
            else:
                sentences.append(
                    f"{_format_list(delta['removed'])} is no longer named"
                )
            continue
        sentences.append(
            f"the {delta['friendly_label']} changed from "
            f"{_format_list(delta['old'])} to {_format_list(delta['new'])}"
        )

    stats = diff_stats(old_text, new_text)
    obligations_before = entity_texts(old_text, "OBLIGATION")
    obligations_after = entity_texts(new_text, "OBLIGATION")
    if {o.casefold() for o in obligations_before} != {
        o.casefold() for o in obligations_after
    }:
        sentences.append(
            "the obligation wording changed "
            f"({_format_list(obligations_before)} to "
            f"{_format_list(obligations_after)})"
        )

    verify = (
        " Read the quoted text on both sides to confirm the practical and "
        "legal effect; this description reports what the words say, not what "
        "they mean."
    )

    if sentences:
        body = (
            "The two clauses cover the same material, but "
            + "; ".join(sentences)
            + f". {stats['words_added']} words were added and "
            f"{stats['words_removed']} removed."
        )
        return body + verify

    if stats["words_added"] == 0 and stats["words_removed"] == 0:
        return (
            "The words are the same; only spacing, punctuation or "
            "capitalisation differs. This is an extraction or typesetting "
            "difference rather than a policy change."
        )

    if similarity is not None and similarity >= high_confidence_similarity:
        return (
            "The clause is largely unchanged: "
            f"{stats['words_added']} words were added and "
            f"{stats['words_removed']} removed, with no change to any date, "
            "period, amount or percentage that the extractor recognises."
            + verify
        )

    return (
        "This clause appears to have been substantially rewritten while "
        "remaining related to the earlier clause "
        f"({stats['words_added']} words added, {stats['words_removed']} "
        "removed). "
        + (
            "The match is ambiguous -- another clause scored almost as well -- "
            "so treat the pairing itself as unconfirmed. "
            if ambiguous
            else ""
        )
        + "The precise effect needs human review."
    )


def explain_added(text: str) -> str:
    """Why an added clause is reported as added, and what to check."""
    cues = entity_texts(text, "OBLIGATION")
    dates = entity_texts(text, "DATE") + entity_texts(text, "PERIOD")
    extra = ""
    if cues:
        extra += (
            f" It uses obligation wording ({_format_list(cues)}), so it may "
            "impose a new requirement."
        )
    if dates:
            extra += f" It mentions {_format_list(dates)}."
    return (
        "This clause appears in the newer version, and no sufficiently "
        "similar clause was found in the older one." + extra +
        " Open the cited page to see who it applies to and what it requires."
    )


def explain_removed(text: str) -> str:
    """Why a removed clause is reported as removed, and the caveat."""
    return (
        "This clause appears in the older version, and no sufficiently "
        "similar clause was found in the newer one. That can mean it was "
        "removed, but it can also mean it was rewritten far enough to fall "
        "below the matching threshold, or moved into an annexure that was "
        "published separately. Verify against the complete notification "
        "before relying on it."
    )


def explain_unchanged(*, renumbered: bool = False, reordered: bool = False) -> str:
    """Why an unchanged clause is nonetheless worth showing.

    The renumbered wordings say "renumbering" explicitly rather than only
    "the clause number changed". A reader skimming a Compare screen needs to
    see at a glance that the row is *not* a substantive change, and the word
    for that is renumbering; a test asserts it is present.
    """
    if renumbered and reordered:
        return (
            "The wording is identical: this is a renumbering and a move, not "
            "a change of substance. Both the printed clause number and the "
            "position in the document changed."
        )
    if renumbered:
        return (
            "The wording is identical. Only the printed clause number "
            "changed, so this is a renumbering and not a change of substance."
        )
    if reordered:
        return (
            "The wording is identical, but the clause appears at a different "
            "position in the document."
        )
    return "The extracted wording is identical in both versions."


def describe(change: Any) -> str:
    """Explain any :class:`~.alignment.Change`, dispatching on its type.

    Typed as ``Any`` rather than importing ``Change`` so that this module
    stays importable on its own and ``alignment`` never acquires a cycle back
    through the explanation layer. ``alignment`` must not depend on how a
    change is worded.
    """
    if change.change_type == "added":
        return explain_added(change.new.text)
    if change.change_type == "removed":
        return explain_removed(change.old.text)
    if change.change_type == "unchanged":
        return explain_unchanged(
            renumbered=change.renumbered, reordered=change.reordered
        )
    return explain_change(
        change.old.text,
        change.new.text,
        change.similarity,
        renumbered=change.renumbered,
        reordered=change.reordered,
        ambiguous=change.ambiguous,
    )
