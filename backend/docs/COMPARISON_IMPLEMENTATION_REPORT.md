# Comparison module — audit, integration and verification report

**Written:** 2026-11-14
**Scope:** auditing the Comparison module a teammate contributed, integrating
it into the existing NiyamSetu backend, acquiring genuine version pairs to run
it on, and verifying the result.
**Status:** `[IMPLEMENTED]` — 4 endpoints, 324 new tests, measured on 12 real
RBI version pairs, and **driven by hand in a real browser** (§9.1).
**1,083 tests pass, 1 skipped, 0 failed** across the whole repository.

---

## 0. The short version

| | |
|---|---|
| Teammate's module | `comparision.py`, 252 lines, a Streamlit app. **Preserved unmodified** at the repository root |
| Could it run? | **No.** `import comparision` fails with `ModuleNotFoundError: sklearn` in this project's virtualenv |
| What was reused | TF-IDF alignment, the regex fact extractor, the plain-language explainer — three of its five algorithms |
| What was dropped | Its PDF extraction, text cleaning and clause splitting — all three duplicate the existing pipeline |
| Where it lives now | `backend/app/comparison/`, 9 modules, 3,863 lines |
| New dependencies | **none** |
| New database tables | **none**. New migrations: **none** |
| API | 4 new paths. The 10 pre-existing paths unchanged, pinned by two tests |
| Real version pairs | **12**, across 3 documents, from 9 genuine RBI master-circular issues (288 pages) acquired for this work |
| Of those, authority-declared | **6** — the newer issue's own text names the older one as its predecessor |
| Evidence verified | **5,104 / 5,104 clause sides** (100%) resolved against their stored page offsets |
| Alignment accuracy | **not measured, and deliberately not claimed** — no clause-level annotation exists for 288 pages of master circulars. See §8 |
| Entity benchmark | regex **measured**; CRF and transformer **NOT RUN**, with reasons, pinned by tests |
| Protected data | `niyamsetu` and `niyamsetu_sample` verified byte-identical afterwards |
| Real browser | **Yes** — Brave, Claude in Chrome, 2026-11-14. Both previews driven by hand; 4 defects found and fixed, each re-verified in the browser (§9.1) |

**Seven defects were found and fixed.** Three by the tests and fixtures
written for this work (§4): ambiguity was not flagged on clause-number
matches, a multi-page citation's quote was not a verbatim substring, and label
filtering in the entity extractor bypassed pattern precedence. Four more by
**driving the page by hand in Brave** (§9.1), none of which any automated
check had caught, because each is a property of the page *over time* rather
than of a single request.

---

## 1. Audit of the teammate's module

### 1.1 What it is

One file, `comparision.py`, 252 lines, at the repository root. A Streamlit
application: upload 2–4 PDFs → extract text → split into clauses → TF-IDF
similarity → greedy alignment → classify → explain → download a CSV.

It is a **coherent, working prototype of the right algorithm**. The alignment
approach is sound, the explanation layer is genuinely useful, and the
captions attached to its numbers are careful — it already said, in the UI,
that the average similarity "is not a legal-equivalence score or an overall
policy similarity score". That instinct is preserved in the integrated module
and is now carried in the API payload itself.

### 1.2 Does it run?

**No.** Verified:

```
$ .venv/Scripts/python.exe -c "import comparision"
ModuleNotFoundError: No module named 'sklearn'
```

It depends on `scikit-learn`, `streamlit` and `pandas`. None is in
`requirements.txt`; `scikit-learn` is not installed in `.venv` at all.
(`streamlit` and `pandas` are present only because `preview_app/` pinned
Streamlit separately.) The large set of packages in the user's **global**
Python install is not this project's dependency set — `CLAUDE.md` §19 is
explicit about that — so the prototype has never executed in this project.

### 1.3 Component-by-component

| # | Component | Verdict | Where it went |
|---|---|---|---|
| 1 | `similarity_matrix` — `TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True, stop_words="english")` + cosine | **reusable, kept** | `similarity.py`, same configuration, stdlib implementation (§3.1) |
| 2 | `align_clauses` — greedy best-first above a threshold, one-to-one | **reusable, kept as one stage of four** | `alignment.py` stage 3 |
| 3 | `extract_facts` — 4 regex groups, bare strings, no offsets | **reusable, extended** | `entities.py`, 8 labels with character spans |
| 4 | `explain_change` — date/period/amount deltas, similarity fallback | **reusable, extended** | `explain.py`, plus percentages, authorities, cross-references, word diff |
| 5 | `clean_text` | **duplicate — dropped** | `document_processing/normalization.py` already does this, with 7 independently toggleable steps and 15 tests |
| 6 | `extract_pdf` | **duplicate — dropped** | `document_processing/pdf_extractor.py` already does this, with validation, SHA-256, per-page provenance and 9 tests |
| 7 | `split_clauses` | **duplicate — dropped** | `document_processing/segmentation.py` already does this, with clause numbers, headings, hierarchy to depth 4, 5 marker styles and `segment_pages` rows |
| 8 | Streamlit UI (~110 lines) | **not carried over** | Replaced by 4 API endpoints + `compare_preview/` |

