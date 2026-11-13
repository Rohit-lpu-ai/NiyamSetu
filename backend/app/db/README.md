# Database — Phase 3, implemented and verified

> **Status: `[IMPLEMENTED]`** as of 2026-11-12 (Work Package 1).
>
> Both migrations have been **applied** to PostgreSQL 18.6, the four-version
> RBI corpus is **loaded**, and the full suite is **462 passed, 0 skipped**.
> Measured: 4 documents, 4 versions, 57 pages, 151 removed header lines, 501
> segments (138 paragraph + 363 clause-aware), 552 `segment_pages` rows, 45
> page-spanning segments. Every count matches the JSON artefacts exactly, and
> all 501 segments resolve from their stored offsets *read back out of the
> database*.
>
> Results, including the two defects found on first apply:
> `evaluation/2026-11-12-phase3-postgresql.md`. Viva-facing explanation of every
> design decision: `docs/phase3-postgresql.md`.
>
> `SQLAlchemy==2.0.46` and `psycopg[binary]==3.2.12` are pinned. The access
> layer is **Core only** — no ORM, no Alembic (D-4).
>
> Run it again from scratch:
>
> ```
> .venv\Scripts\python -m backend.app.db.migrate
> .venv\Scripts\python -m backend.app.db.loader
> .venv\Scripts\python -m pytest
> ```
>
> **Resolved:**
> - **D-8** — PostgreSQL 18 installed, running, and now actually connected to.
>   Databases `niyamsetu` and `niyamsetu_test` exist. Other machines still open.
> - **D-11 / `offset_basis`** — `normalized_text`, verified twice: 501/501 from
>   the artefacts and 501/501 from the database. 14 resolve against
>   `cleaned_text`, 0 against `original_text`.
>
> **Still open:** D-10 (four documents, one authority, zero version pairs), and
> therefore every claim about Compare.
>
> The sections below were written as a *proposal* before Phase 3 and are kept
> because the reasoning is still the reasoning.

---

## 1. Why this directory exists before Phase 3 does

`CLAUDE.md` §12 and `docs/pdf-extraction.md` §12 both argue the same thing: the
hardest part of the schema is the segment tables, and they are easier to design
against **real segments** than against a sketch. That was the reason for doing
segmentation before the database.

Real segments now exist — 501 of them, across 4 versions, with hierarchy, page
spans, marker styles and a removal audit trail. So the schema is written now,
against the measured output, and committed for review. Writing it is not the
same as applying it.

### What has to happen before this is applied

| | |
|---|---|
| **D-4** — SQLAlchemy Core + plain SQL migrations, not Alembic | **answered.** Confirmed by the project owner. This directory follows it. |
| **D-8** — is PostgreSQL installed and reachable on all five machines? | **partly answered.** PostgreSQL 18 is installed and running on the owner's machine (`psql` is merely not on PATH). The other four machines are still unknown. |
| **D-10** — which documents form the corpus? | **open.** 4 validation documents exist; the corpus does not. |
| Owner review of Phase 2's segmentation output | **pending.** |
| The `offset_basis` contract point (§3 below) | **answered and verified** as `normalized_text` (501/501 segments resolve). Still wants explicit team sign-off, since four consumers read it. |

---

## 2. What is here

```text
backend/app/db/
├── README.md                          # this file
├── __init__.py
├── config.py                          # env/.env config; no credentials in source
├── engine.py                          # SQLAlchemy Core engine; explicit transactions
├── migrate.py                         # the migration runner (+ --status, --dry-run)
├── loader.py                          # data/processed/*.json -> PostgreSQL
├── repository.py                      # read queries in the CLAUDE.md §12 shapes
└── migrations/
    ├── 0001_initial_schema.sql        # 7 tables, 1 view, 4 triggers, 15 indexes
    └── 0002_public_id_sequences.sql   # public_id allocation
```

`0001_initial_schema.sql` creates seven tables, one view, four trigger
functions and fifteen indexes, in a single transaction. Every column carries an
inline comment saying why it is there, and the measurement that justified it
where there was one.

`0002_public_id_sequences.sql` adds the sequences and column defaults that
allocate `public_id` values, so the loader never has to write a placeholder and
then update it. See that file's header for why a generated column cannot do
this.

The five Python modules were added in Work Package 1, together with
`SQLAlchemy==2.0.46` and `psycopg[binary]==3.2.12` in `requirements.txt`. All
five have since been **run against PostgreSQL 18.6** — see the status block at
the top of this file.

