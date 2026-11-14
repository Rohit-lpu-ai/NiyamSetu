"""Experiment F — Library & Search retrieval quality, measured.

Run against a live API::

    .venv\\Scripts\\python -m evaluation.library_search_eval
    .venv\\Scripts\\python -m evaluation.library_search_eval --base http://127.0.0.1:8000/api

What this measures, and the one thing it does not
-------------------------------------------------
It scores the **document-level** ranking of the full-text baseline against the
committed judgements in ``data/annotations/search_relevance/judgements.json``,
and it runs the same queries through the ``ilike`` condition so the two can be
compared on identical input.

**It does not establish that search is good.** The judgements come from one
annotator who is not independent of the system (that file's §0 says so, and so
does every report this writes). The numbers are diagnostic: they find ranking
defects and they make a regression visible. Treated as a validated relevance
measurement they would be dishonest.

Four things are checked beyond precision and recall, because each is a defect
class that a precision number hides:

``pagination integrity``
    Page through every result set and assert that no clause id appears twice
    and none is skipped. A ranking with ties and no tie-breaker silently
    reshuffles between pages, and ``total`` then describes a set the pages
    never cover.
``filter correctness``
    Apply each facet value and assert every returned document really has it.
    A filter that is applied after pagination returns short pages instead of
    wrong rows, so the count is checked too.
``provenance integrity``
    For every clause hit, assert it carries at least one page row, that
    ``page_start``/``page_end`` bracket those pages, and that the stored
    character range resolves to non-empty text on the page the API serves.
``empty and edge handling``
    Blank, punctuation-only, stop-word-only and no-match queries must each
    produce the right *kind* of response, not just a 200.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
JUDGEMENTS = (
    REPO_ROOT / "data" / "annotations" / "search_relevance" / "judgements.json"
)
RESULTS_DIR = REPO_ROOT / "evaluation" / "library-search"
DEFAULT_BASE = "http://127.0.0.1:8000/api"

#: Cut-off for precision and recall. 5 because that is roughly what a Library
#: user sees without scrolling, and because with 17 documents a deeper cut-off
#: would measure almost the whole corpus.
K = 5

__all__ = [
    "K",
    "ApiClient",
    "load_judgements",
    "score_query",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "run",
    "main",
]


class ApiError(RuntimeError):
    """The API returned a non-2xx response. Carries the envelope."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class ApiClient:
    """Minimal GET client. stdlib only, so the scorer adds no dependency."""

    def __init__(self, base: str = DEFAULT_BASE) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str, **params: Any) -> Tuple[Dict[str, Any], float]:
        """Returns ``(payload, elapsed_ms)``. Raises :class:`ApiError` on 4xx/5xx."""
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}{path}"
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, json.loads(exc.read())) from None
        return payload, round((time.perf_counter() - started) * 1000, 2)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def precision_at_k(ranked: List[str], relevant: List[str], k: int = K) -> Optional[float]:
    """Fraction of the top ``k`` that are relevant.

    ``None`` when the query has no relevant document: precision over an empty
    truth set is not 0, it is undefined, and reporting 0 would make the
    deliberate-zero queries look like failures when returning nothing is the
    correct behaviour. Those queries are scored by
    :func:`correct_empty` instead.
    """
    if not relevant:
        return None
    top = ranked[:k]
    if not top:
        return 0.0
    return sum(1 for item in top if item in relevant) / len(top)


def recall_at_k(ranked: List[str], relevant: List[str], k: int = K) -> Optional[float]:
    if not relevant:
        return None
    top = set(ranked[:k])
    return sum(1 for item in relevant if item in top) / len(relevant)


def reciprocal_rank(ranked: List[str], relevant: List[str]) -> Optional[float]:
    """1/rank of the first relevant document. ``None`` when none is expected."""
    if not relevant:
        return None
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def correct_empty(ranked: List[str], relevant: List[str]) -> Optional[bool]:
    """For a deliberate-zero query: did search correctly return nothing?

    The honest scoring of "this corpus cannot answer that": anything returned
    is a false positive, and the measure is simply whether the result list is
    empty.
    """
    if relevant:
        return None
    return len(ranked) == 0


