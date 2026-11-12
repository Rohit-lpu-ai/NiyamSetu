# Experiment A — PDF extractor comparison

- **Date:** 2026-11-12
- **Run by:** Person 1 (Backend + Document NLP)
- **Corpus:** the 4 real RBI documents in `data/manifest.csv` (57 pages total)
- **Script:** `evaluation/experiment_a_extractor_comparison.py`
- **Detectors:** `evaluation/text_artifacts.py`
- **Backend adapters:** `backend/app/document_processing/extractor_backends.py`
- **Raw results:** `evaluation/experiment-a/results.json` (committed)
- **Per-page text dumps:** `data/processed/<version_id>/experiment-a-text/*.txt`
  (git-ignored; regenerate with `--dump-text`)

**Command:**

```bash
python -m evaluation.experiment_a_extractor_comparison \
    --manifest data/manifest.csv --repeats 3 --dump-text
```

## What this report is, and is not

This is the comparison `CLAUDE.md` §15 requires before any extractor change can
be justified, and that the Phase 1 validation report §5.2 listed as the open
next step. It replaces the Phase 1 statement *"pypdf chosen on availability and
simplicity … not yet compared against anything."*

It contains **no accuracy score**, because there is still no annotated ground
truth for extracted text. Every number below is either a direct count or a
cross-extractor *difference*. "Backend X produced 44 replacement characters
where backend Y produced none" is a fact; "backend Y is 100% accurate" would be
a fabrication.

The five conditions compared are **not** five libraries. They are three
libraries, two of them in two modes each, because a library's default and its
layout-aware mode are different claims:

| Condition | Library | Mode |
|---|---|---|
| `pypdf` | pypdf 6.18.1 | `page.extract_text()` — **the baseline; what the pipeline uses today** |
| `pymupdf` | PyMuPDF 2.1 (MuPDF 1.28.6) | `page.get_text("text")`, content-stream order |
| `pymupdf-sorted` | PyMuPDF 2.1 | `page.get_text("text", sort=True)`, position-sorted |
| `pdfplumber` | pdfplumber 0.11.10 | `page.extract_text()` |
| `pdfplumber-layout` | pdfplumber 0.11.10 | `page.extract_text(layout=True)` |

---

## 1. Headline table — all 4 documents, all 5 conditions

`FFFD` = U+FFFD replacement characters. `mismap` = characters from Unicode
blocks that do not belong in this corpus (Latin Extended, IPA, combining
diacriticals, Greek, Cyrillic). `loss` = the sum of the two — see §3, which is
the most important section of this report. `hyph` / `quot` = the hyphen-space
and padded-quote artefacts from Phase 1 §4.4–4.5. `flatT` = pages flagged as a
column-by-column flattened table. `wordRat` = median per-page word count as a
fraction of the best backend on that same page. `sec` = median of 3 runs.

