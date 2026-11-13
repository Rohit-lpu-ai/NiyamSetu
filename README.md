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
> Status 2026-11-13: ingestion, PostgreSQL persistence and the FastAPI layer are
> implemented and verified — **600 tests passing, 0 failed, 0 skipped**
> (re-run 2026-11-13 after the repository cleanup; 590 of them predate it, 10
> pin the Library & Search scaffold).
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
.venv\Scripts\python -m pytest -m integration        :: the 73 needing PostgreSQL
.venv\Scripts\python -m pytest -m "not integration"  :: the 527 that do not
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

curl -X POST http://127.0.0.1:8000/api/documents/ingest \
  -F "file=@data/raw/your-document.pdf" \
  -F "title=Your Document Title" \
  -F "document_group=your-document-group" \
  -F "document_type=circular"
```

## 8. Documentation map

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
| [`backend/app/library_search/README.md`](backend/app/library_search/README.md) | **Next module.** Library & Search: scope, reuse map, planned endpoints |
| [`CLAUDE.md`](CLAUDE.md) | Project reference and open decisions |

## 9. Known limitations

Honest and load-bearing — see `backend/docs/final-backend-evaluation.md` §9 for the full list.

- **4 documents, one authority (RBI), zero version pairs, zero scanned** (D-10).
  Nothing here generalises to government documents at large, and Compare has no
  data to run on.
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
