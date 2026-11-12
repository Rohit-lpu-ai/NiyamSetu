# Phase 2 — header/footer cleaning and segmentation

> **Written:** 2026-11-12. **Owner:** Person 1 (Backend + Document NLP).
> **Status:** `[IMPLEMENTED]` — built, run on the real corpus, 385 passing
> tests. The *evaluation* of it is `[PARTIAL]`: real metrics exist on a 4-page
> annotated sample, single-annotated, with inter-annotator agreement `PENDING`.
>
> Companion documents: `docs/pdf-extraction.md` (Phase 1),
> `evaluation/experiment-b-segmentation.md` (the numbers and the failure cases),
> `data/annotations/README.md` (the annotation guideline),
> `backend/app/db/README.md` (what this output implies for the schema).

---

## 1. What this phase does

Phase 1 produced page-level text. Phase 2 turns it into the units the rest of
the team consumes, without ever losing the link back to the page it came from.

```text
extraction.json  (Phase 1: pages, raw + normalised text)
  -> clean_pages()            remove running headers, footers, page numbers
                              — reversibly, with the evidence recorded
  -> build_source_lines()     flatten to addressable lines, each carrying its
                              character range in the uncleaned page text
  -> segment_paragraphs()     BASELINE: blank lines + page-boundary handling
  -> segment_clauses()        EXPERIMENTAL: numbering, headings, hierarchy
  -> segments_paragraph.json
     segments_clause_aware.json
     page_cleaning.json
```

Both methods run on every version and are stored side by side
(`CLAUDE.md` §13.2), so Experiment B can compare them and Person 3 can compare
chunking strategies without re-ingesting anything.

### Files

| File | Role |
|---|---|
| `backend/app/document_processing/page_cleaning.py` | Reversible removal of running furniture |
| `backend/app/document_processing/segmentation.py` | Both methods, the segment contract, the provenance invariant |
| `backend/app/document_processing/segmentation_cli.py` | Command-line front end; reads Phase 1 output, never rewrites it |
| `backend/app/document_processing/identity.py` | `segment_id_from_parts()` added |
| `backend/app/document_processing/errors.py` | `SegmentProvenanceError` added |

---

## 2. Commands

```bash
# Segment every version already extracted under data/processed/
python -m backend.app.document_processing.segmentation_cli --all

# One version, with a sample of the clause output printed, and a determinism check
python -m backend.app.document_processing.segmentation_cli \
    --version-id VER-fd6e1537c1a42574 --preview --check-deterministic

# Measure what cleaning contributes, by turning it off
python -m backend.app.document_processing.segmentation_cli --all --no-clean --force

# Offsets into raw text instead of normalised (see §6)
python -m backend.app.document_processing.segmentation_cli --all --use-raw-text --force
```

`extraction.json` is opened read-only and never rewritten. An existing segment
file is never overwritten without `--force`.

---

## 3. Header and footer cleaning

### 3.1 Why it is a separate stage

`normalization.py` works on one page at a time and is structurally incapable of
this: deciding that a line is a *running* header means comparing it against the
other pages of the same document. That is why `CLAUDE.md` §13.1 puts it in
Phase 2.

### 3.2 The threshold is 0.90, not the proposed 0.60

`CLAUDE.md` §13.1 proposed "a line repeating in the same position on ≥ 60% of
pages", and the Phase 1 validation report §4.6 confirmed that catches both
documents in the sample that have real running headers.

What the Phase 1 report did **not** check is whether 60% also removes things it
should not. Measured on the same four documents, it does:

| Repeated line | Pages | What it actually is |
|---|---|---|
| `Reserve Bank of India (Commercial Banks – Prudential Norms` | 36 of 37 (**97%**) | running header |
| RBI bilingual letterhead block (13 lines) | 2 of 2 (**100%**) | running letterhead |
| `BI` / `constituents` / `P&L or` / `balance` / `sheet` / `items` | 7 of 10 (**70%**) | **table column headers — clause content** |