| Document | Condition | pg | chars | words | FFFD | mismap | **loss** | hyph | quot | flatT | wordRat | sec |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| master-circular-272mc | `pypdf` | 8 | 13 439 | 1 930 | 9 | 0 | **9** | 2 | 0 | 1 | 1.000 | 0.305 |
| | `pymupdf` | 8 | 13 469 | 1 897 | 0 | 8 | **8** | 0 | 0 | 2 | 0.989 | 0.020 |
| | `pymupdf-sorted` | 8 | 15 218 | 1 892 | 0 | 8 | **8** | 0 | 0 | 0 | 0.989 | 0.153 |
| | `pdfplumber` | 8 | 12 682 | 1 896 | 9 | 0 | **9** | 1 | 0 | 0 | 0.989 | 1.242 |
| | `pdfplumber-layout` | 8 | 43 778 | 1 896 | 9 | 0 | **9** | 1 | 0 | 0 | 0.989 | 0.815 |
| capital-adequacy (37pp) | `pypdf` | 37 | 86 693 | 13 253 | 1 | 4 | **5** | 54 | 15 | 0 | 1.000 | 1.229 |
| | `pymupdf` | 37 | 86 983 | 13 124 | 0 | 5 | **5** | 1 | 0 | 2 | 0.991 | 0.089 |
| | `pymupdf-sorted` | 37 | 101 573 | 13 119 | 0 | 5 | **5** | 1 | 0 | 0 | 0.991 | 0.618 |
| | `pdfplumber` | 37 | 84 226 | 13 139 | 1 | 4 | **5** | 1 | 0 | 0 | 0.992 | 3.689 |
| | `pdfplumber-layout` | 37 | 208 185 | 13 139 | 1 | 4 | **5** | 1 | 0 | 0 | 0.992 | 3.437 |
| commercial-banks-2nd | `pypdf` | 10 | 12 106 | 1 774 | 2 | 0 | **2** | 5 | 3 | 5 | 1.000 | 0.189 |
| | `pymupdf` | 10 | 12 181 | 1 754 | 0 | 2 | **2** | 0 | 0 | 6 | 0.993 | 0.018 |
| | `pymupdf-sorted` | 10 | 21 351 | 1 754 | 0 | 2 | **2** | 0 | 0 | 0 | 0.993 | 0.080 |
| | `pdfplumber` | 10 | 11 532 | 1 755 | 2 | 0 | **2** | 0 | 0 | 2 | 0.993 | 0.555 |
| | `pdfplumber-layout` | 10 | 53 624 | 1 755 | 2 | 0 | **2** | 0 | 0 | 2 | 0.993 | 0.651 |
| lead-bank-delhi (2pp) | `pypdf` | 2 | 4 224 | 615 | 44 | 0 | **44** | 0 | 0 | 0 | 0.999 | 0.079 |
| | `pymupdf` | 2 | 4 235 | 615 | 0 | 44 | **44** | 0 | 0 | 0 | 0.998 | 0.011 |
| | `pymupdf-sorted` | 2 | 5 572 | 599 | 0 | 34 | **34** | 0 | 0 | 0 | 0.972 | 0.032 |
| | `pdfplumber` | 2 | 3 931 | 609 | 44 | 0 | **44** | 0 | 0 | 0 | 0.989 | 0.198 |
| | `pdfplumber-layout` | 2 | 11 009 | 609 | 44 | 0 | **44** | 0 | 0 | 0 | 0.989 | 0.262 |

All five conditions agreed on page count for all four documents: 2, 8, 10, 37.
No condition produced an empty page, and no condition recorded a per-page
extraction error. **Page coverage is therefore not a differentiator on this
corpus** — which also means it tells us nothing about scanned documents, of
which we still have none (`data/README.md`).

---

## 2. Text completeness: the libraries recover the same words

The `wordRat` column is the median per-page word count as a fraction of
whichever condition recovered the most words on that same page. Every value is
between **0.972 and 1.000**. On three of the four documents `pypdf` is the
condition with the *highest* median word recovery.

**Do not read the `chars` column as completeness.** `pdfplumber-layout` produces
208 185 characters on the capital-adequacy document against pypdf's 86 693 — a
2.4× difference — while recovering **0.992** of the words. The extra 121 492
characters are padding spaces inserted to reproduce visual column positions.
This is exactly why the script reports a word ratio as well: character counts
reward whitespace, word counts measure content. A report that ranked extractors
by character count would have concluded that `pdfplumber-layout` extracts 2.4×
more text than pypdf, which is false.

**Conclusion for completeness:** on this corpus, no library recovers meaningfully
more *content* than any other. The differences are in **how** the recovered text
is shaped and encoded, which is what the rest of this report is about.

---

## 3. Devanagari: PyMuPDF does not fix it, it hides it

This is the most important finding in Experiment A, and it reverses the
conclusion that the U+FFFD column alone would support.

Phase 1 §4.1 recorded pypdf emitting 44 U+FFFD replacement characters in the
bilingual letterhead of the lead-bank circular. PyMuPDF emits **zero**. Taken
alone, that reads as PyMuPDF fixing the project's most serious extraction
defect.

It does not. The Devanagari character count is **identical across every
condition** — 702, 336, 108 and 31 for the four documents. PyMuPDF recovers no
more Devanagari than pypdf. What it does instead is resolve the unmappable
glyphs through a different fallback table, emitting plausible-looking characters
from **Latin Extended-A/B and the combining-diacriticals block**:

| Document | pypdf U+FFFD | PyMuPDF mismapped | Characters PyMuPDF emitted instead |
|---|---:|---:|---|
| lead-bank-delhi | 44 | 44 | `į` `ǐ` `Ǒ` `ȣ` `ȧ` `ͧ` `ͩ` `ͪ` |
| master-circular-272mc | 9 | 8 | `į` `İ` `ƅ` `ƛ` `Ⱦ` |
| commercial-banks-2nd | 2 | 2 | `ƛ` `ǐ` |
| capital-adequacy | 1 | 1 (+4 Greek, see below) | `į` |

The correspondence is close to exact — **44 ↔ 44, 2 ↔ 2, 9 ↔ 8** — which is
what makes this a substitution rather than a coincidence. Side by side on page 1
of the lead-bank circular, where the Hindi for "Reserve Bank of India" should
appear:

```
pypdf       भारतीय �रज़वर् बैंक
pymupdf     भारतीय įरज़वर् बैंक
pdfplumber  भारतीय �रज़वर् बैंक
```

Both are wrong, and the same character is wrong in both. The difference is that
`�` is a **loud** failure — countable, greppable, obviously broken to a reader —
while `į` is a **silent** one. It will pass through normalisation, segmentation,
retrieval indexing and citation matching without tripping a single check, and a
reader skimming the output would not notice it.

For a project whose entire claim is traceability, a silent corruption is worse
than a visible one. This is why `text_artifacts.py` reports
`glyph_loss_signal = replacement_chars + mismapped_glyph_candidates`, and why
the headline table leads with it. **On that measure all five conditions are
equivalent: 44/44/34/44/44, 9/8/8/9/9, 2/2/2/2/2, 5/5/5/5/5.**

`pdfplumber` emits exactly the same U+FFFD counts as pypdf (44, 9, 2, 1). It is
built on `pdfminer.six`, and the two evidently share the same fallback
behaviour.

**Caveat, stated because the detector is a heuristic.** The 4 "mismapped"
characters attributed to pypdf and pdfplumber on the capital-adequacy document
are all `Σ` (U+03A3, Greek capital sigma), which appears **legitimately** in a
formula in that document. All conditions emit it and all are correct. The
detector flags Greek because Greek is not expected in this corpus; here it is a
false positive, and the samples in `results.json` are what made that visible.
A non-zero `mismap` count is a prompt to read the samples, never a defect count
on its own.

**Consequence for the project:** no extractor available to us reads the
Devanagari letterhead correctly. This independently confirms that D-3 (Hindi /
multilingual output) cannot rest on this pipeline — which matches the owner's
decision to pause multilingual work. It also means header/footer removal in
Phase 2 has a second motivation beyond tidiness: the mangled letterhead is
**exactly** the text that removal strips, so cleaning the corpus of running
headers also removes the bulk of the corrupted characters from the segment
text.

---

## 4. Word spacing and broken words: pypdf is measurably the worst

Phase 1 §4.3–4.5 found three artefact classes by hand. Counted across all
conditions, these are the one place where the baseline is clearly behind:

| Document | artefact | `pypdf` | `pymupdf` | `pdfplumber` |
|---|---|---:|---:|---:|
| capital-adequacy | hyphen-space (`'clean- up'`, `'risk -based'`) | **54** | 1 | 1 |
| capital-adequacy | padded quotes (`'‘ the Directions ’'`) | **15** | 0 | 0 |
| commercial-banks-2nd | hyphen-space | **5** | 0 | 0 |
| commercial-banks-2nd | padded quotes | **3** | 0 | 0 |
| master-circular | hyphen-space | **2** | 0 | 1 |

PyMuPDF and pdfplumber both effectively eliminate these. The intra-word space
defect (`'M inimum'`) shows the same pattern, visible in the token-level diff of
lead-bank page 2 — the page where pypdf and PyMuPDF agree least (token overlap
0.786):

```
tokens only in pypdf   : 'neve', 'r', 'L', 'ead', 'B', 'anks'
tokens only in pymupdf : 'never', 'Lead', 'Banks'
```

