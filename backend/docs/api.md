# NiyamSetu API — Phase 4

**Status:** `[IMPLEMENTED]` and verified against a real PostgreSQL database and
the real corpus, 2026-11-12. Six endpoints; no authentication (none is required
by the current architecture); synchronous ingestion (D-5).

Every example below is **copied from an actual response**, not written by hand.

---

## 1. Running it

```
copy .env.example .env                  # first time only; fill in the password
.venv\Scripts\python -m backend.app.db.migrate
.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload
```

Then open:

| URL | What |
|---|---|
| http://127.0.0.1:8000/docs | **Swagger UI** — try every endpoint from the browser |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/openapi.json | the OpenAPI document |
| http://127.0.0.1:8000/api/health | liveness + database reachability |

`--reload` is for development. Drop it and add `--host 0.0.0.0 --port 8000` to
serve elsewhere on the network.

### Environment variables

Read from the environment, optionally seeded from a git-ignored `.env`. **No
credential is ever hardcoded** (`CLAUDE.md` §20).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `NIYAMSETU_DB_HOST` | — | `localhost` | |
| `NIYAMSETU_DB_PORT` | — | `5432` | |
| `NIYAMSETU_DB_NAME` | — | `niyamsetu` | development database |
| `NIYAMSETU_DB_USER` | — | `postgres` | |
| `NIYAMSETU_DB_PASSWORD` | **yes** | — | percent-encoded for you |
| `NIYAMSETU_DATABASE_URL` | — | — | a complete URL; wins over the parts |
| `NIYAMSETU_TEST_DATABASE_URL` | for DB tests | — | throwaway DB; name **must** contain `test` |
| `NIYAMSETU_MAX_UPLOAD_BYTES` | — | `26214400` (25 MB) | upload cap |
| `NIYAMSETU_PROCESSED_DIR` | — | `data/processed` | where ingestion writes artefacts |
| `NIYAMSETU_RAW_DIR` | — | `data/raw` | where the source PDF is kept |
| `NIYAMSETU_DOCS` | — | `1` | set `0` to disable `/docs` and `/openapi.json` |

---

## 2. Conventions

**Identifiers are always `public_id` strings** — `DOC-0001`, `VER-0001`,
`CLS-0000123`. Database row ids never leave the repository layer
(`CLAUDE.md` §12).

**Every non-2xx response has one shape** (§12.1), with no exceptions:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "no document with id DOC-9999",
    "details": {}
  }
}
```

Codes in use: `VALIDATION_ERROR`, `UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE`,
`EMPTY_FILE`, `PDF_ENCRYPTED`, `PDF_HAS_NO_PAGES`, `EXTRACTION_FAILED`,
`SEGMENT_PROVENANCE_ERROR`, `VERSION_CONFLICT`, `LOAD_ERROR`, `NOT_FOUND`,
`METHOD_NOT_ALLOWED`, `INTERNAL_ERROR`. The pipeline codes are **reused from**
`document_processing/errors.py` rather than re-invented, so one vocabulary
spans the CLI and the API.

**`offset_basis` is on every page and clause response.** It is
`normalized_text` (D-11) and it tells you which page text the character offsets
index into. Slice a different field and your highlight lands in the wrong
place — measured: only 14 of 501 clauses happen to resolve against
`cleaned_text`, and 0 against `raw_text`.

---

## 3. Endpoints

### `GET /api/health`

Reports process health and database health **separately**. Returns `200` when
both are fine, **`503`** when the database is unreachable — the API still
answers, which is why `status` becomes `degraded` rather than the request
failing.

```json
{ "status": "ok", "database": "ok", "version": "0.1.0" }
```

---

### `POST /api/documents/ingest`

`multipart/form-data`. Runs the full pipeline and stores the result in one
transaction.

| Field | Required | Notes |
|---|---|---|
| `file` | **yes** | the PDF |
| `title` | **yes** | |
| `document_group` | **yes** | which logical document this is a version of — **a human decides this** (D-9); it is never inferred from title similarity. Reusing an existing group attaches the upload as a new version of that document. |
| `version_label` | no | defaults to the version fingerprint rather than inventing `v1` |
| `issuing_department`, `document_type`, `reference_no`, `publication_date`, `source_url`, `retrieved_at` | no | |

```bash
curl -X POST http://127.0.0.1:8000/api/documents/ingest \
  -F "file=@data/raw/rbi_circular_lead-bank-delhi_v1.pdf" \
  -F "title=Re-organisation of districts in the NCT of Delhi" \
  -F "document_group=rbi-lead-bank-delhi" \
  -F "document_type=circular"
