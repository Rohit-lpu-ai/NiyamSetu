# Backend integration contract

**For:** Person 2 (Frontend + Classification), Person 3 (Ask / RAG),
Person 4 (Compare + Extraction), Person 5 (Library search + Integration).
**Status:** `[IMPLEMENTED]` and verified against a live PostgreSQL database,
2026-11-13. **Version 1.0.**

This is what the backend guarantees. Code against it. Where something is not
guaranteed, it says so — those are the places that will change.

**Contract change process** (`CLAUDE.md` §12.4): additive fields may appear at
any time; a removed or renamed field is announced to all four of you **before**
it merges. Pin to this document's version in your own README.

---

## 1. Start here

```
.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload
```

| | |
|---|---|
| Base URL | `http://127.0.0.1:8000/api` |
| Swagger (try it live) | `http://127.0.0.1:8000/docs` |
| OpenAPI JSON | `http://127.0.0.1:8000/openapi.json` |

**Generate a client from `/openapi.json`** rather than hand-writing models —
every schema below is in it, and a generated client stays in sync.

**The API is the only supported read path.** Reading PostgreSQL directly is not
a contract: table and column names may change, `public_id` values will not.

---

## 2. Identifiers — the thing to get right

Three ID schemes exist and they are **not** interchangeable.

| ID | Shape | Example | Use it for |
|---|---|---|---|
| `document_id` | `DOC-` + ≥4 digits | `DOC-0001` | citing a logical policy |
| `version_id` | `VER-` + ≥4 digits | `VER-0001` | citing one published issue |
| `clause_id` | `CLS-` + ≥7 digits | `CLS-0000253` | **citing a clause** |
| `fingerprint` | `SEG-`/`VER-`/`DOC-` + 16 hex | `SEG-2146f692b738fe91` | matching across re-runs |

**Rules:**

1. **Cite `clause_id`, never `clause_number`.** Clause numbers are *not unique*
   — duplicate `(clause_number, level)` pairs occur 3–54 times per document
   (nested lists restart at `(i)`, and table rows collide with real clauses).
   Resolving "clause 2" by number yields multiple candidates.
2. **Database integers never appear in any payload.** If you see one, it is a bug.
3. `fingerprint` is content-derived and stable across re-segmentation;
   `public_id` is what a human cites. Store `clause_id` in your indexes.

---

## 3. Response schemas

Exact field names, copied from live responses. Optional fields may be `null`;
they are never absent.

### 3.1 Document summary — `GET /api/documents`

```json
{
  "total": 4, "limit": 20, "offset": 0,
  "items": [{
    "document_id": "DOC-0001",
    "title": "Re-organisation of districts in the NCT of Delhi ...",
    "department": "Financial Inclusion & Development Dept, Central Office",
    "document_type": "circular",
    "reference_no": "RBI/2026-27/300 FIDD.CO.LBS.BC.No.12/02.08.001/2026-27",
    "version_count": 1,
    "latest_publication_date": "2026-10-26"
  }]
}
```

`document_type` ∈ `notification | circular | scheme | guideline | amendment |
other`, or `null`. Constrained in the schema, so a typo cannot create a new
type — **Person 2**, you can treat it as a closed vocabulary for classification
labels; **Person 5**, as a filter facet.

`department`, `document_type`, `reference_no`, `latest_publication_date` are
**nullable** — real documents omit them (1 of 4 has no department). Render the
gap; do not invent a default.

### 3.2 Document detail — `GET /api/documents/{document_id}`

Adds `versions[]`, newest first:

```json
{
  "version_id": "VER-0001",
  "version_label": "2026-10-26",
  "publication_date": "2026-10-26",
  "page_count": 2,
  "extraction_status": "ok",
  "extraction_tool": "pypdf 6.18.1",
  "offset_basis": "normalized_text",
  "source_url": "https://rbidocs.rbi.org.in/...",
  "file_checksum": "6507f32084d732...",
  "processing_warnings": [],
  "ingested_at": "2026-11-12T23:08:48.359078+05:30",
  "supersedes_version_id": null,
  "segment_counts": {"clause_aware": 11, "paragraph": 4}
}
```

- `extraction_status` ∈ `pending | ok | partial | failed`. **Only trust text
  from `ok`.** The citation view excludes everything else.
- `processing_warnings` — "extraction succeeded" and "extraction was clean" are
  different claims. Surface these on the Document View.
- `supersedes_version_id` is **always `null` today** (D-10: no version pairs
  exist). **Person 4** — the field and its constraints work and are tested, but
  nothing populates it yet. Compare has no real data until the corpus grows.

### 3.3 Pages — `GET /api/documents/{document_id}/pages`

Query: `version_id`, `page_from`, `page_to`, `text`, `limit` (≤100), `offset`.

`text` selects which fields appear:

| value | returns | meaning |
|---|---|---|
| `normalized` (default) | `normalized_text` | **the offset basis** |
| `raw` | `raw_text` | the extractor's verbatim output, never rewritten |
| `cleaned` | `cleaned_text` | normalised minus running headers/footers |
| `both` | `raw_text` + `normalized_text` | |

```json
{"page_number": 1, "char_count": 2038, "word_count": 310,
 "is_empty": false, "has_meaningful_text": true, "normalized_text": "..."}
```

`has_meaningful_text` is a heuristic — `false` means *look*, not *broken*.

### 3.4 Clauses — `GET /api/documents/{document_id}/clauses`

**The endpoint Persons 3, 4 and 5 all read.**

Query: `version_id`, `method` (`clause_aware` default | `paragraph`),
`segment_type`, `page_from`, `page_to`, `limit` (≤100), `offset`.

```json
{
  "document_id": "DOC-0004", "version_id": "VER-0004",
  "segmentation_method": "clause_aware",
  "offset_basis": "normalized_text",
  "total": 254, "limit": 50, "offset": 0,
  "items": [{
    "clause_id": "CLS-0000253",
    "fingerprint": "SEG-2146f692b738fe91",
    "ordinal": 5,
    "segment_type": "clause",
    "clause_number": "(2)",
    "clause_number_ambiguous": false,
    "heading": null,
    "text": "(2) The minimum leverage ratio ...",
    "page_start": 1, "page_end": 2,
    "pages": [
      {"page_number": 1, "char_start": 1719, "char_end": 1955,
       "line_index_start": 32, "line_index_end": 34},
      {"page_number": 2, "char_start": 119, "char_end": 318,
       "line_index_start": 4, "line_index_end": 6}
    ],
    "level": 2,
    "marker_style": "paren_number",
    "parent_clause_id": "CLS-0000250",
    "metadata": {"marker_style": "paren_number"}
  }]
}
```

`segment_type` ∈ `clause | paragraph | heading | annexure | preamble |
unclassified`.
`marker_style` ∈ `numeric_dotted | paren_number | paren_roman | paren_alpha |
annexure | structural | spelled`, or `null`.

**Both segmentation methods are stored for every version** — 138 paragraph and
363 clause-aware segments across the corpus. **Person 3**: you can compare
chunking strategies without re-ingesting anything.

`clause_number_ambiguous` flags the `(i)` case — simultaneously roman one and
letter nine. 31 occurrences corpus-wide. The segmenter defaults to roman and
**flags** rather than silently deciding. **Person 4**: do not align on a flagged
number without checking.

---

## 4. Provenance — the rules that must not be broken

**`CLAUDE.md` §24 rule 6: nothing downstream may drop a page reference.**

1. **Every segment has ≥1 `pages` entry.** Guaranteed in memory, on the
   artefacts, and by a deferred database trigger at COMMIT. 501/501 verified.
2. **A multi-page clause returns every page it touches**, never collapsed. 45
   of 501 segments span a page break; the longest spans 5 pages.
3. **`offset_basis` is `normalized_text`.** Character ranges are half-open
   `[char_start, char_end)` slices into the page's `normalized_text`.

**To resolve a clause from its offsets** (what a citation does):

```python
pages  = GET /api/documents/{doc}/pages?version_id={ver}      # text=normalized
clause = GET /api/documents/{doc}/clauses?version_id={ver}

rebuilt = " ".join(
    pages[p["page_number"]]["normalized_text"][p["char_start"]:p["char_end"]]
    for p in clause["pages"]
)
assert collapse_whitespace(rebuilt) == collapse_whitespace(clause["text"])
```

**Two traps:**

- **Slicing a different text field gives the wrong span.** Measured: against
  `cleaned_text` only 14 of 501 resolve; against `raw_text`, **0**. The 14 are
  the trap — enough to make a spot-check look right.
- **Whitespace differs.** Segmentation joins wrapped lines with a space, so the
  page slice carries a newline the clause text does not. Character *ranges* are
  exact; compare whitespace-insensitively.

**⚠️ Offset resolution is not segmentation accuracy.** 501/501 means the
bookkeeping is sound, **not** that boundaries are correct. Boundary F1 is
0.267–0.963 depending on the document, on a 21-clause sample with no
independent validation. **Person 5** — do not quote 501/501 as a quality figure
in the end-to-end report.

---

## 5. Citation format

`CLAUDE.md` §12.2, shared by Ask and Compare:

```json
{"document_id": "DOC-0001", "version_id": "VER-0001",
 "clause_id": "CLS-0000123", "page_start": 3, "page_end": 3,
 "quote": "exact substring of the clause text"}
```