### 1.4 What was missing

| Missing | Consequence |
|---|---|
| **Any database** | It re-extracted PDFs on every upload. It could not see the 501 segments already ingested, and nothing it produced was citable afterwards |
| **Version modelling** | It compared two *uploaded files*. There was no `document`, no `version`, no supersedes relationship, and no way to say "these are two issues of one policy" |
| **Character offsets** | Clauses carried a page number only. A page is not a citation: `CLAUDE.md` §12.2 requires a verbatim quote, and §5 forbids showing one that cannot be checked |
| **Stable identifiers** | Clause ids were regenerated per run as `P3-C12`, so the same clause had a different id on every upload. The backend's `CLS-0000123` ids are stable |
| **Renumbering handling** | Greedy similarity alone reports a renumbered clause as *modified*. The brief forbids exactly this |
| **Ambiguity reporting** | A near-tie was committed to silently. §5.4 requires uncertain matches to be distinguishable from confident ones |
| **Tests** | None |
| **Evaluation** | None. No ground truth, no benchmark, no failure cases |
| **The CRF / BERT comparison** its own deliverable named | Not present in any form |

### 1.5 Incompatibilities that had to be resolved

| Assumption in the prototype | Reality in this backend | Resolution |
|---|---|---|
| Clauses come from an uploaded file | Clauses come from `segments`, already segmented, with provenance | `comparison/repository.py` reads them; no ingestion path in the module |
| A clause is `(doc_name, page, clause_id, text)` | A segment has 14 columns including `segment_pages` rows, `offset_basis`, hierarchy and a marker style | `ClauseRef` carries the real fields and **adds none the database does not hold** |
| Offsets do not exist | Offsets index into `normalized_text` (D-11, verified) | Every citation is sliced and checked against the right page column |
| `sklearn` is available | It is not, and adding it needs a written justification (§24 rule 5) | Reimplemented, with an equivalence test that runs if sklearn ever is present |
| Comparison happens in the UI | The UI must never hold the logic — four other modules read the API | Service + API layers; `compare_preview/` holds no comparison logic |

### 1.6 Preservation

`comparision.py` is **untouched** at the repository root — not moved, not
edited, not deleted. `CLAUDE.md` §24 rule 10 forbids destroying work without
explicit file-by-file approval, and the file is also the audit's evidence.
`backend/app/comparison/README.md` §2 maps every part of it to where it ended
up. If the owner would rather it moved to `backend/docs/superseded/` or a
`prototypes/` directory, that is a one-line change and their call.

---

## 2. Final architecture

```
Official PDF  ──(existing pipeline, unchanged)──►  PostgreSQL
                                                      │
                             documents / document_versions / pages
                             segments / segment_pages
                                                      │
                                        ┌─────────────┴──────────────┐
                                        │  backend/app/comparison/   │
                                        │                            │
    repository.py  ◄── read-only SQL ───┤  similarity.py  key+TFIDF  │
                                        │  alignment.py   4-stage    │
                                        │  entities.py    regex NER  │
                                        │  explain.py     diff+prose │
                                        │  service.py     validate,  │
                                        │                 direction, │
                                        │                 evidence   │
                                        │  schemas.py     Pydantic   │
                                        │  api.py         4 routes   │
                                        └─────────────┬──────────────┘
                                                      │
                            backend/app/api/main.py (composition point)
                                                      │
                                      /api/compare · /api/compare/options
                                      /api/compare/entities · …/labels
                                                      │
                                        compare_preview/  (throwaway UI)
```

| Decision | Choice | Why |
|---|---|---|
| Location | `backend/app/comparison/` | The repo's convention is `backend/app/<feature>/` (§17.2). Compare is SQL over tables the backend owns, through the engine it already pools — the same reasoning `library_search/` records |
| Router | Owned by the module, mounted by `api/main.py` | Everything comparison-related in one directory; `main.py` stays a composition point |
| Data access | `comparison/repository.py`, beside `db/repository.py` | `db/repository.py` is the frozen Phase 4 read layer four teammates code against. Putting experimental queries behind a stable contract is how a contract stops being stable |
| Persistence | **none** — computed on demand | A comparison is a pure function of its inputs; a stored row goes stale the moment a version is re-ingested. See `comparison/README.md` §5 |
| Dependencies | **none added** | §3.1 |
| Migrations | **none added** | §3.2 |

---

## 3. Two decisions worth defending

### 3.1 No scikit-learn

The prototype's TF-IDF configuration is reproduced exactly — sublinear term
frequency, word unigrams and bigrams, scikit-learn's English stop-word list
verbatim, L2 normalisation, `smooth_idf=True` — in about 70 lines of standard
library.

Importing it instead would add scikit-learn, scipy, joblib and threadpoolctl
(of the order of 100 MB) to a backend whose entire dependency set is ten
packages, for arithmetic that must be explainable in a viva anyway. The
justification `CLAUDE.md` §24 rule 5 demands runs the other way here.

