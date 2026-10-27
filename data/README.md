# NiyamSetu — Data: Collection, Preparation and Conventions

> Written 2026-10-28. Owner: Person 1 (Backend + Document NLP).
>
> ## Status: NO DATA HAS BEEN COLLECTED
>
> This directory currently contains **only this file**. There are no PDFs, no
> processed documents, no annotations and no manifest. Nothing described below has
> been executed. This file defines the *intended* process so that collection, when
> it happens, is consistent and reproducible.
>
> Which documents form the corpus, and who collects them, is **Open Decision D-10**
> (`CLAUDE.md` §22.2) and is unresolved. Every NLP experiment in this project is
> blocked on it.

---

## 1. What belongs here, and what does not

| Path | Contents | Committed to git? |
|---|---|---|
| `data/raw/` | Original, unmodified PDFs exactly as downloaded | **No** — git-ignored |
| `data/processed/` | Extracted page text and segments as JSON | Only if small; otherwise regenerate |
| `data/annotations/` | Hand-annotated evaluation samples + the guideline | **Yes** — small, and essential for reproducibility |
| `data/manifest.csv` | One row per source document: provenance and metadata | **Yes** |
| `data/README.md` | This file | **Yes** |

**Why raw PDFs are not committed:** government PDFs run to several MB each; a
corpus of 20–40 would bloat the repository permanently, and git cannot compress
them. The manifest plus the source URL is what makes the corpus reproducible.
Anyone can re-download from the recorded URL and verify against the recorded
SHA-256 checksum.

`.gitignore` must contain at least:

```gitignore
data/raw/
data/processed/
*.pdf
.env
__pycache__/
.venv/
.pytest_cache/
```

Exception: a handful of **tiny** fixture PDFs for tests are committed, under
`backend/tests/fixtures/`, never under `data/`. They are clearly labelled as
fixtures and are not part of the corpus.

---

## 2. Source selection and authenticity

### Inclusion criteria `[PROPOSED]`
- Published by an Indian government department, ministry or statutory authority.
- Downloaded from that body's **own domain** (or an official gazette portal).
- Machine-readable text PDF (see §2.3 on scanned documents).
- Public by design — notifications, circulars, schemes, guidelines, amendments.

### Exclusion criteria `[PROPOSED]`
- Any document whose issuing authority cannot be verified.
- Copies hosted on third-party aggregators, news sites or coaching websites,
  unless the original has also been located on the official domain.
- Documents containing personal data of identifiable individuals.
- Scanned / image-only PDFs — excluded from the MVP corpus pending Open
  Decision D-6.
- Anything synthetic. **We never create a fake "government document."** If an
  example is needed for a demo, it is labelled a fixture, never placed in the
  corpus, and never quoted as real policy.

### 2.3 Authenticity check — recorded per document
1. The URL's domain belongs to the issuing body.
2. The document carries its own identifying marks (notification/circular number,
   issuing office, date) consistent with the metadata we record.
3. `retrieved_at` is recorded at download time.
4. The SHA-256 checksum is recorded, so later tampering or a silent re-publication
   is detectable.

If any of 1–3 cannot be satisfied, the document is **not** added to the corpus.
Record the rejection and the reason in `data/rejected.csv` — a documented
exclusion is itself a result worth reporting.

### 2.4 Corpus composition target `[PROPOSED]`
- 20–40 documents, enough for meaningful evaluation and small enough to annotate honestly.
- Spread across at least 3 document types, so Person 2's classification task is not trivial.
- **At least 2 (ideally 4) policies present in two versions each** — Person 4's
  Compare feature is impossible without this, and it is the easiest requirement to
  forget during collection.
- A mix of lengths, including at least a few documents over 15 pages.

---

## 3. Naming conventions `[PROPOSED]`

Raw files keep a deterministic, filesystem-safe name:

```text
data/raw/<dept-slug>_<doctype>_<reference-or-date>_<version-label>.pdf

examples:
data/raw/minfin_notification_42-2026_v1.pdf
data/raw/minfin_notification_42-2026_v2.pdf
data/raw/edu-dept_scheme_2026-04-01_v1.pdf
```

Rules: lowercase; words separated by `-`; fields separated by `_`; ASCII only;
no spaces. The **original** filename as downloaded is preserved in the manifest
and in `document_versions.original_filename` — renaming for the filesystem must
never lose it.

