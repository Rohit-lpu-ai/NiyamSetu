"""Text similarity for clause alignment, and the formatting-insensitive key.

Two separate jobs live here, and keeping them apart matters:

**The comparison key** (:func:`comparison_key`) answers *"is this the same
text?"* It exists because a policy clause that was not touched between two
issues of a circular still does not extract byte-identically: the 2016 PDF
breaks a line where the 2017 PDF does not, one uses a non-breaking hyphen and
the other an ASCII one, and ``pypdf`` inserts a stray space inside a word on a
kerned line. Comparing raw text would report dozens of "modified" clauses
where the policy did not change at all -- exactly the false positive the
brief forbids. The key normalises away everything that cannot carry legal
meaning, and nothing that can.

**The similarity score** (:func:`similarity_matrix`) answers *"are these two
clauses about the same thing?"*, for clauses whose wording genuinely differs.

Why TF-IDF cosine, implemented here, rather than scikit-learn
-------------------------------------------------------------
The teammate's prototype (``comparision.py``, preserved at the repository
root) used ``sklearn.feature_extraction.text.TfidfVectorizer(ngram_range=(1,2),
sublinear_tf=True, stop_words="english")`` followed by
``sklearn.metrics.pairwise.cosine_similarity``. That choice is sound for the
task, and this module reproduces **that exact configuration** -- sublinear
term frequency, word unigrams and bigrams, English stop words removed, L2
normalisation, smoothed IDF -- in the standard library.

It is reimplemented rather than imported for a measured reason, not an
aesthetic one: ``scikit-learn`` **is not installed in this project's
virtualenv**, so ``import comparision`` fails with ``ModuleNotFoundError`` on
this machine. Pinning it would add scikit-learn, scipy, joblib and
threadpoolctl -- of the order of 100 MB -- to a backend whose entire
dependency set is ten packages, for about seventy lines of arithmetic that has
to be explainable in a viva anyway (``CLAUDE.md`` §24 rule 5: no new
dependency without a written, agreed justification).
``test_comparison_unit.py`` asserts this implementation agrees with
scikit-learn's to within 1e-9 **when scikit-learn happens to be importable**,
and skips when it is not, so the equivalence claim is checked rather than
merely asserted.

The IDF formula is scikit-learn's ``smooth_idf=True`` variant::

    idf(t) = ln((1 + n) / (1 + df(t))) + 1

term frequency is ``1 + ln(tf)`` (``sublinear_tf=True``), and each document
vector is L2-normalised so the dot product *is* the cosine.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Sequence

__all__ = [
    "ENGLISH_STOP_WORDS",
    "comparison_key",
    "tokenize",
    "ngrams",
    "TfidfIndex",
    "cosine_similarity",
    "similarity_matrix",
    "jaccard",
]

#: scikit-learn's ``stop_words="english"`` list, verbatim. Reproduced rather
#: than approximated: a different stop-word list changes every score and would
#: silently break the equivalence test.
ENGLISH_STOP_WORDS = frozenset(
    """
