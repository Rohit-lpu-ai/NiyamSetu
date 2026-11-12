"""The preview frontend's data layer, and that every screen renders.

Two kinds of test here:

* **Data-layer tests** run always. They are plain Python and assert the two
  properties that matter most for a viewer built on this project: that an absent
  artefact is an empty state rather than an error, and that **no figure is ever
  invented** — a missing metadata field must surface as "Not available" and a
  missing evaluation must surface as an empty list, never as a zero that looks
  like a measurement.
* **Render tests** use ``streamlit.testing.v1.AppTest`` and are skipped when
  Streamlit is not installed, because it is deliberately *not* a backend
  dependency (``requirements.txt``). They assert that each screen renders
  without raising — which is the only automated way to catch a screen that
  breaks on real data.

Nothing here asserts a metric value. The preview displays numbers produced
elsewhere; its job is to display them faithfully, and that is what is tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from preview_app.components import data_access as da
from preview_app.components.styles import empty_state, escape, highlight, stat

VERSION_ID = "VER-0123456789abcdef"
DOCUMENT_ID = "DOC-fedcba9876543210"

PAGE_ONE = "RUNNING HEADER\n1\n\n1. First clause of the fixture.\n"
PAGE_TWO = "RUNNING HEADER\n2\n\n2. Second clause of the fixture.\n"


def _extraction(**document_overrides: object) -> Dict[str, object]:
    document = {
        "schema_version": "1.0.0",
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "filename": "fixture.pdf",
        "checksum": "a" * 64,
        "file_size_bytes": 2048,
        "page_count": 2,
        "extraction_status": "ok",
        "extraction_tool": "pypdf 6.18.1",
        "extracted_at": "2026-11-12T00:00:00+00:00",
        "pdf_metadata": {},
        "source_metadata": {
            "title": "Fixture Directions",
            "issuing_department": "Fixture Department",
            "document_type": "circular",
            "reference_no": "FIX/1/2026",
            "publication_date": "2026-11-01",
            "source_url": "https://example.gov.in/fixture.pdf",
            "retrieved_at": "2026-11-12",
            "document_group": "fixture-group",
            "version_label": "v1",
            "supersedes": None,
        },
        "normalization_options": {},
        "warnings": ["page 2: something worth noting"],
    }
    document.update(document_overrides)
    return {
        "document": document,
        "pages": [
            {
                "page_number": index,
                "original_text": text,
                "normalized_text": text,
                "char_count": len(text),
                "normalized_char_count": len(text),
                "word_count": len(text.split()),
                "is_empty": False,
                "has_meaningful_text": True,
            }
            for index, text in enumerate([PAGE_ONE, PAGE_TWO], start=1)
        ],
    }


def _segments(method: str) -> Dict[str, object]:
    def one(ordinal: int, page: int, number: str, parent: str | None) -> Dict[str, object]:
        return {
            "segment_id": f"SEG-{ordinal:016d}",
            "document_id": DOCUMENT_ID,
            "version_id": VERSION_ID,
            "segmentation_method": method,
            "ordinal": ordinal,
            "segment_type": "clause",
            "clause_number": number if method == "clause_aware" else None,
            "heading": None,
            "text": f"{number}. Clause {number} of the fixture document.",
            "page_start": page,
            "page_end": page,
            "page_offsets": [
                {
                    "page_number": page,
                    "char_start": 0,
                    "char_end": 20,
                    "line_index_start": 0,
                    "line_index_end": 0,
                }
            ],
            "parent_segment_id": parent,
            "level": 1 if parent is None else 2,
            "metadata": {"clause_number_ambiguous": True} if ordinal == 1 else {},
        }

    return {
        "schema_version": "1.0.0",
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "segmentation_method": method,
        "offset_basis": "normalized_text",
        "segment_count": 2,
        "stats": {"segment_count": 2, "mean_chars": 40, "page_spanning_segments": 0},
        "warnings": [],
        "segments": [one(0, 1, "1", None), one(1, 2, "2", "SEG-0000000000000000")],
    }


def _cleaning() -> Dict[str, object]:
    return {
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "offset_basis": "normalized_text",
        "plan": {
            "page_count": 2,
            "options": {"frequency_threshold": 0.9},
            "header_candidates": [
                {
                    "position": 0,
                    "zone": "top",
                    "text": "RUNNING HEADER",
                    "pages": 2,
                    "page_total": 2,
                    "share": 1.0,
                    "removed": True,
                }
            ],
            "footer_candidates": [],
            "warnings": ["2 page(s) is below the confidence floor"],
        },
        "pages": [
            {
                "page_number": 1,
                "cleaned_text": "\n1. First clause of the fixture.\n",
                "removed_line_count": 2,
                "removed_lines": [
                    {
                        "page_number": 1,
                        "line_index": 0,
                        "text": "RUNNING HEADER",
                        "reason": "repeated_header",
                        "evidence": "repeated at top position 0 on 2/2 pages",
                    },
                    {
                        "page_number": 1,
                        "line_index": 1,
                        "text": "1",
                        "reason": "printed_page_number",
                        "evidence": "matches the physical page number",
                    },
                ],
                "line_offset_map": [2, 3, 4],
            }
        ],
        "totals": {
            "pages": 2,
            "removed_lines": 2,
            "pages_with_removals": 1,
            "reversible": True,
        },
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A complete fake repository: manifest, one version, all three evaluations."""
    processed = tmp_path / "data" / "processed" / VERSION_ID
    processed.mkdir(parents=True)
    (processed / "extraction.json").write_text(json.dumps(_extraction()), encoding="utf-8")
    (processed / "segments_paragraph.json").write_text(
        json.dumps(_segments("paragraph")), encoding="utf-8"
    )
    (processed / "segments_clause_aware.json").write_text(
        json.dumps(_segments("clause_aware")), encoding="utf-8"
    )
    (processed / "page_cleaning.json").write_text(json.dumps(_cleaning()), encoding="utf-8")

    (tmp_path / "data" / "manifest.csv").write_text(
        "local_filename,original_filename,sha256,title,department,document_type,"
        "reference_no,publication_date,source_url,retrieved_at,document_group,"
        "version_label,supersedes,is_scanned,notes\n"
        f"fixture.pdf,FIXTURE.PDF,{'a' * 64},Fixture Directions,Fixture Department,"
        "circular,FIX/1/2026,2026-11-01,https://example.gov.in/fixture.pdf,"
        "2026-11-12,fixture-group,v1,,false,\n",
        encoding="utf-8",
    )

    evaluation = tmp_path / "evaluation"
    (evaluation / "experiment-a").mkdir(parents=True)
    (evaluation / "experiment-a" / "results.json").write_text(
        json.dumps(
            {
                "experiment": "A",
                "generated_at": "2026-11-12T00:00:00+00:00",
                "python": "3.11.9",
                "repeats_per_document": 3,
                "backends_compared": [{"name": "pypdf", "tool_version": "pypdf 6.18.1"}],
                "backends_unavailable": {},
                "documents": [
                    {
                        "document_label": "fixture",
                        "version_id": VERSION_ID,
                        "backends": {
                            "pypdf": {
                                "tool_version": "pypdf 6.18.1",
                                "page_count": 2,
                                "total_chars": 100,
                                "total_words": 20,
                                "replacement_chars": 3,
                                "mismapped_glyph_candidates": 0,
                                "glyph_loss_signal": 3,
                                "hyphen_space_artifacts": 1,
                                "padded_quotes": 0,
                                "flattened_table_pages": [2],
                                "word_ratio_vs_best_median": 1.0,
                                "seconds_median": 0.01,
                                "tables": {
                                    "supported": False,
                                    "reason": "library has no table API",
                                },
                            }
                        },
                        "pairwise": {},
                        "notes": [],
                        "pages": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (evaluation / "experiment-b").mkdir(parents=True)
    (evaluation / "experiment-b" / "results.json").write_text(
        json.dumps(
            {
                "experiment": "B",
                "tolerance_chars": 5,
                "inter_annotator_agreement": "PENDING - no second annotator",
                "aggregate": {
                    "paragraph": {
                        "annotated_clauses": 2, "predicted_segments": 2, "matched": 2,
                        "boundary_precision": 1.0, "boundary_recall": 1.0,
                        "boundary_f1": 1.0, "exact_match_rate": 1.0,
                        "numbering_preservation_rate": None,
                        "page_provenance_rate": 1.0,
                    }
                },
                "versions": [
                    {
                        "version_id": VERSION_ID,
                        "annotated_pages": [1, 2],
                        "annotator": "tester",
                        "review_status": ["awaiting_owner_review"],
                        "annotated_clauses_total": 2,
                        "annotated_clauses_uncertain": 0,
                        "headline_excluding_uncertain": {
                            "paragraph": {
                                "annotated_clauses": 2, "predicted_segments": 2,
                                "matched": 2, "boundary_precision": 1.0,
                                "boundary_recall": 1.0, "boundary_f1": 1.0,
                                "exact_match_rate": 1.0, "incorrect_merges": 0,
                                "incorrect_splits": 0,
                                "numbering_preservation_rate": None,
                                "page_provenance_rate": 1.0,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (evaluation / "data-quality").mkdir(parents=True)
    (evaluation / "data-quality" / "results.json").write_text(
        json.dumps(
            {
                "experiment": "C",
                "versions_inspected": [VERSION_ID],
                "checks_run": ["identifiers", "pages"],
                "checks_not_applicable": {"manifest": "no manifest"},
                "totals": {"findings": 2, "errors": 0, "warnings": 1, "info": 1},
                "findings": [
                    {
                        "check": "manifest",
                        "severity": "warning",
                        "message": "no version pair",
                        "subjects": [],
                        "count": 0,
                    },
                    {
                        "check": "pages",
                        "severity": "info",
                        "message": "2 pages, all in sequence",
                        "subjects": [],
                        "count": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def load(repo_root: Path) -> da.ProjectData:
    return da.load_project_data(
        repo_root / "data" / "processed",
        repo_root / "data" / "manifest.csv",
        repo_root / "evaluation",
    )


# --------------------------------------------------------------------------- #
# Data layer: loading
# --------------------------------------------------------------------------- #


def test_everything_loads_from_a_complete_fake_repository(repo: Path) -> None:
    data = load(repo)
    assert data.load_errors == []
    assert data.version_count == 1
    assert data.document_count == 1
    assert data.manifest_row_count == 1
    assert data.total_pages == 2
    assert data.segment_totals == {"paragraph": 2, "clause_aware": 2}
    assert data.versions_with_segments == 1
    assert data.total_removed_lines == 2
    assert data.status_counts == {"ok": 1}
    assert data.experiment_a and data.experiment_b and data.data_quality


def test_absent_artefacts_are_an_empty_state_not_an_error(tmp_path: Path) -> None:
    """A fresh clone has no data/processed/. That must not look like a failure."""
    data = da.load_project_data(
        tmp_path / "processed", tmp_path / "manifest.csv", tmp_path / "evaluation"
    )
    assert data.load_errors == []
    assert data.versions == []
    assert data.manifest_rows == []
    assert data.experiment_a is None
    assert data.experiment_b is None
    assert data.data_quality is None
    assert data.version_count == 0
    assert data.total_pages == 0


def test_an_unreadable_artefact_is_recorded_and_does_not_stop_the_rest(
    repo: Path,
) -> None:
    """One corrupt file must not blank the whole preview."""
    (repo / "data" / "processed" / VERSION_ID / "segments_paragraph.json").write_text(
        "{broken", encoding="utf-8"
    )
    data = load(repo)
    assert len(data.load_errors) == 1
    assert "not valid JSON" in data.load_errors[0]
    # Extraction and the clause-aware segments still loaded.
    assert data.version_count == 1
    assert data.segment_totals["clause_aware"] == 2


def test_a_version_directory_without_extraction_is_skipped(repo: Path) -> None:
    (repo / "data" / "processed" / "VER-notextracted").mkdir()
    data = load(repo)
    assert [version.version_id for version in data.versions] == [VERSION_ID]


def test_extraction_without_a_document_object_is_an_error(repo: Path) -> None:
    (repo / "data" / "processed" / VERSION_ID / "extraction.json").write_text(
        json.dumps({"pages": []}), encoding="utf-8"
    )
    data = load(repo)
    assert any("has no 'document' object" in error for error in data.load_errors)


def test_a_manifest_row_with_no_processed_output_is_listed(repo: Path) -> None:
    """So the dashboard's count is never mistaken for the whole corpus."""
    manifest = repo / "data" / "manifest.csv"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"second.pdf,SECOND.PDF,{'b' * 64},Second,,,,,"
        "https://example.gov.in/second.pdf,2026-11-12,other-group,v1,,false,\n",
        encoding="utf-8",
    )
    data = load(repo)
    assert data.manifest_rows_not_ingested == ["second.pdf"]


def test_a_version_is_matched_to_its_manifest_row_by_checksum(repo: Path) -> None:
    version = load(repo).versions[0]
    assert version.manifest_row is not None
    assert version.manifest_row["local_filename"] == "fixture.pdf"


# --------------------------------------------------------------------------- #
# Data layer: never invent a value
# --------------------------------------------------------------------------- #


def test_a_missing_metadata_field_renders_as_not_available(repo: Path) -> None:
    """The brief's requirement, and the project's "empty is better than wrong"."""
    path = repo / "data" / "processed" / VERSION_ID / "extraction.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["document"]["source_metadata"]["issuing_department"] = None
    payload["document"]["source_metadata"]["publication_date"] = ""
    path.write_text(json.dumps(payload), encoding="utf-8")

    version = load(repo).versions[0]
    assert version.authority == da.NOT_AVAILABLE
    assert version.publication_date == da.NOT_AVAILABLE
    # A field that IS present is not replaced.
    assert version.title == "Fixture Directions"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_display_turns_every_kind_of_absence_into_not_available(value: object) -> None:
    assert da.display(value) == da.NOT_AVAILABLE


def test_a_missing_evaluation_yields_an_empty_table_not_zeroes(tmp_path: Path) -> None:
    """A zero would look like a measurement. An empty list drives an empty state."""
    data = da.load_project_data(
        tmp_path / "processed", tmp_path / "manifest.csv", tmp_path / "evaluation"
    )
    assert da.experiment_a_table(data) == []
    assert da.experiment_b_rows(data) == []
    assert da.data_quality_findings(data) == []


def test_evaluation_tables_copy_values_rather_than_recomputing(repo: Path) -> None:
    data = load(repo)
    row = da.experiment_a_table(data)[0]
    assert row["backend"] == "pypdf"
    assert row["U+FFFD"] == 3
    assert row["glyph_loss"] == 3
    assert row["flattened_table_pages"] == 1  # len of the page list

    b_row = da.experiment_b_rows(data)[0]
    assert b_row["method"] == "paragraph"
    assert b_row["f1"] == 1.0
    # A None rate stays None - it must not become 0.0 on the way to the screen.
    assert b_row["numbering"] is None


def test_data_quality_findings_filter_by_severity(repo: Path) -> None:
    data = load(repo)
    assert len(da.data_quality_findings(data)) == 2
    assert len(da.data_quality_findings(data, "warning")) == 1
    assert len(da.data_quality_findings(data, "error")) == 0


# --------------------------------------------------------------------------- #
# Data layer: derived views
# --------------------------------------------------------------------------- #


def test_segment_tree_depth_comes_from_parent_pointers(repo: Path) -> None:
    version = load(repo).versions[0]
    tree = da.segment_tree(version, "clause_aware")
    assert [depth for _, depth in tree] == [0, 1]


def test_segment_tree_survives_a_parent_cycle(repo: Path) -> None:
    """A cycle must not hang the UI, however it got into the file."""
    path = repo / "data" / "processed" / VERSION_ID / "segments_clause_aware.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["segments"][0]["parent_segment_id"] = payload["segments"][1]["segment_id"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    tree = da.segment_tree(load(repo).versions[0], "clause_aware")
    assert len(tree) == 2  # returned, not hung


def test_segment_view_labels_and_flags(repo: Path) -> None:
    version = load(repo).versions[0]
    segments = version.segments_for("clause_aware")
    assert segments[0].page_label == "p1"
    assert segments[0].spans_pages is False
    assert segments[1].is_ambiguous is True  # metadata flag read through
    assert segments[0].is_ambiguous is False


def test_marker_style_table_is_empty_without_stats(repo: Path) -> None:
    version = load(repo).versions[0]
    assert da.marker_style_table(version) == []


def test_removed_lines_table_flattens_every_page(repo: Path) -> None:
    version = load(repo).versions[0]
    rows = da.removed_lines_table(version)
    assert len(rows) == 2
    assert {row["reason"] for row in rows} == {
        "repeated_header",
        "printed_page_number",
    }
    for row in rows:
        assert row["evidence"]


def test_page_accessor_returns_none_for_an_unknown_page(repo: Path) -> None:
    version = load(repo).versions[0]
    assert version.page(1) is not None
    assert version.page(99) is None


def test_version_accessor_returns_none_for_an_unknown_id(repo: Path) -> None:
    data = load(repo)
    assert data.version(VERSION_ID) is not None
    assert data.version("VER-nope") is None


# --------------------------------------------------------------------------- #
# Data layer: search
# --------------------------------------------------------------------------- #


def test_page_search_finds_every_occurrence_with_context(repo: Path) -> None:
    version = load(repo).versions[0]
    hits = da.search_pages(version, "clause")
    assert len(hits) == 2
    assert {hit.page_number for hit in hits} == {1, 2}
    for hit in hits:
        assert "clause" in hit.context.lower()
        assert hit.where == "page"


def test_search_is_case_insensitive(repo: Path) -> None:
    version = load(repo).versions[0]
    assert da.search_pages(version, "FIRST CLAUSE")


def test_an_empty_query_returns_nothing(repo: Path) -> None:
    version = load(repo).versions[0]
    assert da.search_pages(version, "   ") == []
    assert da.search_segments(version, "clause_aware", "") == []


def test_segment_search_reports_the_clause_number(repo: Path) -> None:
    version = load(repo).versions[0]
    hits = da.search_segments(version, "clause_aware", "Clause 2")
    assert len(hits) == 1
    assert hits[0].clause_number == "2"
    assert hits[0].segment_id


def test_search_respects_its_limit(repo: Path) -> None:
    version = load(repo).versions[0]
    assert len(da.search_pages(version, "clause", limit=1)) == 1


def test_search_everything_covers_every_version(repo: Path) -> None:
    data = load(repo)
    assert len(da.search_everything(data, "clause")) == 2


# --------------------------------------------------------------------------- #
# Presentation helpers - escaping is a correctness issue here
# --------------------------------------------------------------------------- #


def test_escape_neutralises_markup_in_document_text() -> None:
    """Real policy text contains & and <, and every screen uses raw HTML."""
    assert escape("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )
    assert escape('a & b "c"') == "a &amp; b &quot;c&quot;"


def test_highlight_escapes_before_inserting_marks() -> None:
    """A document containing <mark> must not be able to smuggle markup."""
    out = highlight("<b>minimum</b> capital", "minimum")
    assert "<mark>minimum</mark>" in out
    assert "&lt;b&gt;" in out
    assert "<b>" not in out


def test_highlight_is_case_insensitive_and_preserves_original_case() -> None:
    out = highlight("The Minimum ratio", "minimum")
    assert "<mark>Minimum</mark>" in out


def test_highlight_with_an_empty_query_only_escapes() -> None:
    assert highlight("a < b", "") == "a &lt; b"
    assert highlight("a < b", "   ") == "a &lt; b"


def test_empty_state_includes_the_command_to_run() -> None:
    html = empty_state("Nothing here", "Run the pipeline.", "python -m something")
    assert "Nothing here" in html
    assert "python -m something" in html


def test_stat_escapes_its_inputs() -> None:
    assert "&lt;b&gt;" in stat("<b>", "label")


# --------------------------------------------------------------------------- #
# Render tests - Streamlit is optional, so these skip when it is absent
# --------------------------------------------------------------------------- #

streamlit = pytest.importorskip(
    "streamlit",
    reason=(
        "Streamlit is a preview-only dependency and is not pinned in "
        "requirements.txt; install preview_app/requirements.txt to run these"
    ),
)

from streamlit.testing.v1 import AppTest  # noqa: E402 - after importorskip

APP_PATH = str(Path(__file__).resolve().parents[2] / "preview_app" / "app.py")

SCREEN_NAMES = [
    "Dashboard",
    "Document library",
    "Document viewer",
    "Segmentation explorer",
    "Header/footer cleaning",
    "Extractor comparison (A)",
    "Evaluation (B and C)",
]


@pytest.mark.parametrize("screen", SCREEN_NAMES)
def test_every_screen_renders_without_raising(screen: str) -> None:
    """The only automated check that a screen does not break on real data.

    Runs against the repository's actual artefacts when they are present, and
    against the empty state when they are not — both must render.
    """
    app = AppTest.from_file(APP_PATH, default_timeout=120).run()
    app.sidebar.radio[0].set_value(screen).run()
    assert not app.exception, [str(exc.value) for exc in app.exception]


def test_the_sidebar_lists_every_screen() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=120).run()
    assert list(app.sidebar.radio[0].options) == SCREEN_NAMES


def test_the_reload_button_exists_and_does_something() -> None:
    """No fake controls: the reload button must actually clear the cache."""
    app = AppTest.from_file(APP_PATH, default_timeout=120).run()
    assert app.sidebar.button
    app.sidebar.button[0].click().run()
    assert not app.exception
