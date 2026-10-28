# PDF extraction — design, contract and findings

> Phase 1 of the NiyamSetu backend. Status: **implemented and tested**.
> Owner: Person 1 (Backend + Document NLP). Written 2026-10-28.
>
> This document describes code that exists in `backend/app/document_processing/`
> and has been run. Where something is not built, it says so.

---

## 1. What this phase does

```
Official PDF
  -> validate the bytes          (exists, non-empty, size cap, %PDF- header)
  -> open                        (encryption and parse failures typed, not swallowed)
  -> extract text page by page   (1-based, nothing merged, nothing dropped)
  -> normalise conservatively    (into a separate field, never over the raw text)
  -> measure and classify pages  (empty / text-poor / meaningful)
  -> derive extraction_status    (ok / partial / failed)
  -> structured JSON             (ready for PostgreSQL insert in Phase 3)
```

Not in this phase: PostgreSQL, FastAPI, clause segmentation, entity extraction,
retrieval, OCR, header/footer removal.

---

## 2. Library choice: pypdf

`pypdf==6.18.1`. Reasons, in order of weight:

1. **Indexed per-page access.** `reader.pages[i]` is what makes page-level
   provenance possible at all, and page provenance is the project's central
   claim.
2. **Pure Python, no transitive dependencies.** Five students on five machines
   install one wheel with no compiler. PyMuPDF ships a compiled binding to
   MuPDF; pdfplumber pulls in pdfminer.six.
3. **Already in the project's toolchain** (`CLAUDE.md` §15), so Phase 1 adds no
   new runtime dependency at all.
4. **Also gives the `/Info` metadata dictionary** and encryption detection,
   which the validation step needs.

### The honest trade-off

pypdf has no layout-analysis model. Its text extraction is weaker than PyMuPDF's
on multi-column layouts and tables, and §7 of this document shows exactly where
that bites on our real documents.

**pypdf was chosen on availability and simplicity, and has not yet been compared
against anything.** The comparison is Experiment A (`CLAUDE.md` §15) and it
needs the corpus to exist first. `CLAUDE.md` §24 rule 5 requires a written
justification before adding a dependency, and "the tables look bad" is not yet a
measurement.

### The seam for swapping extractors

Every byte of text in the pipeline comes through one function:

```python
# backend/app/document_processing/pdf_extractor.py
def _extract_page_text(reader: PdfReader, page_index: int) -> str: ...
```

A second extractor plugs in there. Nothing else in the pipeline — validation,
normalisation, measurement, the schema — needs to change.

---

## 3. Module layout

```text
backend/
├── app/
│   └── document_processing/
│       ├── __init__.py          public API re-exports
│       ├── errors.py            typed exceptions, each with a stable `code`
│       ├── identity.py          checksums and stable document/version ids
│       ├── normalization.py     the conservative cleaning steps
│       ├── schemas.py           the data contract (dataclasses)
│       ├── manifest.py          reading data/manifest.csv
│       ├── pdf_extractor.py     validation + extraction (the library)
│       └── cli.py               command-line front end only
└── tests/
    ├── conftest.py                     synthetic PDF fixtures
    ├── pdf_builder.py                  a minimal PDF writer, test-only
    ├── inspect_real_extraction.py      manual-inspection helper (not a test)
    └── test_*.py                       90 tests
```

**`pdf_extractor.py` imports no FastAPI, no SQLAlchemy and no CLI code.** It can
be called from a test, a script, a notebook or a future API endpoint.

### Why dataclasses and not Pydantic

The extraction library stays importable with nothing but `pypdf`. Dataclasses
plus explicit `__post_init__` checks give the validation this phase needs.
Pydantic belongs at the API boundary in Phase 4, where request parsing and HTTP
error mapping are the actual job. Adding it now would couple the extraction
library to the web stack for no benefit.

---

## 4. Identifiers — the stable-id strategy

The requirement is that processing the same PDF twice yields the same
identifiers, or every re-run looks like a new document and duplicate detection
is impossible.

