# Phase 3 — PostgreSQL persistence: design, rationale, viva preparation

**Audience:** the project owner preparing for a viva, and the four teammates
who will read this data through the Phase 4 API.
**Status:** see §0. Written 2026-11-12.

Every section below follows the same shape: **simple explanation**, **technical
explanation**, **a small example**, and **a likely viva question with an
answer**. Nothing here describes functionality that does not exist — if a
feature is planned rather than built, it is named as planned and not explained
as if it worked.

---

## 0. What exists

**All of it is applied, loaded and tested** as of 2026-11-12.

| Thing | Status |
|---|---|
| `0001_initial_schema.sql` — 7 tables, 1 view, 4 triggers, 15 indexes | `[IMPLEMENTED]` — applied to PostgreSQL 18.6 |
| `0002_public_id_sequences.sql` — public_id allocation | `[IMPLEMENTED]` — applied |
| `config.py` / `engine.py` / `migrate.py` / `loader.py` / `repository.py` | `[IMPLEMENTED]` |
| Tests | **462 passed, 0 skipped** |
| Corpus in the database | 4 documents, 4 versions, 57 pages, 501 segments, 552 `segment_pages`, 151 removed lines |
| FastAPI endpoints | `[PLANNED]` — Phase 4, not started |
| `supersedes_version_id` population | `[PARTIAL]` — column, constraints and tests exist; no corpus document supersedes another (D-10), so nothing populates it |

Run it:

```
copy .env.example .env          # then edit in your password
.venv\Scripts\python -m backend.app.db.migrate --status
.venv\Scripts\python -m backend.app.db.migrate
.venv\Scripts\python -m backend.app.db.loader
.venv\Scripts\python -m pytest
```

Measured results and the defects found on first apply:
`evaluation/2026-11-12-phase3-postgresql.md`.

---

## 1. Why PostgreSQL

**Simple.** The data is highly relational — documents have versions, versions
have pages, pages contain parts of clauses — and we need the database itself to
refuse bad data, not just hope the application is careful.

**Technical.** Three capabilities decided it, each of which we actually use:

1. **Declarative integrity**: foreign keys, `CHECK`, `UNIQUE`, and — critically
   — `DEFERRABLE INITIALLY DEFERRED` constraint triggers. The provenance
   guarantee ("no clause without a page") is only checkable *at commit*, once
   both the segment and its page rows exist. SQLite cannot express a deferred
   constraint trigger; MongoDB cannot express the foreign keys at all.
2. **Transactional DDL**: a migration that fails rolls back completely, so the
   schema is never half-applied.
3. **Full-text search in the database**: `TSVECTOR` + a GIN index gives
   Person 5 a keyword-search baseline with no extra service, and `JSONB` gives
   the `metadata` column for downstream annotation without a schema change.

**Example.** This insert is refused by the database, not by Python:

```sql
INSERT INTO segments (fingerprint, version_id, segmentation_method, ordinal,
                      segment_type, text, page_start, page_end)
VALUES ('SEG-9999999999999999', 1, 'clause_aware', 99, 'clause',
        'orphan with no provenance', 1, 1);
-- ERROR: segment SEG-9999999999999999 has no segment_pages row;
--        a segment without page provenance must not be stored
```

**Viva Q: "Why not SQLite? It is simpler and needs no server."**
A: SQLite would have been simpler to set up and we would have lost the one
guarantee the project is built on. Our central invariant is that no clause
exists without page provenance, and that can only be checked at commit time,
after both the segment and its `segment_pages` rows are inserted. PostgreSQL's
deferred constraint triggers express exactly that; SQLite has no equivalent, so
the check would have moved into application code — where it is a convention
that the next person can forget, rather than something the database enforces.

---

## 2. Why SQLAlchemy Core rather than the ORM

**Simple.** We wanted the SQL to stay visible. An ORM writes the SQL for you,
which is convenient until you have to explain what ran.

**Technical.** Core gives us the two things we actually need — connection
pooling and parameter binding — without an identity map, lazy loading, or
`Session` lifecycle. Consequences we chose on purpose:

- **No hidden queries.** There is no lazy relationship that fires a `SELECT` on
  attribute access, so the number of round trips is whatever the code shows.
  In `repository.get_segments` this is explicit: one query for the segments,
  then **one** query for all their page rows via `page_id = ANY(:ids)`, rather
  than the 254 queries a naive per-segment loop would issue.
- **One definition of the schema.** The tables are defined once, in the `.sql`
  migrations. We deliberately did *not* also declare `Table()` objects in
  Python, because two descriptions of one schema drift apart.
- **Parameters are always bound.** Every statement uses `text()` with named
  parameters, so injection is structurally impossible (`CLAUDE.md` §20).

**Example.**

```python
conn.execute(
    text("SELECT text FROM segments WHERE version_id = :vid AND ordinal = :n"),
    {"vid": version_id, "n": ordinal},   # bound, never interpolated
)
```

**Viva Q: "Isn't writing raw SQL a step backwards from an ORM?"**
A: For an application that mostly reads documents by id and page range, the ORM
buys abstraction we do not need and costs explainability we do need. The
decisive case was `segment_pages`: its composite key, its denormalised
`page_number`, and its validation trigger are all things I would have had to
fight an ORM to express, and all things the plain SQL states in a form I can
defend line by line. Core still gives pooling and bound parameters, which are
the parts that genuinely prevent bugs.

---

## 3. Why plain SQL migrations rather than Alembic

**Simple.** The migrations are files of SQL applied in filename order, and a
small runner that records which ones have run.

**Technical.** `migrate.py` lists `migrations/NNNN_*.sql`, compares it with the
`schema_migrations` table, and applies the difference. Two deliberate details:

- **The filename shape is enforced.** Apply order is lexical, so `2_fix.sql`
  would sort *after* `10_later.sql`. The runner rejects an unpadded name rather
  than silently applying migrations in the wrong order.
- **The checksum of every applied file is stored.** If an already-applied
  migration is edited, the runner refuses to continue instead of reporting the
  database as up to date. That is the failure Alembic's autogenerate makes easy
  to create and hard to notice.

Alembic was available and rejected (D-4, confirmed by the owner): its value is
autogenerated diffs from ORM models, and we have no ORM models to diff.

**Example.**

```
$ python -m backend.app.db.migrate --status
migration                                status
--------------------------------------------------------
0001_initial_schema.sql                  applied
0002_public_id_sequences.sql             PENDING
```

**Viva Q: "How do you roll a migration back?"**
A: We do not, and that is a deliberate limitation rather than an oversight.
There is no `down` script. For a one-month project with a corpus that can be
re-derived from the PDFs in minutes, the recovery path is "drop the database,
migrate, reload" — which is tested, because that is exactly what the test
fixture does on every run. Writing reverse migrations we never exercise would
be ceremony that fails the first time it is genuinely needed.

---

## 4. Why `documents` and `document_versions` are separate

**Simple.** "The Capital Adequacy Directions" is one *policy*. The 2026 11th
Amendment is one *version* of it. Compare needs to ask "what changed between
two versions of the same policy", which is only answerable if the policy has an
identity separate from its issues.

**Technical.** `documents` holds the logical identity (title, department, type,
reference number). `document_versions` holds everything that varies per
publication (file, checksum, page count, publication date, extraction status,
`offset_basis`) plus a nullable self-reference `supersedes_version_id`.

Two rules follow, both of which the schema enforces:

- **Similar titles do not imply a version relationship.** A version joins a
  document because the manifest's `document_group` says so — a value a human
  wrote down. The loader matches on the fingerprint derived from it and never
  on title similarity (D-9).
- **Publication date does not establish version order.** It is nullable and a
  sortable hint only; `version_label` and `supersedes_version_id` carry the
  real ordering.

**Example.** Two files, one policy:

| documents | document_versions |
|---|---|
| `DOC-0001` "Capital Adequacy Directions" | `VER-0001` label `2026-10th-amendment`, checksum `a1b2…` |
| | `VER-0002` label `2026-11th-amendment`, checksum `c3d4…`, supersedes `VER-0001` |

