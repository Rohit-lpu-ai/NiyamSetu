# Phase 1 — PDF extraction validation run

- **Date:** 2026-10-28
- **Run by:** Person 1 (Backend + Document NLP)
- **Extractor:** `pypdf 6.18.1`
- **Pipeline:** `backend/app/document_processing/` at this commit
- **Command:**
  `python -m backend.app.document_processing.cli --manifest data/manifest.csv --verify-checksums --output-dir data/processed`
- **Inspection helper:** `python -m backend.tests.inspect_real_extraction`

## What this report is, and is not

This is a **manual inspection** of four real documents, per `CLAUDE.md` §14
rule 6 and §15 Experiment A. It reports counts that a committed script
reproduces, plus defects found by reading the extracted text against the
original PDFs.

It contains **no accuracy percentage**, because no annotated ground truth exists
yet. "57 of 57 pages produced text" is a fact about the extractor running; it is
**not** a claim that the text is correct. The defects in §4 are the honest part
of this report.

---

## 1. Corpus used

Four notifications/circulars from the Reserve Bank of India, discovered from
RBI's own notification index (`https://www.rbi.org.in/scripts/NotificationUser.aspx`)
and downloaded from RBI's document host `rbidocs.rbi.org.in`. Full provenance,
including SHA-256 and retrieval date, is in `data/manifest.csv`.

| Local filename | Type | Pages | Printed reference | Date |
|---|---|---|---|---|
| `rbi_circular_lead-bank-delhi_v1.pdf` | circular | 2 | RBI/2026-27/300 FIDD.CO.LBS.BC.No.12/02.08.001/2026-27 | 2026-10-26 |
| `rbi_master-circular_272mc_v1.pdf` | circular | 8 | RBI/2026-27/272 FIDD.CO.GSSD.BC.No.11/09.09.001/2026-27 | 2026-10-01 |
| `rbi_directions_commercial-banks-forthcoming-2nd-amendment_v1.pdf` | amendment | 10 | RBI/2026-27/299 DOR.ORG.REC.252/21.06.050/2026-27 | 2026-10-23 |
| `rbi_directions_capital-adequacy-11th-amendment_v1.pdf` | amendment | 37 | RBI/2026-27/297 DOR.CAP.REC. 251/21-02-002/2026-27 | 2026-10-23 |

Titles, reference numbers, departments and dates were read off page 1 of each
document, not taken from the index page or guessed. Where a document does not
print its issuing department (the capital-adequacy amendment), the manifest
field is **blank** rather than inferred from the `DOR.CAP` reference prefix.

### Authenticity
Criteria 1–4 of `data/README.md` §2.3 are satisfied for all four: official
domain, printed reference number and issuing office consistent with the recorded
metadata, `retrieved_at` recorded, SHA-256 recorded and re-verified by the run
above (`--verify-checksums` passed for all four).

### Download note worth keeping
`rbidocs.rbi.org.in` returns an **HTML interstitial page with
`Content-Type: text/html`** for a `.PDF` URL unless a `Referer` header from
`rbi.org.in` is sent. The first download attempt therefore saved four files
named `*.pdf` that were HTML. They were deleted and re-fetched. This is why
`validate_pdf_file` sniffs the bytes for `%PDF-` and never trusts the extension;
the behaviour has its own test (`test_html_named_pdf_is_rejected_on_bytes_not_extension`).

---

## 2. Extraction result per document

| Document | Pages | Pages with meaningful text | Empty pages | Text-poor pages | Warnings | Status |
|---|---|---|---|---|---|---|
| lead-bank-delhi | 2 | 2 | 0 | 0 | 0 | `ok` |
| master-circular-272mc | 8 | 8 | 0 | 0 | 0 | `ok` |
| commercial-banks-2nd-amendment | 10 | 10 | 0 | 0 | 0 | `ok` |
| capital-adequacy-11th-amendment | 37 | 37 | 0 | 0 | 0 | `ok` |
| **total** | **57** | **57** | **0** | **0** | **0** | — |

