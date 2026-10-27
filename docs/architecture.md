# NiyamSetu — Technical Architecture

> Written 2026-10-28. Companion to `CLAUDE.md`; this file carries the technical
> detail, alternatives and reasoning. `CLAUDE.md` is the summary reference.
>
> **Status:** every component described here is `[PROPOSED]` or `[PLANNED]`.
> At the time of writing the repository contains `README.md` plus the
> documentation set created in this audit. No application code exists.
> Status labels are defined in `CLAUDE.md` §0.

---

## 1. Architectural goals and constraints

| Goal | Consequence for the design |
|---|---|
| Every output traceable to a document and page | Provenance is a first-class column at every pipeline stage, not an afterthought |
| Explainable in a viva | Plain SQL, visible pipeline stages, no hidden framework magic |
| Five people working in parallel for one month | A single hard interface (the HTTP API) and a frozen JSON contract |
| Honest evaluation | Both segmentation methods stored side by side so comparisons need no re-ingestion |
| Small stack | PostgreSQL + FastAPI + Python stdlib-heavy services; nothing distributed |

Hard constraints:
- Python 3.11 (3.11.9 on the owner's machine).
- PostgreSQL as the only datastore (`psql` not on PATH on this machine —
  unconfirmed, Open Decision D-8).
- No vector database, queue, container orchestration or agent framework without
  a written, agreed justification.

---

## 2. Component responsibilities

### 2.1 Ingestion service (`backend/app/services/`) — Person 1
| Module | Responsibility | Must not |
|---|---|---|
| `validation` | Extension and MIME sniff, size cap, SHA-256 checksum, duplicate lookup | Trust the client-supplied filename or content-type alone |
| `extraction` | Per-page text extraction; record the tool and version used | Merge or reorder pages |
| `normalisation` | Produce `normalized_text` from `raw_text` | Mutate `raw_text` |
| `segmentation` | Produce paragraph segments and clause-aware segments | Emit a segment without a page range |
| `quality` | Run the Experiment C checks, emit warnings | Silently drop a failing document |
| `persistence` | One transaction per version | Leave a half-ingested version visible |

### 2.2 API layer (`backend/app/api/`) — Person 1
Thin routers over the services. Responsibilities: request validation via
Pydantic, mapping domain errors to the error envelope, pagination, and nothing
else. No business logic in routers; no SQL in routers.

### 2.3 Storage layer (`backend/app/db/`, `backend/app/models/`) — Person 1
Table definitions, migrations as numbered `.sql` files, a migration runner, and
query functions. Read access for NLP modules is allowed through the API;
direct read-only SQL is tolerated for batch experiments, **write access is not**.

### 2.4 NLP modules
| Module | Owner | Input | Output |
|---|---|---|---|
| `nlp/classification/` | Person 2 | document title + clause text from the API | `{document_id, label, score, model}` |
| `nlp/rag_qa/` | Person 3 | clause corpus from the API; a user question | `{answer, citations[], sufficient_evidence: bool}` |
| `nlp/extraction_diff/` | Person 4 | clauses of two versions | entities per clause; `{change_type, old_clause_id, new_clause_id, confidence, citations[]}` |
| `nlp/library_search/` | Person 5 | document + clause corpus; a query | ranked `{document_id, score, matched_clause_ids[]}` |

Each module is a plain Python package with a documented function signature and a
CLI entry point, so it can be tested without the frontend.

### 2.5 Frontend (`frontend/`) — Person 2, with Person 5
Streamlit (Open Decision: the brief says Streamlit unless the team agrees
otherwise). Four pages: Library, Document View, Ask, Compare. Talks only to the
HTTP API. Holds no business logic and no document parsing.

### 2.6 Evaluation (`evaluation/`) — all five, convention owned by Person 5
One script per experiment, one dated report per run, inputs read from
`data/annotations/`. A report that cannot be regenerated from committed files
does not count.

---

## 3. Data flow

### 3.1 Ingestion path
```text
client (HTTP multipart) or CLI
  -> validate(file)                 -> 415 / 413 on failure
  -> sha256(file)                   -> 409 if the checksum already exists
  -> resolve document identity      -> existing document_public_id, or create a new document
  -> extract_pages(pdf)             -> [(page_number, raw_text)]
  -> normalise(pages)               -> normalized_text per page
  -> segment_paragraphs(pages)      -> segments[method='paragraph']
  -> segment_clauses(pages)         -> segments[method='clause_aware']
  -> quality_checks(...)            -> warnings[]; may set extraction_status='partial'
  -> persist(...)                   -> ONE transaction; status becomes 'ok' | 'partial' | 'failed'
  -> 201 response with counts and warnings
```
Failure policy: a validation failure stores nothing. An extraction failure
stores the version with `extraction_status='failed'` plus the reason, so the
failure is auditable, and the listing endpoints exclude it by default.

### 3.2 Read path (Ask, as the representative case)
```text
question
  -> P3 retrieval over clauses pulled from GET /clauses?method=...
  -> top-k clauses, each already carrying document_id, version_id, clause_id, page_start, page_end
  -> grounded generation constrained to those clauses
  -> answer + citations[] (CLAUDE.md §12.2), or sufficient_evidence=false
  -> Ask UI renders the answer with clickable citations into Document View at the cited page
```
The citation object is produced from the clause record itself, never
reconstructed by the generator. That is the mechanism that makes citations
checkable: Person 3's test asserts `quote` is a substring of
`segments.text` for the given `clause_id`.

### 3.3 Compare path
```text
(version_a, version_b)
  -> clauses of both from GET /clauses?version_id=...&method=clause_aware
  -> candidate pairing (clause_number match, then lexical/semantic similarity)
  -> change classification: added | removed | modified | unchanged | unmatched
  -> per-change evidence: both clause records and their page refs
  -> Compare UI, with unmatched/low-confidence pairs visually separated
```
`unmatched` is a first-class output. A clause that cannot be aligned is reported
as unaligned, never force-paired to produce a tidier diff.

---

## 4. Database relationships

See `CLAUDE.md` §11.3 for the proposed DDL. The reasoning behind the shape:

### 4.1 Why `documents` and `document_versions` are separate
A policy identity persists across amendments; a version is one published issue.
Separating them is what makes Compare expressible as a query
(`two versions of one document_id`) instead of a fuzzy document-matching problem.

### 4.2 Why the file lives on the version, not in its own table
A physical file is 1:1 with the version we ingested it as. A separate `files`
table would add a join for no behaviour we need. `file_checksum UNIQUE` on
`document_versions` gives duplicate detection directly. If we ever need to
record several files per version (an annexure published separately), that
becomes a `version_files` table — an additive change.

### 4.3 Why `segments` carries `segmentation_method` rather than two tables
Both methods produce rows of identical shape, and every consumer selects one
method at a time. One table with a `method` column plus
`UNIQUE (version_id, segmentation_method, ordinal)` keeps the comparison
experiment to a `WHERE` clause. Two tables would duplicate every index, query
and API handler.

### 4.4 Why `segment_pages` exists
`page_start`/`page_end` is enough to cite a clause, but not to highlight it when
it spans a page break. `segment_pages` records each touched page with character
offsets. Keeping both is a deliberate redundancy: the cheap fields serve the
common case (citations), the detail table serves highlighting, and the pair is
the documented answer to the multi-page provenance requirement.

**Known limitation:** offsets are into `pages.raw_text`. They do not map onto
`normalized_text` after normalisation, so highlighting over normalised text is
approximate. An offset map per page would fix this; not planned for the MVP.

### 4.5 Version ordering
`supersedes_version_id` is an explicit, human-recorded link.
`publication_date` is nullable and is only a sort hint. We never infer "this is
the newer version" from a date alone, and never infer "these are versions of the
same policy" from title similarity (Open Decision D-9).

### 4.6 Integrity rules
- `ON DELETE RESTRICT` from `document_versions` to `documents`: you cannot
  delete a policy that still has versions.
- `ON DELETE CASCADE` from `pages` / `segments` to `document_versions`:
  re-ingesting a version replaces its derived content cleanly.
- `CHECK (page_end >= page_start)` and `CHECK (page_number >= 1)`: invalid page
  references are rejected by the database, not just by application code.
- `extraction_status` gates API visibility, so a `failed` version is retained
  for audit without polluting the corpus.

---

## 5. API boundaries

The HTTP API is the **only** supported integration surface. The contract
(paths, payloads, error envelope, citation format) lives in `CLAUDE.md` §12 and
is versioned there.

Boundary rules:
1. Database integer IDs never appear in a payload; `public_id` strings do.
2. Page references are 1-based and inclusive at both ends, everywhere.
3. Error responses always use the single envelope shape — no endpoint invents
   its own error format.
4. Removing or renaming a field requires notifying all four consumers before
   merge; adding a field does not.
5. `metadata` on a clause is a reserved extension point so downstream modules
   can attach labels without a schema migration.

### 5.1 Stub-first integration
Persons 2–5 should not wait for a running server. The contract is complete
enough to write a local stub that returns fixture JSON in the documented shape.
When the real API lands, only the base URL changes. This is the main mechanism
that keeps five people unblocked in a one-month project.

---

## 6. NLP module boundaries

| Boundary | Rule |
|---|---|
| NLP → DB | Read-only, and only for batch experiments. Never write to document / version / page / segment tables. |
| NLP → API | The normal path. Modules consume documented JSON. |
| NLP → NLP | No direct imports across modules. If Person 3 needs Person 4's entities, it goes through a documented JSON file or an agreed API field. |
| NLP → provenance | A module may add fields to a record; it may never drop `document_id`, `version_id`, `clause_id` or page references. |
| Model artefacts | Live inside the owning module's directory, git-ignored if large, with a documented way to regenerate them. |

---

## 7. Integration points and ownership

| Integration point | Produced by | Consumed by | Risk if it drifts |
|---|---|---|---|
| Clause JSON (`CLAUDE.md` §12.3) | P1 | P2, P3, P4, P5 | Four modules break at once — freeze early |
| Citation JSON (§12.2) | P1's data, emitted by P3 and P4 | Frontend | Uncheckable citations; the project's core claim fails |
| Error envelope (§12.1) | P1 | Frontend, all modules | Inconsistent error UI; brittle clients |
| `segmentation_method` values | P1 | P3 (chunking study), P5 (error propagation) | Experiments become incomparable |
| Document metadata fields | P1 (schema), corpus collector (values) | P2 filters, P5 search | Filters silently empty |
| Annotation file format | P1 (segmentation), P5 (convention) | all evaluation scripts | Metrics not reproducible |
| Change-type vocabulary (`added`/`removed`/`modified`/`unmatched`) | P4 | Frontend | Compare UI misreports confidence |

---

## 8. Architecture decisions and alternatives

Each decision records what we chose, what we rejected, and why — this table is
the viva answer to "why did you build it this way".

### AD-1 — PostgreSQL as the single datastore
**Chosen:** PostgreSQL for documents, pages, clauses and full-text search.
**Rejected:** SQLite (weaker full-text and JSON story, though simpler to set up);
a document store (we have genuinely relational data: document → version → page →
clause); a vector DB alongside Postgres (premature; see AD-5).
**Cost:** every team member needs a reachable Postgres instance (D-8).

### AD-2 — SQLAlchemy Core + hand-written SQL migrations
**Chosen:** Core for connections and parameterised queries; numbered `.sql`
migrations applied by a small runner.
**Rejected:** full ORM (hides the schema we are graded on explaining); Alembic
autogenerate (another tool's semantics to learn for about eight tables); raw
psycopg everywhere (loses pooling and composability).
**Revisit if:** migrations become frequent and interdependent → adopt Alembic
(Open Decision D-4).

### AD-3 — Store both segmentation methods for every version
**Chosen:** run paragraph and clause-aware segmentation at ingestion, store both.
**Rejected:** store the "best" method only (destroys the comparison the project
is graded on); segment on demand (non-deterministic results across experiments).
**Cost:** roughly double the segment rows. Negligible at corpus scale, and it is
what makes Experiment B and Person 3's chunking study cheap.

### AD-4 — Synchronous ingestion for the MVP
**Chosen:** ingest inside the request; return counts and warnings.
**Rejected:** background worker + job polling. It is the right answer at scale
and for scanned files, but it adds a worker lifecycle, a polling endpoint and
flaky tests to a one-month project.
**Mitigation:** `ingestion_jobs` is already in the schema, and a CLI ingestion
path exists for bulk loading, so switching later is additive (D-5).

### AD-5 — No vector database
**Chosen:** Person 3 keeps whatever index they need (BM25 structures, embeddings)
inside `nlp/rag_qa/`, loaded from the clause corpus.
**Rejected:** pgvector or a standalone vector store as the default. At a
20–40 document corpus, an in-process index is faster to build, trivially
reproducible, and easier to explain. pgvector becomes justified only if we
measure a concrete need (D-7).

### AD-6 — Raw text and normalised text both stored
**Chosen:** two columns per page; normalisation never mutates raw.
**Rejected:** normalise in place (irreversible, and destroys the evidence that
extraction artefacts existed); normalise on read (repeated cost, and the
reported numbers then depend on when you read).
**Benefit:** Experiment A can quantify what normalisation changed.

### AD-7 — Streamlit frontend
**Chosen (provisionally):** Streamlit, per the working brief.
**Rejected:** a React SPA — more capable, but a month-long project does not have
the budget for a separate frontend build chain, and the brief's priority is the
NLP work. **Needs team confirmation**, since a Compare view with aligned
side-by-side clause highlighting is genuinely awkward in Streamlit; that
limitation should be accepted explicitly rather than discovered in week four.

### AD-8 — The HTTP API as the only hard interface
**Chosen:** one documented contract, stub-first integration.
**Rejected:** shared Python imports across members' modules (couples five
people's refactors together); a shared database with everyone writing (nobody
can reason about provenance invariants any more).

### AD-9 — Exclude scanned PDFs from the MVP corpus
**Chosen (proposed):** detect image-only pages, warn, mark the version
`partial`, and keep scanned documents out of the evaluated corpus.
**Rejected:** OCR in the MVP. Tesseract plus layout recovery is its own project,
and OCR noise would contaminate every segmentation metric we report.
**Record it as a limitation, not a silent gap** (D-6).

---

## 9. What this architecture deliberately does not do

- No authentication or multi-user accounts — out of scope for the course project.
- No document crawling or scheduled monitoring of government sites; the corpus is
  collected by hand, with provenance recorded.
- No write path for NLP modules into document tables.
- No cross-document knowledge graph.
- No production deployment story (no containers, no CI/CD beyond running tests).
- No guarantee of correctness on document layouts outside the curated corpus.