The third row is pages 2–8 of the commercial-banks amendment. Its four-column
table spans seven pages, so pypdf's flattened output repeats the column
headings on each one. At a 60% threshold they are deleted from seven pages of a
ten-page document — destroying exactly the clause content Phase 2 exists to
segment.

0.90 separates the cases cleanly on this corpus: 97% and 100% are removed, 70%
is kept. It is a **tuned parameter reported as such**, not a law. It is
configurable (`--frequency-threshold`), the plan records the evidence for every
decision, and a document whose running header genuinely appears on only 80% of
its pages will be missed. The trade accepted: a missed header leaves noise in a
segment; a wrongly removed line loses policy text.

This is the clearest case in Phase 2 of a documented proposal being revised by
measurement, and the regression test for it is
`test_repeated_table_column_headers_are_not_removed`.

### 3.3 The other three safeguards

1. **Contiguity.** Candidates must form an unbroken run from the first (or
   last) non-blank line. A repeated phrase in body text is not a header,
   whatever its frequency.
2. **A per-page 50% ceiling.** If the rules want more than half of a page's
   non-blank lines, nothing is removed from that page and the plan says why. A
   page that is *mostly* header is far more likely a detection failure than a
   real page.
3. **A low-confidence warning below 4 pages.** On a 2-page document any
   repeated line trivially reaches 100%. Cleaning still runs — the RBI
   letterhead is genuinely furniture — but the plan records that the evidence
   is weak, so the result is never read as confident.

### 3.4 Printed page numbers need a separate rule

Frequency cannot catch them: every page's number is different. `master-circular`
has **no** line repeating on ≥60% of its pages at all — its letterhead is on
page 1 only — yet its printed page number is the first line of all 8 pages.
This is exactly the case the Phase 1 report §4.6 flagged as needing position as
well as frequency.

A line that is nothing but a page number (`32`, `- 32 -`, `Page 32`,
`Page 32 of 37`, `32/37`, `32 of 37`) in the top or bottom three lines is
removed when **either**:

- the number equals the physical page number — unambiguous; or
- bare-number lines appear in the same zone on ≥ the threshold fraction of
  pages **and the numbers increase** across them.

The second condition catches printed numbering that is offset from physical
numbering (a cover page, an annexure restarting at 1). Requiring the numbers to
*increase* is what stops a column of figures in a table being read as page
numbering — `test_non_increasing_numbers_are_not_treated_as_page_numbering`.

Page numbers are detected **before** running headers, and this ordering matters:
the capital-adequacy document prints its page number *between* the second header
line and the body, so without removing it first the header block would not be
contiguous and would be truncated to its first line.

### 3.5 Reversibility

Every removed line records the index it occupied, its text, a reason code and
human-readable evidence. `CleanedPage.restore()` reinserts them and must return
the input exactly.

This is not a nicety. Cleaning is the only stage that **deletes** text, and
`CLAUDE.md` §24 rule 6 forbids losing source evidence. The round-trip is
asserted in unit tests, on the real corpus before any segment file is written
(the CLI refuses to write otherwise), and again by the data-quality checks on
the artefacts.

### 3.6 Measured result

| Document | Pages | Lines removed | Breakdown | Reversible |
|---|---:|---:|---|---|
| capital-adequacy | 37 | **108** | 72 running-header + 36 page numbers | yes |
| lead-bank | 2 | **26** | 13 letterhead lines × 2 pages | yes |
| commercial-banks | 10 | **9** | page numbers (page 1 has none) | yes |
| master-circular | 8 | **8** | page numbers only | yes |

Nothing was removed that should have been kept. Two things worth noting:

- On lead-bank, detection stops exactly at `RBI/2026-27/300` — the reference
  number is correctly **kept**. That stopping point is the best single piece of
  evidence that the contiguity rule works.
- On commercial-banks, the 70% table headers were correctly **not** removed.

### 3.7 Known limitation: page-1-only letterheads

`master-circular`'s bilingual letterhead appears on page 1 only, so no
frequency rule can see it. It lands inside the preamble segment, mangled
Devanagari and all.

