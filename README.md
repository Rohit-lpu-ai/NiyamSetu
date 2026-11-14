# NiyamSetu
🚀 **Project Idea: NiyamSetu — Making Government Rules Easy to Understand**

### 🧠 What is the project?

Government notifications, schemes, rules and legal documents are usually written in complicated language.

**NiyamSetu** is an NLP-based system that reads these documents, understands the important information, detects what has changed, and explains it to people in **simple language and regional languages**.

Basically:

> **Complex Government Document → NLP Understanding → Simple, Actionable Explanation**

---

### 🔨 What are we actually going to build?

We'll collect real government notifications/schemes and build an NLP pipeline that can understand things like:

- Who does this rule apply to?
- What is the person/department required to do?
- What is the deadline?
- What changed from the previous rule?
- Which authority/document/section is involved?
- Is this rule new, amended or replaced?

Then users can simply ask:

> "What changed in this new notification?"

> "Does this apply to me?"

> "What do I need to do?"

> "Explain this in Hindi/Punjabi."

And the system gives a **simple answer with the original source/section**, so the answer can be verified.

---

### 🎯 Main Features

**1. Document Understanding**
- Upload/search government documents
- Automatically classify them
- Extract important information

**2. NLP Analysis**
- Named Entity Recognition
- Important dates/deadlines
- Organizations, schemes, laws, forms, locations
- Requirement/action extraction
- Semantic understanding

**3. Policy Change Detection 🔥**
Compare old vs new documents and show:

> Form 16 → Form 17  
> Quarterly → Monthly  
> Effective from → 1 April 2027

So instead of reading two 30-page documents, users can see **exactly what changed**.

**4. Ask Questions**
Users can ask questions about policies/documents in normal language.

**5. Simple Language Mode**
Convert complicated government language into understandable language.

**6. Multilingual**
Initially English + Hindi, with the possibility of Punjabi and other Indian languages later.

**7. Source-Based Answers**
Every important answer should show:

> Document → Page → Section/Clause

So we're not just trusting an AI-generated answer.

---

### 🖥️ What could the interface look like?

Something simple:

**Dashboard**

```text
┌──────────────────────────────────────┐
│ NiyamSetu                             │
│                                      │
│ 🔍 Search government policies...     │
│                                      │
│ [Upload Document]                    │
│                                      │
│ Recent Updates                       │
│                                      │
│ 🔴 New Policy        🟡 Amended      │
│ 🟢 Scheme Update     🔵 Notification │
└──────────────────────────────────────┘
```

When opening a document:

```text
POLICY CHANGE

What changed?
────────────────────
Old: Quarterly submission
New: Monthly submission

Who is affected?
District Offices

Deadline:
15 November 2026

[Explain in Hindi]

Source:
Notification 42/2026
Section 3.2
```

Clean and understandable. **Not another over-designed AI dashboard.**

---

### 👨‍👩‍👧‍👦 Who does this help?

**Parents / normal citizens**

They don't need to understand complicated government language to know what they need to do.

**Local people**

They can access information in Hindi/Punjabi instead of struggling with English legal terminology.

**Students**

They can understand government schemes, policies and official documents quickly.

**Businesses**

They can track regulatory changes that affect them.

**Government/organizations**

They can quickly identify changes across large numbers of documents.

**International users/researchers**

They can understand Indian policies and regulations without manually going through hundreds of pages.

---

### 📈 How can we scale it later?

We don't need to build everything initially.

Start with:

> **Government notifications + English/Hindi + policy change detection**

Later we can add:

- Punjabi + more Indian languages
- Legal documents
- Government schemes
- Court/legal information
- Consumer complaints
- Personalized citizen assistance
- Voice input
- Mobile application
- WhatsApp/Telegram interface
- More advanced policy knowledge graphs
- Automatic monitoring of new government notifications

Eventually it could become:

> **"Google for understanding Indian government rules and policies."**

---

### 💪 Why is this stronger than a normal AI project?

Because this isn't simply:

> PDF → RAG → ChatGPT → Answer

We're actually studying and implementing **NLP**:

**Classical NLP**
→ tokenization, POS, parsing, regex, etc.

**ML NLP**
→ classification, information extraction