**Not here:** anything FastAPI. The API lives in `backend/app/api/` (added in
Work Package 2), and `repository.py` deliberately stops at returning plain
dicts in the agreed payload shapes so the routers have nothing to do but
serialise them.

---

## 3. The one contract change that needs team sign-off

`document_versions.offset_basis`.

`CLAUDE.md` §11.4 specifies `char_start` / `char_end` as offsets into
`pages.raw_text`. **The pipeline produces offsets into `normalized_text`**, and
this schema records which basis was used per version rather than assuming one.

Why the pipeline does it that way:

- Segmentation has to run on normalised text to work at all — clause detection
  matches on line starts, and raw text carries whitespace artefacts and
  inconsistent indentation that defeat the patterns.
- `normalization.collapse_blank_lines` changes a page's **line count**, so raw
  and normalised text share no line numbering. Translating an offset between
  them needs a per-page offset map, which §11.4 itself puts out of MVP scope.
- The API serves `text=normalized` by default (§12.3), so offsets on the same
  basis as the displayed text are exactly right for the common path. §11.4's
  approximation moves to the `text=raw` path instead — the less-used direction.

So the approximation in §11.4 is not removed, it is **inverted**, and the trade
is now explicit and recorded per version instead of implied. The segmentation
CLI's `--use-raw-text` flag produces the other basis, which is why the column
exists rather than a global constant.

This is a change to a documented contract that four other modules read. It is
flagged here, in `docs/phase2-segmentation.md`, in the segmentation module
docstring and in the committed segment JSON. It should be agreed before Phase 3
loads anything.

---

## 4. Seven things the §11.3 sketch did not anticipate

Each one came out of running segmentation on the real documents.

| # | Addition | The measurement that forced it |
|---|---|---|
| 1 | `segments.parent_id` | Clause hierarchy to depth 4; 241 of 254 segments in the capital-adequacy document have a parent. Person 2 cannot render a clause tree without it and Person 4 cannot align sub-clauses. |
| 2 | `segments.segment_type` | The sketch assumed every segment is a clause. Reality: clause 355, paragraph 138, preamble 4, annexure 3, heading 1. Unattributable text is kept, not discarded, so the distinction must be storable. |
| 3 | `segments.level`, `.marker_style`, `.clause_number_ambiguous` | Needed for per-style accuracy in Experiment B, and to explain a nesting decision afterwards. 31 bracketed markers across the corpus are genuinely ambiguous between a roman numeral and a list letter (21 in the capital-adequacy amendment, 10 in the commercial-banks amendment). |
| 4 | `segment_pages.line_index_start/end` | The sketch had character offsets only. Line indices are what the preview highlights with, and what locates a removed header line relative to a segment. |
| 5 | `document_versions.offset_basis` | §3 above. |
| 6 | **`page_removed_lines`** — a whole table | Cleaning is the only stage that *deletes* text, and its guarantee is reversibility. If that trail lives only in a JSON file, a database deployment has silently lost source evidence. 151 rows on the current corpus. |
| 7 | `segments.text_search` as a stored generated column | Rather than an expression index, so Person 5's keyword baseline has a stable column to select and rank on, and the tsvector configuration is recorded in one place. |

---

## 5. Invariants the database enforces, not just the code

The point of putting these in the schema is that they then hold for data
arriving by any route — a loader, a hand-written `INSERT`, a future API — not
only through `segmentation.py`.

| Invariant | Mechanism |
|---|---|
| `pages.raw_text` is immutable | `BEFORE UPDATE` trigger. Normalisation is additive (`CLAUDE.md` §10.2); this is the project's most load-bearing claim and it is enforced rather than trusted. |
| No segment without page provenance | Deferred constraint trigger, checked at `COMMIT` — which is when "one version per transaction" makes the answer knowable. |
| A segment's page range agrees with the pages its offsets touch | Trigger on `segment_pages`. A cross-row invariant a `CHECK` cannot express. |
| A character offset never points past the end of its page | Same trigger, resolving against the version's `offset_basis`. |
| A segment and its page belong to the same version | Same trigger. Catches a cross-version provenance bug that would otherwise produce a confidently wrong citation. |
| No empty segments, no reversed page ranges, no negative offsets | `CHECK` constraints. |
| The paragraph baseline never acquires clause numbers or headings | `CHECK`. It is the control condition for Experiment B; if it drifted, the comparison would be meaningless. |
| A removed line always records its reason and its evidence | `NOT NULL` plus a non-blank `CHECK`. A line removed without stated evidence is indistinguishable from a bug. |
| A version never supersedes itself | `CHECK`. Longer cycles are not expressible as a constraint and are caught by `evaluation/data_quality.py` instead. |
| A failed ingestion job always says why | `CHECK`. |

