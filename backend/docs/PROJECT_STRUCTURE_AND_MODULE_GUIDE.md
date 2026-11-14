# NiyamSetu — project structure and module guide

**Written:** 2026-11-13, after the repository cleanup · **By:** Person 1
**Status of every claim here: re-verified by running it**, on this machine,
after the cleanup — not carried over from an earlier report.

> **If you are new to this repository, read this file first.** It says what
> exists, where it lives, and where your module goes. It is deliberately short;
> each section points at the one document that goes deeper.

---

## 1. What the completed backend does

One pipeline and one HTTP contract:

```
Official policy PDF
  ├─ validate_pdf_file    type sniff, size cap, SHA-256, emptiness      Phase 1
  ├─ extract_pdf          per-page text; raw kept verbatim, normalised
  │                       stored alongside it — never replacing it      Phase 1
  ├─ clean_pages          reversible header/footer removal, evidence
  │                       recorded per removed line                     Phase 2
  ├─ segment_paragraphs   the baseline method                           Phase 2
  ├─ segment_clauses      clause-aware: numbering, headings, hierarchy  Phase 2
  ├─ load_version         one PostgreSQL transaction per version        Phase 3
  └─ FastAPI              14 endpoints under /api                       Phase 4+
       ├─ 6  documents, pages, clauses, health, ingest                  Phase 4
       ├─ 4  Library & Search                                          §4
       └─ 4  Compare                                                   §5
```

**The property the whole system is built around:** every segment knows which
pages it came from and the character range on each of them, and that survives
extraction, cleaning, segmentation, a database round-trip and an HTTP response.
A clause spanning two pages returns **both** pages, never collapsed onto one.

**Verified, 2026-11-13, after the cleanup:**

| Check | Command | Result |
|---|---|---|
| Full suite | `python -m pytest` | **1,083 passed, 1 skipped, 0 failed** (2026-11-14, after the browser session) |
| Needs PostgreSQL | `python -m pytest -m integration` | **196 passed** |
| Real browser | Brave + Claude in Chrome, by hand | both previews driven; **4 defects found and fixed**, each re-verified (COMPARISON_IMPLEMENTATION_REPORT.md §9.1) |
| Needs no server | `python -m pytest -m "not integration"` | **879 passed, 1 skipped** |
| Search retrieval | `python -m evaluation.library_search_eval` | P@5 **0.84** fts vs **0.34** ilike; 12/12 filter checks, 40/40 provenance, 15/15 edge cases |
| Search flow (HTTP) | `python -m evaluation.library_search_e2e` | **51/51** checks |
| Compare over real pairs | `python -m evaluation.comparison_eval` | **12/12** pairs pass every invariant; **5,104/5,104** citations verified; self-comparison 100% unchanged |
| Compare flow (HTTP) | hand-run against a live server | **59/59** checks |
| Compare frontend | `node compare_preview/check_render.mjs` | **55/55** checks |
| Entity extraction | `python -m evaluation.entity_extraction_eval` | regex exact F1 **0.61**, relaxed **0.69**; CRF and transformer **NOT RUN**, with reasons |
| Data quality | `python -m evaluation.data_quality` | 14 findings: **0 errors**, 1 warning (D-10), 13 info |
| Migrations applied | `applied_migrations()` | `0001_initial_schema.sql`, `0002_public_id_sequences.sql` |
| Dev database | `ping(database_url())` | **reachable** (PostgreSQL 18.6) |
| Stored corpus | `corpus_counts()` | 4 documents · 4 versions · 57 pages · 501 segments · 552 segment_pages · 151 removed lines · 4 jobs |
| Segments by method | — | 138 paragraph · 363 clause_aware · 45 page-spanning |
| Live API | `uvicorn` on a real port, 9 requests | all 6 endpoints 200; `404 NOT_FOUND` and `422 VALIDATION_ERROR` envelopes correct; `/docs`, `/redoc`, `/openapi.json` serve |

The corpus counts are **identical** to the pre-cleanup figures. No row was
added, changed or deleted.