**Modern NLP**
→ BERT/Transformers, embeddings, fine-tuning

**Advanced system**
→ semantic search, policy comparison, multilingual NLP, RAG

And we can actually compare:

> **Traditional NLP vs Transformer-based NLP**

So the project is useful **and** academically connected to what we're learning.

---

### 🏆 The main idea in one sentence

> **NiyamSetu is an NLP-powered platform that turns complicated government documents into simple, searchable, multilingual and actionable information while detecting what changed between policies.**

For now, **this is just the idea**.

If everyone likes the direction, then we can move to the next step:

**Problem Statement → Requirements → Features → Architecture → Tech Stack → Dataset → Development Plan**

and decide exactly what we're going to build for the first version.

---

# Backend — running it

> The sections above describe the **product idea**. This section is the
> **backend as actually built** (Person 1: Backend + Document NLP).
>
> Status 2026-11-14: ingestion, PostgreSQL persistence, the FastAPI layer,
> **Library & Search** and **Compare** are implemented and verified —
> **1,075 tests passing, 1 skipped, 0 failed** (196 need PostgreSQL).
>
> Search is PostgreSQL full-text over the stored `tsvector`, measured against a
> substring baseline:
> `backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`.
>
> Compare aligns the clauses of two issues of a policy and classifies every one
> as added, removed, modified or unchanged, with a **verified** citation on both
> sides. It runs on **9 genuine RBI master-circular issues** acquired for it —
> three circulars × three annual issues, 12 comparable version pairs, 6 of them
> declared by the issuing authority — and **5,104 of 5,104 citations resolve**:
> `backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md`.
> The single skipped test checks the TF-IDF implementation against
> scikit-learn, which is deliberately not a dependency.
> Teammates integrating against it should read
> [`backend/docs/backend-integration-contract.md`](backend/docs/backend-integration-contract.md).

## 1. Prerequisites

- **Python 3.11** (3.11.9 confirmed)
- **PostgreSQL 14+** (18.6 confirmed). The Windows installer puts `psql` in
  `C:\Program Files\PostgreSQL\18\bin`, which is **not on PATH** by default —
  that is normal and nothing here needs it.

## 2. Environment

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

```bash
# macOS / Linux
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Database configuration

```bat
copy .env.example .env
```

Edit `.env` and set `NIYAMSETU_DB_PASSWORD`. `.env` is **git-ignored**; never
commit it. `.env.example` holds placeholders only.

Create the two databases (the test one must have `test` in its name — the code
refuses to run destructive tests otherwise):

```sql
CREATE DATABASE niyamsetu;
CREATE DATABASE niyamsetu_test;
```

## 4. Migrations

```bat
.venv\Scripts\python -m backend.app.db.migrate --status
.venv\Scripts\python -m backend.app.db.migrate
```

Plain SQL files in `backend/app/db/migrations/`, applied in filename order. The
runner records each file's SHA-256 and **refuses to continue if an applied
migration was edited** — add a new migration instead.

## 5. Build the corpus and load it

`data/raw/` and `data/processed/` are git-ignored, so a fresh clone has no
documents. With the source PDFs in `data/raw/` and `data/manifest.csv` present:

```bat
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data/manifest.csv
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all
.venv\Scripts\python -m backend.app.db.loader
```

Expected on the current corpus: 4 documents, 4 versions, 57 pages, 501 segments
(138 paragraph + 363 clause-aware), 552 segment-page rows.

## 6. Checks

```bat
.venv\Scripts\python -m evaluation.data_quality
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest -m integration        :: the 129 needing PostgreSQL
.venv\Scripts\python -m pytest -m "not integration"  :: the 631 that do not
```

## 7. Run the API

```bat
.venv\Scripts\python -m uvicorn backend.app.api.main:app --reload
```

| | |
|---|---|
| **Swagger UI** | http://127.0.0.1:8000/docs |
| ReDoc | http://127.0.0.1:8000/redoc |
| OpenAPI JSON | http://127.0.0.1:8000/openapi.json |
| Health | http://127.0.0.1:8000/api/health |

### Example requests

```bash
curl http://127.0.0.1:8000/api/health
curl "http://127.0.0.1:8000/api/documents?limit=5"
curl http://127.0.0.1:8000/api/documents/DOC-0001
curl "http://127.0.0.1:8000/api/documents/DOC-0004/clauses?method=clause_aware&limit=3"

