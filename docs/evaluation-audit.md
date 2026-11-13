# Evaluation audit — Work Package 3

**Date:** 2026-11-13 · **By:** Person 1 (Backend + Document NLP)
**Scope:** audit only. No metric was added, no pipeline or schema changed, no
experiment re-run. Two documentation errors were corrected (§4.4).

**State at audit time:** commit `97c3908`, branch `diw_backend`, tree clean,
**516 tests passing**, data quality **0 errors / 1 warning**.

---

## 1. Current evaluation status

### 1.1 The one-paragraph version

Provenance is **thoroughly** evaluated and is the strongest evidence this
project has. Extraction is **characterised but not scored** — counts and
defects, no accuracy, by deliberate choice. Cleaning is **verified reversible
but never checked for correctness** — the largest unmeasured area, and this
audit's main structural finding. Segmentation has **real but preliminary**
metrics from a 4-page sample annotated by a non-independent annotator.
Independent annotation **has not happened**, and the package prepared for it
**cannot produce an agreement figure as currently scoped** (§3.2) — the second
main finding.

### 1.2 Evidence inventory, by what it actually proves

| Area | Artefact | What it proves | What it does **not** prove |
|---|---|---|---|
| Extraction | `experiment-a-extractor-comparison.md`, `experiment-a/results.json` | 5 extractor conditions × 4 documents differ in measurable, explainable ways | that any of them is *correct* — no ground truth exists |
| Extraction | `text_artifacts.py` | reproducible defect counters (broken words, U+FFFD, orphan marks) | that the counters are complete or calibrated |
| Cleaning | `data_quality.py::cleaning_reversibility` | all 151 removals are reversible and carry recorded evidence | **that the right lines were removed** |
| Segmentation | `experiment-b-segmentation.md`, `experiment-b/results.json` | boundary P/R/F1 computable from committed script + committed annotations | generalisation — 4 pages, 21 clauses, one non-independent annotator |
| Segmentation | 9 documented failure cases | the known failure modes are identified and traceable | their frequency across a real corpus |
| Provenance | `2026-11-12-provenance-validation.md`, `data_quality.py::offset_resolution` | **501/501** segments rebuild from stored offsets | **that the segment boundaries are right** (§2.D) |
| Provenance | `2026-11-12-phase3-postgresql.md`, 73 DB tests | the same holds after a PostgreSQL round-trip | — |
| Corpus | `data_quality.py`, 13 checks | 0 errors on the current corpus | that the corpus is adequate (D-10 open) |

### 1.3 Corpus and measurement base

Everything below rests on this, and it is small:

```
4 documents · 1 issuing authority (RBI) · 57 pages · 0 scanned · 0 version pairs
501 segments = 138 paragraph + 363 clause-aware
552 segment_pages rows · 45 page-spanning segments
151 removed lines = 98 repeated_header + 53 printed_page_number
31 ambiguous clause markers (21 capital-adequacy, 10 commercial-banks)
```

**D-10 remains the binding constraint on every number in this document.**

---

## 2. Audit by area

### A. PDF extraction

**Status: characterised, not scored.** Deliberate — `CLAUDE.md` §24 rule 12
forbids inventing a metric, and no ground-truth transcription exists.

Measured and reproducible today (`text_artifacts.py` over the current corpus):

| document | pages | chars | U+FFFD | broken words | hyphen-space | Devanagari chars |
|---|---:|---:|---:|---:|---:|---:|
| lead-bank | 2 | 3,954 | 44 | 4 | 0 | 710 |
| commercial-banks | 10 | 11,559 | 2 | 22 | 5 | 110 |
| 272 master circular | 8 | 12,792 | 9 | 17 | 2 | 339 |
| capital-adequacy | 37 | 84,474 | 1 | 271 | 54 | 33 |
| **total** | **57** | **112,779** | **56** | **314** | **61** | **1,192** |

**Assessment.**

- *Text completeness* — all 57 pages yielded text; 0 empty pages. Experiment A
  §2 shows the extractors recover substantially the same words, so completeness
  is not the problem.