**What it does not do**, stated plainly because a viva will ask: no OCR (D-6,
scanned PDFs excluded), no authentication, no Ask/Compare/classification/search
yet, and **no corpus decision** (D-10 — 4 documents from 1 authority, 0 version
pairs). See `final-backend-evaluation.md` §9 for the full limitation list and
which metrics are still `PENDING` human input.

---

## 2. Where everything lives

```
NiyamSetu-diw_backend/
├── README.md              product idea + how to run the backend
├── CLAUDE.md              project reference, status labels, open decisions
├── requirements.txt       root: shared by backend/, evaluation/, data/ scripts
├── pytest.ini             root: rootdir must be the repo for `backend.*` imports
├── .env / .env.example    root: db/config.py resolves REPO_ROOT/".env"
│
├── backend/
│   ├── app/
│   │   ├── api/               FastAPI: main, routes, schemas, dependencies,
│   │   │                      ingestion, errors
│   │   ├── db/                config, engine, migrate, loader, repository,
│   │   │                      migrations/*.sql
│   │   ├── document_processing/  extraction, normalisation, cleaning,
│   │   │                      segmentation, identity, CLIs
│   │   ├── library_search/    IMPLEMENTED — search, 4 endpoints (§4)
│   │   └── comparison/        IMPLEMENTED — Compare, 4 endpoints (§5)
│   ├── docs/              all documentation (this file included)
│   ├── scripts/           hand-run helpers; nothing in app/ imports these
│   └── tests/             1,076 tests, 31 modules, tests only
│
├── data/                  manifest, annotations (committed), raw + processed
│                          (git-ignored)
├── evaluation/            importable package: scorers + dated evidence reports
├── preview_app/           THROWAWAY developer preview over pipeline JSON
├── search_preview/        THROWAWAY browser UI over the live search API (§4)
└── compare_preview/       THROWAWAY browser UI over the live Compare API (§5)
```

`data/` holds three corpora, in three separate databases, and the separation is
deliberate — each one has published numbers measured against it:

| Corpus | Database | Why it is separate |
|---|---|---|
| `data/raw/` — 4 RBI documents | `niyamsetu` | Every number in `final-backend-evaluation.md` was measured on it |
| `data/sample_corpus/` — 17 documents, 3 authorities | `niyamsetu_sample` | The Library & Search relevance numbers were measured on it |
| `data/version_pairs/` — 9 documents, **3 genuine version chains** | `niyamsetu_compare` | Compare's only real test data; see §5 |

### Why four things are not inside `backend/`

| At root | Moving it would break |
|---|---|
| `pytest.ini` | `testpaths = backend/tests` with imports as `backend.*`; pytest's rootdir must be the repo root or `backend.tests` stops being a package and the shared `conftest.py` / `pdf_builder.py` stop resolving |
| `requirements.txt` | It is not backend-only — `evaluation/` and `data/annotations/` need the same pins. `preview_app/requirements.txt` stays separate so the backend installs without Streamlit |
| `.env`, `.env.example` | `db/config.py` computes `REPO_ROOT = Path(__file__).resolve().parents[3]` and reads `REPO_ROOT/".env"` |
| `data/`, `evaluation/` | `evaluation` is imported by the tests as a top-level package, and both are shared team deliverables, not backend internals |

`backend/app/db/migrations/` also stays put: `migrate.py` locates it as
`Path(__file__).parent / "migrations"`, both files are **already applied**, and
the runner verifies each applied file's SHA-256. See §7.

---

## 3. The existing contract: APIs and tables

### 3.1 Fourteen endpoints, all under `/api`

Six from Phase 4, unchanged:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + database reachability |
| POST | `/api/documents/ingest` | upload a PDF as a version — **the only writer** |
| GET | `/api/documents` | paginated, filterable document list |
| GET | `/api/documents/{document_id}` | metadata + every version |
| GET | `/api/documents/{document_id}/pages` | page text (`normalized` \| `raw` \| `both`) |
| GET | `/api/documents/{document_id}/clauses` | segments by method, with page provenance |