pypdf split `never` into `neve r`, `Lead` into `L ead`, and `Banks` into
`B anks`; PyMuPDF kept all three intact. Every other token difference on that
page is the differently-mangled Devanagari of §3.

**Why the `brokn` counter was dropped from the headline table.** An earlier run
reported broken-word counts of 271 / 267 / 268 for pypdf / PyMuPDF /
pdfplumber on the capital-adequacy document — a 1% difference that looked like
"no library is better". Reading the samples showed the counter is dominated by
legitimate single-letter words ("A notification", "I agree"), which swamp the
real defects. The regex is documented as having that false positive, the
counter is retained in `results.json`, and it is **not** quoted as a finding
here, because on this corpus it does not measure what it was meant to measure.
The token-diff above is the honest version of the same observation.

**This matters more than its size suggests.** `CLAUDE.md` §12.2 requires a
citation `quote` to be a verbatim substring of the clause. A clause containing
`'L ead B anks'` cannot be matched by a user searching "Lead Banks", and
Person 3's citation-correctness test will assert exactly that substring
relationship. Phase 1 §5.1 already flagged this as a contract question for
Person 3; this experiment quantifies it and shows it is a *pypdf-specific*
defect rather than an inherent property of PDF extraction.

---

## 5. Reading order and tables: the clearest win, and it is not about text

### 5.1 Reading order

Reading order is measured by two numbers read together, per page pair:
**token overlap** (do the two conditions agree on *which* words are on the
page?) and **line-structure difference** (do they agree on how those words are
grouped into lines?). High overlap with a high line difference means the same
content in a different order.

`pymupdf` vs `pymupdf-sorted` is the controlled test, because it is the same
library with one flag changed:

| Document | token overlap (median) | line-structure diff (median) |
|---|---:|---:|
| commercial-banks-2nd | **1.000** | 0.521 |
| capital-adequacy | **1.000** | 0.132 |
| master-circular | **1.000** | 0.000 |
| lead-bank-delhi | 0.955 | 0.379 |

Identical words, substantially different line structure. That is the signature
this metric was built to detect, and it confirms `sort=True` changes ordering
and not content.

Does the reordering help? On the flattened-table proxy, yes, and consistently:

| Document | `pypdf` flagged pages | `pymupdf` | `pymupdf-sorted` | `pdfplumber` |
|---|---|---|---|---|
| commercial-banks-2nd | 2, 3, 5, 6, 7 | 2, 3, 5, 6, 7, 8 | **none** | 3, 6 |
| capital-adequacy | none | 26, 27 | **none** | none |
| master-circular | 8 | 7, 8 | **none** | none |

`pymupdf-sorted` is the only condition that flags **zero** flattened pages on
every document. Note also that PyMuPDF in its *default* mode is slightly
**worse** than pypdf here (6 flagged pages vs 5 on commercial-banks, 2 vs 0 on
capital-adequacy) — so "PyMuPDF has better reading order" is false for the
default mode and true only with `sort=True`. Reporting the two modes separately
is what made that visible.

### 5.2 Tables — the one capability pypdf genuinely lacks

pypdf has **no table API at all**. PyMuPDF (`find_tables()`) and pdfplumber
(`extract_tables()`) both do, and on this corpus they agree closely on the
shapes they find:

| Document | page | `pymupdf` | `pdfplumber` |
|---|---:|---|---|
| master-circular | 7 | 1 table, 16r × 4c, 64 filled | 1 table, 16r × 4c, 64 filled |
| master-circular | 8 | 1 table, 6r × 4c, 24 filled | 1 table, 6r × 4c, 24 filled |
| capital-adequacy | 26 | 3 tables, 7×9 / 6×10 / 4×10 | 3 tables, 7×9 / 6×10 / 4×10 |
| capital-adequacy | 27 | 1 table, 9r × 6c | 2 tables, 1r × 10c + 9r × 6c |
| commercial-banks-2nd | 2–8 | 1 table per page, 5 columns | 1 real + **2 spurious** per page |
| lead-bank-delhi | 1 | 1 table, 4r × 4c | 1 table, 4r × 4c |

