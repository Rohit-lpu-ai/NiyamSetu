# Provenance validation — Phase 2 closure

**Run:** 2026-11-12 · **Ran by:** Person 1 (backend + document NLP) ·
**Corpus:** the four verified RBI PDFs in `data/manifest.csv`, 57 pages.

Reproduce:

```
.venv\Scripts\python -m backend.app.document_processing.cli --manifest data/manifest.csv
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli --all
.venv\Scripts\python -m evaluation.data_quality
```

This report answers one question: **can every stored segment be traced back to
the exact page text it came from?** Not "are the page numbers plausible" — can
a consumer take the stored numbers and get the clause back.

---

## 0. What is new in this run

Phase 2 already checked that provenance is *well formed*: offsets present,
ordered, within the page, page range agreeing with the offsets
(`check_segment_provenance`). That is necessary and not sufficient. An offset
can satisfy every bound and still point at the wrong characters — an
off-by-one, a stale offset after a re-run, or the right numbers read against
the wrong text basis.

So this run adds a thirteenth data-quality check, **`offset_resolution`**
(`evaluation/data_quality.py`), which performs the operation a citation
actually performs: slice `pages.normalized_text[char_start:char_end]` for each
page the segment touches, join, and compare with the stored segment text.

**Result: all 501 segments rebuild exactly.** 0 errors.

---

## 1. The resolution rule, stated precisely

```
segment.text  ==  " ".join(
    normalized_text[o.char_start : o.char_end] for o in segment.page_offsets
)
```
…up to whitespace.

The whitespace tolerance is a **finding, not a convenience**, and it is the one
thing a consumer has to know. Segmentation joins wrapped source lines with a
space, so a slice of the page carries the newline the segment text does not:

```
page slice   : '1.2 Banks will have to establish closer liaison with the District Industries Centres,\nwhich have been set up...'
segment text : '1.2 Banks will have to establish closer liaison with the District Industries Centres, which have been set up...'
                                                                                           ^ newline became a space
```

Character **ranges are exact** — the slice above is 162 characters and the
segment is 162 characters. Only intra-segment whitespace differs. Anything
beyond whitespace still fails the check, which is pinned by
`test_an_offset_shifted_off_by_one_is_an_error`: shifting one `char_start` by a
single character is caught.

**D-11 is therefore confirmed empirically, not just by declaration.** Every
segment file declares `offset_basis: "normalized_text"`, and the offsets
resolve against `normalized_text` and against nothing else. Measured, applying
the same rule to each candidate basis:

| basis sliced | segments that resolve (of 501) |
|---|---:|
| `normalized_text` | **501** |
| `cleaned_text` | 14 |
| `original_text` | 0 |

The 14 that happen to resolve against `cleaned_text` are segments on pages
where cleaning removed nothing before the segment's start, so the two bases
coincide there by accident. That is the trap: a consumer guessing the basis
would see a handful of correct citations and conclude it had guessed right.
Hence the basis is a stored, per-version column, not a global constant.

---

## 2. Results against the real corpus

| version | method | pages | segments | single-page | multi-page | starts p1 | ends last p | **resolved** | header lines removed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| VER-6507f3…(lead-bank) | paragraph | 2 | 4 | 4 | 0 | 1 | 3 | **4** | 26 |
| VER-6507f3… | clause_aware | 2 | 11 | 11 | 0 | 5 | 6 | **11** | 26 |
| VER-7c5ad7…(commercial-banks amdt) | paragraph | 10 | 14 | 13 | 1 | 2 | 2 | **14** | 9 |
| VER-7c5ad7… | clause_aware | 10 | 49 | 41 | 8 | 6 | 3 | **49** | 9 |
| VER-b8a13c…(272 master circular) | paragraph | 8 | 46 | 46 | 0 | 9 | 1 | **46** | 8 |
| VER-b8a13c… | clause_aware | 8 | 49 | 47 | 2 | 1 | 7 | **49** | 8 |
| VER-fd6e15…(capital-adequacy 11th) | paragraph | 37 | 74 | 64 | 10 | 3 | 1 | **74** | 108 |
| VER-fd6e15… | clause_aware | 37 | 254 | 230 | 24 | 6 | 7 | **254** | 108 |
| **total** | both | **57** | **501** | **456** | **45** | **33** | **30** | **501** | **151** |