Characters per page (raw extracted text):

| Document | min | median | max |
|---|---|---|---|
| lead-bank-delhi | 1994 | 2230 | 2230 |
| master-circular-272mc | 809 | 1924 | 2187 |
| commercial-banks-2nd-amendment | 812 | 1214 | 2089 |
| capital-adequacy-11th-amendment | 885 | 2202 | 4527 |

Effect of normalisation, measured as total character reduction across the whole
document (raw → normalised):

| Document | raw chars | normalised chars | reduction |
|---|---|---|---|
| lead-bank-delhi | 4 224 | 3 954 | 6.4% |
| master-circular-272mc | 13 439 | 12 792 | 4.8% |
| commercial-banks-2nd-amendment | 12 106 | 11 559 | 4.5% |
| capital-adequacy-11th-amendment | 86 693 | 84 474 | 2.6% |

The reduction is entirely whitespace and invisible characters; no normalisation
step removes words. The raw text is retained in `original_text` on every page,
so this is reversible and auditable.

### Determinism
Running the pipeline twice on the same files produced identical `document_id`
and `version_id` values for all four documents. Identifiers are derived from the
file checksum, so re-processing is idempotent (see `docs/pdf-extraction.md` §4).

---

## 3. What the run proves, and what it does not

**Proved by this run:**
- Validation rejects non-PDF bytes, including a `.pdf`-named HTML page.
- All 57 pages were extracted with 1-based page numbers, in order, with no page
  dropped or merged.
- Raw and normalised text are stored separately on every page.
- Human-supplied metadata is carried through verbatim; absent fields stay null.
- Recorded checksums match the files on disk.
- The output JSON loads and has the documented shape.

**Not proved by this run:**
- **Text correctness.** No annotated ground truth exists, so no accuracy number
  is reported. §4 lists the defects found by reading the output.
- **Scanned-document behaviour.** All four documents are machine-readable text
  PDFs. The `partial`, `failed`, empty-page and text-poor code paths were
  exercised **only by synthetic test fixtures**, never by a real scanned
  government PDF. Until one is processed, the pipeline's handling of scanned
  documents is untested against reality.
- **Compare readiness.** Both amendment documents amend 2025 parent Directions
  that are **not in this corpus**. The corpus therefore contains **zero genuine
  version pairs**, and Person 4's Compare feature cannot be evaluated on it.
  This is the single most important gap for corpus collection (Open Decision
  D-10).
- **Generalisation.** Four documents from one issuing authority. Nothing here
  says anything about state-government or ministry publishing.

---

## 4. Extraction defects found by inspection

These were found by reading the extracted text against the original PDFs. Each
is reproducible via `python -m backend.tests.inspect_real_extraction`.

### 4.1 Devanagari (Hindi) letterhead is corrupted — **most serious**

Every document carries a bilingual Hindi/English RBI letterhead. pypdf emits the
Devanagari with combining marks reordered and some glyphs unmapped, producing
U+FFFD replacement characters:

| Document | U+FFFD count | Pages affected |
|---|---|---|
| lead-bank-delhi | 44 | 1, 2 |
| master-circular-272mc | 9 | 1 |
| commercial-banks-2nd-amendment | 2 | 1 |
| capital-adequacy-11th-amendment | 1 | 1 |

Example, page 1 of the lead-bank circular, where the Hindi for "Reserve Bank of
India" should appear:

```
भारतीय �रज़वर् बैंक
```

Also visible: dependent vowel signs detached from their consonant
(`'ीय'`, `'ेंद्र�य'` as standalone tokens).

**Impact and scope.** Confined to the letterhead and footer in this sample — the
English body text is unaffected — so it does not currently damage any clause.
But it rules out treating this extraction as a basis for any Hindi NLP claim
(`CLAUDE.md` D-3), and it would matter immediately if a genuinely
Hindi-language document entered the corpus.