pdfplumber over-detects: on every table page of the commercial-banks amendment
it reports two extra 2×1 and 4×1 "tables", which are the letterhead block, not
tables. PyMuPDF returns the one real table.

Checked against the page Phase 1 §4.2 named as the worst case — page 6 of the
commercial-banks amendment, where pypdf produced "49 of 64 lines at ≤ 2 words" —
PyMuPDF's `find_tables()` recovers the row structure:

```
PyMuPDF find_tables(), page 6:
| BI constituents | P&L or balance sheet items | Description | Typical sub -items | [added] |
| ...             | Fee and commission expense | Expenses paid for receivin… | Fee and commission expense | Schedule 16 |
| ...             | Other operating income     | Income from ordinary banki… | (i) Rental income from inv… | Schedule 14 |

pypdf page.extract_text(), same page:
  BI
  constituents
  P&L or
  balance
  sheet
  items
  Description Typical sub-items [added]
```

The four-column row that pypdf flattens into a vertical stack comes back as an
addressable row with its cells separated. **This is a real capability gap, and
it is the strongest argument in this experiment for keeping PyMuPDF installed.**

---

## 6. Extraction time and installation footprint

Median of 3 runs, whole document, including file open:

| Document | pages | `pypdf` | `pymupdf` | `pymupdf-sorted` | `pdfplumber` |
|---|---:|---:|---:|---:|---:|
| lead-bank-delhi | 2 | 0.079 s | **0.011 s** | 0.032 s | 0.198 s |
| master-circular | 8 | 0.305 s | **0.020 s** | 0.153 s | 1.242 s |
| commercial-banks-2nd | 10 | 0.189 s | **0.018 s** | 0.080 s | 0.555 s |
| capital-adequacy | 37 | 1.229 s | **0.089 s** | 0.618 s | 3.689 s |

PyMuPDF is **10–15× faster** than pypdf. pdfplumber is **3–4× slower** than
pypdf, and 40× slower than PyMuPDF.

In absolute terms none of this matters yet: the slowest condition on the longest
document is 3.7 seconds, and D-5 (synchronous ingestion) is comfortable at that
speed for a 20–40 document corpus. It would matter at hundreds of documents, or
if ingestion moved inside a web request with a timeout.

**Installation footprint, measured on this machine:**

| State | `.venv` size |
|---|---|
| Phase 1 (pypdf + pytest only) | ~25 MB |
| After adding PyMuPDF + pdfplumber | **314 MB** |

PyMuPDF 2.1 is 105 MB by itself and declares `pymupdf-layout`, `pymupdf4llm`
and **`onnxruntime` (48 MB)** as hard dependencies; `numpy` (36 MB + 21 MB of
libs) and `networkx` (19 MB) come with them. pdfplumber adds `pdfminer.six`,
`Pillow`, `pypdfium2` and `cryptography`. A 12× increase in environment size,
on five student machines, for a library whose text output is not measurably more
complete, is a cost worth naming out loud.

---

## 7. Recommendation

**Keep `pypdf` as the primary page-text extractor. Do not migrate.**
**Keep PyMuPDF installed as an evaluation and table-extraction dependency.**

The reasoning, in the order the evidence supports it:

1. **No completeness gain.** Word recovery is 0.972–1.000 across all five
   conditions, and pypdf has the highest median on three of four documents.
   There is no text being lost that another library recovers.
2. **No Devanagari gain, and a real regression in diagnosability.** §3 — the
   glyph-loss signal is identical, and PyMuPDF converts a loud failure into a
   silent one. For a traceability project this argues *against* PyMuPDF for the
   primary text path, not for it.
3. **A 12× environment cost** (§6) for benefits that §1–§2 show are not about
   completeness.
4. **The pypdf defects that are real (§4) are narrow and partly fixable where
   they are.** 54 hyphen-space artefacts across 37 pages and a handful of
   intra-word splits are a normalisation and citation-matching problem. The
   hyphen-space case in particular is a candidate for an additive normalisation
   step, which is cheaper and more explainable than a dependency migration.
5. **Working code that passes 93 tests should not be replaced for a
   non-improvement** (`CLAUDE.md` §24 rule 2).

