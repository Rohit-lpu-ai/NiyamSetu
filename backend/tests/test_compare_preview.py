"""The Compare preview's integrity, checked as source. No browser, no Node.

``compare_preview/check_render.mjs`` exercises the page against a live
backend and is the stronger check, but it needs Node and a running server.
These tests need neither, so the properties that must never regress are
covered by the ordinary suite:

* the page is a **developer tool** and says so, in a place a reader will see;
* **nothing is hardcoded** — no sample comparison, no clause text, no
  fabricated version pair;
* clause text reaches the DOM as a **text node**, never as markup, because the
  text comes out of a government PDF;
* the proxy forwards to **loopback only**, and POST only under ``/api/``;
* the preview is **self-contained**: no npm, no CDN, no framework, and nothing
  in ``backend/`` imports it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PREVIEW = Path("compare_preview")
APP_JS = PREVIEW / "app.js"
INDEX = PREVIEW / "index.html"
STYLES = PREVIEW / "styles.css"
SERVE = PREVIEW / "serve.py"


@pytest.fixture(scope="module")
def app_js() -> str:
    if not APP_JS.exists():
        pytest.skip(f"{APP_JS} is absent")
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    if not INDEX.exists():
        pytest.skip(f"{INDEX} is absent")
    return INDEX.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Remove /* */ and // comments, so a rule is not satisfied by a comment."""
    without_block = re.sub(r"/\*[\s\S]*?\*/", "", source)
    return re.sub(r"(?m)^\s*//.*$", "", without_block)


# --------------------------------------------------------------------------- #
# It is a developer tool, and says so
# --------------------------------------------------------------------------- #


def test_the_page_says_it_is_not_the_product_frontend(index_html):
    assert "NOT" in index_html
    assert "product" in index_html.lower()
    assert "D-1" in index_html


def test_the_first_comment_in_app_js_says_the_same(app_js):
    head = app_js[:600]
    assert "NOT" in head
    assert "developer frontend" in head


def test_the_footer_warns_about_unverified_changes(index_html):
    """A low-confidence row must carry a visible caution, not only a colour.

    Whitespace is collapsed before matching: the phrase is line-wrapped in the
    source, and a test that depended on where the line broke would fail on the
    next reflow for no reason.
    """
    assert "footer" in index_html
    flat = " ".join(index_html.split())
    assert "checked against the source PDF" in flat


def test_the_page_states_that_descriptions_are_not_legal_advice(index_html):
    assert "never" in index_html.lower()
    assert "legal effect" in index_html.lower()


# --------------------------------------------------------------------------- #
# Nothing is hardcoded
# --------------------------------------------------------------------------- #


def test_no_version_or_clause_id_appears_as_data(app_js):
    """A sample payload left in the page would make a broken backend look fine."""
    body = _strip_comments(app_js)
    assert not re.search(r"VER-\d{4}", body), "a version id appears in the page source"
    assert not re.search(r"CLS-\d{4,}", body), "a clause id appears in the page source"
    assert not re.search(r"DOC-\d{4}", body), "a document id appears in the page source"


def test_no_comparison_fixture_is_embedded(app_js):
    body = _strip_comments(app_js)
    for marker in ('"change_type":', "'change_type':", '"similarity":'):
        assert marker not in body, f"{marker} suggests an embedded payload"


def test_every_metric_shown_comes_from_the_payload(app_js):
    """The counts are read off summary.counts rather than recomputed."""
    body = _strip_comments(app_js)
    assert "payload.summary.counts" in body
    assert "payload.summary.similarity_mean_note" in body


def test_the_similarity_caveat_is_taken_from_the_api_not_rewritten(app_js):
    """The backend owns that sentence; the page must not paraphrase it."""
    body = _strip_comments(app_js)
    assert "similarity_mean_note" in body
    assert "legal-equivalence" not in body, (
        "the page writes its own version of the caveat; it must render the "
        "backend's, so the two cannot drift"
    )


# --------------------------------------------------------------------------- #
# Document text is never markup
# --------------------------------------------------------------------------- #


def test_the_page_never_assigns_innerhtml(app_js):
    body = _strip_comments(app_js)
    assert "innerHTML" not in body
    assert "outerHTML" not in body
    assert "insertAdjacentHTML" not in body
    assert "document.write" not in body