Processed output mirrors the identifiers assigned at ingestion:

```text
data/processed/<version_public_id>/pages.json
data/processed/<version_public_id>/segments_paragraph.json
data/processed/<version_public_id>/segments_clause_aware.json
```

---

## 4. Manifest — `data/manifest.csv`

One row per source file. This is the human-maintained record of provenance; the
database is generated from it, not the other way round.

| Column | Required | Notes |
|---|---|---|
| `local_filename` | yes | the name under `data/raw/` |
| `original_filename` | yes | exactly as downloaded |
| `sha256` | yes | computed at download |
| `title` | yes | as printed on the document |
| `department` | no | leave empty if not stated — **do not guess** |
| `document_type` | no | notification / circular / scheme / guideline / amendment / other |
| `reference_no` | no | official number, as printed |
| `publication_date` | no | ISO-8601 (`YYYY-MM-DD`); empty if not printed |
| `source_url` | yes | the official page or direct PDF link |
| `retrieved_at` | yes | ISO-8601 date of download |
| `document_group` | yes | a collector-assigned key shared by versions of the same policy |
| `version_label` | yes | `v1`, `v2`, `2026-amendment`, … assigned by the collector |
| `supersedes` | no | the `version_label` this one replaces, if known |
| `is_scanned` | yes | `true` / `false` |
| `notes` | no | anything odd about the file |

**`document_group` and `version_label` are human decisions, deliberately.** The
pipeline never infers that two documents are versions of the same policy from
title similarity, and never infers version order from publication date
(`CLAUDE.md` §11.1, Open Decision D-9). The collector asserts the relationship;
the manifest records it; `supersedes` makes the order explicit.

**Empty is better than wrong.** A blank `department` is a documented gap that
Experiment C will count. A guessed `department` is a silent data error that
corrupts Person 2's classification labels and Person 5's filters.

---

## 5. Metadata requirements

Mandatory for every document (ingestion fails without them): `title`,
`original_filename`, `sha256`, `source_url`, `retrieved_at`, `document_group`,
`version_label`.

Optional, nullable in the schema because real documents omit them:
`department`, `document_type`, `reference_no`, `publication_date`, `supersedes`.

Generated by the pipeline, never hand-entered: `document_id`, `version_id`,
`clause_id`, `page_count`, `extraction_status`, `extraction_tool`,
`processing_warnings`, `ingested_at`.

---

## 6. Page and clause provenance

The non-negotiable rule for this project: **extracted text never loses its
address.**

- Page numbers are **1-based** and match the PDF's physical page order (not any
  printed page number on the page, which may differ — if they differ, record the
  discrepancy in `notes`).
- `pages.raw_text` is byte-for-byte what the extractor returned. Normalisation
  writes to a separate column and never overwrites it.
- Every clause carries `page_start` and `page_end`, inclusive at both ends.
- A clause spanning a page break keeps `page_start < page_end` **and** gets one
  `segment_pages` row per page touched, with character offsets into that page's
  raw text. We never collapse a spanning clause onto one page.
- Character offsets are into **raw** text. After normalisation they no longer
  align with normalised text, so highlighting over normalised text is approximate
  — a documented limitation (`docs/architecture.md` §4.4).
- If a clause's page cannot be determined, **ingestion fails loudly**. A guessed
  page reference is worse than no clause, because the whole project's claim is
  that citations can be trusted.

---

## 7. Annotation conventions

Annotations live in `data/annotations/`, with the guideline committed beside them.

```text
data/annotations/
├── README.md                          # the guideline — write this BEFORE annotating
├── segmentation/<version_id>.jsonl    # clause boundaries (Person 1, Experiment B)
├── entities/<version_id>.jsonl        # entity spans (Person 4)
├── alignment/<pair_id>.jsonl          # cross-version clause pairs (Person 4)
└── queries/                           # questions + relevance judgements (Persons 3, 5)
```

### Segmentation annotation `[PROPOSED]` — Person 1's sample
- Sample: 10 documents × about 5 pages, chosen to cover each document type and
  each clause-numbering style observed, not just the easy documents.
- One JSONL record per true clause:
  `{version_id, page_start, page_end, clause_number, heading, text_start_marker, text_end_marker}`.
- Markers are short verbatim snippets from the start and end of the clause, so the
  annotation survives a change in extraction output.
