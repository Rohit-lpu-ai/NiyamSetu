# `compare_preview/` — temporary Compare testing interface

**This is a developer tool, not the product.** It is not Person 4's Compare
interface and not Person 2's frontend (`CLAUDE.md` §5.4, §7). D-1 still gates
`frontend/`, so this lives at the repository root beside `search_preview/` and
`preview_app/`, and deleting the directory affects nothing but itself. Nothing
in `backend/` imports it, and a test asserts that.

It exists to answer one question by hand, in a browser: **does Compare
actually work on real policy documents?**

---

## Run it

Three terminals, or two if the backend is already up.

```
# 1. the backend, pointed at the version-pair corpus
.venv\Scripts\python -m backend.scripts.setup_compare_database      # once
set NIYAMSETU_DB_NAME=niyamsetu_compare
.venv\Scripts\python -m uvicorn backend.app.api.main:app --port 8010

# 2. this preview
.venv\Scripts\python compare_preview/serve.py --port 5174 --api http://127.0.0.1:8010
```

Then open <http://127.0.0.1:5174>.

`NIYAMSETU_DB_NAME=niyamsetu_compare` is the important part: the development
database `niyamsetu` and the search sample `niyamsetu_sample` both have **zero
version pairs**, so pointed at either of those this page correctly shows its
empty state and there is nothing to compare.

---

## What it does

1. **Choose a document and two issues.** Only documents with two or more
   successfully extracted versions are offered, read from
   `GET /api/compare/options`. The note under the picker says, in words,
   whether the order of the pair is *declared by the issuing authority* (the
   newer issue's own metadata names the older one as the version it
   supersedes) or merely inferred from publication dates.
2. **Run the comparison** — `POST /api/compare`.
3. **Read the summary**: added / removed / modified / unchanged, plus
   ambiguous, renumbered and reordered counts, the clause counts on each side,
   and every threshold that produced the result.
4. **Read the changes side by side**, with a word-level diff: deletions struck
   through, insertions underlined, both coloured *and* decorated so the diff
   survives colour blindness.
5. **Filter** by change type, confidence and similarity range, or show only
   the matches the aligner was unsure about. Every filter change **re-runs the
   comparison on the backend** rather than hiding rows in the browser, so what
   is on screen is always a real API response.
6. **Open the evidence** for either side: the document, version and clause id,
   the pages, the character offsets, which page column those offsets index
   into, and the verbatim text sliced out of each page — or, when the slice did
   not verify, the reason instead of a quote.

---

## Three rules the page keeps

**Nothing is hardcoded.** There is no sample comparison, no sample clause and
no sample version pair anywhere in `app.js`. If the backend is down the page
says so; if the corpus has no version pairs it says *that*, with the corpus
counts, rather than offering a pair that does not exist. A test greps the
source for version, document and clause ids to keep it that way.

**Document text is never markup.** Clause text comes out of a government PDF.
Every insertion uses `textContent` or `document.createTextNode`, and the
word-level diff builds real `<span>` elements with the text inside them rather
than concatenating a string of tags. `innerHTML` does not appear in the file.

**Uncertainty is visible.** A low-confidence match, an ambiguous pairing and
evidence that did not resolve are each rendered distinctly, because §5.4
forbids presenting a speculative change as a verified fact. A quote is shown
only when the backend verified it; otherwise the page shows the problem.

---

## Files

| File | What it is |
|---|---|
| `index.html` | The page. One `<script>`, one stylesheet, no CDN |
| `app.js` | All the behaviour. No framework, no build step |
| `styles.css` | Plain CSS. Palette shared with `search_preview/` on purpose |
| `serve.py` | A 170-line standard-library server that also proxies `/api` |
| `check_render.mjs` | Optional: runs `app.js` against the live API under a DOM shim |

### Why `serve.py` rather than `python -m http.server`

`http.server` would serve the three static files, and the backend does send
CORS headers for the local dev ports. This exists for the case where that is
not enough — a different port, a different host, a browser that refuses the
preflight — and proxying `/api` through the same origin removes cross-origin
from the picture. **It also forwards POST**, which `search_preview/serve.py`
does not need and this page cannot work without: `POST /api/compare` is how a
comparison runs.

It is a development tool and behaves like one: loopback only, POST only under
`/api/`, a 64 KB body cap, `Cache-Control: no-store`, and the API's error
envelope passed through unchanged so the page can show the real code.

### `check_render.mjs`, and the honest limit of this preview

**Browser click-through has now been performed**, on 2026-11-14, in Brave
with the Claude in Chrome extension connected. The version picker, Compare,
the summary, the side-by-side word diff, the change filters, the ambiguity
filter and the evidence drawer (including a clause spanning two pages, with
both spans quoted) were all driven by hand. **Three defects were found that no
automated check had caught**, and all three were fixed and re-verified in the
browser — see `backend/docs/COMPARISON_IMPLEMENTATION_REPORT.md` §9.1.

Two things the browser session could **not** verify: the window would not
resize (the `resize_window` tool reported success while `window.innerWidth`
stayed at 1536), so **responsive layout at a narrow viewport is untested**;
and no screen reader was used, so accessibility is unverified.

`check_render.mjs` remains useful as the fast, repeatable check, and runs
`app.js` against a **real response from the running backend**, through a
minimal DOM shim built from Node's standard library, and asserts what the page
actually builds: that the picker fills from the API, that the request carries
the right filters, that the summary renders the counts and the similarity
caveat, that the word diff produces insertion and deletion spans whose text is
in text nodes, that the evidence drawer shows one quote block per page a clause
touches, that filtering re-runs on the backend, and that an impossible filter
produces the honest empty state.

```
node compare_preview/check_render.mjs http://127.0.0.1:8010
```

**55 checks, 0 failed** on the nine-document version-pair corpus.

It is not a browser and does not claim to be: there is no layout, no CSS, no
event loop, no real pointer, and therefore no evidence about whether the page
*looks* right or whether a click lands where it should. Two of its own early
failures were gaps in the shim rather than bugs in the page — a `<select>`
whose `value` did not follow `selectedIndex`, and the `method` options that
`index.html` declares and the shim had not supplied — and both are recorded in
comments where they happened, because a harness that silently blames the code
is worse than no harness.

`backend/tests/test_compare_preview.py` covers the same rules as source checks,
so they are enforced by the ordinary pytest suite with no Node and no server.