Four from Library & Search (§4):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search` | documents ranked by their best matching clause |
| GET | `/api/search/clauses` | clause hits, with page provenance |
| GET | `/api/search/suggest` | type-ahead over document identity fields |
| GET | `/api/library/facets` | filter values and corpus counts |

Four from Compare (§5):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/compare/options` | documents, their comparable versions, and every older→newer pair |
| POST | `/api/compare` | run a comparison; filters and thresholds in the body |
| GET | `/api/compare/entities` | rule-based entity extraction over one version |
| GET | `/api/compare/entities/labels` | the label vocabulary **and its limitations** |

**Two tests pin this surface**: one asserts the exact set of fourteen paths by
name, the other the count. A route added by accident fails both.

`GET /api/documents` additionally gained `date_from`, `date_to` and `sort`, plus
five `latest_version` fields per item — additive, so no existing consumer is
affected (§12.4).

Run it: `.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload` →
Swagger at `/docs`. Full detail in `api.md`; the guarantees teammates may rely
on are in **`backend-integration-contract.md`** (version 1.0).

Non-negotiables: identifiers in payloads are always `public_id` strings
(`DOC-0001`, `VER-0001`, `CLS-0000123`), every non-2xx response uses the one
error envelope, `offset_basis` is `normalized_text`, and **citations use
`clause_id`, never `clause_number`** — printed numbers are not unique.

### 3.2 Seven tables, applied and loaded

| Table | Rows | Note |
|---|---:|---|
| `documents` | 4 | logical policy identity |
| `document_versions` | 4 | one published issue; carries the file + checksum + `offset_basis` |
| `pages` | 57 | `raw_text` never overwritten; `normalized_text` separate |
| `page_removed_lines` | 151 | every cleaning removal, reversibly, with evidence |
| `segments` | 501 | both methods side by side; its `text_search` tsvector is what Library & Search queries |
| `segment_pages` | 552 | per-page `char_start`/`char_end` — how a hit becomes a citation |
| `ingestion_jobs` | 4 | processing log |

Plus 1 view (`segment_citations`), 4 triggers and 15 indexes. Rationale for
every column, with viva Q&A: `phase3-postgresql.md`. What real segments forced
the original sketch to add: `backend/app/db/README.md` §4.

---

## 4. Library & Search — implemented

**`backend/app/library_search/` — `[IMPLEMENTED]` 2026-11-14.** PostgreSQL
full-text search over the corpus, four endpoints, 170 tests, no new dependency
and no migration. Measured results, defects and limitations:
**`LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`**.

```
backend/app/library_search/
├── README.md        endpoint reference + the reuse map
├── __init__.py      public surface, CONTRACT_VERSION = "1.0.0"
├── api.py           the two routers, mounted by api/main.py
├── service.py       validation, strategy (fts | ilike), timing, envelope
├── repository.py    SQL over segments.text_search + the GIN index
└── schemas.py       ranking metadata and facets only
```

| Endpoint | What it returns |
|---|---|
| `GET /api/search` | documents, ranked by their best matching clause |
| `GET /api/search/clauses` | clauses, each citable to a document and page |
| `GET /api/search/suggest` | type-ahead over titles, reference numbers, departments |
| `GET /api/library/facets` | filter values and corpus counts, from the tables |

The piece of infrastructure it lit up:

```sql
segments.text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);
```

Created by `0001_initial_schema.sql` for exactly this and queried by nothing
until now — which is why the module needed **no migration**. The match is
`websearch_to_tsquery` (phrases, OR, exclusion, and it never raises on
punctuation), ranked by `ts_rank_cd` with length normalisation, snippets from
`ts_headline`.

**Why inside the backend rather than a separate `nlp/library_search/`:** search
is SQL over tables the backend already owns, through the connection pool it
already manages, and its results must carry the same provenance fields as
`/clauses` or they cannot be cited. A separate top-level package would need its
own database access and would duplicate the read layer. The other three NLP
modules (classification, rag_qa, extraction_diff) have no such pull and stay
outside — they consume the HTTP API.

**What it reuses instead of re-declaring:** `db.engine.connection` (one pool),
`db.config.database_url` (one place the password is read), `api.errors.ApiError`
(one envelope), `api.schemas` (one definition of a document, page and clause),
and `GET /api/documents` for the Library listing — extended additively with
`date_from`, `date_to` and `sort` rather than duplicated.

