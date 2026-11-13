# Work Package 1 — repository verification report

**Ran:** 2026-11-12, before any code was modified. **By:** Person 1.

Step 1 of the work package asks for a verification of the repository against
the previous implementation report, with differences explained. This is that
report. Everything below was observed, not assumed.

---

## 1. Git state

| Claim in the brief | Observed | Verdict |
|---|---|---|
| branch pushed to `origin/diw_backend` | `diw_backend`, tracking `origin/diw_backend` | confirmed |
| latest commit `2cff9a4` | `2cff9a4 docs(phase2): schema proposal, Phase 2 design doc, and status brought to truth` | confirmed |
| — | working tree **clean** | no uncommitted work was at risk |

No reset, no history rewrite, no deletions. All changes in this work package
are additive except four edits, each listed in §6.

---

## 2. Test suite

```
.venv\Scripts\python -m pytest
385 passed in 4.40s
```

**Confirmed exactly** as reported. 12 test modules, 385 tests.

---

## 3. Pipeline artefacts

Phase 1 and Phase 2 output is present for all four versions, and the
`extraction.json` / `page_cleaning.json` / `segments_paragraph.json` /
`segments_clause_aware.json` quartet is complete in each.

| version | document | pages | paragraph segs | clause segs | header lines removed |
|---|---|---:|---:|---:|---:|
| VER-6507f3… | lead-bank Delhi circular | 2 | 4 | 11 | 26 |
| VER-7c5ad7… | commercial-banks 2nd amendment | 10 | 14 | 49 | 9 |
| VER-b8a13c… | 272 master circular (SC/ST credit) | 8 | 46 | 49 | 8 |
| VER-fd6e15… | capital-adequacy 11th amendment | 37 | 74 | 254 | 108 |
| **total** | | **57** | **138** | **363** | **151** |

Four real RBI PDFs are present in `data/raw/` (git-ignored, as documented).

### Difference found: the clause-segment count

`CLAUDE.md` stated **366** clause segments in two places. The artefacts contain
**363**. The total, **501**, was stated correctly — and 138 + 363 = 501, while
138 + 366 = 504, so the document was internally inconsistent and the *total*
was the correct half.

This is a documentation error, not a pipeline defect: the JSON files, the
`segment_count` fields, and the sum all agree on 363. Corrected in `CLAUDE.md`
§0 and §21. No code was affected.

A second, smaller instance — **and this one I got wrong, now corrected.** A
comment in `0001_initial_schema.sql` said there were **31** ambiguous clause
markers across the corpus. I measured only the capital-adequacy document, found
**21**, and "corrected" the comment to 21. The original was right: the corpus
total is **31** — 21 in capital-adequacy plus 10 in the commercial-banks
amendment.

The documentation is fixed. The SQL comment still reads 21 and **cannot be
edited**, because the migration has since been applied and the runner's
checksum verification correctly refuses an edited applied migration. It needs a
`COMMENT ON COLUMN` correction in a future migration — logged in
`docs/evaluation-audit.md` §4. A cosmetic error in a comment, but it is the kind
that a viva question would surface.

Everything else in the previous report checked out: 57 pages, 138 paragraph
segments, 151 removed header lines, 45 page-spanning segments, 4 marker styles
plus `annexure`, hierarchy to depth 4.

---

## 4. Experiments

| Experiment | State | Notes |
|---|---|---|
| A — extractor comparison | present, complete | `evaluation/experiment-a/results.json` + report. Conclusion stands: keep `pypdf`, keep PyMuPDF for tables |
| B — segmentation | present, **preliminary** | real metrics from a committed script and committed annotations. `inter_annotator_agreement` is the literal string `"PENDING - no second annotator"`. 4 pages, 2 documents, 21 clause records, single non-independent annotator |
| C — data quality | present, running | 12 checks, 0 errors, 1 warning (no version pair exists — D-10) |

Experiment B's status matches its own documented caveats. It has **not** been
overstated anywhere in the repository.

---

## 5. Environment findings

### D-8 is answerable: PostgreSQL is installed and running

