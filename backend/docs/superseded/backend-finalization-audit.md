# Backend finalization audit — Phase A

**Date:** 2026-11-13 · **By:** Person 1 · **Commit at audit:** `fc259ac`
**Branch:** `diw_backend` · **Working tree:** clean
**Remote:** `https://github.com/Rohit-lpu-ai/NiyamSetu.git`

Everything below was **re-verified by running it**, not carried over from
earlier reports.

---

## 1. Runtime verification

| Check | Command | Result |
|---|---|---|
| Test suite | `python -m pytest` | **516 passed, 0 failed, 0 skipped** |
| Integration-marked | `pytest -m integration --collect-only` | **73 of 516** |
| Data quality | `python -m evaluation.data_quality` | 13 checks, **0 errors**, 1 warning (D-10) |
| Dev database | `ping(database_url())` | **reachable** |
| Test database | `ping(test_database_url())` | **reachable** (`niyamsetu_test`) |
| Migrations | `applied_migrations()` | `0001_initial_schema.sql`, `0002_public_id_sequences.sql` |

**Stored corpus — matches the expected counts exactly:**

```
documents 4 · versions 4 · pages 57 · segments 501 · segment_pages 552
paragraph 138 · clause_aware 363 · page_spanning 45 · removed_lines 151
ingestion_jobs 4
```

---

## 2. Test inventory

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
| `test_db_integration.py` | 17 | **PostgreSQL** |
| `test_api_integration.py` | 15 | **PostgreSQL + real pipeline over HTTP** |
| `test_normalization.py` | 15 | unit |
| `test_cli.py` | 14 | unit |
| `test_manifest.py`, `test_validation.py` | 12 each | unit |
| `test_identity.py` | 10 | unit |
| `test_schemas.py` | 7 | unit |
| **Total** | **516** | **443 need no server · 73 need PostgreSQL** |

The `integration` marker is **derived, not hand-maintained** —
`conftest.py::pytest_collection_modifyitems` applies it to any test requesting a
database fixture, so "mocked or real?" cannot go stale.

---

## 3. Status by component

### Complete and verified

| Component | Evidence |
|---|---|
| PDF validation + extraction | 23 + 12 tests; 4 RBI PDFs, 57 pages, all `ok` |
| Normalisation (7 toggleable steps) | 15 tests |
| Reversible header/footer cleaning | 28 tests; 151 removals, all reversible with evidence |
| Paragraph + clause-aware segmentation | 63 tests; 138 + 363 segments |
| Page provenance | 501/501 offset resolution, artefacts **and** database |
| PostgreSQL schema + access layer | 2 migrations applied; 73 real-DB tests |
| FastAPI, 6 endpoints | 39 mocked + 15 real-DB tests; Swagger live |
| Data quality (13 checks) | 0 errors |
| Experiment A (extractors) | 5 conditions × 4 documents |
| Experiment C (data quality) | automated, in CI |

### Partially complete

| Component | What exists | What is missing |
|---|---|---|
| **Experiment B** | real P/R/F1 from committed script + annotations | 4 pages, 21 clauses, **one non-independent annotator**; pooled only, no per-document breakdown |
| **Annotation package** | guideline + 5 blind worksheets (11 pages) | worksheets **do not overlap** the primary annotated pages — agreement not computable (`evaluation-audit.md` §3.2) |
| **Corpus (D-10)** | 4 documents, 1 authority | no agreed corpus, **zero version pairs**, 0 scanned |

### Missing entirely

| Gap | Impact |
|---|---|
| **Cleaning correctness** | the only stage that *deletes* text has no correctness measure |
| **Inter-annotator agreement** | no independent validation of any segmentation claim |
| **Table-induced false-clause rate** | the worst failure (F1) quantified on one document only |
| **Extraction fidelity (CER/WER)** | no ground-truth transcription anywhere |
| **Per-document segmentation breakdown** | pooling 4 pages hid that one document drove the precision gap |

### Broken

**None found.** 0 failing tests, 0 data-quality errors, no broken imports, no
stale `.env`, no secret in any tracked file (scanned: the live password appears
in **zero** tracked files; `.env` is git-ignored, `.env.example` is committed
with placeholders).

One **cosmetic defect, not fixable here**: `0001_initial_schema.sql` says "21
ambiguous markers across the corpus"; the true figure is 31. The migration is
applied, so the runner's checksum guard correctly refuses an edit. Needs a
`COMMENT ON COLUMN` in a future migration.

---

## 4. Work plan for this mission

| Phase | Work | Needs a human? |
|---|---|---|
| B | Re-verify extraction, cleaning, segmentation, DB, API | no |
| **C/E1** | Overlap worksheets + agreement scorer + tests | **yes — a second annotator** |
| **C/E2** | Cleaning-label CSV + template + scorer | **yes — a labeller** |
| C/E3 | Per-document breakdown; schema validation for expanded annotations | yes, for the numbers |
| C/E4 | Table-overlap proxy over all 57 pages | no |
| C/E5 | Transcription template + CER/WER scorer | yes, for the numbers |
| D | `backend/docs/final-backend-evaluation.md` | no |
| E | Full test run with category breakdown | no |
| F | README, CLAUDE.md, setup docs, `.env.example` | no |
| G | `backend/docs/backend-integration-contract.md` | no |

**All infrastructure is buildable now.** Three experiments (E1, E2, E5) cannot
produce *numbers* without human input; their scorers, templates and tests will
be built and their results reported as `PENDING`, never estimated.