| Identifier | Derived from | Why |
|---|---|---|
| `checksum` | SHA-256 of the file's bytes | **This is** the identity of the physical file. Two uploads with the same checksum are the same file — that is duplicate detection, with no text comparison needed. |
| `version_id` | `VER-<checksum[:16]>` | Deterministic, so re-processing is idempotent. Changes the instant one byte of the source changes. |
| `document_id` | `DOC-<sha256(document_group)[:16]>` | The *logical policy* identity. It **cannot** come from the file bytes, because two versions of one policy are two different files that must share one `document_id`. |

### How a file becomes a version of an existing document

By a **human saying so**, via the `document_group` column of
`data/manifest.csv`. Two files with the same `document_group` get the same
`document_id` and different `version_id`s.

With **no** `document_group`, each file becomes its own logical document. That
is deliberate: `CLAUDE.md` Open Decision D-9 forbids inferring a version
relationship from title similarity or publication date. The collector asserts
it; the manifest records it; the pipeline carries it.

Both behaviours are tested (`test_document_group_makes_two_files_one_logical_document`,
`test_without_a_document_group_each_file_is_its_own_document`).

### Bridging to PostgreSQL in Phase 3

`CLAUDE.md` §11.3 specifies sequential `public_id` values (`DOC-0001`). Those
are assigned by the database, which does not exist yet. The content-derived ids
here are the **fingerprint** the database rows will be keyed on and matched
against: the fingerprint recognises a file we have already seen, the sequence is
how the API refers to it. Both can coexist; neither replaces the other.

### 16 hex characters?

64 bits of the digest. Far more than enough to keep a corpus of a few thousand
documents collision-free, and short enough to read in a log line.

---

## 5. Input and output contracts

### Input

```python
from pathlib import Path
from backend.app.document_processing import SourceMetadata, extract_pdf

result = extract_pdf(
    Path("data/raw/rbi_master-circular_272mc_v1.pdf"),
    source_metadata=SourceMetadata(
        title="Master Circular - Credit facilities to SCs & STs",
        issuing_department="Financial Inclusion & Development Dept., Central Office",
        document_type="circular",
        publication_date="2026-10-01",
        source_url="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/272MC...PDF",
        retrieved_at="2026-10-28",
        document_group="rbi-master-circular-sc-st-credit-facilities",
        version_label="2026-10-01",
    ),
)
```

Everything in `SourceMetadata` is optional. **An omitted field stays `None`.**
Nothing is ever guessed — a blank `department` is a documented gap that
Experiment C counts; a guessed one silently corrupts Person 2's classification
labels and Person 5's filters.

### Output

`ExtractionResult.to_dict()` is the authoritative JSON contract. Nothing else
builds these dictionaries, so there is one place to change it (`CLAUDE.md`
§12.4). `schema_version` is currently `1.0.0`; read it rather than assuming a
shape.

```json
{
  "document": {
    "schema_version": "1.0.0",
    "document_id": "DOC-5cb05e2353d62271",
    "version_id": "VER-b8a13c2a7651eb84",
    "filename": "rbi_master-circular_272mc_v1.pdf",
    "checksum": "b8a13c2a7651eb84e68d733ff50125f02bb8a9e15d4cec6102f4661a628ac658",
    "file_size_bytes": 265034,
    "page_count": 8,
    "extraction_status": "ok",
    "extraction_tool": "pypdf 6.18.1",
    "extracted_at": "2026-10-28T09:12:44+00:00",
    "pdf_metadata": { "creator": "Acrobat PDFMaker 26 for Word", "title": null },
    "source_metadata": {
      "title": "Master Circular - Credit facilities to SCs & STs",
      "issuing_department": "Financial Inclusion & Development Dept., Central Office",
      "document_type": "circular",
      "reference_no": "RBI/2026-27/272 FIDD.CO.GSSD.BC.No.11/09.09.001/2026-27",
      "publication_date": "2026-10-01",
      "source_url": "https://rbidocs.rbi.org.in/...",
      "retrieved_at": "2026-10-28",
      "document_group": "rbi-master-circular-sc-st-credit-facilities",
      "version_label": "2026-10-01",
      "supersedes": null
    },
    "normalization_options": { "unicode_nfkc": true, "...": true },
    "warnings": []
  },
  "pages": [
    {
      "page_number": 1,
      "original_text": "<verbatim, exactly what pypdf returned>",
      "normalized_text": "<after the conservative steps in section 6>",
      "char_count": 1802,
      "normalized_char_count": 1721,
      "word_count": 243,
      "is_empty": false,
      "has_meaningful_text": true
    }
  ]
}
```