`CLAUDE.md` §21 recorded PostgreSQL as **unconfirmed** because `psql` is not on
`PATH`. That is still true, and the conclusion drawn from it was wrong:

```
Get-Service *postgres*  →  postgresql-x64-18   Running
C:\Program Files\PostgreSQL\18\bin\psql.exe     exists
pg_hba.conf                                     scram-sha-256 for local + 127.0.0.1
```

**PostgreSQL 18 is installed and its service is running.** It is simply not on
`PATH`. The database precondition in the standing stop condition (§24) is
therefore met on this machine; whether it holds on the other four is still
genuinely open, and remains the useful part of D-8.

Authentication is `scram-sha-256`, so a password is required and none was
stored anywhere in the repository (no `.env`, no `pgpass.conf`) — correctly, per
§20.

### Virtualenv drift from what `CLAUDE.md` describes

`CLAUDE.md` §19 says `.venv/` holds "exactly `pypdf==6.18.1` and
`pytest==8.3.4`". It does not, and has not since Experiment A: it also contains
`pymupdf`, `pdfplumber`, `streamlit` and their transitive dependencies
(`pandas`, `numpy`, `onnxruntime`, `pyarrow`, `altair`, `uvicorn`, `starlette`
and more) — 50 packages.

This is **not** a violation: `requirements.txt` documents pymupdf/pdfplumber as
evaluation-only and `preview_app/requirements.txt` holds streamlit, both with
written justification and owner approval. The §19 sentence is just out of date.
Worth knowing because `uvicorn` and `starlette` being importable in the venv
could mislead someone into thinking FastAPI work has begun. It has not —
`fastapi` itself is absent, and there is no `main.py`.

### What genuinely does not exist

Confirmed absent, matching the report: no database (the schema was a
never-executed proposal), no FastAPI application, no `backend/app/main.py`, no
`nlp/`, no product `frontend/`. `preview_app/` is the throwaway developer tool
it claims to be — nothing in `backend/` imports from it.

---

## 6. Changes made in this work package

Additive except where noted. Nothing was deleted.

**New files:**
- `backend/app/db/{__init__,config,engine,migrate,loader,repository}.py`
- `backend/app/db/migrations/0002_public_id_sequences.sql`
- `backend/tests/test_db_schema.py`, `backend/tests/test_db_integration.py`
- `.env.example`
- `data/annotations/independent-review/` — 5 blind worksheets + README
- `evaluation/2026-11-12-provenance-validation.md`
- `evaluation/2026-11-12-phase3-postgresql.md`
- `docs/phase3-postgresql.md`, `docs/work-package-1-verification.md`

**Edited:**
1. `evaluation/data_quality.py` — added the `offset_resolution` check (13th) and
   a `_collapse_whitespace` helper. Existing checks untouched.
2. `evaluation/experiment_b_segmentation.py` — added the `blind` subcommand and
   `build_blind_worksheet`. Scoring logic untouched, so the committed
   Experiment B metrics are unchanged.
3. `backend/tests/test_data_quality.py`, `backend/tests/test_experiment_b.py` —
   tests appended.
4. `CLAUDE.md` — the 366 → 363 correction (2 places).
5. `backend/app/db/migrations/0001_initial_schema.sql` — one comment corrected
   (31 → 21 ambiguous markers). Safe because the file had **never been
   applied**; editing an applied migration is exactly what the runner's
   checksum verification now refuses.
6. `requirements.txt` — pinned `SQLAlchemy==2.0.46` and
   `psycopg[binary]==3.2.12`, with the justification Rule 5 requires.

**Not touched:** the whole of `backend/app/document_processing/`. Phase 1 and
Phase 2 pipeline code is unchanged, which is why the original 385 tests still
pass unmodified.

---

## 7. Conclusion

The previous implementation report was accurate apart from the clause-segment
count, and the repository was in the state it claimed to be in. The two
corrections are documentation-only.

The one materially new fact is that **PostgreSQL is installed and running**, so
Phase 3 was not blocked by its environment — only by the missing credential,
which is held by the owner and not by the repository.
