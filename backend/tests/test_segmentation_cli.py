"""The Phase 2 CLI: file discovery, output contract, determinism and safety.

The behaviours that matter most here are the ones that protect existing work:
the extraction JSON must never be rewritten, and an existing segment file must
not be clobbered without ``--force``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from backend.app.document_processing.segmentation_cli import (
    CLEANING_FILENAME,
    EXTRACTION_FILENAME,
    SEGMENT_FILENAMES,
    SegmentationInputError,
    discover_version_dirs,
    load_extraction,
    main,
    segment_extraction,
)

SAMPLE_PAGES = [
    (
        "RUNNING HEADER OF THE DIRECTIONS\n"
        "1\n"
        "\n"
        "RBI/2026-27/999\n"
        "Madam/ Dear Sir,\n"
        "\n"
        "1. Planning Process\n"
        "\n"
        "1.1 The committees shall meet quarterly and shall report on"
    ),
    (
        "RUNNING HEADER OF THE DIRECTIONS\n"
        "2\n"
        "\n"
        "the progress achieved during the quarter.\n"
        "\n"
        "2. Credit Facilities\n"
        "\n"
        "(a) advances to the target group; and\n"
        "\n"
        "(b) advances for housing."
    ),
]


def build_extraction_payload(
    version_id: str = "VER-0123456789abcdef",
    document_id: str = "DOC-0123456789abcdef",
    pages: List[str] | None = None,
) -> Dict[str, object]:
    """Build an extraction.json payload matching the Phase 1 contract."""
    pages = pages if pages is not None else SAMPLE_PAGES
    return {
        "document": {
            "schema_version": "1.0.0",
            "document_id": document_id,
            "version_id": version_id,
            "filename": "fixture.pdf",
            "checksum": "0" * 64,
            "file_size_bytes": 1234,
            "page_count": len(pages),
            "extraction_status": "ok",
            "extraction_tool": "pypdf 6.18.1",
            "extracted_at": "2026-11-12T00:00:00+00:00",
            "pdf_metadata": {},
            "source_metadata": {"title": "Fixture Directions"},
            "normalization_options": {},
            "warnings": [],
        },
        "pages": [
            {
                "page_number": index,
                "original_text": text,
                "normalized_text": text,
                "char_count": len(text),
                "normalized_char_count": len(text),
                "word_count": len(text.split()),
                "is_empty": not text.strip(),
                "has_meaningful_text": True,
            }
            for index, text in enumerate(pages, start=1)
        ],
    }


@pytest.fixture
def processed_dir(tmp_path: Path) -> Path:
    """A ``data/processed``-shaped directory holding one extracted version."""
    version_dir = tmp_path / "VER-0123456789abcdef"
    version_dir.mkdir()
    (version_dir / EXTRACTION_FILENAME).write_text(
        json.dumps(build_extraction_payload(), indent=2), encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


def test_missing_extraction_file_is_a_typed_error(tmp_path: Path) -> None:
    with pytest.raises(SegmentationInputError) as excinfo:
        load_extraction(tmp_path / "nope.json")
    assert excinfo.value.code == "SEGMENTATION_INPUT_ERROR"


def test_invalid_json_is_a_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SegmentationInputError, match="not valid JSON"):
        load_extraction(path)


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda p: p.pop("pages"), "pages"),
        (lambda p: p.update(pages=[]), "pages"),
        (lambda p: p.pop("document"), "document"),
        (lambda p: p["document"].update(version_id=""), "version_id"),
        (lambda p: p["document"].update(document_id=""), "document_id"),
    ],
)
def test_a_malformed_payload_is_rejected_with_a_clear_message(
    tmp_path: Path, mutate, expected: str
) -> None:
    payload = build_extraction_payload()
    mutate(payload)
    path = tmp_path / "extraction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SegmentationInputError, match=expected):
        load_extraction(path)


def test_a_page_missing_its_text_field_is_rejected() -> None:
    payload = build_extraction_payload()
    del payload["pages"][0]["normalized_text"]  # type: ignore[index]
    with pytest.raises(SegmentationInputError, match="normalized_text"):
        segment_extraction(payload)


def test_a_non_positive_page_number_is_rejected() -> None:
    payload = build_extraction_payload()
    payload["pages"][0]["page_number"] = 0  # type: ignore[index]
    with pytest.raises(SegmentationInputError, match="1-based"):
        segment_extraction(payload)


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #


def test_segment_extraction_runs_both_methods_and_reports_reversibility() -> None:
    results, cleaning = segment_extraction(build_extraction_payload())
    assert set(results) == {"paragraph", "clause_aware"}
    assert cleaning["totals"]["reversible"] is True  # type: ignore[index]
    assert cleaning["offset_basis"] == "normalized_text"
    assert cleaning["totals"]["removed_lines"] > 0  # type: ignore[index]
    for result in results.values():
        assert result.segments


def test_use_raw_text_switches_the_offset_basis() -> None:
    _, cleaning = segment_extraction(build_extraction_payload(), use_raw_text=True)
    assert cleaning["offset_basis"] == "original_text"
    assert cleaning["source_text_field"] == "original_text"


def test_discover_version_dirs_finds_only_directories_with_extraction(
    processed_dir: Path,
) -> None:
    (processed_dir / "not-a-version").mkdir()
    (processed_dir / "loose-file.json").write_text("{}", encoding="utf-8")
    found = discover_version_dirs(processed_dir)
    assert [path.name for path in found] == ["VER-0123456789abcdef"]


def test_discover_version_dirs_on_a_missing_directory_returns_empty(
    tmp_path: Path,
) -> None:
    assert discover_version_dirs(tmp_path / "absent") == []


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #


def test_all_writes_both_segment_files_and_the_cleaning_report(
    processed_dir: Path,
) -> None:
    exit_code = main(["--all", "--processed-dir", str(processed_dir)])
    assert exit_code == 0

    version_dir = processed_dir / "VER-0123456789abcdef"
    for filename in (*SEGMENT_FILENAMES.values(), CLEANING_FILENAME):
        assert (version_dir / filename).exists(), filename

    payload = json.loads(
        (version_dir / SEGMENT_FILENAMES["clause_aware"]).read_text(encoding="utf-8")
    )
    assert payload["segmentation_method"] == "clause_aware"
    assert payload["offset_basis"] == "normalized_text"
    assert payload["segment_count"] == len(payload["segments"])
    assert payload["source_extraction"]["extraction_tool"] == "pypdf 6.18.1"
    assert payload["segments"]


def test_the_extraction_file_is_never_modified(processed_dir: Path) -> None:
    """``CLAUDE.md`` §24 rule 10: Phase 2 reads Phase 1's output, never rewrites it."""
    extraction = processed_dir / "VER-0123456789abcdef" / EXTRACTION_FILENAME
    before = extraction.read_bytes()
    assert main(["--all", "--processed-dir", str(processed_dir)]) == 0
    assert extraction.read_bytes() == before