def test_the_page_never_evaluates_a_string(app_js):
    body = _strip_comments(app_js)
    assert not re.search(r"\beval\s*\(", body)
    assert "new Function(" not in body


def test_clause_text_is_inserted_as_text(app_js):
    body = _strip_comments(app_js)
    assert "createTextNode" in body
    assert "textContent" in body


def test_the_word_diff_builds_elements_rather_than_a_string_of_tags(app_js):
    """A diff concatenated as HTML would execute whatever the PDF contained."""
    body = _strip_comments(app_js)
    diff = body[body.index("function renderDiff") : body.index("function renderSide")]
    assert "createTextNode" in diff
    assert "<span" not in diff


# --------------------------------------------------------------------------- #
# Uncertainty is shown
# --------------------------------------------------------------------------- #


def test_the_page_renders_the_evidence_status(app_js):
    body = _strip_comments(app_js)
    assert "evidence.status" in body
    assert "resolved" in body


def test_the_page_distinguishes_an_ambiguous_match(app_js):
    body = _strip_comments(app_js)
    assert "ambiguous" in body
    assert "runner_up_clause_id" in body


def test_the_page_renders_renumbered_and_reordered_flags(app_js):
    body = _strip_comments(app_js)
    assert "renumbered" in body
    assert "reordered" in body


def test_the_page_shows_a_confidence_label(app_js):
    body = _strip_comments(app_js)
    assert "confidence" in body
    assert "confidencePillKind" in body


def test_a_quote_is_only_shown_when_the_span_resolved(app_js):
    """The backend nulls an unverified quote; the page must not invent one."""
    body = _strip_comments(app_js)
    assert "span.quote" in body
    assert "could not be sliced" in body


def test_the_warnings_from_the_api_are_rendered(app_js):
    body = _strip_comments(app_js)
    assert "payload.warnings" in body
    assert "same_document" in body


# --------------------------------------------------------------------------- #
# The empty state is honest
# --------------------------------------------------------------------------- #


def test_no_version_pair_produces_an_explanation_not_an_invention(app_js):
    body = _strip_comments(app_js)
    assert "no document has two issues" in body
    assert "No pair is invented" in body


def test_an_unreachable_backend_is_reported(app_js):
    body = _strip_comments(app_js)
    assert "backend unreachable" in body
    assert "uvicorn" in body, "the page should say how to start the backend"


def test_an_api_error_envelope_is_surfaced_with_its_code(app_js):
    body = _strip_comments(app_js)
    assert "envelope.code" in body and "envelope.message" in body


# --------------------------------------------------------------------------- #
# Self-contained
# --------------------------------------------------------------------------- #


def test_there_is_no_build_step_and_no_package_manifest():
    for name in ("package.json", "package-lock.json", "node_modules", "yarn.lock"):
        assert not (PREVIEW / name).exists(), name


def test_the_page_loads_no_remote_script_or_stylesheet(index_html):
    """No element may fetch anything off this machine.

    Checked on the **attribute values that cause a fetch** (``src``/``href``)
    rather than on any occurrence of ``http`` in the file. The blunter version
    of this test failed on the inline SVG favicon, whose ``xmlns`` is an XML
    namespace identifier and not a URL the browser ever requests — a false
    positive. Narrowing it to real fetch points keeps the rule and drops the
    noise.
    """
    fetches = re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', index_html)
    assert fetches, "no src/href attributes found at all — parse problem"
    for target in fetches:
        if target.startswith("data:"):
            continue  # inlined, no request
        assert "://" not in target or target.startswith("http://127.0.0.1"), target
    assert index_html.count("<script") == 1
    assert 'src="app.js"' in index_html
    assert 'href="styles.css"' in index_html


def test_the_page_inlines_its_favicon(index_html):
    """A missing favicon is a 404 on every page load; seen in the browser."""
    icons = re.findall(r'<link[^>]*rel="icon"[^>]*href="([^"]*)"', index_html)
    assert len(icons) == 1, icons
    assert icons[0].startswith("data:image/svg+xml,"), "the icon must not be a request"


def test_the_preview_is_exactly_the_expected_files():
    present = {path.name for path in PREVIEW.iterdir() if path.is_file()}
    assert present == {"index.html", "app.js", "styles.css", "serve.py", "README.md", "check_render.mjs"}, present


def test_nothing_in_the_backend_imports_the_preview():
    """A developer tool must not become a dependency of the application."""
    for path in Path("backend/app").rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "compare_preview" not in body, path


