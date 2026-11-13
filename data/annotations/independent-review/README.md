# Independent annotation package — Experiment B

**Status: AWAITING AN INDEPENDENT ANNOTATOR. Inter-annotator agreement is
`PENDING` and no agreement figure exists.**

Prepared 2026-11-12 by Person 1. This directory is the *task*, not a result.

---

## 1. Why this package exists

Experiment B's committed metrics (`evaluation/experiment-b-segmentation.md`)
rest on a sample annotated by **one** annotator — `person1-claude-code` — who
is **not independent of the system being measured**. That is recorded in
`data/annotations/README.md` §7 and in the report's §0, and it is the single
largest caveat on every segmentation number this project reports.

It cannot be fixed by annotating more pages with the same annotator. It needs a
**second person who has not seen the segmenter's output**.

### The anchoring problem, and why `template` cannot solve it

The existing `template` subcommand pre-fills an annotation file from the
clause-aware segmenter's own boundaries. For the system's author correcting
their own work that is an efficient starting point, and
`validate --against-template` reports how many records actually got changed.

For *agreement* it is worthless. An annotator shown our boundaries tends to
accept them, so the resulting number measures anchoring, not independent
judgement — it would be a fabricated agreement figure with a real-looking
decimal point on it.

So this package is produced by a different subcommand, **`blind`**, which emits
the normalised page text with line numbers and **nothing else**: no predicted
boundaries, no clause numbers, not even the number of segments the system
found.

---

## 2. What is in here

Five worksheets, 11 pages across 3 of the 4 corpus documents:

| worksheet | pages | chosen because |
|---|---|---|
| `VER-b8a13c2a7651eb84.pages1-2` | 2 | paragraph-dense prose; the paragraph baseline's strongest document (17 paragraph segments in 2 pages) |
| `VER-b8a13c2a7651eb84.pages7-8` | 2 | **annexure/schedule markers** |
| `VER-7c5ad74aba9ebd62.pages1-3` | 3 | dotted numeric + roman nesting, and the **repeated table column headers** that forced the 0.90 header threshold |
| `VER-7c5ad74aba9ebd62.pages9-10` | 2 | **bracketed alphabetic** numbering |
| `VER-fd6e1537c1a42574.pages1-2` | 2 | deepest nesting in the corpus (levels 0–3) and the **ambiguous `(i)`** case |

Measured structural coverage of these 11 pages, by the system's own reckoning
— stated here for the *reviewer of this package*, and deliberately **not** in
the worksheets the annotator reads:

- **68** clause-aware segments, **31** paragraph segments
- all five marker styles observed in the corpus: `numeric_dotted` (33),
  `paren_roman` (22), `paren_number` (5), `paren_alpha` (3), `annexure` (2)
- segment types: `clause` (63), `preamble` (3), `annexure` (2)
- hierarchy depth **0–3**
- **8** cross-page segments
- **4** ambiguous markers

This satisfies the Work Package 1 §5C requirement that the sample cover
different numbering styles, paragraph boundaries, clause boundaries, cross-page
clauses, and at least one genuinely difficult structure.

### The difficult structure, named

`(i)` is simultaneously a lowercase roman numeral and the ninth letter of the
alphabet. In a list that runs `(i) (ii) (iii)` it is roman; in one that runs
`(h) (i) (j)` it is a letter. The segmenter resolves it as roman by default and
**flags the segment** `clause_number_ambiguous: true` with a reason string,
rather than silently picking. 21 segments in the corpus carry that flag; 4 are
in this package.

This is the case where an independent annotator is most likely to disagree with
us, which is exactly why it is included. If the annotator reads a `(i)` as a
letter where we read it as roman, **the guideline is what needs amending** —
record the disagreement, do not adjust to match us (`data/annotations/README.md`
§7).

---

## 3. What the annotator does

1. Read `data/annotations/README.md` — the guideline. It was written **before**
   any annotating was done, and it defines what counts as a boundary.
2. Open a worksheet. Open the matching PDF in `data/raw/`.
3. Mark the clause boundaries you believe are correct.
4. Write one JSONL record per clause, in the §4 format of the guideline, to:
   ```
   data/annotations/segmentation/<VERSION_ID>.annotator2.jsonl
   ```
   Set `"annotator"` to your own name. The `.annotator2` infix matters: the
   scorer's `annotation_files()` excludes those files from the primary metrics,
   so adding yours cannot silently move the committed headline numbers.
5. Use `"uncertain": true` with a reason in `"note"` wherever you genuinely
   cannot decide. An uncertain record is more useful than a confident guess —
   the scorer reports confident and uncertain subsets separately.

Regenerate a worksheet at any time (it is deterministic):

```
.venv\Scripts\python -m evaluation.experiment_b_segmentation blind ^
    --version-id VER-fd6e1537c1a42574 --pages 1-2 ^
    --output data/annotations/independent-review/VER-fd6e1537c1a42574.pages1-2.worksheet.txt
```

---

## 4. What happens after

Once a second annotator's files exist, agreement gets computed and reported —
boundary-level agreement on the overlapping pages, plus the disagreement list.
Until then:

- `inter_annotator_agreement` stays the literal string
  `"PENDING - no second annotator"` in `evaluation/experiment-b/results.json`.
- No agreement figure appears in any report.
- Every segmentation metric keeps its §0 caveat.

**This file must not be read as evidence that independent review happened.** It
is evidence that the package for it exists and that the honest status is
recorded. If this project is presented in a viva before a second annotator has
worked through these pages, the correct answer to "how do you know your
segmentation is right?" is: *"we measured it against a single non-independent
annotation on a small sample, we know that is the weakness, and here is the
prepared package that would fix it."*