def test_an_existing_segment_file_is_not_overwritten_without_force(
    processed_dir: Path,
) -> None:
    assert main(["--all", "--processed-dir", str(processed_dir)]) == 0
    target = (
        processed_dir / "VER-0123456789abcdef" / SEGMENT_FILENAMES["paragraph"]
    )
    target.write_text('{"edited": "by hand"}', encoding="utf-8")

    assert main(["--all", "--processed-dir", str(processed_dir)]) == 1
    assert json.loads(target.read_text(encoding="utf-8")) == {"edited": "by hand"}

    assert main(["--all", "--processed-dir", str(processed_dir), "--force"]) == 0
    assert "segments" in json.loads(target.read_text(encoding="utf-8"))


def test_no_write_produces_no_files(processed_dir: Path) -> None:
    assert main(["--all", "--processed-dir", str(processed_dir), "--no-write"]) == 0
    version_dir = processed_dir / "VER-0123456789abcdef"
    assert not (version_dir / SEGMENT_FILENAMES["paragraph"]).exists()


def test_output_is_deterministic(processed_dir: Path) -> None:
    """Same input, same options, byte-identical segments.

    Asserted twice over: the CLI's own ``--check-deterministic`` pass, and a
    comparison of the files from two separate runs.
    """
    assert (
        main([
            "--all", "--processed-dir", str(processed_dir), "--check-deterministic"
        ])
        == 0
    )
    version_dir = processed_dir / "VER-0123456789abcdef"
    first = {
        name: (version_dir / name).read_text(encoding="utf-8")
        for name in SEGMENT_FILENAMES.values()
    }
    assert main(["--all", "--processed-dir", str(processed_dir), "--force"]) == 0
    for name, text in first.items():
        after = json.loads((version_dir / name).read_text(encoding="utf-8"))
        before = json.loads(text)
        # generated_at is a timestamp and is expected to differ; everything
        # that describes the segmentation itself must not.
        after.pop("generated_at")
        before.pop("generated_at")
        assert after == before


