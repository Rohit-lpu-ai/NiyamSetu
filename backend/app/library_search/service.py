"""Search orchestration: validate, choose a strategy, time it, explain it.

The API layer must never choose a retrieval strategy, and the repository must
never decide what counts as a valid query. This module is the seam between
them, and it exists for the same reason
``document_processing.extractor_backends`` exists for Experiment A: a
comparison is only honest if both conditions run through the identical call
path, with the same filters, the same pagination and the same response shape.

What it adds over the raw repository calls
------------------------------------------
* **Query validation in one place.** Blank, whitespace-only and over-long
  queries are rejected here, so the four endpoints cannot disagree about what
  an empty search means.
* **Honest reporting of a query that parsed to nothing.** ``"the of and"``
  and ``"!!!"`` are *valid* requests that match nothing because PostgreSQL's
  English configuration strips them entirely. That is a different answer from
  "no document contains this", and the warning says which happened.
* **Strategy attribution.** Every response names the strategy that produced
  it. A screenshot or a logged result can always be traced to a condition,
  which is a requirement of the evaluation rather than a nicety.
* **Timing.** ``took_ms`` is measured around the database call. It is what
  makes "is the index being used?" visible in ordinary use.

Strategies
----------
======================  ==========================================
``fts``                 PostgreSQL full-text over the existing
                        ``segments.text_search`` GIN index. **The baseline,
                        and the default.**
``ilike``               Substring match. **The measured comparison
                        condition**, never a fallback: nothing selects it
                        automatically, and the response says when it was used.
======================  ==========================================

``semantic`` and ``hybrid`` are *not* implemented and are not listed as
available. Both need an embedding model, which needs D-7 answered and a pinned
dependency with a written justification (``CLAUDE.md`` §24 rule 5). The seam
is here for them; the claim that they work is not.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from backend.app.library_search import repository

__all__ = [
    "InvalidQuery",
    "MAX_QUERY_LENGTH",
    "STRATEGIES",
    "DEFAULT_STRATEGY",
    "PLANNED_STRATEGIES",
    "SORTS",
    "DEFAULT_SORT",
    "METHODS",
    "VERSION_SCOPES",
    "SearchService",
    "normalize_query",
]


class InvalidQuery(ValueError):
    """The request cannot be searched at all. Carries a stable code.

    Distinct from "found nothing": a blank query is a client mistake and gets
    a 422, while a well-formed query with no matches is a successful search
    with an empty result list. Collapsing the two is how an empty state comes
    to look like a bug.
    """

    code = "VALIDATION_ERROR"


#: Long enough for a pasted clause, short enough that a pathological query
#: cannot be used to make PostgreSQL do unbounded parsing work.
MAX_QUERY_LENGTH = 300

#: Implemented and selectable.
STRATEGIES = ("fts", "ilike")
DEFAULT_STRATEGY = "fts"

#: Named so the roadmap is visible in the code, and explicitly not offered.
PLANNED_STRATEGIES = ("semantic", "hybrid")

SORTS = ("relevance", "date_desc", "date_asc", "title")
DEFAULT_SORT = "relevance"

METHODS = ("clause_aware", "paragraph")
VERSION_SCOPES = ("latest", "all")


def normalize_query(query: Optional[str]) -> str:
    """Trim and validate a user query, or raise :class:`InvalidQuery`.

    Collapsing internal whitespace matters for more than tidiness: the ILIKE
    condition matches a literal string, so ``"capital  adequacy"`` with two
    spaces would find nothing while the full-text condition found plenty —
    and the comparison between them would be measuring the wrong thing.
    """
    if query is None:
        raise InvalidQuery("A search query is required.")
    cleaned = " ".join(query.split())
    if not cleaned:
        raise InvalidQuery(
            "A search query is required. Use GET /api/documents to list the "
            "corpus without searching it."
        )
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise InvalidQuery(
            f"Query is too long: {len(cleaned)} characters, "
            f"maximum {MAX_QUERY_LENGTH}."
        )
    return cleaned


def _validate_choice(name: str, value: str, allowed: tuple) -> str:
    if value not in allowed:
        if name == "strategy" and value in PLANNED_STRATEGIES:
            raise InvalidQuery(
                f"strategy={value!r} is planned but not implemented "
                f"(needs D-7). Available: {', '.join(allowed)}."
            )
        raise InvalidQuery(
            f"{name}={value!r} is not supported. Available: {', '.join(allowed)}."
        )
    return value


class SearchService:
    """Search over one database. Holds a URL so tests can point elsewhere.

    The same shape as ``api.dependencies.Repository``: an object the routes
    depend on and a test can replace wholesale, rather than module functions
    the routes call directly. The wrapper adds orchestration and nothing else
    — no payload reshaping, because that would be a second place defining what
    a search hit looks like.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = url

    @property
    def url(self) -> Optional[str]:
        return self._url

    # -- the two searches ---------------------------------------------------
    def search_documents(
        self,
        query: Optional[str],
        *,
        strategy: str = DEFAULT_STRATEGY,
        method: str = "clause_aware",
        sort: str = DEFAULT_SORT,
        version_scope: str = "latest",
        limit: Optional[int] = None,
        offset: int = 0,
        **filter_kwargs: Any,
    ) -> Dict[str, Any]:
        """Ranked documents for a query, with the best clause as evidence.

        ``strategy=ilike`` is answered by running the clause-level substring
        search and grouping its hits by document here, in Python. That is the
        honest implementation: a substring match has no score, so there is
        nothing for SQL to aggregate, and the comparison baseline should not
        be given a ranking it does not have.
        """
        cleaned = normalize_query(query)
        _validate_choice("strategy", strategy, STRATEGIES)
        _validate_choice("method", method, METHODS)
        _validate_choice("sort", sort, SORTS)
        _validate_choice("version_scope", version_scope, VERSION_SCOPES)
        filters = repository.SearchFilters(**filter_kwargs)

        if strategy == "ilike":
            return self._documents_via_ilike(
                cleaned,
                method=method,
                version_scope=version_scope,
                limit=limit,
                offset=offset,
                filters=filters,
            )

        payload, took_ms = _timed(
            lambda: repository.search_documents(
                cleaned,
                method=method,
                filters=filters,
                sort=sort,
                version_scope=version_scope,
                limit=limit,
                offset=offset,
                url=self._url,
            )
        )
        return self._envelope(
            payload,
            query=cleaned,
            strategy="fts",
            sort=sort,
            method=method,
            version_scope=version_scope,
            took_ms=took_ms,
            filters=filters,
        )

    def search_clauses(
        self,
        query: Optional[str],
        *,
        strategy: str = DEFAULT_STRATEGY,
        method: str = "clause_aware",
        sort: str = DEFAULT_SORT,
        version_scope: str = "latest",
        limit: Optional[int] = None,
        offset: int = 0,
        include_text: bool = False,
        **filter_kwargs: Any,
    ) -> Dict[str, Any]:
        """Ranked clauses for a query. Every hit is citable."""
        cleaned = normalize_query(query)
        _validate_choice("strategy", strategy, STRATEGIES)
        _validate_choice("method", method, METHODS)
        _validate_choice("sort", sort, SORTS)
        _validate_choice("version_scope", version_scope, VERSION_SCOPES)
        filters = repository.SearchFilters(**filter_kwargs)

        if strategy == "ilike":
            payload, took_ms = _timed(
                lambda: repository.search_clauses_ilike(
                    cleaned,
                    method=method,
                    filters=filters,
                    version_scope=version_scope,
                    limit=limit,
                    offset=offset,
                    include_text=include_text,
                    url=self._url,
                )
            )
            return self._envelope(
                payload,
                query=cleaned,
                strategy="ilike",
                # ILIKE has no score, so "relevance" would be a lie about the
                # ordering. The response says what the order actually is.
                sort="document_order",
                method=method,
                version_scope=version_scope,
                took_ms=took_ms,
                filters=filters,
                extra_warnings=[
                    "strategy=ilike: substring match, no stemming and no "
                    "ranking. Results are in document order. This is the "
                    "comparison baseline, not the production search."
                ],
            )

        payload, took_ms = _timed(
            lambda: repository.search_clauses(
                cleaned,
                method=method,
                filters=filters,
                sort=sort,
                version_scope=version_scope,
                limit=limit,
                offset=offset,
                include_text=include_text,
                url=self._url,
            )
        )
        return self._envelope(
            payload,
            query=cleaned,
            strategy="fts",
            sort=sort,
            method=method,
            version_scope=version_scope,
            took_ms=took_ms,
            filters=filters,
        )

    def _documents_via_ilike(
        self,
        query: str,
        *,
        method: str,
        version_scope: str,
        limit: Optional[int],
        offset: int,
        filters: repository.SearchFilters,
    ) -> Dict[str, Any]:
        """Group ILIKE clause hits into documents, in Python and on purpose.

        It reads up to ``MAX_LIMIT`` clause hits and groups those. The limit
        is real and is reported: this is a comparison condition on a corpus of
        tens of documents, and giving it a paginating SQL aggregate would be
        building a second production search to measure the first one against.
        """
        payload, took_ms = _timed(
            lambda: repository.search_clauses_ilike(
                query,
                method=method,
                filters=filters,
                version_scope=version_scope,
                limit=repository.MAX_LIMIT,
                offset=0,
                include_text=False,
                url=self._url,
            )
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for hit in payload["items"]:
            entry = grouped.get(hit["document_id"])
            if entry is None:
                grouped[hit["document_id"]] = {
                    "document_id": hit["document_id"],
                    "title": hit["document_title"],
                    "department": hit["department"],
                    "document_type": hit["document_type"],
                    "reference_no": hit["reference_no"],
                    "version_id": hit["version_id"],
                    "version_label": hit["version_label"],
                    "publication_date": hit["publication_date"],
                    "page_count": None,
                    "version_count": 1,
                    "extraction_status": "ok",
                    "score": 0.0,
                    "matched_clauses": 1,
                    "metadata_match": False,
                    "best_clause": hit,
                }
            else:
                entry["matched_clauses"] += 1

        items = list(grouped.values())
        window = _clamp_limit(limit)
        start = max(0, int(offset))
        truncated = payload["total"] > repository.MAX_LIMIT
        warnings = [
            "strategy=ilike: substring match, no stemming and no ranking. "
            "Documents are grouped from the first "
            f"{repository.MAX_LIMIT} clause matches, in document order."
        ]
        if truncated:
            warnings.append(
                f"{payload['total']} clause matches exceeded the "
                f"{repository.MAX_LIMIT}-row grouping window, so the document "
                "count is a lower bound."
            )
        return {
            "query": query,
            "strategy": "ilike",
            "parsed_query": None,
            "sort": "document_order",
            "segmentation_method": method,
            "version_scope": version_scope,
            "total": len(items),
            "limit": window,
            "offset": start,
            "took_ms": took_ms,
            "warnings": warnings,
            "items": items[start : start + window],
        }

    # -- library support ----------------------------------------------------
    def facets(self) -> Dict[str, Any]:
        """Filter values and counts, straight from the corpus."""
        return repository.facet_counts(url=self._url)

    def suggest(self, prefix: Optional[str], *, limit: int = 10) -> Dict[str, Any]:
        """Type-ahead values. A short prefix returns nothing, not an error.

        Autocomplete fires on every keystroke; the first letter of a word is
        not a client mistake, so it gets an empty list rather than a 422.
        """
        cleaned = " ".join((prefix or "").split())
        return {
            "prefix": cleaned,
            "items": repository.suggest(cleaned, limit=limit, url=self._url),
        }

    def explain(self, query: Optional[str], *, method: str = "clause_aware") -> str:
        """``EXPLAIN ANALYZE`` for the clause search. Engineering tool."""
        cleaned = normalize_query(query)
        _validate_choice("method", method, METHODS)
        return repository.explain_clause_search(
            cleaned, method=method, url=self._url
        )

    # -- shared envelope ----------------------------------------------------
    def _envelope(
        self,
        payload: Dict[str, Any],
        *,
        query: str,
        strategy: str,
        sort: str,
        method: str,
        version_scope: str,
        took_ms: float,
        filters: repository.SearchFilters,
        extra_warnings: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Wrap a repository payload in the fields every response carries."""
        warnings = list(extra_warnings or [])
        if payload.get("no_searchable_terms"):
            warnings.append(
                "The query contained no searchable terms after stemming and "
                "stop-word removal, so nothing could match. PostgreSQL's "
                f"'{repository.TS_CONFIG}' configuration reduced it to an "
                "empty query — try different wording."
            )
        if filters.as_dict():
            warnings.append(
                "Filters applied: "
                + ", ".join(f"{k}={v}" for k, v in sorted(filters.as_dict().items()))
            )
        return {
            "query": query,
            "strategy": strategy,
            "parsed_query": payload.get("parsed_query"),
            "sort": sort,
            "segmentation_method": method,
            "version_scope": version_scope,
            "total": payload["total"],
            "limit": payload["limit"],
            "offset": payload["offset"],
            "took_ms": took_ms,
            "warnings": warnings,
            "items": payload["items"],
        }


def _clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return repository.DEFAULT_LIMIT
    return max(1, min(int(limit), repository.MAX_LIMIT))


def _timed(call: Callable[[], Any]) -> tuple:
    """Run ``call`` and return ``(result, elapsed_ms)``.

    ``perf_counter`` rather than ``time.time``: the latter can step backwards
    over an NTP correction, and a negative duration in a response is the kind
    of detail that makes a reader doubt every other number in it.
    """
    started = time.perf_counter()
    result = call()
    return result, round((time.perf_counter() - started) * 1000, 2)
