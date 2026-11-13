# NiyamSetu — backend final completion report

**Date:** 2026-11-13 · **By:** Person 1 (Backend + Document NLP)
**Branch:** `diw_backend` · **Commit at start:** `fc259ac` · **Nothing committed or pushed**

---

## 1. Executive summary

The backend is **complete as an engineering deliverable** and **honestly
incomplete as an evaluation deliverable** — by necessity, not by omission.

Ingestion, PostgreSQL persistence and the FastAPI layer are built, running and
verified against the live database and a live server. **590 tests pass, 0 fail,
0 skip.** *(Measured when this report was written. The 2026-11-13 repository
cleanup added 10 scaffold tests and re-ran everything: **600 pass, 0 fail, 0
skip** — see `PROJECT_STRUCTURE_AND_MODULE_GUIDE.md` §5. The 590 figure below
is left as measured, not restated.)* The evaluation framework is now complete *as infrastructure*: five
experiments, every scorer built, tested and reproducible.

Three of those five cannot produce numbers without a human, and no amount of
engineering changes that. Inter-annotator agreement needs a second annotator;
cleaning correctness needs a labeller; CER/WER needs a transcriber. Each scorer
reports `PENDING`, and each is pinned by a test asserting it keeps reporting
`PENDING` until real input exists — so no future change can quietly turn an
absent measurement into a number.

**Verdict: READY WITH DOCUMENTED EVALUATION LIMITATIONS** (§14).

### What this session added

| | |
|---|---|
| **Fixed a blocking defect** | the independent-annotation package overlapped the primary annotations on **one page**, so agreement was not computable at all. Two overlap worksheets now cover all 4 annotated pages. |
| **New measurement** | table-overlap proxy across all 57 pages: **145 of 363 clause segments (39.9%)** overlap a detected table — generalising the worst known failure from one document to the corpus. |
| **New infrastructure** | E1 agreement scorer, E2 cleaning-correctness package, E5 CER/WER scorer — **74 new tests** |
| **Corrected an error I introduced** | ambiguous-marker count 21 → **31** (I had measured one document and generalised) |

---

## 2. Final architecture

```
Official PDF
  ├─ validate_pdf_file      type sniff, size cap, emptiness        [Phase 1]
  ├─ extract_pdf            per-page text, raw + normalised         [Phase 1]
  ├─ clean_pages            reversible header/footer removal        [Phase 2]
  ├─ segment_paragraphs     the baseline                            [Phase 2]
  ├─ segment_clauses        numbering, hierarchy, cross-page        [Phase 2]
  ├─ load_version_directory ONE transaction into PostgreSQL         [Phase 3]
  └─ FastAPI /api/*         6 endpoints                             [Phase 4]

backend/app/
├── document_processing/   extraction, cleaning, segmentation (12 modules)
├── db/                    config, engine, migrate, loader, repository
│   └── migrations/        0001_initial_schema.sql, 0002_public_id_sequences.sql
└── api/                   main, routes, schemas, dependencies, errors, ingestion
evaluation/                experiment_a, experiment_b, data_quality,
                           annotator_agreement, cleaning_correctness,
                           table_overlap, extraction_fidelity
```

**The API implements none of the pipeline.** `api/ingestion.py` calls the same
functions the CLIs call, in the same order — so a document cannot differ
depending on whether it arrived by CLI or HTTP.

---

## 3. Completed backend components

| Component | Status | Evidence |
|---|---|---|
| PDF validation + page extraction | ✅ | 57 pages, all `ok`, 0 empty; 35 tests |
| Normalisation (7 toggleable steps) | ✅ | 15 tests |
| Reversible cleaning | ✅ | 151 removals, 151 reversible, 151 with evidence; 28 tests |
| Paragraph segmentation | ✅ | 138 segments; 63 tests (shared) |
| Clause-aware segmentation | ✅ | 363 segments, depth 0–4, 5 marker styles |
| Page provenance | ✅ | **501/501** offset resolution, 3 enforcement layers |
| PostgreSQL schema + access | ✅ | 2 migrations, 7 tables, 4 triggers; 73 real-DB tests |
| FastAPI, 6 endpoints | ✅ | 39 mocked + 15 real-DB tests; live server verified |
| Data quality | ✅ | 13 checks, 0 errors |
| Evaluation framework | ✅ infrastructure | 5 experiments; 3 awaiting human input |