**Viva Q: "Why not one table with a `version` column?"**
A: Because then every per-policy fact — title, department, reference number —
is duplicated on each version and can disagree between them, and there is no
single row to hang "this is the same policy" on. The split also makes
`UNIQUE (document_id, version_label)` expressible, which is what stops the same
label being used twice for one policy. The honest caveat: our corpus currently
has four documents and **zero** version pairs (D-10), so this design is
defensible on reasoning but not yet exercised on real amendment chains.

---

## 5. Why pages are stored separately

**Simple.** Every citation this project makes is "document, version, **page**".
If page boundaries are not stored, they cannot be cited.

**Technical.** `pages` is one row per physical PDF page per version, holding
three parallel texts:

| column | what it is | why it exists |
|---|---|---|
| `raw_text` | the extractor's verbatim output | the evidence; **immutable**, enforced by a trigger |
| `normalized_text` | NFKC, de-hyphenated, whitespace-collapsed | what segmentation ran on, and **the offset basis** |
| `cleaned_text` | normalised minus running headers/footers | what the segmenter read; stored so offsets can be checked |

`UNIQUE (version_id, page_number)` makes a page number unambiguous within a
version. `page_number` is the **physical** PDF page, never the number printed
on the page — where they differ, that is a manifest note, not a correction.

**Example.** The immutability trigger firing:

```sql
UPDATE pages SET raw_text = 'rewritten' WHERE id = 1;
-- ERROR: pages.raw_text is immutable (page 1 of version 1);
--        normalisation is additive and must write to normalized_text
```

**Viva Q: "Why keep three copies of the same text? That is wasteful."**
A: They are not copies, they are three different claims, and collapsing them
would destroy the audit trail. `raw_text` is what the PDF gave us —
`CLAUDE.md` §10.2 forbids ever overwriting it, because every downstream claim
of traceability bottoms out in text that was never altered. `normalized_text`
is what the offsets are measured against. `cleaned_text` is what the segmenter
actually read, after headers were removed. Storage is ~1.5 MB for 57 pages; the
alternative is being unable to answer "did normalisation break this clause?"

---

## 6. Why paragraph and clause-aware are separate methods, stored side by side

**Simple.** They are two competing answers to "where does one chunk of this
document end". We store both so they can be compared, and so a downstream
module can pick.

**Technical.** `segments` is keyed
`UNIQUE (version_id, segmentation_method, ordinal)`. Both methods' output
coexists for the same version. Measured on the corpus: 138 paragraph segments
and 363 clause-aware segments over the same 57 pages.

This is what makes two experiments possible without re-ingesting anything:
Person 3 can measure retrieval quality against both chunkings, and Person 5 can
trace error propagation. It is also why `segmentation_method` is part of the
uniqueness key rather than a filter applied afterwards — ordinal 0 exists twice
per version, once per method, and both are correct.

A `CHECK` additionally forbids the paragraph method from carrying clause
structure, so the two cannot be quietly conflated.

**Example.**

```sql
SELECT segmentation_method, count(*), max(page_end) FROM segments
 WHERE version_id = 1 GROUP BY segmentation_method;
--  paragraph    |  74 | 37
--  clause_aware | 254 | 37
```

**Viva Q: "Which method is better?"**
A: On our measured sample, neither dominates — and the honest answer has to
name the sample size. Boundary F1 was 0.865 for the paragraph baseline against
0.714 for clause-aware, so the baseline finds *boundaries* better; but
clause-number recovery was 0.0% against 92.9%, so only the clause-aware method
produces citable clause numbers. Page provenance was 100% for both. That is
from a 4-page, 21-clause sample annotated by a single annotator who is not
independent of the system, with inter-annotator agreement still `PENDING`, so
it indicates a tendency and does not settle the question. Storing both is
partly a hedge against exactly that uncertainty.

---

## 7. Why `normalized_text` is the offset basis (D-11)

**Simple.** A clause's character offsets have to count into *some* specific
version of the page text. We chose the normalised text, and every version
records that choice in a column so nobody has to guess.

**Technical.** `CLAUDE.md` §11.4 originally specified offsets into `raw_text`.
The pipeline produces offsets into `normalized_text`, for two reasons:

1. Normalisation changes a page's **line count** (`collapse_blank_lines`), so
   raw and normalised text share no line numbering. Mapping between them needs
   a per-page offset map that §11.4 puts out of MVP scope.