**Measured:** P@5 **0.84** and MRR **0.97** for full-text against **0.34** for a
substring baseline on a 20-query set, better on 11 queries and worse on none.
**That is a diagnostic, not a validated measurement** — one annotator, not
independent of the system, 17 documents. `data/annotations/search_relevance/README.md`
§0 before quoting it.

### The corpus it was tested on

| Database | Contents | Purpose |
|---|---|---|
| `niyamsetu` | 4 documents · 57 pages · 501 segments | the **verified development corpus**. Untouched by this work |
| `niyamsetu_sample` | **17 documents · 3 authorities · 148 pages · 1,503 segments** | the enlarged search corpus |
| `niyamsetu_test` | throwaway | truncated and reloaded by the integration fixtures |

Built by `backend/scripts/setup_sample_database.py`, which refuses any database
name without "sample" in it and contains no `DROP`, `TRUNCATE` or `--replace`.
14 PDFs were downloaded from `irdai.gov.in` and `www.nabard.org` with SHA-256
provenance recorded per file; 1 was excluded with a written reason; 9 selected
RBI documents could not be downloaded because their host does not complete a
TLS handshake from this machine. See `data/sample_corpus/sources.py`.

### The temporary frontend

`search_preview/` — `index.html`, `app.js`, `styles.css` and a 120-line stdlib
server that also proxies `/api`. No build step, no npm, no CDN. It is a
**throwaway developer UI for testing search**, not Person 2's product frontend,
and `frontend/` was deliberately not created (D-1).

```bat
set NIYAMSETU_DB_NAME=niyamsetu_sample
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8000
.venv\Scripts\python search_preview\serve.py --port 5173
```

**Browser click-through was not performed** — the Chrome extension was not
connected on this machine. `evaluation/library_search_e2e.py` replays the
frontend's exact request sequence instead (51 checks, all passing); DOM
rendering, input handling and CSS remain unverified.

---

## 5. Compare — implemented

**`backend/app/comparison/` — `[IMPLEMENTED]` 2026-11-14.** Clause alignment,
change classification and verified evidence between two issues of a policy.
Four endpoints, 316 tests, **no new dependency, no new table, no migration**.
Full audit of the teammate's prototype and every measured number:
**`COMPARISON_IMPLEMENTATION_REPORT.md`**. Module reference:
`backend/app/comparison/README.md`.

```
backend/app/comparison/
├── similarity.py   formatting-insensitive comparison key + TF-IDF cosine
├── alignment.py    the four-stage matcher, classification, confidence
├── entities.py     the rule-based entity extractor (the regex baseline)
├── explain.py      word-level diff + plain-language description
├── repository.py   read-only SQL over the existing tables
├── service.py      validation, direction, evidence resolution, filtering
├── schemas.py      Pydantic models (change metadata only)
└── api.py          the router, mounted by api/main.py
```

### How it aligns clauses

Four stages, strongest evidence first, one-to-one throughout. Measured over
the whole real corpus: **73.2%** of matches are settled by identical text,
**16.7%** by an equal printed clause number with related text, **10.1%** by
similarity.

The consequence that matters: **renumbering and reordering are not
modifications.** A clause whose text is identical once its printed number is
removed comes back as `unchanged` with `renumbered = true`. Reordering is
computed as the complement of a longest-increasing-subsequence over the
matched ordinals, so one clause promoted to the top flags one clause and not
the whole run.

Nothing is forced into a match. When the runner-up scored within
`ambiguity_margin` of the winner, the change is `ambiguous`, `confidence` is
`low`, and the clause the matcher nearly chose instead is named.

### Evidence — the part that matters

Every side of every change carries an `evidence` object whose `status` was
**verified, not assumed**: the stored character offsets are sliced out of the
stored page text on the version's own `offset_basis`, rejoined, and compared
against the clause under the comparison key. A `quote` is populated **only**
when that resolved, so a client rendering `quote` unconditionally still cannot
display an unverified citation.

**Measured: 5,104 of 5,104 clause sides across all 12 real version pairs
resolved.** A clause spanning a page break arrives with both pages, each span
verified separately.

