# Backend + Document NLP — Phased Roadmap (Person 1)

> Written 2026-10-28. Owner: Person 1.
>
> **Status:** Phase 0 is complete. Phases 1–6 are `[PLANNED]` and have not been
> started — no application code exists in this repository.
>
> **Standing rule:** do not start Phase *n+1* until Phase *n*'s definition of done
> is met and its tests have actually been run with their real output reported.
>
> Timeline context: roughly one month from 2026-10-28, i.e. a target of about
> 2026-11-27 for an integrated, demonstrable system. The week bands below are
> planning guides, not commitments; the dependency order is what matters.

---

## Phase overview

| Phase | Goal | Rough band | Blocked by |
|---|---|---|---|
| 0 | Repository audit and project documentation | done 2026-10-28 | — |
| 1 | Corpus confirmed; page-aware PDF extraction | week 1 | D-10, D-2 |
| 2 | Segmentation (both methods) + annotated comparison | week 1–2 | Phase 1 |
| 3 | PostgreSQL schema and persistence | week 2 | D-4, D-8 |
| 4 | FastAPI endpoints with validation and tests | week 2–3 | Phase 3, D-5 |
| 5 | Data-quality and segmentation evaluation reports | week 3 | Phases 2–4 |
| 6 | Integration with Persons 2–5; end-to-end tests | week 3–4 | Phase 4 + their modules |

Phases 2 and 3 can overlap: segmentation is pure-Python and testable on files
before the database exists. Phase 5 is written up continuously, not left to the end.

---

## Phase 0 — Repository audit and project documentation

**Status:** complete, 2026-10-28.

**Goal:** a shared, accurate understanding of NiyamSetu before any code is written.

**Inputs:** the existing repository; the project brief; `README.md`.

**Tasks performed**
- Enumerated the repository: one pre-existing file, `README.md` (5.9 KB).
- Read `README.md` in full.
- Confirmed no git repository, no code, no data, no tests, no wireframe present.
- Checked the toolchain: Python 3.11.9, pip 26.0.1, git 2.53.0, node 24.15.0;
  `psql` not on PATH.
- Noted relevant globally-installed packages (not project dependencies).
- Wrote `CLAUDE.md`, `docs/architecture.md`, `docs/team-responsibilities.md`,
  this file, and `data/README.md`.
- Recorded ten open decisions (D-1 … D-10) and four brief↔repository conflicts.

**Deliverables:** the five documentation files above.

**Tests:** none applicable — documentation only. Validation performed was a
consistency review (`CLAUDE.md` §21 vs the actual filesystem) and a Mermaid
syntax review.

**Definition of done:** ✅ documentation exists; status of every component is
labelled; open decisions are listed; the owner has reviewed it.

**Risks:** the documentation describes a `[PROPOSED]` architecture. If the team
disagrees with D-1 … D-10, parts of Phases 3–4 change shape. That is why the
decisions are listed rather than assumed.

---

## Phase 1 — Corpus confirmation and page-aware PDF extraction

**Goal:** turn a confirmed set of real official PDFs into page-wise text records,
in memory and on disk, with provenance intact and extraction quality measured.
No database yet.

