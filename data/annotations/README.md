# Annotation guidelines — segmentation boundaries

> Written 2026-11-12, **before** any annotation was produced, per
> `data/README.md` §7 ("write the guideline first").
> Guideline version: **1.0**. Owner: Person 1 (Backend + Document NLP).
>
> Every annotation file records the guideline version it was produced under. If
> this document changes after annotation begins, the version is bumped and the
> report says which records were produced under which version.

---

## 0. What this guideline is for

Experiment B (`CLAUDE.md` §15) compares two segmentation methods — paragraph
and clause-aware — against human judgement about where clauses actually begin
and end. That comparison is only meaningful if "where a clause begins" means
the same thing to every annotator on every document.

This file is that definition. It is deliberately specific about the hard cases,
because the easy ones were never the problem.

**The hard rule from `data/README.md` §7, restated:** no evaluation number is
written down before the annotation file and the scoring script both exist and
have been run. An estimated metric is academic dishonesty and is also useless.

---

## 1. Current status of the annotation set

| | |
|---|---|
| Guideline version | 1.0 |
| Documents annotated | 2 of 4 in the validation sample |
| Pages annotated | 4 |
| Annotator | **one** (`person1-claude-code`, see §7) |
| Inter-annotator agreement | **PENDING — no second annotator has worked on this sample** |
| Review status | `awaiting_owner_review` on every record |

**Read §7 before quoting any number produced from these files.** The sample is
small and single-annotated on purpose: a small honest sample beats a large
careless one (`backend/docs/backend-roadmap.md`, Phase 2 risks), and the guideline
required that agreement be reported rather than assumed.

---

## 2. The unit being annotated

We annotate **clauses**, not paragraphs. The paragraph method is scored against
the same clause annotations, which is the point: the question Experiment B asks
is "how well does each method recover the document's actual clause structure",
and paragraph splitting is the control condition for that question, not a
separate target.

### 2.1 What counts as a clause

A clause is **the smallest span of text that carries one complete, separately
citable obligation, definition, condition or statement**, together with the
number or heading under which the document itself presents it.

In practice, a clause is what you would cite if you had to point a reader at
exactly the provision you mean. If a reader would need the surrounding text to
make sense of your citation, you have cut too small; if your citation would
force them to read past the part you meant, you have cut too large.

**Positive indicators** — annotate a boundary where the document starts a new:

- numbered item: `2.`, `3.2`, `3.2.1`
- bracketed item: `(a)`, `(iv)`, `(12)`
- spelled-out reference used as a heading: `Clause 5`, `Section 7`,
  `Paragraph 29`
- structural division: `CHAPTER IV`, `PART A`
- annexure, schedule or appendix: `Annexure`, `Schedule II`, `Appendix`
- unnumbered heading followed by its own body text

### 2.2 What counts as a paragraph

Recorded here because the term is used loosely elsewhere. A **paragraph** is a
run of text separated from its neighbours by a blank line, within one page. It
is a *formatting* unit and we do not annotate it — the paragraph method's
output is generated, not annotated, and is compared against the clause
annotations.

A single clause often contains several paragraphs. That is the main way the two
methods differ, and §4.3 says how to annotate it.

### 2.3 What is **not** a clause

Do not annotate a boundary for:

- a running header, footer, letterhead or printed page number (these are removed
  before segmentation; if one survives into the text, see §5.4)
- a line break inside a sentence
- a table row, unless the document numbers it as a clause (see §4.5)
- a signature block, an address block or an anti-phishing notice
- a cross-reference inside a sentence (`"conferred by Section 35A"` is a
  reference, not the start of Section 35A)

---

## 3. The record format

One JSON object per line, in `data/annotations/segmentation/<version_id>.jsonl`.

```json
{
  "version_id": "VER-b8a13c2a7651eb84",
  "guideline_version": "1.0",
  "annotator": "person1-claude-code",
  "review_status": "awaiting_owner_review",
  "clause_index": 7,
  "page_start": 2,
  "page_end": 2,
  "clause_number": "1.1",
  "heading": null,
  "text_start_marker": "1.1 The District Level Consultative Committees",
  "text_end_marker": "development agencies.",
  "uncertain": false,
  "note": null
}
```