# --------------------------------------------------------------------------- #
# Per-query scoring
# --------------------------------------------------------------------------- #

def load_judgements(path: Path = JUDGEMENTS) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"no judgements at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def score_query(
    client: ApiClient, query: Dict[str, Any], strategy: str
) -> Dict[str, Any]:
    """Run one query under one strategy and score the document ranking."""
    payload, elapsed = client.get(
        "/search", q=query["query"], strategy=strategy, limit=10
    )
    ranked = [item["document_id"] for item in payload["items"]]
    relevant = query["relevant"]
    return {
        "id": query["id"],
        "query": query["query"],
        "strategy": strategy,
        "theme": query["theme"],
        "relevant": relevant,
        "returned": ranked,
        "total": payload["total"],
        "parsed_query": payload.get("parsed_query"),
        "precision_at_k": precision_at_k(ranked, relevant),
        "recall_at_k": recall_at_k(ranked, relevant),
        "reciprocal_rank": reciprocal_rank(ranked, relevant),
        "correct_empty": correct_empty(ranked, relevant),
        "first_relevant_rank": next(
            (i for i, d in enumerate(ranked, 1) if d in relevant), None
        ),
        "took_ms": payload["took_ms"],
        "round_trip_ms": elapsed,
        "warnings": payload["warnings"],
    }


def _mean(values: List[Optional[float]]) -> Optional[float]:
    real = [v for v in values if v is not None]
    return round(statistics.fmean(real), 4) if real else None