#### Field notes

| Field | Meaning |
|---|---|
| `page_number` | **1-based**, following the PDF's *physical* page order. Deliberately not the number printed on the page, which can differ. Where they differ, the discrepancy goes in the manifest `notes`, never into this field. |
| `original_text` | Verbatim extractor output, artefacts included. **Never overwritten.** |
| `normalized_text` | Section 6. Stored *alongside* the raw text. |
| `is_empty` | Literally nothing but whitespace came out. Strong signal of a scanned page. |
| `has_meaningful_text` | Heuristic: ≥ 30 alphanumeric characters (`--min-meaningful-chars` to change). A **signal to look**, not a verdict that the page is broken — a page holding only a signature block or a stamp is legitimate. |
| `pdf_metadata` | The PDF's own `/Info` dictionary, recorded for provenance and **never** promoted to real metadata. §7.8 of the validation report shows why: all four real documents ship an empty or absent `/Title`. |
| `normalization_options` | How this output was produced, so it can be reproduced. |
| `extraction_tool` | Library **and version** actually used, per `CLAUDE.md` §10.1. |

#### `extraction_status`

| Value | Meaning |
|---|---|
| `ok` | Every page carries meaningful text. |
| `partial` | Some pages do, some do not — the usual outcome for a document with an image-only annexure or a signature page. |
| `failed` | No page carries meaningful text; the document is unusable (probably scanned). |

These match `document_versions.extraction_status` in `CLAUDE.md` §11.3. The
database also has `pending` for a row created before extraction runs; this
pipeline never produces it, because it only returns a finished result.

**Note a deviation from the brief:** the illustrative contract in the Phase 1
brief used `"extraction_status": "success"`. We use `ok` to match the project's
own schema in `CLAUDE.md` §11.3 rather than introduce a second vocabulary. Say
the word if you would rather it be `success` and the database follow.

#### Guarantees the schema enforces

`ExtractionResult.__post_init__` **rejects** a result whose pages are not a
complete, gap-free, 1-based sequence, or whose `page_count` disagrees with the
number of page records. A dropped page is a provenance bug, so it is a
construction error rather than something to notice later
(`test_page_sequence_must_not_have_gaps`).

---

## 6. Text normalisation decisions

Normalisation is **additive** (`CLAUDE.md` §10.2): raw text is kept verbatim and
the normalised text sits beside it. Every step is a separately named function
with its own flag in `NormalizationOptions`, so an experiment can disable one
step and measure the difference, and so each choice can be defended on its own.

| Step | What it does | Why |
|---|---|---|
| `unicode_nfkc` | Unicode NFKC | Repairs typographic ligatures (`ﬁ` → `fi`) and presentation-form characters that PDF fonts emit. |
| `unify_line_endings` | `\r\n`, `\r` → `\n` | One line-ending convention, so later regexes and segmentation behave the same on every machine. |
| `strip_invisible_characters` | Removes BOM, zero-width space/non-joiner/joiner, soft hyphen, word joiner | They carry no meaning and silently break string comparison and search. |
| `dehyphenate_line_breaks` | `com-\npliance` → `compliance` | A layout artefact, not a word. **Only** when a lowercase letter precedes the hyphen and the next line starts lowercase, so `Centre-\nState` and clause markers survive. |
| `collapse_horizontal_whitespace` | Any run of spaces/tabs/NBSP/typographic spaces → one space | PDF extraction emits these in quantity around columns and tables. |
| `strip_trailing_line_whitespace` | Trailing spaces/tabs per line | Pure noise. |
| `collapse_blank_lines` | 3+ newlines → exactly 2 | Keeps paragraph breaks, which Phase 2 segmentation needs, without pages of vertical whitespace. |

