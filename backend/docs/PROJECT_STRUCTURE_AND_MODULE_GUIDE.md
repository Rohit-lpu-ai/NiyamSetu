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
  └─ FastAPI              6 endpoints under /api                        Phase 4
```

**The property the whole system is built around:** every segment knows which
pages it came from and the character range on each of them, and that survives
extraction, cleaning, segmentation, a database round-trip and an HTTP response.
A clause spanning two pages returns **both** pages, never collapsed onto one.

**Verified, 2026-11-13, after the cleanup:**

| Check | Command | Result |
|---|---|---|
| Full suite | `python -m pytest` | **600 passed, 0 failed, 0 errors, 0 skipped** |
| Needs PostgreSQL | `python -m pytest -m integration` | **73 passed** |
| Needs no server | `python -m pytest -m "not integration"` | **527** |
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
│   │   └── library_search/    SCAFFOLD — the next module (§4)
│   ├── docs/              all documentation (this file included)
│   ├── scripts/           hand-run helpers; nothing in app/ imports these
│   └── tests/             600 tests, 17 modules, tests only
│
├── data/                  manifest, annotations (committed), raw + processed
│                          (git-ignored)
├── evaluation/            importable package: scorers + dated evidence reports
└── preview_app/           THROWAWAY developer preview — not the product frontend
```

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

### 3.1 Six endpoints, all under `/api`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + database reachability |
| POST | `/api/documents/ingest` | upload a PDF as a version — **the only writer** |
| GET | `/api/documents` | paginated, filterable document list |
| GET | `/api/documents/{document_id}` | metadata + every version |
| GET | `/api/documents/{document_id}/pages` | page text (`normalized` \| `raw` \| `both`) |
| GET | `/api/documents/{document_id}/clauses` | segments by method, with page provenance |

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
| `segments` | 501 | both methods side by side; has an unused `text_search` tsvector |
| `segment_pages` | 552 | per-page `char_start`/`char_end` — how a hit becomes a citation |
| `ingestion_jobs` | 4 | processing log |

Plus 1 view (`segment_citations`), 4 triggers and 15 indexes. Rationale for
every column, with viva Q&A: `phase3-postgresql.md`. What real segments forced
the original sketch to add: `backend/app/db/README.md` §4.

---

## 4. Where Library & Search goes

**`backend/app/library_search/` — scaffolded, not implemented.**

```
backend/app/library_search/
├── README.md        responsibility, reuse map, planned endpoints, open decisions
├── __init__.py      exports nothing yet, on purpose
├── service.py       strategy seam: keyword (baseline) | semantic | hybrid
├── repository.py    planned SQL over segments.text_search
└── schemas.py       deliberately contains no models — reuse api/schemas.py
```

Every entry point raises `NotImplementedError`, and
`backend/tests/test_library_search_scaffold.py` (10 tests) asserts that it
still does, that `schemas.py` defines no competing models, that no write
statement appears anywhere in the package, and that **the API still exposes
exactly six paths** — so a scaffold cannot quietly look finished.

**Why inside the backend rather than a separate `nlp/library_search/`:** search
is SQL over tables the backend already owns, through the connection pool the
backend already manages, and its results must carry the same provenance fields
as `/clauses` or they cannot be cited. A separate top-level package would need
its own database access and would end up duplicating the read layer. The other
three NLP modules (classification, rag_qa, extraction_diff) have no such pull
and stay outside — they consume the HTTP API.

**The one piece of unused infrastructure it exists to light up:**

```sql
segments.text_search tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);
```

Created by `0001_initial_schema.sql` for this module, already populated for all
501 segments, and **queried by nothing today**. The keyword baseline is
`@@ plainto_tsquery` + `ts_rank_cd` over it: no migration, no new dependency,
no index build. Its known defect — the `'english'` configuration is the wrong
stemmer for the Devanagari text in the corpus — is recorded in the schema and
must be reported, not hidden.

Full plan, including the four `[PROPOSED]` endpoints and the decisions that gate
them (D-7, D-10): **`backend/app/library_search/README.md`**.

---

## 5. Test inventory (measured 2026-11-13, post-cleanup)

| Module | Tests | Kind |
|---|---:|---|
| `test_segmentation.py` | 63 | unit |
| `test_data_quality.py` | 57 | unit (fixture corpus) |
| `test_experiment_b.py` | 52 | unit (scorer) |
| `test_db_schema.py` | 49 | 10 unit + 39 **PostgreSQL** |
| `test_preview_app.py` | 42 | unit |
| `test_api.py` | 39 | **mocked API** (dependency overrides) |
| `test_extractor_backends.py` | 32 | unit |
| `test_segmentation_cli.py` | 29 | unit |
| `test_page_cleaning.py` | 28 | unit |
| `test_extraction.py` | 23 | unit |
| `test_extraction_fidelity.py` | 22 | unit (E5 scorer) |
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
| `test_library_search_scaffold.py` | **10** | unit — **new in this cleanup** |
| `test_schemas.py` | 7 | unit |
| **Total** | **600** | **527 need no server · 73 need PostgreSQL** |

The `integration` marker is **derived, not hand-maintained**:
`conftest.py::pytest_collection_modifyitems` applies it to any test requesting
a database fixture, so "mocked or real?" cannot go stale. Integration tests
skip themselves — visibly — when `NIYAMSETU_TEST_DATABASE_URL` is unset, and
`assert_is_test_url` refuses any URL whose database name lacks `test`.

---

## 6. How a future module integrates without duplicating the backend

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
| Library & Search | Person 5 | `backend/app/library_search/` (scaffolded) | tables directly, read-only, via `db.engine` |
| Classification | Person 2 | `nlp/classification/` — **not created**, D-1 | `GET /clauses` |
| Ask / RAG | Person 3 | `nlp/rag_qa/` — **not created**, D-1 | `GET /clauses` as the retrieval corpus |
| Compare | Person 4 | `nlp/extraction_diff/` — **not created**, D-1 | two versions' clauses |
| Frontend | Person 2 | `frontend/` — **not created**, D-1 | the HTTP API only |

`frontend/` and `nlp/` are not created because D-1 (monorepo vs backend-only
repo) is unresolved, and creating empty directories would pre-empt a team
decision. `preview_app/` is **not** the frontend: it is a throwaway developer
tool, nothing in `backend/` imports it, and deleting it affects nothing else.

---

## 7. Two traps to know about before you touch anything

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

## 8. Environment facts worth knowing

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

## 9. Where to read next

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
| Who owns what? | `team-responsibilities.md`, `CLAUDE.md` §7 |
| What is still undecided? | `CLAUDE.md` §22.2 — **D-10 is the binding one** |

`backend/docs/superseded/` holds two obsolete reports that this cleanup
intended to delete but could not; `superseded/README.md` says what replaced
each and gives the command to remove them.
