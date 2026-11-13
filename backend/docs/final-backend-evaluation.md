# Final backend evaluation — all metrics

**Date:** 2026-11-13 · **By:** Person 1 · **Corpus:** 4 RBI PDFs, 57 pages
**Suite at time of writing:** 590 passed, 0 failed, 0 skipped
*(Re-run 2026-11-13 after the repository cleanup: 600 passed, 0 failed, 0
skipped — 10 added tests pin the Library & Search scaffold. No metric in this
document changed; the numbers below are left exactly as measured.)*

Every number below was produced by a committed script from committed data, and
every command shown was actually run. Where a metric requires human input that
does not exist, it is reported as **PENDING** — never estimated, never
defaulted to 0 or 1.

**Two separations are load-bearing in this document:**

1. **Measured vs PENDING.** A built scorer is not a measurement.
2. **Offset resolution vs segmentation accuracy.** They answer different
   questions and are never combined (§4).

---

## 0. Status at a glance

| Area | Metric | Status |
|---|---|---|
| Extraction | defect counters | ✅ measured |
| Extraction | CER / WER | ⏳ **PENDING** — needs transcription (E5) |
| Cleaning | reversibility | ✅ measured |
| Cleaning | removal precision / recall | ⏳ **PENDING** — needs labelling (E2) |
| Segmentation | boundary P/R/F1 | ⚠️ **preliminary** — 4 pages, 1 non-independent annotator |
| Segmentation | table-overlap proxy | ✅ measured (as a proxy) |
| Segmentation | inter-annotator agreement | ⏳ **PENDING** — needs a 2nd annotator (E1) |
| Provenance | offset resolution | ✅ measured, 501/501 |
| Database | integrity + counts | ✅ measured |
| API | endpoint behaviour | ✅ measured |

---

## 1. Extraction

### 1.1 Defect counters — MEASURED

**Definition.** Reproducible counts of specific extraction defects, per page,
summed per document.
**Formula.** Direct counts; `glyph_loss_signal = replacement_chars +
mismapped_glyph_candidates`.
**Sample.** All 57 pages, 112,779 characters. Full corpus, not a sample.
**Source.** `evaluation/text_artifacts.py` over `data/processed/*/extraction.json`.
**Command.**
```
.venv\Scripts\python -m evaluation.experiment_a_extractor_comparison
```

| document | pages | chars | U+FFFD | broken words | hyphen-space | padded quotes | Devanagari |
|---|---:|---:|---:|---:|---:|---:|---:|
| lead-bank | 2 | 3,954 | 44 | 4 | 0 | 0 | 710 |
| commercial-banks | 10 | 11,559 | 2 | 22 | 5 | 3 | 110 |
| 272 master circular | 8 | 12,792 | 9 | 17 | 2 | 0 | 339 |
| capital-adequacy | 37 | 84,474 | 1 | 271 | 54 | 15 | 33 |
| **total** | **57** | **112,779** | **56** | **314** | **61** | **18** | **1,192** |

**Page completeness:** 57 of 57 pages yielded text. **Empty pages: 0.**

**Limitations.** These are *detectors we wrote*, so they are self-referential —
they say "this pattern occurs", not "this is wrong". The broken-word regex has
documented false positives. No ground truth exists; that is E5.

### 1.2 Extractor comparison — MEASURED (decision made)

**Definition.** 5 extractor conditions × 4 documents on the same inputs.
**Sample.** 57 pages × 5 conditions.
**Source.** `evaluation/experiment-a-extractor-comparison.md`.

**Result:** keep `pypdf` as the production extractor; keep PyMuPDF for its table
API. The headline finding inverts the obvious reading — **PyMuPDF does not fix
the Devanagari problem, it converts a visible loss (U+FFFD) into a silent one.**

**Limitations.** No ground truth: "better" means "differs in a direction we can
argue for". Four documents, one authority, one PDF toolchain, zero scanned.

### 1.3 CER / WER — PENDING