- *Extraction errors* — **314 broken words** is the headline defect, and it is
  pypdf-specific (Experiment A §4). It matters beyond cosmetics: `CLAUDE.md`
  §12.2 requires a citation `quote` to be a verbatim substring, and
  `'L ead B anks'` defeats a search for "Lead Banks". **This is a live contract
  risk for Person 3**, quantified but not yet scored against truth.
- *Page-level consistency* — page counts agreed across all 5 extractor
  conditions; no page-boundary disagreement.
- *Devanagari* — 1,192 characters, materially wrong. Experiment A §3's finding
  stands and is the uncomfortable one: PyMuPDF does not fix it, it converts a
  **visible** loss (U+FFFD) into a **silent** one. Switching extractor would
  make the corpus look cleaner while being equally wrong.
- *Tables* — pypdf flattens them; cell structure is gone. This is the root
  cause of the worst segmentation failure (§5, F1).

**Gap:** no page has ever been transcribed by hand and diffed against the
extractor output. Everything above is self-referential — defect *detectors*,
not accuracy.

---

### B. Text cleaning — **the weakest area**

**Status: auditable and reversible; correctness never measured.**

What is proven: all **151** removals reverse exactly (`restore() ==
source_text`, enforced — the segmentation CLI *refuses to write* if the
round-trip fails), and every removal carries a human-readable evidence string
(`NOT NULL`, non-blank, enforced in the schema).

What is **not** proven: that those 151 lines *should* have been removed, or
that no line that should have been removed survived. There is no labelled set
of "this line is a running header". The 0.90 threshold was tuned by observing
four documents and is protected by a regression test — which pins *current
behaviour*, not correctness.

**Inspected by eye during this audit** (not ground truth, and labelled as such):

- The 26 removals on the 2-page lead-bank document are all genuine letterhead —
  RBI masthead, departmental address block, the standard caution notice. They
  look right.
- That document correctly raised its own low-confidence warning: *"2 page(s) is
  below the 4-page confidence floor: any line repeated on all pages reaches
  100% trivially"*. The guard works and fired exactly where it should.
- The asymmetry worth noting: the **same letterhead survives** into the
  capital-adequacy preamble (Experiment B §3.9), because there it appears on 1
  of 37 pages — 3%, far below threshold. Frequency-based detection is
  structurally blind to a page-1-only letterhead, and that is a design
  limitation rather than a tuning problem.

**Gap:** this is the only pipeline stage that *deletes* text, and it is the
only stage with no correctness measure. Highest-value cheap experiment (§4,
E2).

---

### C. Segmentation

**Status: real metrics, preliminary and narrow.**

From `evaluation/experiment-b/results.json`, 4 pages / 21 clauses / 2
documents, tolerance 5 characters:

| metric | paragraph | clause-aware |
|---|---:|---:|
| boundary precision | 0.842 | 0.625 |
| boundary recall | 0.889 | 0.833 |
| **boundary F1** | **0.865** | **0.714** |
| exact match rate | 0.722 | 0.722 |
| clause-number recovery | 0.0% | **92.9%** |
| page provenance | 100% | 100% |
| incorrect merges | 2 | 2 |
| incorrect splits | 1 | 0 |

**Reading these honestly.** Neither method dominates. The paragraph baseline
finds *boundaries* better; only the clause-aware method recovers citable clause
numbers. The clause-aware precision figure is dragged down almost entirely by
one document — the table-heavy lead-bank circular, where precision is 0.182 and
9 of 11 predicted segments are spurious table rows. That is one failure mode on
one document, not a general property, and the sample is far too small to
separate the two.

Measurable **without any annotation** (counted during this audit):

- *Cross-page behaviour* — 45 page-spanning segments; every one carries exactly
  `page_end − page_start + 1` page rows. Structurally correct; whether the
  spans are *semantically* right is unmeasured.
- *Numbering ambiguity* — 31 markers flagged `clause_number_ambiguous`, never
  silently resolved. The flag's **accuracy** is unmeasured.
- *Hierarchy* — depth 0–4; 240 of 254 segments in capital-adequacy have a
  parent. Structure exists; correctness unmeasured.