The equivalence is **checked, not claimed**:
`test_this_tfidf_agrees_with_scikit_learn_when_it_is_installed` asserts the two
agree to within 1e-9 over a 4×4 similarity matrix, and skips when scikit-learn
is absent — which it is on this machine, so that one test is the single `skipped`
in the suite.

### 3.2 No new table, and no migration

`align()` takes no database, no clock and no randomness. Storing its output
would add a cache-invalidation problem for nothing: re-ingest a version from a
corrected PDF and every stored comparison silently describes a document that no
longer exists.

The useful half of persistence is kept. Every response carries a
`comparison_id` — a SHA-256 over the two version **fingerprints**, the method
and every threshold. Same inputs, same id; any change, different id. So a
result can be cited in a report and re-derived exactly, with no row to go
stale. `GET /api/compare/{comparison_id}` is deliberately absent and a test
keeps it absent.

The two existing migrations were **not touched**: same two files, same SHA-256
checksums recorded in all three databases after this work.

---

## 4. Three defects the tests found

Each was found by a test or fixture written for this work, not by inspection,
and each is recorded in a comment where it happened.

### 4.1 Ambiguity was not flagged on clause-number matches

The `ambiguous_alignment` fixture failed. Clause `1.` had been matched to
clause `1.` at similarity 0.774 while clause `2.` scored **0.839** — the
runner-up outscored the winner, and the result was reported as confident.

Cause: ambiguity was only computed for stage-3 (similarity) matches. A
clause-number match is accepted at a *lower* bar precisely because the number
is independent evidence, which is exactly when the runner-up can score higher
— the case a reviewer most needs flagged.

Fix: ambiguity now applies to both scored bases. An exact-text match is still
never ambiguous, because the comparison keys are equal and there is nothing to
be uncertain about.

### 4.2 A multi-page citation's quote was not a verbatim substring

`test_every_quote_is_a_verbatim_substring_of_its_clause[multi_page_clause]`
failed. A clause spanning a page break is stored as one span per page, and the
quote was built by concatenating the slices directly — producing
`...received andthe name of...`, which is not a substring of anything and
therefore not a citation at all.

Worse, the evidence had been marked `resolved`: verification joined the slices
with a space while the quote joined them with nothing, so the two disagreed and
only the quote was wrong.

Fix: `SPAN_JOIN` is one constant used by both, so a quote is offered only when
the joined form matches the stored clause text.

### 4.3 Label filtering bypassed pattern precedence

Found while reading real output from the 2017 Minority Communities circular:
the explanation claimed *"the date changed from 05/09.10, July 01, 2017,
01/09.10 and June 30, 2017 to …"*. `05/09.10` is a fragment of the circular's
file number `FIDD.GSSD.BC.No.05/09.10.01/2017-18`.

Cause: `extract_entities(text, labels=["DATE"])` skipped the un-wanted
patterns, so `REFERENCE_NO` never ran and never claimed those characters —
the date pattern got them instead. Asking for one label changed the answer for
that label.

Fix: `labels` now filters the **result**, not the cascade. Every pattern always
runs, so precedence is identical with or without a filter.

---

## 5. The real version-pair corpus

### 5.1 What was acquired, and why it counts

**9 documents: 3 RBI Master Circulars × 3 successive annual issues, 288 pages.**
Full provenance in `data/version_pairs/README.md`.

A master circular is the ideal case and not a convenient one: the RBI reissues
each one annually, and **each issue names its predecessor by reference number
in its own opening paragraph**. The version relationship is therefore asserted
by the issuing authority in the document text, not inferred by us from title
similarity — the standard D-9 sets.

`rbidocs.rbi.org.in`, which the sample-corpus work recorded as unreachable, now
*connects* but answers a PDF request with an F5/TSPD JavaScript bot-protection
challenge instead of the file (verified 2026-11-14, a 200 whose body begins
`<!DOC`). It is still unusable from a script, for a different reason than
before. `www.rbi.org.in` serves the same circulars as real PDFs with no such
challenge, and every URL was found from that host's own master-circular index.

`fetch_version_pairs.py --verify` re-reads page 1 of each PDF and checks the
recorded reference number actually appears in it — because a URL that quietly
started serving a different year would turn a genuine version pair into a
comparison of two unrelated documents, and no checksum would catch that.
**Last run: 9 documents, 0 problems.**

### 5.2 Resulting pairs

Loaded into `niyamsetu_compare`, a third database created for this corpus: 6
documents, 13 versions, 345 pages, 5,088 segments. **3 documents have more than
one version, yielding 12 ordered pairs, 6 of them authority-declared.**

The SC/ST chain joins the 2026 issue already in `data/manifest.csv` — they
share a `document_group` — so that logical document has **four** versions.

### 5.3 The loader gap this exposed

`data/manifest.csv` has always had a `supersedes` column holding the
`version_label` a version replaces, and the extraction CLI has always copied it
into `extraction.json`. **Nothing ever read it back out**, so
`document_versions.supersedes_version_id` was never populated and the one
human-asserted statement about version order was dropped at the last step of
ingestion.