### What normalisation deliberately does NOT do

No lowercasing. No punctuation stripping. No stopword removal. No stemming or
lemmatisation. No reordering. No digit or date rewriting. **No header/footer
removal.**

Those would destroy exactly what the project depends on: clause numbers like
`3.2`, defined terms, the literal wording of an obligation, and printed dates.
Each has a test asserting it does not happen
(`test_clause_numbering_survives_normalisation`,
`test_case_and_punctuation_are_not_altered`,
`test_dates_and_numbers_are_not_rewritten`,
`test_hindi_text_is_not_stripped`).

Header/footer removal needs to compare text **across** pages, so it belongs to
Phase 2 segmentation, not to a per-page function. The validation report §4.6
measures what will need removing.

Measured effect on the real corpus: a 2.6%–6.4% character reduction per
document, entirely whitespace and invisible characters.

---

## 7. Error handling

Every anticipated failure is a typed exception carrying a stable `code`. Those
codes are the strings the Phase 4 API will put in its error envelope
(`CLAUDE.md` §12.1), so there is one vocabulary, not two.

| Exception | `code` | Raised when |
|---|---|---|
| `SourceFileNotFoundError` | `SOURCE_FILE_NOT_FOUND` | Path missing, or not a regular file |
| `EmptyFileError` | `EMPTY_FILE` | Zero bytes |
| `FileTooLargeError` | `FILE_TOO_LARGE` | Over the cap (default 25 MB) |
| `NotAPdfError` | `UNSUPPORTED_FILE_TYPE` | No `%PDF-` header in the first 1024 bytes |
| `PdfEncryptedError` | `PDF_ENCRYPTED` | Encrypted and the empty password does not open it |
| `PdfUnreadableError` | `EXTRACTION_FAILED` | Header present, body unparseable |
| `PdfHasNoPagesError` | `PDF_HAS_NO_PAGES` | Parses, reports zero pages |
| `OutputExistsError` | `OUTPUT_EXISTS` | CLI refusing to overwrite without `--force` |

### Three specific policies

**The extension is never trusted.** Validation sniffs the bytes for `%PDF-`.
This is not hypothetical: `rbidocs.rbi.org.in` returns an HTML interstitial with
`Content-Type: text/html` for `.PDF` URLs unless a `Referer` header is sent, so
the first download of this corpus produced four HTML files named `*.pdf`
(validation report §1).

**No bare `except:`.** Where pypdf's exception types are not guaranteed, the
pipeline catches broadly, re-raises a typed error, and keeps the original as
`__cause__`. Nothing is swallowed.

**One bad page does not discard the document.** If text extraction fails for a
single page, that page is stored with empty text and a warning, and the other
36 pages are kept. The page sequence stays complete, so provenance is never
broken by a partial failure.

---

## 8. OCR — not implemented

Some government PDFs are scans with no embedded text. **This phase does not do
OCR and does not claim to.** What it does instead:

- A page with zero extractable text is recorded with `is_empty = true` and a
  warning naming the page number. It is **not** dropped.
- A page with very little text gets `has_meaningful_text = false` and a separate
  "text-poor" warning. It is deliberately *not* treated as an error — a page
  holding only a signature, a stamp or a figure is legitimate.
- If no page in a document has meaningful text, `extraction_status = failed`
  with a warning that the document is probably scanned.

### Honest limitation

All four real documents processed so far are machine-readable. **The `partial`,
`failed`, empty-page and text-poor paths have been exercised only by synthetic
test fixtures, never by a real scanned government PDF.** Until one is processed,
scanned-document handling is untested against reality.

### Recommended next step