### 3.1 Field rules

| Field | Rule |
|---|---|
| `version_id` | The version this clause belongs to, exactly as the pipeline assigns it. |
| `guideline_version` | The version of **this file** the record was made under. |
| `annotator` | A stable identifier, not a display name. See §7. |
| `review_status` | `awaiting_owner_review` \| `reviewed` \| `disputed`. |
| `clause_index` | 0-based, in document order, within this version. Dense. |
| `page_start`, `page_end` | 1-based **physical** page numbers, inclusive at both ends. Equal when the clause sits on one page. |
| `clause_number` | Exactly as printed, with no reformatting: `3.2`, `(iv)`, `Clause 5`. `null` when the clause carries no number. |
| `heading` | The heading governing this clause, verbatim, or `null`. |
| `text_start_marker` | A short verbatim snippet from the **start** of the clause. |
| `text_end_marker` | A short verbatim snippet from the **end** of the clause. |
| `uncertain` | `true` when §6 applies. |
| `note` | Free text. **Required** when `uncertain` is `true`. |

### 3.2 Why markers and not character offsets

A character offset is invalidated by any change to extraction or normalisation.
A short verbatim snippet survives, because it can be located again in the new
text. Since Experiment A may yet change the extractor
(`evaluation/experiment-a-extractor-comparison.md` §7), annotations anchored on
offsets would have to be redone; anchored on markers they do not.

**Marker rules:**

- Copy it **verbatim** from the extracted text, including any extraction
  artefact. If the text reads `L ead Banks`, the marker is `L ead Banks`. A
  marker "corrected" by the annotator will not be found.
- Long enough to be unique on its page: aim for 30–60 characters.
- If a marker is not unique on its page, lengthen it until it is. The scoring
  script reports an ambiguous marker as an error rather than guessing.
- The start marker **must include the clause number** when there is one, since
  that is the first text of the clause.

---

## 4. Boundary decisions

### 4.1 A clause ends where the next one begins

A clause's span runs from its own first character to the character immediately
before the next clause's first character. There is no gap and no overlap. The
`text_end_marker` records the last few words of that span.

Consequence: **every character of the document body belongs to exactly one
clause.** If text falls between two clauses and belongs to neither, that is an
unnumbered clause and gets its own record with `clause_number: null` — not a
hole in the annotation.

### 4.2 Nested clauses are annotated as separate clauses

`2.1` and its sub-items `(a)`, `(b)`, `(c)` are **four** records, not one.
`2.1`'s span ends where `(a)` begins, so `2.1` covers only its own introductory
text ("The following shall apply:").

This is deliberate and it is the decision most likely to be argued with, so the
reasoning is recorded: Person 3's retrieval and Person 4's alignment both need
the smallest citable unit. A reader asking "what does (b) require" should get
(b), not the whole of 2.1. The parent/child relationship is not lost — the
segmenter records it in `parent_segment_id`, and the annotation's
`clause_number` makes it recoverable.

### 4.3 A clause containing several paragraphs is **one** clause

If clause `3.1` runs to three paragraphs with blank lines between them, that is
one record spanning all three. Its `text_end_marker` comes from the end of the
third paragraph.

This is where the two methods diverge most, and scoring it correctly is the
whole point of the experiment. Do not split such a clause just because the
paragraph method would.

### 4.4 A clause crossing a page break is **one** clause

Set `page_start` to the page where it begins and `page_end` to the page where it
ends. Never split a clause at a page boundary and never record only one of the
two pages — the project's provenance rule (`data/README.md` §6) depends on both
being present.

### 4.5 Tables

Default: **a table is one clause**, belonging to whichever clause introduces it.
Its `text_start_marker` is the start of that introducing clause.

Exception: if the document numbers the table's rows as clauses in their own
right, annotate them as clauses.