```

**`201 Created`:**

```json
{
  "status": "loaded",
  "document_id": "DOC-0001",
  "version_id": "VER-0001",
  "checksum": "6507f32084d7325769ebf9ef44a07ba20bd1240295add75c369377128d0cdc45",
  "page_count": 2,
  "segment_counts": { "paragraph": 4, "clause_aware": 11 },
  "segment_total": 15,
  "removed_lines": 26,
  "extraction_status": "ok",
  "offset_basis": "normalized_text",
  "warnings": [],
  "message": null
}
```

**Status codes — the distinctions matter:**

| Code | When | Changed anything? |
|---|---|---|
| `201` | loaded | yes |
| `200` | `status: "skipped_duplicate"` — the same file is already stored | **no** |
| `409` | `VERSION_CONFLICT` — same id, different bytes (or vice versa) | **no**, and it is never overwritten |
| `413` | over the size cap | no |
| `415` | not a PDF (content is sniffed, not the extension) | no |
| `422` | empty file, encrypted, no pages, extraction or segmentation failure | no |

A duplicate is deliberately **not** an error — but a client that cannot tell
`200` from `201` will double-count its corpus, which is why `status` is in the
body too.

---

### `GET /api/documents`

Query: `q` (title/reference substring), `department`, `document_type`,
`limit` (1–100, default 20), `offset`.

Metadata only — no page text, no segments. Version counts come from an
aggregate, so listing a 37-page document costs the same as a 2-page one.

```json
{
  "total": 4,
  "limit": 2,
  "offset": 0,
  "items": [
    {
      "document_id": "DOC-0001",
      "title": "Re-organisation of districts in the NCT of Delhi - Assignment and Review of Lead Bank Responsibility",
      "department": "Financial Inclusion & Development Dept, Central Office",
      "document_type": "circular",
      "reference_no": "RBI/2026-27/300 FIDD.CO.LBS.BC.No.12/02.08.001/2026-27",
      "version_count": 1,
      "latest_publication_date": "2026-10-26"
    }
  ]
}
```

---

### `GET /api/documents/{document_id}`

```json
{
  "document_id": "DOC-0001",
  "title": "Re-organisation of districts in the NCT of Delhi - Assignment and Review of Lead Bank Responsibility",
  "department": "Financial Inclusion & Development Dept, Central Office",
  "document_type": "circular",
  "reference_no": "RBI/2026-27/300 FIDD.CO.LBS.BC.No.12/02.08.001/2026-27",
  "versions": [
    {
      "version_id": "VER-0001",
      "version_label": "2026-10-26",
      "publication_date": "2026-10-26",
      "page_count": 2,
      "extraction_status": "ok",
      "extraction_tool": "pypdf 6.18.1",
      "offset_basis": "normalized_text",
      "source_url": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NOT300...PDF",
      "file_checksum": "6507f32084d732...",
      "processing_warnings": [],
      "ingested_at": "2026-11-12T23:08:48.359078+05:30",
      "supersedes_version_id": null,
      "segment_counts": { "clause_aware": 11, "paragraph": 4 }
    }
  ]
}
```

`extraction_status` and `processing_warnings` are both present because "the
extraction succeeded" and "the extraction was clean" are different claims.

---

### `GET /api/documents/{document_id}/pages`

Query: `version_id` (defaults to the latest version with
`extraction_status='ok'`), `page_from`, `page_to`, `text`, `limit`, `offset`.

`text` selects which text fields appear:

| value | returns |
|---|---|
| `normalized` (default) | `normalized_text` — **the offset basis** |
| `raw` | `raw_text` — the extractor's verbatim output, never rewritten |
| `cleaned` | `cleaned_text` — normalised minus running headers/footers |
| `both` | `raw_text` **and** `normalized_text` |

The three are kept separate on purpose: collapsing them would destroy the audit
trail that lets you ask "did normalisation break this clause?"

---

### `GET /api/documents/{document_id}/clauses`

Query: `version_id`, `method` (`clause_aware` default | `paragraph`),
`segment_type`, `page_from`, `page_to`, `limit`, `offset`.

```json
{
  "document_id": "DOC-0004",
  "version_id": "VER-0004",
  "segmentation_method": "clause_aware",
  "offset_basis": "normalized_text",
  "total": 254,
  "limit": 1,
  "offset": 0,
  "items": [
    {
      "clause_id": "CLS-0000253",
      "fingerprint": "SEG-2146f692b738fe91",
      "ordinal": 5,
      "segment_type": "clause",
      "clause_number": "(2)",
      "clause_number_ambiguous": false,
      "heading": null,
      "text": "(2) The minimum leverage ratio for a domestic systemically important bank...",
      "page_start": 1,
      "page_end": 2,
      "pages": [
        { "page_number": 1, "char_start": 1719, "char_end": 1955, "line_index_start": 32, "line_index_end": 34 },
        { "page_number": 2, "char_start": 119,  "char_end": 318,  "line_index_start": 4,  "line_index_end": 6  }
      ],
      "level": 2,
      "marker_style": "paren_number",
      "parent_clause_id": "CLS-0000250",
      "metadata": { "marker_style": "paren_number" }
    }
  ]
}
```

**Read the `pages` array.** `page_start`/`page_end` answer "where is this
clause" for a citation. `pages` answers "which pages does it touch, and where
in each" for highlighting. A clause spanning pages 1–2 returns **both**, never
collapsed onto one — 45 of the 501 stored segments span a page break.

`clause_number_ambiguous` flags the `(i)` case — simultaneously roman one and
letter nine. The segmenter defaults to roman and **flags** it rather than
silently deciding.

Both segmentation methods are stored for every version, so a consumer can
compare chunking strategies without re-ingesting anything.

---

## 4. How ingestion flows

```
POST /api/documents/ingest
  │
  ├─ route         size / emptiness check before anything touches disk
  ├─ staging       bytes → a generated temp filename (never the client's)
  │
  ├─ validate_pdf_file        type sniff, size, emptiness        [Phase 1]
  ├─ extract_pdf              per-page text + page provenance    [Phase 1]
  ├─ result_to_json           the extraction.json contract
  ├─ segment_extraction       cleaning + BOTH methods            [Phase 2]
  ├─ result_payload           the segments_*.json contract
  │
  ├─ writes data/processed/<VER-fingerprint>/{extraction,page_cleaning,
  │         segments_paragraph,segments_clause_aware}.json
  ├─ copies the PDF to data/raw/   (document_versions.stored_path points at it)
  │
  └─ load_version_directory   ONE transaction into PostgreSQL    [Phase 3]
          └─ at COMMIT the deferred trigger proves every segment has a page
```

**The API implements none of those steps.** `backend/app/api/ingestion.py` is
orchestration only — every stage is the same function the Phase 1/2 CLIs call,
in the same order. A second implementation would mean documents differing
depending on whether they arrived by CLI or by HTTP.

**Why it writes artefacts to disk** rather than loading from memory: the JSON
files are what Persons 2–5 read and what `evaluation/data_quality.py` checks.
An in-memory path would save a few writes and lose the artefact that makes an
ingestion auditable afterwards.

---

## 5. Which tests were mocked, and which hit PostgreSQL

This distinction is tracked by a marker, not by prose, so it cannot go stale:

```
.venv\Scripts\python -m pytest -m integration        # 71 tests: need PostgreSQL
.venv\Scripts\python -m pytest -m "not integration"  # 443 tests: no server needed
```

The marker is **derived, not maintained** — `conftest.py` applies it to any
test requesting a database fixture (`db`, `db_url`, `loaded`, `real_corpus_db`,
`api_db`). Hand-marking drifts; this cannot.

| Module | Tests | Database? |
|---|---:|---|
| `test_api.py` | 39 | **mocked** — fake repository via `dependency_overrides`. Proves routing, validation, status codes, the error envelope and serialisation. **Proves nothing about PostgreSQL.** |
| `test_api_integration.py` | 13 | **real** — a real PDF through the real pipeline into the real database, over HTTP |
| `test_db_schema.py` | 49 | 10 need no server (config, migration discovery); 39 real |
| `test_db_integration.py` | 17 | **real** — the four RBI documents |
| everything else | 396 | no database involved |

`test_api_integration.py` additionally asserts that the **fake's payload shapes
still match the real ones**. A fake that silently drifts turns 39 passing tests
into 39 tests of nothing, so that guard is the one holding the mocked suite
honest.

Measured, 2026-11-12:

- **with** PostgreSQL configured: **514 passed, 0 skipped**
- **without** a test database: **443 passed, 71 skipped** — skipped, never
  silently passed

---

## 6. Known limitations

- **No authentication.** Not required by the current architecture and
  deliberately not invented. Anyone who can reach the port can ingest.
- **Synchronous ingestion** (D-5). A 37-page PDF takes a few seconds and holds
  the request open. `ingestion_jobs` exists in the schema so a background-task
  switch is additive, but no async path is built.
- **`segment_type` filtering is done in Python**, not SQL: the endpoint reads
  the version's segments (≤254 today) and filters. Pushing it into the
  repository is the right fix if the corpus grows.
- **Page pagination is applied after the repository query**, so a page range is
  fetched then sliced. Fine at 57 pages; not at 5,000.
- **No rate limiting, no request size streaming.** The upload is read fully
  into memory before the size check, bounded by `NIYAMSETU_MAX_UPLOAD_BYTES`.
- **`supersedes_version_id` is always `null`** — no corpus document supersedes
  another (D-10). The field is exercised only by synthetic fixtures.
- **Devanagari text is mangled** in some preamble segments. No extractor
  available to this project reads it correctly (Experiment A §3); the API
  faithfully serves what extraction produced, including that defect.
- **Not built, by scope:** Ask/RAG, Compare, classification, library search,
  the product frontend.