`backend/app/db/loader.py` gained `declarations_from_tree` and
`link_supersedes` to close it. The declaration is read from the **artefacts**,
so there is no new column and no migration — only the resolved link is written.
Linking is idempotent, refuses a self-reference, refuses a link that would
close a cycle, and reports an unresolvable label rather than guessing.
**6 links written.**

---

## 6. Test coverage

**316 new tests. Repository total: 1,075 passed, 1 skipped, 0 failed** (was
760 before this work). 879 need no database; 196 need PostgreSQL and are
selected by `-m integration`.

| Module | Tests | What it covers |
|---|---|---|
| `test_comparison_unit.py` | 119 | Comparison key, TF-IDF (incl. the sklearn equivalence check), the four stages, classification, confidence rules, LIS reordering, word diff, explanations, entity patterns, module boundaries |
| `test_comparison_api.py` | 62 | All four endpoints with a fake service and no database: routing, validation, the §12.1 envelope, parameter forwarding, serialisation, the 10-path regression |
| `test_comparison_integration.py` | 67 | Real PostgreSQL: 6 synthetic fixtures through the real pipeline, plus the real RBI corpus |
| `test_compare_preview.py` | 35 | The preview as source: nothing hardcoded, no `innerHTML`, loopback-only proxy, self-contained |
| `test_entity_extraction_eval.py` | 33 | The gold file validates; the scorer's formulas; the unrun conditions cannot start emitting a number |

### 6.1 The brief's required test cases

Every case the brief named, with where it is covered:

| Required | Covered by | Result |
|---|---|---|
| An added clause | `added_removed_modified_unchanged` fixture + unit | ✅ |
| A removed clause | same | ✅ |
| A modified clause | same, asserting the `30 days → 60 days` entity delta, not merely the label | ✅ |
| An unchanged clause | same, plus `identical_versions` (all 4 clauses unchanged) | ✅ |
| A renumbered clause | `renumbered_only` fixture — asserts **0 modified**, ≥3 renumbered | ✅ |
| A reordered clause | `reordered_only` fixture — asserts **0 modified**, ≥1 reordered | ✅ |
| An ambiguous alignment | `ambiguous_alignment` fixture — asserts `ambiguous`, `confidence=low`, a named runner-up | ✅ |
| A clause spanning multiple pages | `multi_page_clause` fixture — 2 `segment_pages` rows, both spans verified, and the changed figure is **past** the page break | ✅ |
| Version validation | 8 integration tests + 7 API tests | ✅ |
| Same-document validation | refused by default; `allow_cross_document` warns | ✅ |
| Similarity thresholds | configurable, validated, echoed, swept | ✅ |
| Evidence resolution | every side of every change, every fixture | ✅ |
| Comparison API | 62 mocked + 59 real-HTTP checks | ✅ |
| Invalid version ids | `404 VERSION_NOT_FOUND` | ✅ |
| Empty version pairs | honest empty state with corpus counts | ✅ |
| Database integration | 67 integration tests | ✅ |
| Regression on existing APIs | 2 path tests + 4 live endpoint checks | ✅ |

### 6.2 The synthetic fixtures

`backend/tests/version_pair_builder.py` builds 6 labelled pairs as PDFs and
runs them through the **real pipeline** — `build_pdf_bytes` → `extract_pdf` →
header/footer cleaning → both segmentations → `db.loader` → PostgreSQL. There
is no fixture-only load path, so a fixture cannot pass through a door a real
document does not use.

They are clearly synthetic: every clause reads as invented, the preamble says
`FIXTURE CIRCULAR - NOT A GOVERNMENT DOCUMENT`, and they are generated into
`tmp_path` at test time and never written to `data/`. `CLAUDE.md` §16 forbids
synthetic text in the corpus and nothing here goes near it.

One fixture had to be rewritten: the first version gave the added and removed
clauses the same number (`3.`) and near-identical wording, and the matcher
correctly paired them as a modification — so the fixture tested nothing. They
now share no vocabulary and no clause number, and the note in the file records
why.

---

## 7. Verification against the live backend

The backend was run against `niyamsetu_compare` and exercised over real HTTP.

### 7.1 API flow — 59 checks, 0 failed

Every declared pair compared end to end, every filter, pagination,
determinism, direction swapping, all seven validation cases, cross-document
refusal and opt-in, entity extraction, and the ten pre-existing paths.

```
POST /api/compare on every declared pair
  DOC-0003 VER-0012->VER-0005  +3 -1 ~19 =40    | clauses  60->62  | evidence 122/122 |   56ms
  DOC-0003 VER-0009->VER-0012  +6 -54 ~31 =23   | clauses 108->60  | evidence 168/168 |   47ms
  DOC-0005 VER-0006->VER-0008  +35 -410 ~144 =375 | clauses 929->554 | evidence 841/841 | 1099ms
  DOC-0005 VER-0013->VER-0006  +243 -21 ~139 =547 | clauses 707->929 | evidence 943/943 | 1266ms
  DOC-0006 VER-0011->VER-0007  +9 -0 ~32 =245   | clauses 277->286 | evidence 563/563 |  160ms
  DOC-0006 VER-0010->VER-0011  +2 -0 ~40 =235   | clauses 275->277 | evidence 552/552 |  148ms
```