2. The API serves `text=normalized` by default (§12.3), so offsets on the same
   basis as the displayed text are correct for the common path.

The trade is therefore **inverted, not removed**: highlighting against
`raw_text` is now the approximate direction. It is recorded per version as
`document_versions.offset_basis`, constrained to
`('normalized_text', 'original_text')`, because the segmentation CLI's
`--use-raw-text` can produce the other basis.

**This is empirically verified, not just declared.** Applying the resolution
rule to each candidate basis across all 501 segments:

| basis | segments that resolve |
|---|---:|
| `normalized_text` | **501 / 501** |
| `cleaned_text` | 14 / 501 |
| `original_text` | 0 / 501 |

**Example.** Resolving a clause from its stored offsets:

```sql
SELECT substring(p.normalized_text FROM sp.char_start + 1
                 FOR sp.char_end - sp.char_start)
  FROM segment_pages sp JOIN pages p ON p.id = sp.page_id
 WHERE sp.segment_id = 42 ORDER BY sp.page_number;
```

Note `char_start + 1`: our offsets are 0-based half-open Python slices, while
SQL `substring` is 1-based. That off-by-one is exactly the kind of error the
`offset_resolution` check exists to catch.

**Viva Q: "What breaks if a consumer assumes raw-text offsets?"**
A: Their highlight lands in the wrong place, silently — and only 14 of 501
clauses would look correct, which is the dangerous part: a developer spot-
checking two or three could conclude it worked. That is why the basis is a
`NOT NULL` column on every version rather than a constant in a document, and
why the loader *refuses* a segment file declaring a different basis instead of
loading offsets the schema would then misinterpret.

---

## 8. Why `segment_pages` is needed

**Simple.** `page_start` and `page_end` tell you a clause spans pages 3–4.
They do not tell you *which part* is on page 3. `segment_pages` does.

**Technical.** One row per page a segment actually touches, carrying the
character range and the line range on that page. 45 of our 501 segments span a
page break; the longest spans 5 pages.

Both representations are kept because they answer different questions at
different costs: `page_start`/`page_end` answers "where is this clause" with no
join, which is what a citation needs; `segment_pages` answers "which pages, and
where in each", which is what highlighting needs.

Three integrity properties, all enforced:

1. `PRIMARY KEY (segment_id, page_number)` — a page cannot be listed twice.
2. A per-row trigger checks that the denormalised `page_number` matches the
   referenced `pages` row, that the page belongs to the **same version** as the
   segment, and that `char_end` is within that page's text under the version's
   `offset_basis`.
3. A deferred constraint trigger requires at least one row per segment (§9).

**Example.** A clause spanning two pages:

```
segments:       id=42  page_start=1  page_end=2
segment_pages:  segment_id=42  page_number=1  char_start=1719  char_end=1955
                segment_id=42  page_number=2  char_start=119   char_end=318
```

**Viva Q: "Why store `page_number` in `segment_pages` when `page_id` already
implies it?"**
A: It is denormalisation for query cost — ordering and provenance checks would
otherwise need a join to `pages` every time. Denormalisation normally risks the
two disagreeing, so the trigger makes that specific disagreement impossible:
an insert whose `page_number` does not equal the referenced page's number is
rejected. We get the cheap query without the usual cost.

---

## 9. How foreign keys and constraints preserve provenance

**Simple.** The rule "every clause must be traceable to a page" is written into
the database, so breaking it is impossible rather than discouraged.

**Technical.** Provenance is defended in layers, each catching what the one
above cannot:

| Layer | Mechanism | Catches |
|---|---|---|
| In memory | `Segment.__post_init__` raises on empty `page_offsets` | a pipeline bug, immediately |
| On the artefacts | `data_quality.py`: `segment_provenance` + `offset_resolution` | a hand-edited JSON file, or output from an older pipeline |
| Foreign keys | `segment_pages → segments`, `→ pages`; `pages → document_versions` | a row pointing at nothing |
| Per-row trigger | page/version agreement, `char_end` bounds | a row pointing at the *wrong* page |
| Deferred trigger | `segments_provenance_required` | a segment with **no** page row at all |

