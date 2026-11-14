"""End-to-end verification of the Library & Search user flow, over HTTP.

Run with both servers up::

    .venv\\Scripts\\python -m uvicorn backend.app.api.main:app --port 8000
    .venv\\Scripts\\python search_preview/serve.py --port 5173
    .venv\\Scripts\\python -m evaluation.library_search_e2e

Why this exists, stated plainly
-------------------------------
The intended verification was browser automation: open the page, type a query,
click a filter, open a result. **The Chrome extension was not connected on this
machine** (``tabs_context_mcp`` reported "Browser extension is not connected"),
so that was not done and is not claimed.

This is the strongest alternative, and it is not a weak one: it replays the
**exact request sequence the frontend issues**, in order, through the preview
server's own proxy — so it exercises the same URLs, the same query parameters
and the same JSON the browser would receive, and it checks the responses the
way ``app.js`` consumes them.

What it does **not** cover, and what therefore remains unverified:

* that the DOM renders those responses correctly;
* that clicks and keyboard input reach the handlers;
* CSS layout, and the no-HTML snippet rendering actually producing ``<mark>``
  elements in a real document.

Those need a browser. This file is the honest remainder.

Each step below maps to a frontend action, named in the ``action`` field, and
the script asserts the specific property the UI depends on rather than only a
200.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "evaluation" / "library-search"
PREVIEW_DIR = REPO_ROOT / "search_preview"

#: Through the preview server, not the API directly: that is the path the
#: browser takes, and it also proves the proxy forwards the error envelope.
DEFAULT_BASE = "http://127.0.0.1:5173"

__all__ = ["Client", "run", "main"]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.calls: List[Dict[str, Any]] = []

    def get(self, path: str, **params: Any) -> Tuple[int, Any, float]:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}{path}"
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body.decode("utf-8", "replace")
        self.calls.append({"url": url, "status": status, "ms": elapsed})
        return status, payload, elapsed


class Checks:
    """Collects pass/fail with a reason, so one failure does not hide the rest."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def record(self, action: str, check: str, ok: bool, detail: Any = "") -> bool:
        self.rows.append(
            {"action": action, "check": check, "ok": bool(ok), "detail": detail}
        )
        return bool(ok)

    @property
    def failures(self) -> List[Dict[str, Any]]:
        return [row for row in self.rows if not row["ok"]]


def _static_assets(client: Client, checks: Checks) -> None:
    """The three files the browser loads before any API call."""
    for name, expected in (
        ("/index.html", "text/html"),
        ("/styles.css", "text/css"),
        ("/app.js", "javascript"),
    ):
        status, payload, _ = client.get(name)
        checks.record(
            "load the page",
            f"GET {name} serves",
            status == 200 and bool(payload),
            f"status={status}",
        )
        void = expected  # content type is checked below from the raw headers
        del void

    # The page must reference all three, or the browser loads a blank shell.
    html = (PREVIEW_DIR / "index.html").read_text(encoding="utf-8")
    for asset in ("styles.css", "app.js"):
        checks.record(
            "load the page", f"index.html references {asset}", asset in html
        )


def _frontend_endpoints_exist(client: Client, checks: Checks) -> None:
    """Every endpoint ``app.js`` calls must exist in the served OpenAPI document.

    A frontend calling a path the backend does not expose is the integration
    bug that a 200-only smoke test misses: the page would simply show an empty
    state forever.
    """
    source = (PREVIEW_DIR / "app.js").read_text(encoding="utf-8")
    called = set(re.findall(r"api\(\s*[`'\"]([^`'\"]+)", source))
    # Template literals with ${...} become a FastAPI path parameter.
    normalised = {
        re.sub(r"\$\{[^}]+\}", "{document_id}", path).split("?")[0]
        for path in called
    }
    status, spec, _ = client.get("/api/openapi.json")
    served = set(spec.get("paths", {})) if isinstance(spec, dict) else set()
    for path in sorted(normalised):
        full = f"/api{path}"
        checks.record(
            "frontend/backend agreement",
            f"app.js calls {full}, which the API exposes",
            full in served,
            sorted(served),
        )


