# Experiment B — paragraph vs clause-aware segmentation

- **Date:** 2026-11-12
- **Run by:** Person 1 (Backend + Document NLP)
- **Scoring script:** `evaluation/experiment_b_segmentation.py`
- **Annotations:** `data/annotations/segmentation/` (committed)
- **Guideline:** `data/annotations/README.md`, version 1.0
- **Raw results:** `evaluation/experiment-b/results.json` (committed)

**Commands:**

```bash
python -m evaluation.experiment_b_segmentation validate --against-template
python -m evaluation.experiment_b_segmentation score
```

---

## 0. Read this first — what these numbers are worth

Three limitations, stated before any metric, because each one bounds what the
rest of this report can claim.

### 0.1 The annotator is not independent of the system

The annotations were produced by **Claude Code working as Person 1's
engineering assistant**, which also wrote the segmenter being scored. Every
record carries `annotator: "person1-claude-code"` and
`review_status: "awaiting_owner_review"`.

That is a conflict of interest and the numbers below must be read as a
**provisional self-check, not independent ground truth.** The mitigation is
that the sample is small enough for the owner to review by hand, the guideline
was written before annotating, and the limitation is recorded here, in
`data/annotations/README.md` §7 and in `CLAUDE.md`.

### 0.2 Inter-annotator agreement is `PENDING`

No second annotator has worked on this sample, so **no agreement figure
exists** and none is quoted. `PENDING` means "not yet measured", not "low".
The procedure for producing one is `data/annotations/README.md` §8.

### 0.3 The sample is 4 pages and 21 clauses

| | |
|---|---|
| Documents | 2 of the 4 in the validation sample |
| Pages | 4 |
| Annotated clauses | 21 (18 confident, 3 uncertain) |
| Records differing from the auto-generated template | **21 of 21** |

Smaller than the 10 documents × ~5 pages `CLAUDE.md` §15 proposed. That was a
deliberate trade — `docs/backend-roadmap.md` Phase 2 says "shrink the sample and
say so, rather than annotating sloppily". The two documents were chosen to be
an **easy case and a hard case**, not a representative draw:

- `master-circular-272mc` pages 2–3 — flowing prose with `1.` / `1.1`
  numbering, one clause per paragraph. The easy case.
- `lead-bank-delhi` pages 1–2 — a two-page circular that is mostly `Sr. No`
  tables. The hard case.

The "21 of 21 differ from template" row matters: the template generator
pre-fills markers from the segmenter's own output, so a file identical to its
template would be scoring the segmenter against itself
(`data/annotations/README.md` §9.1). Every record was changed.

---

## 1. Headline results

Boundary tolerance **5 characters**. Uncertain records excluded (§4 reports them
included). `P` / `R` / `F1` are boundary precision, recall and F1; `exact` is
the whitespace-insensitive exact-text-match rate; `num%` is clause-number
preservation; `prov%` is page-provenance correctness.

| Document | Method | ann | pred | match | P | R | F1 | exact | merge | split | num% | prov% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| master-circular (pp 2–3) | `paragraph` | 14 | 15 | 14 | 0.933 | **1.000** | **0.966** | 0.929 | 0 | 1 | **0.000** | 1.000 |
| master-circular (pp 2–3) | `clause_aware` | 14 | 13 | 13 | **1.000** | 0.929 | 0.963 | 0.929 | 0 | 0 | **0.923** | 1.000 |
| lead-bank (pp 1–2) | `paragraph` | 4 | 4 | 2 | 0.500 | 0.500 | 0.500 | 0.000 | 2 | 0 | 0.000 | 1.000 |
| lead-bank (pp 1–2) | `clause_aware` | 4 | 11 | 2 | **0.182** | 0.500 | 0.267 | 0.000 | 2 | 0 | 1.000 | 1.000 |
| **POOLED** | `paragraph` | 18 | 19 | 16 | 0.842 | 0.889 | **0.865** | 0.722 | 2 | 1 | **0.000** | **1.000** |
| **POOLED** | `clause_aware` | 18 | 24 | 15 | 0.625 | 0.833 | **0.714** | 0.722 | 2 | 0 | **0.929** | **1.000** |

Pooled figures are computed from pooled **counts**, not by averaging
per-document rates: a 2-page sample and a 37-page document should not carry
equal weight in a mean.

