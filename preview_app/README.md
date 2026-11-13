# Developer preview frontend

> **This is not the product frontend.** Person 2 owns the real UI
> (`CLAUDE.md` §5, §7). This is an internal tool so Person 1 can see what the
> backend and the document-processing pipeline actually produce, on screen,
> during development.
>
> It is built to be thrown away. Nothing in `backend/` imports from here, and
> deleting this directory affects nothing — not the pipeline, not the tests
> (they skip), not the evaluation.

---

## 1. Running it

From the **repository root**:

```bash
# once: Streamlit is a preview-only dependency, not a backend one
.venv\Scripts\python.exe -m pip install -r preview_app/requirements.txt

# then, every time
.venv\Scripts\python.exe -m streamlit run preview_app/app.py
```

It opens on <http://localhost:8501>. On macOS/Linux use
`.venv/bin/python` instead of `.venv\Scripts\python.exe`.

Run it from the repository root, not from inside `preview_app/` — the app
inserts the repository root on `sys.path` so `backend` and `preview_app` both
import, and it resolves `data/` and `evaluation/` relative to that root.

### It will look empty at first, and that is correct

`data/raw/` and `data/processed/` are **git-ignored** (`data/README.md` §1), so
a fresh clone has no documents to show. Every screen then renders an empty state
naming the command that would fill it. To get data:

```bash
# 1. Put the four PDFs in data/raw/ under the names data/manifest.csv records.
#    Download them by hand from each row's source_url — see data/README.md §9
#    for the Referer-header quirk on rbidocs.rbi.org.in.

# 2. Extract
python -m backend.app.document_processing.cli \
    --manifest data/manifest.csv --verify-checksums

# 3. Segment (both methods, plus the header/footer cleaning report)
python -m backend.app.document_processing.segmentation_cli --all

# 4. Optional — the three experiment screens stay empty without these
python -m evaluation.experiment_a_extractor_comparison --manifest data/manifest.csv
python -m evaluation.experiment_b_segmentation score
python -m evaluation.data_quality
```

After regenerating anything, press **Reload artefacts from disk** in the
sidebar. The loader is cached per process because Streamlit re-runs the whole
script on every interaction, and without the cache each click would re-parse
several megabytes of JSON.

---

## 2. Screens