**Caveat on the numbers.** The U+FFFD count is exact. The combining-mark
detector in the inspection script is narrow and reported "none detected" for the
capital-adequacy document even though that document does carry one replacement
character. **Absence of a flag is not evidence of correct Devanagari.**

### 4.2 Multi-column tables are flattened column-by-column

Row structure is lost completely. Pages where more than half the lines are two
words or fewer:

| Document | Pages | Example |
|---|---|---|
| commercial-banks-2nd-amendment | 2, 3, 5, 6, 7 (5 of 10) | page 6: 49 of 64 lines are ≤ 2 words |
| master-circular-272mc | 8 | 15 of 28 lines are ≤ 2 words |

Page 6 of the commercial-banks amendment, which in the PDF is a four-column
table, extracts as a vertical stack:

```
BI
constituents
P&L or
balance
sheet
items
Description Typical sub-items [added]
...
Fee and
commission
expenses
```

**Impact.** A clause that lives in a table will segment badly in Phase 2 and
retrieve badly in Person 3's Ask feature. Half of one document in this sample is
affected.

**Not universal.** The capital-adequacy amendment's annexures (pages 31–37,
including its densest page at 4 527 characters) extract as readable flowing
numbered rows, with **no** flattened-table pages detected. So the outcome depends
on the table's own layout, not on the extractor alone. This is the strongest
argument for running Experiment A properly against PyMuPDF and pdfplumber before
Phase 2 segmentation is tuned.

### 4.3 Spaces inserted inside words

Found in all four documents. Examples with page numbers:

- `'M inimum'` (commercial-banks p1), `'s hall'` (p4)
- `'C astes'` (master-circular p3), `'c redit'` / `'r eview'` (p6)
- `'L ead'`, `'B anks'` (lead-bank p2)
- `'f inancial'` (capital-adequacy p32)

### 4.4 Spaces on the wrong side of a hyphen

- `'clean- up'`, `'non- performing'` (commercial-banks p4, p10)
- `'risk -based'`, `'On- balance sheet'` (capital-adequacy p2, p3)
- `'co- ordination'`, `'production- oriented'` (master-circular p2)

### 4.5 Padded quotation marks

`'‘ the Directions ’'` (capital-adequacy p1), `'‘ Reserve Bank of India'`
(commercial-banks p2), `'Directions, 2025 ’'` (p8).

**Combined impact of 4.3–4.5.** None of these change what the document says to a
human reader. All of them break **exact string matching**, which is precisely
what `CLAUDE.md` §12.2 requires of a citation `quote` and what Person 3's
citation-correctness test will assert. A citation lifted from a clause
containing `'M inimum'` will not match a user's query for "minimum". This should
be raised with Person 3 before the Ask retrieval work starts.

### 4.6 Running headers, footers and printed page numbers leak into every page

- capital-adequacy: a two-line running header appears on **36 of 37** pages
  (`Reserve Bank of India (Commercial Banks – Prudential Norms` /
  `on Capital Adequacy) Eleventh Amendment Directions, 2026`).
- lead-bank: six or more letterhead lines appear on **2 of 2** pages, including
  the full postal address, telephone and email, and an anti-phishing warning.
- The printed page number also lands in the text (page 32 of the capital-adequacy
  document begins with the header followed by a bare `32`).

This is expected — removing them needs cross-page comparison, which is
deliberately **not** in this phase (`CLAUDE.md` §13.1 puts it in Phase 2). The
measurement matters for Phase 2: the ≥60% threshold that `CLAUDE.md` §13.1
proposes catches the capital-adequacy header at 36/37 (97%) and the lead-bank
letterhead at 2/2 (100%), so on this sample the proposed threshold works. One
caution: `master-circular-272mc` had **no** line repeating on ≥60% of its pages,
so a purely frequency-based rule will not strip its letterhead, which appears
only on page 1.

### 4.7 A metadata discrepancy worth recording