This is **not fixed**, deliberately. Page 1 is also where the title, reference
number and date live, so a positional rule confined to page 1 would risk
removing the document's most important metadata. It is reported as an uncleaned
artefact instead (`evaluation/experiment-b-segmentation.md` §3.9). A safer
approach once the corpus has more than one document per authority: match page
1's leading lines against the letterhead block already detected on other
documents from the same authority.

---

## 4. Paragraph segmentation — the baseline

Blank-line splitting with careful page-boundary handling, and nothing else. No
numbering detection, no heading detection, no hierarchy. Every segment is
`segment_type="paragraph"` with `clause_number=None`.

That is the point. It is the control condition for Experiment B, and a baseline
that quietly did some clause detection would make the comparison meaningless —
which is why the database schema has a `CHECK` forbidding the paragraph method
from ever carrying a clause number.

### The rules

1. A blank line **within a page** ends the paragraph.
2. A page boundary ends the paragraph **unless** the fragment so far is clearly
   incomplete *and* the next page's first line of content looks like a
   continuation. Both sides must agree.
3. A paragraph with no non-whitespace text is not emitted.

"Clearly incomplete" means the text does not end in sentence-final punctuation
(looking past closing brackets and quotes). "Looks like a continuation" means it
starts with a lowercase letter and does **not** open a clause marker. Checking
both sides is what stops the rule swallowing a clause marker after an
incomplete-looking page.

### A structural bug found by measuring it

The first version applied rule 1 before rule 2, on raw line order. Page text
begins and ends with newlines, and header removal leaves those behind, so **the
literal first line of every cleaned page is blank**. The blank-line rule
therefore always closed the paragraph before the boundary was ever considered:

- `cross_page_joins` was **structurally zero** — no paragraph could ever span a
  page, in any document;
- 32 of 36 page boundaries on the capital-adequacy document had an incomplete
  fragment open and were being split anyway.

The loop now tracks blank runs and page changes separately, and at a page
boundary **the page rule wins**. Blank lines adjacent to a page break are an
artefact of extracting text page by page; they are not evidence that the author
ended a paragraph there.

After the fix: 15 cross-page joins and 10 page-spanning paragraphs on
capital-adequacy, 1 on commercial-banks. The regression test is
`test_blank_lines_at_a_page_edge_do_not_prevent_a_join`.

This is worth recording because the bug was invisible to the unit tests that
existed — every one of them used single-page fixtures with no leading blank
line. It only showed up when a statistic that should have been non-zero was
zero on all four real documents.

---

## 5. Clause-aware segmentation

A single pass with a nesting stack. For each non-blank line: ask whether it
opens a new unit; if so close the current segment and open one, resolving the
marker's level against the stack and pointing `parent_segment_id` at the
innermost open segment at a shallower level; if not, the line continues the
current segment — **including across a page boundary**, because a clause that
continues onto the next page is still one clause.

A blank line does **not** close a clause. Numbered clauses in these documents
routinely contain blank lines between their own paragraphs, and splitting on
them would reduce this method to the baseline.

### 5.1 Numbering styles

| Style | Pattern | Occurrences in the corpus |
|---|---|---:|
| `numeric_dotted` | `2.`, `3.2`, `3.2.1` | **160** |
| `paren_roman` | `(i)`, `(iv)`, `(vii)` | **104** |
| `paren_number` | `(1)`, `(12)` | **52** |
| `paren_alpha` | `(a)`, `(A)` | **39** |
| `annexure` | `Annexure`, `Schedule II`, `Appendix` | **3** |
| `structural` | `CHAPTER IV`, `PART A` | **0** |
| `spelled` | `Clause 5`, `Section 7`, `Paragraph 29` | **0** |

The last two rows are a finding, not an omission. The brief asked for
`Clause 5` and `Section 7` and they are implemented — but in all four RBI
documents those words occur **only mid-sentence** ("conferred by Section 35A"),
never opening a line. They are covered by unit tests against synthetic input,
and Experiment B reports the zero count rather than implying the paths were
exercised on real data.