Keep OCR out of scope for the MVP (`CLAUDE.md` Open Decision D-6) and exclude
scanned documents from the corpus, recording the exclusion in `data/README.md`.
But **acquire one scanned document anyway**, purely to confirm these paths behave
on a real file. If the corpus later forces OCR, the smallest viable addition is
a per-page Tesseract fallback behind a flag, writing into `original_text` with
`extraction_tool` recording that OCR produced it — so an OCR'd page is never
indistinguishable from an extracted one.

---

## 9. Commands

All commands run from the repository root, with the project virtualenv active.

### Setup

```bash
python -m venv .venv
.venv\Scripts\activate              # Windows
source .venv/bin/activate           # macOS / Linux
pip install -r requirements.txt
```

### Extract the whole corpus (the reproducible way)

Metadata comes from each document's manifest row, so nothing depends on shell
history:

```bash
python -m backend.app.document_processing.cli \
    --manifest data/manifest.csv \
    --verify-checksums \
    --output-dir data/processed
```

`--verify-checksums` fails any file whose SHA-256 differs from the manifest. A
mismatch means the document was re-published or altered: **report it, do not
update the manifest silently.**

### Extract a single file

```bash
python -m backend.app.document_processing.cli data/raw/example.pdf

python -m backend.app.document_processing.cli data/raw/example.pdf \
    --output data/processed/example.json --force \
    --title "Some Notification" --document-type notification
```

Useful flags:

| Flag | Effect |
|---|---|
| `--no-write` | Process and summarise, write nothing |
| `--preview` | Show the first 300 characters of the first page with text |
| `--force` | Overwrite an existing output |
| `--no-normalize` | Disable every normalisation step (`normalized_text` == raw) |
| `--min-meaningful-chars N` | Change the `has_meaningful_text` threshold |
| `--verbose` | Show the pipeline's INFO log lines |

Output default: `data/processed/<version_id>/extraction.json`.

**Overwrite policy:** an existing output is **never** replaced without
`--force`. Re-running extraction is cheap; clobbering a file somebody has
already inspected or annotated is not recoverable. Without `--force` the file is
left untouched, reported as skipped, and the exit code is 1.

**The CLI never prints a whole document.** A 37-page circular would flood the
terminal; it prints counts, warnings and (with `--preview`) one short snippet.

Exit codes: `0` all files processed, `1` at least one failed or was skipped,
`2` bad arguments.

### Run the tests

```bash
python -m pytest                 # all 90
python -m pytest -v              # with names
python -m pytest backend/tests/test_extraction.py
```

### Inspect real extraction quality

```bash
python -m backend.tests.inspect_real_extraction
python -m backend.tests.inspect_real_extraction --dir data/processed
```

This asserts nothing and computes no score. It surfaces header/footer
candidates, Devanagari damage, intra-word spacing, hyphen spacing, padded quotes
and flattened-table pages, so findings come from a human reading them.

---

## 10. Validation findings

Full report: **`evaluation/2026-10-28-phase1-extraction-validation.md`**.

Four real RBI documents, 57 pages, all `extraction_status = ok`, 57/57 pages
with meaningful text, zero warnings. That is a fact about the extractor running
— **not** a claim that the text is correct. The defects found by reading the
output against the originals:

1. **Devanagari letterhead corrupted** — 1 to 44 U+FFFD replacement characters
   per document, combining marks reordered. Confined to letterhead/footer in
   this sample, so no clause is damaged, but it rules out any Hindi NLP claim
   based on this extraction.
2. **Multi-column tables flattened column-by-column**, losing row structure
   entirely — 5 of 10 pages in one document. Other tables in the corpus extract
   fine, so the outcome depends on the table's layout.
3. **Spaces inserted inside words** — `M inimum`, `C astes`, `f inancial`.
4. **Spaces on the wrong side of hyphens** — `clean- up`, `risk -based`.
5. **Padded quotation marks** — `‘ the Directions ’`.
6. **Running headers, footers and printed page numbers on every page** —
   expected; Phase 2's job.