### 1.1 The result, stated plainly

**On boundary F1 alone, the clause-aware method does not beat the paragraph
baseline. Pooled, it loses: 0.714 against 0.865.**

That is not the conclusion a reader would expect from a report written by the
person who built the clause-aware method, so here is what is actually going on:

1. **On prose, the two methods are indistinguishable on boundaries.** 0.963 vs
   0.966 on the master-circular — a one-clause difference. That document's
   clauses happen to be exactly one blank-line-separated paragraph each, so
   splitting on blank lines recovers the clause structure essentially for free.
2. **On tables, the clause-aware method is much worse.** 0.267 vs 0.500 on the
   lead-bank circular, driven entirely by precision (0.182): 9 of its 11
   predicted segments are flattened table rows mistaken for clauses (§3.1).
3. **Only the clause-aware method recovers clause numbers at all: 92.9%
   against 0.0%.** The baseline scores zero by construction, and this is not a
   detail. `CLAUDE.md` §12.2 requires every citation to carry a `clause_id` and
   a quote; a user asking "what does clause 2.4 require" needs clause 2.4 to be
   *identified*, not merely delimited. A method with perfect boundaries and no
   clause numbers cannot serve the Ask or Compare features.
4. **Page provenance is correct in 100% of matched pairs for both methods**, on
   every document, at every tolerance. That is the project's load-bearing
   invariant and it is the one number here that is unambiguously good.

**So: the honest claim is not "clause-aware segmentation is better". It is that
clause-aware segmentation buys clause identity at no boundary cost on prose, and
at a substantial precision cost on flattened tables.** Fixing the table case
(§5) is the highest-value next step, and it is an *extraction* problem as much
as a segmentation one.

---

## 2. Tolerance sensitivity — the parameter is doing nothing

`CLAUDE.md` §15 requires "a stated tolerance for whitespace-only offsets". It is
5 characters. Measured across five values, boundary F1 does not move at all:

| Document | Method | t=0 | t=2 | t=5 | t=20 | t=80 |
|---|---|---:|---:|---:|---:|---:|
| master-circular | `paragraph` | 0.966 | 0.966 | 0.966 | 0.966 | 0.966 |
| master-circular | `clause_aware` | 0.963 | 0.963 | 0.963 | 0.963 | 0.963 |
| lead-bank | `paragraph` | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 |
| lead-bank | `clause_aware` | 0.267 | 0.267 | 0.267 | 0.267 | 0.267 |

Identical at a 0-character tolerance and at 80. **On this sample, predicted and
annotated boundaries either coincide exactly or are nowhere near each other.**

Two readings, and the second is the right one:

- The flattering reading: boundary detection is exact when it works.
- The honest reading: the tolerance parameter is **not load-bearing**, so the
  headline number does not depend on a tunable knob — which is good — but it
  also means the metric cannot distinguish "nearly right" from "completely
  wrong". Every near-miss case this tolerance was designed for (a segment
  starting one space early) simply does not occur, because both the annotation
  markers and the segment offsets are derived from the same cleaned line
  structure. A future sample annotated against the PDF by eye rather than
  against extracted text would likely exercise it.

---

## 3. Failure cases

Nine concrete cases, each traceable to a specific segment or annotation in
`evaluation/experiment-b/results.json`. The brief asked for at least five.

### 3.1 Flattened table rows become clauses — the worst failure

**Document:** lead-bank, pages 1–2. **Method:** `clause_aware`.
**Effect:** precision 0.182; 9 of 11 predicted segments are spurious.

The `Sr. No` tables extract as a vertical stack, so each table row begins at the
start of a line with its row label:

```
predicted [2] p1 "1. Old Delhi Canara Bank 03E (to be read as 'Numeral Zero..."
predicted [3] p1 "2. Central North State Bank of India 03F (to be read as..."
predicted [4] p1 "3. Outer North Punjab National Bank 03G (to be read as..."
predicted [6] p2 "1. Central Canara Bank 358 State Bank of India"
predicted [7] p2 "2. Northwest Punjab National Bank 362 Union Bank of India"
predicted [9] p2 "1 Shahdara Bank of Baroda 369"
```