Unrecognised styles return `None` and the text is preserved as an unclassified
segment. Nothing is discarded and no clause number is ever invented.

### 5.2 The roman/alphabetic collision, handled explicitly

`(i)` is both the first roman numeral and the ninth letter. So are `(v)`,
`(x)`, `(c)`, `(d)`, `(l)` and `(m)`. In the corpus, 104 lines match a roman
pattern and 39 an alphabetic one, and the overlap is exactly these letters.

Resolution order:

1. Multi-character tokens are unambiguous — `(ii)`, `(iv)`, `(vii)` are roman.
   A multi-character token that is **not** a roman numeral is **not a marker at
   all** (see §5.3).
2. A single letter outside the roman set is alphabetic: `(a)`, `(b)`, `(e)`.
3. For the ambiguous letters, look at the previous bracketed marker. `(h)` then
   `(i)` is alphabetic; `(iii)` then `(iv)` is roman.
4. Otherwise default to roman **and flag it**. A bracketed `(i)` opening a list
   is far more often roman "first item" than letter nine.

The flag is the part that matters. It propagates to the segment's
`metadata.clause_number_ambiguous`, is counted in the result's stats, raises a
warning on the CLI, and is shown as a pill in the preview. 31 markers across the
corpus are ambiguous (21 in capital-adequacy, 10 in commercial-banks). The
ambiguity is **reported, never silently resolved** — which is also what the
annotation guideline tells a human annotator to do, so the scorer compares like
with like.

### 5.3 Three bugs found by reading the real output

All three were invisible to the tests that existed, and all three are now
regression tests.

**1. `(STs)` became a clause marker.** The master-circular's line
*"…credit facilities to Scheduled Castes (SCs) & Scheduled Tribes"* wraps so
that `(STs)` begins the next line. The first version classified any
multi-character bracketed token as a list letter, so it opened a clause
numbered `(STs)`. Multi-character bracketed tokens are now only accepted as
roman numerals.

**2. `5.5%–6.375% 3.5%–3.625% 100%` matched the dotted-numeric pattern.** It is
a row of the capital-adequacy document's flattened table. A clause number must
now be followed by whitespace and then prose — a letter or an opening bracket —
not by a percent sign or another number.

**3. `Schedule` matched inside `Scheduled`.** The annexure pattern had no
boundary check after its alternation, so the master-circular's wrapped
*"Scheduled Castes Finance & Development Corporation"* opened a bogus annexure
segment, twice in one document. A `\b` would not have helped — the boundary
between `e` and `d` is not a word boundary — so the fix is an explicit
`(?![A-Za-z])`.

That third fix moved the measured score materially, which is worth seeing:

| capital-adequacy, `clause_aware` | before | after |
|---|---:|---:|
| boundary precision | 0.867 | **1.000** |
| boundary F1 | 0.897 | **0.963** |
| exact-match rate | 0.786 | **0.929** |
| incorrect splits | 2 | **0** |

Two false clause starts cost 0.066 of F1 on a 14-clause sample. It is also a
warning about the sample size: with 18 confident annotated clauses, one regex
defect is worth a large fraction of the headline number.

**A fourth, caught by a test rather than by reading output.** Python's `\d`
matches **every** Unicode decimal digit, so the Devanagari numeral in `२. A line`
was being detected as a clause number by accident. All digit classes are now
explicitly `[0-9]`; Devanagari-numbered clauses fall through to an unclassified
segment — text preserved, no number invented — which is the documented
limitation while multilingual work is paused (D-3).

### 5.4 Hierarchy

Dotted numeric markers state their own depth: `3.2.1` is level 3. Bracketed
markers do not — `(a)` is level 2 under `3.` but level 3 under `3.2` — so their
depth is one below the innermost enclosing marker of a shallower *kind*:

```text
structural / annexure  <  numeric dotted  <  paren number  <  paren alpha  <  paren roman
```