**Items 3–5 matter more than they look.** None change what a human reads. All
break **exact string matching**, which is exactly what `CLAUDE.md` §12.2
requires of a citation `quote` and what Person 3's citation-correctness test
will assert. This needs raising with Person 3 before the Ask retrieval work
starts.

---

## 11. Known limitations

- **One extractor, not yet compared.** pypdf chosen on availability and
  simplicity. Experiment A has not been run.
- **No OCR**, and the scanned-document paths are untested on a real scan (§8).
- **Tables are not reliably usable** (§10 item 2).
- **Devanagari is not reliable** (§10 item 1).
- **Headers and footers are still in the text.** By design in this phase.
- **Reading order is not verified** beyond spot-reading four documents. pypdf
  has no layout model, so a genuinely multi-column body page may come out
  interleaved. No such page appeared in this sample, which is not evidence that
  none exists.
- **`has_meaningful_text` is a 30-character heuristic**, tuned by looking at
  four documents from one authority.
- **No clause or paragraph segmentation**, so `segments` / `clause_id` do not
  exist yet. Downstream modules that need clauses must wait for Phase 2.
- **Four documents from one issuing authority**, so nothing here generalises.
- **Synchronous and single-file.** No batching beyond a loop, no concurrency.
  Fine at corpus scale (the 37-page document extracts in about a second).

---

## 12. Next step toward PostgreSQL and FastAPI

The JSON contract in §5 was shaped to match the proposed schema in
`CLAUDE.md` §11.3, so Phase 3 is an insert rather than a translation:

| JSON field | Target |
|---|---|
| `document.source_metadata.title` / `.issuing_department` / `.document_type` / `.reference_no` | `documents.title` / `.department` / `.document_type` / `.reference_no` |
| `document.document_id` | the fingerprint `documents` rows are matched on; `documents.public_id` stays the DB sequence (§4) |
| `document.version_id`, `.checksum`, `.filename`, `.page_count`, `.extraction_status`, `.extraction_tool`, `.warnings` | `document_versions.*` (`file_checksum`, `original_filename`, `processing_warnings`) |
| `document.source_metadata.version_label` / `.supersedes` / `.publication_date` / `.source_url` / `.retrieved_at` | `document_versions.*` |
| `pages[].page_number`, `.original_text`, `.normalized_text`, `.char_count`, `.is_empty` | `pages.page_number`, `.raw_text`, `.normalized_text`, `.char_count`, `.is_empty` |

Recommended order from here:

1. **Phase 2 — segmentation.** Paragraph baseline, then clause-aware, both
   stored side by side, plus header/footer removal informed by validation report
   §4.6. This produces `segments` and `segment_pages`, which four of the five
   team members are blocked on. **Do this before the database**, because the
   schema's hardest part is the segment tables and it is easier to design them
   against real segments than against a sketch.
2. **Phase 3 — PostgreSQL.** Resolve Open Decisions D-4 (SQLAlchemy Core + SQL
   migrations) and D-8 (is PostgreSQL actually installed and reachable for all
   five of us — `psql` was not on PATH on this machine). Then: migrations, the
   access layer, and a loader that reads these JSON files into the schema. The
   JSON files become the intermediate representation, so ingestion and
   persistence stay separately testable.
3. **Phase 4 — FastAPI.** The error `code` values in §7 map straight onto the
   envelope in `CLAUDE.md` §12.1; `extract_pdf` becomes the body of
   `POST /api/documents/ingest`. Open Decision D-5 (synchronous vs asynchronous)
   stands; synchronous is still the recommendation — the 37-page document
   extracts in about a second.

### Two things to settle with the team first

- **D-1, monorepo vs backend-only.** Still unresolved. Nothing in this phase
  depends on it: only `backend/`, `data/`, `docs/` and `evaluation/` were
  created, so no `frontend/` or `nlp/` directory was assumed into existence.
- **Citation matching under whitespace artefacts** (§10). A contract question
  for Person 3, not an implementation detail.
