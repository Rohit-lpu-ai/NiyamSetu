"""Shared CSS and small presentation helpers for the preview.

Design intent: a plain, legible government-document dashboard. Generous
whitespace, a restrained palette, and a serif face for document text so
extracted policy prose reads as a document rather than as UI copy.

The palette is defined once as CSS custom properties so the whole preview
changes from one place, and it is declared on ``:root`` with a
``prefers-color-scheme`` override, so a developer running Streamlit in dark mode
gets readable text instead of black-on-black.

Nothing here is product design. Person 2 owns the real UI; this is a tool for
looking at pipeline output.
"""

from __future__ import annotations

from typing import Optional

#: Severity colours, reused by the data-quality screen and the status pills.
SEVERITY_COLORS = {
    "error": "var(--ns-danger)",
    "warning": "var(--ns-warn)",
    "info": "var(--ns-muted)",
}

#: Extraction-status colours. `partial` and `failed` must look different from
#: `ok` at a glance - that is the whole point of showing the status.
STATUS_COLORS = {
    "ok": "var(--ns-ok)",
    "partial": "var(--ns-warn)",
    "failed": "var(--ns-danger)",
    "pending": "var(--ns-muted)",
}

#: Segment-type colours, so the Segmentation Explorer can be scanned by shape
#: rather than read line by line.
SEGMENT_TYPE_COLORS = {
    "clause": "var(--ns-accent)",
    "paragraph": "var(--ns-muted)",
    "heading": "var(--ns-ok)",
    "annexure": "var(--ns-warn)",
    "preamble": "var(--ns-muted)",
    "unclassified": "var(--ns-danger)",
}

CSS = """
<style>
:root {
  --ns-bg: #f7f8fa;
  --ns-panel: #ffffff;
  --ns-border: #e2e6ec;
  --ns-text: #1b2430;
  --ns-muted: #6b7684;
  --ns-accent: #1f4e79;
  --ns-accent-soft: #eaf1f8;
  --ns-ok: #1f7a4d;
  --ns-warn: #b06f00;
  --ns-danger: #b3261e;
  --ns-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  --ns-serif: Georgia, "Times New Roman", serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --ns-bg: #11161d;
    --ns-panel: #1a212b;
    --ns-border: #2b3542;
    --ns-text: #e7ecf3;
    --ns-muted: #9aa7b6;
    --ns-accent: #7fb0e0;
    --ns-accent-soft: #1f2b38;
    --ns-ok: #5fc38c;
    --ns-warn: #e0a23c;
    --ns-danger: #f0867e;
  }
}

/* Keep the page from feeling like a form. */
.block-container { padding-top: 2.2rem; max-width: 1180px; }

.ns-title {
  font-size: 1.85rem; font-weight: 650; letter-spacing: -0.01em;
  color: var(--ns-text); margin: 0 0 0.15rem 0;
}
.ns-subtitle { color: var(--ns-muted); font-size: 0.95rem; margin: 0 0 1.4rem 0; }

.ns-section {
  font-size: 1.05rem; font-weight: 620; color: var(--ns-text);
  margin: 1.9rem 0 0.7rem 0; padding-bottom: 0.35rem;
  border-bottom: 1px solid var(--ns-border);
}

/* Stat tiles. One number, one label, no sparkline - every figure here is a
   count of something on disk, and a trend line would imply history we do not
   have. */
.ns-stats { display: flex; flex-wrap: wrap; gap: 0.8rem; margin-bottom: 0.5rem; }
.ns-stat {
  flex: 1 1 150px; background: var(--ns-panel);
  border: 1px solid var(--ns-border); border-radius: 10px;
  padding: 0.85rem 1rem;
}
.ns-stat-value {
  font-size: 1.7rem; font-weight: 650; line-height: 1.15;
  color: var(--ns-accent);
}
.ns-stat-label {
  font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--ns-muted); margin-top: 0.2rem;
}
.ns-stat-note { font-size: 0.76rem; color: var(--ns-muted); margin-top: 0.3rem; }

.ns-card {
  background: var(--ns-panel); border: 1px solid var(--ns-border);
  border-radius: 10px; padding: 1rem 1.15rem; margin-bottom: 0.8rem;
}
.ns-card-title {
  font-weight: 620; color: var(--ns-text); margin-bottom: 0.15rem;
  font-size: 1.02rem;
}
.ns-card-meta { color: var(--ns-muted); font-size: 0.84rem; line-height: 1.6; }

.ns-pill {
  display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
  font-size: 0.72rem; font-weight: 600; letter-spacing: 0.02em;
  border: 1px solid currentColor; margin-right: 0.3rem; white-space: nowrap;
}

.ns-tag {
  display: inline-block; padding: 0.05rem 0.4rem; border-radius: 4px;
  font-family: var(--ns-mono); font-size: 0.72rem;
  background: var(--ns-accent-soft); color: var(--ns-accent);
  margin-right: 0.3rem;
}

.ns-na { color: var(--ns-muted); font-style: italic; }

/* Document text. Serif, generous line height, and it must wrap rather than
   scroll horizontally. */
.ns-doc {
  font-family: var(--ns-serif); font-size: 0.95rem; line-height: 1.75;
  color: var(--ns-text); white-space: pre-wrap; word-wrap: break-word;
  background: var(--ns-panel); border: 1px solid var(--ns-border);
  border-left: 3px solid var(--ns-accent);
  border-radius: 8px; padding: 1rem 1.15rem;
  max-height: 32rem; overflow-y: auto;
}
.ns-raw {
  font-family: var(--ns-mono); font-size: 0.8rem; line-height: 1.6;
  white-space: pre-wrap; word-wrap: break-word;
  background: var(--ns-panel); border: 1px solid var(--ns-border);
  border-radius: 8px; padding: 0.9rem 1rem;
  max-height: 32rem; overflow-y: auto; color: var(--ns-text);
}

/* A segment row in the Segmentation Explorer. The left border width encodes
   nesting depth, so hierarchy is visible without an indent guide. */
.ns-seg {
  background: var(--ns-panel); border: 1px solid var(--ns-border);
  border-radius: 8px; padding: 0.7rem 0.9rem; margin-bottom: 0.5rem;
}
.ns-seg-head {
  display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline;
  margin-bottom: 0.35rem;
}
.ns-seg-number {
  font-family: var(--ns-mono); font-weight: 650; color: var(--ns-accent);
  font-size: 0.86rem;
}
.ns-seg-heading { font-weight: 600; color: var(--ns-text); font-size: 0.9rem; }
.ns-seg-text {
  font-family: var(--ns-serif); font-size: 0.9rem; line-height: 1.65;
  color: var(--ns-text); white-space: pre-wrap; word-wrap: break-word;
}
.ns-seg-foot {
  font-family: var(--ns-mono); font-size: 0.7rem; color: var(--ns-muted);
  margin-top: 0.4rem;
}

.ns-hit {
  border-left: 3px solid var(--ns-warn); background: var(--ns-panel);
  border: 1px solid var(--ns-border); border-left: 3px solid var(--ns-warn);
  border-radius: 6px; padding: 0.55rem 0.8rem; margin-bottom: 0.4rem;
  font-size: 0.86rem; line-height: 1.6;
}
.ns-hit mark {
  background: #ffe08a; color: #1b2430; padding: 0 0.1rem; border-radius: 2px;
}

/* Empty and error states. Both must say what to DO, not just what is wrong. */
.ns-empty {
  background: var(--ns-panel); border: 1px dashed var(--ns-border);
  border-radius: 10px; padding: 1.6rem 1.4rem; text-align: left;
  color: var(--ns-muted);
}
.ns-empty-title {
  font-weight: 620; color: var(--ns-text); font-size: 1rem;
  margin-bottom: 0.35rem;
}
.ns-empty code {
  display: block; font-family: var(--ns-mono); font-size: 0.8rem;
  background: var(--ns-accent-soft); color: var(--ns-accent);
  padding: 0.6rem 0.8rem; border-radius: 6px; margin-top: 0.7rem;
  white-space: pre-wrap; word-wrap: break-word;
}

.ns-note {
  font-size: 0.82rem; color: var(--ns-muted); line-height: 1.6;
  border-left: 2px solid var(--ns-border); padding-left: 0.8rem;
  margin: 0.6rem 0 1rem 0;
}

/* Wide tables must scroll inside their own container, never the page. */
[data-testid="stDataFrame"] { border-radius: 8px; }
</style>
"""