### Why no `comparison_results` table

A comparison is a pure function of two stored versions, a method and four
thresholds. A stored row would go stale the moment a version is re-ingested.
Instead every response carries a `comparison_id` — a hash of the two version
fingerprints, the method and every threshold — so a result can be cited and
re-derived exactly, with nothing to invalidate.
`GET /api/compare/{comparison_id}` is deliberately absent and a test keeps it
absent.

### What Compare needed from the loader

`document_versions.supersedes_version_id` was never populated: the manifest's
`supersedes` column reached `extraction.json` and nothing read it back out.
`db/loader.link_supersedes` closes that, reading the declaration from the
artefacts so no new column and no migration were needed. It is what lets
`/compare/options` report `supersedes_declared` — the one statement about
version order asserted by a human rather than inferred (D-9). **6 links
written** across the real corpus.

### What is not measured

**There is no alignment accuracy figure, and that is deliberate.** No
clause-level annotation exists for 288 pages of master circulars, so
`evaluation/comparison_eval.py` reports invariants (12/12 pairs pass), a
self-comparison check (929 clauses against themselves: 100% unchanged), and
descriptive counts — never a score. The entity benchmark measures the regex
condition and reports CRF and transformer as **NOT RUN** with the reason, each
pinned by a test.

---

## 6. Test inventory (measured 2026-11-14)

| Module | Tests | Kind |
|---|---:|---|
| `test_comparison_unit.py` | 119 | unit — key, TF-IDF, the 4 stages, diff, entities · **new** |
| `test_comparison_integration.py` | 67 | **PostgreSQL** — 6 synthetic fixtures + the real RBI pairs · **new** |
| `test_segmentation.py` | 63 | unit |
| `test_data_quality.py` | 57 | unit (fixture corpus) |
| `test_library_search_integration.py` | 56 | **PostgreSQL** — search over the real corpus · **new** |
| `test_library_search_unit.py` | 54 | unit — validation, SQL predicates, envelope · **new** |
| `test_comparison_api.py` | 64 | **mocked service** — all 4 compare endpoints · **new** |
| `test_experiment_b.py` | 52 | unit (scorer) |
| `test_db_schema.py` | 49 | 10 unit + 39 **PostgreSQL** |
| `test_preview_app.py` | 42 | unit |
| `test_api.py` | 39 | **mocked API** (dependency overrides) |
| `test_library_search_api.py` | 38 | **mocked service** — no database · **new** |
| `test_compare_preview.py` | 41 | unit — the preview as source: nothing hardcoded, no markup · **new** |
| `test_entity_extraction_eval.py` | 33 | unit — the gold file, the scorer, the unrun conditions · **new** |
| `test_extractor_backends.py` | 32 | unit |
| `test_segmentation_cli.py` | 29 | unit |
| `test_page_cleaning.py` | 28 | unit |
| `test_extraction.py` | 23 | unit |
| `test_extraction_fidelity.py` | 22 | unit (E5 scorer) |
| `test_library_search_scaffold.py` | 22 | unit — module boundaries (read-only, API surface) · **new** |
| `test_cleaning_correctness.py` | 20 | unit (E2 scorer) |
| `test_annotator_agreement.py` | 18 | unit (E1 scorer) |
| `test_db_integration.py` | 17 | **PostgreSQL** |
| `test_api_integration.py` | 15 | **PostgreSQL + real pipeline over HTTP** |
| `test_normalization.py` | 15 | unit |
| `test_cli.py` | 14 | unit |
| `test_table_overlap.py` | 14 | unit (E4 scorer) |
| `test_manifest.py` | 12 | unit |
| `test_validation.py` | 12 | unit |
| `test_identity.py` | 10 | unit |
| `test_schemas.py` | 7 | unit |
| **Total** | **1,084** | **888 need no server · 196 need PostgreSQL** |

1,083 pass and **1 skips**: the single skip is
`test_this_tfidf_agrees_with_scikit_learn_when_it_is_installed`, which checks
`comparison/similarity.py` against scikit-learn's `TfidfVectorizer` to within
1e-9 and skips because scikit-learn is deliberately **not** a project
dependency. It is a visible "not run", not a gap.

