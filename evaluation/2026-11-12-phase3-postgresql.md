# Phase 3 — PostgreSQL load results

**Run:** 2026-11-12 · **By:** Person 1 · **Server:** PostgreSQL 18.6, localhost:5432
**Databases:** `niyamsetu` (development), `niyamsetu_test` (tests only).

Reproduce:

```
.venv\Scripts\python -m backend.app.db.migrate
.venv\Scripts\python -m backend.app.db.loader
.venv\Scripts\python -m pytest
```

---

## 1. What was loaded

| version | public_id | pages | removed lines | paragraph | clause_aware | segment_pages |
|---|---|---:|---:|---:|---:|---:|
| VER-6507f3… lead-bank Delhi | `VER-0001` | 2 | 26 | 4 | 11 | 15 |
| VER-7c5ad7… commercial-banks amdt | `VER-0002` | 10 | 9 | 14 | 49 | 73 |
| VER-b8a13c… 272 master circular | `VER-0003` | 8 | 8 | 46 | 49 | 97 |
| VER-fd6e15… capital-adequacy 11th | `VER-0004` | 37 | 108 | 74 | 254 | 367 |
| **total** | **4 documents / 4 versions** | **57** | **151** | **138** | **363** | **552** |

Row counts read back from the database:

```
documents 4 | versions 4 | pages 57 | removed_lines 151
segments 501 (paragraph 138, clause_aware 363) | segment_pages 552
page_spanning_segments 45 | ingestion_jobs 4
```

**Every count matches the JSON artefacts exactly.** `test_the_database_holds_
exactly_what_the_artefacts_hold` asserts this rather than trusting it, so a
loader that dropped or duplicated a row would fail the suite.

---

## 2. Tests

**462 passed, 0 skipped, 0 failed** (previously 406 passed + 58 skipped — the
58 database tests now actually run).

Constraint and transaction behaviour, each observed firing against the real
server rather than reasoned about:

| Guarantee | Test | Result |
|---|---|---|
| Segment with no `segment_pages` row | `test_a_segment_with_no_page_row_fails_at_commit` | deferred trigger rejects **at COMMIT** |
| `segment_pages.page_number` disagreeing with its page | `test_a_page_row_disagreeing_with_its_page_is_rejected` | rejected |
| Offset past end of page | `test_an_offset_past_the_end_of_the_page_is_rejected` | rejected |
| `raw_text` overwrite | `test_raw_text_cannot_be_overwritten` | rejected; `normalized_text` still updatable |
| Duplicate checksum | `test_a_duplicate_checksum_is_rejected_by_the_database` | rejected |
| Duplicate ordinal per (version, method) | `test_a_duplicate_ordinal_for_one_method_is_rejected` | rejected |
| Reversed page range / bad method / bad `offset_basis` | 3 tests | rejected |
| Removed line without evidence | `test_a_removed_line_without_evidence_is_rejected` | rejected |
| Mid-load failure | `test_a_load_that_fails_midway_rolls_back_the_whole_version` | **0 rows survive**, pages included |
| Idempotent reload | `test_reloading_the_whole_corpus_is_idempotent` | `loaded=0`, counts unchanged |

---

## 3. Provenance, verified inside the database

`test_every_stored_segment_resolves_from_its_stored_offsets` re-runs the
`offset_resolution` check against what PostgreSQL actually holds — slicing
`pages.normalized_text` at each stored `(char_start, char_end)` and requiring
the clause back. **All 501 stored segments resolve**, matching the artefact-level
result in `2026-11-12-provenance-validation.md`.

Also asserted and passing: 0 orphan segments, 0 `segment_pages` rows crossing
into another version's pages, 0 page references outside `1..page_count`, and
every page-spanning segment carrying exactly `page_end - page_start + 1` rows.

A worked example, read back out of the database:

```
CLS-0000253  clause (2)  pages 1-2
  page 1  chars 1719-1955
  page 2  chars  119- 318
  "(2) The minimum leverage ratio for a domestic systemically important bank..."
citation quote is a verbatim substring of the clause text: True
```

---

## 4. Two defects found on first apply

Both were in *our* code, both found by running rather than by review, and both
are the reason this report exists.

**1. `%` in PL/pgSQL defeated the migration runner.** `apply_migration` used
SQLAlchemy's `exec_driver_sql`, which hands the SQL to psycopg with an empty
parameter set — switching on `%`-interpolation. Our trigger bodies are full of
`RAISE EXCEPTION '... (%) ...'` format strings, so psycopg read those as
placeholders and refused:

```
incomplete placeholder: '%'; if you want to use '%' as an operator you can double it up
```

Fixed by executing through the raw DBAPI cursor with no parameters at all. The
tempting fix — doubling every `%` in the SQL — would have "worked" by corrupting
the error messages the triggers raise. A migration is a literal script and must
never be parameterised.

**2. A fixture reused segment fingerprints across versions.** `segments.fingerprint`
is `UNIQUE` table-wide; the synthetic bundle hard-coded `SEG-1111…`, so loading a
second version collided. This was a *test* bug, not a product bug — and the
schema catching it is the constraint working. Fixed by deriving fixture
fingerprints from `(version, method, ordinal)`, as the real pipeline does.

**3. A password leaked into the pytest report.** `test_database_url` is a
function in `config.py`; imported bare into a test module, pytest collected it
as a test (the `test_` prefix) and printed its return value — a URL containing
the database password — as a `PytestReturnNotNoneWarning`. Fixed by aliasing it
on import. Worth knowing generally: **never import a `test_`-prefixed helper
into a test module unaliased.**

---

## 5. Limitations unchanged by this run

- **D-10 is still open.** Four documents, one authority (RBI), **zero version
  pairs**. `supersedes_version_id` is exercised only by synthetic fixtures, and
  Compare has nothing real to run on.
- **0 empty pages** in the corpus, so that provenance path remains untested on
  real data (scanned PDFs are excluded by D-6).
- **Self-consistency is not extraction correctness.** The Devanagari letterhead
  loss (Experiment A §3) resolves perfectly here and is still wrong text.
- **No `EXPLAIN ANALYZE`.** The indexes are reasoned about, not measured; 501
  segments is far too small for query cost to show.
- **Experiment B's inter-annotator agreement is still `PENDING`.**
- No FastAPI (Phase 4), no NLP module connected (Phase 6).