# Library & Search
curl "http://127.0.0.1:8000/api/search?q=capital%20adequacy&limit=3"
curl "http://127.0.0.1:8000/api/search/clauses?q=transfer+of+shares&limit=5"
curl "http://127.0.0.1:8000/api/search/suggest?q=capital"
curl http://127.0.0.1:8000/api/library/facets

# Compare (needs the version-pair corpus: see section 9)
curl http://127.0.0.1:8000/api/compare/options
curl -X POST http://127.0.0.1:8000/api/compare \
  -H 'Content-Type: application/json' \
  -d '{"base_version_id":"VER-0010","compare_version_id":"VER-0011",
       "change_types":["added","removed","modified"]}'
curl "http://127.0.0.1:8000/api/compare/entities?version_id=VER-0010&limit=5"
curl http://127.0.0.1:8000/api/compare/entities/labels

curl -X POST http://127.0.0.1:8000/api/documents/ingest \
  -F "file=@data/raw/your-document.pdf" \
  -F "title=Your Document Title" \
  -F "document_group=your-document-group" \
  -F "document_type=circular"
```

## 8. Try the search UI

```bat
:: terminal 1 - the API over the 17-document sample corpus
set NIYAMSETU_DB_NAME=niyamsetu_sample
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8000

:: terminal 2 - the temporary test frontend
.venv\Scripts\python search_preview\serve.py --port 5173
```

Open **http://127.0.0.1:5173**. Library listing with live counts and filters,
ranked search results with page references, and a document view that highlights
a clause's stored character range inside the page text it came from.

`search_preview/` is a **throwaway developer UI for testing search**, not
Person 2's product frontend. The 17-document corpus lives in a separate
database; the 4-document development corpus is untouched. To build it:

```bat
.venv\Scripts\python data\sample_corpus\fetch_sample_corpus.py
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data\sample_corpus\manifest.csv --output-dir data\sample_corpus\processed
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all --processed-dir data\sample_corpus\processed
.venv\Scripts\python -m backend.scripts.setup_sample_database
```

## 9. Try the Compare UI

Compare needs two issues of the *same* policy, and neither the 4-document
development corpus nor the 17-document search corpus has any — every document
in both has exactly one version. So Compare has its own corpus: **9 genuine
RBI master-circular issues**, three circulars in three successive annual
issues, in a third database.

```bat
:: build it once - downloads from www.rbi.org.in, verifies every reference number
.venv\Scripts\python data\version_pairs\fetch_version_pairs.py
.venv\Scripts\python data\version_pairs\fetch_version_pairs.py --verify
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data\version_pairs\manifest.csv --output-dir data\version_pairs\processed --verify-checksums
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all --processed-dir data\version_pairs\processed
.venv\Scripts\python -m backend.scripts.setup_compare_database
```

```bat
:: terminal 1 - the API over the version-pair corpus
set NIYAMSETU_DB_NAME=niyamsetu_compare
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8010