`quote` **must be a verbatim substring** of the clause's `text`.
**Person 3** — your citation test asserts exactly this, and there is a known
hazard: pypdf produces **314 broken words** corpus-wide (`'L ead B anks'`), so a
quote copied from a user's search terms may not match the stored text. Build
from the returned `text`, not from user input.

---

## 6. Pagination

| | |
|---|---|
| Parameters | `limit`, `offset` |
| Default `limit` | 20 (documents) / 50 (pages, clauses) |
| Maximum `limit` | **100** — a larger value is **clamped, not rejected** |
| `total` | count **before** pagination |

Page *ranges* (`page_from`/`page_to`) and pagination are different filters and
compose: `page_from`/`page_to` select printed positions; `limit`/`offset`
window the result.

---

## 7. Errors

**Every non-2xx response has this shape, with no exceptions:**

```json
{"error": {"code": "NOT_FOUND",
           "message": "no document with id DOC-9999",
           "details": {}}}
```

| code | HTTP | meaning |
|---|---|---|
| `VALIDATION_ERROR` | 422 | bad query/form; `details.errors[]` has `field`, `message`, `type` |
| `NOT_FOUND` | 404 | unknown document, version or clause |
| `UNSUPPORTED_FILE_TYPE` | 415 | not a PDF (content sniffed, not the extension) |
| `FILE_TOO_LARGE` | 413 | over the cap; `details.limit_bytes` |
| `EMPTY_FILE` | 422 | zero bytes |
| `PDF_ENCRYPTED`, `PDF_HAS_NO_PAGES`, `EXTRACTION_FAILED` | 422 | pipeline rejected it |
| `VERSION_CONFLICT` | 409 | conflicts with stored data; `details.changed == false` |
| `INTERNAL_ERROR` | 500 | our bug — message is deliberately generic |

**Never parse `message`** — it is for humans. Branch on `code`.
A 500 never leaks a stack trace, path, SQL or connection string.

---

## 8. Ingestion — `POST /api/documents/ingest`

`multipart/form-data`. Required: `file`, `title`, `document_group`.
Optional: `version_label`, `issuing_department`, `document_type`,
`reference_no`, `publication_date`, `source_url`, `retrieved_at`.

**`document_group` is a human decision** (D-9). Reusing an existing group
attaches the upload as a **new version of that document**. It is never inferred
from title similarity — two similarly-titled documents are two documents unless
a person says otherwise.

| outcome | HTTP | `status` | changed anything? |
|---|---|---|---|
| new version stored | 201 | `loaded` | yes |
| same file already stored | **200** | `skipped_duplicate` | **no** |
| same id, different bytes | 409 | — | **no**, never overwritten |

**Check `status`, not just the HTTP code** — a client that cannot tell 200 from
201 will double-count its corpus. A duplicate also leaves stored artefacts
untouched.

Ingestion is **synchronous** (D-5): a 37-page PDF takes seconds and holds the
request open.

---

## 9. Boundaries — what the backend will not do

| Rule | Why |
|---|---|
| **NLP modules never write** to documents / versions / pages / segments | `CLAUDE.md` §9. The pipeline owns that data. |
| The frontend never talks to PostgreSQL directly | §9. The API is the contract. |
| `metadata` on a clause is **read-only** for now | Reserved for your annotations; the write mechanism is not agreed yet. Bring a proposal. |
| No auth | None required by the current architecture; not invented. Anyone who can reach the port can ingest. |

**If you need a field that does not exist, ask — do not derive it and store it
elsewhere.** Additive fields are cheap; a second source of truth is not.

---

## 10. Known limitations that will affect your module

| Limitation | Who it hits | Detail |
|---|---|---|
| **314 broken words** (`'L ead B anks'`) | **P3** citations, **P5** search | pypdf-specific; verbatim-substring matching can fail |
| **Devanagari is corrupted** (1,192 chars) | **P2** display, **P3** | no available extractor fixes it; PyMuPDF hides rather than fixes |
| **Tables flatten** — up to 39.9% of clause segments overlap a table region | **P3** retrieval noise, **P4** alignment | an upper bound, not a rate (D-12 open) |
| **`clause_number` is not unique** | **P4** alignment | cite `clause_id` |
| **No version pairs** | **P4** Compare | D-10 — nothing to diff |
| **4 documents, 1 authority** | **everyone** | D-10 — results will not generalise |
| **`to_tsvector('english')`** on segments | **P5** | wrong analyser for Devanagari text |
| **Segmentation is unvalidated** | **P5** error-propagation | agreement PENDING; F1 from 21 clauses |

---

## 11. Stubbing before the server is up

Everything above is in `/openapi.json`. Generate a client and a mock from it,
or copy the payloads in §3 — they are real responses, not invented examples.
`backend/tests/test_api.py` contains a `FakeRepository` you can read for the
exact shapes, and an integration test asserts that fake still matches the real
database, so it cannot silently drift.