---

## 4. PostgreSQL verification

```
.venv\Scripts\python -m backend.app.db.migrate --status
→ 0001_initial_schema.sql       applied
  0002_public_id_sequences.sql  applied
```

| | measured | expected | |
|---|---:|---:|---|
| documents | 4 | 4 | ✅ |
| versions | 4 | 4 | ✅ |
| pages | 57 | 57 | ✅ |
| segments | 501 | 501 | ✅ |
| segment_pages | 552 | 552 | ✅ |
| removed_lines | 151 | — | ✅ |
| ingestion_jobs | 4 | — | ✅ |

Both databases reachable (`niyamsetu`, `niyamsetu_test`). **Nothing was
deleted, reset or recreated.** Destructive tests run only against
`niyamsetu_test`, which `assert_is_test_url` enforces by refusing any database
whose name lacks `test`.

**Constraints observed firing** — not merely declared: deferred provenance
trigger at COMMIT, page/version consistency, offset bounds, `raw_text`
immutability, duplicate checksum, duplicate ordinal, reversed page range,
invalid `offset_basis`, removal without evidence, mid-load rollback (0 rows
survive), idempotent reload (`loaded == 0`, counts unchanged).

---

## 5. FastAPI verification

Verified against a **live uvicorn server** this session, not only TestClient:

| endpoint | result |
|---|---|
| `GET /api/health` | 200 — `{"status":"ok","database":"ok","version":"0.1.0"}` |
| `GET /api/documents?limit=2` | 200 |
| `GET /api/documents/DOC-0001` | 200 |
| `GET /api/documents/DOC-0004/pages?limit=1` | 200 |
| `GET /api/documents/DOC-0004/clauses?limit=1` | 200 |
| `GET /docs` (Swagger) | 200 |
| `GET /api/documents/DOC-9999` | **404** in the §12.1 envelope |
| `GET /api/documents?limit=0` | **422** validation error |

**Ingestion behaviour** (tested against the real database, not mocks): new
document 201 `loaded`; re-upload **200 `skipped_duplicate`, nothing created and
stored artefacts untouched**; conflicting version 409 with `changed: false`;
oversized 413; non-PDF 415 (content sniffed); empty 422.

Health reports API and database **separately** — 503 + `degraded` when the
database is unreachable, never a flat `ok`.

**The production corpus was not altered to manufacture results.** Integration
tests write to a temp directory and the test database.

---

## 6. Extraction and segmentation results

**Extraction** (all 57 pages, 112,779 chars): 0 empty pages; **56** U+FFFD;
**314** broken words; **61** hyphen-space artefacts; **1,192** Devanagari chars
(materially corrupted). Decision: keep `pypdf` — PyMuPDF does not fix
Devanagari, it converts a visible loss into a silent one.

**Segmentation** — per document, which is the view that matters:

| document | method | P | R | F1 | num% |
|---|---|---:|---:|---:|---:|
| lead-bank (tables) | clause_aware | 0.182 | 0.500 | **0.267** | 1.000 |
| 272 master (prose) | clause_aware | 1.000 | 0.929 | **0.963** | 0.923 |
| **pooled** | paragraph | 0.842 | 0.889 | **0.865** | 0.000 |
| **pooled** | clause_aware | 0.625 | 0.833 | **0.714** | 0.929 |

The pooled clause-aware figure is **one document**, not a property of the
method: on prose it beats the baseline (0.963); on the table-heavy circular it
collapses (0.267). Sample: **21 clauses, 4 pages, one non-independent
annotator.**

---

## 7. All evaluation metrics

Full detail with formulas, sample sizes and commands:
**[`backend/docs/final-backend-evaluation.md`](final-backend-evaluation.md)**