- *Clause-number collisions* — duplicate `(clause_number, level)` pairs are
  widespread (3 / 5 / 7 / 54 per document). Mostly legitimate nested-list
  restarts, but it confirms Experiment B §3.2: **`clause_number` is not a
  unique key**, which is precisely why `CLAUDE.md` §12.2 cites `clause_id`.

**Gap:** no per-document breakdown on a representative sample, and no
table-page measurement at all beyond the one annotated document.

---

### D. Provenance — **the strongest area**

**Status: thoroughly verified, twice, at two layers.**

- **501/501** segments rebuild from their stored offsets against
  `normalized_text` (whitespace-insensitive; ranges are exact).
- Re-verified **after** the PostgreSQL round-trip, so the loader cannot have
  stored right numbers against the wrong column.
- Basis discrimination measured: `normalized_text` 501/501, `cleaned_text`
  14/501, `original_text` 0/501. The 14 are the dangerous ones — a developer
  spot-checking two or three against `cleaned_text` could wrongly conclude they
  had guessed the basis correctly.
- Enforced at three layers: in memory (`Segment.__post_init__`), on the
  artefacts (`offset_resolution`), and in the database (deferred constraint
  trigger at COMMIT).
- 0 orphan segments, 0 cross-version page references, 0 out-of-range page
  references.

**The distinction the brief asks for, stated explicitly:**

> **Offset reconstruction success does not prove segmentation accuracy.**

These measure different things and must never be collapsed:

| | Offset resolution | Segmentation accuracy |
|---|---|---|
| Asks | "does the stored range return the stored text?" | "is that text the right clause?" |
| Compares against | the pipeline's own page text | human-annotated boundaries |
| Needs annotation | **no** | **yes** |
| Current result | **501/501** | F1 0.714–0.865 on 21 clauses |

A segmenter that split every page at character 500 would score **100% offset
resolution** and near-zero boundary F1. The first number is about *bookkeeping
integrity*; only the second is about *reading the document correctly*. This
report never cites 501/501 as evidence of segmentation quality, and neither
should the viva.

---

## 3. Annotation quality

### 3.1 Status: independent annotation has NOT happened

| Question | Answer |
|---|---|
| Independent annotations completed? | **No.** Zero `*.annotator2.jsonl` files exist. |
| Multiple annotators? | **No.** Every record: `annotator: "person1-claude-code"`. |
| Agreement measurable? | **No** — and not only because of the above. See §3.2. |
| Ground truth independent of the implementation? | **No.** The same assistant wrote the segmenter and the annotations. |
| Sample representative? | **No, and deliberately not** — 2 documents chosen as an easy case and a hard case. |
| Owner review done? | **No.** All records: `review_status: "awaiting_owner_review"`. |

`inter_annotator_agreement` is the literal string `"PENDING - no second
annotator"` in the results JSON. **`PENDING` means not yet measured, not low.**
No agreement figure exists anywhere in this repository, and none may be quoted.

### 3.2 New finding — the independent package cannot yield agreement as scoped

Agreement requires **the same pages annotated twice**. They barely overlap:

| document | primary annotated pages | blind worksheet pages | overlap |
|---|---|---|---|
| lead-bank (VER-6507) | **1, 2** | *none* | **0** |
| 272 master circular (VER-b8a13c) | **2, 3** | 1–2, 7–8 | **page 2 only** |
| commercial-banks (VER-7c5ad7) | *none* | 1–3, 9–10 | 0 |
| capital-adequacy (VER-fd6e) | *none* | 1–2 | 0 |

**The entire overlap is one page.** The worksheets were drawn to maximise
*structural coverage* (all 5 marker styles, annexures, cross-page clauses,
ambiguous markers) and nobody checked they covered the pages already annotated.
As packaged, a second annotator could work through all 11 pages and still
produce **no computable agreement figure** beyond a single page.

This is a defect in the package I prepared in Work Package 1, found by this
audit. It is cheap to fix (§4, E1): generate two more worksheets covering
lead-bank pages 1–2 and master-circular pages 2–3, which makes all 4 primary
pages double-annotated. The existing 11 pages stay useful as *sample expansion*
— a different and also-needed job.

