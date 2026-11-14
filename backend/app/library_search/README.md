# Library & Search — module reference

**Status: `[IMPLEMENTED]`.** PostgreSQL full-text search over
`segments.text_search`, four endpoints, 170 tests. Measured results and every
limitation: **`backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`**.

**Owner:** Person 5 (Library intelligence + integration), per `CLAUDE.md` §7.
**Depends on:** Phases 3 and 4 — both `[IMPLEMENTED]` and verified.

> This file replaced a scaffold brief. If you are looking for the planning
> document that preceded the implementation, it is in this file's git history.

---

## 1. What it does

One job: **given a query, return the documents and clauses that match it, in a
defensible order, with the evidence to show why.**

```
GET /api/search            documents, ranked by their best matching clause
GET /api/search/clauses    clauses, each citable to a document and page
GET /api/search/suggest    type-ahead over titles, reference numbers, departments
GET /api/library/facets    filter values and corpus counts, read from the tables
```

Every clause hit carries `page_start`, `page_end` and a `pages` array with the
character range on each page it touches — the same provenance
`GET /api/documents/{id}/clauses` guarantees, because it is read from the same
`segment_pages` rows. A search result can therefore be turned into a citation
without a second query and without guessing.

### Explicitly not this module's job

| Not here | Where it lives |
|---|---|
| PDF extraction, cleaning, segmentation | `backend/app/document_processing/` |
| Any write to the document tables | `backend/app/db/loader.py`, used only by ingestion |
| Document / page / clause response shapes | `backend/app/api/schemas.py` |
| Answer *generation* over retrieved text | Person 3's `rag_qa` — retrieval here, answers there |
| Chunking text for an index | **nobody.** The stored segments *are* the chunks |
| A second document listing endpoint | `GET /api/documents`, extended additively |

The last two rows are the ones that cost the most to get wrong. Re-chunking
would produce offsets that no longer match `segment_pages`, and then a hit
could not be cited (`CLAUDE.md` §6, §9). A second listing endpoint would be a
second definition of what a document summary is.

---

## 2. The files

| File | What it holds |
|---|---|
| `repository.py` | The SQL. Indexed full-text match, `ts_rank_cd` ranking, `ts_headline` snippets, facets, suggestions, the ILIKE comparison baseline, and the `EXPLAIN` helpers. Read-only |
| `service.py` | Query validation, strategy choice, timing, and the response envelope that makes every result attributable to the condition that produced it |
| `api.py` | The two routers (`/search`, `/library`), mounted by `api/main.py` under `/api` |
| `schemas.py` | Only the genuinely new shapes: ranking metadata, facets, suggestions |
| `__init__.py` | Public surface and `CONTRACT_VERSION` |

```
backend/app/api/main.py          mounts both routers under /api
  └─ library_search.api          FastAPI routes + validation
      └─ library_search.service  strategy, validation, timing, envelope
          └─ library_search.repository   SQL over segments.text_search
              └─ backend.app.db.engine   the EXISTING pooled connection
```

---

## 3. How the search works, and why each choice

`segments.text_search` is a **stored `tsvector` generated column with a GIN
index**, created by `0001_initial_schema.sql` for exactly this purpose and
queried by nothing until now:

```sql
text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
CREATE INDEX segments_text_search_idx ON segments USING GIN (text_search);
```

| Choice | Why, and what the alternative costs |
|---|---|
| `websearch_to_tsquery` | Supports quoted phrases, `or`, `-exclusion`, and never raises on punctuation a user typed. `plainto_tsquery` ANDs every word and cannot express a phrase; `to_tsquery` raises on `!!!` |
| `ts_rank_cd` | Cover density: terms occurring *close together* rank higher, so "capital adequacy ratio" in one sentence beats "capital" on line 1 and "adequacy" on line 40 |
| normalisation `1\|32` | `1` divides by log(length) so a 2,000-character annexure does not outrank a 90-character clause by sheer size; `32` maps the score into `0 < score < 1` so it is readable |
| one method per query | Paragraph and clause-aware segments cover **the same text**. A union would return every passage twice. `method` is a parameter, default `clause_aware`; never both |
| `version_scope=latest` | One version per document by default, so an amended policy does not return the same clause twice. An explicit `version_id` overrides it |
| `extraction_status = 'ok'` only | A `partial` extraction must not surface as evidence — the same rule the schema's `segment_citations` view enforces |
| query function inlined, not in a CTE | A materialised CTE hides the constant from the planner and the GIN index stops being usable |

**Document score** = the best clause score in the document **+ 1.0 if the
title, reference number or department matched.** The bonus is larger than any
possible `ts_rank_cd` value (the `32` flag caps it below 1), so a document whose
own name matches always outranks one that merely mentions the words. A document
can match on metadata alone, and then `best_clause` is `null` — a meaningful
answer, not a missing field.

### Known limitation: segment-level AND

`websearch_to_tsquery` ANDs its terms, and the AND is evaluated **within one
segment**. A document genuinely about "insurance intermediaries registration"
can mention each word in a different clause and match nothing.

Document-level aggregation was **implemented, measured and rejected**: on the
20-query set it recovered 1 relevant document and admitted 4 irrelevant ones,
and it is a sequential aggregate with no index support. The experiment is
reproducible — `evaluation/library_search_eval.py::compare_match_granularity`,
results in `evaluation/library-search/results.json`.

### Known limitation: the `english` configuration on a bilingual corpus