**Definition.** `CER = levenshtein(reference, extracted) / len(reference)` on
NFKC-normalised, whitespace-collapsed text; WER likewise over words.
**Ground truth required.** Hand transcription of 5 pages.
**Status.** Infrastructure complete and tested (22 tests); **no page
transcribed**, so no value exists.
**Command.**
```
.venv\Scripts\python -m evaluation.extraction_fidelity --write-templates
.venv\Scripts\python -m evaluation.extraction_fidelity
```
**Pages prepared** (chosen for worst-case coverage, *not* at random):

| page | why |
|---|---|
| VER-6507… p1 | most Devanagari in the corpus |
| VER-7c5ad7… p3 | most table-like |
| VER-7c5ad7… p6 | second most table-like |
| VER-fd6e15… p32, p35 | large prose pages, as contrast |

**Limitations when it lands.** 5 pages cannot generalise, and the selection is
worst-case weighted — the result will **not** be a corpus average. Its real
value is calibrating §1.1's detectors.

---

## 2. Cleaning

### 2.1 Removal count and reversibility — MEASURED

**Definition.** Lines removed as running headers, footers or printed page
numbers; reversibility = `restore() == source_text` for every page.
**Sample.** All 57 pages.
**Command.** `.venv\Scripts\python -m evaluation.data_quality`

| reason | removals |
|---|---:|
| `repeated_header` | 98 |
| `printed_page_number` | 53 |
| **total** | **151** |

| document | removals | note |
|---|---:|---|
| lead-bank | 26 | **low-confidence warning raised** — 2 pages, so "repeats on all pages" is trivially 100% |
| commercial-banks | 9 | page numbers only |
| 272 master circular | 8 | page numbers only |
| capital-adequacy | 108 | 72 header + 36 page numbers |

**Reversibility: 151/151.** Enforced, not merely observed — the segmentation
CLI *refuses to write* if a page fails to round-trip.
**Evidence: 151/151** carry a non-blank evidence string (`NOT NULL` in the
schema).

**Limitations.** **Reversibility is not correctness.** This says a mistake would
be *recoverable*, not that none was made.

### 2.2 Removal precision / recall / content destruction — PENDING

**Definitions.**
- `removal_precision = correctly_removed / removed_and_labelled`
- `removal_recall = correctly_removed / (correctly_removed + missed_boilerplate)`
- `content_destruction_count` = occurrences of lines labelled `content` that
  were removed. **An absolute count, never a rate** — the only acceptable value
  is 0, and a percentage invites "99.3% is fine".

**Counted by occurrence, not by distinct string:** removing a content line from
36 pages is 36 deletions.
**Ground truth required.** A human labels 91 distinct lines as
header / footer / page_number / content / unsure.
**Status.** Package built and tested (20 tests); **no labels exist.**
**Command.**
```
.venv\Scripts\python -m evaluation.cleaning_correctness --write-template
# fill in data/annotations/cleaning/removed-lines.template.csv
# save as removed-lines.labelled.csv
.venv\Scripts\python -m evaluation.cleaning_correctness
```

**Package contents:** 91 rows — **52 distinct removed lines covering all 151
removals**, plus **39 retained page-edge lines** so recall is measurable.
Without the retained set, recall would be 1.0 by construction. Retained lines
are filtered to plausible boilerplate (repeated across pages, **or anywhere on
page 1** — the latter because a page-1-only letterhead is a known real failure,
Experiment B §3.9, and filtering it out would hide the defect recall exists to
catch).

**Limitations.** The labeller must not be whoever tuned the 0.90 threshold.
Says nothing about non-RBI boilerplate conventions.

---

## 3. Segmentation

### 3.1 Boundary metrics — PRELIMINARY

**Definitions.** A predicted boundary matches an annotated one when on the same
page within **±5 characters**. `P = matched/predicted`, `R = matched/annotated`,
`F1 = 2PR/(P+R)`. `num%` = clause numbers correctly recovered.
**Sample.** **4 pages, 21 clauses (18 confident + 3 uncertain), 2 of 4
documents.** One annotator, **not independent of the system**.
**Source.** `evaluation/experiment-b/results.json`.
**Command.**
```
.venv\Scripts\python -m evaluation.experiment_b_segmentation validate --against-template
.venv\Scripts\python -m evaluation.experiment_b_segmentation score
```