# --------------------------------------------------------------------------- #
# The proxy
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def serve_source() -> str:
    if not SERVE.exists():
        pytest.skip(f"{SERVE} is absent")
    return SERVE.read_text(encoding="utf-8")


def test_the_proxy_forwards_to_loopback_only(serve_source):
    assert "ALLOWED_BACKEND_HOSTS" in serve_source
    assert "refusing to proxy" in serve_source


def test_the_loopback_guard_actually_rejects_a_remote_backend():
    """Not just the message: the code path returns non-zero."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("compare_preview_serve", SERVE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.main(["--api", "http://example.com"]) == 2
    assert module.ALLOWED_BACKEND_HOSTS == {"127.0.0.1", "localhost", "[::1]"}


def test_the_proxy_forwards_post_only_under_api(serve_source):
    assert "def do_POST" in serve_source
    assert 'startswith("/api/")' in serve_source


def test_the_proxy_caps_the_request_body(serve_source):
    assert "MAX_BODY_BYTES" in serve_source


def test_the_proxy_passes_the_error_envelope_through_unchanged(serve_source):
    """The page must be able to show the API's real code and message."""
    assert "HTTPError" in serve_source
    assert "unchanged" in serve_source


def test_the_proxy_sends_no_store(serve_source):
    """A stale diff looks exactly like a wrong diff."""
    assert "no-store" in serve_source


def test_the_proxy_adds_no_dependency(serve_source):
    """Standard library only; the preview must not reach requirements.txt."""
    imports = re.findall(r"(?m)^(?:import|from)\s+([\w.]+)", serve_source)
    allowed = {
        "__future__", "argparse", "sys", "urllib", "urllib.error",
        "urllib.parse", "urllib.request", "functools", "http.server",
        "pathlib",
    }
    assert set(imports) <= allowed, set(imports) - allowed


def test_the_preview_uses_a_different_port_from_the_search_preview(serve_source):
    """Both must be able to run at once, which is how a regression is spotted."""
    assert "5174" in serve_source
    search = Path("search_preview/serve.py")
    if search.exists():
        assert "5173" in search.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The optional Node render check
# --------------------------------------------------------------------------- #


def test_the_render_check_exists_and_declares_what_it_is_not():
    path = PREVIEW / "check_render.mjs"
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "not a browser" in body
    assert "no npm" in body


def test_the_render_check_needs_no_packages():
    """It must run on a bare Node, or nobody will run it."""
    body = (PREVIEW / "check_render.mjs").read_text(encoding="utf-8")
    imports = re.findall(r"from\s+'([^']+)'", body)
    assert all(name.startswith("node:") for name in imports), imports


# --------------------------------------------------------------------------- #
# Regression: the result on screen must match the controls
# --------------------------------------------------------------------------- #


def test_a_filter_change_during_a_request_is_queued_not_dropped(app_js):
    """Found in a browser: two quick filter changes showed a stale result.

    The old code returned early whenever a request was in flight, so the
    second change never reached the backend and the page showed the first
    result under the second set of controls — 60 of 65 changes with all four
    change types ticked.
    """
    body = _strip_comments(app_js)
    assert "rerunQueued" in body
    # The bare early return is the defect; it must not come back.
    assert "if (state.running) return;" not in body


def test_a_superseded_response_cannot_render(app_js):
    """A slow earlier request must not overwrite a faster later one."""
    body = _strip_comments(app_js)
    assert "requestSeq" in body
    assert "seq !== state.requestSeq" in body


def test_changing_the_selection_invalidates_the_displayed_comparison(app_js):
    """Found in a browser: switching document left the old diff on screen.

    The picker read DOC-0006 while the results pane still showed DOC-0003's
    comparison, with nothing marking it stale.
    """
    body = _strip_comments(app_js)
    assert "function invalidateComparison" in body
    for control in ("document", "base-version", "compare-version"):
        assert control in body
    assert "selection changed" in body


def test_a_filter_change_re_runs_rather_than_invalidating(app_js):
    """A filter still describes the same pair, so it re-requests."""
    body = _strip_comments(app_js)
    assert ".ctype, .conf" in body
    assert "runComparison(0)" in body


def test_the_version_head_labels_the_reference_as_document_level(app_js):
    body = _strip_comments(app_js)
    assert "document_reference_no" in body
    assert "'document ref'" in body