One finding: `include_diff=false` returns `"word_diff": null` rather than
omitting the key, because the Pydantic response model declares the field. That
is now the **documented** contract — one thing for a consumer to test instead
of two — with a test pinning it.

### 7.2 A real result, read in full

Minority Communities, 2017-07-01 → 2018-07-02 (an authority-declared pair):
275 → 277 clauses, **235 unchanged, 40 modified, 2 added, 0 removed**, every
citation resolved. A modified clause, as the API reports it:

> **OLD** p2 clause 1: *1. Credit Facilities to Minority Communities: The
> Government of India has indicated that care should be taken to see that
> minority communities secure,…*
> **NEW** p2 clause 1: *(same opening)*
> **Explanation:** "The two clauses cover the same material, but the percentage
> changed from 25%, 40 percent and 10 per cent to 25%, 40 percent and 75%;
> Small Finance Banks is now named, and was not before. 22 words were added and
> 49 removed. Read the quoted text on both sides to confirm the practical and
> legal effect; this description reports what the words say, not what they
> mean."

That is the deliverable working: a named, checkable change — a percentage and
a newly named class of institution — not a similarity score.

### 7.3 Evaluation over all 12 pairs

`evaluation/comparison_eval.py`, output committed to
`evaluation/comparison/`.

| Measure | Result |
|---|---|
| Pairs passing **every** invariant | **12 / 12** |
| Invariants checked per pair | conservation on both sides, every change has a side, no quote without resolution, determinism, direction as requested |
| Clause sides checked | **5,104** |
| Verified against stored page offsets | **5,104 (100%)** — `{"resolved": 5104}` |
| Self-comparison (929 clauses against themselves) | **100% unchanged**, 0 spurious reordering, 0 spurious renumbering |
| Match basis across the corpus | `exact_text` **73.2%**, `clause_number` **16.7%**, `similarity` **10.1%** |
| Median unchanged ratio, declared consecutive pairs | **0.605** |
| Median alignment time | **73 ms**; slowest pair (707 × 929 clauses) **858 ms** |

The **self-comparison check** is the one correctness statement available
without annotation: a version compared with itself must come back 100%
unchanged, with nothing reordered or renumbered. A matcher that cannot
recognise a document as identical to itself cannot be trusted on two documents
that differ. On the 929-clause Lead Bank Scheme issue — the one with the most
repeated boilerplate — it does.

The **threshold sensitivity sweep** shows the result is identical across
`match_threshold` 0.35–0.60 and moves only at 0.20 and 0.80, which is why the
default 0.45 sits in the middle of the plateau rather than being asserted.

---

## 8. What is NOT measured, and why

This section is the important one.

### 8.1 There is no alignment accuracy figure anywhere

**No clause-level ground truth exists for the real corpus.** Nobody has
annotated 288 pages of master circulars saying which clause of the 2017 issue
corresponds to which clause of the 2016 one. So:

* `evaluation/comparison_eval.py` reports **no** alignment precision, recall,
  F1 or accuracy. A test greps it to keep it that way.
* The per-pair counts in §7.1 are **descriptions of what the system
  reported**, not scores. "54 clauses were removed between the 2016 and 2017
  SC/ST circulars" is a fact about the output; whether 54 is *correct* is
  exactly what no number here can tell you.
* What *is* asserted is invariants (a violation is a defect, not a judgement
  call), the self-comparison check, and the synthetic fixtures' known answers.

Producing a real alignment metric needs a second person to annotate a sample of
clause correspondences. The guideline would follow
`data/annotations/entities/README.md`'s shape, and it is the single highest-value
next step (§11).

### 8.2 The entity benchmark: one condition of three

The teammate's deliverable named regex, CRF and "pretrained or fine-tuned
BERT". Measured honestly:

**Gold set:** 30 clauses drawn with `random.Random(20261114)` from the 747
clause-aware segments of 80–700 characters across all 9 real issues.
**90 gold mentions**, hand-annotated against a guideline written first
(`data/annotations/entities/README.md`). **One annotator, not independent of
the system; agreement PENDING.** These are diagnostic numbers, not a validated
measurement.

#### regex — MEASURED

| Label | Exact P | Exact R | Exact F1 | Relaxed F1 | Gold |
|---|---|---|---|---|---|
| OBLIGATION | **1.00** | **1.00** | **1.00** | **1.00** | 10 |
| SECTION_REF | **1.00** | **1.00** | **1.00** | **1.00** | 3 |
| DATE | 0.82 | **1.00** | **0.90** | **0.90** | 14 |
| REFERENCE_NO | 0.45 | 0.42 | 0.43 | **0.96** | 12 |
| AUTHORITY | **1.00** | 0.22 | 0.35 | 0.35 | 51 |
| **Overall** | **0.83** | **0.48** | **0.61** | **0.69** | 90 |
| **Macro F1** | | | **0.74** | **0.84** | |

Inference: **0.14 ms/clause**, 1.7M characters/second.