The RBI index links the commercial-banks amendment at a URL path containing
`NT298…`, while the document itself prints `RBI/2026-27/299`. The manifest
records **what the document prints**, with the discrepancy in `notes`. This is
the kind of thing that makes "`publication_date` does not establish version
order" (`CLAUDE.md` §11.1) more than a theoretical worry.

### 4.8 PDF `/Info` metadata is not usable as a document title

All four documents were produced by `Acrobat PDFMaker 26 for Word`. Three carry
an **empty** `/Title`; the fourth has no `/Title` at all. One records
`/Company: RBI`, another `/Company: RESERVE BANK OF INDIA`, two are blank.
The pipeline records `/Info` for provenance and never promotes it to the
document's real metadata — this run confirms that was the right call.

---

## 5. Recommended next steps

1. **Raise 4.3–4.5 with Person 3** before retrieval work begins. Either
   citations tolerate whitespace differences, or normalisation gains a
   whitespace-insensitive matching helper. This is a contract question, not an
   implementation detail.
2. **Run Experiment A properly** (`CLAUDE.md` §15): compare pypdf against
   PyMuPDF and pdfplumber on these same four documents plus any scanned
   document, scoring table handling and Devanagari specifically. Only then is a
   dependency change justified under rule 5. *Current state: pypdf chosen on
   availability and simplicity, with the defects above documented. Not yet
   compared against anything.*
3. **Collect at least two genuine version pairs** (§3). Without them Compare has
   nothing to run on. The 2025 parent Directions for both amendments in this
   sample are the obvious first targets.
4. **Acquire one scanned document** purely to exercise the `partial` / `failed`
   paths against reality, even if it is then excluded from the MVP corpus per
   D-6.
5. **Phase 2 header/footer removal** should use the ≥60% rule as proposed but
   must handle the `master-circular` case (letterhead on page 1 only), so
   position-based detection will be needed alongside frequency.

---

## 6. Automated test results

Run on 2026-10-28 with `.venv\Scripts\python.exe -m pytest` (Python 3.11.9,
pytest 8.3.4):

```
90 passed in 1.33s
```

| Test module | Tests | Covers |
|---|---|---|
| `test_validation.py` | 9 | missing file, directory, zero bytes, size cap, HTML-named-PDF, truncated PDF, string paths |
| `test_extraction.py` | 23 | page ordering, boundary preservation, raw vs normalised separation, identity, metadata, empty/text-poor pages, status derivation, JSON contract |
| `test_normalization.py` | 15 | each step individually, and what must **not** change (clause numbers, case, punctuation, dates, Devanagari) |
| `test_identity.py` | 10 | SHA-256 known-answer, chunk-boundary safety, determinism, document-group semantics |
| `test_schemas.py` | 7 | 1-based page numbers, no gaps in the page sequence, page_count consistency, status whitelist |
| `test_cli.py` | 14 | output paths, overwrite refusal, exit codes, per-file error isolation, preview, flags |
| `test_manifest.py` | 12 | required columns, blank-to-null, per-row metadata, checksum mismatch, missing download |
| **total** | **90** | |

Every PDF used by these tests is generated at test time by
`backend/tests/pdf_builder.py`. They are **synthetic fixtures**, never presented
as government documents and never part of the corpus. The real-document findings
in §4 come from the four PDFs in §1 only.

### Two failures found and fixed during this session

Recorded because the fixes changed behaviour, not just tests:

1. `test_json_is_utf8_safe_for_non_ascii_text` failed with
   `UnicodeEncodeError: 'latin-1' codec can't encode character '–'`. The
   fixture builder declares `/WinAnsiEncoding` but encoded content streams as
   latin-1, which cannot represent the en-dash that real circulars use.
   Fixed by encoding as cp1252, which *is* WinAnsi.
2. `test_runs_of_spaces_collapse_to_one` failed because the whitespace regex
   required two or more characters, so a lone tab survived. Changed to
   one-or-more, so normalised text is uniformly space-separated. (While fixing
   it, the invisible-character and whitespace constants were rewritten as
   explicit codepoint lists — the previous version had literal zero-width
   characters embedded in the source.)
