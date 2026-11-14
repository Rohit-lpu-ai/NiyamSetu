# `data/version_pairs/` — the genuine RBI version-pair corpus

**Nine real documents: three RBI Master Circulars, each in three successive
annual issues.** Acquired on 2026-11-14 so that Compare had something real to
run on.

This is the corpus that closes the gap D-10 had left open for Compare
specifically. The 4-document development corpus and the 17-document Library &
Search sample corpus both contain **zero version pairs** — every document has
exactly one version — so until now every claim about clause alignment could
only be demonstrated on synthetic fixtures.

---

## 0. What makes these genuine version pairs

An RBI Master Circular is reissued every year, consolidating the instructions
issued since the last issue, and **each issue names its predecessor in its own
opening paragraph**. The 2017 SC/ST circular begins:

> Please refer to the Master Circular FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17
> dated July 01, 2016 consolidating guidelines / Instructions / directions
> issued to banks with regard to providing credit facilities to Scheduled
> Castes (SCs) and Scheduled Tribes (STs). The Master Circular has been
> suitably updated by incorporating the instructions issued up to
> June 30, 2017…

`FIDD.CO.GSSD.BC.No.03/09.09.001/2016-17` is the reference number printed on
the 2016 document in this corpus. So the version relationship is **asserted by
the issuing authority in the document text**, not inferred by us from title
similarity — which is the standard `CLAUDE.md` D-9 sets for declaring one
upload a version of another.

Each row's `supersedes_evidence` in `sources.py` records the sentence the claim
rests on, so a reader can open the PDF and check it rather than trust this
file.

---

## 1. The corpus

| document_group | Issues | Pages each | Reference numbers |
|---|---|---|---|
| `rbi-master-circular-sc-st-credit-facilities` | 2016-07-01, 2017-07-01, 2019-07-01 | 13, 8, 8 | RBI/2016-17/05, RBI/2017-18/7, RBI/2019-20/06 |
| `rbi-master-circular-minority-communities-credit` | 2017-07-01, 2018-07-02, 2019-07-01 | 17, 17, 17 | RBI/2017-2018/6, RBI/2018-19/8, RBI/2019-20/03 |
| `rbi-master-circular-lead-bank-scheme` | 2017-07-03, 2018-07-02, 2019-07-02 | 66, 71, 71 | RBI/2017-2018/8, RBI/2018-19/5, RBI/2019-20/10 |

**288 pages, 9 documents, all `extraction_status = ok`, zero empty pages.**

The SC/ST chain joins the 2026 issue that is already in
`data/manifest.csv` — they share a `document_group`, so that logical document
ends up with **four** versions and six ordered pairs.

The Lead Bank Scheme chain is included deliberately: at 66–71 pages with
substantial annexures and district-allocation tables, it is the hard case. The
other two chains are short enough that alignment could look easy.

---

## 2. Rebuild it

The PDFs and the pipeline output are git-ignored, exactly like
`data/sample_corpus/`. The manifest, the fetch log and `sources.py` **are**
committed — they are the provenance record and the only unregenerable part.

```
# 1. download (official publisher host only, checksums recorded)
.venv\Scripts\python data/version_pairs/fetch_version_pairs.py
.venv\Scripts\python data/version_pairs/fetch_version_pairs.py --verify

# 2. extract and segment, with the ordinary CLIs
.venv\Scripts\python -m backend.app.document_processing.cli ^
    --manifest data/version_pairs/manifest.csv ^
    --output-dir data/version_pairs/processed --verify-checksums
.venv\Scripts\python -m backend.app.document_processing.segmentation_cli ^
    --all --processed-dir data/version_pairs/processed

# 3. load into the isolated database
.venv\Scripts\python -m backend.scripts.setup_compare_database
```

`--verify` does more than recompute checksums: it re-reads page 1 of each PDF
and checks that the reference number recorded in `sources.py` actually appears
in it. The whole value of this corpus is that the version relationships are
declared by the publisher, and a URL that quietly started serving a different
year's circular would turn a genuine version pair into a comparison of two
unrelated documents. Catching that needs the document's own text, not its
checksum. **Last run: 9 documents, 0 problems.**

---

## 3. Why `www.rbi.org.in` and not `rbidocs.rbi.org.in`