The last one is the interesting case, because `CHECK` cannot express it: at the
moment a segment is inserted, its `segment_pages` rows do not exist yet, so any
immediate check would fail every correct insert. `DEFERRABLE INITIALLY
DEFERRED` moves the check to `COMMIT`, which is precisely when the answer is
knowable.

It is also written to check **only the row that fired it**, by id. A
whole-table scan would be slow on every insert and wrong in a way that matters:
it would fail a transaction because of some *other* pre-existing bad row,
pointing the error at the wrong segment.

**Example.** Deleting a version cascades correctly and stops at the document:

```
DELETE FROM document_versions WHERE id = 1;
  -> pages                CASCADE
  -> page_removed_lines   CASCADE (via pages)
  -> segments             CASCADE
  -> segment_pages        CASCADE (via segments)
  -> documents            RESTRICT  -- the policy identity survives its versions
```

**Viva Q: "Could you not just check this in Python?"**
A: We do, in two places — and it is still not enough. The JSON artefacts are
what Persons 2–5 read, and the database can be written to by any of us; a
guarantee that lives only in the ingestion code is void the moment someone
inserts a row by hand or an older version of the pipeline writes a file. Having
the constraint in the schema means the guarantee holds regardless of which
client is talking to the database.

---

## 10. How transactions prevent partially loaded versions

**Simple.** A version loads completely or not at all. There is no state where
half a document is visible.

**Technical.** `loader.load_version_directory` wraps one version — document
row, version row, every page, every removed line, both methods' segments, every
`segment_pages` row, and the `ingestion_jobs` record — in a single
`engine.transaction()` block. `CLAUDE.md` §10.2's "one version per
transaction", literally.

This interlocks with §9: because the provenance trigger is deferred to
`COMMIT`, the transaction boundary is *also* the verification boundary. The
commit is the moment "every segment in this version has a page" is proved, and
a failure at that instant discards the entire version.

Versions are independent of each other: `load_processed_tree` gives each its
own transaction, so one malformed artefact does not roll back a previously
successful version, and the run continues and reports the failure.

**Example.** A failure injected at the *last* segment of a version — after all
pages and most segments are already inserted:

```
LoadError: segment SEG-… references page 9, which was not extracted
→ documents: 0, versions: 0, pages: 0, segments: 0
```

Nothing survives, including the pages that had already been written —
asserted by `test_a_load_that_fails_midway_rolls_back_the_whole_version`,
which passes against the live server.

**Viva Q: "What happens if the process is killed mid-load?"**
A: The transaction never commits, so PostgreSQL discards it and the database is
exactly as it was. That is also why `extraction_status` gates visibility in the
`segment_citations` view: even a *committed* version whose extraction was only
`partial` is excluded from citations, so incomplete text cannot be quoted as
evidence.

---

## 11. How duplicate detection works

**Simple.** The same PDF cannot be stored twice, and the system notices by
looking at the file's content rather than its name.

**Technical.** Two unique keys on `document_versions`, checked in both
directions by the loader before it writes anything:

- `file_checksum CHAR(64) UNIQUE` — the full SHA-256 of the file bytes. Answers
  "are these exact bytes already here, under any id?"
- `fingerprint TEXT UNIQUE` — `VER-` plus the first 16 hex of that checksum.
  Answers "is this version id already here?"

Both are checked because they catch different mistakes: a renamed re-download
has new metadata and the same bytes; a re-processed artefact has the same id.
Either is a duplicate.

Critically, the loader distinguishes **duplicate** from **conflict**:

- fingerprint *and* checksum both match → duplicate. Skipped, reported, nothing
  changed.
- one matches and the other does not → **conflict**. The load aborts with
  `VersionConflict` and changes nothing, because overwriting would destroy
  whichever of the two was correct. Replacing requires an explicit
  `--replace`.

**Example.**

```
$ python -m backend.app.db.loader
  [loaded]            VER-6507f32084d73257 VER-0001
  [skipped_duplicate] VER-7c5ad74aba9ebd62 VER-0002
      already loaded as VER-0002; nothing changed
```