**Per document — and this is the important view:**

| version | method | ann | pred | match | P | R | F1 | exact | merge | split | num% | prov% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lead-bank | paragraph | 4 | 4 | 2 | 0.500 | 0.500 | 0.500 | 0.000 | 2 | 0 | 0.000 | 1.000 |
| lead-bank | clause_aware | 4 | 11 | 2 | **0.182** | 0.500 | **0.267** | 0.000 | 2 | 0 | 1.000 | 1.000 |
| 272 master | paragraph | 14 | 15 | 14 | 0.933 | 1.000 | 0.966 | 0.929 | 0 | 1 | 0.000 | 1.000 |
| 272 master | clause_aware | 14 | 13 | 13 | **1.000** | 0.929 | **0.963** | 0.929 | 0 | 0 | 0.923 | 1.000 |
| **POOLED** | paragraph | 18 | 19 | 16 | 0.842 | 0.889 | **0.865** | 0.722 | 2 | 1 | 0.000 | 1.000 |
| **POOLED** | clause_aware | 18 | 24 | 15 | 0.625 | 0.833 | **0.714** | 0.722 | 2 | 0 | **0.929** | 1.000 |

**Reading this honestly.** The pooled clause-aware F1 of 0.714 is **not a
general property of the method** — it is one document. On prose (272 master)
clause-aware scores **0.963** and beats the baseline; on the table-heavy
lead-bank circular it collapses to **0.267**. Pooling 4 pages hid that, which
is why the per-document view leads here. Only clause-aware recovers clause
numbers (92.9% vs 0.0%), and page provenance is 100% for both.

**Limitations.** Sample too small to separate the methods. Annotator not
independent. Agreement PENDING. Two documents chosen as an easy and a hard
case — deliberately **not** representative.

### 3.2 Inter-annotator agreement — PENDING

**Definition.** Jaccard over matched boundaries:
`agreement = matched / (|A| + |B| − matched)` at ±5 characters.
**Why not Cohen's κ.** κ needs a fixed category set and a defensible chance
model. Boundary detection has neither — the "categories" are character
positions, and there is no principled probability of two annotators coinciding
by accident. A κ here would have an arbitrary denominator and look more
rigorous than it is.
**Status.** Scorer built and tested (18 tests). **No second annotator.** Result
is the literal string `PENDING - no second annotator`.
**Command.** `.venv\Scripts\python -m evaluation.annotator_agreement`

**Blocker fixed this session.** The audit found the blind worksheets overlapped
the primary annotations on **one page**, so agreement was not computable at all.
Two overlap worksheets were generated; the 4 primary-annotated pages are now
fully covered:

| document | primary pages | overlap worksheet |
|---|---|---|
| lead-bank | 1–2 | ✅ `VER-6507….pages1-2.OVERLAP.worksheet.txt` |
| 272 master circular | 2–3 | ✅ `VER-b8a13c….pages2-3.OVERLAP.worksheet.txt` |

**Limitations when it lands.** 4 pages / 21 clauses is a small base — the figure
will be indicative, and the n must be quoted beside it.

### 3.3 Table-overlap proxy — MEASURED (as a proxy)

**Definition.** Share of `clause_aware` segments whose **token** content
overlaps a PyMuPDF-detected table region by ≥ 0.6.
**Formula.** `|tokens(segment) ∩ tokens(table cells)| / |tokens(segment)|`,
type-based (a repeated word does not inflate it).
**Sample.** All 57 pages, all 363 clause-aware segments.
**Command.** `.venv\Scripts\python -m evaluation.table_overlap`

| document | clause segs | on table pages | **flagged** | sensitivity 0.4→0.8 |
|---|---:|---:|---:|---|
| lead-bank | 11 | 11 | **6** | 7,7,6,6,6 |
| commercial-banks | 49 | 42 | **37** | 37,37,37,36,34 |
| 272 master circular | 49 | 23 | **23** | 23,23,23,23,22 |
| capital-adequacy | 254 | 103 | **79** | 86,85,79,67,57 |
| **total** | **363** | **179** | **145 (39.9%)** | 22 pages with a detected table |