def test_version_id_selects_a_single_version(processed_dir: Path) -> None:
    exit_code = main([
        "--version-id", "VER-0123456789abcdef",
        "--processed-dir", str(processed_dir),
    ])
    assert exit_code == 0


def test_an_unknown_version_id_fails_without_touching_others(
    processed_dir: Path,
) -> None:
    exit_code = main([
        "--version-id", "VER-doesnotexist",
        "--processed-dir", str(processed_dir),
    ])
    assert exit_code == 1


def test_no_clean_disables_removal(processed_dir: Path) -> None:
    assert (
        main([
            "--all", "--processed-dir", str(processed_dir), "--no-clean"
        ])
        == 0
    )
    cleaning = json.loads(
        (processed_dir / "VER-0123456789abcdef" / CLEANING_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert cleaning["totals"]["removed_lines"] == 0
    # With the header left in, it must show up in the segment text.
    payload = json.loads(
        (
            processed_dir / "VER-0123456789abcdef" / SEGMENT_FILENAMES["clause_aware"]
        ).read_text(encoding="utf-8")
    )
    joined = " ".join(segment["text"] for segment in payload["segments"])
    assert "RUNNING HEADER OF THE DIRECTIONS" in joined


def test_cleaning_actually_removes_the_header_from_segment_text(
    processed_dir: Path,
) -> None:
    assert main(["--all", "--processed-dir", str(processed_dir)]) == 0
    payload = json.loads(
        (
            processed_dir / "VER-0123456789abcdef" / SEGMENT_FILENAMES["clause_aware"]
        ).read_text(encoding="utf-8")
    )
    joined = " ".join(segment["text"] for segment in payload["segments"])
    assert "RUNNING HEADER OF THE DIRECTIONS" not in joined


def test_the_cleaning_report_records_every_removed_line(processed_dir: Path) -> None:
    assert main(["--all", "--processed-dir", str(processed_dir)]) == 0
    cleaning = json.loads(
        (processed_dir / "VER-0123456789abcdef" / CLEANING_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    removed = [
        line for page in cleaning["pages"] for line in page["removed_lines"]
    ]
    assert removed
    for line in removed:
        assert line["reason"]
        assert line["evidence"]
    assert cleaning["totals"]["removed_lines"] == len(removed)


def test_frequency_threshold_is_overridable(processed_dir: Path) -> None:
    assert (
        main([
            "--all", "--processed-dir", str(processed_dir),
            "--frequency-threshold", "0.1",
        ])
        == 0
    )
    cleaning = json.loads(
        (processed_dir / "VER-0123456789abcdef" / CLEANING_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert cleaning["plan"]["options"]["frequency_threshold"] == 0.1


def test_cli_requires_a_target(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])


def test_all_with_an_empty_processed_dir_reports_and_fails(tmp_path: Path) -> None:
    assert main(["--all", "--processed-dir", str(tmp_path)]) == 1


def test_extraction_path_can_be_given_directly(processed_dir: Path) -> None:
    extraction = processed_dir / "VER-0123456789abcdef" / EXTRACTION_FILENAME
    assert main(["--extraction", str(extraction)]) == 0
    assert (extraction.parent / SEGMENT_FILENAMES["paragraph"]).exists()


def test_preview_runs_without_error(processed_dir: Path) -> None:
    assert (
        main([
            "--all", "--processed-dir", str(processed_dir),
            "--no-write", "--preview",
        ])
        == 0
    )


def test_every_segment_in_the_written_files_has_page_provenance(
    processed_dir: Path,
) -> None:
    """The invariant, checked on the artefact rather than only in memory."""
    assert main(["--all", "--processed-dir", str(processed_dir)]) == 0
    version_dir = processed_dir / "VER-0123456789abcdef"
    page_count = 2
    for filename in SEGMENT_FILENAMES.values():
        payload = json.loads((version_dir / filename).read_text(encoding="utf-8"))
        assert payload["segments"]
        for segment in payload["segments"]:
            assert segment["page_offsets"], segment["segment_id"]
            assert 1 <= segment["page_start"] <= segment["page_end"] <= page_count
            touched = sorted(
                offset["page_number"] for offset in segment["page_offsets"]
            )
            assert touched[0] == segment["page_start"]
            assert touched[-1] == segment["page_end"]
