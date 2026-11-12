"""The preview's five screens.

Each function takes an already-loaded
:class:`~preview_app.components.data_access.ProjectData` and renders it. No
screen reads a file, and no screen computes a metric — a figure on screen is
either a count of loaded records or a value copied from an evaluation
``results.json``. That constraint is what keeps the preview from becoming a
place where numbers appear that no committed script produced
(``CLAUDE.md`` §14 rule 1).

Every screen handles three states explicitly: loaded, empty (the artefact has
not been generated, with the command that would generate it) and error (the
artefact exists but could not be read). Empty is the common case in a fresh
clone, because ``data/raw/`` and ``data/processed/`` are git-ignored.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import streamlit as st

from .data_access import (
    COMMANDS,
    NOT_AVAILABLE,
    ProjectData,
    SegmentView,
    VersionView,
    data_quality_findings,
    display,
    experiment_a_table,
    experiment_b_rows,
    marker_style_table,
    removed_lines_table,
    search_pages,
    search_segments,
    segment_tree,
)
from .styles import (
    SEGMENT_TYPE_COLORS,
    SEVERITY_COLORS,
    STATUS_COLORS,
    empty_state,
    escape,
    highlight,
    note,
    pill,
    stat,
    stats_row,
    tag,
)

#: How much segment text to show before offering the rest in an expander. Long
#: enough to judge a boundary, short enough that fifty segments stay scannable.
_SEGMENT_PREVIEW_CHARS = 420


def _header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="ns-title">{escape(title)}</div>'
        f'<div class="ns-subtitle">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def _section(title: str) -> None:
    st.markdown(f'<div class="ns-section">{escape(title)}</div>', unsafe_allow_html=True)


def _no_corpus(data: ProjectData) -> bool:
    """Render the shared "pipeline has not been run" state. Returns True if shown."""
    if data.versions:
        return False
    st.markdown(
        empty_state(
            "No processed documents found",
            "data/processed/ is empty. It is git-ignored by design, so a fresh "
            "clone always starts here. Put the PDFs in data/raw/ under the "
            "names data/manifest.csv records, then run the extraction and "
            "segmentation CLIs.",
            f"{COMMANDS['extraction']}\n{COMMANDS['segments']}",
        ),
        unsafe_allow_html=True,
    )
    return True


def _status_pill(status: str) -> str:
    return pill(status, STATUS_COLORS.get(status, "var(--ns-muted)"))


# --------------------------------------------------------------------------- #
# A. Dashboard
# --------------------------------------------------------------------------- #


def render_dashboard(data: ProjectData) -> None:
    _header(
        "NiyamSetu — developer preview",
        "Pipeline output as it stands on disk. Every figure is a count of real "
        "records; nothing here is sample data.",
    )

    if data.load_errors:
        _section("Artefacts that could not be read")
        for error in data.load_errors:
            st.error(error)

    if _no_corpus(data):
        _section("What the manifest says should be here")
        if data.manifest_rows:
            st.markdown(
                f"`data/manifest.csv` records **{data.manifest_row_count}** "
                f"document(s), none of which has been processed yet."
            )
        else:
            st.markdown("`data/manifest.csv` is absent or empty too.")
        return

    segment_totals = data.segment_totals
    _section("Corpus")
    st.markdown(
        stats_row(
            [
                stat(
                    data.document_count,
                    "Logical documents",
                    "distinct document_id",
                ),
                stat(
                    data.version_count,
                    "Processed versions",
                    f"{data.versions_with_segments} segmented",
                ),
                stat(data.total_pages, "Extracted pages", f"{data.total_empty_pages} empty"),
                stat(data.manifest_row_count, "Manifest rows", "human-recorded provenance"),
            ]
        ),
        unsafe_allow_html=True,
    )

    _section("Segmentation")
    st.markdown(
        stats_row(
            [
                stat(segment_totals.get("paragraph", 0), "Paragraph segments", "baseline method"),
                stat(segment_totals.get("clause_aware", 0), "Clause-aware segments", "experimental method"),
                stat(data.page_spanning_segments, "Cross-page segments", "two page_offsets each"),
                stat(data.total_removed_lines, "Removed header lines", "reversible, with evidence"),
            ]
        ),
        unsafe_allow_html=True,
    )
    if segment_totals.get("clause_aware", 0) == 0:
        st.markdown(
            empty_state(
                "No segments yet",
                "Extraction has run but segmentation has not.",
                COMMANDS["segments"],
            ),
            unsafe_allow_html=True,
        )

    _section("Processing status")
    status_bits = [
        f"{_status_pill(status)} {count} version(s)"
        for status, count in sorted(data.status_counts.items())
    ]
    st.markdown(" &nbsp; ".join(status_bits), unsafe_allow_html=True)
    if data.total_warnings:
        st.markdown(
            f'<div class="ns-note">{data.total_warnings} extraction warning(s) '
            f"recorded across the corpus. See each document in the Viewer.</div>",
            unsafe_allow_html=True,
        )

    quality = data.data_quality
    if quality:
        totals = quality.get("totals") or {}
        errors = int(totals.get("errors") or 0)
        warnings = int(totals.get("warnings") or 0)
        _section("Data quality (Experiment C)")
        st.markdown(
            stats_row(
                [
                    stat(errors, "Errors", "broken invariants; must be zero"),
                    stat(warnings, "Warnings", "worth a look"),
                    stat(len(quality.get("checks_run") or []), "Checks run", ""),
                ]
            ),
            unsafe_allow_html=True,
        )
        if errors:
            st.error(
                f"{errors} data-quality error(s). See the Data Quality screen."
            )
        for finding in data_quality_findings(data, "warning"):
            st.warning(f"[{finding.get('check')}] {finding.get('message')}")
    else:
        _section("Data quality (Experiment C)")
        st.markdown(
            empty_state(
                "Data-quality checks have not been run",
                "Experiment C reports whether every segment carries valid page "
                "provenance, among 11 other checks.",
                COMMANDS["data_quality"],
            ),
            unsafe_allow_html=True,
        )

    _section("Available versions")
    for version in data.versions:
        segment_summary = " · ".join(
            f"{method}: {len(items)}" for method, items in sorted(version.segments.items())
        ) or "not segmented"
        st.markdown(
            '<div class="ns-card">'
            f'<div class="ns-card-title">{escape(version.title)}</div>'
            f'<div class="ns-card-meta">'
            f"{tag(version.version_id)}{tag(version.document_id)}"
            f"{_status_pill(version.extraction_status)}<br>"
            f"{escape(version.page_count)} pages · {escape(segment_summary)} · "
            f"version {escape(version.version_label)} · "
            f"{escape(version.extraction_tool)}"
            "</div></div>",
            unsafe_allow_html=True,
        )

    missing = data.manifest_rows_not_ingested
    if missing:
        st.markdown(
            f'<div class="ns-note">{len(missing)} manifest row(s) have no '
            f"processed output: {escape(', '.join(missing))}. The PDF is "
            f"probably not in data/raw/.</div>",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
# B. Document Library
# --------------------------------------------------------------------------- #


def render_library(data: ProjectData) -> None:
    _header(
        "Document library",
        "Every processed document with the metadata the manifest records. "
        f"A field nobody recorded shows as “{NOT_AVAILABLE}” rather than blank.",
    )
    if _no_corpus(data):
        return

    query = st.text_input(
        "Search the full extracted text of every document",
        placeholder="e.g. Lead Bank, leverage ratio, Scheduled Castes",
        key="library_search",
    )

    authorities = sorted(
        {version.authority for version in data.versions}
    )
    types = sorted({version.document_type for version in data.versions})
    left, right = st.columns(2)
    with left:
        authority_filter = st.selectbox(
            "Issuing authority", ["All"] + authorities, key="library_authority"
        )
    with right:
        type_filter = st.selectbox(
            "Document type", ["All"] + types, key="library_type"
        )

    visible = [
        version
        for version in data.versions
        if (authority_filter == "All" or version.authority == authority_filter)
        and (type_filter == "All" or version.document_type == type_filter)
    ]

    if query.strip():
        _section(f"Text matches for “{query}”")
        total = 0
        for version in visible:
            hits = search_pages(version, query, limit=5)
            if not hits:
                continue
            total += len(hits)
            st.markdown(
                f"**{escape(version.title)}** &nbsp; {tag(version.version_id)}",
                unsafe_allow_html=True,
            )
            for hit in hits:
                st.markdown(
                    f'<div class="ns-hit"><strong>page {hit.page_number}</strong> — '
                    f"{highlight(hit.context, query)}</div>",
                    unsafe_allow_html=True,
                )
        if total == 0:
            st.markdown(
                empty_state(
                    "No matches",
                    f"“{query}” does not occur in the extracted text of the "
                    f"{len(visible)} visible document(s). Note that pypdf "
                    f"sometimes inserts spaces inside words (Experiment A §4), "
                    f"so a word may be present but split.",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                note(
                    "Up to 5 matches shown per document. Search is a plain "
                    "case-insensitive substring match over normalised page "
                    "text — not the semantic search Person 5 is building."
                ),
                unsafe_allow_html=True,
            )

    _section(f"Documents ({len(visible)} of {data.version_count})")
    if not visible:
        st.markdown(
            empty_state(
                "No document matches these filters",
                "Reset the authority or type filter above.",
            ),
            unsafe_allow_html=True,
        )
        return

    for version in visible:
        with st.container():
            st.markdown(
                '<div class="ns-card">'
                f'<div class="ns-card-title">{escape(version.title)}</div>'
                f'<div class="ns-card-meta">'
                f"<strong>Issuing authority</strong> {escape(version.authority)}<br>"
                f"<strong>Reference</strong> {escape(version.reference_no)}<br>"
                f"<strong>Published</strong> {escape(version.publication_date)} &nbsp;·&nbsp; "
                f"<strong>Version</strong> {escape(version.version_label)} &nbsp;·&nbsp; "
                f"<strong>Type</strong> {escape(version.document_type)}<br>"
                f"<strong>Pages</strong> {escape(version.page_count)} &nbsp;·&nbsp; "
                f"<strong>Segments</strong> {escape(version.total_segments)} "
                f"across {escape(len(version.segments))} method(s)<br>"
                f"{tag(version.version_id)}{_status_pill(version.extraction_status)}"
                "</div></div>",
                unsafe_allow_html=True,
            )
            if version.source_url:
                st.markdown(f"[Official source]({version.source_url})")


# --------------------------------------------------------------------------- #
# C. Document Viewer
# --------------------------------------------------------------------------- #


def _version_picker(data: ProjectData, key: str) -> Optional[VersionView]:
    labels = {
        f"{version.title[:70]}  ·  {version.version_id}": version.version_id
        for version in data.versions
    }
    if not labels:
        return None
    chosen = st.selectbox("Document", list(labels), key=key)
    return data.version(labels[chosen])


def render_viewer(data: ProjectData) -> None:
    _header(
        "Document viewer",
        "Extracted text per page, in all three variants: raw, normalised, and "
        "cleaned of running headers.",
    )
    if _no_corpus(data):
        return

    version = _version_picker(data, "viewer_version")
    if version is None:
        return

    st.markdown(
        stats_row(
            [
                stat(version.page_count, "Pages", ""),
                stat(version.extraction_status, "Status", version.extraction_tool),
                stat(len(version.warnings), "Warnings", ""),
                stat(version.removed_line_count, "Header lines removed", "reversible"),
            ]
        ),
        unsafe_allow_html=True,
    )

    if version.warnings:
        with st.expander(f"Extraction warnings ({len(version.warnings)})"):
            for warning in version.warnings:
                st.markdown(f"- {escape(warning)}")

    pdf_path = version.raw_pdf_path
    if pdf_path:
        with st.expander("Original PDF"):
            st.caption(
                f"{pdf_path.relative_to(pdf_path.parents[2])} — "
                f"{pdf_path.stat().st_size // 1024} KB"
            )
            # Streamlit cannot render a PDF inline, so the bytes are offered
            # for download rather than shown. A fake embedded viewer would be
            # a non-functional control.
            st.download_button(
                "Open the source PDF",
                data=pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                key="viewer_pdf",
            )
    else:
        st.markdown(
            note(
                "The source PDF is not in data/raw/ (git-ignored), so it cannot "
                "be shown beside the text. Re-download it from the manifest's "
                "source_url to enable this."
            ),
            unsafe_allow_html=True,
        )

    _section("Page text")
    page_numbers = [page.page_number for page in version.pages]
    chosen_page = st.selectbox("Page", page_numbers, key="viewer_page")
    page = version.page(chosen_page)
    if page is None:
        st.error(f"Page {chosen_page} is not in the extraction output.")
        return

    variant = st.radio(
        "Text variant",
        ["Cleaned (segmented)", "Normalised", "Original (raw)"],
        horizontal=True,
        key="viewer_variant",
        help=(
            "Cleaned text is what segmentation actually ran on. Normalised is "
            "cleaned's input. Original is byte-for-byte what pypdf returned and "
            "is never modified."
        ),
    )
    query = st.text_input(
        "Find within this page", key="viewer_find", placeholder="substring"
    )

    st.markdown(
        stats_row(
            [
                stat(page.char_count, "Characters", "raw"),
                stat(page.word_count, "Words", "normalised"),
                stat("yes" if page.has_meaningful_text else "no", "Meaningful text", ""),
                stat(page.removed_line_count, "Lines removed", "on this page"),
            ]
        ),
        unsafe_allow_html=True,
    )

    if variant == "Original (raw)":
        body, css_class = page.original_text, "ns-raw"
    elif variant == "Normalised":
        body, css_class = page.normalized_text, "ns-doc"
    else:
        if page.cleaned_text is None:
            st.markdown(
                empty_state(
                    "No cleaned text for this page",
                    "Header/footer cleaning runs as part of segmentation, which "
                    "has not been run for this version.",
                    COMMANDS["segments"],
                ),
                unsafe_allow_html=True,
            )
            return
        body, css_class = page.cleaned_text, "ns-doc"

    if not body.strip():
        st.markdown(
            empty_state(
                f"Page {chosen_page} produced no text",
                "This is how the pipeline reports a scanned or image-only page. "
                "OCR is deliberately out of scope (CLAUDE.md D-6).",
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="{css_class}">{highlight(body, query)}</div>',
            unsafe_allow_html=True,
        )

    if page.removed_lines:
        with st.expander(
            f"Lines removed from page {chosen_page} ({page.removed_line_count})"
        ):
            st.caption(
                "Removal is reversible: each line is recorded with the index it "
                "came from and the evidence for removing it."
            )
            st.dataframe(
                [
                    {
                        "line": line.get("line_index"),
                        "reason": line.get("reason"),
                        "text": str(line.get("text") or "").strip(),
                        "evidence": line.get("evidence"),
                    }
                    for line in page.removed_lines
                ],
                width="stretch",
                hide_index=True,
            )


# --------------------------------------------------------------------------- #
# D. Segmentation Explorer
# --------------------------------------------------------------------------- #


def _segment_card(segment: SegmentView, depth: int, query: str) -> str:
    colour = SEGMENT_TYPE_COLORS.get(segment.segment_type, "var(--ns-muted)")
    number = (
        f'<span class="ns-seg-number">{escape(segment.clause_number)}</span>'
        if segment.clause_number
        else '<span class="ns-na">no clause number</span>'
    )
    heading = (
        f'<span class="ns-seg-heading">{escape(segment.heading)}</span>'
        if segment.heading
        else ""
    )
    flags = ""
    if segment.spans_pages:
        flags += pill("spans pages", "var(--ns-warn)")
    if segment.is_ambiguous:
        flags += pill("ambiguous number", "var(--ns-danger)")

    text = segment.text
    truncated = len(text) > _SEGMENT_PREVIEW_CHARS
    shown = text[:_SEGMENT_PREVIEW_CHARS] + ("…" if truncated else "")

    offsets = " ".join(
        f"p{offset['page_number']}:{offset['char_start']}–{offset['char_end']}"
        for offset in segment.page_offsets
    )
    parent = (
        f" · parent {escape(segment.parent_segment_id)}"
        if segment.parent_segment_id
        else " · top level"
    )

    return (
        f'<div class="ns-seg" style="margin-left:{depth * 1.3}rem;'
        f'border-left:3px solid {colour}">'
        '<div class="ns-seg-head">'
        f"{pill(segment.segment_type, colour)}"
        f"{number}{heading}{flags}"
        f'<span class="ns-seg-foot">{escape(segment.page_label)} · '
        f"level {escape(segment.level)} · depth {escape(depth)}</span>"
        "</div>"
        f'<div class="ns-seg-text">{highlight(shown, query)}</div>'
        f'<div class="ns-seg-foot">{escape(segment.segment_id)} · '
        f"ordinal {escape(segment.ordinal)} · offsets {escape(offsets)}"
        f"{parent}</div>"
        "</div>"
    )


def render_segmentation(data: ProjectData) -> None:
    _header(
        "Segmentation explorer",
        "The same document under both methods, side by side. This is where the "
        "two approaches become visibly different.",
    )
    if _no_corpus(data):
        return

    version = _version_picker(data, "seg_version")
    if version is None:
        return

    if not version.has_segments:
        st.markdown(
            empty_state(
                "This version has not been segmented",
                "Both methods run in one command and are stored side by side.",
                COMMANDS["segments"],
            ),
            unsafe_allow_html=True,
        )
        return

    methods = [method for method, items in version.segments.items() if items]
    method = st.radio(
        "Segmentation method",
        methods,
        horizontal=True,
        key="seg_method",
        format_func=lambda value: {
            "paragraph": "Paragraph (baseline)",
            "clause_aware": "Clause-aware (experimental)",
        }.get(value, value),
    )

    stats: Dict[str, object] = version.segment_stats.get(method) or {}
    tiles = [
        stat(len(version.segments_for(method)), "Segments", ""),
        stat(stats.get("page_spanning_segments", 0), "Cross-page", "two offsets each"),
        stat(stats.get("mean_chars", 0), "Mean characters", ""),
    ]
    if method == "clause_aware":
        tiles.append(stat(stats.get("max_level", 0), "Max nesting level", ""))
        tiles.append(
            stat(stats.get("ambiguous_markers", 0), "Ambiguous markers", "roman vs letter")
        )
    else:
        tiles.append(stat(stats.get("cross_page_joins", 0), "Cross-page joins", ""))
        tiles.append(stat(stats.get("blank_line_splits", 0), "Blank-line splits", ""))
    st.markdown(stats_row(tiles), unsafe_allow_html=True)

    for warning in version.segment_warnings.get(method, []):
        st.warning(warning)

    if method == "clause_aware":
        styles = marker_style_table(version)
        if styles:
            _section("Clause-marker styles detected")
            st.markdown(
                " ".join(
                    f"{tag(name)} {count}" for name, count in styles
                ),
                unsafe_allow_html=True,
            )

    _section("Compare the two methods on this document")
    comparison = []
    for candidate in methods:
        candidate_stats = version.segment_stats.get(candidate) or {}
        comparison.append(
            {
                "method": candidate,
                "segments": len(version.segments_for(candidate)),
                "cross-page": candidate_stats.get("page_spanning_segments", 0),
                "mean chars": candidate_stats.get("mean_chars", 0),
                "with clause number": sum(
                    1 for s in version.segments_for(candidate) if s.clause_number
                ),
                "with heading": sum(
                    1 for s in version.segments_for(candidate) if s.heading
                ),
                "with parent": sum(
                    1 for s in version.segments_for(candidate) if s.parent_segment_id
                ),
            }
        )
    st.dataframe(comparison, width="stretch", hide_index=True)
    st.markdown(
        note(
            "The paragraph baseline recovers no clause numbers at all, by "
            "construction. See evaluation/experiment-b-segmentation.md for what "
            "that costs and what it buys."
        ),
        unsafe_allow_html=True,
    )

    _section("Segments")
    left, right = st.columns([2, 1])
    with left:
        query = st.text_input(
            "Find within these segments", key="seg_find", placeholder="substring"
        )
    with right:
        types = sorted({s.segment_type for s in version.segments_for(method)})
        type_filter = st.selectbox("Segment type", ["All"] + types, key="seg_type")

    tree = segment_tree(version, method)
    if query.strip():
        hits = {hit.segment_id for hit in search_segments(version, method, query, limit=500)}
        tree = [(segment, depth) for segment, depth in tree if segment.segment_id in hits]
    if type_filter != "All":
        tree = [
            (segment, depth) for segment, depth in tree if segment.segment_type == type_filter
        ]

    if not tree:
        st.markdown(
            empty_state(
                "No segment matches",
                "Clear the search box or the type filter above.",
            ),
            unsafe_allow_html=True,
        )
        return

    page_size = 25
    total_pages = (len(tree) + page_size - 1) // page_size
    if total_pages > 1:
        index = st.number_input(
            f"Page of results (25 per page, {len(tree)} matching)",
            min_value=1,
            max_value=total_pages,
            value=1,
            key="seg_page",
        )
        window = tree[(int(index) - 1) * page_size : int(index) * page_size]
    else:
        window = tree

    for segment, depth in window:
        st.markdown(_segment_card(segment, depth, query), unsafe_allow_html=True)
        if len(segment.text) > _SEGMENT_PREVIEW_CHARS:
            with st.expander(f"Full text of {segment.segment_id}"):
                st.markdown(
                    f'<div class="ns-doc">{highlight(segment.text, query)}</div>',
                    unsafe_allow_html=True,
                )


# --------------------------------------------------------------------------- #
# E. Extractor Comparison (Experiment A)
# --------------------------------------------------------------------------- #


def render_extractor_comparison(data: ProjectData) -> None:
    _header(
        "Extractor comparison — Experiment A",
        "pypdf against PyMuPDF and pdfplumber on the real corpus. Every number "
        "is read from evaluation/experiment-a/results.json.",
    )

    rows = experiment_a_table(data)
    if not rows:
        st.markdown(
            empty_state(
                "Experiment A has not been run",
                "The comparison measures page coverage, text completeness, "
                "reading order, word spacing, table structure, Devanagari "
                "rendering and extraction time across five conditions.",
                COMMANDS["experiment_a"],
            ),
            unsafe_allow_html=True,
        )
        return

    payload = data.experiment_a or {}
    _section("Run")
    st.markdown(
        f"Generated **{escape(payload.get('generated_at'))}** · "
        f"Python {escape(payload.get('python'))} · "
        f"{escape(payload.get('repeats_per_document'))} timing repeat(s) per document",
        unsafe_allow_html=True,
    )
    st.markdown(
        " ".join(
            f"{tag(backend.get('name'))} {escape(backend.get('tool_version'))}"
            for backend in payload.get("backends_compared") or []
        ),
        unsafe_allow_html=True,
    )
    unavailable = payload.get("backends_unavailable") or {}
    if unavailable:
        st.warning(
            "Not compared (library missing): " + ", ".join(unavailable)
        )

    _section("Per document, per condition")
    documents = sorted({str(row["document"]) for row in rows})
    chosen = st.selectbox("Document", ["All"] + documents, key="exp_a_document")
    visible = rows if chosen == "All" else [r for r in rows if r["document"] == chosen]
    st.dataframe(visible, width="stretch", hide_index=True)

    st.markdown(
        note(
            "glyph_loss = U+FFFD + mismapped. Read those two together: where "
            "pypdf emits a visible U+FFFD, PyMuPDF emits a plausible-looking "
            "wrong character from Latin Extended instead, so the loss is the "
            "same and only its visibility differs. See §3 of the report."
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        note(
            "Do not read 'chars' as completeness. pdfplumber's layout mode pads "
            "with spaces and produces 2.4x more characters while recovering the "
            "same words — which is why word_ratio_vs_best is the column to "
            "trust."
        ),
        unsafe_allow_html=True,
    )

    _section("Reading order and content agreement")
    for document in payload.get("documents", []):
        if chosen != "All" and document.get("document_label") != chosen:
            continue
        pairwise = document.get("pairwise") or {}
        if not pairwise:
            continue
        with st.expander(str(document.get("document_label"))):
            st.caption(
                "High token overlap with a large line-structure difference means "
                "the same words in a different order — a reading-order "
                "difference, not lost text."
            )
            st.dataframe(
                [
                    {
                        "pair": name,
                        "token overlap (median)": value.get("token_overlap_median"),
                        "token overlap (min)": value.get("token_overlap_min"),
                        "line-structure diff": value.get("line_structure_diff_median"),
                        "pages below 80% overlap": ", ".join(
                            str(page)
                            for page in value.get(
                                "pages_with_token_overlap_below_80pct"
                            )
                            or []
                        )
                        or "none",
                    }
                    for name, value in pairwise.items()
                ],
                width="stretch",
                hide_index=True,
            )

    _section("Table extraction")
    table_rows: List[Dict[str, object]] = []
    for document in payload.get("documents", []):
        if chosen != "All" and document.get("document_label") != chosen:
            continue
        for backend, summary in (document.get("backends") or {}).items():
            if backend == "_meta" or not isinstance(summary, dict):
                continue
            tables = summary.get("tables")
            if not isinstance(tables, dict):
                continue
            if not tables.get("supported"):
                table_rows.append(
                    {
                        "document": document.get("document_label"),
                        "backend": backend,
                        "page": "—",
                        "tables found": "unsupported",
                        "shapes": str(tables.get("reason")),
                    }
                )
                continue
            for page_number, info in (tables.get("pages") or {}).items():
                shapes = info.get("shapes")
                table_rows.append(
                    {
                        "document": document.get("document_label"),
                        "backend": backend,
                        "page": str(page_number),
                        # Stringified deliberately: this column mixes a table
                        # count, the word "unsupported" for pypdf, and an error
                        # string. Arrow cannot type a mixed column and
                        # Streamlit falls back with a warning, so the column is
                        # made uniformly textual here.
                        "tables found": str(
                            info.get("table_count", info.get("error", "—"))
                        ),
                        "shapes": "; ".join(
                            f"{shape['rows']}r x {shape['columns']}c "
                            f"({shape['filled_cells']} filled)"
                            for shape in shapes
                        )
                        if shapes
                        else "—",
                    }
                )
    if table_rows:
        st.dataframe(table_rows, width="stretch", hide_index=True)
        st.markdown(
            note(
                "pypdf has no table API at all. This is the one capability gap "
                "Experiment A recommends keeping PyMuPDF installed for."
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            empty_state(
                "No table probes in this run",
                "Re-run Experiment A without --no-tables to probe the table APIs.",
                COMMANDS["experiment_a"],
            ),
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
# F. Evaluation (Experiments B and C)
# --------------------------------------------------------------------------- #


def render_evaluation(data: ProjectData) -> None:
    _header(
        "Evaluation — Experiments B and C",
        "Segmentation scores against committed annotations, and the "
        "corpus-wide data-quality checks.",
    )

    _section("Experiment B — paragraph vs clause-aware")
    rows = experiment_b_rows(data)
    if not rows:
        st.markdown(
            empty_state(
                "Experiment B has not been run",
                "It scores both methods against the hand-checked clause "
                "annotations in data/annotations/segmentation/. It computes "
                "nothing without them, deliberately.",
                COMMANDS["experiment_b"],
            ),
            unsafe_allow_html=True,
        )
    else:
        payload = data.experiment_b or {}
        st.error(
            "Read these numbers with their caveats. The annotations were "
            "produced by the same assistant that wrote the segmenter, so they "
            "are a provisional self-check, not independent ground truth. "
            f"Inter-annotator agreement: {payload.get('inter_annotator_agreement')}."
        )
        for version in payload.get("versions", []):
            st.markdown(
                f"{tag(version.get('version_id'))} "
                f"pages {escape(version.get('annotated_pages'))} · "
                f"{escape(version.get('annotated_clauses_total'))} annotated clause(s) "
                f"({escape(version.get('annotated_clauses_uncertain'))} uncertain) · "
                f"annotator {escape(version.get('annotator'))} · "
                f"review {escape(version.get('review_status'))}",
                unsafe_allow_html=True,
            )
        st.dataframe(rows, width="stretch", hide_index=True)
        aggregate = payload.get("aggregate") or {}
        pooled = [
            {"method": method, **{
                key: value
                for key, value in score.items()
                if key in (
                    "annotated_clauses", "predicted_segments", "matched",
                    "boundary_precision", "boundary_recall", "boundary_f1",
                    "exact_match_rate", "numbering_preservation_rate",
                    "page_provenance_rate",
                )
            }}
            for method, score in aggregate.items()
            if isinstance(score, dict)
        ]
        if pooled:
            st.markdown("**Pooled across versions**")
            st.dataframe(pooled, width="stretch", hide_index=True)
        st.markdown(
            note(
                "Tolerance "
                f"{payload.get('tolerance_chars')} characters. Uncertain records "
                "are excluded from these figures and reported separately in the "
                "report. Full discussion and nine failure cases: "
                "evaluation/experiment-b-segmentation.md"
            ),
            unsafe_allow_html=True,
        )

    _section("Experiment C — data quality")
    quality = data.data_quality
    if not quality:
        st.markdown(
            empty_state(
                "Data-quality checks have not been run",
                "12 checks over the manifest, extraction and segment artefacts.",
                COMMANDS["data_quality"],
            ),
            unsafe_allow_html=True,
        )
        return

    totals = quality.get("totals") or {}
    st.markdown(
        stats_row(
            [
                stat(totals.get("errors", 0), "Errors", "must be zero"),
                stat(totals.get("warnings", 0), "Warnings", ""),
                stat(totals.get("info", 0), "Info", "counts and gaps"),
                stat(len(quality.get("versions_inspected") or []), "Versions", ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    not_applicable = quality.get("checks_not_applicable") or {}
    if not_applicable:
        st.info(
            "Checks reported as not applicable (rather than passing vacuously): "
            + "; ".join(f"{check} — {reason}" for check, reason in not_applicable.items())
        )

    for severity in ("error", "warning", "info"):
        findings = data_quality_findings(data, severity)
        if not findings:
            continue
        with st.expander(
            f"{severity.title()} ({len(findings)})", expanded=severity != "info"
        ):
            for finding in findings:
                st.markdown(
                    f"{pill(finding.get('check'), SEVERITY_COLORS[severity])} "
                    f"{escape(finding.get('message'))}",
                    unsafe_allow_html=True,
                )
                subjects = finding.get("subjects") or []
                if subjects:
                    for subject in subjects[:10]:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;• `{escape(subject)}`", unsafe_allow_html=True)
                    if len(subjects) > 10:
                        st.caption(f"… and {len(subjects) - 10} more")

    st.markdown(
        note(
            "These checks report and never repair. A correction has to be made "
            "deliberately, elsewhere, by a person (data/README.md §8)."
        ),
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# G. Header / footer cleaning audit
# --------------------------------------------------------------------------- #


def render_cleaning(data: ProjectData) -> None:
    _header(
        "Header and footer cleaning",
        "What was removed from each page, why, and the evidence for it. "
        "Removal is reversible by construction.",
    )
    if _no_corpus(data):
        return

    version = _version_picker(data, "clean_version")
    if version is None:
        return

    if not version.cleaning_totals:
        st.markdown(
            empty_state(
                "No cleaning report for this version",
                "Cleaning runs as part of segmentation.",
                COMMANDS["segments"],
            ),
            unsafe_allow_html=True,
        )
        return

    totals = version.cleaning_totals
    st.markdown(
        stats_row(
            [
                stat(totals.get("removed_lines", 0), "Lines removed", ""),
                stat(totals.get("pages_with_removals", 0), "Pages affected", ""),
                stat(
                    "yes" if totals.get("reversible") else "NO",
                    "Reversible",
                    "restore() == input",
                ),
                stat(totals.get("pages", 0), "Pages", ""),
            ]
        ),
        unsafe_allow_html=True,
    )
    if not totals.get("reversible"):
        st.error(
            "Cleaning is NOT reversible for this version. Source evidence has "
            "been lost; the segment files built on it should not be trusted."
        )

    plan = version.cleaning_plan or {}
    for warning in plan.get("warnings") or []:
        st.warning(warning)

    options = plan.get("options") or {}
    if options:
        _section("Detection parameters used")
        st.markdown(
            " ".join(f"{tag(key)} {escape(value)}" for key, value in options.items()),
            unsafe_allow_html=True,
        )
        st.markdown(
            note(
                "The frequency threshold is 0.90, not the 0.60 originally "
                "proposed: at 0.60 the commercial-banks amendment's repeated "
                "table column headers (7 of 10 pages) were deleted as if they "
                "were a running header. See docs/phase2-segmentation.md."
            ),
            unsafe_allow_html=True,
        )

    for zone, key in (("Header", "header_candidates"), ("Footer", "footer_candidates")):
        candidates = plan.get(key) or []
        if not candidates:
            continue
        _section(f"{zone} candidates considered")
        st.caption(
            "Every candidate is shown, removed or not, so a near-miss is visible."
        )
        st.dataframe(
            [
                {
                    "position": candidate.get("position"),
                    "pages": f"{candidate.get('pages')}/{candidate.get('page_total')}",
                    "share": candidate.get("share"),
                    "removed": candidate.get("removed"),
                    "text": candidate.get("text"),
                }
                for candidate in candidates
            ],
            width="stretch",
            hide_index=True,
        )

    rows = removed_lines_table(version)
    _section(f"Removed lines ({len(rows)})")
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.markdown(
            empty_state(
                "Nothing was removed from this version",
                "No line met the frequency and position rules. For a short "
                "document, or one whose letterhead appears on page 1 only, this "
                "is the expected and correct outcome.",
            ),
            unsafe_allow_html=True,
        )