**This generalises the worst known failure from one document to the corpus.**
Flagged examples are unambiguously table rows read as clauses:

```
p1 [1]  overlap=1.00 '1. Old Delhi Canara Bank 03E (to be read as...'
p7 [2]  overlap=1.00 '2. DBOD.No.BP.BC.8/C.453(K)-Gen January 09, 1979...'
p26 [50] overlap=1.00 '50 Receivable SFT'
```

**Manual confirmation of flagged pages** (performed this session, by eye):
table detection is consistent — pages known to contain tables detect them
(lead-bank p1 4×4, p2 3×5 + 2×4; 272 master p7 16×4; capital-adequacy p26
7×9 + 6×10 + 4×10), and prose pages detect none (272 master p1,
capital-adequacy p5, commercial-banks p1).

**⚠️ This is a PROXY, not a false-positive rate.** Three reasons:
1. `find_tables()` is itself a heuristic with its own errors.
2. The intersection is **textual, not geometric** — segments carry character
   offsets, tables carry page coordinates; there is no shared space.
3. **A table row can legitimately be a clause**, and the proxy over-flags. A
   confirmed example from the output: `(ii) Table 8 under Paragraph 29 shall be
   modified as under…` scores 0.74 — a genuine clause that *introduces* a table.

So 39.9% is an **upper bound** on table-induced false clauses, not the rate.
Per `CLAUDE.md` §24 rule 5, no segmentation logic was changed on this evidence;
it is input to D-12, not a decision.

---

## 4. Provenance

### 4.1 Offset resolution — MEASURED, 501/501

**Definition.** Slice `pages.normalized_text[char_start:char_end]` for every
page a segment touches, join, and require the segment text back
(whitespace-insensitive; character *ranges* are exact).
**Sample.** All 501 segments. Full population.
**Command.** `.venv\Scripts\python -m evaluation.data_quality`

| basis sliced | segments that resolve (of 501) |
|---|---:|
| **`normalized_text`** | **501** |
| `cleaned_text` | 14 |
| `original_text` | 0 |

The 14 are the dangerous ones: a developer spot-checking two or three against
`cleaned_text` could wrongly conclude they had guessed the basis right. This is
why `offset_basis` is a stored per-version column, not a constant.

Verified **twice**: on the JSON artefacts, and again after the PostgreSQL
round-trip (so a loader storing right numbers against the wrong column would be
caught), and again through HTTP in `test_api_integration.py`.

### 4.2 Page attribution — MEASURED

| check | result |
|---|---|
| segments with ≥1 page row | **501 / 501** |
| page-spanning segments with exactly `page_end − page_start + 1` rows | **45 / 45** |
| orphan segments (no `segment_pages`) | **0** |
| `segment_pages` rows crossing into another version's pages | **0** |
| page references outside `1..page_count` | **0** |
| versions declaring a basis other than `normalized_text` | **0** |

### 4.3 ⚠️ Offset resolution is NOT segmentation accuracy

Stated explicitly because it is the easiest number in this project to
over-read:

| | Offset resolution | Segmentation accuracy |
|---|---|---|
| Asks | "does the stored range return the stored text?" | "is that text the right clause?" |
| Compares against | the pipeline's own page text | human-annotated boundaries |
| Needs annotation | no | **yes** |
| Result | **501/501** | F1 0.267–0.963 on 21 clauses |

**A segmenter that split every page at character 500 would score 100% offset
resolution and near-zero boundary F1.** The first is bookkeeping integrity; only
the second is reading the document correctly. 501/501 is never cited in this
project as evidence of segmentation quality.

---

## 5. Database

**Command.** `.venv\Scripts\python -m backend.app.db.migrate --status`

| metric | value |
|---|---|
| Migrations applied | `0001_initial_schema.sql`, `0002_public_id_sequences.sql` |
| Documents | 4 |
| Versions | 4 |
| Pages | 57 |
| Segments | 501 (138 paragraph + 363 clause_aware) |
| `segment_pages` | 552 |
| `page_removed_lines` | 151 |
| `ingestion_jobs` | 4 |
| Page-spanning segments | 45 |