**Required inputs**
- **D-10 resolved:** an agreed list of documents, with at least one policy present
  in two versions (Person 4's Compare depends on this).
- The PDFs themselves, plus a metadata row per document (title, department,
  type, publication date, source URL, retrieval date).
- **D-2 resolved:** git initialised, so this work is committed on a branch.

**Tasks**
1. Create `backend/` skeleton, project virtualenv, pinned `requirements.txt`,
   `.env.example`, `.gitignore`.
2. Write `data/manifest.csv` (schema defined in `data/README.md`) recording every
   document's provenance.
3. Implement `services/validation.py`: extension and MIME sniff, size cap,
   SHA-256 checksum.
4. Implement `services/extraction.py`: per-page extraction returning
   `[(page_number, raw_text)]` plus the tool name and version used.
5. Implement `services/normalisation.py`: NFKC, ligature repair, de-hyphenation,
   whitespace collapse, repeating header/footer removal — each step individually
   toggleable and documented.
6. Implement image-only page detection; emit a warning and mark the document
   `partial`.
7. Run **Experiment A** across the corpus; write the findings.
8. Add a CLI: `python -m backend.app.cli extract <pdf> --out <json>`.

**Expected deliverables**
`backend/app/services/{validation,extraction,normalisation}.py`;
`backend/app/cli.py`; `data/manifest.csv`;
`evaluation/experiment-a-extraction.md`; `requirements.txt`; `.gitignore`;
`.env.example`.

**Tests**
- Unit: checksum stability; rejection of a non-PDF; rejection of an oversized file.
- Unit: each normalisation step, on crafted strings.
- Fixture: a small committed 3-page PDF extracts 3 page records, in order.
- Fixture: an image-only page yields `is_empty=true` plus a warning.
- Manual: read the extracted text of 3 real documents side by side with the PDF
  and record what is wrong.

**Definition of done**
Every corpus PDF extracts to page-wise records with no silent failure; every page
record carries its page number; `raw_text` is byte-identical to what the extractor
returned; Experiment A is written up with real per-document numbers and named
failure cases; all Phase 1 tests pass and their output is pasted into the commit
or PR.

**Dependencies:** D-10, D-2. **Blocked without the corpus.**

**Risks**
- *The corpus is not ready.* This blocks everything. Mitigation: start with
  3–5 documents to unblock code, but do not report Experiment A numbers on a
  partial corpus and call it final.
- *Scanned documents dominate.* Would force D-6 to be reconsidered. Mitigation:
  check the corpus for image-only pages on day one, before writing extraction code.
- *A second library is needed.* Allowed, but write the justification in
  `evaluation/experiment-a-extraction.md` first (Rule 5).

---

## Phase 2 — Segmentation and dataset preparation

**Goal:** two segmentation methods, both preserving page provenance, with an
honest comparison on a manually annotated sample.

**Required inputs:** Phase 1 page records; the annotation guideline; annotator
time (mine, plus one teammate for the agreement subset).

**Tasks**
1. Implement `services/segmentation.py::segment_paragraphs` — the baseline.
2. Implement `services/segmentation.py::segment_clauses` — numbering detection
   (`3.`, `3.2`, `(a)`, `(iv)`, `Clause 5`, `Section 7`), heading detection,
   schedule/annexure markers, page-break carry-over.
3. Attach `clause_number`, `heading`, `page_start`, `page_end` and the per-page
   offset list to every segment.
4. Assert the invariant: **no segment without a page range**; raise rather than guess.
5. Write the annotation guideline in `data/annotations/README.md`.
6. Annotate the sample (proposed: 10 documents × ~5 pages, covering each document
   type) as `data/annotations/segmentation/*.jsonl`.
7. Double-annotate an overlap subset; compute and report agreement.
8. Write `evaluation/experiment-b-segmentation.py` to compute boundary P/R/F1,
   exact match rate, missed clauses, merges, splits, heading/number preservation,
   page-provenance correctness.
9. Write up Experiment B, including the error taxonomy.

**Expected deliverables**
`backend/app/services/segmentation.py`; `data/annotations/README.md`;
`data/annotations/segmentation/*.jsonl`;
`evaluation/experiment-b-segmentation.py`;
`evaluation/experiment-b-segmentation.md`;
`data/processed/` JSON output per document (both methods).

**Tests**
- Unit: numbered-clause detection across each numbering style, including a style
  not in the corpus (to confirm graceful failure, not a crash).
- Unit: a clause spanning a page break yields `page_start < page_end` and two
  page-offset entries.
- Unit: a segment is never emitted with a missing or zero page number.
- Unit: paragraph and clause methods produce different segment counts on the
  same fixture (guards against the clause method silently falling back).
- Evaluation script runs end to end on the annotated sample.

**Definition of done**
Both methods run on the whole corpus; every segment has a valid page range;
the annotated sample is committed with its guideline; Experiment B reports real
metrics from the committed script and names at least five concrete failure cases;
inter-annotator agreement is reported on the overlap subset.

**Dependencies:** Phase 1.

**Risks**
- *Annotation is slower than expected.* Mitigation: shrink the sample and say so,
  rather than annotating sloppily. A small honest sample beats a large careless one.
- *The temptation to report numbers before annotating.* Forbidden
  (`CLAUDE.md` §14, Rule 1).
- *Clause numbering varies more than expected.* That is a finding — report the
  styles handled and the styles not handled.

---

## Phase 3 — PostgreSQL schema and persistence

**Goal:** the pipeline's output persists, with the integrity constraints that make
bad provenance impossible to store.

**Required inputs:** **D-4** (access layer) and **D-8** (a reachable Postgres)
resolved; the schema in `CLAUDE.md` §11.3 reviewed by the team — particularly by
Person 4, who depends on version and clause semantics.

**Tasks**
1. Confirm Postgres is installed and reachable; document the setup steps.
2. Write `backend/app/db/migrations/001_initial.sql` from `CLAUDE.md` §11.3.
3. Write the migration runner and a `--reset` guarded by an explicit confirmation
   flag (never implicit; `CLAUDE.md` §24 Rule 10).
4. Implement the engine/session module reading `DATABASE_URL` from the environment.
5. Implement `services/persistence.py`: one transaction per version, inserting
   document (if new) → version → pages → segments → segment_pages.
6. Implement duplicate handling: checksum hit returns the existing identifiers
   instead of inserting.
7. Implement `public_id` generation for documents, versions and segments.
8. Add indexes, including the `GIN` full-text index for Person 5.
9. Load the whole corpus through the CLI.

**Expected deliverables**
`backend/app/db/{engine.py,migrate.py,migrations/001_initial.sql}`;
`backend/app/models/`; `backend/app/services/persistence.py`;
a documented local setup procedure; a populated database.

**Tests**
- Migration applies to an empty database and is idempotent on re-run.
- Insert a version; `pages` and `segments` counts match the pipeline output.
- Same file twice → one version row; the second attempt reports the duplicate.
- `CHECK (page_end >= page_start)` rejects an invalid segment.
- `UNIQUE (version_id, segmentation_method, ordinal)` rejects a duplicate ordinal.
- Deleting a version cascades its pages and segments; deleting a document with
  versions is **restricted**.
- A failed ingestion leaves no partially-visible version.

**Definition of done**
A clean database can be created from committed migrations by following the
documented steps; the whole corpus loads without error; every constraint above is
proven by a passing test; no credential appears anywhere in the source tree.

**Dependencies:** Phases 1–2; D-4; D-8; team review of the schema.

**Risks**
- *Postgres not available on a teammate's machine.* Mitigation: documented setup
  plus a tested fallback for local development; if Postgres proves impractical for
  the team, that is a decision to escalate, not to work around silently.
- *Schema churn after Persons 3–5 start coding.* Mitigation: freeze the clause and
  document JSON before Phase 4, and treat removals/renames as announced changes.

---

## Phase 4 — FastAPI endpoints

**Goal:** the published contract runs, is validated, and is tested — the point at
which the other four members stop using stubs.

**Required inputs:** Phase 3 complete; **D-5** (sync vs async ingestion) resolved.

**Tasks**
1. `main.py`, app factory, CORS for the Streamlit origin, structured logging.
2. `core/errors.py`: domain exceptions mapped to the error envelope
   (`CLAUDE.md` §12.1); a global handler that never leaks internals.
3. `schemas/`: Pydantic models for every request and response in `CLAUDE.md` §12.3.
4. `GET /api/health` including the DB check.
5. `POST /api/documents/ingest` wiring validation → pipeline → persistence.
6. `GET /api/documents` with filters and pagination.
7. `GET /api/documents/{document_id}`.
8. `GET /api/documents/{document_id}/pages`.
9. `GET /api/documents/{document_id}/clauses`.
10. Verify the generated OpenAPI docs match `CLAUDE.md` §12; fix whichever is wrong.
11. Write `docs/api-examples.md` with a real captured request/response per endpoint.

**Expected deliverables**
`backend/app/main.py`; `backend/app/api/*`; `backend/app/schemas/*`;
`backend/app/core/errors.py`; `backend/tests/test_api_*.py`;
`docs/api-examples.md`.

**Tests** (each endpoint gets a happy path and at least one error path)
- health: with DB, and with the DB unreachable → 503.
- ingest: valid PDF → 201 with correct counts; non-PDF → 415; oversized → 413;
  duplicate → 409 carrying existing IDs; unknown `document_public_id` → 404;
  zero-text PDF → recorded warnings.
- list: empty corpus → `total: 0`; each filter; pagination boundaries;
  bad `limit` → 422.
- detail: document with two versions, newest first; unknown id → 404.
- pages: full range; subset; `text=raw`; invalid range → 422.
- clauses: both methods; a multi-page clause returning two `pages[]` entries;
  page filter; unknown method → 422.
- Contract test: every documented field is present in the live response.

**Definition of done**
All six endpoints respond as documented; every test above passes with pasted
output; `docs/api-examples.md` contains real captured payloads, not hand-written
ones; no stack trace or file path appears in any error response; the contract in
`CLAUDE.md` §12 matches reality (or has been updated and the change announced).

**Dependencies:** Phase 3; D-5.

**Risks**
- *Contract drift between the doc and the code.* Mitigation: the contract test,
  plus `docs/api-examples.md` being generated from real calls.
- *Large-file timeouts under synchronous ingestion.* Mitigation: the size cap, and
  the CLI path for bulk loading; revisit D-5 only with a measured failure.

---

## Phase 5 — Data quality and evaluation

**Goal:** honest, reproducible reports on what the pipeline does well and where it
fails.

**Required inputs:** a populated database; the Phase 2 annotations; Person 5's
evaluation conventions.

**Tasks**
1. Implement **Experiment C** as `evaluation/data_quality.py`: every check in
   `CLAUDE.md` §15.
2. Wire the critical checks into `pytest` so a regression fails the build.
3. Validate source references at scale: every segment's `page_start`/`page_end`
   within `1..page_count`; every `segment_pages.page_number` matching a real page.
4. Re-run Experiment B against the stored data to confirm nothing was lost in
   persistence.
5. Test failure recovery: interrupt an ingestion and confirm no partial version
   is visible.
6. Write `evaluation/data-quality-report.md` and
   `evaluation/backend-limitations.md`.

**Expected deliverables**
`evaluation/data_quality.py`; `evaluation/data-quality-report.md`;
`evaluation/backend-limitations.md`; a final
`evaluation/experiment-b-segmentation.md`; quality tests in the pytest suite.

**Tests**
- Each quality check has a positive and a negative fixture case.
- A corpus-wide run completes and reports counts per check.
- An interrupted ingestion leaves the database consistent.
- Page-reference validation passes on the full corpus (or the failures are listed
  in the report — either outcome is acceptable; hiding them is not).

**Definition of done**
Every check is automated and runnable with one command; the report states real
counts with the corpus size; limitations are written down plainly; any check that
fails on the real corpus is either fixed or documented as a known limitation with
its cause.

**Dependencies:** Phases 2–4; Person 5's report conventions.

**Risks**
- *The results are unflattering.* Report them anyway — error analysis is what the
  course is marking. A documented failure scores; a hidden one is academic
  dishonesty.

---

## Phase 6 — Integration

**Goal:** Persons 2–5's modules run against the real backend, end to end.

**Required inputs:** Phase 4 stable; the other four members' modules at a
consumable state; the contract frozen.

**Tasks**
1. Publish integration instructions: environment setup, how to run the API, how to
   load the corpus, how to call each endpoint.
2. Support Person 2's frontend integration; fix contract mismatches on my side.
3. Provide Person 3 a stable clause-corpus export for indexing.
4. Provide Person 4 two-version document pairs and confirm `clause_id` stability
   across re-ingestion — **if re-ingestion changes clause IDs, Compare's stored
   results break; this must be stated explicitly in the integration notes.**
5. Support Person 5's full-text index and integration tests.
6. Fix API bugs found by end-to-end testing; add a regression test for each.
7. Update `CLAUDE.md` §21 (status) and §22 (limitations) to reflect reality.

**Expected deliverables**
`docs/integration-guide.md`; a clause-corpus export command; regression tests for
every integration bug found; refreshed status and limitations sections.

**Tests**
- End-to-end: ingest a document → appears in the Library → opens in Document View
  → is retrievable by Ask with a correct citation → is comparable against its other
  version.
- Regression test per integration bug.
- The full suite passes from a clean checkout plus a documented setup.

**Definition of done**
All four members can run their module against the backend using only the written
instructions; the end-to-end walkthrough succeeds and is recorded; the full test
suite passes with pasted output; documentation matches the built system.

**Dependencies:** everyone. This is the phase most exposed to other people's
timelines.

**Risks**
- *Integration left until the final week.* Mitigation: the stub-first contract in
  `docs/architecture.md` §5.1 means integration should be incremental from week 2,
  not a single event in week 4.
- *`clause_id` instability across re-ingestion.* Real risk. Decide early whether
  `public_id` values are regenerated on re-ingest or preserved; Compare and any
  cached evaluation results depend on the answer.

---

## Cross-phase definition of "done" for this whole workstream

1. A clean machine can set up the database and load the corpus from written steps.
2. Every clause in the database traces to a document, version and page range.
3. Six endpoints match the published contract, with tests whose output has been shown.
4. Experiments A, B and C are written up with reproducible numbers from committed
   scripts and committed annotations.
5. `data/README.md` lets someone else rebuild the dataset.
6. Limitations are documented in plain language.
7. No credential is in the repository or its history.
8. I can explain, without notes: why this extraction library, how clause-aware
   segmentation works, what each metric measures, why the schema separates
   documents from versions, and what the system cannot reliably do.