170 of those are Library & Search (§4) and **324 are Compare (§5)**. The five
Compare modules replace nothing. Two existing tests were edited in place, and
both kept their strength: the exact-path-set assertion in
`test_library_search_scaffold.py` now lists the four compare paths, and the
path-count assertion in `test_library_search_api.py` went from 10 to 14 with
the reason written next to it. No test was weakened or removed.

Earlier, `test_library_search_scaffold.py` had itself been rewritten in place:
it used to assert that the module was an unimplemented scaffold, and now asserts
the module's boundaries — read-only, no duplicate payload definitions, and an
exact API surface.

The `integration` marker is **derived, not hand-maintained**:
`conftest.py::pytest_collection_modifyitems` applies it to any test requesting
a database fixture, so "mocked or real?" cannot go stale. Integration tests
skip themselves — visibly — when `NIYAMSETU_TEST_DATABASE_URL` is unset, and
`assert_is_test_url` refuses any URL whose database name lacks `test`.

---

## 7. How a future module integrates without duplicating the backend

Five rules. The first two are the ones that cost the most to get wrong.

1. **Never re-chunk the text.** The 501 stored segments *are* the chunks. New
   chunking produces offsets that no longer match `segment_pages`, and then a
   result cannot be turned into a citation — which is the project's whole
   differentiator (`CLAUDE.md` §6).
2. **Never write to `documents` / `document_versions` / `pages` / `segments` /
   `segment_pages`.** `db/loader.py`, driven by ingestion, is the only writer.
   An NLP module that writes document data makes provenance unprovable.
3. **Reuse, do not re-declare.** One engine (`db.engine.connection`), one URL
   resolution (`db.config.database_url`), one error envelope
   (`api.errors.ApiError`), one set of response models (`api.schemas`). A
   second definition of a clause payload is the failure mode to avoid.
4. **Go through the API if you are outside `backend/`.** Read-only SQL is
   tolerated for a batch experiment; writes are not; the frontend never touches
   PostgreSQL (`CLAUDE.md` §9).
5. **Inject, don't import, at the route.** Follow
   `api/dependencies.py::get_repository`: a dependency a test can override is
   why 39 API tests run with no server.

Where each module belongs:

| Module | Owner | Location | Consumes |
|---|---|---|---|
| Library & Search | Person 5 | `backend/app/library_search/` — **implemented** (§4) | tables directly, read-only, via `db.engine` |
| Compare | Person 4 | `backend/app/comparison/` — **implemented** (§5) | tables directly, read-only, via `db.engine` |
| Classification | Person 2 | `nlp/classification/` — **not created**, D-1 | `GET /clauses` |
| Ask / RAG | Person 3 | `nlp/rag_qa/` — **not created**, D-1 | `GET /clauses` as the retrieval corpus |
| Frontend | Person 2 | `frontend/` — **not created**, D-1 | the HTTP API only |

**Compare moved inside `backend/` for the same reason Library & Search did**,
and the earlier sketch putting it at `nlp/extraction_diff/` is superseded: a
diff is SQL over `segments` and `segment_pages`, through the engine the backend
already pools, and a separate top-level package would need its own database
access and would duplicate the read layer. The other two NLP modules have no
such pull toward the backend and stay outside it.

Two rules hold *inside* `backend/app/comparison/`, and tests pin both:
`alignment.py` may not import `entities.py` or `explain.py` — entity patterns
and wording must never be able to change which clauses are reported as
modified — and nothing in the package writes.

`frontend/` and `nlp/` are not created because D-1 (monorepo vs backend-only
repo) is unresolved, and creating empty directories would pre-empt a team
decision. `preview_app/`, `search_preview/` and `compare_preview/` are **not**
the frontend: all three are throwaway developer tools, nothing in `backend/`
imports any of them, and deleting one affects nothing else.

---

## 8. Two traps to know about before you touch anything