### 3.3 What must be completed, exactly

1. Generate the two missing overlap worksheets (one command each, §4 E1).
2. A **second person — not Claude, not Person 1 —** reads
   `data/annotations/README.md` and annotates those 4 pages blind, writing
   `data/annotations/segmentation/<VERSION>.annotator2.jsonl`.
3. Compute agreement on the overlap; record disagreements; amend the guideline
   where it failed to decide a case (guideline §7).
4. Owner reviews the primary records and flips `review_status` to `reviewed`.

Until step 2 happens, **no claim of independent validation may be made.**

---

## 4. Recommended experiments

Ordered by value per hour. Nothing here has been run.

### Already complete — do not repeat

| | Experiment | Why not repeat |
|---|---|---|
| A | Extractor comparison | 5 conditions × 4 documents; decision made (keep pypdf, PyMuPDF for tables). Only re-run if the corpus gains a non-RBI toolchain. |
| C | Data quality, 13 checks | Runs in CI and in the suite; currently 0 errors. |
| — | Provenance validation | 501/501 at two layers. Re-runs automatically. |

---

### E1 — Make inter-annotator agreement computable *(blocks everything in §3)*

1. **Objective:** produce the project's first real agreement figure.
2. **Input:** lead-bank pages 1–2; master-circular pages 2–3 (the 4 already-annotated pages).
3. **Ground truth:** a second annotator, independent of the implementation.
4. **Metric:** boundary agreement = matched boundaries / union of boundaries, at
   the same ±5-character tolerance; plus raw disagreement count and a list of
   disagreeing boundaries. **No Cohen's κ** — boundary detection has no fixed
   category set and no meaningful "expected agreement by chance", so κ would be
   a number with no defensible denominator.
5. **Procedure:**
   ```
   python -m evaluation.experiment_b_segmentation blind \
       --version-id VER-6507f32084d73257 --pages 1-2 --output <...>
   python -m evaluation.experiment_b_segmentation blind \
       --version-id VER-b8a13c2a7651eb84 --pages 2-3 --output <...>
   # second annotator writes <VERSION>.annotator2.jsonl
   # new scorer subcommand: agreement
   ```
6. **Outputs:** `data/annotations/segmentation/*.annotator2.jsonl`;
   `evaluation/experiment-b/agreement.json`; a §0 update to the Experiment B report.
7. **Limitations:** 4 pages and 21 clauses is a small base for an agreement
   figure; it will be indicative, not tight. Report the n alongside it, always.

---

### E2 — Cleaning correctness *(highest value of the automatable work)*

1. **Objective:** measure whether header/footer removal removes the right lines
   — the only deleting stage with no correctness measure.
2. **Input:** all 57 pages; the 151 removal decisions already recorded with
   evidence strings.
3. **Ground truth:** a human labels each **distinct** removed line (not each
   occurrence) as header / footer / page-number / **content**, plus scans each
   page's *retained* top-5 and bottom-5 lines for missed boilerplate. Small:
   roughly 30 distinct removed strings, not 151.
4. **Metric:** removal precision = correctly-removed / removed;
   removal recall = correctly-removed / (correctly-removed + missed);
   **content-destruction count** = removed lines that were real content —
   report this as an absolute number, never a rate, because the tolerable value
   is zero.
5. **Procedure:** emit the distinct removed lines with page positions and
   evidence to a CSV; label it; score.
6. **Outputs:** `data/annotations/cleaning/removed-lines.labelled.csv`;
   `evaluation/experiment-e2-cleaning/results.json`; a dated report.
7. **Limitations:** the labeller must be someone other than the person who
   tuned the 0.90 threshold, or it repeats §3's independence problem. Says
   nothing about documents whose boilerplate differs from RBI's.

---

### E3 — Expand the segmentation sample

1. **Objective:** reduce the dominance of one table-heavy document in the
   headline F1.
2. **Input:** the 11 pages already prepared as blind worksheets (3 documents, all
   5 marker styles, 8 cross-page segments, 4 ambiguous markers).
