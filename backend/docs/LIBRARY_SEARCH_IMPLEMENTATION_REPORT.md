# Library & Search — implementation report

**Date:** 2026-11-14 · **By:** Person 1 (implementing Person 5's module)
**Branch:** `diw_backend` · **Nothing committed or pushed**
**Status:** `[IMPLEMENTED]` — built, run against real documents, measured, and
honestly limited.

> Every number in this report was produced by a committed script against a
> live database and a live server. Where a measurement needs a human the
> report says so and gives no figure.

---

## 0. Summary, and the one claim this report does not make

PostgreSQL full-text search over the corpus, four endpoints, a measured
comparison against a substring baseline, and a working browser frontend. The
full-text condition beats the substring condition on every query where they
differ, and the provenance guarantee survives into every search result.

| | |
|---|---|
| Endpoints added | **4** (`/api/search`, `/api/search/clauses`, `/api/search/suggest`, `/api/library/facets`) |
| Existing endpoints changed | **1**, additively (`GET /api/documents` gained `date_from`, `date_to`, `sort` + 5 fields) |
| Tests | **760 pass, 0 fail, 0 error, 0 skipped** (was 600; **170 new**, 10 obsolete scaffold tests rewritten) |
| Corpus searched | **17 documents, 3 authorities, 148 pages, 1,503 segments** — up from 4 / 1 / 57 / 501 |
| Sample documents acquired | **14 downloaded**, 13 ingested, 1 excluded with a recorded reason |
| Retrieval quality (FTS) | P@5 **0.84**, R@5 **0.97**, MRR **0.97**, top-1 correct **15/16** |
| Retrieval quality (ILIKE) | P@5 **0.34**, R@5 **0.38**, MRR **0.34**, top-1 correct **5/16** |
| Head-to-head | FTS better on **11** queries, ILIKE better on **0**, tied on **9** |
| Query latency | median **5–12 ms** warm; first call in a process **~1.1 s** (connection setup) |
| End-to-end flow | **51/51 HTTP checks pass** |

**The claim this report does not make: that search quality is validated.** The
relevance judgements come from one annotator who is not independent of the
system — the person who wrote the ranking. The numbers are diagnostic: they
find ranking defects and make regressions visible. `data/annotations/
search_relevance/README.md` §0 states this before any metric, and a second
annotator would turn it into a measurement.

---

## 1. What was implemented

```
backend/app/library_search/
├── repository.py   SQL: indexed FTS, ts_rank_cd, ts_headline, facets,
│                   suggestions, the ILIKE baseline, EXPLAIN helpers
├── service.py      validation, strategy choice, timing, response envelope
├── api.py          two routers, mounted by api/main.py
├── schemas.py      only the new shapes: ranking metadata, facets
├── __init__.py     public surface, CONTRACT_VERSION = "1.0.0"
└── README.md       endpoint reference and the reuse map
```

Supporting work, all new:

| Path | What |
|---|---|
| `data/sample_corpus/sources.py` | The URL list, with provenance per row and the 9 documents that could not be downloaded |
| `data/sample_corpus/fetch_sample_corpus.py` | Host-restricted downloader; validates `%PDF-`, records SHA-256, never overwrites |
| `backend/scripts/setup_sample_database.py` | Builds the **isolated** sample database; refuses any name without "sample" |
| `data/annotations/search_relevance/` | 20 queries, judgements, the guideline written first, and a blank template for a second annotator |
| `evaluation/library_search_eval.py` | Retrieval quality + 4 defect-class checks + the match-granularity experiment |
| `evaluation/library_search_e2e.py` | Replays the frontend's request sequence: 51 checks |
| `search_preview/` | The temporary frontend: `index.html`, `app.js`, `styles.css`, `serve.py` |

### Deliberately not built

* **No semantic or hybrid retrieval.** Both need an embedding model, which
  needs D-7 answered and a pinned dependency with a written justification
  (`CLAUDE.md` §24 rule 5). `service.STRATEGIES` lists `fts` and `ilike`;
  asking for `semantic` returns a 422 that says it is planned, not broken.
* **No vector database, no external search service, no new runtime
  dependency.** Zero lines were added to `requirements.txt`.
* **No migration.** The index this module needs already existed.
* **No second document listing.** The Library browses through
  `GET /api/documents`.

---

## 2. Architecture

```
HTTP
 └─ backend/app/api/main.py            mounts search_router + library_router
     └─ library_search/api.py          FastAPI validation, ApiError envelope
         └─ library_search/service.py  query validation, strategy, timing
             └─ library_search/repository.py   SQL
                 └─ backend/app/db/engine.py   the EXISTING pooled engine
```

Three rules this wiring keeps, each with a test:

1. **One engine.** `repository.py` imports `db.engine.connection`; the word
   `create_engine` does not appear in the package.
2. **Read-only.** No `INSERT`/`UPDATE`/`DELETE`/`DROP`/`TRUNCATE`/`ALTER`/
   `CREATE` in any SQL the package executes. The single exception is
   `SET LOCAL enable_seqscan` in the `EXPLAIN` helper, which changes a planner
   setting for one transaction and is asserted to be the *only* `SET`.
3. **Injected, not imported, at the route.** `get_search_service` is a FastAPI
   dependency, so 38 API tests run with no database.

**Why inside `backend/app/` rather than a separate `nlp/library_search/`:**
search is SQL over tables the backend already owns, through the pool the
backend already manages, and its results must carry the same provenance fields
as `/clauses` or they cannot be cited. A separate top-level package would need
its own database access and would duplicate the read layer. The other three NLP
modules have no such pull and stay outside (`CLAUDE.md` §17.2).

---

## 3. API endpoints and example requests

Full parameter tables: `backend/app/library_search/README.md` §5.

```bash
# documents, ranked by their best matching clause
curl "http://127.0.0.1:8000/api/search?q=capital%20adequacy&limit=3"

# a quoted phrase -- becomes 'capit' <-> 'adequaci'
curl "http://127.0.0.1:8000/api/search?q=%22capital+adequacy%22"

# clauses, with page provenance on every hit
curl "http://127.0.0.1:8000/api/search/clauses?q=transfer+of+shares&limit=5"

# filtered, sorted, paginated
curl "http://127.0.0.1:8000/api/search?q=insurance&document_type=notification&sort=date_desc&limit=5&offset=5"

# the measured comparison baseline
curl "http://127.0.0.1:8000/api/search/clauses?q=insurance&strategy=ilike"

# type-ahead, and the filter values the Library renders
curl "http://127.0.0.1:8000/api/search/suggest?q=capital"
curl "http://127.0.0.1:8000/api/library/facets"
```

Observed response for the first call (copied, not written by hand):

```json
{
  "query": "capital adequacy", "strategy": "fts",
  "parsed_query": "'capit' & 'adequaci'",
  "sort": "relevance", "segmentation_method": "clause_aware",
  "version_scope": "latest", "total": 2, "limit": 3, "offset": 0,
  "took_ms": 7.8, "warnings": [],
  "items": [{
    "document_id": "DOC-0004",
    "title": "Reserve Bank of India (Commercial Banks - Prudential Norms on Capital Adequacy) Eleventh Amendment Directions, 2026",
    "score": 1.0457, "matched_clauses": 2, "metadata_match": true,
    "best_clause": {"clause_id": "CLS-0000248", "page_start": 1, "page_end": 1, "pages": [{"page_number": 1, "char_start": 0, "char_end": 1142, "line_index_start": 0, "line_index_end": 18}], "offset_basis": "normalized_text"}
  }]
}
```

`parsed_query` is in the response on purpose: *"why did that match?"* is
otherwise unanswerable, and it is what makes the stop-word case explainable
rather than mysterious.

---

## 4. Database tables and indexes reused

**No schema change. No migration. Nothing in the schema was altered.**

| Reused | How search uses it |
|---|---|
| `segments.text_search` | **the whole baseline.** A stored `tsvector` generated column, populated for every segment, created for this module and queried by nothing until now |
| `segments_text_search_idx` | the GIN index on it (§8 for the `EXPLAIN` evidence) |
| `segments` | the search corpus: `text`, `segmentation_method`, `segment_type`, `clause_number`, `heading`, `level`, `ordinal`, `page_start`/`page_end` |
| `segment_pages` | per-page `char_start`/`char_end` — how a hit becomes a citation |
| `document_versions` | `extraction_status = 'ok'` gate, `publication_date` sorting, `offset_basis`, latest-version resolution |
| `documents` | title/reference/department matching and every facet |
| `documents_department_idx`, `documents_type_idx`, `versions_publication_idx` | the filters |

### Schema changes and why there were none

The §11.3 sketch anticipated this module: *"a full-text index
`GIN(to_tsvector('english', text))` on `segments` for Person 5's keyword-search
baseline."* `0001_initial_schema.sql` went further and made it a **stored
generated column** so that there is one place the configuration is recorded.
That decision is why this module needed no migration, and why the applied
migrations' checksums are untouched.

One thing a future migration should carry: `documents` has no stored tsvector,
so metadata matching computes `to_tsvector` over title + reference + department
at query time. On a tens-of-rows table that costs nothing measurable; at
thousands of documents it is the first thing to turn into a stored column.

---

## 5. Sample documents acquired

**14 PDFs downloaded from two official publishers; 13 ingested.** Full
provenance — URL, SHA-256, publisher index page, human-assigned type — in
`data/sample_corpus/manifest.csv` and `fetch-log.csv`; the selection with its
reasoning is in `data/sample_corpus/sources.py`.

| Authority | Documents | Types |
|---|---:|---|
| IRDAI (`irdai.gov.in`) | 12 | 6 circular, 3 amendment, 2 notification, 1 guideline |
| NABARD (`www.nabard.org`) | 2 (1 ingested) | 1 guideline, 1 excluded |
| *plus the existing corpus:* RBI | 4 | 2 circular, 2 amendment |

Date range **2021-05-20 → 2026-11-02**, so date filtering and date sorting have
a real five-year spread. Page counts 1–35. Both the "numbered clauses" and
"tables" requirements are covered: the IRDAI Master Circular on Registration
(35 pages, 107 clause segments, hierarchy to depth 3) and the reporting-format
circulars carry prescribed tables.

### What could not be acquired, and why it matters

**Nine selected RBI documents could not be downloaded.** The RBI publishes its
PDFs on `rbidocs.rbi.org.in`, and that host does not complete a TLS handshake
from this machine: TCP connects in 20 ms and the server never answers the
ClientHello — from Python, from `curl` with the Windows TLS stack, and with
every TLS version forced. `www.rbi.org.in` (the HTML index) is reachable, which
is how the candidates were identified. The nine are listed in
`sources.UNREACHABLE` with their titles and URLs so a teammate on a different
network can fetch them by moving a row.

**SEBI was attempted and abandoned.** `www.sebi.gov.in` is reachable, but its
circular index is rendered client-side and exposes no PDF link; the circular
pages are HTML. Three plausible attachment URL patterns were tried and returned
404. Guessing further risked putting a file of unknown provenance in the
corpus, so it was not done. IRDAI's index pages do expose direct PDF links,
which is why the sample is IRDAI-heavy.

**This matters for two reasons, and both are reported rather than worked
around:**

1. Two of the brief's suggested query themes — **KYC** and **digital payment
   security controls** — have no document behind them, because the RBI Master
   Directions that would answer them are in `sources.UNREACHABLE`. Those
   queries are **kept in the evaluation set and judged as having no relevant
   document** (§7, q17–q18). A search that returns something for them is
   matching vocabulary, not subject.
2. D-10 is not closed. 17 documents from 3 authorities is better than 4 from 1,
   and still not an agreed corpus.

### One document downloaded and deliberately excluded

`glossary.pdf` from NABARD: a **372-page English–Hindi banking dictionary**
whose Hindi is set in a legacy non-Unicode font. Extraction returns 780,269
characters of which 44.8% are ASCII letters and **zero are Devanagari
codepoints** — the Hindi came back as mojibake (`ie e'eceerCe efJekeAeme`),
exactly the failure Experiment A found no extractor fixes. Three pages extract
nothing, so the version is `partial`.

Indexing it would have put 372 pages of corrupt text into an `english`
tsvector and let it dominate every corpus count. The file stays on disk and in
the fetch log; the exclusion and its reason are recorded on the row in
`sources.py`, and the manifest-driven loader skips it. **The decision is
auditable rather than invisible** — and a test fixture that loaded "everything
on disk" instead of "everything in the manifest" was caught doing the wrong
thing because of it (§10, defect 5).

### Database safety

The sample corpus lives in a **separate database**, `niyamsetu_sample`:

```
dev     niyamsetu          4 documents ·  57 pages ·  501 segments   UNTOUCHED
sample  niyamsetu_sample  17 documents · 148 pages · 1,503 segments  new
test    niyamsetu_test     throwaway, truncated by the test fixtures
```

`backend/scripts/setup_sample_database.py` enforces that rather than trusting
it: the target name **must contain "sample"**, there is no `DROP`, no
`TRUNCATE` and no `--replace` passthrough in the file, and the development
corpus is *copied* from the same JSON artefacts, never moved. Verified before
and after: the development database still reports exactly
`4 / 4 / 57 / 151 / 501 / 552 / 138 / 363 / 45 / 4`.

The whole corpus was processed by the **existing** pipeline — the Phase 1
extraction CLI, the Phase 2 segmentation CLI and the Phase 3 loader. No second
extraction path was written.

---

## 6. Search evaluation: queries and results

20 queries, judged at the **document** level, binary relevance.
Committed: `data/annotations/search_relevance/judgements.json`.
Scorer: `evaluation/library_search_eval.py`.
Results: `evaluation/library-search/results.json`.

**Read `data/annotations/search_relevance/README.md` §0 before quoting any
number below.** One annotator, not independent of the system; agreement not
measured; 17 documents.

| | FTS | ILIKE |
|---|---:|---:|
| Precision@5 | **0.8438** | 0.3438 |
| Recall@5 | **0.9688** | 0.3750 |
| Mean reciprocal rank | **0.9688** | 0.3438 |
| Top-1 correct (of 16 judged) | **15** | 5 |
| Relevant found in top 5 | **16** | 6 |
| Deliberate zeros correct (of 4) | **4** | 4 |
| Median query time | 8.95 ms | 17.46 ms |

Per-query, FTS:

| # | Query | Relevant | Rank of first relevant |
|---|---|---|---|
| q01 | capital adequacy | DOC-0004 | **1** |
| q02 | `"capital adequacy"` (phrase) | DOC-0004 | **1** |
| q03 | priority sector lending | DOC-0003 | **1** |
| q04 | credit facilities to scheduled castes and scheduled tribes | DOC-0003 | **1** |
| q05 | lead bank responsibility districts | DOC-0001 | **1** |
| q06 | cyber security | DOC-0007 | **1** |
| q07 | insurance ombudsman | DOC-0009 | **1** |
| q08 | foreign investment in insurance companies | DOC-0011 | **1** |
| q09 | transfer of shares of insurers | DOC-0015 | **1** |
| q10 | annual fee payment certificate of registration | DOC-0010, DOC-0013 | **1** |
| q11 | obligatory cession reinsurance | DOC-0016 | **1** |
| q12 | alternative investment funds | DOC-0017 | **1** |
| q13 | formats for publishing financial results | DOC-0008 | **1** |
| q14 | gender policy women | DOC-0006 | **1** |
| **q15** | **insurance intermediaries registration** | DOC-0012, DOC-0015 | **2** |
| q16 | tariff de-notification | DOC-0014 | **1** |
| q17 | know your customer | *(none — no such document)* | correctly empty |
| q18 | digital payment security controls | *(none)* | correctly empty |
| q19 | liquidity coverage ratio requirements | *(none)* | correctly empty |
| q20 | zzzzqqq nonexistent term | *(none)* | correctly empty |

Nothing was missed entirely, and no deliberate-zero query returned a hit.

### q15, the one imperfect result, diagnosed

`insurance intermediaries registration` ranks DOC-0010 (a surveyors circular,
2 matching clauses) above DOC-0015, and **DOC-0012 — the Intermediaries
Amendment Regulations — does not appear at all.**

Measured cause, not guessed: DOC-0012 contains all three terms (16 clauses
mention "insurance", 2 "intermediaries", 4 "registration") but **never all
three in one segment**, and `registration` is absent from its title so the
metadata path does not catch it either. Segment-level AND therefore misses it.

The fix was implemented and measured before being judged:

| Condition | Relevant recovered | Irrelevant admitted | Deliberate zeros broken |
|---|---:|---:|---|
| document-level aggregated tsvector | **1** | **4** | 0 |

**Rejected.** It trades precision for a small recall gain and is a sequential
aggregate over every segment with no index support, where the implemented match
uses the GIN index. Reproducible via
`compare_match_granularity` in the scorer, recorded in `results.json` under
`match_granularity_experiment`. The miss stands as a documented limitation.

---

## 7. FTS versus ILIKE

ILIKE is in the module as the **measured comparison condition**, selectable
only by asking for `strategy=ilike`, and the response says what it is. Nothing
ever falls back to it silently.

**Head-to-head on identical queries: FTS better on 11, ILIKE better on 0, tied
on 9.** The nine ties are the four deliberate-zero queries (both correctly
empty) and five where both put the relevant document first.

Why FTS wins, mechanically rather than by assertion:

| | FTS | ILIKE |
|---|---|---|
| Stemming | `requirement` ≡ `requirements` (verified: identical totals) | different totals; the plural cannot find the singular |
| Ranking | `ts_rank_cd`, cover density, length-normalised | **none.** Results are in document order, because a substring match has no score |
| Phrases | `"capital adequacy"` → `'capit' <-> 'adequaci'` | literal string only |
| Multi-word | each term matched independently, then ANDed | the whole string must appear contiguously |
| Punctuation | `!!!` parses to an empty query and says so | matched literally |
| Index | GIN (§8) | none — `%term%` cannot use one |
| Median latency | 8.95 ms | 17.46 ms |

**Where ILIKE is genuinely better, and it is not nothing:** an exact literal
string, including punctuation and casing, in a document that uses an unusual
form — a reference number such as `IRDAI/INT/CIR/MISC/41/3/2026`, or a phrase
whose stems collide. That is why it stays available rather than being deleted.

---

## 8. Performance and the engineering review

### Is the GIN index used?

**Yes, and the honest answer is more specific than that.** Three versions of
the test for this were wrong, and each wrong version is worth recording:

1. *"The plan always uses `segments_text_search_idx`"* — **false.** On 1,503
   segments the whole table is a couple of megabytes, so for a term in ~10% of
   rows PostgreSQL correctly prefers a sequential scan. Demanding the index
   would be demanding a worse plan.
2. *"With `enable_seqscan = off` it must use the GIN index"* — **also false.**
   Discouraging sequential scans does not force a *particular* index: the
   planner can answer through `segments_version_method_ordinal_idx` and filter
   on `text_search`, and it does.
3. What is asserted now: **the GIN index is applicable to the predicate the
   search uses**, and whichever plan is chosen returns identical rows.

Observed plan for a selective term, on the live sample database:

```
Limit  (cost=45.26..45.27 rows=1) (actual time=0.282..0.283 rows=3 loops=1)
  ->  Sort  Sort Key: (ts_rank_cd(s.text_search, '''capit'' & ''adequaci''', 33)) DESC, s.public_id
        ->  Nested Loop
              ->  Bitmap Heap Scan on segments s
                    Recheck Cond: (text_search @@ '''capit'' & ''adequaci'''::tsquery)
                    ->  Bitmap Index Scan on segments_text_search_idx
                          Index Cond: (text_search @@ '''capit'' & ''adequaci'''::tsquery)
Execution Time: 0.365 ms
```

### ANALYZE is not optional after a bulk load

Measured: immediately after loading 17 documents the planner has **no
statistics** for `segments`, estimates every full-text match at one row, and
picks the wrong index — the search runs as an index scan with a filter instead
of a bitmap index scan. After `ANALYZE` the same query uses the GIN index.

`setup_sample_database.py` now runs `ANALYZE` on the five loaded tables, and so
does the integration-test fixture. **Recommendation for `db.loader`:** do the
same at the end of `load_processed_tree`. Not done here — the loader is
verified Phase 3 code and this report is not the place to change it quietly.

### Latency

| Path | Median | Max observed | Note |
|---|---:|---:|---|
| `/api/library/facets` | 17.6 ms | — | 8 aggregates |
| `/api/search/clauses` | 4–7 ms | 7.4 ms | |
| `/api/search` (documents) | 7–12 ms | 13.9 ms | 6 CTEs + per-document headline |
| **first call in a fresh process** | — | **~1,144 ms** | **connection setup, not query cost** |

The last row was investigated rather than reported as a slow query: 20
consecutive calls of a brand-new query through HTTP run 7–8 ms each, while the
first in-process call costs 1,144 ms and the next eleven cost 7–11 ms. That is
TCP plus `scram-sha-256` authentication plus pool creation. **Recommendation:**
a warm-up query at application startup. Not implemented — it changes
`api/main.py`'s startup behaviour, which deserves its own decision.

### Review checklist

| Item | Finding |
|---|---|
| GIN index used | yes, for selective terms; seq scan for common ones is correct at this size |
| Sensible limits | every endpoint caps at 100; the repository clamps rather than erroring |
| Joins causing duplicates | **no.** Asserted: no `clause_id` or `document_id` appears twice in any result set |
| Filters before pagination | yes — all filtering is in SQL `WHERE`, verified by a filtered `limit=1` returning the full `total` |
| Totals accurate | yes — `total` is a `count(*)` over the identical predicate, and is invariant across page windows |
| Deterministic sorting | yes — every `ORDER BY` ends in a unique id. Asserted for all four sorts and by a full paged walk of 135 hits |
| Response sizes | clause text withheld unless `include_text=true`; snippets capped at 2 fragments × 24 words |
| Parameterised SQL | yes — every value is bound. A test feeds `'; DROP TABLE segments; --` as a department and asserts it appears only in the parameters |
| New dependencies | **none.** `requirements.txt` unchanged |
| Migrations | **none** |

---

## 9. Search quality findings

Beyond the headline metrics, from inspecting real output:

1. **Ranking behaves as designed.** For `reporting requirements` the SC/ST
   master circular outranks the 37-page capital-adequacy amendment even though
   the latter has more matching clauses — because the former has a short clause
   *titled* "Reporting Requirements" and cover density plus length
   normalisation reward that. This is the intended behaviour and a good viva
   example.
2. **The metadata bonus does the job.** 10 of 16 judged queries put a
   `metadata_match` document first; `insurance ombudsman` finds the Ombudsman
   Rules rather than the six circulars that mention an ombudsman.
3. **Devanagari in snippets is the most visible defect.** For `cyber security`
   the snippet opens mid-Hindi before reaching the English match, because
   `ts_headline` picks the fragment with the densest match and the bilingual
   page interleaves the two languages. Not fixed: the fix is either a
   language-aware fragment chooser or stripping Devanagari from the snippet,
   and both change what the user is shown relative to what the document says.
4. **One segment in 1,503 is unsearchable.** A paragraph segment whose entire
   text is `***************` — a separator line the paragraph baseline kept.
   `to_tsvector` produces nothing from punctuation. Accepted and tested as a
   bound (<1% of segments), because the alternative is dropping
   punctuation-only segments at segmentation time, which would change the
   Phase 2 baseline Experiment B measured.
5. **No duplicate-segment problem**, because one segmentation method is
   searched at a time. Searching both would have returned every passage twice;
   this was designed out rather than discovered.
6. **No wrong-version results**, but **not meaningfully tested**: the corpus
   has exactly one version per document, so `version_scope=latest` has nothing
   to discriminate. The mechanism is implemented and unit-tested against the
   SQL; it is unproven on data. **This needs a version pair (D-10).**
7. **Table-heavy documents search acceptably** but inherit the known
   segmentation weakness (D-12): a flattened table row can become its own
   clause, so a hit may be a table fragment rather than a sentence. Visible in
   the reporting-format circulars.

---

## 10. Defects found and fixed

Each was found by running the thing, not by reading it.

| # | Defect | How it surfaced | Fix |
|---|---|---|---|
| 1 | `search_clauses_ilike` built its snippet from a column it never selected, so every ILIKE snippet was empty | writing the first integration test | select `s.text`, withhold it from the payload unless `include_text` |
| 2 | The document-level query applied a `version_id` filter against the *latest* version joined for display, not the segment's version — silently returning nothing | reading the SQL while adding filter tests | `document_sql(include_version=False)` for that query, with the reason in the docstring |
| 3 | A malformed date (`date_from=yesterday`) reached PostgreSQL and became a `DataError` → **500** for what is plainly a client mistake | an edge-case check in the evaluator | parse ISO dates in the route; 11 tests cover it. Applied to `GET /api/documents` too |
| 4 | My own pagination check walked 60 rows and diffed them against 100, reporting 40 "missing" results | the first evaluator run reported `pagination FAIL` | fixed the **check**, not the code — pagination was correct |
| 5 | The integration fixture loaded *every* version directory on disk, including the deliberately excluded 372-page dictionary — so the test database held a document the application would never serve | two index tests failed on a term (`abandon`) that occurs only in that dictionary, inside a `partial` version search excludes | load by manifest, and draw test terms only from `extraction_status='ok'` versions |
| 6 | Test fixtures drew terms from `ts_stat`, which returns **already-stemmed lexemes**; re-stemming `acceler` gives `accel`, which matches nothing. One lexeme (`-07122022`) also began with a hyphen, which `websearch_to_tsquery` reads as exclusion — turning the probe into a NOT query | three successive index-test failures | fixtures now require letters-only lexemes that round-trip through the query parser |
| 7 | The planner chose the wrong index because `ANALYZE` had never run after the bulk load | the GIN-index test failing with a `rows=1` estimate for a term matching 128 rows | `ANALYZE` in the setup script and the test fixture; recommendation recorded for the loader |
| 8 | 10 scaffold tests asserted "nothing is implemented", which the implementation made false | the full suite, after wiring the routers | rewrote that module in place as the **module-boundary** contract (read-only, no duplicate schemas, exact API surface) — the file's own docstring had said to replace rather than delete it |
| 9 | My write-statement guard flagged its own module: `truncated` (a variable) contains "TRUNCATE" and `offset` contains "SET ", and `repository.py`'s docstring *quotes* `CREATE INDEX` | the guard failing on clean code | match statement *shapes* with word boundaries, over string literals only, with docstrings excluded via `ast` |

Defects 4, 6 and 9 were in **my tests**, not in the module. They are listed
because a test that fails for the wrong reason is a defect too, and because
each one taught something about the tool that is worth having written down.

---

## 11. Tests

```
.venv\Scripts\python -m pytest
760 passed, 0 failed, 0 errors, 0 skipped        (54.8 s)
```

| Module | Tests | Needs PostgreSQL |
|---|---:|---|
| `test_library_search_unit.py` | 54 | no |
| `test_library_search_api.py` | 38 | no — fake service via `dependency_overrides` |
| `test_library_search_scaffold.py` | 22 | no — module boundaries |
| `test_library_search_integration.py` | 56 | **yes** |
| **new total** | **170** | 56 |
| pre-existing suite | 590 | 73 |

Coverage by the brief's list: service logic ✅ · repository ✅ · API ✅ ·
pagination ✅ · filters ✅ · empty query ✅ · no results ✅ · invalid input ✅ ·
provenance ✅ · database integration ✅ · regression on the existing endpoints ✅
(the six original paths are asserted present, and the full surface is pinned at
exactly ten).

No test needs the network. The downloaded PDFs are treated as fixed fixtures,
and the integration module loads the sample corpus only if it is already on
disk — so a fresh clone runs the same tests against the 4-document corpus.

### Browser testing — not performed

**The Chrome extension was not connected on this machine.**
`mcp__claude-in-chrome__tabs_context_mcp` returned *"Browser extension is not
connected"*, so no click-through was done and none is claimed. What that leaves
unverified: DOM rendering, click and keyboard handling, CSS layout, and the
snippet sentinels actually becoming `<mark>` elements in a real document.

The alternative run instead is `evaluation/library_search_e2e.py`, which
replays the frontend's **exact request sequence** through the preview server's
own proxy — the same URLs, parameters and JSON the browser receives — and
asserts the property each UI step depends on:

```
51 passed, 0 failed, 39 HTTP requests, slowest 390 ms
```

It covers: loading the page's three static files · every endpoint `app.js`
calls existing in the served OpenAPI document · the health pill · facets and
counts · the listing and its fields · paging over every document exactly once ·
all five sort orders · suggestions (and the 1-character case) · document and
clause search · provenance resolving to real page text · an authority filter
applied and removed · the "Not stated" bucket · a year filter · opening a
result · the clause list · result pagination without repeats · the ILIKE
toggle · a no-match query · a stop-word-only query · a blank query returning
422 in the shared envelope through the proxy · a 404 for a missing document.

`search_preview/app.js` also passes `node --check`.

---

## 12. Running the frontend

```bat
:: 1. the API, pointed at the enlarged sample corpus
set NIYAMSETU_DB_NAME=niyamsetu_sample
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8000

:: 2. the preview, in a second terminal
.venv\Scripts\python search_preview\serve.py --port 5173
```

Then open **http://127.0.0.1:5173**.

`serve.py` is 120 lines of standard library: it serves the three static files
and reverse-proxies `/api` to the backend, so cross-origin never enters the
picture. The API also sends CORS headers for `http://127.0.0.1:5173` (and
`localhost:5173`), configurable with `NIYAMSETU_CORS_ORIGINS`, so
`python -m http.server 5173 --directory search_preview` works too.

The UI covers the four required areas: a Library landing page with live counts,
authority / type / year / date-range filters and a sortable paginated listing;
a search-results view with ranking, scores, snippets, page references and the
parsed `tsquery`; and a document detail view with metadata, versions, the
official source link, the clause list, and a **provenance panel** that slices
the clause's stored character range out of the page text the API serves and
highlights it — so the end-to-end provenance claim is visible on screen rather
than asserted in prose.

It is explicitly **not** Person 2's product frontend (`CLAUDE.md` §5, §7, D-1),
and `search_preview/` is named to keep that distinction. No `frontend/`
directory was created.

---

## 13. Running the backend

```bat
copy .env.example .env                       :: first time; fill in the password
.venv\Scripts\python -m backend.app.db.migrate

:: the development corpus (4 documents) -- unchanged by this work
.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload

:: the enlarged sample corpus (17 documents), built once:
.venv\Scripts\python data\sample_corpus\fetch_sample_corpus.py
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data\sample_corpus\manifest.csv --output-dir data\sample_corpus\processed
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all --processed-dir data\sample_corpus\processed
.venv\Scripts\python -m backend.scripts.setup_sample_database
.venv\Scripts\python -m backend.scripts.setup_sample_database --status

:: checks
.venv\Scripts\python -m pytest
.venv\Scripts\python -m evaluation.library_search_eval          :: retrieval quality
.venv\Scripts\python -m evaluation.library_search_e2e           :: the HTTP flow
.venv\Scripts\python data\sample_corpus\fetch_sample_corpus.py --verify
```

`fetch_sample_corpus.py` never overwrites a present file and only downloads
from `irdai.gov.in` and `www.nabard.org`; `--verify` re-checks every manifest
checksum against the bytes on disk.

---

## 14. Known limitations

1. **D-10, still the binding one.** 17 documents, 3 authorities, **0 version
   pairs**. Relevance numbers from one non-independent annotator on 20 queries
   are diagnostic, not validated.
2. **No independent relevance judgement.** `template.json` is ready for a
   second annotator; agreement is **not measured**.
3. **No browser testing** (§11). DOM, input handling and CSS are unverified.
4. **Segment-level AND** misses a document whose terms are spread across
   clauses — q15, measured, with the rejected alternative recorded (§6).
5. **The `english` text-search configuration on a bilingual corpus.** 12 of 17
   documents carry real Devanagari; those tokens are indexed unstemmed, Hindi
   queries will not stem, and a snippet can open mid-Hindi (§9.3). D-3 puts
   Hindi out of scope.
6. **`version_scope` is unproven on data** — one version per document, so there
   is nothing to discriminate (§9.6).
7. **One unsearchable segment** of 1,503 (punctuation only) (§9.4).
8. **No semantic or hybrid retrieval.** D-7.
9. **Nine selected RBI documents are missing** because their host is
   unreachable from this machine, which is why two of the brief's query themes
   have no document behind them (§5).
10. **Document metadata matching is not index-backed** — `to_tsvector` over
    three columns at query time. Free at tens of documents; the first thing to
    change at thousands (§4).
11. **First query in a process costs ~1.1 s** of connection setup (§8). No
    warm-up is implemented.
12. **No authentication, and no rate limiting**, on an endpoint that runs
    full-text queries. Same posture as the rest of the API, and acceptable for
    a local project; it would not be for a deployment.
13. **Facet counts ignore filters.** The sidebar shows corpus-wide counts, not
    counts within the current filter — so a count can exceed the number of
    results after filtering. A real UX wrinkle, left as-is because computing
    conditional facets means running the filter N times.

---

## 15. Recommended next improvements

In the order I would do them.

1. **A second annotator on the 20-query set.** It is the single change that
   turns P@5 0.84 from a diagnostic into a measurement, and the package is
   ready. Cost: a few hours of one teammate's time.
2. **Close D-10.** Specifically: get the nine `sources.UNREACHABLE` RBI
   documents from a machine that can reach `rbidocs.rbi.org.in`, and find **one
   genuine version pair**. That unblocks `version_scope` testing, Compare, and
   the KYC and digital-payment-security query themes.
3. **`ANALYZE` at the end of `db.loader.load_processed_tree`** (§8). Three
   lines; without it the first searches after an ingestion use the wrong plan.
4. **A stored tsvector on `documents`** for title/reference/department
   matching, in a new migration, once the corpus is in the hundreds (§4).
5. **Conditional facet counts** (§14.13), if the UX wrinkle proves to matter.
6. **A language-aware `ts_headline`**, or a Hindi text-search configuration,
   once D-3 is decided. This is also where the *simple* version of Hindi
   support would start.
7. **Only then** argue D-7 for `semantic`, with the keyword numbers in hand.
   The seam is `service.STRATEGIES`; a dense condition slots in beside `fts`
   and is scored by the same committed script against the same judgements.

---

## Appendix — verification commands and their real output

```
.venv\Scripts\python -m pytest
  760 passed, 0 failed, 0 errors, 0 skipped

.venv\Scripts\python -m pytest -m integration
  129 passed            (73 pre-existing + 56 new)

.venv\Scripts\python -m evaluation.library_search_eval
  precision_at_5   fts 0.8438   ilike 0.3438
  recall_at_5      fts 0.9688   ilike 0.3750
  mrr              fts 0.9688   ilike 0.3438
  pagination PASS (3 queries, 0 duplicates, 0 missing)
  filters    PASS (12 checks)
  provenance PASS (40 hits inspected, 0 defects)
  edges      PASS (15 checks)
  match granularity: 1 relevant recovered, 4 irrelevant added -> rejected

.venv\Scripts\python -m evaluation.library_search_e2e
  51 passed, 0 failed, 39 HTTP requests, slowest 390 ms

.venv\Scripts\python -m backend.scripts.setup_sample_database --status
  documents 17 · versions 17 · pages 148 · segments 1503 · segment_pages 1618

corpus_counts(url=database_url())      # the development database
  documents 4 · versions 4 · pages 57 · segments 501 · segment_pages 552
  -- identical to before this work
```