**Applied migrations are immutable.** `migrate.py` records the SHA-256 of each
applied file and refuses to continue if the file on disk has changed. During
this cleanup a path rewrite edited one comment line inside
`0001_initial_schema.sql`; the guard caught it immediately — 73 integration
tests skipped with an explicit checksum mismatch rather than running against a
schema the repository no longer described. The fix was to restore the file
byte-for-byte (checksum back to `73d4d409ff2b`), not to loosen the guard.

Two consequences that stand today, both cosmetic and both unfixable without a
new migration: the file's comment says "21 ambiguous markers" where the corpus
total is **31**, and it still cites `docs/pdf-extraction.md` at the pre-cleanup
path. Both need a `COMMENT ON COLUMN` correction in a future `0003_*.sql`.

**`sed -i` rewrites line endings on Windows.** The path rewrite in this cleanup
converted CRLF to LF in every file it opened, including files it changed
nothing in. With `core.autocrlf=true` git sees no content change, but
`git status` will show such files as modified until the index stat cache
refreshes. Prefer a Python rewrite that preserves `newline=""` for bulk edits
here.

---

**Do not recreate `api/routes/`, `api/schemas/`, `api/services/`.** Three empty
directories with those names existed next to `routes.py`, `schemas.py` and the
`document_processing` package; the cleanup removed them. Git never showed them
because it does not track empty directories, and they were an import hazard:
adding an `__init__.py` to `api/schemas/` would shadow `api/schemas.py` and
break every response model in the API.

---

## 9. Environment facts worth knowing

- **Python 3.11.9**, PostgreSQL **18.6** (service `postgresql-x64-18`). `psql`
  is at `C:\Program Files\PostgreSQL\18\bin` and is **not on PATH** — normal,
  and nothing here needs it.
- Authentication is `scram-sha-256`, so the password comes from `.env`
  (git-ignored). `.env.example` is committed with placeholders, 11 variables.
  The live password appears in **zero** tracked files.
- `.venv/` holds ~50 packages, not the "exactly pypdf and pytest" that
  `CLAUDE.md` §19 used to claim: `requirements.txt` has since pinned
  pymupdf/pdfplumber (evaluation-only, lazily imported), SQLAlchemy + psycopg,
  fastapi + uvicorn + python-multipart + httpx. Streamlit lives only in
  `preview_app/requirements.txt`. Each was approved and justified where it was
  pinned.
- Always run through the project virtualenv
  (`.venv\Scripts\python -m …`), never the global interpreter.
- **Windows console encoding.** `backend/scripts/inspect_real_extraction.py`
  prints the Devanagari letterhead damage it detects, which a cp1252 console
  cannot encode — it raises `UnicodeEncodeError` part-way through its output.
  Pre-existing, unrelated to any file move, and the workaround is one variable:

  ```
  set PYTHONIOENCODING=utf-8
  .venv\Scripts\python -m backend.scripts.inspect_real_extraction
  ```

---

## 10. Where to read next

| Question | Document |
|---|---|
| What is built, and what was verified? | `BACKEND_FINAL_COMPLETION_REPORT.md` |
| I am integrating — what can I rely on? | `backend-integration-contract.md` |
| What are the endpoints, exactly? | `api.md` |
| What has been measured, and what has not? | `final-backend-evaluation.md`, then `evaluation-audit.md` |
| Why is the schema shaped like this? | `phase3-postgresql.md`, `backend/app/db/README.md` |
| How does extraction / segmentation work? | `pdf-extraction.md`, `phase2-segmentation.md` |
| Why these architectural choices? | `architecture.md` §8 (AD-1…AD-9) |
| What is the next phase's definition of done? | `backend-roadmap.md` |
| How does Library & Search work? | `LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`, `backend/app/library_search/README.md` |
| How does Compare work, and what is *not* measured? | `COMPARISON_IMPLEMENTATION_REPORT.md`, `backend/app/comparison/README.md` |
| Where did the genuine version pairs come from? | `data/version_pairs/README.md` |
| Who owns what? | `team-responsibilities.md`, `CLAUDE.md` §7 |
| What is still undecided? | `CLAUDE.md` §22.2 — **D-10 is the binding one** |

`backend/docs/superseded/` holds two obsolete reports that this cleanup
intended to delete but could not; `superseded/README.md` says what replaced
each and gives the command to remove them.