A bracketed marker found with nothing open defaults to level 2, i.e. it is
assumed to sit under an unseen parent rather than at the top level. Treating
`(a)` as top-level would make every following clause its sibling.

Measured depths across the corpus: 0 → 146, 1 → 142, 2 → 121, 3 → 76, 4 → 16.

`level` (from the marker style) and the depth implied by walking
`parent_segment_id` are kept **separately**, because they can disagree and that
disagreement is diagnostic. The preview shows both.

### 5.5 Nothing is discarded

Text that cannot be attributed becomes `preamble` (before the first marker) or
`unclassified` (after it), with `clause_number=None`. Asserted by
`test_no_input_text_is_discarded_by_either_method`, which checks that every
input word appears somewhere in the output of **both** methods.

---

## 6. Page provenance — the invariant, and how it is kept

### 6.1 What is guaranteed

Every segment carries `page_start`, `page_end`, and one `page_offsets` entry
**per page it touches**, each with character offsets and line indices. A clause
spanning pages 3–4 gets `page_start=3, page_end=4` and two entries. A spanning
clause is never collapsed onto one page.

A segment that cannot establish provenance raises `SegmentProvenanceError`
rather than being emitted with a guessed page. `CLAUDE.md` §10.2: a guessed page
reference is worse than no clause, because the project's whole claim is that a
citation can be checked.

### 6.2 How it is kept correct by construction

The subtle part is that segmentation runs on *cleaned* text, which is not what
gets stored. An offset into cleaned text would index nothing.

`SourceLine` bridges that. It is produced from the cleaned text but carries the
character range the line occupies in the **uncleaned** page text it came from,
taken from the `line_offset_map` that `clean_pages` produces alongside the
cleaned text. Segments are spans of `SourceLine`, so their offsets address text
that exists on disk — by construction, with no arithmetic to get wrong.

Enforcement is layered, so no code path can bypass it:

| Layer | What it catches |
|---|---|
| `Segment.__post_init__` | No offsets, reversed range, range disagreeing with the offsets, empty text — at construction, so no code path can emit a bad segment |
| `build_source_lines` | A `line_offset_map` out of step with the cleaned text, which would make every downstream offset silently wrong |
| The CLI | Refuses to write segment files if cleaning is not reversible for any page |
| `evaluation/data_quality.py` | The same invariants re-checked on the written artefacts, which is what Persons 2–5 actually read |
| The proposed schema | `CHECK`s plus a deferred constraint trigger, so the invariant holds for data arriving by any route |

Verified on the corpus: **all 501 segments** across both methods have valid,
resolvable page provenance, including the 45 that span a page break. Experiment
B measures page-provenance correctness at **100%** of matched pairs, on every
document, at every tolerance.

### 6.3 `offset_basis` — a contract change needing sign-off

`CLAUDE.md` §11.4 specifies offsets into `raw_text`. The pipeline produces
offsets into `normalized_text` and **records which** on every output as
`offset_basis`.

Why: segmentation has to run on normalised text to work at all;
`collapse_blank_lines` changes a page's line count so raw and normalised share
no line numbering (mapping between them needs a per-page offset map that §11.4
puts out of MVP scope); and the API serves `text=normalized` by default, so
offsets on the same basis as the displayed text are right for the common path.

The §11.4 approximation is therefore **inverted**, not removed, and the trade is
explicit and recorded per segment. `--use-raw-text` produces the other basis.
Flagged in four places — here, `backend/app/db/README.md` §3, the module
docstring, and the committed JSON — and it should be agreed before Phase 3
loads anything.

---

## 7. The output contract

`data/processed/<version_id>/segments_{paragraph,clause_aware}.json`:

```json
{
  "schema_version": "1.0.0",
  "generated_at": "...",
  "source_extraction": { "checksum": "...", "extraction_tool": "pypdf 6.18.1", "page_count": 37 },
  "document_id": "DOC-df7091d72242ee75",
  "version_id": "VER-fd6e1537c1a42574",
  "segmentation_method": "clause_aware",
  "offset_basis": "normalized_text",
  "segment_count": 254,
  "stats": { "marker_styles": {...}, "ambiguous_markers": 21, "max_level": 4, "...": "..." },
  "warnings": ["21 bracketed marker(s) were ambiguous ..."],
  "segments": [
    {
      "segment_id": "SEG-2146f692b738fe91",
      "document_id": "DOC-df7091d72242ee75",
      "version_id": "VER-fd6e1537c1a42574",
      "segmentation_method": "clause_aware",
      "ordinal": 5,
      "segment_type": "clause",
      "clause_number": "(2)",
      "heading": null,
      "text": "<verbatim text as segmented from the source document>",
      "page_start": 1,
      "page_end": 2,
      "page_offsets": [
        { "page_number": 1, "char_start": 1719, "char_end": 1955,
          "line_index_start": 32, "line_index_end": 34 },
        { "page_number": 2, "char_start": 119, "char_end": 318,
          "line_index_start": 4, "line_index_end": 6 }
      ],
      "parent_segment_id": "SEG-94f9972109cc6544",
      "level": 2,
      "metadata": { "marker_style": "paren_number" }
    }
  ]
}
```

`page_cleaning.json` carries the detection plan (every candidate considered,
removed or not), every removed line with its reason and evidence, and a
`totals.reversible` flag.

### Identifiers

`segment_id` is `SEG-` plus 16 hex characters of
`sha256(version_id|method|ordinal)`. Deterministic, so a re-run lands on the
same ids; distinct per method, because paragraph 12 and clause 12 are not the
same segment. It deliberately does **not** hash the text — otherwise a
whitespace fix in normalisation would change every id and invalidate every
committed annotation.

### Determinism

Given the same `extraction.json` and the same options, output is byte-identical
apart from `generated_at`. Nothing consults a clock, a random source or
dictionary insertion order. `--check-deterministic` segments twice and compares,
and it passes on all four documents.

---

## 8. Measured output

| Document | Pages | Paragraph segments | Clause segments | Cross-page (para / clause) | Max level |
|---|---:|---:|---:|---:|---:|
| lead-bank | 2 | 4 | 11 | 0 / 0 | 1 |
| master-circular | 8 | 46 | 52 | 0 / 2 | 2 |
| commercial-banks | 10 | 14 | 49 | 1 / 8 | 3 |
| capital-adequacy | 37 | 74 | 254 | 10 / 24 | 4 |
| **total** | **57** | **138** | **366** | **11 / 34** | — |

Segment types across the corpus: `clause` 355, `paragraph` 138, `preamble` 4,
`annexure` 3, `heading` 1.

The two methods produce clearly different output on every document, which is
what makes the comparison meaningful — and
`test_the_two_methods_produce_different_segment_counts` guards against the
clause method silently degrading into the baseline.

---

## 9. Evaluation summary

Full detail, nine failure cases and all the caveats:
`evaluation/experiment-b-segmentation.md`. The headline, pooled over a 4-page,
21-clause single-annotated sample:

| Method | Boundary F1 | Exact match | Clause numbers recovered | Page provenance |
|---|---:|---:|---:|---:|
| `paragraph` (baseline) | **0.865** | 0.722 | **0.000** | 1.000 |
| `clause_aware` | 0.714 | 0.722 | **0.929** | 1.000 |

**Read this honestly: on boundary F1 alone, clause-aware segmentation does not
beat the baseline.** On prose the two are indistinguishable (0.963 vs 0.966);
on a table-heavy circular clause-aware is much worse (precision 0.182), because
flattened table rows look exactly like numbered clauses. What only clause-aware
does is recover **clause identity** — 92.9% against 0.0% — and without that the
Ask and Compare features cannot cite a clause at all.

The highest-value fix is table-aware segmentation (§10.1), and it is as much an
extraction problem as a segmentation one.

Three kinds of evidence, kept separate on purpose:

| Evidence | What it shows | Where |
|---|---|---|
| 385 unit tests | The code does what it was written to do, on synthetic fixtures | `backend/tests/` |
| Automatic evaluation | How closely each method matches human judgement on 4 real pages | `evaluation/experiment-b/results.json` |
| Manual inspection | *Why* the methods fail where they do — 9 cases, no metric | report §3 |