3. **Ground truth:** annotation of those pages against the existing guideline.
4. **Metric:** the existing Experiment B metrics, reported **per document as
   well as pooled** — pooling 4 pages hid that one document drove the precision
   gap.
5. **Procedure:** annotate → `validate --against-template` → `score`.
6. **Outputs:** updated `experiment-b/results.json` and report; the previous
   results kept, not overwritten.
7. **Limitations:** still one authority, still no scanned documents. Raises the
   sample to ~15 pages, which is better and still small — say so.

---

### E4 — Table-induced false clause rate *(automated, no annotation)*

1. **Objective:** quantify the worst known failure (§5, F1) corpus-wide instead
   of on one document.
2. **Input:** all 57 pages. `is_probably_flattened_table()` already exists in
   `text_artifacts.py`; PyMuPDF `find_tables()` is already a pinned dependency.
3. **Ground truth:** none needed for the *signal*; a human confirms the flagged
   pages are genuinely tables (fast — a page is or is not a table).
4. **Metric:** count of `clause_aware` segments whose span lies inside a
   detected table region, as a share of all clause segments; per document.
5. **Procedure:** detect table regions → intersect with segment page offsets →
   report.
6. **Outputs:** `evaluation/experiment-e4-tables/results.json` + report.
7. **Limitations:** measures a *proxy* (overlap with a detected region), not
   true false-positive rate. Evidence for D-12, not a decision on its own.

---

### E5 — Extraction fidelity on a transcribed sample *(do only if time allows)*

1. **Objective:** the only experiment that would give extraction a real accuracy
   number.
2. **Input:** 3–5 pages chosen to include one table page and one Devanagari-heavy page.
3. **Ground truth:** hand-transcription from the rendered PDF. Expensive — this
   is why it is last.
4. **Metric:** character error rate and word error rate against the transcription.
5. **Procedure:** transcribe → normalise both sides identically → align → score.
6. **Outputs:** `data/annotations/extraction/*.truth.txt`; results JSON; report.
7. **Limitations:** 3–5 pages cannot generalise. Its real value is **calibrating
   the existing detectors** — telling us whether "314 broken words" over- or
   under-states the true damage.

---

## 5. Failure analysis, classified and prioritised

The 9 cases in Experiment B §3, re-classified into the brief's taxonomy. **No
algorithm was changed during this audit.**

| # | Failure | Class | Severity | MVP verdict |
|---|---|---|---|---|
| F1 | Flattened table rows become clauses (precision 0.182 on lead-bank; 9/11 spurious) | **Extraction-induced** → table-related | **High** | **Fix** — D-12, table-aware segmentation |
| F2 | Numbering restarts undetected; `clause_number` not unique | Clause recognition | Medium | **Document** — citations already use `clause_id` |
| F3 | `Schedule` matched inside `Scheduled` | Clause recognition | — | **Already fixed**; precision 0.867 → 1.000 |
| F4 | Unnumbered opening recital merged into preamble | Paragraph boundary | Low | Document |
| F5 | Closing matter merged into last clause | Clause recognition | Low | Document |
| F6 | Guideline vs implementation disagree on trailing punctuation | **Guideline defect** | Low | **Fix the guideline**, not the code |
| F7 | Paragraph baseline splits heading from body | Paragraph boundary | Low | Document — it is the baseline; "dumb" is the point |
| F8 | Clause-aware misses page-2 title heading | Clause recognition | Low | Document |
| F9 | Page-1-only letterhead survives into preamble | **Cleaning-induced** | Medium | **Fix if cheap** — a position+page-1 rule; frequency cannot catch it |

**Classes with zero failures:** *cross-page* (45/45 structurally correct) and
*provenance* (501/501). Those two are genuinely solid.

**Fix for the MVP — two items only:**

1. **F1, table-aware segmentation (D-12).** The only case where clause-aware is
   clearly *worse* than the baseline, the largest single metric effect, and the
   tooling already exists as a pinned dependency.
2. **F9, page-1 letterhead removal.** Cheap, and it currently pollutes the
   preamble segment of the largest document — the first thing a reader sees.

**Everything else stays a documented limitation.** F2 and F5–F8 are small,
honestly recorded, and defensible in a viva. Fixing them would consume the time
that E1 and E2 need, and an unmeasured fix is worth less than a measured
limitation.

