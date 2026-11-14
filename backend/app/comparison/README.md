# `backend/app/comparison/` — Compare: clause alignment, change classification, evidence

The Compare capability of `CLAUDE.md` §5.4, integrated into the existing
backend rather than built beside it. It reads the `documents`,
`document_versions`, `segments` and `segment_pages` tables the ingestion
pipeline already populates, through the engine `backend.app.db` already pools,
and serves its endpoints from the FastAPI application that already exists.

**Status: `[IMPLEMENTED]`** — 4 endpoints, 316 tests, measured on 12 genuine
RBI version pairs. No new dependency, no new database table, no migration.

Full report, including the audit of the teammate's prototype and every
measured number: `backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md`.

---

## 1. Layout

```
similarity.py   formatting-insensitive comparison key + TF-IDF cosine
alignment.py    the four-stage matcher and change classification
entities.py     the rule-based entity extractor (the regex baseline)
explain.py      word-level diff + plain-language description
repository.py   read-only SQL over the existing tables
service.py      validation, direction, evidence resolution, filtering
schemas.py      Pydantic request/response models
api.py          the router mounted by api/main.py
```

Two import rules hold inside the package, and tests pin both:

* **`alignment` must not import `entities` or `explain`.** Entity extraction
  and wording must never be able to change which clauses are reported as
  modified. The brief requires extraction to stay separable; this is how.
* **Nothing here writes.** `CLAUDE.md` §9 forbids anything downstream of
  extraction from modifying document data, and a test greps the whole package
  for write statements.

---

## 2. What came from the teammate's prototype, and where it went

The prototype is preserved **unmodified** at `comparision.py` in the
repository root. It is a 252-line Streamlit app: upload 2–4 PDFs, extract,
split into clauses, TF-IDF similarity, greedy alignment, explain, download a
CSV.

| Prototype | Where it is now | What changed |
|---|---|---|
| `similarity_matrix` — `TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True, stop_words="english")` + cosine | `similarity.py` | Same configuration, reimplemented in the standard library. See §3 |
| `align_clauses` — greedy best-first above a threshold | `alignment.py` **stage 3** | Kept as the last stage, after two stronger ones |
| `extract_facts` — 4 regex groups returning bare strings | `entities.py` | 8 labels, character spans, precedence-based overlap resolution |
| `explain_change` — date/period/amount comparison, similarity fallback | `explain.py` | Extended with percentages, authorities, cross-references, word counts, and a word-level diff |
| `clean_text` | **dropped** — `document_processing/normalization.py` already does this, with seven independently toggleable steps |
| `extract_pdf` | **dropped** — `document_processing/pdf_extractor.py` already does this, with validation, checksums and per-page provenance |
| `split_clauses` | **dropped** — `document_processing/segmentation.py` already does this, with clause numbers, headings, hierarchy and `segment_pages` rows |
| Streamlit UI | **not carried over** — replaced by 4 API endpoints and `compare_preview/` |

The three dropped functions are the audit's main finding: the prototype
contained a second, simpler copy of the ingestion pipeline, because it had to
— it was a standalone app with no backend to read from. Integration means
deleting that copy, not merging it.

---

## 3. Why TF-IDF is reimplemented rather than imported

`scikit-learn` **is not installed in this project's virtualenv**, so
`import comparision` fails with `ModuleNotFoundError` on this machine. The
prototype has never run here.

Pinning scikit-learn would add it plus scipy, joblib and threadpoolctl — of
the order of 100 MB — to a backend whose entire dependency set is ten
packages, for about seventy lines of arithmetic that has to be explainable in
a viva anyway. `CLAUDE.md` §24 rule 5 requires a written justification for a
new dependency, and the justification here runs the other way.

So `similarity.py` reproduces the prototype's exact configuration —
sublinear term frequency, word unigrams and bigrams, scikit-learn's English
stop-word list verbatim, L2 normalisation, smoothed IDF — in the standard
library. **`test_comparison_unit.py` asserts the two agree to within 1e-9
whenever scikit-learn happens to be importable**, and skips when it is not, so
the equivalence is checked rather than claimed.

---

## 4. The matcher, in four stages

Strongest evidence first. Each stage only sees what earlier stages left
unmatched, and matches are one-to-one throughout.

| Stage | What it matches | Why it exists |
|---|---|---|
| **1 — identical text** | Clauses whose `comparison_key` is equal, ignoring number and position entirely | This is what makes renumbering and reordering survive. It is also what makes the common case fast: **73.2%** of all matches across the real corpus are settled here |
| **1b — identical once the number is removed** | Same, after stripping a leading clause marker | "Renumbered but otherwise identical" — reported as `unchanged` with `renumbered = true`, never as a modification |
| **2 — equal clause number, related text** | Equal printed clause number *and* similarity ≥ `number_match_threshold` | A printed number is strong evidence but not proof: `1.` occurs once per annexure, so a number match on unrelated text is rejected. **16.7%** of real matches |
| **3 — similarity** | The prototype's greedy best-first matching above `match_threshold` | The right answer for clauses whose wording genuinely changed. **10.1%** of real matches |
| **4 — no counterpart** | Everything left: old → `removed`, new → `added` | Nothing is forced into a match. A threshold that matched everything would be a threshold that matched the wrong things |