a about above across after afterwards again against all almost alone along
already also although always am among amongst amoungst amount an and another
any anyhow anyone anything anyway anywhere are around as at back be became
because become becomes becoming been before beforehand behind being below
beside besides between beyond bill both bottom but by call can cannot cant co
con could couldnt cry de describe detail do done down due during each eg eight
either eleven else elsewhere empty enough etc even ever every everyone
everything everywhere except few fifteen fify fill find fire first five for
former formerly forty found four from front full further get give go had has
hasnt have he hence her here hereafter hereby herein hereupon hers herself him
himself his how however hundred i ie if in inc indeed interest into is it its
itself keep last latter latterly least less ltd made many may me meanwhile
might mill mine more moreover most mostly move much must my myself name namely
neither never nevertheless next nine no nobody none noone nor not nothing now
nowhere of off often on once one only onto or other others otherwise our ours
ourselves out over own part per perhaps please put rather re same see seem
seemed seeming seems serious several she should show side since sincere six
sixty so some somehow someone something sometime sometimes somewhere still
such system take ten than that the their them themselves then thence there
thereafter thereby therefore therein thereupon these they thick thin third
this those though three through throughout thru thus to together too top
toward towards twelve twenty two un under until up upon us very via was we
well were what whatever when whence whenever where whereafter whereas whereby
wherein whereupon wherever whether which while whither who whoever whole whom
whose why will with within without would yet you your yours yourself
yourselves
""".split()
)

#: scikit-learn's default ``token_pattern``: two or more word characters.
_TOKEN_PATTERN = re.compile(r"(?u)\b\w\w+\b")

#: Codepoints that differ between two extractions of the same printed line and
#: can never change what a clause requires. Folded to one ASCII form before
#: comparison. Written as escapes so the table survives any editor's encoding.
_PUNCTUATION_FOLDING = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ",
    "…": "...",
    "­": "",   # soft hyphen: invisible in print, present in the bytes
    "﻿": "",
    "​": "", "‌": "", "‍": "",
}

_COLLAPSE_SPACE = re.compile(r"\s+")

#: A leading clause marker: '3.', '3.2', '(a)', '(iv)', 'A.1'. Stripped by
#: ``comparison_key(..., drop_marker=True)`` so a clause whose *only* change is
#: its number compares as unchanged -- the brief's explicit requirement that
#: renumbering alone must never be reported as a modification.
_LEADING_MARKER = re.compile(
    r"^\s*(?:\(\s*(?:\d{1,3}|[ivxlcdm]{1,7}|[a-z])\s*\)"
    r"|(?:\d+(?:\.\d+){0,4}|[ivxlcdm]{1,7}|[a-z])\s*[.)\]]"
    r"|\d+(?:\.\d+){1,4}"
    r"|[A-Za-z]\.\d+(?:\.\d+)*)\s+"
)

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:)\]!?])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")

_FOLD_TABLE = str.maketrans(_PUNCTUATION_FOLDING)


def comparison_key(text: str, *, drop_marker: bool = False) -> str:
    """A form of ``text`` in which only meaning-bearing differences survive.

    The steps, each defensible on its own:

    1. **NFKC** -- the same ligature reaches us as ``fi`` from one extraction
       and as U+FB01 from another.
    2. **Punctuation folding** -- curly quotes, the seven dash codepoints, the
       six space codepoints and the soft hyphen collapse to one ASCII form. A
       typographic difference is not a policy change.
    3. **Case folding** -- a heading set in small caps in one issue and in
       title case in the next is the same heading.
    4. **Whitespace collapse** -- line breaks fall wherever the page does, so
       they carry no information about the clause.
    5. **Space-before-punctuation removal** -- ``pypdf`` emits ``word .`` on
       justified lines; ``word.`` is the same sentence.

    What is deliberately **not** done: digits are never touched, there is no
    stemming, no stop-word removal and nothing is reordered. ``"within 30
    days"`` and ``"within 60 days"`` must stay different, because that is
    precisely the kind of change the feature exists to find.

    ``drop_marker=True`` additionally removes a leading clause number, which
    is how "renumbered but otherwise identical" is detected.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_FOLD_TABLE)
    folded = _COLLAPSE_SPACE.sub(" ", folded).strip().casefold()
    folded = _SPACE_BEFORE_PUNCT.sub(r"\1", folded)
    folded = _SPACE_AFTER_OPEN.sub(r"\1", folded)
    if drop_marker:
        folded = _LEADING_MARKER.sub("", folded, count=1)
    return folded.strip()


def tokenize(text: str, *, drop_stop_words: bool = True) -> List[str]:
    """Lowercase word tokens, optionally without English stop words.

    Same contract as scikit-learn's default analyzer: the token pattern is
    applied to the lowercased string and stop words are removed *after*
    tokenisation -- which is what scikit-learn does, and what makes the bigram
    boundaries agree between the two implementations.
    """
    tokens = _TOKEN_PATTERN.findall(text.lower())
    if drop_stop_words:
        tokens = [token for token in tokens if token not in ENGLISH_STOP_WORDS]
    return tokens