12 of the 17 sample documents are bilingual Hindi/English, and the Hindi half
extracts as **real Devanagari** (20,712 characters in the longest). The
`english` configuration indexes those tokens unstemmed: they are searchable
only by exact form, Hindi queries will not stem correctly, and a snippet can
open mid-Hindi before reaching the English match. Recorded, not worked around —
D-3 puts Hindi out of scope for this phase.

---

## 4. Reuse: what this module does not re-declare

| Reuses | Instead of |
|---|---|
| `db.engine.connection()` | a second engine or pool |
| `db.config.database_url()` | reading `.env` again |
| `db.repository.get_pages()` | its own page reader |
| `api.errors.ApiError` + handlers | a second error envelope |
| `api.schemas.ErrorResponse` | a second error model |
| `GET /api/documents` (extended) | a Library listing endpoint of its own |
| FastAPI `Query` validation | hand-rolled parameter checks |

`GET /api/documents` gained `date_from`, `date_to` and `sort` plus five
`latest_version` fields on each item. Additive (§12.4): omitting them
reproduces the previous behaviour exactly, and `CLAUDE.md` §12.3 specified
them all along — Phase 4 simply shipped without them.

---

## 5. Endpoint reference

### `GET /api/search` — documents

| Parameter | Default | Notes |
|---|---|---|
| `q` | required | 1–300 chars. `websearch_to_tsquery` syntax |
| `strategy` | `fts` | `fts` \| `ilike`. `semantic`/`hybrid` → 422, not implemented (D-7) |
| `method` | `clause_aware` | `clause_aware` \| `paragraph` |
| `sort` | `relevance` | `relevance` \| `date_desc` \| `date_asc` \| `title` |
| `version_scope` | `latest` | `latest` \| `all` |
| `department` | — | exact match; `null` selects "not stated" |
| `document_type` | — | the schema's vocabulary |
| `date_from`, `date_to` | — | ISO dates, inclusive |
| `limit`, `offset` | 20, 0 | limit capped at 100 |

```bash
curl "http://127.0.0.1:8000/api/search?q=capital%20adequacy&limit=3"
```

```json
{
  "query": "capital adequacy",
  "strategy": "fts",
  "parsed_query": "'capit' & 'adequaci'",
  "sort": "relevance",
  "segmentation_method": "clause_aware",
  "version_scope": "latest",
  "total": 2,
  "limit": 3,
  "offset": 0,
  "took_ms": 7.8,
  "warnings": [],
  "items": [
    {
      "document_id": "DOC-0004",
      "title": "Reserve Bank of India (Commercial Banks - Prudential Norms on Capital Adequacy) Eleventh Amendment Directions, 2026",
      "department": null,
      "document_type": "amendment",
      "score": 1.0457,
      "matched_clauses": 2,
      "metadata_match": true,
      "best_clause": { "clause_id": "CLS-0000248", "page_start": 1, "pages": [...] }
    }
  ]
}
```

### `GET /api/search/clauses` — clauses

Everything above plus `document_id`, `version_id`, `segment_type`, `page_from`,
`page_to`, `include_text`. Each item adds `clause_number`,
`clause_number_ambiguous`, `heading`, `level`, `snippet`, `page_start`,
`page_end`, `pages[]`, `offset_basis`.

`snippet` marks matches with `[[hl]]` / `[[/hl]]` sentinels and is **plain
text, not HTML** — the text comes out of a PDF, and handing a browser markup
assembled from document content is how a document becomes a script. Both
sentinels were verified absent from all 501 original segments before being
chosen.

### `GET /api/search/suggest`

`q` (0–120 chars), `limit` (1–25). Titles, reference numbers and departments
only — **never clause text**: a clause fragment offered as a suggestion looks
like an answer and carries no page reference to check it against. Fewer than
two characters returns an empty list, not a 422.

### `GET /api/library/facets`

No parameters. `documents`, `versions`, `pages`, `segments` per method,
`departments`, `document_types`, `publication_years`, `extraction_statuses`,
`date_range`. Every value is a `GROUP BY` over the real tables. `null` is a
**real bucket** labelled "Not stated", and filtering on it works — one of the
four original documents does not print its department.

---

## 6. Tests

| Module | Tests | Needs PostgreSQL |
|---|---:|---|
| `test_library_search_unit.py` | 54 | no |
| `test_library_search_api.py` | 38 | no (fake service) |
| `test_library_search_scaffold.py` | 22 | no (module boundaries) |
| `test_library_search_integration.py` | 56 | **yes** |
| **total** | **170** | 56 |

Plus `evaluation/library_search_eval.py` (retrieval quality, 20 queries) and
`evaluation/library_search_e2e.py` (51 HTTP flow checks against a running
server).

---

## 7. Still open

| ID | What it gates |
|---|---|
| **D-10** | **The binding one.** 17 documents, 3 authorities. Relevance numbers from a 20-query set judged by one non-independent annotator are diagnostic, not validated — `data/annotations/search_relevance/README.md` §0 |
| **D-7** | `semantic` and `hybrid`. Both need an embedding model, a pinned dependency and a written justification. The seam exists; nothing claims they work |
| new | A second annotator for the relevance set. `template.json` is ready |
| new | Whether `clause_aware` stays the default searched method (it is the better citation unit; `paragraph` gives more context) |

Read next: `backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md` for the
measured results, the FTS-vs-ILIKE comparison, the defects found and fixed, and
the startup instructions.
