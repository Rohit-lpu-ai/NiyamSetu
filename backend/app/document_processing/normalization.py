"""Conservative text normalisation.

The rule for this whole project (``CLAUDE.md`` §10.2) is that normalisation is
**additive**: the raw extracted text is kept verbatim and the normalised text is
stored beside it, never instead of it. Nothing here is allowed to change what the
document *says*.

Every step is a separate named function with its own flag in
:class:`NormalizationOptions`, so an experiment can switch one step off and
measure its effect, and so each choice can be defended individually in a viva.

What we deliberately do NOT do
------------------------------
No lowercasing, no punctuation stripping, no stopword removal, no stemming or
lemmatisation, no reordering, no header/footer removal, no digit or date
rewriting. Those would destroy exactly the information this project depends on:
clause numbers like ``3.2``, defined terms, the literal wording of an
obligation, and printed dates. Header and footer removal needs to compare text
*across* pages, so it belongs to Phase 2 segmentation, not here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Dict

#: Characters that carry no meaning in extracted text but break string
#: comparison and search. Listed by codepoint rather than as literals, because
#: literal zero-width characters in source code are invisible and unmaintainable.
_INVISIBLE_CODEPOINTS = (
    0xFEFF,  # byte-order mark / zero-width no-break space
    0x200B,  # zero-width space
    0x200C,  # zero-width non-joiner
    0x200D,  # zero-width joiner
    0x00AD,  # soft hyphen
    0x2060,  # word joiner
)
_INVISIBLE_RE = re.compile(
    "[" + re.escape("".join(chr(cp) for cp in _INVISIBLE_CODEPOINTS)) + "]"
)

#: A hyphen at the end of a line, where a lowercase letter precedes it and the
#: next line starts with a lowercase letter. This is the signature of a word
#: split across a line break by the PDF's layout ("com-\npliance"). We require
#: lowercase on both sides so that genuine hyphenated compounds at a line end
#: ("Centre-\nState") and clause markers are left alone.
_LINE_BREAK_HYPHEN_RE = re.compile(r"([a-z])-[ \t]*\n[ \t]*([a-z])")

#: Any run of horizontal whitespace: ordinary spaces, tabs, the no-break space
#: and the typographic spaces PDF fonts emit for column gutters. Listed by
#: codepoint for the same reason as above - these characters are invisible.
#:
#: One-or-more rather than two-or-more, so that a lone tab also becomes a plain
#: space and the normalised text is uniformly space-separated. For a single
#: ordinary space the substitution is a no-op.
_HORIZONTAL_WHITESPACE_CODEPOINTS = (
    0x0009,  # tab
    0x0020,  # space
    0x00A0,  # no-break space
    *range(0x2000, 0x200B),  # en quad through hair space
    0x202F,  # narrow no-break space
    0x205F,  # medium mathematical space
    0x3000,  # ideographic space
)
_HORIZONTAL_WHITESPACE_RUN_RE = re.compile(
    "["
    + re.escape("".join(chr(cp) for cp in _HORIZONTAL_WHITESPACE_CODEPOINTS))
    + "]+"
)

#: Three or more consecutive newlines, i.e. two or more blank lines.
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class NormalizationOptions:
    """Switches for the individual normalisation steps.

    The defaults are the pipeline's documented behaviour. Turning a flag off is
    how Experiment A (``CLAUDE.md`` §15) isolates the effect of one step.
    """

    unicode_nfkc: bool = True
    unify_line_endings: bool = True
    strip_invisible_characters: bool = True
    dehyphenate_line_breaks: bool = True
    collapse_horizontal_whitespace: bool = True
    collapse_blank_lines: bool = True
    strip_trailing_line_whitespace: bool = True

    def as_dict(self) -> Dict[str, bool]:
        """Return the options as a plain dict, for recording in the output JSON."""
        return asdict(self)


def apply_unicode_nfkc(text: str) -> str:
    """Apply Unicode NFKC normalisation.

    This is what repairs typographic ligatures (``ﬁ`` -> ``fi``) and full-width
    or presentation-form characters that PDF fonts emit. NFKC is *compatibility*
    normalisation, so it is slightly lossy in appearance but not in meaning.
    """
    return unicodedata.normalize("NFKC", text)


def unify_line_endings(text: str) -> str:
    """Convert Windows (``\r\n``) and classic Mac (``\r``) breaks to ``\n``."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_invisible_characters(text: str) -> str:
    """Remove zero-width and soft-hyphen characters (see ``_INVISIBLE_CHARS``)."""
    return _INVISIBLE_RE.sub("", text)


def dehyphenate_line_breaks(text: str) -> str:
    """Rejoin a lowercase word split by a hyphen at a line break."""
    return _LINE_BREAK_HYPHEN_RE.sub(r"\1\2", text)


def collapse_horizontal_whitespace(text: str) -> str:
    """Normalise every run of horizontal whitespace to a single plain space."""
    return _HORIZONTAL_WHITESPACE_RUN_RE.sub(" ", text)


def strip_trailing_line_whitespace(text: str) -> str:
    """Remove trailing spaces and tabs from the end of every line."""
    return "\n".join(line.rstrip(" \t") for line in text.split("\n"))


def collapse_blank_lines(text: str) -> str:
    """Reduce three or more consecutive newlines to exactly two."""
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", text)


def normalize_text(text: str, options: NormalizationOptions | None = None) -> str:
    """Run the enabled normalisation steps, in a fixed order.

    The order matters: Unicode normalisation first (so later regexes see
    canonical characters), line endings before anything that matches ``\n``,
    and whitespace collapsing last so it cleans up after de-hyphenation.
    """
    options = options or NormalizationOptions()
    result = text
    if options.unicode_nfkc:
        result = apply_unicode_nfkc(result)
    if options.unify_line_endings:
        result = unify_line_endings(result)
    if options.strip_invisible_characters:
        result = strip_invisible_characters(result)
    if options.dehyphenate_line_breaks:
        result = dehyphenate_line_breaks(result)
    if options.collapse_horizontal_whitespace:
        result = collapse_horizontal_whitespace(result)
    if options.strip_trailing_line_whitespace:
        result = strip_trailing_line_whitespace(result)
    if options.collapse_blank_lines:
        result = collapse_blank_lines(result)
    return result
