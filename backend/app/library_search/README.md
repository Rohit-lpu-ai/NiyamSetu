# Library & Search — module brief

**Status: `[PLANNED]` — this directory is a scaffold.** Four files exist
(`__init__.py`, `service.py`, `repository.py`, `schemas.py`); every function
raises `NotImplementedError` and `backend/tests/test_library_search_scaffold.py`
asserts that it still does. Nothing here is wired into the FastAPI app, so the
running API is unchanged.

**Owner:** Person 5 (Library intelligence + integration), per `CLAUDE.md` §7.
**Depends on:** Phases 3 and 4, both `[IMPLEMENTED]` and verified.

---

## 1. Responsibility

One job: **given a query, return the documents or clauses that match it, in a
defensible order, with the evidence to show why.**

That is three deliverables in Person 5's half of the project — a keyword
baseline, at least one comparison condition, and a relevance evaluation.

### Explicitly not this module's job

| Not here | Where it lives |
|---|---|
| PDF extraction, cleaning, segmentation | `backend/app/document_processing/` |
| Any write to `documents` / `document_versions` / `pages` / `segments` | `backend/app/db/loader.py`, used only by ingestion |
| Document / page / clause response shapes | `backend/app/api/schemas.py` |
| Answer *generation* over retrieved text | Person 3's `rag_qa` — retrieval here, answers there |
| Chunking text for an index | **nobody.** The stored segments *are* the chunks |

The last row is the one that matters most. Re-chunking would produce text whose
offsets no longer match `segments.char_start` / `char_end`, and then a search
result could not be turned into a citation. `CLAUDE.md` §6 and §9 make that a
hard rule, not a preference.

---

## 2. What already exists and must be reused

### 2.1 Database tables — all 7 exist, applied, loaded

| Table | Rows now | How search uses it |
|---|---:|---|
| `documents` | 4 | the unit a Library hit points at; `department` / `document_type` / `reference_no` are the filter columns |
| `document_versions` | 4 | restricts search to one version; `extraction_status` gates what is searchable at all |
| `pages` | 57 | page-scoped results and page text for display |
| `segments` | 501 | **the search corpus.** 138 paragraph + 363 clause-aware, both methods stored side by side |
| `segment_pages` | 552 | turns a hit into a citation: page number + `char_start` / `char_end` per page |
| `page_removed_lines` | 151 | explains why a header is absent from a result; not searched |
| `ingestion_jobs` | 4 | out of scope for search |

### 2.2 Indexes that already exist — no migration needed for the baseline

```sql
segments.text_search  tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);
CREATE INDEX documents_department_idx  ON documents (department);
CREATE INDEX documents_type_idx        ON documents (document_type);
CREATE INDEX versions_publication_idx  ON document_versions (publication_date DESC NULLS LAST);
```

`segments.text_search` is **queried by nothing today.** It was created in
`0001_initial_schema.sql` specifically for this module. Lighting it up is the
whole of the keyword baseline — and because it is a stored generated column,
it is already populated for all 501 segments.

**Carry its defect forward honestly:** the configuration is `'english'`, which
is the wrong stemmer for the Devanagari text in the corpus. The schema records
this in a `COMMENT`; the evaluation must report it rather than let coverage
look better than it is.

### 2.3 Python to call, not to copy

| Reuse | Instead of |
|---|---|
| `backend.app.db.engine.connection()` | a second engine or pool |
| `backend.app.db.config.database_url()` | reading `.env` again |
| `backend.app.db.repository.list_documents()` | re-writing the unfiltered Library listing |
| `backend.app.db.repository.get_citation()` | assembling a citation by hand |
| `backend.app.api.errors` + `ApiError` | a second error envelope |
| `backend.app.api.schemas` models | a second `ClauseOut` |

### 2.4 Endpoints that already work (6, all under `/api`)