`data/sample_corpus/sources.py` records that `rbidocs.rbi.org.in` would not
complete a TLS handshake from this machine, which is why nine selected RBI
documents could not be downloaded for that corpus.

**That has changed, and it is still unusable.** The host now connects, but it
answers a PDF request with an F5/TSPD JavaScript bot-protection challenge
rather than the file — a `200` response whose body begins `<!DOC` and is 47 KB
of obfuscated JavaScript. Verified on 2026-11-14. So it is unusable from a
script for a *different* reason than before, and the earlier note should not
be read as still accurate about the cause.

`www.rbi.org.in` serves the same master circulars as real PDFs under
`/commonman/Upload/English/Notification/PDFs/` with no such challenge. Every
URL in `sources.py` was found from that host's own master-circular index, so
the provenance chain is RBI's index page → RBI's notification page → RBI's
PDF, all on the publisher's own domain. `ALLOWED_HOSTS` is checked before
every request.

---

## 4. Files

| File | Committed? | What it is |
|---|---|---|
| `sources.py` | **yes** | The URL list, the human-asserted metadata, and the predecessor evidence for every row |
| `fetch_version_pairs.py` | **yes** | The downloader and the `--verify` provenance check |
| `manifest.csv` | **yes** | 9 rows in `data/manifest.csv`'s own format, with `document_group`, `version_label` and `supersedes` filled in |
| `fetch-log.csv` | **yes** | What each request returned: status, bytes, SHA-256, page count |
| `raw/` | no (git-ignored) | The 9 PDFs |
| `processed/` | no (git-ignored) | Extraction, cleaning and both segmentations per version — regenerable |

### What is human-asserted, and what is not

`title`, `document_group`, `version_label` and `supersedes` are **human
decisions** (`data/README.md` §4) — `supersedes` especially: a person read the
PDF's opening paragraph and recorded which issue it names. That is the whole
point of D-9, and it is why `backend/app/db/loader.link_supersedes` resolves
the label rather than inferring the relationship from titles.

`reference_no` and `publication_date` were read off page 1 of each downloaded
PDF and are recorded in `sources.py` so `--verify` can cross-check them.
`sha256` is never written in `sources.py`: it is computed from the bytes that
actually arrive.

---

## 5. Which database it is loaded into

**`niyamsetu_compare`, a third database, created for this corpus.**

`niyamsetu` holds the verified 4-document corpus every number in
`backend/docs/final-backend-evaluation.md` was measured on.
`niyamsetu_sample` holds the 17-document corpus the Library & Search relevance
numbers were measured on. Adding nine documents to either would make a
published measurement unreproducible.

`backend/scripts/setup_compare_database.py` refuses any database whose name
does not contain `compare`, issues `CREATE DATABASE` and nothing destructive,
and loads idempotently. The development corpus is **re-loaded from the same
JSON artefacts** into the new database — a copy, not a move — so a comparison
can be demonstrated against the familiar four documents as well.

Verified after the build: `niyamsetu` still 4 documents / 4 versions / 57 pages
/ 501 segments, `niyamsetu_sample` still 17 / 17 / 148 / 1,503. Both byte-for-byte
as they were, and the same two migrations with the same checksums in all three
databases.

---

## 6. Known limitations

* **Three chains, one authority.** All nine are RBI. IRDAI and NABARD
  reissue documents too, but their sites expose the current issue rather than
  an archive, and guessing at older URLs would risk putting a file of unknown
  provenance in the corpus. D-10 is advanced, not closed.
* **One chain skips a year.** The SC/ST 2019 issue's predecessor in RBI's own
  history is the 2018 issue, which is not in this corpus; the declared link
  therefore runs 2017 → 2019. `consecutive` in the API means *consecutive among
  the ingested versions*, not in RBI's publication history, and the
  `supersedes_evidence` for that row says so.
* **No clause-level annotation.** These documents prove Compare works at
  scale on real policy text. They cannot prove a *specific* alignment is
  correct, because nobody has marked up 288 pages. That is why
  `evaluation/comparison_eval.py` reports invariants and descriptive counts
  and no accuracy figure, and why the synthetic fixtures in
  `backend/tests/version_pair_builder.py` exist alongside.
* **The Lead Bank Scheme annexures are tables.** Segmentation treats their
  rows as clauses (D-12 is still open), so the large added/removed counts on
  that chain are partly table rows moving rather than policy changing.