---

## 10. What is not done

### 10.1 Table-aware segmentation — the largest gap
A flattened table is indistinguishable from a numbered list, which is the only
case where clause-aware segmentation is clearly worse than the baseline.
Experiment A §5.2 showed PyMuPDF's `find_tables()` recovers the row structure on
exactly these pages, and PyMuPDF is already pinned. The proposal: record table
regions per page at extraction time, and treat a table region as one segment
belonging to the clause that introduces it — which is also what the annotation
guideline §4.5 tells a human to do.

### 10.2 Numbering-restart detection
A clause number that goes backwards at the same nesting level is a restart. The
lead-bank circular has clauses `2.`–`5.` and table rows `1.`–`3.`, and the
segmenter emits `clause_number` `"1"`, `"2"`, `"3"` twice with no indication.
Segment ids stay unique so nothing is lost, but it is the strongest argument for
citations carrying `clause_id` rather than `clause_number` — which `CLAUDE.md`
§12.2 already specifies.

### 10.3 Page-1-only letterhead removal
§3.7.

### 10.4 A larger, independently annotated sample
The highest-value change to the *evaluation* rather than the system. 21 clauses
on 4 pages, single-annotated by an annotator who is not independent of the
system, is enough to demonstrate the method and find the bugs in §5.3 — it is
not enough to support a confident comparison. Priority: a second annotator on
the existing 4 pages to replace `PENDING` with a real agreement figure, then
pages from `capital-adequacy`, the only document exercising `(a)`, `(i)` and
`(12)` numbering and 4-level nesting.

### 10.5 Not in this phase, by design
No database, no API, no OCR, no multilingual handling. Phase 3 and Phase 4 are
still gated on `CLAUDE.md` §24's stop condition and on D-8 and D-10.

---

## 11. Integration notes for Persons 2–5

What is available now, and what to be careful of.

**Available now, as committed JSON:** both segmentations of all four versions,
with clause numbers, headings, hierarchy, segment types, page ranges and
character offsets. Read them through
`preview_app/components/data_access.py`'s shapes if you want a worked example of
consuming them — it is the only data layer written against this contract so far.

- **Person 2 (Document View, classification).** `segment_type` tells you what a
  segment is; `parent_segment_id` and `level` give you a tree. Expect
  `segment_type="preamble"` on the first segment of every document, sometimes
  containing a mangled Devanagari letterhead (§3.7). `heading` is populated
  rarely — 1 segment in the whole corpus — so do not design around it.
- **Person 3 (Ask / RAG).** Two chunkings of the same text, side by side, with
  no re-ingestion needed: `segments_paragraph.json` averages 275–1081 characters
  per segment and `segments_clause_aware.json` 172–314. **Raise the citation
  question before you build retrieval:** pypdf inserts spaces inside words
  (`'M inimum'`, `'L ead Banks'`), so a `quote` that is a verbatim substring of
  `segments.text` may not match what a user typed. Either citations tolerate
  whitespace differences or normalisation gains a whitespace-insensitive
  matching helper. This is a contract question, not an implementation detail,
  and it is still open from Phase 1 §5.1.
- **Person 4 (Compare).** `parent_segment_id` makes sub-clause alignment
  possible. But the corpus still contains **zero genuine version pairs** — both
  amendments amend 2025 parent Directions that are not here — so there is
  nothing to align yet (D-10). Also read §10.2: clause numbers are not unique
  within a document where a table restarts numbering, so align on `segment_id`
  and treat `clause_number` as a hint.
- **Person 5 (Library search, integration).** `segments[].text` is the search
  corpus. `evaluation/data_quality.py` is a worked example of the shared
  evaluation conventions you own, and it is yours to overrule — the severity
  grading and the "report, never repair" rule are proposals, not decisions.

**Contract point to agree:** `offset_basis` (§6.3). It changes what a character
offset means.