`1.`, `2.`, `3.` at the start of a line is exactly what a top-level clause looks
like. Nothing in the extracted text distinguishes a table row label from a
clause number — the table's cell boundaries are gone (Experiment A §5.2).

**Why this is partly an extraction problem.** PyMuPDF's `find_tables()`
recovers the row structure on these pages (Experiment A §5.2). A segmenter told
"lines 15–29 of page 1 are a table" would skip them. That is the fix in §5.1.

### 3.2 Numbering restarts are not detected

**Document:** lead-bank. **Method:** `clause_aware`.

The document's own clauses are numbered `2.`, `3.`, `4.`, `5.` and its table rows
are numbered `1.`, `2.`, `3.`. The segmenter emits `clause_number` `"1"`, `"2"`,
`"3"` **twice**, at the same nesting level, with no indication that the second
run is a restart. Clause `2` and table row `2` are indistinguishable in the
output by number alone.

Segment ids remain unique (they are derived from the ordinal, not the number),
so nothing is lost or overwritten — but a consumer resolving "clause 2" by
number would find two candidates. This is the strongest argument for citations
carrying `clause_id` rather than `clause_number`, which `CLAUDE.md` §12.2
already specifies.

### 3.3 "Schedule" matched inside "Scheduled" — found and fixed

**Document:** master-circular, page 3. **Method:** `clause_aware`.
**Status:** fixed in this milestone; the before/after is recorded because the
fix changed the metric.

`_ANNEXURE_RE` had no boundary check after its alternation, so `Schedule`
matched the first eight characters of `Scheduled`. The master-circular's wrapped
lines produced two bogus annexure segments:

```
predicted [13] "Scheduled Castes Finance & Development Corporation..."  clause_number="Schedule"
predicted [15] "Scheduled Tribes for the specific purpose of purchase..."  clause_number="Schedule"
```

A `\b` word boundary would not have helped: the boundary between `e` and `d` is
not a word boundary. The fix is an explicit `(?![A-Za-z])`.

Effect on the metric, master-circular `clause_aware`:

| | before | after |
|---|---:|---:|
| predicted segments | 15 | 13 |
| boundary precision | 0.867 | **1.000** |
| boundary F1 | 0.897 | **0.963** |
| exact-match rate | 0.786 | **0.929** |
| incorrect splits | 2 | **0** |

Two false clause starts cost 10 points of F1 on a 14-clause sample. It is also a
reminder that on a sample this size one bug is worth a large fraction of the
headline number.

### 3.4 An unnumbered opening recital is merged into the preamble

**Document:** lead-bank, page 1, `clause_index` 1. **Both methods.**
**Effect:** 2 merges; both methods miss this boundary.

Annotation 1 is the paragraph beginning *"The Government of National Capital
Territory (NCT) of Delhi has notified re-organisation…"* — substantive text a
reader could cite, so the guideline (§4.7) makes it its own clause. Both methods
absorbed it into the preceding preamble segment, because the extracted text has
no blank line between the subject line and the recital, and the recital carries
no clause marker.

Neither method can do better without a cue. A heading-detection rule that fired
on the subject line would create the boundary, but the subject line is
mixed-case prose and would need a much looser heading heuristic than §`_is_unnumbered_heading` currently uses — which would misfire elsewhere.

### 3.5 Closing matter is merged into the last clause

**Document:** lead-bank, page 2, `clause_index` 6. **Both methods.**

*"Yours faithfully, (Nisha Nambiar) Chief General Manager-in-Charge"* was
absorbed into clause `5`. Same cause as §3.4: no blank line, no marker.

Low impact — the signature block is not text anyone will cite — but it means
clause 5's text ends with an officer's name, which would appear in a citation
quote for clause 5.

### 3.6 The guideline and the implementation disagree about trailing punctuation

**Document:** master-circular, page 3, `clause_index` 13. **Method:**
`clause_aware`. **Effect:** numbering preservation 12/13 rather than 13/13.

The document prints this clause as `2.6.` with a trailing stop, unlike its
siblings `2.1`–`2.5`. The guideline (§5.3) says to record the number exactly as
printed, so the annotation says `"2.6."`. The segmenter strips the trailing stop
and reports `"2.6"`.

**The segmenter is arguably right** — `2.6` is the clause's number and the stop
is punctuation — and the single "failure" in the numbering column is a
guideline/implementation disagreement rather than a defect. Recorded rather than
quietly reconciled: resolving it is a guideline amendment (and a version bump),
which is the process `data/annotations/README.md` §8 step 4 describes.