| Method | Path | What search gets from it |
|---|---|---|
| GET | `/api/health` | the pattern for DB-reachability reporting |
| POST | `/api/documents/ingest` | **the only writer.** Search never ingests |
| GET | `/api/documents` | substring filter on title / `reference_no`, plus `department` and `document_type`. **This is the fallback the baseline must beat** |
| GET | `/api/documents/{document_id}` | metadata + version list for a hit |
| GET | `/api/documents/{document_id}/pages` | page text for display |
| GET | `/api/documents/{document_id}/clauses` | the clause corpus, with provenance, by method |

Today's `GET /api/documents?q=` is `ILIKE '%q%'` over `title` and
`reference_no` only — it does not touch clause text at all. That is the honest
baseline-of-the-baseline, and the first measurable claim this module can make
is how much ranked full-text search over 501 segments improves on it.

---

## 3. How it connects

```
HTTP
 └─ backend/app/api/routes.py          existing routers; add a search_router here
     └─ library_search.service         strategy choice: keyword | semantic | hybrid
         ├─ library_search.repository  SQL: tsvector @@ plainto_tsquery, ts_rank_cd
         │   └─ backend.app.db.engine  the EXISTING pooled connection
         └─ backend.app.db.repository  reused reads (citations, document detail)
```

Three rules this wiring must keep:

1. **One engine.** Go through `db.engine.connection()`; never construct an
   engine here.
2. **Read-only.** No `INSERT` / `UPDATE` / `DELETE` in this package, ever.
3. **Injected, not imported, at the route.** Follow
   `api/dependencies.py::get_repository`: a `SearchService` dependency that
   tests can override, so search gets mocked tests like the API already has.

---

## 4. Endpoints it will likely need `[PROPOSED]`

None of these exist yet. Each needs the §12.4 contract announcement before it
is merged.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/api/search` | ranked search across documents **and** clauses | `q`, `scope`, `strategy`, `department`, `document_type`, `limit`, `offset`. Empty `q` → `422 VALIDATION_ERROR`, not "everything" |
| GET | `/api/search/clauses` | clause-level hits with provenance | every item already citable: `clause_id`, pages, offsets, `offset_basis` |
| GET | `/api/library/facets` | filter values and counts | `department`, `document_type`, years — with an explicit `null` bucket, since both columns are nullable |
| GET | `/api/search/suggest` | type-ahead | titles and `reference_no` only, never clause text |

Deliberately **not** proposed: a "related documents" endpoint. It needs
document similarity, and with 4 documents from 1 authority and 0 version pairs
(D-10) any number it produced would be noise.

---

## 5. Open decisions that gate this module

| ID | Decision | Effect here |
|---|---|---|
| **D-10** | which documents form the corpus | **binding.** Relevance on 4 documents from 1 authority is not a measurement. The baseline can be *built* now; it cannot be *evaluated* meaningfully |
| **D-7** | where retrieval embeddings live | gates `semantic` and `hybrid`. No vector DB without a written justification (`CLAUDE.md` §24 rule 5) |
| **new** | which segmentation method is searched by default | `clause_aware` (363 segments) is the better unit for citation; `paragraph` (138) gives more context. Both are stored, so this is a parameter — but a default has to be chosen and recorded |
| **new** | relevance ground truth | a query set with judged results, committed under `data/annotations/`, written **before** any metric — `CLAUDE.md` §14 rule 1 |

---

## 6. First three steps, in order

1. **`keyword` only**, behind `search_segments`, with unit tests that do not
   need a server plus `@pytest.mark.integration` tests that do.
2. **Measure it against today's `ILIKE` listing** on a committed query set.
   That is a real baseline-vs-comparison with no new dependency.
3. **Only then** argue D-7 for `semantic`, with the keyword numbers in hand.

Related reading: `backend/docs/backend-integration-contract.md` (what the API
guarantees), `backend/docs/phase3-postgresql.md` (schema rationale),
`backend/docs/PROJECT_STRUCTURE_AND_MODULE_GUIDE.md` (where everything lives).
