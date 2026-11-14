# Search-relevance judgements — the guideline, and what these numbers are worth

**Written before the judgements were made**, which is the project's rule
(`CLAUDE.md` §14, rule 1 and §15 Experiment B). Read §0 before quoting any
number from `evaluation/library-search/`.

---

## 0. Read this first — the sample and the annotator

| | |
|---|---|
| Corpus judged | **17 documents** (4 RBI + 12 IRDAI + 1 NABARD), 148 pages, 728 clause-aware segments |
| Queries | **20**, written against the corpus that exists |
| Judgement unit | **the document**, not the clause |
| Annotator | **one**, and **not independent of the system** — the same person who wrote the search code |
| Inter-annotator agreement | **not measured.** There is no second annotator |
| Therefore | these are **diagnostic** numbers for finding ranking defects, **not** a validated relevance measurement. They belong in a report with this caveat attached, and nowhere else |

This is the same honesty constraint Experiment B carries, for the same reason:
a person who knows how the ranker works cannot judge its output blind, and a
number produced that way measures agreement with the author's expectations
rather than relevance.

**A second annotator would change this from a diagnostic to a measurement.**
`template.json` exists so that can happen without re-deriving the query set.

---

## 1. What "relevant" means here

A document is **relevant to a query** if a reader asking that question in the
Library would reasonably want that document in the first screen of results.

Three rules, applied consistently:

1. **Subject, not vocabulary.** A document is relevant because it is *about*
   the thing asked for, not because the words appear in it. The RBI capital
   adequacy amendment is relevant to `capital adequacy`; a document that
   mentions the phrase once while citing a different circular is not.
2. **The governing instrument beats a passing reference.** For
   `insurance ombudsman`, the Ombudsman Rules are relevant; a circular that
   mentions the ombudsman in a list is not.
3. **Amendments count as relevant to their subject.** A reader asking about
   capital adequacy wants the amendment that changes it, even though the
   parent directions are not in the corpus.

A query may have **zero** relevant documents. That is a real and useful case:
four queries below are included *because* the corpus cannot answer them, and a
search that invents an answer for them is worse than one that returns nothing.

## 2. What is NOT judged

* **Clause-level relevance.** Judging which of 728 segments answer a question
  is a much larger annotation job, and the clause ranking is reported
  descriptively instead (snippet quality, page correctness).
* **Graded relevance.** Binary only: relevant or not. nDCG would imply a
  precision of judgement this sample does not support.
* **Anything about the Hindi halves** of the bilingual IRDAI documents. The
  index configuration is `english`; Hindi retrieval is explicitly out of scope
  (D-3) and would need a Hindi-reading annotator.

## 3. How the queries were chosen

Themes came from the mission brief (capital adequacy, commercial-bank
regulation, KYC, priority sector lending, digital payment security, liquidity,
regulatory reporting). Each was then **adapted to the corpus that exists** —
and where a theme has no document behind it, the query was **kept anyway** and
judged as having no relevant document, rather than quietly replaced with one
that works. Queries 17–20 are the deliberate negative and edge cases.

## 4. Files

| File | What it is |
|---|---|
| `judgements.json` | The query set and the relevant-document sets. The only unregenerable input |
| `template.json` | The same queries with empty judgement lists, for a second annotator |
| `README.md` | This guideline |

`evaluation/library_search_eval.py` scores `judgements.json` against the live
API and writes `evaluation/library-search/results.json`. Nothing in the
scorer knows the expected answers; they live only here.
