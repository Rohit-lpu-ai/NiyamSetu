"""Comparison orchestration: validate, align, resolve evidence, filter.

The seam between the API and the database, for the same reason
``library_search/service.py`` is: routes must not decide what a valid version
pair is, and the repository must not decide what a change record looks like.

What it adds over calling :func:`~.alignment.align` directly
------------------------------------------------------------
* **Pair validation** -- both versions exist, both extracted cleanly, they are
  not the same version, and they belong to the same document unless the caller
  explicitly asked otherwise.
* **Direction.** A diff is only meaningful if "old" and "new" are the right way
  round. The service determines the direction from evidence in this order:
  a declared ``supersedes_version_id``, then publication date, then ingestion
  order -- and when nothing establishes it, it says so in a warning rather
  than picking silently.
* **Evidence resolution.** Every side of every change is verified against the
  page text it claims to come from, and the result is reported per change. A
  citation that cannot be verified is marked unresolved with a reason; it is
  never silently dropped and never shown as if it were sound.
* **Filtering and pagination** over the aligned result, so the UI's change
  filters are one well-defined implementation rather than four.
* **A deterministic ``comparison_id``** that makes a result referenceable
  without a table to store it in.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.app.comparison import explain as explain_module
from backend.app.comparison import repository
from backend.app.comparison.alignment import (
    CHANGE_TYPES,
    CONFIDENCE_LEVELS,
    AlignmentConfig,
    Change,
    align,
    summarize,
)
from backend.app.comparison.entities import extract_entities
from backend.app.db.engine import connection

__all__ = [
    "InvalidComparison",
    "ComparisonNotFound",
    "METHODS",
    "DEFAULT_METHOD",
    "MAX_LIMIT",
    "DEFAULT_LIMIT",
    "ComparisonService",
    "comparison_id_for",
]


class InvalidComparison(ValueError):
    """The requested comparison cannot be run. Carries a stable code.

    Distinct from "the comparison found nothing": asking to compare a version
    with itself is a client mistake and gets a 422, while two versions that
    happen to be identical is a successful comparison whose every change is
    ``unchanged``. Collapsing the two would make a real answer look like a bug.
    """

    code = "VALIDATION_ERROR"


class ComparisonNotFound(LookupError):
    """A named document or version does not exist."""

    code = "VERSION_NOT_FOUND"


METHODS = ("clause_aware", "paragraph")
DEFAULT_METHOD = "clause_aware"

#: Same cap as the other read layers: a caller asking for more gets this
#: rather than an error, so a paginating client cannot request a whole corpus.
MAX_LIMIT = 500
DEFAULT_LIMIT = 100

#: How the slices of a clause that spans several pages are rejoined into one
#: quote. A space, because that is what segmentation puts between the lines it
#: joins: a clause broken across a page boundary ends one page mid-sentence and
#: resumes on the next, and the two halves are separate words. Concatenating
#: them directly produced "...received andthe name of...", which is not a
#: verbatim substring of the clause and therefore not a citation at all. The
#: same join is used to *verify* the quote, so a mismatch downgrades the
#: evidence to `partial` rather than shipping a wrong highlight.
SPAN_JOIN = " "


def comparison_id_for(
    base_fingerprint: str,
    compare_fingerprint: str,
    method: str,
    config: AlignmentConfig,
) -> str:
    """A stable identifier for one comparison, derived from its inputs.

    This is how ``GET /api/compare/{comparison_id}`` is answered without a
    table: the id is a hash of the two version *fingerprints* (which change if
    the underlying document is re-ingested differently), the segmentation
    method and every threshold. Two runs with the same inputs produce the same
    id, and any change to the inputs produces a different one -- which is the
    useful half of persistence (a result can be cited and re-derived) without
    the half that goes stale (a stored row that no longer matches the data).

    The fingerprints, not the public ids: ``VER-0003`` can be re-ingested from
    a corrected PDF, and a comparison id that did not change would then point
    at a result nobody can reproduce.
    """
    payload = json.dumps(
        {
            "base": base_fingerprint,
            "compare": compare_fingerprint,
            "method": method,
            "config": config.to_dict(),
            "algorithm": "niyamsetu-compare-1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "CMP-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _resolve_evidence(
    clause: Any,
    version: repository.VersionInfo,
    pages: Dict[int, str],
) -> Dict[str, Any]:
    """Build and *check* the citation for one side of one change.

    This is the module's most important function, and it is written to fail
    loudly. ``CLAUDE.md`` §5 forbids showing a citation whose text does not
    support the statement, and the brief repeats it: "If evidence cannot be
    resolved, expose the problem instead of displaying a misleading citation."

    So every span is verified, not assumed. For each page the clause touches,
    the stored ``char_start``/``char_end`` are sliced out of that page's text
    on the version's own ``offset_basis``, and the concatenation is compared
    against the clause's stored text under the formatting-insensitive
    comparison key. The outcome is one of:

    ``resolved``
        every span sliced cleanly and the result matches the clause text.
    ``partial``
        the spans sliced, but the reconstruction differs from the stored
        text. The spans are still returned, with the reason -- a highlight
        that is slightly off is useful, a highlight presented as exact when it
        is not is not.
    ``unresolved``
        a page is missing, a span is out of range, or there are no
        ``segment_pages`` rows at all. No quote is offered.

    Page numbers are never invented. If ``segment_pages`` is empty the
    evidence is unresolved even though ``page_start``/``page_end`` are
    present, because those two columns answer "where is this clause" and the
    quote needs "where exactly".
    """
    spans: List[Dict[str, Any]] = []
    problems: List[str] = []
    collected: List[str] = []

    if not clause.pages:
        problems.append(
            "the clause has no segment_pages rows, so no character range on "
            "any page can be quoted"
        )

    for page in clause.pages:
        number = int(page["page_number"])
        start, end = int(page["char_start"]), int(page["char_end"])
        body = pages.get(number)
        if body is None:
            problems.append(f"page {number} has no stored text on {version.offset_basis}")
            spans.append(
                {
                    "page_number": number,
                    "char_start": start,
                    "char_end": end,
                    "quote": None,
                    "resolved": False,
                }
            )
            continue
        if start < 0 or end > len(body) or end < start:
            problems.append(
                f"offsets {start}:{end} fall outside page {number}, "
                f"which holds {len(body)} characters"
            )
            spans.append(
                {
                    "page_number": number,
                    "char_start": start,
                    "char_end": end,
                    "quote": None,
                    "resolved": False,
                }
            )
            continue
        sliced = body[start:end]
        collected.append(sliced)
        spans.append(
            {
                "page_number": number,
                "char_start": start,
                "char_end": end,
                "quote": sliced,
                "resolved": True,
            }
        )

    from backend.app.comparison.similarity import comparison_key

    # A clause spanning two pages is stored as one span per page, and the
    # clause's own text joins the pages with a separator -- so the quote must
    # be assembled the same way. Concatenating the slices directly produced
    # "...received andthe name of...", which is not a verbatim substring of
    # anything and therefore not a citation. Verification below uses the same
    # join, so a quote is offered only when the joined form matches.
    quote = SPAN_JOIN.join(collected)

    status = "resolved"
    if problems:
        status = "unresolved"
    elif collected:
        if comparison_key(quote) != comparison_key(clause.text):
            status = "partial"
            problems.append(
                "the text sliced from the stored offsets does not match the "
                "stored clause text; the highlight may be approximate"
            )

    return {
        "document_id": version.document_public_id,
        "version_id": version.version_public_id,
        "clause_id": clause.clause_id,
        "page_start": clause.page_start,
        "page_end": clause.page_end,
        "offset_basis": version.offset_basis,
        "spans": spans,
        "status": status,
        "resolved": status == "resolved",
        "problems": problems,
        # The §12.2 citation shape expects `quote` to be a verbatim substring
        # of the clause text. It is only offered when the slice verified.
        "quote": (quote if status == "resolved" and collected else None),
    }


class ComparisonService:
    """Compare two stored versions. Holds a URL so tests can point elsewhere.

    The same shape as ``api.dependencies.Repository`` and
    ``library_search.service.SearchService``: an object the routes depend on
    and a test can replace wholesale, rather than module functions the routes
    call directly.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = url

    @property
    def url(self) -> Optional[str]:
        return self._url

    # -- options ------------------------------------------------------------
    def options(self, *, include_single_version: bool = False) -> Dict[str, Any]:
        """Which comparisons the stored corpus can actually support.

        Returns the documents, their ok versions and every older/newer pair,
        plus a corpus summary so an empty result can explain itself. The
        frontend renders the empty state from ``corpus``, which is why that
        key is present even when ``documents`` is not empty.
        """
        documents = repository.comparable_documents(
            include_single_version=include_single_version, url=self._url
        )
        corpus = repository.corpus_version_count(url=self._url)
        pair_count = sum(len(entry["pairs"]) for entry in documents)
        declared = sum(
            1
            for entry in documents
            for pair in entry["pairs"]
            if pair["supersedes_declared"]
        )
        return {
            "documents": documents,
            "corpus": corpus,
            "comparable_pairs": pair_count,
            "declared_supersedes_pairs": declared,
            "methods": list(METHODS),
            "default_method": DEFAULT_METHOD,
            "defaults": AlignmentConfig().to_dict(),
            "note": (
                "A version is offered only when extraction_status = 'ok'. A "
                "partial extraction can lose a clause, and a lost clause "
                "would be reported as removed -- an extraction defect shown "
                "to the user as a policy change."
            ),
        }

    # -- the comparison -----------------------------------------------------
    def compare(
        self,
        base_version_id: Optional[str],
        compare_version_id: Optional[str],
        *,
        document_id: Optional[str] = None,
        method: str = DEFAULT_METHOD,
        config: Optional[AlignmentConfig] = None,
        allow_cross_document: bool = False,
        change_types: Optional[Sequence[str]] = None,
        confidence: Optional[Sequence[str]] = None,
        min_similarity: Optional[float] = None,
        max_similarity: Optional[float] = None,
        only_ambiguous: bool = False,
        include_text: bool = True,
        include_diff: bool = True,
        include_entities: bool = False,
        include_explanation: bool = True,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Run one comparison and return the §12-shaped payload.

        ``base_version_id`` is the older side and ``compare_version_id`` the
        newer one *as the caller names them*; if the evidence says they are
        the wrong way round they are swapped and a warning records it. Getting
        this wrong silently would invert every added and removed label in the
        result, which is the kind of defect a demo does not survive.
        """
        if not base_version_id or not compare_version_id:
            raise InvalidComparison(
                "both base_version_id and compare_version_id are required. "
                "Use GET /api/compare/options to list the pairs this corpus "
                "supports."
            )
        if base_version_id == compare_version_id:
            raise InvalidComparison(
                f"base_version_id and compare_version_id are both "
                f"{base_version_id}; there is nothing to compare."
            )
        if method not in METHODS:
            raise InvalidComparison(
                f"method={method!r} is not supported. "
                f"Available: {', '.join(METHODS)}."
            )
        cfg = config or AlignmentConfig()
        _validate_config(cfg)
        selected_types = _validate_subset("change_types", change_types, CHANGE_TYPES)
        selected_confidence = _validate_subset(
            "confidence", confidence, CONFIDENCE_LEVELS
        )
        if min_similarity is not None and not 0.0 <= min_similarity <= 1.0:
            raise InvalidComparison("min_similarity must be between 0 and 1.")
        if max_similarity is not None and not 0.0 <= max_similarity <= 1.0:
            raise InvalidComparison("max_similarity must be between 0 and 1.")
        if (
            min_similarity is not None
            and max_similarity is not None
            and min_similarity > max_similarity
        ):
            raise InvalidComparison(
                "min_similarity must not be greater than max_similarity."
            )

        warnings: List[str] = []
        started = time.perf_counter()

        with connection(self._url) as conn:
            try:
                base = repository.load_version(base_version_id, conn=conn)
                target = repository.load_version(compare_version_id, conn=conn)
            except repository.NotFound as exc:
                raise ComparisonNotFound(str(exc)) from None

            if document_id and document_id not in (
                base.document_public_id,
                target.document_public_id,
            ):
                raise InvalidComparison(
                    f"document_id={document_id!r} matches neither version: "
                    f"{base.version_public_id} belongs to "
                    f"{base.document_public_id} and {target.version_public_id} "
                    f"to {target.document_public_id}."
                )

            same_document = base.document_id == target.document_id
            if not same_document and not allow_cross_document:
                raise InvalidComparison(
                    f"{base.version_public_id} belongs to document "
                    f"{base.document_public_id} and {target.version_public_id} "
                    f"to {target.document_public_id}. Versions of the same "
                    f"document are compared by default; pass "
                    f"allow_cross_document=true to compare two documents that "
                    f"have not been declared versions of one another, and "
                    f"read the warning the result carries."
                )

            for version in (base, target):
                if version.extraction_status != "ok":
                    raise InvalidComparison(
                        f"{version.version_public_id} has extraction_status "
                        f"{version.extraction_status!r}. Only 'ok' versions "
                        f"can be compared: an incomplete extraction loses "
                        f"clauses, and a lost clause would be reported as "
                        f"removed."
                    )

            if not same_document:
                warnings.append(
                    "CROSS_DOCUMENT: these two versions are not declared "
                    "versions of the same document "
                    f"({base.document_public_id} and "
                    f"{target.document_public_id}). Nobody has asserted that "
                    "they are successive issues of one policy (D-9), so read "
                    "every result as a text comparison and not as an "
                    "authority-confirmed amendment."
                )

            base, target, direction_note = _order_versions(base, target)
            if direction_note:
                warnings.append(direction_note)

            old_clauses = repository.load_clauses(
                base.version_id, method=method, conn=conn
            )
            new_clauses = repository.load_clauses(
                target.version_id, method=method, conn=conn
            )
            if not old_clauses and not new_clauses:
                warnings.append(
                    f"NO_SEGMENTS: neither version has segments for "
                    f"method={method!r}. Both were ingested, so this is a "
                    f"segmentation gap, not an empty document."
                )
            elif not old_clauses:
                warnings.append(
                    f"NO_SEGMENTS: {base.version_public_id} has no "
                    f"method={method!r} segments, so every clause of the "
                    f"newer version is reported as added."
                )
            elif not new_clauses:
                warnings.append(
                    f"NO_SEGMENTS: {target.version_public_id} has no "
                    f"method={method!r} segments, so every clause of the "
                    f"older version is reported as removed."
                )

            old_pages = repository.page_texts(
                base.version_id, basis=base.offset_basis, conn=conn
            )
            new_pages = repository.page_texts(
                target.version_id, basis=target.offset_basis, conn=conn
            )

        changes = align(old_clauses, new_clauses, cfg)
        align_ms = (time.perf_counter() - started) * 1000.0

        summary = summarize(changes)
        filtered = [
            change
            for change in changes
            if _passes(
                change,
                selected_types,
                selected_confidence,
                min_similarity,
                max_similarity,
                only_ambiguous,
            )
        ]
        capped = _clamp(limit)
        start = max(0, int(offset))
        window = filtered[start : start + capped]

        items = [
            self._render(
                change,
                base,
                target,
                old_pages,
                new_pages,
                include_text=include_text,
                include_diff=include_diff,
                include_entities=include_entities,
                include_explanation=include_explanation,
            )
            for change in window
        ]

        evidence_problems = sum(
            1
            for item in items
            for side in ("old", "new")
            if item[side] is not None
            and item[side]["evidence"]["status"] != "resolved"
        )
        if evidence_problems:
            warnings.append(
                f"EVIDENCE: {evidence_problems} of the returned sides could "
                f"not be verified against their stored page offsets. Each "
                f"says why in its own evidence.problems."
            )

        return {
            "comparison_id": comparison_id_for(
                base.fingerprint, target.fingerprint, method, cfg
            ),
            "base_version": base.to_dict(),
            "compare_version": target.to_dict(),
            "same_document": same_document,
            "segmentation_method": method,
            "config": cfg.to_dict(),
            "summary": summary,
            "clause_counts": {
                "base": len(old_clauses),
                "compare": len(new_clauses),
            },
            "total": len(filtered),
            "total_unfiltered": len(changes),
            "limit": capped,
            "offset": start,
            "filters": {
                "change_types": list(selected_types) if selected_types else None,
                "confidence": list(selected_confidence) if selected_confidence else None,
                "min_similarity": min_similarity,
                "max_similarity": max_similarity,
                "only_ambiguous": only_ambiguous,
            },
            "warnings": warnings,
            "took_ms": round(align_ms, 3),
            "items": items,
        }

    # -- entity extraction, exposed on its own ------------------------------
    def entities(
        self,
        version_id: str,
        *,
        method: str = DEFAULT_METHOD,
        clause_id: Optional[str] = None,
        labels: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """The rule-based extractor's output over one version's clauses.

        Kept on its own endpoint because the brief requires entity extraction
        to stay separable from comparison. It reads the same clauses through
        the same repository, so a mention's offsets are into the same clause
        text the comparison quotes.
        """
        if method not in METHODS:
            raise InvalidComparison(
                f"method={method!r} is not supported. "
                f"Available: {', '.join(METHODS)}."
            )
        with connection(self._url) as conn:
            try:
                version = repository.load_version(version_id, conn=conn)
            except repository.NotFound as exc:
                raise ComparisonNotFound(str(exc)) from None
            clauses = repository.load_clauses(
                version.version_id, method=method, conn=conn
            )
        if clause_id:
            clauses = [clause for clause in clauses if clause.clause_id == clause_id]
            if not clauses:
                raise ComparisonNotFound(
                    f"no clause {clause_id} in {version.version_public_id} "
                    f"under method={method!r}"
                )

        capped = _clamp(limit)
        start = max(0, int(offset))
        window = clauses[start : start + capped]

        started = time.perf_counter()
        items = []
        for clause in window:
            try:
                found = extract_entities(clause.text, labels=labels)
            except ValueError as exc:
                raise InvalidComparison(str(exc)) from None
            items.append(
                {
                    "clause_id": clause.clause_id,
                    "ordinal": clause.ordinal,
                    "clause_number": clause.clause_number,
                    "page_start": clause.page_start,
                    "page_end": clause.page_end,
                    "entities": [entity.to_dict() for entity in found],
                }
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        counts: Dict[str, int] = {}
        for item in items:
            for entity in item["entities"]:
                counts[entity["label"]] = counts.get(entity["label"], 0) + 1

        return {
            "version_id": version.version_public_id,
            "document_id": version.document_public_id,
            "segmentation_method": method,
            "condition": "regex",
            "total": len(clauses),
            "limit": capped,
            "offset": start,
            "mentions_by_label": counts,
            "took_ms": round(elapsed_ms, 3),
            "note": (
                "Rule-based baseline. OBLIGATION matches modal language, not "
                "verified obligations, and AUTHORITY matches a closed list of "
                "bodies observed in this corpus. See "
                "backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md for the "
                "measured precision and recall and for what was not measured."
            ),
            "items": items,
        }

    # -- rendering ----------------------------------------------------------
    def _render(
        self,
        change: Change,
        base: repository.VersionInfo,
        target: repository.VersionInfo,
        old_pages: Dict[int, str],
        new_pages: Dict[int, str],
        *,
        include_text: bool,
        include_diff: bool,
        include_entities: bool,
        include_explanation: bool,
    ) -> Dict[str, Any]:
        """One change record, in the shape the API and the UI consume."""

        def side(
            clause: Any, version: repository.VersionInfo, pages: Dict[int, str]
        ) -> Optional[Dict[str, Any]]:
            if clause is None:
                return None
            payload = clause.to_dict()
            if not include_text:
                payload.pop("text", None)
            payload["evidence"] = _resolve_evidence(clause, version, pages)
            if include_entities:
                payload["entities"] = [
                    entity.to_dict() for entity in extract_entities(clause.text)
                ]
            return payload

        record: Dict[str, Any] = {
            "change_id": f"{change.change_type.upper()[:3]}-"
            + (change.new.clause_id if change.new else change.old.clause_id),
            "change_type": change.change_type,
            "similarity": change.similarity,
            "runner_up_similarity": change.runner_up_similarity,
            "runner_up_clause_id": change.runner_up_clause_id,
            "match_basis": change.match_basis,
            "confidence": change.confidence,
            "ambiguous": change.ambiguous,
            "renumbered": change.renumbered,
            "reordered": change.reordered,
            "notes": list(change.notes),
            "old": side(change.old, base, old_pages),
            "new": side(change.new, target, new_pages),
        }
        if include_explanation:
            record["explanation"] = explain_module.describe(change)
        if include_diff and change.old is not None and change.new is not None:
            record["word_diff"] = explain_module.word_diff(
                change.old.text, change.new.text
            )
            record["diff_stats"] = explain_module.diff_stats(
                change.old.text, change.new.text
            )
            record["entity_deltas"] = explain_module.entity_deltas(
                change.old.text, change.new.text
            )
        return record


def _order_versions(
    first: repository.VersionInfo, second: repository.VersionInfo
) -> Tuple[repository.VersionInfo, repository.VersionInfo, Optional[str]]:
    """Return ``(older, newer, warning)``, using the strongest evidence first.

    1. **A declared supersedes link.** ``document_versions.supersedes_version_id``
       is asserted by a human at ingestion (D-9) and is the only statement in
       the database about version order that is not an inference.
    2. **Publication date.** A hint, not proof -- ``CLAUDE.md`` §11.1 says so
       explicitly, and the column is nullable.
    3. **Ingestion order**, as the last resort, with a warning: the order two
       files were loaded in says nothing about which policy came first.

    The caller's own labelling is honoured when nothing contradicts it, which
    is why a swap always produces a warning. Silently reversing a diff would
    invert every added and removed label without telling anyone.
    """
    if second.supersedes_version_id == first.version_id:
        return (first, second, None)
    if first.supersedes_version_id == second.version_id:
        return (
            second,
            first,
            f"DIRECTION_SWAPPED: {first.version_public_id} declares that it "
            f"supersedes {second.version_public_id}, so it is the newer "
            f"version. The sides were swapped to match.",
        )

    if first.publication_date and second.publication_date:
        if second.publication_date < first.publication_date:
            return (
                second,
                first,
                f"DIRECTION_SWAPPED: {second.version_public_id} was published "
                f"on {second.publication_date.isoformat()}, before "
                f"{first.version_public_id} on "
                f"{first.publication_date.isoformat()}. The sides were "
                f"swapped so the diff reads older to newer.",
            )
        if second.publication_date == first.publication_date:
            return (
                first,
                second,
                f"DIRECTION_UNVERIFIED: both versions carry the publication "
                f"date {first.publication_date.isoformat()} and neither "
                f"declares that it supersedes the other, so the direction is "
                f"the one you asked for and is not confirmed by the data.",
            )
        return (first, second, None)

    return (
        first,
        second,
        "DIRECTION_UNVERIFIED: at least one version has no publication date "
        "and neither declares that it supersedes the other, so 'older' and "
        "'newer' are the ones you asked for. Record a publication date or a "
        "supersedes link at ingestion (D-9) to make the order a fact rather "
        "than an assumption.",
    )


def _validate_config(cfg: AlignmentConfig) -> None:
    for name in (
        "match_threshold",
        "number_match_threshold",
        "ambiguity_margin",
        "high_confidence_similarity",
    ):
        value = getattr(cfg, name)
        if not 0.0 <= float(value) <= 1.0:
            raise InvalidComparison(f"{name} must be between 0 and 1, got {value!r}.")
    if cfg.number_match_threshold > cfg.match_threshold:
        raise InvalidComparison(
            "number_match_threshold must not exceed match_threshold: an equal "
            "printed clause number is extra evidence, so it should lower the "
            "bar for a match, never raise it."
        )


def _validate_subset(
    name: str, values: Optional[Sequence[str]], allowed: Sequence[str]
) -> Tuple[str, ...]:
    if values is None:
        return ()
    cleaned = tuple(dict.fromkeys(value for value in values if value))
    unknown = [value for value in cleaned if value not in allowed]
    if unknown:
        raise InvalidComparison(
            f"{name}={unknown!r} is not supported. "
            f"Available: {', '.join(allowed)}."
        )
    return cleaned


def _passes(
    change: Change,
    types: Sequence[str],
    confidence: Sequence[str],
    min_similarity: Optional[float],
    max_similarity: Optional[float],
    only_ambiguous: bool,
) -> bool:
    """Filter one change. Unscored changes survive a similarity filter.

    An ``added`` clause has no similarity, and dropping it because
    ``min_similarity=0.5`` was asked for would silently hide every addition
    from a filtered view. A similarity range filters the *scored* changes and
    leaves the others alone; the ``change_types`` filter is how a caller
    excludes additions.
    """
    if types and change.change_type not in types:
        return False
    if confidence and change.confidence not in confidence:
        return False
    if only_ambiguous and not change.ambiguous:
        return False
    if change.similarity is not None:
        if min_similarity is not None and change.similarity < min_similarity:
            return False
        if max_similarity is not None and change.similarity > max_similarity:
            return False
    return True


def _clamp(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))