def ngrams(tokens: Sequence[str], *, max_n: int = 2) -> List[str]:
    """Unigrams through ``max_n``-grams, joined by a single space.

    Bigrams are what make this usable on policy text: "scheduled castes" and
    "scheduled banks" share a token, and unigrams alone would call them
    similar. ``ngram_range=(1, 2)`` is the prototype's setting, kept.
    """
    out: List[str] = list(tokens)
    for n in range(2, max_n + 1):
        out.extend(" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1))
    return out


class TfidfIndex:
    """TF-IDF vectors for a fixed corpus, with cosine similarity between them.

    Built once over **both** versions' clauses together, which is the only
    correct way to do it: IDF must be computed on one shared vocabulary, or
    the two sides' vectors live in different spaces and their dot product
    means nothing.
    """

    def __init__(
        self,
        documents: Sequence[str],
        *,
        max_n: int = 2,
        drop_stop_words: bool = True,
        sublinear_tf: bool = True,
    ) -> None:
        self._max_n = max_n
        self._drop_stop_words = drop_stop_words
        self._sublinear_tf = sublinear_tf

        term_lists = [
            ngrams(tokenize(doc or "", drop_stop_words=drop_stop_words), max_n=max_n)
            for doc in documents
        ]
        document_frequency: Counter = Counter()
        for terms in term_lists:
            document_frequency.update(set(terms))

        total = len(term_lists)
        # scikit-learn's smooth_idf=True.
        self._idf: Dict[str, float] = {
            term: math.log((1 + total) / (1 + df)) + 1.0
            for term, df in document_frequency.items()
        }
        self._vectors: List[Dict[str, float]] = [
            self._vectorize(terms) for terms in term_lists
        ]

    def _vectorize(self, terms: Sequence[str]) -> Dict[str, float]:
        counts = Counter(terms)
        if not counts:
            return {}
        vector: Dict[str, float] = {}
        for term, count in counts.items():
            idf = self._idf.get(term)
            if idf is None:
                continue
            tf = 1.0 + math.log(count) if self._sublinear_tf else float(count)
            vector[term] = tf * idf
        norm = math.sqrt(sum(value * value for value in vector.values()))
        if norm == 0.0:
            return {}
        return {term: value / norm for term, value in vector.items()}

    def __len__(self) -> int:
        return len(self._vectors)

    def vector(self, index: int) -> Dict[str, float]:
        return self._vectors[index]

    def similarity(self, i: int, j: int) -> float:
        """Cosine similarity of two indexed documents, in ``[0, 1]``."""
        return dot(self._vectors[i], self._vectors[j])


def dot(left: Dict[str, float], right: Dict[str, float]) -> float:
    """Dot product of two L2-normalised sparse vectors, clamped to ``[0, 1]``.

    The shorter vector is iterated, so a 90 character clause against a 2,000
    character annexure costs the length of the clause and not of the annexure.
    Clamping absorbs the floating-point error that can push an identical pair
    a hair above 1.0.
    """
    if not left or not right:
        return 0.0
    if len(right) < len(left):
        left, right = right, left
    total = 0.0
    for term, value in left.items():
        other = right.get(term)
        if other is not None:
            total += value * other
    return min(1.0, max(0.0, total))


def cosine_similarity(left: str, right: str, *, max_n: int = 2) -> float:
    """Similarity of two strings on their own, as a two-document corpus.

    Convenience for tests and one-off checks. Alignment never uses it: IDF
    over two documents is not IDF over the corpus, so the scores would not be
    comparable across pairs.
    """
    index = TfidfIndex([left, right], max_n=max_n)
    return index.similarity(0, 1)


def similarity_matrix(
    old_texts: Sequence[str], new_texts: Sequence[str], *, max_n: int = 2
) -> List[List[float]]:
    """Dense ``len(old) x len(new)`` cosine matrix over one shared vocabulary.

    This is the prototype's ``similarity_matrix`` with the same semantics. It
    is dense rather than top-k because the caller needs the *runner-up* score
    for every row in order to report ambiguity, and a top-k would throw away
    exactly that.
    """
    index = TfidfIndex(list(old_texts) + list(new_texts), max_n=max_n)
    split = len(old_texts)
    return [
        [dot(index.vector(i), index.vector(split + j)) for j in range(len(new_texts))]
        for i in range(split)
    ]


def jaccard(left: str, right: str) -> float:
    """Token-set overlap. A second opinion for explanations, never a decision.

    Used to describe a match in prose ("the clauses share 80% of their
    vocabulary"), never to decide one -- TF-IDF cosine decides, so there is
    one ranking function and one threshold to defend.
    """
    a, b = set(tokenize(left)), set(tokenize(right))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