**Viva Q: "Why both a checksum and a fingerprint? Isn't that redundant?"**
A: The fingerprint is derived from the checksum, so they never disagree for a
correctly processed file — and that is what makes the comparison useful. If they
*do* disagree, something is wrong: either the artefact was edited after
extraction, or two different files were assigned the same id. Checking both
turns a silent inconsistency into a loud refusal. The 16-hex fingerprint exists
separately because it is short enough to use as a directory name and to read
aloud, which a 64-character hash is not.

---

## 12. How idempotent ingestion works

**Simple.** Running the loader twice over the same artefacts leaves the
database exactly as it was after the first run.

**Technical.** Idempotency rests on *stable, content-derived* identifiers
rather than on the loader remembering anything:

- `identity.py` derives the version fingerprint from the file's SHA-256, so
  re-processing the same PDF produces the same `VER-…` every time — verified
  idempotent across re-runs in Phase 1.
- The document fingerprint derives from the manifest's `document_group`, so a
  second version of a known policy attaches to the existing `documents` row
  instead of creating a near-duplicate.
- Segment fingerprints are keyed on `(version, method, ordinal)` and **not** on
  the text, deliberately: a normalisation fix changes clause text without
  invalidating every annotation that referenced it.

So a repeat load is *detected* (§11) and skipped. No `ON CONFLICT DO UPDATE`
that would silently rewrite stored text, and no deletion unless asked.

Verified: `test_reloading_the_whole_corpus_is_idempotent` re-runs the loader
over the whole real corpus and asserts every table count is unchanged and
`summary.loaded == 0`. It passes.

**Example.**

```python
before = repository.corpus_counts(url=url)
summary = load_processed_tree(PROCESSED_DIR, url=url)
assert summary.loaded == 0 and summary.skipped >= 1
assert repository.corpus_counts(url=url) == before
```

**Viva Q: "Is idempotency the same as `ON CONFLICT DO NOTHING`?"**
A: No, and the difference is the point. `ON CONFLICT DO NOTHING` at row level
would let a *partially* different version merge into an existing one — some rows
new, some skipped — and the result would be a version that matches neither
artefact. We decide at the level of the whole version, before writing anything:
already present and identical → skip; present and different → refuse and say
so; absent → load inside one transaction. The unit of idempotency is the
version, because that is the unit the data has meaning in.

---

## 13. Honest limitations

- **No version pairs in the corpus** (D-10). `supersedes_version_id` and the
  document/version split are tested with synthetic fixtures only. Compare has
  nothing real to run on.
- **One authority** (RBI), four documents, 57 pages. Nothing here establishes
  that the schema fits other departments' document structures.
- **No rollback migrations** (§3), by choice.
- **`to_tsvector('english')`** is the wrong analyzer for the Devanagari
  letterhead text that survives into some preamble segments. Recorded in a
  column comment rather than papered over; no extractor available to us reads
  that Devanagari correctly anyway (Experiment A §3).
- **Highlighting against `raw_text` is approximate** (§7) — the trade-off is
  inverted from `CLAUDE.md` §11.4's original specification, not eliminated.
- **`cleaned_text` offsets are not stored.** Segmentation reads cleaned text but
  records offsets against `normalized_text`; relating the two needs the
  per-page line-offset map in `page_cleaning.json`.
- **Synchronous ingestion only** (D-5). `ingestion_jobs` exists so a later
  switch to a background task is additive, but no async path is built.
- **No FastAPI** (Phase 4) and **no connection from the NLP modules** (Phase 6).

---

## 14. Questions still to prepare

Things a viva could reasonably ask that this document does not yet answer:

1. **Query performance at scale.** Every measurement here is on 57 pages and
   501 segments. No `EXPLAIN ANALYZE` has been run, and the indexes are
   reasoned about rather than measured.
2. **Why no `pgvector`.** The answer is D-7 (Person 3 keeps an index inside
   `nlp/rag_qa/`, add `pgvector` only on measured need), but no retrieval
   experiment has been run to make that need measurable either way.
3. **Concurrency.** Two loaders running at once would be serialised by the
   unique constraints, and one would get a conflict — reasoned, not tested.
4. **Backup and restore.** Not addressed; the recovery path is "re-derive from
   the PDFs" (§3).
5. **How Person 4 will align clauses across versions** using
   `supersedes_version_id` and clause numbers — the schema supports it, the
   algorithm is Person 4's and does not exist yet.
