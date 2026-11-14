# Entity annotations — the guideline, and what these numbers are worth

**Written before the annotations were made**, which is the project's rule
(`CLAUDE.md` §14 rule 1, §15 Experiment B). Read §0 before quoting any number
from `evaluation/entity-extraction/`.

---

## 0. Read this first — the sample and the annotator

| | |
|---|---|
| Corpus annotated | the **genuine RBI version-pair corpus**, `data/version_pairs/` — 3 master circulars × 3 annual issues, 288 pages, 3,621 clause-aware segments |
| Sample | **30 clauses**, drawn with `random.Random(20261114)` from the 747 clause-aware segments of 80–700 characters across all 9 issues |
| Annotation unit | the **character span**, inside one clause's `text` |
| Gold mentions | **88** — DATE 13, PERIOD 0, MONEY 0, PERCENT 0, OBLIGATION 10, AUTHORITY 50, REFERENCE_NO 12, SECTION_REF 3 |
| Annotator | **one**, and **not independent of the system** — the same person who wrote the extractor |
| Inter-annotator agreement | **not measured.** There is no second annotator |
| Therefore | these are **diagnostic** numbers that locate specific extractor failures. They are **not** a validated accuracy measurement, and they must never be quoted without this paragraph |

Same honesty constraint as `data/annotations/search_relevance/`, for the same
reason: a person who knows which patterns the extractor contains cannot
annotate blind, and a number produced that way partly measures agreement with
the author's own patterns.

**Two things were done to limit that bias, and they are worth stating because
they changed the result:**

1. **The labels were defined from what the product needs, not from what the
   regex can do.** `AUTHORITY` in particular is defined below as *any named
   body*, which includes the forty-odd individual bank names that appear in
   the Lead Bank Scheme's district-allocation annexures. A closed list of
   patterns cannot find those, and the measured recall says so. Narrowing the
   label to "bodies the extractor knows about" would have produced a much
   better number and measured nothing.
2. **The sample was drawn at random and kept**, including the clauses that are
   table fragments with no entities at all. Hand-picking prose clauses would
   have flattered both precision and recall, because the hard cases in these
   documents are the tabular circular indexes.

**A second annotator would turn this from a diagnostic into a measurement.**
`gold.json` records the sampling seed and the full clause text, so a second
annotator can redo it without re-deriving the sample.

---

## 1. The labels

Eight labels. The extractor publishes the same list at
`GET /api/compare/entities/labels`; this file is the authority on what each
one *means for annotation*.

### DATE — a printed calendar date

Annotate the date exactly as printed, in any format these documents use:

- `October 22, 1981`, `August 25, 2016`, `July 01, 2016`
- `02.03.2010`, `11.08.1988`, `18.06.1980` (RBI's circular indexes use dots)
- `02.11.87`, `14.08.14`, `27.09.88` (two-digit years, also used in the indexes)
- `October 1969` (a month and year with no day is still a date)

**Not a DATE:**
- a year on its own (`1969`), which is too ambiguous to be useful as a deadline
- the financial year inside a reference number (`.../2009-2010`) — that is part
  of the `REFERENCE_NO` span and is annotated as such
- `quarter ended............`, a blank to be filled in

### PERIOD — a duration with an explicit quantity

`30 days`, `three months`, `5 years`, `two fortnights`.

**Not a PERIOD:** `quarterly`, `periodically`, `regularly`, `annually`, `on a
quarterly basis`. These have no measurable duration in the text, and a system
that reported "the period changed from quarterly to quarterly" would be
reporting noise. This is a deliberate narrowing and it is why the sample
contains zero PERIOD mentions.

### MONEY — a rupee or lakh/crore amount

`Rs. 25,000 crore`, `₹ 1,00,000`, `10 lakh`. A bare count is not money:
`population of over 2000` is not a MONEY mention.

### PERCENT — a percentage

`7.5 per cent`, `40 percent`, `12%`, `2 percentage points`.

### OBLIGATION — modal language expressing obligation or prohibition

`shall`, `shall not`, `must`, `must not`, `should`, `should not`,
`is/are required to`, `will have to`, `may not`, `is prohibited`.

**Not an OBLIGATION:** a bare permissive `may` (`the Lead Banks may
collaborate with…`) — it grants a liberty rather than imposing a duty.

This label is explicitly a **lexical cue and not a verified obligation**:
`banks shall not be required to report` is annotated as an OBLIGATION mention
(`shall not`) even though it releases banks from one. The label records that
obligation language is present; reading the clause remains the user's job.

### AUTHORITY — a named body

Any organisation, committee, office or institution **that the span itself
identifies**. This includes:

- regulators: `Reserve Bank of India`, `RBI`, `NABARD`, `IRDAI`, `SEBI`
- governments: `Government of India`, `State Government`
- offices: `Prime Minister`, `Parliament`
- committees: `District Consultative Committees`, `High Level Committee to
  Review Lead Bank Scheme`, `Gadgil Study Group`, `Committee of Executive
  Directors`
- institutions, **including individual banks**: `State Bank of India`,
  `UCO Bank`, `United Bank of India`, `Andhra Bank`, `Lead Banks`,
  `Regional Rural Banks`, `RRBs`
- named agencies: `Krishi Vigyan Kendra`, `National Skill Development
  Corporation`, `Agriculture Skill Council of India`

An abbreviation is **its own mention**: `District Level Review Committee
(DLRC)` is two mentions, `District Level Review Committee` and `DLRC`. The
reason is practical — a later clause will use only `DLRC`, and a system that
cannot recognise the abbreviation on its own has not found the authority.

**Not an AUTHORITY:**
- a generic noun phrase that names no one: bare `the Government`,
  `Government Agencies`, `development agencies`, `banks`, `the Bank`
- a **scheme or programme**: `Lead Bank Scheme`, `Swarna Jayanti Shahari
  Rozgar Yojana`, `National Urban Livelihoods Mission`, `Horticulture
  Mission`. A scheme is a thing that is administered, not a body that
  administers. (The single exception in this sample's wording is where a
  mission is plainly the organisation itself, e.g. `Horticulture Mission`
  appearing in a list of partner organisations — annotated as AUTHORITY
  there, and the clause note records the call.)
- a generic back-reference to a body named earlier: `the Study Group` after
  `Gadgil Study Group` has already been annotated

### REFERENCE_NO — an official circular or notification file number

`RBI/2017-18/7`, `FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17`,
`DBOD.No.BP.BC.132/C.594-81`.

The span is **the reference as it appears in the extracted text, including the
stray spaces extraction introduced**. `pypdf` reads
`RPCD.CO. LBS.BC.57/ 02.19.10/2009-2010` with two spaces inside what is one
printed reference number, and `RPCD.No.LBS.BC.12/65/8 8-89` with a space
inside `88-89`. Annotating the clean form instead would be annotating a
document we do not have, and would hide a real extraction defect behind a
scoring convention.

### SECTION_REF — a cross-reference to part of a document

`Annexure I`, `Annexure III`, `paragraph 5.3`, `Section 12`, `Schedule II`,
`Chapter 4`.

---

## 2. Rules applied throughout

1. **Spans do not overlap.** Where two labels could claim the same characters,
   the more specific one wins: the financial year inside a reference number
   belongs to `REFERENCE_NO`, not `DATE`.
2. **Annotate every occurrence**, not every distinct string. Seven bank names
   in a district table are seven mentions.
3. **Annotate the text that is there**, including extraction artefacts
   (`National Skill Development C orporation`, `Government A gencies`). The
   benchmark measures performance on the text the pipeline actually produces.
4. **A clause with no entities is a data point.** Four of the thirty clauses
   are table fragments with zero gold mentions, and they are kept: they are
   where false positives would show up.
5. **When a call is genuinely arguable, it is recorded** in the clause's
   `note` field in `gold.json` rather than silently decided.

---

## 3. Files

| File | What it is |
|---|---|
| `gold.json` | The 30 annotated clauses: full text, the sampling seed, and every gold span as `{label, text}` in document order |
| `README.md` | This guideline |

`gold.json` stores each mention as a **label and a surface string**, not a
character offset. The scorer resolves each string to its first not-yet-claimed
occurrence in the clause text, in the order the mentions are listed. This was
chosen so the annotations stay readable and reviewable by a human — an offset
pair is unreadable, and an unreadable annotation is one nobody checks — and
`evaluation/entity_extraction_eval.py --validate` fails loudly if any gold
string cannot be located, so the two cannot drift.

---

## 4. How to score it

```
.venv\Scripts\python -m evaluation.entity_extraction_eval --validate
.venv\Scripts\python -m evaluation.entity_extraction_eval
```

The scorer reports, per label and overall: precision, recall, F1 under **exact
span match** and under **relaxed (overlapping span, same label)** match, plus
the false positives and false negatives in full, plus inference time. It
writes `evaluation/entity-extraction/results.json` and a human-readable
summary.

Both match modes are reported because they answer different questions. Exact
match is what a span-level NER evaluation normally means. Relaxed match is
what matters for the product: a highlight that covers
`RPCD.CO. LBS.BC.57/ 02.19.10` when the gold span runs two characters further
is useful to a reader, while a miss is not.