### The comparison key

Two extractions of the same printed clause are not byte-identical: the line
breaks fall elsewhere, one uses a non-breaking hyphen, `pypdf` inserts a stray
space on a kerned line. Comparing raw text would report dozens of clauses as
*modified* where the policy did not change — the exact false positive the
brief forbids.

`comparison_key` folds NFKC, the seven dash codepoints, the six space
codepoints, curly quotes, soft hyphens, case, whitespace runs and
space-before-punctuation. It touches **no digit**, does no stemming and
reorders nothing, so `within 30 days` and `within 60 days` stay different —
which is precisely what the feature exists to find.

### Reordering

Detected with a **longest-increasing-subsequence** over the matched pairs'
ordinals. The LIS is the set of clauses that kept their relative order;
anything outside it genuinely moved. Comparing each pair to its neighbour
instead would flag a whole run of clauses when a single clause was promoted to
the top.

### Classification

| Label | When |
|---|---|
| `unchanged` | the two comparison keys are equal. `renumbered` and `reordered` say whether the number or the position moved; **either on its own is not a modification** |
| `modified` | matched, but the keys differ |
| `added` | present in the newer version only |
| `removed` | present in the older version only |

### Confidence, and ambiguity

`confidence` is a **label computed from stated rules**, never a probability —
the system has no calibration data, and "0.83 confident" would be an
invention. The rules are in `alignment.confidence_label` and are tested. The
raw `similarity` and `runner_up_similarity` travel beside it.

A match is `ambiguous` when the runner-up scored within `ambiguity_margin` of
the winner. It applies to the two *scored* bases, including clause-number
matches — where the runner-up can score **higher** than the clause that won,
which is the case a reviewer most needs flagged. That was found by a fixture,
not by inspection; the first version of the check only looked at stage 3.

### Thresholds

All four are constructor arguments on `AlignmentConfig`, overridable per
request, and **echoed back in every response** — a result whose thresholds are
not reported cannot be reproduced. Defaults were chosen on the real corpus,
not guessed; `evaluation/comparison/summary.txt` carries the sensitivity
sweep showing the result is stable across 0.35–0.60, which is why 0.45 sits in
the middle of it.

---

## 5. Why there is no `comparison_results` table

A comparison is a **pure function** of two stored versions, a segmentation
method and four thresholds. `align()` takes no database, no clock and no
randomness.

Storing the result would therefore add a cache-invalidation problem and a
migration in exchange for nothing: re-ingest a version from a corrected PDF
and every stored comparison silently becomes a description of a document that
no longer exists. `CLAUDE.md` §24 rule 5 and the brief both require a
justification for a new table, and there is none.

**The useful half of persistence is kept.** Every response carries a
`comparison_id`: a SHA-256 over the two version *fingerprints*, the method and
every threshold. The same inputs always produce the same id, and any change to
the inputs produces a different one — so a result can be cited in a report and
re-derived exactly by POSTing the same body. The fingerprints rather than the
public ids, because `VER-0003` can be re-ingested and an id that did not change
would point at a result nobody can reproduce.

`GET /api/compare/{comparison_id}` is therefore **deliberately absent**, and a
test asserts it stays absent.

---

## 6. Evidence — the part that matters most

`CLAUDE.md` §5 forbids showing a citation whose text does not support the
statement. The brief repeats it: *"If evidence cannot be resolved, expose the
problem instead of displaying a misleading citation."*

So every span is **verified, not assumed**. For each page a clause touches, the
stored `char_start`/`char_end` are sliced out of that page's text on the
version's own `offset_basis`, the slices are rejoined, and the result is
compared against the clause's stored text under the comparison key.

| `evidence.status` | Meaning | `quote` |
|---|---|---|
| `resolved` | every span sliced cleanly and the result matches the clause | the verbatim text |
| `partial` | the spans sliced, but the reconstruction differs | `null`, with the reason |
| `unresolved` | a page is missing, a span is out of range, or there are **no `segment_pages` rows at all** | `null`, with the reason |

Page numbers are never invented. A clause with no `segment_pages` rows is
`unresolved` even though `page_start`/`page_end` exist, because those two
columns answer *where is this clause* and a quote needs *where exactly*.

**Measured: 5,104 of 5,104 clause sides across all 12 real version pairs
resolved.** A clause spanning a page break arrives with both of its pages,
never collapsed onto one, and both spans are verified separately.

One defect here was found by a fixture and is worth recording: the quote for a
multi-page clause was originally built by concatenating the page slices
directly, producing `...received andthe name of...` — not a verbatim substring
of anything, and therefore not a citation. `SPAN_JOIN` now joins them the way
segmentation does, and the same join verifies the result.

---

## 7. The API