Three findings, each actionable:

1. **OBLIGATION and SECTION_REF are solved by rules.** A closed vocabulary of
   modal verbs and a closed set of cross-reference nouns is exactly what a
   regex is good at. A statistical model has nothing to add here.
2. **REFERENCE_NO finds the entity but gets the boundary wrong** — exact F1
   0.43, relaxed F1 **0.96**. The cause is extraction, not the pattern:
   `pypdf` reads `RPCD.CO. LBS.BC.57/ 02.19.10/2009-2010` with stray spaces
   inside one printed reference number. The gold set annotates the text as it
   actually is, so the defect is visible rather than hidden behind a scoring
   convention. The downstream effect is the 3 DATE false positives — fragments
   like `02.19.10` that `REFERENCE_NO` failed to claim.
3. **AUTHORITY is the headline weakness: precision 1.00, recall 0.22.** A
   closed list of organisation patterns cannot find the individual bank names
   in a district-allocation annexure — `UCO Bank`, `United Bank of India`,
   `Andhra Bank` appear 15 times in two sampled clauses alone. This is
   **the measured argument for a statistical model**, and it only exists
   because the label was defined from what the product needs rather than from
   what the regex can do. A test pins recall below 0.5 so the conclusion cannot
   be quietly overstated.

**Two pattern defects the benchmark found were fixed**, which makes those two
patterns' post-fix numbers in-sample. Both figures are reported:

| | Exact P | Exact R | Exact F1 | Relaxed P |
|---|---|---|---|---|
| Held out (no pattern changed after seeing the gold) | 0.644 | 0.422 | 0.510 | 0.864 |
| After fixing the two defects it found | **0.827** | 0.478 | 0.606 | **0.942** |

The defects were `Lead Bank` matching inside `Lead Bank Scheme` (a scheme is
not a body — 10 false positives) and a missing plural on
`State Level Bankers Committees`. Recall is essentially unchanged, because
neither fix addresses the AUTHORITY gap.

**One annotation error was corrected after the benchmark found it:** `NABARD`
and `April 6, 2018` in `CLS-0002531` were missed because the annotator read a
420-character preview of a 437-character clause. The benchmark reported both as
extractor false positives; they were gold mentions and the extractor was right.
The correction is recorded in the clause's own `note` field and a test asserts
that record exists, because "the gold set was amended after seeing the output"
is exactly what a reader of a metric needs to know.

#### CRF — NOT RUN

A CRF is a supervised sequence labeller and needs labelled training data. The
gold set is 30 clauses / 90 mentions and was drawn as a **test** sample;
training on part of it would leave ~20 clauses to fit and ~10 to score, which
measures the split rather than the model. `sklearn-crfsuite` is not installed
and is not in `requirements.txt`; pinning it would need the §24 rule 5
justification, and that justification cannot honestly be written for a
comparison the data cannot support.

**What would change this:** of the order of 1,000 labelled sentences. That is a
corpus and annotation decision, not a code change — the guideline and the
scorer are both already in place.

#### Pretrained / fine-tuned transformer — NOT RUN

Same data problem, plus a compute one: neither `transformers` nor `torch` is
installed, there is no GPU on this machine, and a zero-shot pretrained NER
predicts PERSON / ORG / LOC / DATE rather than this project's eight labels — so
its output is not comparable to this gold set without a label-mapping decision
that would itself need validating.

**What would change this:** an agreed label mapping (ORG → AUTHORITY, DATE →
DATE, plus an explicit decision about the five labels no general-purpose NER
has) makes a **zero-shot** comparison possible on those two labels with **no
training data at all**. That is the cheapest honest next step, and it is
recommended in §11.

Both unrun conditions carry their reason in `CONDITIONS` and **a test asserts
each contains no numeric key**, so a fabricated metric cannot appear by
accident. This follows the precedent of the project's three existing `PENDING`
scorers.

### 8.3 Other gaps

* **Responsive layout is unverified.** The browser session (§9.1) could not
  resize the window — the tool reported success while the viewport stayed at
  1536px — so the page has only ever been seen at desktop width. The CSS has a
  760px breakpoint; nobody has looked at it.
  Accessibility is likewise unverified: no screen reader was used.
* **Three chains, one authority.** All 9 real documents are RBI. D-10 is
  advanced, not closed.
* **No table awareness** (D-12). The Lead Bank Scheme annexures are
  district-to-bank tables whose rows are segmented as clauses, so the
  `+243/-21` and `+35/-410` counts on that chain are partly table rows moving
  rather than policy changing. This is the clearest single improvement
  available to the alignment quality.
* **No semantic similarity.** TF-IDF cannot match a clause reworded without
  shared vocabulary. Needs D-7.
* **Greedy, not optimal, matching.** A globally better assignment can be
  missed; the runner-up is surfaced as ambiguity rather than discarded.

---

## 9. The temporary frontend

`compare_preview/` — three static files plus a 179-line standard-library
server, in `search_preview/`'s conventions and palette. **Not** Person 4's
Compare interface and not Person 2's frontend; D-1 still gates `frontend/`.