### 3.7 The paragraph baseline splits a heading from its body

**Document:** master-circular, page 2, `clause_index` 0. **Method:**
`paragraph`. **Effect:** 1 incorrect split; 1 spurious segment.

The page-2 document title and its following body paragraph are one clause under
the guideline (§4.6). The paragraph method emits two segments, because a blank
line separates them. Structural, not fixable within the baseline — and precisely
the kind of error clause-aware segmentation exists to avoid.

### 3.8 The clause-aware method misses the page-2 title heading

**Document:** master-circular, page 2, `clause_index` 0. **Method:**
`clause_aware`. **Effect:** recall 0.929 rather than 1.000.

The title heading on page 2 was absorbed into the preamble segment that begins
on **page 1** and runs across the page break. That segment exists and contains
the text — but it starts outside the annotated window, so the scorer counts the
clause as missed.

**This is partly a limitation of the scoring method, and it is stated as one.**
`segments_for_version` keeps only segments whose `page_start` is an annotated
page, which is necessary to stop the other 34 pages of a document counting as
false positives (§6.2). The side effect is that a prediction beginning before
the window is invisible to the matcher, so a genuine merge across the window
boundary is scored as a miss. The merge counter does not catch it either, since
that segment is excluded. A sample whose window starts at page 1 would not have
this problem.

### 3.9 Page-1-only letterheads survive into the preamble segment

**Document:** master-circular and commercial-banks. **Both methods.**

`master-circular`'s bilingual letterhead appears on page 1 only, so no frequency
rule can see it (`page_cleaning` module docstring). It therefore lands inside
the preamble segment, mangled Devanagari and all:

```
segment [0] "भारतीय <U+FFFD>रज़वर् बैंक _____ RESERVE BANK OF INDIA _____ www.rbi.org.in RBI/2026-27/272 …"
```

Not scored here — page 1 is outside both annotation windows — but it is the
largest remaining cleaning gap and it is visible in the committed segment files.
A reader of the preview frontend will see it immediately, which is one reason
the frontend shows the cleaning plan.

---

## 4. Uncertain records, and the sensitivity check

Three of the 21 records are `uncertain: true`, all on the lead-bank circular,
all for the same reason: a clause annotated together with the table it
introduces, where the table's row structure is lost in extraction (guideline
§4.5).

Metrics with those records **included**:

| Document | Method | ann | pred | P | R | F1 | F1 in §1 |
|---|---|---:|---:|---:|---:|---:|---:|
| master-circular | `paragraph` | 14 | 15 | 0.933 | 1.000 | 0.966 | 0.966 |
| master-circular | `clause_aware` | 14 | 13 | 1.000 | 0.929 | 0.963 | 0.963 |
| lead-bank | `paragraph` | 7 | 4 | **1.000** | 0.571 | **0.727** | 0.500 |
| lead-bank | `clause_aware` | 7 | 11 | 0.455 | 0.714 | **0.556** | 0.267 |

Master-circular is identical, as it must be — it has no uncertain records.

On lead-bank, including the three uncertain records **raises** both methods' F1
(0.500 → 0.727 and 0.267 → 0.556), because those three table clauses are
boundaries both methods *do* find: the clause-introducing sentence starts at the
line the segmenter breaks at. Excluding them removes three easy hits while
leaving all the spurious table-row predictions in the denominator.

**So the §1 headline figures are the more conservative of the two**, and the
clause-aware method's apparent weakness on this document is not an artefact of
hiding the hard cases — it is worse when the hard cases are included too.

---

## 5. What to fix next, in order of value

### 5.1 Table-aware segmentation (largest win)
§3.1 and §3.2 are one problem: a flattened table is indistinguishable from a
numbered list. Experiment A §5.2 showed PyMuPDF's `find_tables()` recovers the
row structure on exactly these pages, and PyMuPDF is already a pinned
dependency. The proposal: record table regions per page at extraction time, and
have `segment_clauses` treat a table region as one segment belonging to the
clause that introduces it — which is also what the annotation guideline §4.5
already says a human should do. **This would close the only case where the
clause-aware method is clearly worse than the baseline.**