Four paths, mounted under `/api`. The ten pre-existing paths are untouched; a
test pins the full set of fourteen by name and another pins the count.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/compare/options` | Documents, their comparable versions, and every older→newer pair, with whether each pair's order is **declared** by the authority or merely inferred |
| POST | `/api/compare` | Run a comparison. Filters, thresholds and `include_*` flags in the body |
| GET | `/api/compare/entities` | The rule-based extractor over one version's clauses |
| GET | `/api/compare/entities/labels` | The label vocabulary **and its stated limitations** |

**There is no new version-listing endpoint.** The brief proposed
`GET /api/documents/{document_id}/versions`; `GET /api/documents/{id}` already
returns a document with its full version list, and a second endpoint would be
two places defining what a version summary is. `/compare/options` adds only
what Compare needs on top: the *pairs*.

A field withheld by an `include_*` flag comes back as **`null`, not absent** —
one thing for a consumer to test instead of two. Found by exercising the real
endpoint over HTTP rather than the service directly.

### Validation

| Condition | Response |
|---|---|
| same version twice | `422 VALIDATION_ERROR` — a client mistake, not an empty diff |
| unknown version | `404 VERSION_NOT_FOUND` |
| versions of different documents | `422` unless `allow_cross_document=true`, and then a `CROSS_DOCUMENT` warning on every response |
| `extraction_status != 'ok'` | `422` — a partial extraction loses clauses, and a lost clause would be reported as *removed*: an extraction defect shown to the user as a policy change |
| `number_match_threshold > match_threshold` | `422` — an equal clause number is extra evidence, so it must lower the bar, never raise it |

### Direction

A diff is only meaningful if "old" and "new" are the right way round. The
service decides from evidence, strongest first:

1. a declared `supersedes_version_id` — the only statement in the database
   about version order that is **asserted by a human** rather than inferred
   (D-9);
2. publication date — a hint, not proof (`CLAUDE.md` §11.1), and nullable;
3. ingestion order, with a warning.

A swap always produces a `DIRECTION_SWAPPED` warning. Silently reversing a
diff would invert every added and removed label without telling anyone.

---

## 8. One change outside this package

`backend/app/db/loader.py` gained `declarations_from_tree` and
`link_supersedes`, plus a `supersedes_links` field on `LoadSummary`.

`data/manifest.csv` has always carried a `supersedes` column holding *the
`version_label` this version replaces*, and the extraction CLI has always
copied it into `extraction.json`'s `source_metadata`. **Nothing ever read it
back out**, so `document_versions.supersedes_version_id` was never written and
the one human-asserted statement about version order was being dropped at the
last step of ingestion.

That matters specifically for Compare: without the column, "which of these is
newer" rests on publication date alone. With it, `/compare/options` can report
`supersedes_declared` and the direction of a diff becomes a fact.

The declaration is read out of the **artefacts**, so there is no new column and
no migration — only the resolved link lands in the database. Linking is
idempotent, refuses a self-reference, refuses a link that would close a cycle,
and reports an unresolvable label rather than guessing. **Six links were
written** across the real corpus.

---

## 9. Known limitations

* **No clause-level ground truth for the real corpus.** Nobody has annotated
  288 pages of master circulars, so there is **no alignment accuracy, precision,
  recall or F1** anywhere in this module's evaluation. What is measured is
  invariants, descriptive counts, and a self-comparison check. The synthetic
  fixtures have ground truth by construction and cover the named cases; the
  real corpus shows it works at scale.
* **Alignment is greedy, not optimal.** A globally better assignment can be
  missed. The runner-up score is carried into the output as ambiguity rather
  than discarded, which is the honest mitigation, but it is not a Hungarian
  matcher.
* **No semantic similarity.** TF-IDF cannot match a clause that was reworded
  without shared vocabulary. That needs an embedding model, which needs D-7 and
  a written justification. The seam is `AlignmentConfig`; nothing pretends the
  capability exists.
* **No table awareness** (D-12). The Lead Bank Scheme annexures are
  district-to-bank tables, and both segmentation and alignment treat their rows
  as clauses. The `+243/-21` and `+35/-410` counts on those pairs are partly
  table rows moving, not policy changing.
* **The entity extractor's AUTHORITY recall is 0.22.** A closed list of
  patterns cannot find the individual bank names in a district annexure. This
  is measured, not estimated, and it is the argument for a statistical model.
* **CRF and transformer conditions were not run**, with reasons recorded in
  `evaluation/entity_extraction_eval.py` and pinned by a test.

---

## 10. How to run it

```
# build the isolated version-pair database (creates nothing destructive)
.venv\Scripts\python -m backend.scripts.setup_compare_database

# serve it
set NIYAMSETU_DB_NAME=niyamsetu_compare
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8010

# the temporary frontend
.venv\Scripts\python compare_preview/serve.py --port 5174 --api http://127.0.0.1:8010

# the evaluations
.venv\Scripts\python -m evaluation.comparison_eval
.venv\Scripts\python -m evaluation.entity_extraction_eval

# the tests
.venv\Scripts\python -m pytest backend/tests/test_comparison_unit.py
.venv\Scripts\python -m pytest -m integration
```