Select a document and two issues → compare → read the summary → filter → read
the changes side by side with a word-level diff → open the evidence for either
side. Filters **re-run on the backend** rather than hiding rows in the
browser, so what is on screen is always a real API response.

Its proxy forwards **POST** as well as GET — `POST /api/compare` is how a
comparison runs — restricted to `/api/` paths, a loopback backend and a 64 KB
body.

**The browser gap is closed.** On 2026-11-14 the page was driven by hand in
Brave with the Claude in Chrome extension connected — see §9.1 for what that
found. `compare_preview/check_render.mjs` remains the fast repeatable check:
it runs `app.js` against a **real response from the running backend** through a
minimal DOM shim built from Node's standard library (no npm, no jsdom).

**55 checks, 0 failed**, including: the picker fills from the API with real
`DOC-` ids; the request carries the right filters; the summary renders the four
counts and the similarity caveat; the word diff produces 57 insertion and 56
deletion spans whose text is in **text nodes**; the evidence drawer shows one
quote block per page a clause touches (2 of 2 on a spanning clause) and the
quote equals what the backend verified; filtering re-runs on the backend; and an
impossible filter produces the honest empty state.

It is **not a browser** and does not claim to be: no layout, no CSS, no event
loop, no pointer — so it says nothing about whether the page *looks* right or
whether a click lands where it should. Two of its own early failures were gaps
in the shim rather than bugs in the page (a `<select>` whose `value` did not
follow `selectedIndex`; the `method` options that `index.html` declares), and
both are recorded in comments where they happened — a harness that silently
blames the code is worse than no harness.

`test_compare_preview.py` covers the same rules as 41 source checks, so they
run in the ordinary suite with no Node and no server.

### 9.1 The real-browser session, and the three defects it found

**2026-11-14, Brave, Claude in Chrome extension connected.** Both previews
were driven by hand against the live backends. Every one of the ~80 network
requests returned 200 (bar one favicon 404, fixed below) and the JavaScript
console stayed **completely silent** throughout.

Three defects surfaced that **1,075 passing tests, 55 render checks and 59 HTTP
checks had all missed**, because each is a property of the page *over time*
rather than of any single request:

**1 — A filter change during an in-flight request was silently dropped.**
`runComparison` began `if (state.running) return;`. Ticking two filters in
quick succession left the page showing the *first* result under the *second*
set of controls: all four change types ticked, yet "25 of 60 change(s) shown ·
65 before filtering" — the 5 `unchanged` rows missing with nothing to say so.
On the 707×929-clause Lead Bank pair, where a comparison takes over a second,
any normal user would hit this. Fixed with a queued re-run plus a request
sequence number, so a superseded response can no longer render either.
*Re-verified in the browser: the identical sequence now reports 65.*

**2 — A document-level field was displayed as version-level.**
`reference_no` is a column on `documents`, not `document_versions`, so every
issue of one circular shares a single value. The version card labelled it
`reference`, and the 2019 SC/ST issue was therefore shown carrying the **2026**
issue's number — while the real 2019 number sat visible in the clause text two
panels below. Fixed by renaming the API field to `document_reference_no`, with
its scope in the OpenAPI description, and relabelling the row `document ref`.
*Re-verified in the browser.*

**3 — Changing the selection left the previous comparison on screen.**
Switching the document repopulated the pickers but kept the old diff below
them: DOC-0003's comparison sat under a selector reading DOC-0006, and stayed
there even when the selection became invalid and Compare was disabled. Fixed
with `invalidateComparison()`, which clears the result and shows "selection
changed — press Compare". *Re-verified in the browser.*

A fourth, cosmetic: `GET /favicon.ico` 404'd on every page load of both
previews. Fixed with an inline SVG data-URI icon — no extra file, no extra
request.

Six regression tests now pin all four (`test_comparison_api.py`,
`test_compare_preview.py`), and one existing test was made **more precise**
rather than weaker: `test_the_page_loads_no_remote_script_or_stylesheet`
checked for any `http://` in the file and false-positived on the favicon's
SVG `xmlns`, which is a namespace identifier and not a fetch; it now inspects
`src`/`href` attribute values, which is the rule it always meant.

**What the session could not verify:** the window would not resize — the tool
reported success while `window.innerWidth` stayed 1536 — so **responsive
layout at narrow viewports remains untested**, and no screen reader was used.

---

## 10. What was preserved

Checked after all the work, not assumed:

| Protected | Before | After |
|---|---|---|
| `niyamsetu` | 4 docs / 4 versions / 57 pages / 501 segments / 552 segment_pages | **identical** |
| `niyamsetu_sample` | 17 / 17 / 148 / 1,503 / 1,618 | **identical** |
| Migrations | `0001`, `0002` | **same two files, same SHA-256 in all three databases**. None added, none edited |
| The ten API paths | 10 | **all 10 present**, exercised live, pinned by two tests |
| Library & Search | 170 tests | **passing**, plus live checks of all four search endpoints |
| `search_preview/` | works | untouched; `compare_preview/` uses port 5174 so both run at once |
| Existing tests | 760 passing | **760 still passing.** Two were edited in place — the path-set and path-count pins — to include the four new routes, keeping both assertions exact. None weakened, none removed |
| Git | branch `diw_backend`, uncommitted work present | **nothing committed, nothing pushed, no branch change, no reset** |
| `comparision.py` | the teammate's original | **untouched** |
| `.env` | git-ignored | never printed; the password is masked in every log line |