Reason: pypdf flattens multi-column tables into a vertical stack of one- and
two-word lines (Experiment A §5.2), so the extracted text of a table has no
recoverable row structure. Annotating rows that the text cannot express would
measure the extractor, not the segmenter. Where a table is annotated as one
clause, set `uncertain: true` and note that the row structure was lost.

**Known trap, seen in the real corpus:** the lead-bank circular contains a
`Sr. No` table whose rows begin `1.`, `2.`, `3.`. Those numbers are table row
labels, not clause numbers, and the document's own clauses `1.`, `2.`, `3.`
also exist. Annotate the table as part of its introducing clause and record the
collision in `note`.

### 4.6 Headings

- A heading **with its own body text** → one record, `clause_number: null`,
  `heading` set to the heading text, span covering heading plus body.
- A heading **immediately followed by a numbered clause** → no separate record.
  Attach it as the `heading` of that clause.
- A document title or reference number on page 1 → not a heading. It is part of
  the preamble (§4.7).

### 4.7 Preamble

The material before the first clause — reference number, date, addressee,
salutation, subject line — is **one** record with `clause_number: null`,
`heading: null`, and `note: "preamble"`. It is annotated rather than skipped, so
that §4.1's "every character belongs to a clause" holds and the scorer is not
penalised for a boundary at the first real clause.

A document often then has an **unnumbered opening recital** — the paragraph that
states the background before clause 2 begins. That is a separate record, also
with `clause_number: null`. It is not part of the preamble: it is substantive
text that a reader could cite.

### 4.8 Closing matter