Where the database can enforce something `evaluation/data_quality.py` currently
only *reports*, both exist on purpose: the checks run against the JSON
artefacts, which have no engine to enforce anything.

---

## 6. Identifiers — two schemes, coexisting on purpose

Every table has both:

- **`public_id`** — `DOC-0001`, `VER-0001`, `CLS-0000123`. Assigned by the
  database. This is what API payloads carry and what a citation names
  (`CLAUDE.md` §12).
- **`fingerprint`** — `DOC-<16 hex>`, `VER-<16 hex>`, `SEG-<16 hex>`.
  Content-derived by `backend/app/document_processing/identity.py`. This is how
  a re-ingested file is recognised as one already seen, and how a re-run of
  segmentation lands on the same rows.

They are not redundant. The fingerprint answers "have I seen this before"
without comparing text; the sequence answers "what do I call it". `identity.py`
§"Bridging to PostgreSQL" set this out before the schema existed, and the
schema keeps it.

`segments.fingerprint` is keyed on `(version_id, method, ordinal)` and
**deliberately not on the text** — hashing the text would mean a whitespace fix
in normalisation changed every segment id, invalidating every committed
annotation and every citation that referenced one.

---

## 7. How Phase 3 would proceed

Sketched so the next step is obvious, not as a commitment.

1. **Answer D-8.** Confirm PostgreSQL is installed and reachable on each
   machine, and agree one connection convention via `.env` (with
   `.env.example` committed, `.env` git-ignored — `CLAUDE.md` §20).
2. **Pin the dependencies** in `requirements.txt`, in the commit that first
   uses them: `SQLAlchemy` (Core only) and `psycopg2-binary`.
3. **Write the runner** — `backend/app/db/migrate.py`. Applies each
   `migrations/*.sql` in filename order inside one transaction per file,
   recording filename and SHA-256 in `schema_migrations`. Refuses to re-apply a
   file whose checksum has changed since it was applied, because a silently
   edited migration means two databases with the same recorded history and
   different shapes.
4. **Review this schema against §4 and §5**, then apply it.
5. **Write the loader** — reads `data/processed/<version_id>/*.json` and
   inserts one version per transaction, so a partially-ingested version is
   never visible (`CLAUDE.md` §10.2). The JSON files stay the intermediate
   representation, which keeps ingestion and persistence separately testable.
6. **Re-run Experiment B against the stored data** to confirm nothing was lost
   in persistence — that is already a Phase 3 task in
   `docs/backend-roadmap.md`, and the point of it is precisely that this schema
   is unverified until something has round-tripped through it.
7. **Add the data-quality checks as SQL** alongside the JSON ones, so the same
   invariants are checked on both representations.

---

## 8. Known gaps in this proposal

Stated because an unreviewed schema that claims to be finished is worse than
one that names its holes.

- **It has never been executed.** There may be syntax errors. PostgreSQL is not
  reachable here, so no test covers this file, and nothing in the suite would
  fail if it were wrong.
- **`to_tsvector('english', …)`** is the wrong configuration for the Devanagari
  text that survives into some preamble segments. It is also moot — no
  extractor available to this project reads that Devanagari correctly
  (`evaluation/experiment-a-extractor-comparison.md` §3) — but it would matter
  if a genuinely Hindi document entered the corpus. Multilingual work is paused
  by decision (D-3).
- **No `pgvector`, no embeddings.** Person 3 keeps a retrieval index inside
  `nlp/rag_qa/` (D-7). Adding a vector column needs measured need and a written
  justification first (`CLAUDE.md` §24 rule 5).
- **No row-level security, no multi-tenancy, no auth tables.** Out of scope for
  a coursework project with a single read-only corpus.
- **Cycle detection on `supersedes_version_id` is partial.** A one-row cycle is
  a `CHECK`; longer chains need a recursive query, which lives in
  `evaluation/data_quality.py`.
- **No annotation tables.** `data/annotations/` is committed JSONL and read by
  the scoring script directly. Whether annotations belong in the database is a
  Phase 5 question nobody has asked yet.
- **`segment_citations` filters on `extraction_status = 'ok'`**, so a `partial`
  version's clauses are not citable. That is the conservative reading and it
  should be checked with Person 3 — it is a product decision, not a schema one.