---

## 6. MVP evaluation checklist

| # | Item | Status |
|---|---|---|
| 1 | Extraction defects counted and reproducible | ✅ done |
| 2 | Extractor choice justified by measurement | ✅ done (Experiment A) |
| 3 | Cleaning reversible and evidenced | ✅ done |
| 4 | **Cleaning correctness measured** | ❌ **not started** (E2) |
| 5 | Both segmentation methods implemented and stored | ✅ done |
| 6 | Segmentation scored against annotations | ⚠️ preliminary (4 pages) |
| 7 | Per-document segmentation breakdown | ❌ pooled only (E3) |
| 8 | Failure cases documented | ✅ done (9 cases) |
| 9 | Provenance verified end to end | ✅ done (501/501, two layers) |
| 10 | Offset basis empirically confirmed | ✅ done (501 / 14 / 0) |
| 11 | Annotation guideline written before annotating | ✅ done |
| 12 | **Independent annotation completed** | ❌ **not started** (E1) |
| 13 | **Inter-annotator agreement measured** | ❌ **blocked** — and not computable as the package is currently scoped (§3.2) |
| 14 | Annotations committed and regenerable | ✅ done |
| 15 | Corpus agreed (D-10) | ❌ **open** — 4 documents, 1 authority, 0 version pairs |
| 16 | Data-quality checks automated | ✅ done (13 checks, 0 errors) |
| 17 | Every metric reproducible from committed script + data | ✅ done |

**11 done · 1 preliminary · 5 outstanding.**

---

## 7. Verdict on evaluation readiness

**Ready to defend: provenance and data quality.** 501/501 offset resolution at
two independent layers, 13 automated checks, enforcement in memory, on the
artefacts and in the database. This is the project's strongest claim and it is
properly evidenced.

**Defensible with stated caveats: extraction and the segmentation comparison.**
Experiment A made a real decision on real measurements and is explicit that it
has no ground truth. Experiment B's numbers are reproducible from a committed
script and committed annotations, and its §0 states every limitation before any
metric. Quotable — **only with the caveats attached.**

**Not ready: any claim of validated segmentation accuracy.** One
non-independent annotator, 4 pages, 21 clauses, agreement `PENDING`, and the
package meant to fix that cannot currently produce an agreement figure. The
honest viva answer to *"how do you know your segmentation is right?"* remains:

> *"We measured it against a single non-independent annotation on 4 pages. We
> know that is the weakness. Here is the prepared package that fixes it, and
> here is the gap in that package we found and costed."*

**Not measured at all: cleaning correctness.** The only stage that deletes text
has no correctness measure. It is reversible and audited, which means a mistake
is *recoverable* — not that no mistake was made.

**Recommended order:** E1 (unblocks every independence claim) → E2 (closes the
largest hole) → E3 (widens the sample) → E4 (evidence for D-12) → E5 only if
time allows.

**One structural caveat over all of it:** D-10. Four documents from one
authority, zero version pairs. No amount of evaluation rigour on this corpus
makes a claim about government documents in general — and Compare still has
nothing to run on.

---

## Appendix — corrections made during this audit

Documentation only; no code, schema or pipeline touched.

1. **Ambiguous-marker count: 21 → 31.** Work Package 1 changed a comment in
   `0001_initial_schema.sql` from "31 occurrences across the corpus" to 21. The
   original was right: 21 is the capital-adequacy document alone; the corpus
   total is 31 (21 + 10 in commercial-banks). I made that error by measuring one
   document. Corrected in `backend/app/db/README.md` §4 and
   `docs/work-package-1-verification.md` §3.

   **Still outstanding:** the SQL comment itself reads 21 and **cannot be
   edited** — the migration is applied and the runner's checksum verification
   correctly refuses an edited applied migration. It needs a `COMMENT ON COLUMN`
   correction in a future migration. Not done here: this work package forbids
   schema changes.

2. **Stale status line.** `backend/app/db/README.md` §2 still said the access
   modules had never been run against a server, contradicting its own header.
   Corrected.