| Metric | Value | Sample | Status |
|---|---|---|---|
| Page completeness | 57/57, 0 empty | full corpus | ✅ measured |
| Broken words | 314 | full corpus | ✅ measured |
| U+FFFD | 56 | full corpus | ✅ measured |
| Cleaning removals | 151, all reversible, all evidenced | full corpus | ✅ measured |
| Offset resolution | **501/501** (`cleaned_text` 14, `raw_text` 0) | full population | ✅ measured |
| Page attribution | 45/45 spanning correct; 0 orphans; 0 cross-version | full population | ✅ measured |
| Table-overlap **proxy** | **145/363 (39.9%)**, 22 table pages | full corpus | ✅ measured (upper bound) |
| Boundary F1 | para 0.865 / clause 0.714 pooled | **21 clauses** | ⚠️ preliminary |
| Clause-number recovery | 92.9% vs 0.0% | 21 clauses | ⚠️ preliminary |
| Database integrity | all counts match artefacts | full corpus | ✅ measured |
| **Inter-annotator agreement** | — | — | ⏳ **PENDING** |
| **Cleaning precision/recall** | — | — | ⏳ **PENDING** |
| **CER / WER** | — | — | ⏳ **PENDING** |

**Offset resolution is not segmentation accuracy.** A segmenter splitting every
page at character 500 would score 100% on the first and near-zero on the
second. 501/501 is bookkeeping integrity; boundary F1 is reading correctness.
They are never combined in any document here.

---

## 8. Human-dependent tasks still pending

| # | Needs | Who | Prepared |
|---|---|---|---|
| **E1** | annotate 4 pages blind, write `<VERSION>.annotator2.jsonl` | **a second person — not Person 1, not Claude** | ✅ 2 overlap worksheets + scorer + 18 tests |
| **E2** | label 91 lines as header/footer/page_number/content/unsure | a human, **not whoever tuned the 0.90 threshold** | ✅ template CSV + scorer + 20 tests |
| **E3** | annotate the 11 prepared pages | any annotator | ✅ worksheets + scorer (per-document breakdown already exists) |
| **E5** | transcribe 5 pages by hand | a human | ✅ 5 templates + CER/WER scorer + 22 tests |

**I did not and will not complete these.** Generating annotations, labels or
transcriptions would compare the system against itself and produce a figure
that looks like evidence and is not.

Commands:
```
.venv\Scripts\python -m evaluation.annotator_agreement
.venv\Scripts\python -m evaluation.cleaning_correctness --write-template
.venv\Scripts\python -m evaluation.extraction_fidelity --write-templates
```

---

## 9. Automated test results

| | count |
|---|---:|
| **Passed** | **590** |
| **Failed** | **0** |
| **Skipped** | **0** |
| **Blocked / not run** | **0** |

| category | tests |
|---|---:|
| Unit + mocked (`-m "not integration"`) | 517 |
| PostgreSQL integration (`-m integration`) | 73 |
| — of which mocked API | 39 |
| — of which real-database API | 15 |

The `integration` marker is **derived, not hand-maintained**:
`conftest.py` applies it to any test requesting a database fixture, so
"mocked or real?" cannot go stale.

**Quality checks:** `.env` git-ignored ✅ · the live password appears in **zero**
tracked files ✅ · `.env.example` placeholders only, now complete (11 variables) ✅ ·
test database isolated and name-guarded ✅ · no test touches the development
corpus ✅ · no broken imports ✅ · data quality 0 errors ✅.

---

## 10. Known limitations

1. **D-10 — the binding one.** 4 documents, 1 authority, 1 PDF toolchain,
   **0 version pairs**, 0 scanned. Nothing here generalises, and **Compare has
   no data to run on.**
2. **No independent validation of segmentation** until E1 completes.
3. **Cleaning correctness unmeasured** until E2. Reversible ≠ correct.
4. **No extraction accuracy figure** until E5.
5. **Table-overlap 39.9% is an upper bound**, not a false-positive rate — a
   table row can legitimately be a clause, and a confirmed over-flag exists in
   the output.
6. **Devanagari corrupted** (1,192 chars); no available extractor fixes it.
7. **314 broken words** threaten verbatim-substring citations (Person 3).
8. **0 empty pages** in the corpus, so that provenance path is exercised only
   by synthetic fixtures.
9. **No authentication**; synchronous ingestion (D-5); `segment_type` filtering
   and page pagination applied in Python, not SQL.