Every one of the Step-2 checklist items, against this corpus:

| # | Check | Result |
|---|---|---|
| 1 | Every segment has valid page references | **501/501** |
| 2 | Every referenced page exists | **501/501** — no offset names a page absent from `extraction.json` |
| 3 | Every segment has valid page offsets | **501/501** — present, non-negative, ordered, within the page |
| 4 | A cross-page segment retains every source page | **45/45** — pages touched are exactly the contiguous range `page_start..page_end`, asserted per segment; longest span is 5 pages |
| 5 | Text resolves from stored offsets | **501/501** |
| 6 | Invalid/missing provenance is rejected | **yes**, at three layers — see §4 |
| 7 | No segment silently receives an invented page number | **yes** — see §3 |

---

## 3. "No invented page number", concretely

A page number in this pipeline is never written by hand or inferred. Each
segment's `page_offsets` are derived from `SourceLine` records, and every
`SourceLine` carries the page it was read from — so a segment's page range is
*computed from the lines it contains*, and a segment with no lines cannot be
constructed. `Segment.__post_init__` raises if `page_offsets` is empty, which
means a provenance-less segment cannot exist in memory, let alone be written.

The check that would catch a violation is #4 above: if a page number were
guessed rather than carried, the set of pages the offsets touch would stop
matching the declared `page_start..page_end` range. It matches for all 501.

---

## 4. Where invalid provenance is rejected

Three independent layers, deliberately — the file artefacts are what
Persons 2–5 read, so a guarantee that holds only in memory is not enough:

1. **In memory** — `Segment.__post_init__` rejects empty `page_offsets`, a
   reversed page range, and offsets outside the declared range.
2. **On the artefacts** — `evaluation/data_quality.py` checks
   `segment_provenance` *and* `offset_resolution` over the JSON on disk, which
   catches a hand-edited file or output from an older pipeline version.
3. **In the database** — `backend/app/db/migrations/0001_initial_schema.sql`:
   a deferred constraint trigger (`segments_provenance_required`) fails the
   transaction at COMMIT if any segment has no `segment_pages` row, and a
   per-row trigger rejects a `segment_pages` row whose `page_number` disagrees
   with the page it references, whose page belongs to a different version, or
   whose `char_end` runs past the end of that page under the version's declared
   `offset_basis`. Verified by execution — see
   `evaluation/2026-11-12-phase3-postgresql.md`.

Each of the seven defect classes has a negative test, so a regression fails the
suite rather than quietly degrading the corpus: see
`backend/tests/test_data_quality.py` (offset resolution: 6 tests) and
`backend/tests/test_segmentation.py`.

---

## 5. Limitations — what this run does **not** establish

Stated plainly, because the numbers above are otherwise easy to over-read.

- **Empty and low-text pages are untested on real data.** This corpus has
  **0 empty pages and 0 pages lacking meaningful text**, so the
  empty-page provenance path is exercised only by synthetic fixtures in
  `backend/tests/`. The MVP excludes scanned PDFs (D-6), which is where real
  empty pages would come from. Not a passing result — an unexercised path.
- **"Resolved" means self-consistent, not correct.** This check proves the
  offsets agree with the text the pipeline itself extracted. It cannot prove
  the extraction read the PDF correctly; the Devanagari letterhead loss
  (Experiment A §3) resolves perfectly here and is still wrong text.
- **One authority, four documents, no version pairs.** D-10 remains the
  binding limitation on every metric in this project.
- **Whitespace tolerance is real tolerance.** A defect consisting *only* of
  whitespace misplacement inside a segment would not be caught.
- **`cleaned_text` offsets are not stored.** Segmentation reads cleaned text
  but records offsets against `normalized_text` (the line-offset map in
  `page_cleaning.json` is what relates them). Highlighting against
  `cleaned_text` therefore needs that map; it is not a stored offset basis.
- Page numbers are **physical** PDF page order, never the number printed on the
  page. Where they differ, that is recorded as a manifest note and never
  corrected here (`data/README.md` §6).

---

## 6. Verdict

The provenance invariant holds on the real corpus, under the strongest check
the project has: not "the page reference looks valid" but "the clause can be
rebuilt from it, character for character". 501 of 501, with the defect classes
each covered by a failing-on-regression test.

The honest gap is the empty-page path, and the honest caveat is that
self-consistency is not extraction correctness.