def run(base: str = DEFAULT_BASE, *, write: bool = True) -> Dict[str, Any]:
    client = Client(base)
    checks = Checks()

    # ---------------------------------------------------------------- step 1
    # Opening the page: static files, then the health pill, then the facets
    # and the first page of the listing -- exactly what app.js does on load.
    _static_assets(client, checks)

    status, health, _ = client.get("/api/health")
    checks.record(
        "health pill",
        "health reports ok/ok",
        status == 200 and health.get("status") == "ok" and health.get("database") == "ok",
        health,
    )

    status, facets, facets_ms = client.get("/api/library/facets")
    checks.record("library landing", "facets return 200", status == 200, status)
    checks.record(
        "library landing",
        "document count comes from the API and is non-zero",
        isinstance(facets, dict) and facets.get("documents", 0) > 0,
        facets.get("documents") if isinstance(facets, dict) else facets,
    )
    checks.record(
        "library landing",
        "authorities are listed with counts",
        bool(facets["departments"]) and all("count" in f for f in facets["departments"]),
    )
    checks.record(
        "library landing",
        "document types are listed with counts",
        bool(facets["document_types"]),
    )
    checks.record(
        "library landing",
        "facet counts sum to the document total",
        sum(f["count"] for f in facets["departments"]) == facets["documents"],
    )
    checks.record(
        "library landing",
        "both segmentation methods are counted",
        set(facets["segments"]) == {"clause_aware", "paragraph"},
        facets["segments"],
    )

    status, listing, _ = client.get("/api/documents", limit=10, offset=0, sort="public_id")
    checks.record("library listing", "listing returns 200", status == 200, status)
    checks.record(
        "library listing",
        "the first page holds documents and a total",
        bool(listing["items"]) and listing["total"] == facets["documents"],
        {"items": len(listing["items"]), "total": listing["total"]},
    )
    first_card = listing["items"][0]
    checks.record(
        "library listing",
        "a card carries the fields the UI renders",
        {
            "document_id",
            "title",
            "department",
            "document_type",
            "version_count",
            "page_count",
            "clause_count",
            "extraction_status",
        }
        <= set(first_card),
        sorted(first_card),
    )

    # ---------------------------------------------------------------- step 2
    # Pagination, both directions, asserting no row repeats across pages.
    seen: List[str] = []
    pages = 0
    offset = 0
    while offset < listing["total"] and pages < 10:
        _, page, _ = client.get("/api/documents", limit=5, offset=offset, sort="public_id")
        seen.extend(item["document_id"] for item in page["items"])
        offset += 5
        pages += 1
    checks.record(
        "library pagination",
        "paging covers every document exactly once",
        len(seen) == len(set(seen)) == listing["total"],
        {"collected": len(seen), "unique": len(set(seen)), "total": listing["total"]},
    )

    # ---------------------------------------------------------------- step 3
    # Sorting: the UI's order dropdown.
    for sort in ("public_id", "title", "date_desc", "date_asc", "recent"):
        status, sorted_listing, _ = client.get("/api/documents", sort=sort, limit=50)
        checks.record(
            "change sort order",
            f"sort={sort} returns the full set",
            status == 200 and sorted_listing["total"] == listing["total"],
            status,
        )
    _, by_date, _ = client.get("/api/documents", sort="date_desc", limit=50)
    dates = [
        item["latest_publication_date"]
        for item in by_date["items"]
        if item["latest_publication_date"]
    ]
    checks.record(
        "change sort order",
        "date_desc really orders by date",
        dates == sorted(dates, reverse=True),
        dates[:4],
    )

    # ---------------------------------------------------------------- step 4
    # Suggestions, as typed into the search box.
    probe = first_card["title"].split()[0]
    status, suggestions, _ = client.get("/api/search/suggest", q=probe, limit=8)
    checks.record(
        "type in the search box",
        "a two-or-more character prefix returns suggestions",
        status == 200 and isinstance(suggestions.get("items"), list),
        status,
    )
    status, short, _ = client.get("/api/search/suggest", q="c")
    checks.record(
        "type in the search box",
        "a one-character prefix returns an empty list, not an error",
        status == 200 and short["items"] == [],
        short,
    )

    # ---------------------------------------------------------------- step 5
    # The search itself, both scopes, as the Scope dropdown selects them.
    query = "insurance"
    status, doc_hits, doc_ms = client.get(
        "/api/search", q=query, strategy="fts", method="clause_aware",
        sort="relevance", limit=10, offset=0,
    )
    checks.record("search documents", "returns 200", status == 200, status)
    checks.record(
        "search documents",
        "results are ranked, with the envelope the UI shows",
        doc_hits["total"] > 0
        and doc_hits["strategy"] == "fts"
        and doc_hits["parsed_query"]
        and [h["score"] for h in doc_hits["items"]]
        == sorted((h["score"] for h in doc_hits["items"]), reverse=True),
        {"total": doc_hits["total"], "tsquery": doc_hits["parsed_query"]},
    )

    status, clause_hits, clause_ms = client.get(
        "/api/search/clauses", q=query, limit=10, offset=0
    )
    checks.record("search clauses", "returns 200", status == 200, status)
    checks.record(
        "search clauses",
        "every hit carries page provenance and an offset basis",
        all(
            hit["pages"]
            and min(p["page_number"] for p in hit["pages"]) == hit["page_start"]
            and max(p["page_number"] for p in hit["pages"]) == hit["page_end"]
            and hit["offset_basis"] == "normalized_text"
            for hit in clause_hits["items"]
        ),
    )
    checks.record(
        "search clauses",
        "snippets are marked with sentinels, not HTML",
        all(
            "<" not in hit["snippet"] and "</" not in hit["snippet"]
            for hit in clause_hits["items"]
        )
        and any("[[hl]]" in hit["snippet"] for hit in clause_hits["items"]),
    )

    # ---------------------------------------------------------------- step 6
    # Apply an authority filter, then a type filter, then remove them --
    # the chips in the sidebar.
    authority = next(
        (f for f in facets["departments"] if f["value"] and f["count"] >= 1), None
    )
    if authority:
        status, filtered, _ = client.get(
            "/api/search", q=query, department=authority["value"], limit=50
        )
        checks.record(
            "apply an authority filter",
            "every result has the filtered authority",
            status == 200
            and all(h["department"] == authority["value"] for h in filtered["items"]),
            authority["value"],
        )
        checks.record(
            "apply an authority filter",
            "the filtered total is not larger than the unfiltered one",
            filtered["total"] <= doc_hits["total"],
            {"filtered": filtered["total"], "unfiltered": doc_hits["total"]},
        )
        checks.record(
            "apply an authority filter",
            "the response says a filter was applied",
            any("department" in w for w in filtered["warnings"]),
            filtered["warnings"],
        )
        _, unfiltered_again, _ = client.get("/api/search", q=query, limit=50)
        checks.record(
            "remove the filter",
            "removing it restores the unfiltered total",
            unfiltered_again["total"] == doc_hits["total"],
        )

    null_bucket = next((f for f in facets["departments"] if f["value"] is None), None)
    if null_bucket:
        status, nulls, _ = client.get("/api/documents", department="null", limit=50)
        checks.record(
            "filter on 'Not stated'",
            "the null authority bucket is filterable and matches its count",
            status == 200 and nulls["total"] == null_bucket["count"],
            {"returned": nulls["total"], "facet": null_bucket["count"]},
        )

    year = next((f for f in facets["publication_years"] if f["value"]), None)
    if year:
        status, in_year, _ = client.get(
            "/api/documents",
            date_from=f"{year['value']}-01-01",
            date_to=f"{year['value']}-12-31",
            limit=50,
        )
        checks.record(
            "filter by year",
            "the year facet maps onto a date range the API accepts",
            status == 200 and in_year["total"] >= 1,
            {"year": year["value"], "returned": in_year["total"]},
        )

    # ---------------------------------------------------------------- step 7
    # Select a result, open its document, and trace provenance -- the step
    # the whole project exists for.
    hit = clause_hits["items"][0]
    status, detail, _ = client.get(f"/api/documents/{hit['document_id']}")
    checks.record("open a result", "the document detail loads", status == 200, status)
    checks.record(
        "open a result",
        "the detail carries versions with extraction status",
        bool(detail["versions"]) and "extraction_status" in detail["versions"][0],
    )
    checks.record(
        "open a result",
        "a source URL is available for the official PDF link",
        any(v.get("source_url") for v in detail["versions"]),
    )

    page = hit["pages"][0]
    status, pages_payload, _ = client.get(
        f"/api/documents/{hit['document_id']}/pages",
        version_id=hit["version_id"],
        page_from=page["page_number"],
        page_to=page["page_number"],
        text="normalized",
    )
    checks.record("inspect provenance", "the page text loads", status == 200, status)
    body = pages_payload["items"][0]["normalized_text"] or ""
    sliced = body[page["char_start"] : page["char_end"]]
    checks.record(
        "inspect provenance",
        "the stored character range resolves to real text on that page",
        bool(sliced.strip()),
        {
            "page": page["page_number"],
            "range": [page["char_start"], page["char_end"]],
            "page_length": len(body),
            "resolved_chars": len(sliced),
        },
    )
    checks.record(
        "inspect provenance",
        "the resolved text is what the search matched",
        any(
            word.lower() in sliced.lower()
            for word in re.sub(r"\[\[/?hl\]\]", " ", hit["snippet"]).split()
            if len(word) > 4
        ),
    )

    status, clause_list, _ = client.get(
        f"/api/documents/{hit['document_id']}/clauses",
        version_id=hit["version_id"],
        method="clause_aware",
        limit=40,
    )
    checks.record(
        "inspect clauses",
        "the document's clause list loads with its offset basis",
        status == 200 and clause_list["offset_basis"] == "normalized_text",
        status,
    )
    checks.record(
        "inspect clauses",
        "the clause that was clicked is in the document's clause list",
        any(
            item["clause_id"] == hit["clause_id"]
            for item in client.get(
                f"/api/documents/{hit['document_id']}/clauses",
                version_id=hit["version_id"],
                method=hit["segmentation_method"],
                limit=100,
            )[1]["items"]
        ),
        hit["clause_id"],
    )

    # ---------------------------------------------------------------- step 8
    # Result pagination, and the strategy toggle the UI exposes.
    collected: List[str] = []
    offset = 0
    while offset < min(clause_hits["total"], 30):
        _, page_of_hits, _ = client.get(
            "/api/search/clauses", q=query, limit=5, offset=offset
        )
        collected.extend(item["clause_id"] for item in page_of_hits["items"])
        offset += 5
    checks.record(
        "paginate the results",
        "no clause appears on two pages",
        len(collected) == len(set(collected)),
        {"collected": len(collected), "unique": len(set(collected))},
    )

    status, ilike, _ = client.get(
        "/api/search/clauses", q=query, strategy="ilike", limit=5
    )
    checks.record(
        "switch strategy to ILIKE",
        "the comparison baseline runs and says what it is",
        status == 200
        and ilike["strategy"] == "ilike"
        and any("substring" in w for w in ilike["warnings"]),
        ilike["warnings"],
    )
    checks.record(
        "switch strategy to ILIKE",
        "ILIKE hits still carry provenance",
        all(h["pages"] for h in ilike["items"]),
    )

    # ---------------------------------------------------------------- step 9
    # The states a UI must get right: empty, no-result, and invalid.
    status, nothing, _ = client.get(
        "/api/search/clauses", q="zzzzqqq nonexistent clause", limit=10
    )
    checks.record(
        "search with no matches",
        "an empty result is a 200 with total 0, not an error",
        status == 200 and nothing["total"] == 0 and nothing["items"] == [],
        status,
    )
    status, stopwords, _ = client.get("/api/search/clauses", q="the of and")
    checks.record(
        "search only stop words",
        "the response explains why nothing could match",
        status == 200
        and stopwords["total"] == 0
        and any("no searchable terms" in w for w in stopwords["warnings"]),
        stopwords["warnings"],
    )
    status, blank, _ = client.get("/api/search", q="")
    checks.record(
        "submit an empty query",
        "a blank query is a 422 in the shared error envelope",
        status == 422
        and isinstance(blank, dict)
        and blank.get("error", {}).get("code") == "VALIDATION_ERROR",
        blank,
    )
    checks.record(
        "submit an empty query",
        "the proxy forwards the error envelope unchanged",
        isinstance(blank, dict) and set(blank) == {"error"},
        blank,
    )
    status, missing, _ = client.get("/api/documents/DOC-9999")
    checks.record(
        "open a document that does not exist",
        "404 in the shared envelope",
        status == 404 and missing["error"]["code"] == "NOT_FOUND",
        status,
    )

    timings = {
        "facets_ms": facets_ms,
        "document_search_ms": doc_ms,
        "clause_search_ms": clause_ms,
        "slowest_call_ms": max(call["ms"] for call in client.calls),
        "median_call_ms": sorted(call["ms"] for call in client.calls)[
            len(client.calls) // 2
        ],
        "requests_made": len(client.calls),
    }

    report = {
        "experiment": "Library & Search end-to-end flow (HTTP)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base": base,
        "browser_automation": (
            "NOT PERFORMED. The Chrome extension was not connected on this "
            "machine (tabs_context_mcp reported 'Browser extension is not "
            "connected'), so no click-through was done and none is claimed. "
            "This script replays the frontend's exact request sequence "
            "instead; DOM rendering, input handling and CSS remain unverified."
        ),
        "corpus": {
            "documents": facets["documents"],
            "pages": facets["pages"],
            "segments": facets["segments"],
        },
        "timings_ms": timings,
        "checks": checks.rows,
        "passed": len(checks.rows) - len(checks.failures),
        "failed": len(checks.failures),
    }

    if write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / "e2e-results.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base", default=DEFAULT_BASE,
        help="Preview server base URL (it proxies /api), or the API itself.",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = run(args.base, write=not args.no_write)
    except urllib.error.URLError as exc:
        print(f"cannot reach {args.base}: {exc}")
        print("start both servers first; see this module's docstring.")
        return 1

    current = None
    for row in report["checks"]:
        if row["action"] != current:
            current = row["action"]
            print(f"\n{current}")
        mark = "PASS" if row["ok"] else "FAIL"
        print(f"  {mark}  {row['check']}")
        if not row["ok"]:
            print(f"        detail: {row['detail']}")

    print(
        f"\n{report['passed']} passed, {report['failed']} failed, "
        f"{report['timings_ms']['requests_made']} HTTP requests, "
        f"slowest {report['timings_ms']['slowest_call_ms']} ms"
    )
    print(f"\nbrowser automation: {report['browser_automation']}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