| Screen | What it shows |
|---|---|
| **Dashboard** | Corpus and segmentation counts, per-version processing status, the Experiment C summary, and every available version. Also lists manifest rows with no processed output, so the document count is never mistaken for the corpus the manifest describes. |
| **Document library** | Each document with its manifest metadata — title, issuing authority, reference number, publication date, version, page and segment counts. Filters by authority and type. Full-text search across every document, with matches highlighted in context. |
| **Document viewer** | Page-by-page text in all three variants: **cleaned** (what segmentation ran on), **normalised** (cleaned's input), **original** (byte-for-byte what pypdf returned, never modified). Plus extraction warnings, per-page counts, find-within-page, and the lines cleaning removed from that page with the evidence for each. |
| **Segmentation explorer** | The same document under both methods. Clause number, heading, segment type, page range, nesting depth, character offsets, parent pointer and the ambiguity flag, with hierarchy shown by indentation and a coloured left border. A side-by-side table compares the two methods on the selected document. |
| **Header/footer cleaning** | The removal audit: detection parameters used, **every** candidate line considered (removed or not, so a near-miss is visible), and every removed line with its reason and evidence. Shows prominently whether removal is reversible. |
| **Extractor comparison (A)** | Experiment A's results — per document, per condition: characters, words, glyph loss, spacing artefacts, flattened-table pages, word-recovery ratio and timing. Plus the reading-order agreement matrix and the table-API findings. |
| **Evaluation (B and C)** | Experiment B's boundary scores per document and pooled, with its caveats shown as a banner rather than a footnote, and Experiment C's findings grouped by severity. |

---

## 3. Design rules this preview follows

These are the constraints that make it trustworthy to look at, and they are
the same rules the rest of the project runs on.

1. **No fabricated data, anywhere.** Every number is either a count of records
   loaded from disk or a value copied verbatim from a committed
   `results.json`. The preview computes no metric of its own. There is no
   sample dataset and no fallback — if the pipeline has not run, the screen is
   empty.
2. **Absent is visibly different from zero.** A metadata field nobody recorded
   shows as *“Not available”*, never as a blank cell and never as a guess
   (`data/README.md` §4, "empty is better than wrong"). A rate with no
   denominator stays `None` all the way to the screen rather than becoming
   `0.0`, because *“no heading to preserve”* and *“preserved no headings”* are
   different findings.
3. **Empty states name the command.** "Nothing here" is a frequent and correct
   state, so it must always be distinguishable from a bug and must tell the
   developer what to run.
4. **Missing and corrupt are different.** A missing artefact is an empty state.
   An artefact that exists but will not parse is an error, shown with the file
   named. One corrupt optional file does not blank the version — the page text
   still displays and the error is listed.
5. **No fake controls.** Every button and filter does something. Streamlit
   cannot render a PDF inline, so the viewer offers the source PDF for download
   rather than showing a non-functional embedded viewer.
6. **Read-only.** The data layer has no write path. No amount of clicking can
   alter a file under `data/` or `evaluation/`.
7. **Every value is HTML-escaped.** The screens render real government document
   text inside raw-HTML blocks, and that text contains `&`, `<` and quotation
   marks. Escaping is a correctness requirement, not a nicety, and it has its
   own tests.

---

## 4. Layout

```text
preview_app/
├── README.md               # this file
├── requirements.txt        # streamlit only, deliberately separate
├── app.py                  # routing and chrome; no data access
└── components/
    ├── data_access.py      # THE ONLY module that reads a file
    ├── screens.py          # the seven screens; render only
    └── styles.py           # CSS and small presentation helpers
```

`data_access.py` is the single seam. Screens receive a loaded `ProjectData` and
call its methods; none of them opens a file. That is what keeps a screen from
accidentally inventing a figure, and it means there is exactly **one** file to
change when Phase 4's FastAPI endpoints replace the JSON files as the data
source — `ProjectData` keeps its shape and the screens do not move.

---

## 5. Tests

Preview tests live with the rest of the suite, in
`backend/tests/test_preview_app.py`, and run with it:

```bash
.venv\Scripts\python.exe -m pytest backend/tests/test_preview_app.py -q
```

Two kinds:

- **Data-layer tests** always run. They assert the rules in §3: absent
  artefacts are empty states, corrupt ones are errors, a missing metadata field
  becomes *“Not available”*, a missing evaluation becomes an empty list and
  never a zero, and the escaping helpers cannot be bypassed.
- **Render tests** use `streamlit.testing.v1.AppTest` to run the app headlessly
  and assert that each of the seven screens renders without raising. They
  **skip** when Streamlit is not installed, which is why the backend suite still
  passes in an environment that has only `pypdf` and `pytest`.

The render tests run against the repository's actual artefacts when present, and
against the empty state when not. Both must render.

---

## 6. Deliberate limitations

Worth knowing before reading anything into what you see.

- **Search is a plain case-insensitive substring match** over normalised page
  text. It is not Person 5's keyword-vs-semantic search and carries no
  relevance ranking. It also inherits an extraction defect: pypdf sometimes
  inserts a space inside a word (`'M inimum'`, Experiment A §4), so a word can
  be present and visible yet unfindable by its correct spelling. The Library
  screen says so where no match is found.
- **No PDF is rendered inline.** Streamlit cannot, so the viewer offers the
  file for download. The PDF is often absent anyway, since `data/raw/` is
  git-ignored.
- **The document title shown is the manifest's**, not anything read out of the
  PDF. All four sample documents ship an empty `/Title`
  (`evaluation/2026-10-28-phase1-extraction-validation.md` §4.8).
- **Devanagari in the letterhead displays mangled**, because that is how it was
  extracted. The preview shows what the pipeline produced, deliberately,
  including its defects — see Experiment A §3 for what is actually happening to
  those characters.
- **Experiment B's numbers carry a conflict of interest**, shown as a banner on
  the Evaluation screen: the annotations were produced by the same assistant
  that wrote the segmenter. The full caveats are in
  `evaluation/experiment-b-segmentation.md` §0.
- **No version comparison screen.** Compare is Person 4's feature, and the
  corpus contains zero genuine version pairs anyway (`CLAUDE.md` D-10), so
  there would be nothing to show.
- **It reads files, not an API.** The FastAPI layer does not exist yet
  (Phase 4). When it does, `data_access.py` is the file that changes.