The only pre-existing files modified at all are `backend/app/db/loader.py`
(additive: `link_supersedes`), `backend/app/api/main.py` (three lines mounting
the router plus a docs paragraph), `.gitignore` (two corpus directories), and
the two test pins above.

---

## 11. Recommended next steps, in order of value

1. **Annotate clause correspondences for one real pair.** The single thing
   that would turn §7.3's descriptive counts into an alignment measurement.
   One declared consecutive pair — Minority Communities 2017→2018, 275 vs 277
   clauses — is the right size, and a second annotator makes it a measurement
   rather than a diagnostic. This is the biggest remaining gap in the module.
2. **Make segmentation table-aware (D-12).** The Lead Bank Scheme chain shows
   the cost: annexure table rows are segmented as clauses and then aligned as
   clauses. PyMuPDF's `find_tables()` is already a pinned dependency and is
   already shown to recover the row structure.
3. **Run the zero-shot transformer comparison on AUTHORITY and DATE.** It
   needs no training data — only an agreed label mapping — and AUTHORITY recall
   of 0.22 is the measured case for it. The benchmark harness takes a new
   condition without modification.
4. **Connect the Chrome extension and click through `compare_preview/`.**
   The render check covers the logic; nobody has yet confirmed the page looks
   right.
5. **Extend the version-pair corpus beyond RBI**, if IRDAI or NABARD archives
   can be found. Three chains from one authority is a narrow base for any claim
   about generalisation.
6. **Tell the other four modules about `supersedes_version_id`.** It is now
   populated, and `/compare/options` reports `supersedes_declared`. Person 4's
   version selection and Person 2's document view can both use it.

---

## 12. Files

### Created

| Path | Lines | What |
|---|---|---|
| `backend/app/comparison/similarity.py` | 332 | Comparison key + TF-IDF cosine |
| `backend/app/comparison/alignment.py` | 695 | The four-stage matcher, classification, confidence |
| `backend/app/comparison/entities.py` | 316 | The regex entity baseline |
| `backend/app/comparison/explain.py` | 392 | Word diff + plain-language description |
| `backend/app/comparison/repository.py` | 512 | Read-only SQL |
| `backend/app/comparison/service.py` | 817 | Validation, direction, evidence, filtering |
| `backend/app/comparison/schemas.py` | 447 | Pydantic models |
| `backend/app/comparison/api.py` | 313 | The router |
| `backend/app/comparison/__init__.py` | 99 | Public surface |
| `backend/app/comparison/README.md` | — | Module reference |
| `backend/scripts/setup_compare_database.py` | 351 | Build `niyamsetu_compare` |
| `backend/tests/version_pair_builder.py` | 515 | 6 synthetic pairs through the real pipeline |
| `backend/tests/test_comparison_unit.py` | 974 | 119 tests |
| `backend/tests/test_comparison_api.py` | 864 | 62 tests |
| `backend/tests/test_comparison_integration.py` | 957 | 67 tests |
| `backend/tests/test_compare_preview.py` | 334 | 35 tests |
| `backend/tests/test_entity_extraction_eval.py` | 348 | 33 tests |
| `data/version_pairs/sources.py` | 275 | The corpus list + predecessor evidence |
| `data/version_pairs/fetch_version_pairs.py` | 372 | Downloader + provenance verification |
| `data/version_pairs/README.md` | — | Corpus documentation |
| `data/annotations/entities/README.md` | — | The annotation guideline, written first |
| `data/annotations/entities/gold.json` | — | 30 clauses, 90 gold mentions |
| `evaluation/comparison_eval.py` | 510 | Invariants + descriptive statistics over 12 pairs |
| `evaluation/entity_extraction_eval.py` | 556 | P/R/F1, two match modes, failure cases, timing |
| `compare_preview/` | 1,692 | The throwaway Compare UI + render check |

### Modified

| Path | Change |
|---|---|
| `backend/app/api/main.py` | Mount `compare_router`; a docs paragraph |
| `backend/app/db/loader.py` | **Additive:** `declarations_from_tree`, `link_supersedes`, `LoadSummary.supersedes_links` |
| `backend/app/comparison/entities.py` | Two pattern fixes the benchmark found (§8.2) |
| `backend/tests/test_library_search_scaffold.py` | The exact path set now lists the four compare paths |
| `backend/tests/test_library_search_api.py` | The path count 10 → 14, with the reason |
| `.gitignore` | `data/version_pairs/raw/`, `data/version_pairs/processed/` |

### Generated

`evaluation/comparison/{results.json,summary.txt}`,
`evaluation/entity-extraction/{results.json,summary.txt}`,
`data/version_pairs/{manifest.csv,fetch-log.csv}`.