- Ambiguous cases get `"uncertain": true` plus a note, and are reported separately
  rather than silently counted as either right or wrong.

### Consistency procedure
1. Write the guideline first; include worked examples of the three hardest cases.
2. Annotate a 2-document overlap subset with a second annotator.
3. Compute and **report** agreement on that subset.
4. Where annotators disagree: amend the guideline, record the disagreement, and
   re-annotate the affected items. Do not quietly adopt one annotator's reading.
5. The guideline is versioned with the annotations. If it changes after annotation
   begins, say which records were produced under which version.

### Hard rule
**No evaluation number is written down before the annotation file and the scoring
script both exist and have been run.** Fabricated or estimated metrics are
academic dishonesty, and they are also useless — they tell us nothing about the
pipeline.

---

## 8. Data quality checks

Implemented in Phase 5 as `evaluation/data_quality.py` (Experiment C,
`CLAUDE.md` §15). Each check reports a count and the offending identifiers:

| Check | Why it matters |
|---|---|
| Empty extracted pages | Signals a scanned page or an extraction failure |
| Duplicate files (SHA-256) | The same document ingested twice as two versions |
| Near-duplicate documents | May be undeclared versions of one policy |
| Duplicate clause text within a version | Segmentation emitted the same text twice |
| Missing metadata per field | Quantifies the gaps; feeds Person 2's label availability |
| Page references outside `1..page_count` | A provenance bug — must be zero |
| Malformed clause identifiers | Breaks every downstream citation |
| Extraction failures | Documents silently absent from the usable corpus |
| Segmentation anomalies | e.g. one clause covering a whole document |
| Bad version relationships | Cyclic or cross-document `supersedes` chains |

Critical checks (invalid page references, malformed IDs, duplicate clause text)
are also pytest assertions, so a regression fails the test suite rather than
quietly degrading the corpus.

---

## 9. How to reproduce the dataset

Once Phases 1–3 exist, the documented procedure will be:

```bash
# 1. Re-download the corpus from the manifest's recorded URLs into data/raw/
python -m backend.app.cli fetch --manifest data/manifest.csv

# 2. Verify each file against its recorded checksum
python -m backend.app.cli verify --manifest data/manifest.csv

# 3. Create the database schema
python -m backend.app.db.migrate

# 4. Ingest the corpus (extraction + normalisation + both segmentation methods)
python -m backend.app.cli ingest --manifest data/manifest.csv

# 5. Run the data quality checks
python evaluation/data_quality.py --report evaluation/data-quality-report.md
```

**None of these commands exist yet.** They are the target interface, recorded
here so Phase 1 builds towards something specified. If a checksum in step 2 fails,
the document has been re-published or altered — record that, do not overwrite the
manifest.

---

## 10. Known limitations of the dataset

To be stated in the final report and the viva. Already known before collection
begins:

- **Small and hand-curated.** 20–40 documents cannot represent the diversity of
  Indian government publishing. No result here generalises without that caveat.
- **Selection bias.** We will naturally collect documents that are easy to find
  and machine-readable, which biases the corpus towards better-formatted,
  better-digitised departments.
- **Scanned documents excluded** (pending D-6), so the corpus is unrepresentative
  of older or state-level publishing, where scans are common.
- **English-dominant.** Hindi-language documents are not yet in scope; this limits
  what any multilingual claim can be based on (D-3).
- **Version pairs are scarce.** Finding genuine two-version policy pairs is the
  hardest part of collection, so the Compare evaluation sample will be small and
  must be reported as such.
- **Annotation is by project members**, not domain experts in government
  procedure. Clause-boundary judgements on unusual layouts are our reading, and
  the inter-annotator agreement number is the honest measure of how reliable that
  reading is.
- **No ground truth for answer quality.** Ask can be evaluated for retrieval and
  citation correctness, which are objective; "is this a good explanation" is not
  measured.

---

## 11. Privacy and safety

- Only documents published for public consumption. If a document unexpectedly
  contains personal data (names, addresses, identifiers of individuals), flag it in
  the manifest `notes` and remove it from the corpus.
- No credentials, keys or tokens ever stored in this directory.
- Large binaries are not committed (§1).
- The corpus is for coursework. Redistributing bulk copies of government PDFs is
  out of scope; the manifest-plus-URL approach means we never need to.
