"""The preview frontend's only route to project data.

Why this module is the single seam
----------------------------------
Nothing else in ``preview_app/`` reads a file, and nothing in ``preview_app/``
is imported by ``backend/``. That isolation is the point: the preview is a
throwaway developer tool (``preview_app/README.md``), and when it is deleted
the backend must be unaffected. Keeping every read in one place also means
there is exactly one file to change when Phase 4's API replaces the JSON files
as the data source — the screens call :class:`ProjectData` methods, not
``json.load``.

What it reads
-------------
Only artefacts the real pipeline produced:

====================================================  ===========================
``data/manifest.csv``                                 human-recorded provenance
``data/processed/<version_id>/extraction.json``       Phase 1 page text
``data/processed/<version_id>/page_cleaning.json``    Phase 2 removal audit
``data/processed/<version_id>/segments_*.json``       Phase 2 segments
``evaluation/experiment-a/results.json``              Experiment A
``evaluation/experiment-b/results.json``              Experiment B
``evaluation/data-quality/results.json``              Experiment C
====================================================  ===========================

**There is no fallback dataset and no sample data.** If an artefact is absent,
the loader returns ``None`` or an empty list and the screen renders an empty
state telling the user which command to run. A preview that invented numbers
when the pipeline had not been run would be worse than useless on a project
whose central rule is that no metric may be fabricated (``CLAUDE.md`` §14).

This module is also read-only. It never writes to ``data/`` or ``evaluation/``,
so no amount of clicking in the preview can alter a project artefact.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

#: Repository root, found by walking up from this file. Four levels:
#: data_access.py -> components -> preview_app -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPO_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
MANIFEST_PATH = DATA_DIR / "manifest.csv"
EVALUATION_DIR = REPO_ROOT / "evaluation"

EXTRACTION_FILENAME = "extraction.json"
CLEANING_FILENAME = "page_cleaning.json"
SEGMENT_FILENAMES = {
    "paragraph": "segments_paragraph.json",
    "clause_aware": "segments_clause_aware.json",
}

#: Shown wherever a metadata field is absent, as the brief requires. Never a
#: guess and never a blank cell, so "we do not know" is visibly different from
#: "nobody has looked".
NOT_AVAILABLE = "Not available"

#: The commands a user runs to produce each artefact. Shown verbatim in empty
#: states so the preview is self-explaining.
COMMANDS = {
    "extraction": (
        "python -m backend.app.document_processing.cli "
        "--manifest data/manifest.csv --verify-checksums"
    ),
    "segments": (
        "python -m backend.app.document_processing.segmentation_cli --all"
    ),
    "experiment_a": (
        "python -m evaluation.experiment_a_extractor_comparison "
        "--manifest data/manifest.csv"
    ),
    "experiment_b": "python -m evaluation.experiment_b_segmentation score",
    "data_quality": "python -m evaluation.data_quality",
}


class DataAccessError(Exception):
    """An artefact exists but could not be read.

    Distinct from an artefact being absent, which is an empty state rather than
    an error: "you have not run the pipeline yet" and "the file on disk is
    corrupt" need different messages and different user actions.
    """


def _display_path(path: Path) -> str:
    """Render a path for a message, relative to the repo when it is inside it.

    ``Path.relative_to`` *raises* when the path is not under the given root, so
    it cannot be used unguarded: the preview can be pointed at a directory
    outside the repository (a test fixture does exactly that), and an error
    message is the worst place for a second exception.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> Optional[Dict[str, object]]:
    """Return parsed JSON, ``None`` if absent, or raise if unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataAccessError(
            f"{_display_path(path)} is not valid JSON ({exc}). "
            f"Regenerate it rather than editing it by hand."
        ) from exc
    except OSError as exc:
        raise DataAccessError(f"could not read {path}: {exc}") from exc


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


def display(value: Optional[object]) -> str:
    """Render a value for the screen, or ``NOT_AVAILABLE`` when it is absent."""
    if value is None:
        return NOT_AVAILABLE
    text = str(value).strip()
    return text or NOT_AVAILABLE


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass
class PageView:
    """One page, with both text variants and its cleaning audit trail."""

    page_number: int
    original_text: str
    normalized_text: str
    char_count: int
    word_count: int
    is_empty: bool
    has_meaningful_text: bool
    cleaned_text: Optional[str] = None
    removed_lines: List[Dict[str, object]] = field(default_factory=list)

    @property
    def removed_line_count(self) -> int:
        return len(self.removed_lines)


@dataclass
class SegmentView:
    """One segment, as the segment JSON stores it."""

    segment_id: str
    ordinal: int
    segment_type: str
    text: str
    page_start: int
    page_end: int
    page_offsets: List[Dict[str, int]]
    clause_number: Optional[str] = None
    heading: Optional[str] = None
    parent_segment_id: Optional[str] = None
    level: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def page_label(self) -> str:
        if self.page_start == self.page_end:
            return f"p{self.page_start}"
        return f"p{self.page_start}–{self.page_end}"

    @property
    def spans_pages(self) -> bool:
        return self.page_end > self.page_start

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.metadata.get("clause_number_ambiguous"))


@dataclass
class VersionView:
    """Everything the preview knows about one ingested version."""

    version_id: str
    document_id: str
    filename: str
    checksum: str
    page_count: int
    extraction_status: str
    extraction_tool: str
    extracted_at: str
    file_size_bytes: int
    warnings: List[str] = field(default_factory=list)
    source_metadata: Dict[str, Optional[str]] = field(default_factory=dict)
    pages: List[PageView] = field(default_factory=list)
    segments: Dict[str, List[SegmentView]] = field(default_factory=dict)
    segment_stats: Dict[str, Dict[str, object]] = field(default_factory=dict)
    segment_warnings: Dict[str, List[str]] = field(default_factory=dict)
    offset_basis: Optional[str] = None
    cleaning_plan: Optional[Dict[str, object]] = None
    cleaning_totals: Dict[str, object] = field(default_factory=dict)
    #: Set when the manifest has a row whose sha256 matches this version.
    manifest_row: Optional[Dict[str, Optional[str]]] = None
    #: Optional artefacts (segments, cleaning) that exist but could not be
    #: parsed. Surfaced in the UI; they do not prevent the version loading.
    load_errors: List[str] = field(default_factory=list)

    # -- metadata accessors, all honest about absence -- #

    @property
    def title(self) -> str:
        return display(self.source_metadata.get("title"))

    @property
    def authority(self) -> str:
        return display(self.source_metadata.get("issuing_department"))

    @property
    def document_type(self) -> str:
        return display(self.source_metadata.get("document_type"))

    @property
    def publication_date(self) -> str:
        return display(self.source_metadata.get("publication_date"))

    @property
    def version_label(self) -> str:
        return display(self.source_metadata.get("version_label"))

    @property
    def reference_no(self) -> str:
        return display(self.source_metadata.get("reference_no"))

    @property
    def source_url(self) -> Optional[str]:
        return self.source_metadata.get("source_url")

    @property
    def has_segments(self) -> bool:
        return any(self.segments.values())

    @property
    def total_segments(self) -> int:
        return sum(len(items) for items in self.segments.values())

    @property
    def empty_page_count(self) -> int:
        return sum(1 for page in self.pages if page.is_empty)

    @property
    def removed_line_count(self) -> int:
        return int(self.cleaning_totals.get("removed_lines", 0) or 0)

    @property
    def raw_pdf_path(self) -> Optional[Path]:
        """The source PDF, if it is still on disk.

        ``data/raw/`` is git-ignored, so this is often absent in a fresh clone.
        The viewer uses it to offer the original alongside the extracted text
        and hides that panel when it is missing.
        """
        if not self.manifest_row:
            return None
        local = self.manifest_row.get("local_filename")
        if not local:
            return None
        candidate = DATA_DIR / "raw" / local
        return candidate if candidate.exists() else None

    def page(self, page_number: int) -> Optional[PageView]:
        for page in self.pages:
            if page.page_number == page_number:
                return page
        return None

    def segments_for(self, method: str) -> List[SegmentView]:
        return self.segments.get(method, [])

    def children_of(
        self, method: str, segment_id: Optional[str]
    ) -> List[SegmentView]:
        return [
            segment
            for segment in self.segments_for(method)
            if segment.parent_segment_id == segment_id
        ]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_manifest_rows(path: Path = MANIFEST_PATH) -> List[Dict[str, Optional[str]]]:
    """Read ``manifest.csv`` into plain dicts, blanks as ``None``.

    Deliberately *not* reusing ``backend.app.document_processing.manifest``:
    that module raises on a missing required column, which is correct for
    ingestion and wrong for a viewer. The preview should still display a
    partially filled manifest and say what is missing.
    """
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [
                {key: _blank_to_none(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]
    except OSError as exc:
        raise DataAccessError(f"could not read {path}: {exc}") from exc


def _page_views(
    extraction: Dict[str, object], cleaning: Optional[Dict[str, object]]
) -> List[PageView]:
    cleaned_by_page: Dict[int, Dict[str, object]] = {}
    if cleaning:
        for entry in cleaning.get("pages", []):  # type: ignore[union-attr]
            cleaned_by_page[int(entry["page_number"])] = entry

    pages: List[PageView] = []
    for entry in extraction.get("pages", []):  # type: ignore[union-attr]
        number = int(entry["page_number"])
        cleaned = cleaned_by_page.get(number)
        pages.append(
            PageView(
                page_number=number,
                original_text=str(entry.get("original_text") or ""),
                normalized_text=str(entry.get("normalized_text") or ""),
                char_count=int(entry.get("char_count") or 0),
                word_count=int(entry.get("word_count") or 0),
                is_empty=bool(entry.get("is_empty")),
                has_meaningful_text=bool(entry.get("has_meaningful_text")),
                cleaned_text=(
                    str(cleaned.get("cleaned_text") or "") if cleaned else None
                ),
                removed_lines=list(cleaned.get("removed_lines") or []) if cleaned else [],
            )
        )
    pages.sort(key=lambda page: page.page_number)
    return pages


def _segment_views(payload: Dict[str, object]) -> List[SegmentView]:
    views: List[SegmentView] = []
    for entry in payload.get("segments", []):  # type: ignore[union-attr]
        views.append(
            SegmentView(
                segment_id=str(entry.get("segment_id") or ""),
                ordinal=int(entry.get("ordinal") or 0),
                segment_type=str(entry.get("segment_type") or "unknown"),
                text=str(entry.get("text") or ""),
                page_start=int(entry.get("page_start") or 0),
                page_end=int(entry.get("page_end") or 0),
                page_offsets=list(entry.get("page_offsets") or []),
                clause_number=entry.get("clause_number"),  # type: ignore[arg-type]
                heading=entry.get("heading"),  # type: ignore[arg-type]
                parent_segment_id=entry.get("parent_segment_id"),  # type: ignore[arg-type]
                level=int(entry.get("level") or 0),
                metadata=dict(entry.get("metadata") or {}),
            )
        )
    views.sort(key=lambda segment: segment.ordinal)
    return views


def load_version(
    directory: Path, manifest_rows: Optional[Iterable[Dict[str, Optional[str]]]] = None
) -> Optional[VersionView]:
    """Load one version directory, or ``None`` if it has no extraction output.

    Failure policy, and the distinction matters for the UI:

    * A corrupt or unusable **extraction.json** raises. There is nothing to show
      for that version, so it cannot be rendered at all.
    * A corrupt **segment or cleaning** file is recorded on the returned view's
      ``load_errors`` and the version still loads. Those artefacts are optional
      — a version that has been extracted but not segmented is a normal state —
      so losing the whole document because one of them is malformed would hide
      the page text that is perfectly readable.
    """
    extraction = _read_json(directory / EXTRACTION_FILENAME)
    if not extraction:
        return None
    document = extraction.get("document")  # type: ignore[union-attr]
    if not isinstance(document, dict):
        raise DataAccessError(
            f"{_display_path(directory / EXTRACTION_FILENAME)} has no "
            f"'document' object; it was not produced by this pipeline"
        )

    cleaning_errors: List[str] = []
    try:
        cleaning = _read_json(directory / CLEANING_FILENAME)
    except DataAccessError as exc:
        cleaning = None
        cleaning_errors.append(str(exc))

    view = VersionView(
        version_id=str(document.get("version_id") or directory.name),
        document_id=str(document.get("document_id") or ""),
        filename=str(document.get("filename") or ""),
        checksum=str(document.get("checksum") or ""),
        page_count=int(document.get("page_count") or 0),
        extraction_status=str(document.get("extraction_status") or "unknown"),
        extraction_tool=str(document.get("extraction_tool") or ""),
        extracted_at=str(document.get("extracted_at") or ""),
        file_size_bytes=int(document.get("file_size_bytes") or 0),
        warnings=list(document.get("warnings") or []),
        source_metadata=dict(document.get("source_metadata") or {}),
        pages=_page_views(extraction, cleaning),
        cleaning_plan=(cleaning or {}).get("plan"),  # type: ignore[arg-type]
        cleaning_totals=dict((cleaning or {}).get("totals") or {}),
    )

    view.load_errors.extend(cleaning_errors)

    for method, filename in SEGMENT_FILENAMES.items():
        try:
            payload = _read_json(directory / filename)
        except DataAccessError as exc:
            view.load_errors.append(str(exc))
            continue
        if not payload:
            continue
        view.segments[method] = _segment_views(payload)
        view.segment_stats[method] = dict(payload.get("stats") or {})
        view.segment_warnings[method] = list(payload.get("warnings") or [])
        view.offset_basis = str(payload.get("offset_basis") or "") or view.offset_basis

    if manifest_rows is not None and view.checksum:
        for row in manifest_rows:
            if (row.get("sha256") or "").lower() == view.checksum.lower():
                view.manifest_row = row
                break
    return view


@dataclass
class ProjectData:
    """Everything the preview can show, loaded once.

    Attributes:
        versions: Every version with extraction output, newest-extracted first.
        load_errors: Artefacts that exist but could not be parsed. Surfaced in
            the UI rather than swallowed, because a corrupt artefact is
            actionable.
    """

    versions: List[VersionView] = field(default_factory=list)
    manifest_rows: List[Dict[str, Optional[str]]] = field(default_factory=list)
    experiment_a: Optional[Dict[str, object]] = None
    experiment_b: Optional[Dict[str, object]] = None
    data_quality: Optional[Dict[str, object]] = None
    load_errors: List[str] = field(default_factory=list)

    # -- dashboard figures. Every one is a count of something real. -- #

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def document_count(self) -> int:
        """Distinct *logical* documents, not files.

        Two versions of one policy share a ``document_id``, so this is the
        number a reader means by "how many documents", and it differs from
        ``version_count`` as soon as the corpus has a version pair.
        """
        return len({version.document_id for version in self.versions if version.document_id})

    @property
    def manifest_row_count(self) -> int:
        return len(self.manifest_rows)

    @property
    def total_pages(self) -> int:
        return sum(version.page_count for version in self.versions)

    @property
    def total_empty_pages(self) -> int:
        return sum(version.empty_page_count for version in self.versions)

    @property
    def segment_totals(self) -> Dict[str, int]:
        totals: Dict[str, int] = {method: 0 for method in SEGMENT_FILENAMES}
        for version in self.versions:
            for method, items in version.segments.items():
                totals[method] = totals.get(method, 0) + len(items)
        return totals

    @property
    def versions_with_segments(self) -> int:
        return sum(1 for version in self.versions if version.has_segments)

    @property
    def status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for version in self.versions:
            counts[version.extraction_status] = (
                counts.get(version.extraction_status, 0) + 1
            )
        return counts

    @property
    def total_removed_lines(self) -> int:
        return sum(version.removed_line_count for version in self.versions)

    @property
    def total_warnings(self) -> int:
        return sum(len(version.warnings) for version in self.versions)

    @property
    def page_spanning_segments(self) -> int:
        return sum(
            1
            for version in self.versions
            for items in version.segments.values()
            for segment in items
            if segment.spans_pages
        )

    @property
    def manifest_rows_not_ingested(self) -> List[str]:
        """Manifest rows with no matching extraction output.

        Usually means the PDF has not been downloaded (``data/raw/`` is
        git-ignored). Shown so the dashboard's document count is never mistaken
        for the corpus the manifest describes.
        """
        ingested = {version.checksum.lower() for version in self.versions}
        return [
            row.get("local_filename") or "(unnamed row)"
            for row in self.manifest_rows
            if (row.get("sha256") or "").lower() not in ingested
        ]

    def version(self, version_id: str) -> Optional[VersionView]:
        for candidate in self.versions:
            if candidate.version_id == version_id:
                return candidate
        return None

    def versions_of_document(self, document_id: str) -> List[VersionView]:
        return [
            version for version in self.versions if version.document_id == document_id
        ]


def load_project_data(
    processed_dir: Path = PROCESSED_DIR,
    manifest_path: Path = MANIFEST_PATH,
    evaluation_dir: Path = EVALUATION_DIR,
) -> ProjectData:
    """Load every available artefact. Never raises for an *absent* artefact.

    A parse failure on one artefact is recorded in ``load_errors`` and the rest
    still loads, so one corrupt file does not blank the whole preview.
    """
    data = ProjectData()

    try:
        data.manifest_rows = load_manifest_rows(manifest_path)
    except DataAccessError as exc:
        data.load_errors.append(str(exc))

    if processed_dir.exists():
        for directory in sorted(processed_dir.iterdir()):
            if not directory.is_dir():
                continue
            try:
                version = load_version(directory, data.manifest_rows)
            except DataAccessError as exc:
                data.load_errors.append(str(exc))
                continue
            if version is not None:
                data.versions.append(version)
                data.load_errors.extend(version.load_errors)

    data.versions.sort(key=lambda version: version.extracted_at, reverse=True)

    for attribute, relative in (
        ("experiment_a", "experiment-a/results.json"),
        ("experiment_b", "experiment-b/results.json"),
        ("data_quality", "data-quality/results.json"),
    ):
        try:
            setattr(data, attribute, _read_json(evaluation_dir / relative))
        except DataAccessError as exc:
            data.load_errors.append(str(exc))

    return data


@lru_cache(maxsize=1)
def cached_project_data() -> ProjectData:
    """Load once per process.

    Streamlit re-runs the whole script on every interaction, so an uncached
    loader would re-read and re-parse several megabytes of JSON on every click.
    ``clear_cache`` is wired to the sidebar's reload control, which is the only
    way to pick up a freshly regenerated artefact.
    """
    return load_project_data()


def clear_cache() -> None:
    cached_project_data.cache_clear()


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@dataclass
class SearchHit:
    """One match of a query inside a page or a segment."""

    version_id: str
    document_title: str
    page_number: int
    context: str
    where: str  # "page" | segment method name
    segment_id: Optional[str] = None
    clause_number: Optional[str] = None


def _context_window(text: str, position: int, query: str, width: int = 90) -> str:
    start = max(0, position - width // 2)
    end = min(len(text), position + len(query) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + " ".join(text[start:end].split()) + suffix


def search_pages(
    version: VersionView, query: str, limit: int = 50
) -> List[SearchHit]:
    """Case-insensitive substring search over a version's normalised page text.

    Substring rather than token matching, deliberately: Experiment A §4 showed
    pypdf inserts spaces inside words (``'M inimum'``), so a token search for
    "minimum" would miss text a reader can plainly see. Substring search at
    least finds "inimum". The limitation is surfaced in the UI rather than
    hidden behind a cleverer matcher that would need its own evaluation.
    """
    if not query.strip():
        return []
    needle = query.lower()
    hits: List[SearchHit] = []
    for page in version.pages:
        haystack = page.normalized_text.lower()
        start = haystack.find(needle)
        while start != -1 and len(hits) < limit:
            hits.append(
                SearchHit(
                    version_id=version.version_id,
                    document_title=version.title,
                    page_number=page.page_number,
                    context=_context_window(page.normalized_text, start, query),
                    where="page",
                )
            )
            start = haystack.find(needle, start + len(needle))
        if len(hits) >= limit:
            break
    return hits


def search_segments(
    version: VersionView, method: str, query: str, limit: int = 50
) -> List[SearchHit]:
    """Substring search over one method's segment text."""
    if not query.strip():
        return []
    needle = query.lower()
    hits: List[SearchHit] = []
    for segment in version.segments_for(method):
        position = segment.text.lower().find(needle)
        if position == -1:
            continue
        hits.append(
            SearchHit(
                version_id=version.version_id,
                document_title=version.title,
                page_number=segment.page_start,
                context=_context_window(segment.text, position, query),
                where=method,
                segment_id=segment.segment_id,
                clause_number=segment.clause_number,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def search_everything(
    data: ProjectData, query: str, limit_per_version: int = 20
) -> List[SearchHit]:
    """Search every version's page text. Used by the Library screen."""
    hits: List[SearchHit] = []
    for version in data.versions:
        hits.extend(search_pages(version, query, limit=limit_per_version))
    return hits


# --------------------------------------------------------------------------- #
# Derived views for the screens
# --------------------------------------------------------------------------- #


def segment_tree(
    version: VersionView, method: str
) -> List[Tuple[SegmentView, int]]:
    """Return ``[(segment, depth), ...]`` in document order, depth from parents.

    ``SegmentView.level`` is the segmenter's own notion of nesting, derived from
    the marker style. This recomputes depth by walking ``parent_segment_id``
    instead, so the tree the UI draws is the one the parent pointers actually
    describe. Where the two disagree, the Segmentation Explorer shows both —
    that disagreement is itself worth seeing.
    """
    segments = version.segments_for(method)
    by_id = {segment.segment_id: segment for segment in segments}

    def depth_of(segment: SegmentView, seen: Optional[set] = None) -> int:
        seen = seen or set()
        parent_id = segment.parent_segment_id
        if not parent_id or parent_id in seen or parent_id not in by_id:
            return 0
        seen.add(parent_id)
        return 1 + depth_of(by_id[parent_id], seen)

    return [(segment, depth_of(segment)) for segment in segments]


def marker_style_table(version: VersionView) -> List[Tuple[str, int]]:
    """Clause-marker style counts for the clause-aware method, descending."""
    stats = version.segment_stats.get("clause_aware") or {}
    styles = stats.get("marker_styles") or {}
    if not isinstance(styles, dict):
        return []
    return sorted(styles.items(), key=lambda item: -int(item[1]))


def removed_lines_table(version: VersionView) -> List[Dict[str, object]]:
    """Every removed header/footer line, flattened for a table."""
    rows: List[Dict[str, object]] = []
    for page in version.pages:
        for line in page.removed_lines:
            rows.append(
                {
                    "page": page.page_number,
                    "line": line.get("line_index"),
                    "reason": line.get("reason"),
                    "text": str(line.get("text") or "").strip(),
                    "evidence": line.get("evidence"),
                }
            )
    return rows


def experiment_a_table(
    data: ProjectData,
) -> List[Dict[str, object]]:
    """Flatten Experiment A's per-document, per-backend summaries for a table.

    Returns an empty list when the experiment has not been run. Nothing is
    computed here — every number comes straight from ``results.json``.
    """
    payload = data.experiment_a
    if not payload:
        return []
    rows: List[Dict[str, object]] = []
    for document in payload.get("documents", []):  # type: ignore[union-attr]
        for backend, summary in (document.get("backends") or {}).items():
            if backend == "_meta" or not isinstance(summary, dict):
                continue
            rows.append(
                {
                    "document": document.get("document_label"),
                    "backend": backend,
                    "tool_version": summary.get("tool_version"),
                    "pages": summary.get("page_count"),
                    "chars": summary.get("total_chars"),
                    "words": summary.get("total_words"),
                    "U+FFFD": summary.get("replacement_chars"),
                    "mismapped": summary.get("mismapped_glyph_candidates"),
                    "glyph_loss": summary.get("glyph_loss_signal"),
                    "hyphen_space": summary.get("hyphen_space_artifacts"),
                    "padded_quotes": summary.get("padded_quotes"),
                    "flattened_table_pages": len(
                        summary.get("flattened_table_pages") or []
                    ),
                    "word_ratio_vs_best": summary.get("word_ratio_vs_best_median"),
                    "seconds": summary.get("seconds_median"),
                }
            )
    return rows


def experiment_b_rows(data: ProjectData) -> List[Dict[str, object]]:
    """Flatten Experiment B's headline scores for a table."""
    payload = data.experiment_b
    if not payload:
        return []
    rows: List[Dict[str, object]] = []
    for version in payload.get("versions", []):  # type: ignore[union-attr]
        for method, score in (
            version.get("headline_excluding_uncertain") or {}
        ).items():
            rows.append(
                {
                    "version_id": version.get("version_id"),
                    "pages": ", ".join(
                        str(page) for page in version.get("annotated_pages") or []
                    ),
                    "method": method,
                    "annotated": score.get("annotated_clauses"),
                    "predicted": score.get("predicted_segments"),
                    "matched": score.get("matched"),
                    "precision": score.get("boundary_precision"),
                    "recall": score.get("boundary_recall"),
                    "f1": score.get("boundary_f1"),
                    "exact_match": score.get("exact_match_rate"),
                    "merges": score.get("incorrect_merges"),
                    "splits": score.get("incorrect_splits"),
                    "numbering": score.get("numbering_preservation_rate"),
                    "provenance": score.get("page_provenance_rate"),
                }
            )
    return rows


def data_quality_findings(
    data: ProjectData, severity: Optional[str] = None
) -> List[Dict[str, object]]:
    """Experiment C findings, optionally filtered by severity."""
    payload = data.data_quality
    if not payload:
        return []
    findings = list(payload.get("findings") or [])  # type: ignore[arg-type]
    if severity:
        findings = [
            finding for finding in findings if finding.get("severity") == severity
        ]
    return findings