### 5.2 Numbering-restart detection
A clause number that goes backwards at the same nesting level is a restart.
Detecting it would let §3.2's duplicate numbers be flagged in `metadata`, and
would also help §5.1 by making a run of restarting numbers a table signal.

### 5.3 Page-1 letterhead removal
§3.9. Frequency cannot see a one-page letterhead. A positional rule confined to
page 1 is possible but dangerous — page 1 is where the title, reference number
and date live. A safer approach: match page 1's leading lines against the
letterhead block already detected on other documents from the same authority,
once the corpus has more than one document per authority.

### 5.4 Grow the annotation sample, with a second annotator
The single highest-value change to the *evaluation* rather than the system. 21
clauses on 4 pages, single-annotated by a non-independent annotator, is enough
to demonstrate the method and to find the bugs in §3 — it is not enough to
support a confident comparison. Priorities: a second annotator on the existing
4 pages (to replace `PENDING` with a real agreement figure), then pages from
`capital-adequacy`, which is the only document exercising `(a)`, `(i)` and
`(12)` numbering and the deep nesting those produce.

---

## 6. Threats to validity

1. **The annotator is not independent** (§0.1). This is the dominant threat.
2. **No agreement figure** (§0.2), so annotation reliability is unmeasured.
3. **The window restriction distorts recall at window edges** (§3.8). Scoring
   only segments that *start* on an annotated page is necessary to keep
   precision meaningful, and it makes a cross-window merge look like a miss.
4. **Two documents, one issuing authority.** Both are RBI circulars produced by
   the same Word-to-PDF toolchain. `capital-adequacy` and `commercial-banks`,
   with their bracketed and deeply nested numbering, are **not** in the scored
   sample at all — so the `(a)` / `(i)` / `(12)` detection paths and the
   4-level hierarchy are exercised by unit tests and by the real-document run,
   but are **not** scored against annotations.
5. **A sample this small is bug-dominated.** §3.3 shows one regex defect moving
   F1 by 0.066. With 18 confident clauses, one clause is worth ~5 points of
   recall; no difference smaller than that should be read as meaningful.
6. **The uncertain-record exclusion is conservative here but need not be.** On
   this sample excluding them lowers the scores (§4). On another sample it could
   raise them, so both variants stay in the output.
7. **Clause-number preservation is measured only where a number exists.** The
   baseline's 0.000 is therefore a real zero over 13 opportunities, not an
   artefact of a missing denominator — `_ratio` returns `None` for that case and
   it is not what happened.

---

## 7. Relationship to the unit tests — three different kinds of evidence

Kept separate on purpose, because conflating them is how a project claims more
than it has measured.

| Kind of evidence | What it shows | Where it lives |
|---|---|---|
| **Unit tests** (343 passing) | The code does what it was written to do: provenance invariants hold, each numbering style is detected, cleaning is reversible, output is deterministic. Uses synthetic fixtures. | `backend/tests/` |
| **Automatic evaluation against annotations** (this report, §1–§4) | How closely each method's boundaries match human judgement on 4 real pages. 21 annotated clauses. | `evaluation/experiment-b/results.json` |
| **Manual qualitative inspection** (§3) | Why the methods fail where they fail, read off the real output against the source PDFs. Nine cases, no metric. | §3 of this report |

A passing unit test says nothing about segmentation quality. §1's F1 says
nothing about whether a failure is fixable. §3 is where the engineering
judgement is, and it is the part a viva should be most interested in.

---

## 8. Reproducing this

```bash
# 1. Extract (needs the four PDFs in data/raw/; see data/README.md §9)
python -m backend.app.document_processing.cli \
    --manifest data/manifest.csv --verify-checksums

# 2. Segment both methods
python -m backend.app.document_processing.segmentation_cli \
    --all --check-deterministic

# 3. Check the annotations still resolve against the current extraction
python -m evaluation.experiment_b_segmentation validate --against-template

# 4. Score
python -m evaluation.experiment_b_segmentation score

# Optional: regenerate the annotation files from the recorded boundary
# decisions (does not re-decide anything; see the script's docstring)
python data/annotations/build_annotations.py
```

Step 3 is not optional. If extraction or normalisation changes, markers may stop
resolving, and the scorer raises rather than silently dropping records — which
would inflate both precision and recall.