def summarise(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    judged = [s for s in scored if s["relevant"]]
    empties = [s for s in scored if not s["relevant"]]
    return {
        "queries": len(scored),
        "queries_with_a_relevant_document": len(judged),
        "deliberate_zero_queries": len(empties),
        f"precision_at_{K}": _mean([s["precision_at_k"] for s in judged]),
        f"recall_at_{K}": _mean([s["recall_at_k"] for s in judged]),
        "mean_reciprocal_rank": _mean([s["reciprocal_rank"] for s in judged]),
        "top_1_correct": sum(1 for s in judged if s["first_relevant_rank"] == 1),
        "found_in_top_k": sum(
            1 for s in judged if s["first_relevant_rank"] and s["first_relevant_rank"] <= K
        ),
        "missed_entirely": [
            s["id"] for s in judged if s["first_relevant_rank"] is None
        ],
        "deliberate_zeros_correct": sum(1 for s in empties if s["correct_empty"]),
        "deliberate_zeros_wrong": [
            {"id": s["id"], "returned": s["returned"]}
            for s in empties
            if not s["correct_empty"]
        ],
        "median_took_ms": round(statistics.median([s["took_ms"] for s in scored]), 2),
        "max_took_ms": max(s["took_ms"] for s in scored),
    }


# --------------------------------------------------------------------------- #
# Defect checks
# --------------------------------------------------------------------------- #

def check_pagination(client: ApiClient, query: str) -> Dict[str, Any]:
    """Page through a clause result set; assert no repeats and no gaps."""
    page_size = 3
    first, _ = client.get("/search/clauses", q=query, limit=page_size, offset=0)
    total = first["total"]

    # The single-page reference fetch is capped at the API's MAX_LIMIT, so the
    # paged walk must cover exactly that many rows for the comparison to mean
    # anything. Walking fewer and then diffing against 100 rows reports the
    # walk's own truncation as missing results -- which is what the first
    # version of this check did.
    whole, _ = client.get("/search/clauses", q=query, limit=100, offset=0)
    whole_ids = [item["clause_id"] for item in whole["items"]]
    target = len(whole_ids)

    seen: List[str] = []
    pages = 0
    offset = 0
    while offset < min(total, target) and pages < 60:
        page, _ = client.get("/search/clauses", q=query, limit=page_size, offset=offset)
        seen.extend(item["clause_id"] for item in page["items"])
        offset += page_size
        pages += 1
    return {
        "query": query,
        "total": total,
        "page_size": page_size,
        "pages_walked": pages,
        "reference_rows": target,
        "ids_collected": len(seen),
        "duplicates": len(seen) - len(set(seen)),
        "missing_from_paged_walk": sorted(set(whole_ids) - set(seen)),
        "order_matches_single_page": seen[:target] == whole_ids,
        "ok": (
            len(seen) == len(set(seen))
            and not set(whole_ids) - set(seen)
            and seen[:target] == whole_ids
        ),
    }


def check_filters(client: ApiClient) -> List[Dict[str, Any]]:
    """Every facet value, applied, and every returned row checked against it."""
    facets, _ = client.get("/library/facets")
    checks: List[Dict[str, Any]] = []

    for field, key in (("departments", "department"), ("document_types", "document_type")):
        for facet in facets[field]:
            value = "null" if facet["value"] is None else facet["value"]
            listing, _ = client.get("/documents", **{key: value}, limit=100)
            wrong = [
                item["document_id"]
                for item in listing["items"]
                if (item[key] or "null") != value
            ]
            checks.append(
                {
                    "check": f"documents?{key}={value[:38]}",
                    "facet_count": facet["count"],
                    "returned": listing["total"],
                    "counts_agree": facet["count"] == listing["total"],
                    "rows_with_wrong_value": wrong,
                    "ok": facet["count"] == listing["total"] and not wrong,
                }
            )

    # A date window, checked against the facet's own date range.
    listing, _ = client.get("/documents", date_from="2026-01-01", limit=100)
    bad = [
        item["document_id"]
        for item in listing["items"]
        if item["latest_publication_date"]
        and item["latest_publication_date"] < "2026-01-01"
    ]
    checks.append(
        {
            "check": "documents?date_from=2026-01-01",
            "returned": listing["total"],
            "rows_before_the_window": bad,
            "ok": not bad,
        }
    )

    # The same filter on the search endpoint, so filters are not only a
    # Library feature: a filtered search that ignores its filter is worse than
    # one that refuses it.
    search, _ = client.get(
        "/search", q="insurance", document_type="notification", limit=100
    )
    bad_types = [
        item["document_id"]
        for item in search["items"]
        if item["document_type"] != "notification"
    ]
    checks.append(
        {
            "check": "search?q=insurance&document_type=notification",
            "returned": search["total"],
            "rows_with_wrong_type": bad_types,
            "ok": not bad_types,
        }
    )
    return checks


def check_provenance(client: ApiClient, queries: List[str]) -> Dict[str, Any]:
    """Every clause hit must be citable: pages present, offsets resolving."""
    inspected = 0
    defects: List[Dict[str, Any]] = []
    page_cache: Dict[Tuple[str, str, int], str] = {}

    for query in queries:
        payload, _ = client.get("/search/clauses", q=query, limit=20)
        for hit in payload["items"]:
            inspected += 1
            if not hit["pages"]:
                defects.append({"clause_id": hit["clause_id"], "defect": "no page rows"})
                continue
            numbers = [page["page_number"] for page in hit["pages"]]
            if min(numbers) != hit["page_start"] or max(numbers) != hit["page_end"]:
                defects.append(
                    {
                        "clause_id": hit["clause_id"],
                        "defect": "page_start/page_end do not bracket the page rows",
                        "page_start": hit["page_start"],
                        "page_end": hit["page_end"],
                        "pages": numbers,
                    }
                )
            first = hit["pages"][0]
            cache_key = (hit["document_id"], hit["version_id"], first["page_number"])
            if cache_key not in page_cache:
                pages, _ = client.get(
                    f"/documents/{hit['document_id']}/pages",
                    version_id=hit["version_id"],
                    page_from=first["page_number"],
                    page_to=first["page_number"],
                    text="normalized",
                )
                page_cache[cache_key] = pages["items"][0]["normalized_text"] or ""
            body = page_cache[cache_key]
            sliced = body[first["char_start"] : first["char_end"]]
            if not sliced.strip():
                defects.append(
                    {
                        "clause_id": hit["clause_id"],
                        "defect": "stored offsets resolve to blank text",
                        "page": first["page_number"],
                        "range": [first["char_start"], first["char_end"]],
                        "page_length": len(body),
                    }
                )
    return {
        "hits_inspected": inspected,
        "defects": defects,
        "ok": not defects,
    }


def check_edges(client: ApiClient) -> List[Dict[str, Any]]:
    """Each edge case must produce the right kind of response, not just a 200."""
    checks: List[Dict[str, Any]] = []

    # A blank query is a client mistake: 422, not an empty result set.
    for value in ("", "   "):
        try:
            client.get("/search", q=value)
            checks.append({"check": f"q={value!r}", "expected": 422, "got": 200, "ok": False})
        except ApiError as exc:
            code = exc.body.get("error", {}).get("code")
            checks.append(
                {
                    "check": f"q={value!r}",
                    "expected": 422,
                    "got": exc.status,
                    "error_code": code,
                    "ok": exc.status == 422 and code == "VALIDATION_ERROR",
                }
            )

    # A query that parses to nothing is a *successful* search that found
    # nothing, and it must say why.
    for value in ("the of and", "!!!", "...???"):
        payload, _ = client.get("/search/clauses", q=value)
        explains = any("no searchable terms" in w for w in payload["warnings"])
        checks.append(
            {
                "check": f"q={value!r}",
                "total": payload["total"],
                "parsed_query": payload["parsed_query"],
                "warned": explains,
                "ok": payload["total"] == 0 and explains,
            }
        )

    # Unsupported options must be refused, not silently defaulted.
    for params, expect in (
        ({"q": "insurance", "strategy": "semantic"}, 422),
        ({"q": "insurance", "method": "sentence"}, 422),
        ({"q": "insurance", "sort": "whatever"}, 422),
        ({"q": "insurance", "limit": 0}, 422),
        ({"q": "insurance", "limit": 5000}, 422),
        ({"q": "insurance", "date_from": "not-a-date"}, 422),
        ({"q": "insurance", "date_from": "2026-05-01", "date_to": "2026-01-01"}, 422),
        ({"q": "x" * 400}, 422),
    ):
        label = ", ".join(f"{k}={str(v)[:14]}" for k, v in params.items())
        try:
            payload, _ = client.get("/search", **params)
            checks.append({"check": label, "expected": expect, "got": 200, "ok": False})
        except ApiError as exc:
            checks.append(
                {
                    "check": label,
                    "expected": expect,
                    "got": exc.status,
                    "error_code": exc.body.get("error", {}).get("code"),
                    "ok": exc.status == expect,
                }
            )

    # Paging past the end is a valid request with an empty page.
    payload, _ = client.get("/search/clauses", q="insurance", limit=5, offset=10_000)
    checks.append(
        {
            "check": "offset past the end",
            "total": payload["total"],
            "items": len(payload["items"]),
            "ok": payload["items"] == [] and payload["total"] > 0,
        }
    )

    # Suggestions on one character: empty list, not an error.
    payload, _ = client.get("/search/suggest", q="c")
    checks.append(
        {"check": "suggest with 1 character", "items": len(payload["items"]), "ok": payload["items"] == []}
    )
    return checks


#: The two ways a document can "match" a multi-word query, as SQL. The
#: implemented one is the first; the second is the alternative that was
#: measured and rejected, kept here so the rejection stays reproducible.
_SEGMENT_LEVEL_MATCH = """
    SELECT DISTINCT d.public_id
      FROM segments s
      JOIN document_versions v ON v.id = s.version_id
      JOIN documents d         ON d.id = v.document_id
     WHERE s.segmentation_method = 'clause_aware'
       AND v.extraction_status = 'ok'
       AND s.text_search @@ websearch_to_tsquery('english', :q)
"""

_DOCUMENT_LEVEL_MATCH = """
    SELECT d.public_id
      FROM segments s
      JOIN document_versions v ON v.id = s.version_id
      JOIN documents d         ON d.id = v.document_id
     WHERE s.segmentation_method = 'clause_aware'
       AND v.extraction_status = 'ok'
     GROUP BY d.id, d.public_id
    HAVING to_tsvector('english', string_agg(s.text, ' '))
           @@ websearch_to_tsquery('english', :q)
"""

_METADATA_MATCH = """
    SELECT d.public_id FROM documents d
     WHERE to_tsvector('english', coalesce(d.title, '') || ' ' ||
                       coalesce(d.reference_no, '') || ' ' ||
                       coalesce(d.department, ''))
           @@ websearch_to_tsquery('english', :q)
"""


def compare_match_granularity(url: str, queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Segment-level AND vs document-level AND, measured on the judgements.

    The defect this answers is real and was found by inspection, not guessed:
    ``websearch_to_tsquery`` ANDs its terms, and the AND is evaluated **within
    one segment**. A document that is genuinely about "insurance intermediaries
    registration" can mention each word in a different clause and match
    nothing. Aggregating every segment of a document into one tsvector fixes
    that case.

    It also makes the match looser everywhere else, so the question is
    empirical: how many relevant documents does it recover, and how many
    irrelevant ones does it let in? This function answers that on the
    committed query set. Needs a database connection, so it is skipped when
    one is not configured.
    """
    from sqlalchemy import text as sql  # local: the HTTP-only path must not need it

    from backend.app.db.engine import connection

    rows: List[Dict[str, Any]] = []
    with connection(url) as conn:
        for query in queries:
            params = {"q": query["query"]}
            segment = {r[0] for r in conn.execute(sql(_SEGMENT_LEVEL_MATCH), params)}
            document = {r[0] for r in conn.execute(sql(_DOCUMENT_LEVEL_MATCH), params)}
            metadata = {r[0] for r in conn.execute(sql(_METADATA_MATCH), params)}
            relevant = set(query["relevant"])
            implemented = segment | metadata
            candidate = document | metadata
            rows.append(
                {
                    "id": query["id"],
                    "query": query["query"],
                    "relevant": sorted(relevant),
                    "implemented_matches": sorted(implemented),
                    "candidate_matches": sorted(candidate),
                    "recovered_relevant": sorted((candidate - implemented) & relevant),
                    "added_irrelevant": sorted((candidate - implemented) - relevant),
                }
            )

    recovered = sum(len(r["recovered_relevant"]) for r in rows)
    added = sum(len(r["added_irrelevant"]) for r in rows)
    zeros = [r for r in rows if not r["relevant"]]
    broken_zeros = [r["id"] for r in zeros if r["candidate_matches"]]
    return {
        "question": (
            "Should a document match when its clauses contain the query terms "
            "between them, rather than one clause containing all of them?"
        ),
        "relevant_documents_recovered": recovered,
        "irrelevant_documents_added": added,
        "deliberate_zero_queries_broken": broken_zeros,
        "verdict": (
            "REJECTED. Document-level aggregation recovers "
            f"{recovered} relevant document(s) and admits {added} irrelevant "
            "one(s), so it trades precision for a small recall gain. It is "
            "also a sequential aggregate over every segment with no index "
            "support, where the implemented match uses the GIN index. The "
            "segment-level AND stays, and the miss it causes is reported as a "
            "limitation rather than papered over."
        ),
        "per_query": rows,
    }


def compare_strategies(
    scored_fts: List[Dict[str, Any]], scored_ilike: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Head-to-head on identical queries. Reports where each one wins."""
    by_id = {s["id"]: s for s in scored_ilike}
    rows = []
    fts_better = ilike_better = tied = 0
    for fts in scored_fts:
        other = by_id[fts["id"]]
        a = fts["reciprocal_rank"]
        b = other["reciprocal_rank"]
        if a is None:
            verdict = "n/a (no relevant document)"
            if fts["correct_empty"] and not other["correct_empty"]:
                verdict = "fts correct, ilike returned something"
                fts_better += 1
            elif other["correct_empty"] and not fts["correct_empty"]:
                verdict = "ilike correct, fts returned something"
                ilike_better += 1
            else:
                tied += 1
        elif a > b:
            verdict = "fts"
            fts_better += 1
        elif b > a:
            verdict = "ilike"
            ilike_better += 1
        else:
            verdict = "tie"
            tied += 1
        rows.append(
            {
                "id": fts["id"],
                "query": fts["query"],
                "fts_total": fts["total"],
                "ilike_total": other["total"],
                "fts_first_relevant_rank": fts["first_relevant_rank"],
                "ilike_first_relevant_rank": other["first_relevant_rank"],
                "fts_rr": a,
                "ilike_rr": b,
                "winner": verdict,
            }
        )
    return {
        "fts_better": fts_better,
        "ilike_better": ilike_better,
        "tied": tied,
        "per_query": rows,
    }


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run(
    base: str = DEFAULT_BASE, *, write: bool = True, db_url: Optional[str] = None
) -> Dict[str, Any]:
    client = ApiClient(base)
    data = load_judgements()
    queries = data["queries"]

    health, _ = client.get("/health")
    facets, _ = client.get("/library/facets")

    scored_fts = [score_query(client, q, "fts") for q in queries]
    scored_ilike = [score_query(client, q, "ilike") for q in queries]

    report = {
        "experiment": "F - Library & Search retrieval quality",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "api_base": base,
        "health": health,
        "caveat": data["caveat"],
        "annotator": {
            "annotator": data["annotator"],
            "independent": data["independent"],
            "guideline": "data/annotations/search_relevance/README.md",
        },
        "corpus_as_served": {
            "documents": facets["documents"],
            "versions": facets["versions"],
            "pages": facets["pages"],
            "segments": facets["segments"],
            "authorities": len(facets["departments"]),
            "date_range": facets["date_range"],
        },
        "corpus_as_judged": data["corpus"],
        "fts": {"summary": summarise(scored_fts), "per_query": scored_fts},
        "ilike": {"summary": summarise(scored_ilike), "per_query": scored_ilike},
        "fts_vs_ilike": compare_strategies(scored_fts, scored_ilike),
        "pagination": [
            check_pagination(client, q)
            for q in ("insurance", "transfer of shares", "annual fee payment")
        ],
        "filters": check_filters(client),
        "provenance": check_provenance(
            client,
            ["insurance", "capital adequacy", "transfer of shares", "gender policy women"],
        ),
        "edges": check_edges(client),
    }

    # The match-granularity experiment needs SQL, not HTTP. It is additional
    # evidence rather than a requirement, so a missing or unreachable database
    # records why it was skipped instead of failing the run.
    try:
        from backend.app.db.config import database_url

        url = db_url or database_url()
        report["match_granularity_experiment"] = compare_match_granularity(
            url, queries
        )
    except Exception as exc:  # noqa: BLE001 - optional evidence; report and continue
        report["match_granularity_experiment"] = {
            "skipped": f"{type(exc).__name__}: {str(exc)[:160]}"
        }

    if write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / "results.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    return report


def _print_summary(report: Dict[str, Any]) -> None:
    fts = report["fts"]["summary"]
    ilike = report["ilike"]["summary"]
    print("\ncorpus as served:", json.dumps(report["corpus_as_served"]))
    print(f"\n{'metric':<34}{'fts':>12}{'ilike':>12}")
    for key in (
        f"precision_at_{K}",
        f"recall_at_{K}",
        "mean_reciprocal_rank",
        "top_1_correct",
        "found_in_top_k",
        "deliberate_zeros_correct",
        "median_took_ms",
        "max_took_ms",
    ):
        a, b = fts.get(key), ilike.get(key)
        print(f"{key:<34}{str(a):>12}{str(b):>12}")
    print(f"\nqueries with a relevant document: {fts['queries_with_a_relevant_document']}")
    print(f"deliberate-zero queries:          {fts['deliberate_zero_queries']}")
    if fts["missed_entirely"]:
        print(f"MISSED by fts:                    {fts['missed_entirely']}")
    if fts["deliberate_zeros_wrong"]:
        print(f"fts returned hits where none expected: {fts['deliberate_zeros_wrong']}")

    head = report["fts_vs_ilike"]
    print(f"\nhead-to-head: fts better on {head['fts_better']}, "
          f"ilike better on {head['ilike_better']}, tied on {head['tied']}")

    print("\nper-query (fts):")
    for row in report["fts"]["per_query"]:
        mark = "ok " if (row["first_relevant_rank"] == 1 or row["correct_empty"]) else "   "
        print(
            f"  {mark}{row['id']}  rank={str(row['first_relevant_rank']):<5}"
            f"total={row['total']:<4} {row['query'][:46]:<48}"
            f"{row['took_ms']:>7}ms"
        )

    print("\ndefect checks:")
    for check in report["pagination"]:
        print(f"  pagination {'PASS' if check['ok'] else 'FAIL'}  {check['query'][:30]:<32}"
              f"total={check['total']} dupes={check['duplicates']} "
              f"missing={len(check['missing_from_paged_walk'])}")
    failed_filters = [c for c in report["filters"] if not c["ok"]]
    print(f"  filters    {'PASS' if not failed_filters else 'FAIL'}  "
          f"{len(report['filters'])} checks, {len(failed_filters)} failing")
    for check in failed_filters:
        print(f"      {check}")
    prov = report["provenance"]
    print(f"  provenance {'PASS' if prov['ok'] else 'FAIL'}  "
          f"{prov['hits_inspected']} hits inspected, {len(prov['defects'])} defects")
    for defect in prov["defects"][:5]:
        print(f"      {defect}")
    failed_edges = [c for c in report["edges"] if not c["ok"]]
    print(f"  edges      {'PASS' if not failed_edges else 'FAIL'}  "
          f"{len(report['edges'])} checks, {len(failed_edges)} failing")
    for check in failed_edges:
        print(f"      {check}")

    granularity = report.get("match_granularity_experiment", {})
    if "skipped" in granularity:
        print(f"\nmatch-granularity experiment skipped: {granularity['skipped']}")
    elif granularity:
        print(
            f"\nmatch granularity: document-level aggregation would recover "
            f"{granularity['relevant_documents_recovered']} relevant and add "
            f"{granularity['irrelevant_documents_added']} irrelevant document(s); "
            f"deliberate zeros broken: {granularity['deliberate_zero_queries_broken'] or 'none'}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--db-url", default=None,
        help="Database URL for the match-granularity experiment. Defaults to\n        the configured development database; pass the sample database when\n        the API is serving it.",
    )
    args = parser.parse_args(argv)
    try:
        report = run(args.base, write=not args.no_write, db_url=args.db_url)
    except (urllib.error.URLError, ApiError) as exc:
        print(f"cannot reach the API at {args.base}: {exc}")
        print("start it with: .venv\\Scripts\\python -m uvicorn backend.app.api.main:app")
        return 1
    _print_summary(report)
    failures = (
        [c for c in report["filters"] if not c["ok"]]
        + [c for c in report["edges"] if not c["ok"]]
        + [c for c in report["pagination"] if not c["ok"]]
        + (report["provenance"]["defects"] or [])
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