**Every count matches the JSON artefacts exactly** — asserted by
`test_the_database_holds_exactly_what_the_artefacts_hold`, so a loader dropping
or duplicating a row fails the suite.

**Constraints observed firing** (not merely declared) — 73 PostgreSQL tests:
deferred provenance trigger at COMMIT, `page_number` consistency, offset bounds,
`raw_text` immutability, duplicate checksum, duplicate ordinal, reversed page
range, invalid `offset_basis`, removal without evidence, mid-load rollback
(0 rows survive), idempotent reload (counts unchanged, `loaded == 0`).

**Data quality:** 13 checks, **0 errors**, 1 warning — no version pair exists
(D-10).

---

## 6. API

**Command.** `.venv\Scripts\python -m pytest backend/tests/test_api*.py`

| suite | tests | database |
|---|---:|---|
| `test_api.py` | 39 | **mocked** — proves routing, validation, status codes, error envelope, serialisation. **Proves nothing about PostgreSQL.** |
| `test_api_integration.py` | 15 | **real** — a real PDF through the real pipeline into the real database, over HTTP |

**Verified behaviour:** health reports API and database separately (503 +
`degraded` when the DB is down); ingestion 201 / duplicate 200 / conflict 409 /
oversized 413 / non-PDF 415 / empty 422; listing, detail, pages and clauses with
pagination and validation; 404 for unknown ids; internal errors never leak a DSN
or path.

**Provenance through HTTP — the acceptance condition.**
`test_every_clause_resolves_from_its_offsets_against_the_served_pages` fetches
pages and clauses as a consumer would, slices the served text at the served
offsets, and gets every clause back.

**Duplicate behaviour verified:** a re-upload returns 200 `skipped_duplicate`,
creates nothing, **and does not overwrite stored artefacts** — a regression test
for a defect found by running a real upload through uvicorn in Work Package 2.

---

## 7. Test results

| | count |
|---|---:|
| **Passed** | **590** |
| Failed | 0 |
| Skipped | 0 |
| Blocked / not run | 0 |

| category | tests |
|---|---:|
| Need no database (`-m "not integration"`) | 517 |
| Need PostgreSQL (`-m integration`) | 73 |

New this session: 18 (E1 agreement) + 20 (E2 cleaning) + 14 (E4 tables) +
22 (E5 fidelity) = **74**.

---

## 8. What is still PENDING, and exactly what unblocks it

| # | Blocked metric | Needs | Prepared and ready |
|---|---|---|---|
| E1 | inter-annotator agreement | **a second human** annotating 4 pages | ✅ worksheets + scorer + 18 tests |
| E2 | cleaning precision / recall / content destruction | **a human** labelling 91 lines | ✅ template + scorer + 20 tests |
| E3 | wider segmentation sample | annotation of 11 prepared pages | ✅ worksheets + scorer (per-document breakdown already exists) |
| E5 | CER / WER | **a human** transcribing 5 pages | ✅ templates + scorer + 22 tests |

**None of these can be completed by this assistant without fabricating ground
truth**, which `CLAUDE.md` §24 rule 12 forbids. Each scorer reports `PENDING`
and is pinned by a test asserting it stays `PENDING` until real input exists.

---

## 9. Overall limitations

- **D-10 is the binding constraint.** 4 documents, 1 authority, 1 PDF toolchain,
  **0 version pairs**, 0 scanned. No evaluation rigour on this corpus supports a
  claim about government documents in general, and Compare has nothing to run on.
- **No independent validation of any segmentation claim** until E1 completes.
- **Cleaning correctness is unmeasured** until E2 completes.
- **Extraction has no accuracy figure** until E5 completes.
- **0 empty pages in the corpus**, so that provenance path is exercised only by
  synthetic fixtures.
- **Devanagari is materially wrong** (1,192 characters) and no available
  extractor fixes it.
- **The table-overlap figure is an upper bound**, not a rate.