:: terminal 2 - the temporary test frontend
.venv\Scripts\python compare_preview\serve.py --port 5174 --api http://127.0.0.1:8010
```

Open **http://127.0.0.1:5174**. Pick a document and two of its issues, and you
get the counts, a side-by-side word-level diff, filters for added / removed /
modified / unchanged, and — for either side of any change — the document,
version, clause, pages, character offsets and the verbatim text sliced out of
the page.

`compare_preview/` is a **throwaway developer UI for testing Compare**, not
Person 4's product interface. Both previews can run at once (5173 and 5174),
which is how "did Compare break search?" gets answered.

A real result, from the API: between the 2017 and 2018 issues of the Minority
Communities master circular — 275 and 277 clauses — Compare reports 235
unchanged, 40 modified, 2 added, 0 removed, and explains one of them as *"the
percentage changed from 25%, 40 percent and 10 per cent to 25%, 40 percent and
75%; Small Finance Banks is now named, and was not before."*

The evaluations:

```bat
.venv\Scripts\python -m evaluation.comparison_eval
.venv\Scripts\python -m evaluation.entity_extraction_eval
```

## 10. Documentation map

| Document | What it covers |
|---|---|
| [`backend/docs/PROJECT_STRUCTURE_AND_MODULE_GUIDE.md`](backend/docs/PROJECT_STRUCTURE_AND_MODULE_GUIDE.md) | **Read first.** What exists, where it lives, where your module goes |
| [`backend/docs/BACKEND_FINAL_COMPLETION_REPORT.md`](backend/docs/BACKEND_FINAL_COMPLETION_REPORT.md) | What is built and what was verified, with the honest gaps |
| [`backend/docs/backend-integration-contract.md`](backend/docs/backend-integration-contract.md) | **Start here if you are integrating.** IDs, schemas, provenance rules, errors |
| [`backend/docs/api.md`](backend/docs/api.md) | Endpoints, examples, ingestion flow |
| [`backend/docs/phase3-postgresql.md`](backend/docs/phase3-postgresql.md) | Schema design, every decision with a viva Q&A |
| [`backend/docs/final-backend-evaluation.md`](backend/docs/final-backend-evaluation.md) | **Every metric**, with sample sizes and limitations |
| [`backend/docs/evaluation-audit.md`](backend/docs/evaluation-audit.md) | What is measured, what is not, what is pending |
| [`backend/docs/pdf-extraction.md`](backend/docs/pdf-extraction.md), [`backend/docs/phase2-segmentation.md`](backend/docs/phase2-segmentation.md) | Pipeline design and findings |
| [`backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md`](backend/docs/LIBRARY_SEARCH_IMPLEMENTATION_REPORT.md) | **Library & Search**: what was built, measured results, defects, limitations |
| [`backend/app/library_search/README.md`](backend/app/library_search/README.md) | Search endpoint reference and the reuse map |
| [`backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md`](backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md) | **Compare**: the teammate-module audit, the integration, every measured number, and what is deliberately *not* measured |
| [`backend/app/comparison/README.md`](backend/app/comparison/README.md) | Compare module reference: the four-stage matcher, evidence, why no table |
| [`data/version_pairs/README.md`](data/version_pairs/README.md) | The genuine RBI version-pair corpus and its provenance |
| [`data/annotations/entities/README.md`](data/annotations/entities/README.md) | The entity annotation guideline, written before the annotations |
| [`CLAUDE.md`](CLAUDE.md) | Project reference and open decisions |

## 11. Known limitations

Honest and load-bearing — see `backend/docs/final-backend-evaluation.md` §9 for the full list.

- **The verified development corpus is 4 documents, one authority (RBI), zero
  scanned** (D-10). Nothing here generalises to government documents at large.
  Two further corpora exist in separate databases — 17 documents / 3
  authorities for search, and 9 documents / 3 genuine version chains for
  Compare — but all 9 of the version-pair documents are also RBI, so D-10 is
  advanced rather than closed.
- **Compare has no alignment accuracy figure, deliberately.** No clause-level
  annotation exists for the 288 pages of master circulars, so the evaluation
  reports invariants (12/12 version pairs pass), a self-comparison check (929
  clauses against themselves: 100% unchanged) and descriptive counts — never a
  score. Annotating one real pair is the highest-value next step.
- **Entity extraction: only the rule-based condition was measured.** Exact
  span F1 **0.61** / relaxed **0.69** on 30 clauses and 90 mentions judged by
  one non-independent annotator. `AUTHORITY` recall is **0.22** — a closed list
  of patterns cannot find the individual bank names in a district annexure,
  which is the measured argument for a statistical model. CRF and transformer
  conditions were **not run**, with the reasons recorded and pinned by tests.
- **No independent validation of segmentation.** Boundary F1 comes from 21
  clauses annotated by one non-independent annotator; inter-annotator agreement
  is **PENDING**.
- **Cleaning correctness is unmeasured.** Removals are reversible and audited,
  which makes a mistake recoverable — not absent.
- **Extraction has no accuracy figure** (no ground-truth transcription).
- **Devanagari text is corrupted** and no available extractor fixes it.
- **Tables flatten**; up to 39.9% of clause-aware segments overlap a detected
  table region (an upper bound, not a false-positive rate).
- **No authentication.** Anyone who can reach the port can ingest.