def pill(text: str, color: str = "var(--ns-muted)") -> str:
    """A small coloured capsule, for a status or a count."""
    return f'<span class="ns-pill" style="color:{color}">{escape(text)}</span>'


def tag(text: str) -> str:
    """A monospace inline tag, for identifiers."""
    return f'<span class="ns-tag">{escape(text)}</span>'


def stat(value: object, label: str, note: Optional[str] = None) -> str:
    """One stat tile. ``value`` is always a real count from an artefact."""
    note_html = f'<div class="ns-stat-note">{escape(note)}</div>' if note else ""
    return (
        '<div class="ns-stat">'
        f'<div class="ns-stat-value">{escape(str(value))}</div>'
        f'<div class="ns-stat-label">{escape(label)}</div>'
        f"{note_html}"
        "</div>"
    )


def stats_row(tiles: list[str]) -> str:
    return f'<div class="ns-stats">{"".join(tiles)}</div>'


def escape(text: object) -> str:
    """Escape for HTML.

    Needed on every value that reaches an ``unsafe_allow_html`` block, and that
    is *all* of them: this preview renders real government document text, which
    contains ``&``, ``<`` and quotation marks. Without escaping, a document
    could break the page layout — and the same hole would let any text the
    pipeline ingested inject markup.
    """
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def highlight(text: str, query: str) -> str:
    """Escape ``text``, then wrap case-insensitive matches of ``query`` in ``<mark>``.

    Order matters: escaping happens first, then the marks are inserted, so a
    document containing ``<mark>`` cannot smuggle markup through.
    """
    safe = escape(text)
    if not query.strip():
        return safe
    safe_query = escape(query)
    lowered = safe.lower()
    needle = safe_query.lower()
    out: list[str] = []
    cursor = 0
    while True:
        found = lowered.find(needle, cursor)
        if found == -1:
            out.append(safe[cursor:])
            return "".join(out)
        out.append(safe[cursor:found])
        out.append(f"<mark>{safe[found:found + len(safe_query)]}</mark>")
        cursor = found + len(safe_query)


def empty_state(title: str, message: str, command: Optional[str] = None) -> str:
    """An empty state that names the command which would fill it.

    The preview reads only real artefacts, so "nothing here" is a frequent and
    *correct* state. It must always be distinguishable from a bug, and it must
    tell the developer what to run.
    """
    command_html = f"<code>{escape(command)}</code>" if command else ""
    return (
        '<div class="ns-empty">'
        f'<div class="ns-empty-title">{escape(title)}</div>'
        f"<div>{escape(message)}</div>"
        f"{command_html}"
        "</div>"
    )


def note(text: str) -> str:
    """A quiet caveat line. Used wherever a figure needs its limitation beside it."""
    return f'<div class="ns-note">{escape(text)}</div>'