Symmetric with §4.7, and added while annotating the first document, where §2.3
("a signature block is not a clause") and §4.1 ("every character belongs to
exactly one clause") turned out to contradict each other.

The resolution: a signature block, "Yours faithfully", an officer's name and
designation, or an enclosure list at the end of a document is **one** record
with `clause_number: null`, `heading: null` and `note: "closing matter"`.

Both rules are then satisfied. §2.3 means a signature block never *interrupts* a
clause — it does not create a boundary in the middle of the document. §4.1 means
the trailing matter is still covered. Recording it also stops the scorer
counting the segmenter's closing segment as a false positive, which would
penalise it for behaving correctly.

---

## 5. Specific situations

### 5.1 Numbering that restarts
Annotate what is printed. If `1.` appears twice in a document, both records have
`clause_number: "1"`. Disambiguation is `clause_index`'s job, not the
annotator's.

### 5.2 A bracketed marker that is both a roman numeral and a letter
`(i)`, `(v)`, `(x)`, `(c)`, `(d)`, `(l)`, `(m)` are ambiguous. Record the
marker **exactly as printed** — `(i)` — and do not resolve it. If the
surrounding sequence makes it clear (`(h)` then `(i)` is alphabetic), say so in
`note`. The segmenter has the same problem and flags it the same way, so the
scorer can compare like with like.

### 5.3 An extraction artefact inside a clause number
If the number extracts as `3 .2` or `3.2 .`, the marker is copied verbatim with
the artefact. Add a `note`. Do not silently repair it — the artefact is a real
property of the data and Experiment B should see it.

### 5.4 A surviving header or footer
If a running header was not removed and appears inside the extracted text, do
**not** include it in any clause's span. Start the following clause's marker
after it, and add a `note` naming the leaked line. This keeps the cleaning
stage's failures attributable to cleaning rather than to segmentation.

### 5.5 An empty or image-only page
Produce no record for it. Note it in the file's companion
`<version_id>.meta.json` so the scorer knows the page was reviewed and found
empty, rather than skipped by accident.

---

## 6. Uncertainty — what to do when you genuinely cannot decide

Set `uncertain: true` and write a `note` saying what the two readings are and
which you chose.

Do this, rather than guessing silently, whenever:

- the text could be read as one clause or two with equal justification
- the extracted text is too mangled to locate the boundary confidently
- a table's structure is lost and the clause boundary depends on it
- a marker is ambiguous in a way §5.2 does not settle

**Uncertain records are reported separately and are excluded from the headline
metric**, then reported again as a sensitivity check with them included. Both
numbers go in the report. Hiding a hard case inside an aggregate is how an
evaluation becomes misleading.

Do **not** set `uncertain: true` merely because a decision was slightly
difficult. It marks cases where the guideline genuinely does not determine the
answer — which is also the signal that the guideline needs amending.

---

## 7. Who annotated this, and why that limits the result

**This matters more than any number in the report, so it is stated plainly.**

The annotations currently in `data/annotations/segmentation/` were produced by
**Claude Code working as Person 1's engineering assistant**, at the project
owner's direction, by reading the extracted text of the documents against this
guideline. They are recorded with `annotator: "person1-claude-code"` and
`review_status: "awaiting_owner_review"` on every record.

What that means for the evaluation:

1. **They are not independent of the system being evaluated.** The same
   assistant wrote the segmenter and the annotations. Even working from the
   guideline rather than from the code, that is a conflict of interest, and the
   resulting metrics must be read as a **provisional self-check, not as
   independent ground truth.**
2. **Inter-annotator agreement is `PENDING`, not low.** No second annotator has
   worked on this sample, so no agreement figure exists and none may be quoted.
   §8 is the procedure for producing one.
3. **Every record needs owner review.** Until `review_status` becomes
   `reviewed`, the numbers in `evaluation/experiment-b-segmentation.md` are
   labelled provisional throughout.

The honest alternative — reporting no metric at all — was rejected because the
scoring script has to be demonstrated working on real records, and a committed
annotation set is what makes the number reproducible and *checkable*. The
mitigation is that the sample is small enough for the owner to review by hand in
an hour, and that the limitation is stated here, in the report, and in
`CLAUDE.md`.

---

## 8. Procedure for the agreement subset (not yet done)

1. A second annotator — a teammate, ideally not Person 1 — reads this guideline
   **without** looking at the existing annotations or at the segmenter's output.
2. They annotate the same 6 pages into
   `data/annotations/segmentation/<version_id>.annotator2.jsonl`.
3. Agreement is computed by the scoring script with
   `--compare-annotators`, reporting boundary-level agreement and the count of
   clauses where the two differ.
4. **Every disagreement is recorded, not resolved by one annotator's
   preference.** The guideline is amended to cover the case, its version is
   bumped, and the affected records are re-annotated under the new version.
5. The agreement figure and the disagreement list both go in the report.

Until step 3 has run, the report says `PENDING`.

---

## 9. Producing and checking annotation files

```bash
# Generate a pre-filled template from the segmenter's own output, to be
# corrected by hand. The template is a STARTING POINT, not an annotation:
# accepting it unedited would measure the segmenter against itself.
python -m evaluation.experiment_b_segmentation template \
    --version-id VER-b8a13c2a7651eb84 --pages 1-3

# Validate format, marker uniqueness and clause_index density
python -m evaluation.experiment_b_segmentation validate

# Score both methods against the annotations
python -m evaluation.experiment_b_segmentation score
```

`validate` must pass before `score` is run. It checks that every marker is
findable and unique on its stated page, that `clause_index` is dense and
ordered, that `page_end >= page_start`, and that every `uncertain` record has a
`note`. A marker that cannot be located is an **error**, never a silent skip.

### 9.1 The template trap

The template generator exists because typing 60-character markers by hand is
error-prone, not because the segmenter's boundaries are correct. An annotation
file that is byte-identical to its template measures nothing. `validate`
therefore reports how many records differ from the template, and the report
quotes that number.

---

## 10. Files in this directory

```text
data/annotations/
├── README.md                               # this guideline
└── segmentation/
    ├── <version_id>.jsonl                  # one record per clause
    └── <version_id>.meta.json              # pages covered, annotator, status
```

Annotations **are** committed to git (`data/README.md` §1): they are small, and
they are the only part of the evaluation that cannot be regenerated.

Not present yet, and owned by other team members:
`entities/` (Person 4), `alignment/` (Person 4), `queries/` (Persons 3 and 5).