### Where this recommendation does **not** hold

Required by the brief, and genuinely important:

- **Tables.** pypdf cannot extract table structure at all. On the
  commercial-banks amendment, that is 5 of 10 pages whose row structure is lost
  (§5.2). If clause-level retrieval over tabular annexures becomes a
  requirement — and for Person 3's Ask feature over the capital-adequacy
  annexures it plausibly will — PyMuPDF's `find_tables()` is the tool, used
  *alongside* pypdf for those pages rather than replacing it.
- **Reading order on dense multi-column pages.** `pymupdf-sorted` is the only
  condition that flags zero flattened pages on all four documents (§5.1). If
  Phase 2 segmentation turns out to break specifically on the flagged pages,
  re-extracting just those pages with `pymupdf-sorted` is a targeted fix.
- **Speed at scale.** If the corpus grows past a few hundred documents, the
  10–15× speed difference stops being irrelevant.
- **`pdfplumber` is not recommended for anything here.** It matches pypdf's
  Devanagari behaviour exactly, recovers no more words, over-detects tables
  relative to PyMuPDF, and is the slowest condition tested. Its layout mode is
  actively harmful to the citation contract: it pads with 2.4× more characters,
  so an exact-substring `quote` taken from layout-mode text would not match text
  from any other condition. It stays pinned only so this experiment is
  reproducible.

### Migration implications, if the decision is ever revisited

Recorded now so the option stays cheap:

- `backend/app/document_processing/extractor_backends.py` already wraps all
  three libraries behind one interface. Switching the primary extractor means
  giving `extract_pdf` a `backend` parameter defaulting to `PypdfBackend` — an
  **additive** change.
- **The JSON schema does not change.** `extraction_tool` already records the
  library and version per document (`"pypdf 6.18.1"`), so a mixed corpus is
  self-describing and old outputs stay valid. `schema_version` would not need a
  bump.
- Re-extraction changes page text, so **every annotation and every stored
  character offset becomes stale.** Annotations are anchored on short verbatim
  marker strings rather than offsets (`data/README.md` §7) specifically to
  survive this, but `segment_pages.char_start` / `char_end` would need
  recomputing. Re-running the segmentation CLI regenerates them.
- `version_id` is derived from the **file** checksum, not from the extracted
  text, so identifiers and duplicate detection are unaffected.

---

## 8. Threats to validity

- **Four documents, one issuing authority.** Everything above describes RBI PDFs
  produced by `Acrobat PDFMaker 26 for Word`. A state-government PDF from a
  different toolchain could reverse any of these findings. D-10 is still open.
- **Zero scanned documents.** Page coverage was identical across all conditions
  precisely because every page has a text layer. The scanned case — where the
  libraries would differ most — is untested against reality, and OCR is out of
  scope by decision.
- **No ground truth.** Every "better" above means "differs from the baseline in
  a direction we can argue for", not "closer to the truth". The table row
  structure in §5.2 is the one place where correctness was checked directly, by
  reading the original PDF.
- **The artefact detectors are heuristics** with documented false positives
  (the Greek `Σ`, the broken-word regex). They are consistent across backends,
  which is what makes the *comparison* fair even where an absolute judgement is
  wrong.
- **Timing is wall-clock on one Windows machine**, with the file in OS cache
  after the first of three runs. Treat the ratios as meaningful and the absolute
  numbers as indicative.

---

## 9. Reproducing this

```bash
# Full run, all five conditions, all four documents, with text dumps
python -m evaluation.experiment_a_extractor_comparison \
    --manifest data/manifest.csv --repeats 3 --dump-text

# Just the baseline against default PyMuPDF, no table probing (fast)
python -m evaluation.experiment_a_extractor_comparison \
    --manifest data/manifest.csv --backends pypdf,pymupdf --no-tables
```

Requires the four PDFs in `data/raw/` under their manifest `local_filename`
(see `data/README.md` §9 for the download note — `rbidocs.rbi.org.in` needs a
`Referer` header). The script skips a missing file with a message and compares
the rest.

Automated tests for the comparison machinery itself:
`backend/tests/test_extractor_backends.py`.