10. **Cosmetic, unfixable here:** `0001_initial_schema.sql` says 21 ambiguous
    markers; the true figure is 31. The migration is applied and the checksum
    guard correctly refuses an edit — needs a `COMMENT ON COLUMN` in a future
    migration.

---

## 11. API integration contracts

**[`backend/docs/backend-integration-contract.md`](backend-integration-contract.md)** —
IDs, every response schema, provenance rules, pagination, the error envelope,
ingestion semantics, module boundaries, and a per-person table of limitations
that will affect each teammate.

Key guarantees: identifiers are always `public_id` strings; every segment
carries ≥1 page reference; a multi-page clause returns **every** page;
`offset_basis` is `normalized_text`; every non-2xx response uses one envelope.

Key warning: **cite `clause_id`, never `clause_number`** — numbers are not
unique (3–54 duplicate `(number, level)` pairs per document).

---

## 12. Setup and startup commands

```bat
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                        :: then set NIYAMSETU_DB_PASSWORD

.venv\Scripts\python -m backend.app.db.migrate
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data/manifest.csv
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all
.venv\Scripts\python -m backend.app.db.loader

.venv\Scripts\python -m evaluation.data_quality
.venv\Scripts\python -m pytest

.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload
:: Swagger: http://127.0.0.1:8000/docs
```

Full instructions: `README.md` §Backend.

---

## 13. Git status and files changed

**Branch `diw_backend`. Nothing committed, nothing pushed, no branch switched,
no history altered.**

**Modified (4):**
- `.env.example` — added the 4 API settings (placeholders only)
- `CLAUDE.md` — status, roadmap, stop condition
- `README.md` — appended the backend setup section (vision content untouched)
- `evaluation/experiment-b/results.json` — **timestamp only**; every metric
  byte-identical, which also confirms the scorer is deterministic

**New — evaluation code (4):** `annotator_agreement.py`,
`cleaning_correctness.py`, `table_overlap.py`, `extraction_fidelity.py`

**New — tests (4, 74 tests):** `test_annotator_agreement.py`,
`test_cleaning_correctness.py`, `test_table_overlap.py`,
`test_extraction_fidelity.py`

**New — docs (3):** `backend-finalization-audit.md` (since superseded by this
report and moved to `superseded/`), `final-backend-evaluation.md`,
`backend-integration-contract.md`

**New — artefacts:** 2 overlap worksheets, the E2 labelling template, 5 E5
transcription templates, and results JSON for E1/E2/E4/E5

**Not touched:** `backend/app/document_processing/` (extraction and
segmentation algorithms unchanged — no measured evidence justified a change)
and the applied migrations.

---

## 14. Final verdict

# READY WITH DOCUMENTED EVALUATION LIMITATIONS

**Backend engineering: complete.** All six endpoints work against a live
database and a live server. Persistence is transactional, idempotent and
constraint-enforced. Provenance holds end to end — 501/501, verified on the
artefacts, again after the database round-trip, and again through HTTP.
590 tests pass with none skipped. No known critical defect remains.

**Evaluation infrastructure: complete.** Five experiments, every scorer built,
tested and reproducible from committed scripts and committed data.

**Evaluation evidence: partial, and honestly so.** Measured: extraction defect
counters, cleaning reversibility, provenance, the table-overlap proxy, database
integrity, API behaviour. Preliminary: segmentation boundary metrics (21
clauses, one non-independent annotator). **Pending human input: agreement,
cleaning correctness, CER/WER.**

**Why not READY FOR TEAM INTEGRATION without qualification:** teammates *can*
integrate today — the contract is stable and documented. But the project cannot
yet claim its segmentation is validated, and Person 5's error-propagation
analysis will inherit that gap. Saying "ready" unqualified would hide it.

**Why not NOT READY:** nothing blocks integration. Every pending item is
*evidence*, not *function*, and each has a prepared package and an exact
procedure.

**The honest viva answer to "is your backend done?"**

> *"The engineering is done and tested — 590 tests, a live API over a real
> database, and provenance verified at three layers. The evaluation is honest
> rather than complete: three measurements need a human I do not have, every
> scorer for them is built and tested, and each reports PENDING instead of a
> number. The biggest limitation is not code — it is that four documents from
> one authority cannot support a general claim."*
