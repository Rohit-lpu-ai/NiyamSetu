"""Command-line behaviour: output paths, overwrite policy, exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.document_processing import OutputExistsError, extract_pdf
from backend.app.document_processing.cli import (
    default_output_path,
    main,
    write_result,
)


def test_cli_writes_json_to_the_default_location(
    three_page_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "processed"
    exit_code = main([str(three_page_pdf), "--output-dir", str(out_dir)])
    assert exit_code == 0

    version_id = extract_pdf(three_page_pdf).document.version_id
    written = default_output_path(out_dir, version_id)
    assert written.exists()

    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["document"]["page_count"] == 3
    assert len(payload["pages"]) == 3

    captured = capsys.readouterr().out
    assert "pages              : 3" in captured
    assert "pages with text    : 3 / 3" in captured


def test_cli_respects_an_explicit_output_path(
    three_page_pdf: Path, tmp_path: Path
) -> None:
    target = tmp_path / "custom" / "result.json"
    assert main([str(three_page_pdf), "--output", str(target)]) == 0
    assert target.exists()


def test_cli_does_not_print_the_whole_document_by_default(
    three_page_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 37-page circular must not flood the terminal."""
    main([str(three_page_pdf), "--output-dir", str(tmp_path), "--no-write"])
    captured = capsys.readouterr().out
    assert "filler line" not in captured


def test_cli_preview_flag_shows_a_short_snippet(
    three_page_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([str(three_page_pdf), "--no-write", "--preview"])
    captured = capsys.readouterr().out
    assert "preview (page 1)" in captured
    assert "FIXTURE PAGE 1 OF" in captured


def test_cli_reports_a_missing_file_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(tmp_path / "absent.pdf")])
    assert exit_code == 1
    assert "SOURCE_FILE_NOT_FOUND" in capsys.readouterr().err


def test_cli_reports_a_non_pdf_and_exits_nonzero(
    html_file_named_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(html_file_named_pdf), "--no-write"])
    assert exit_code == 1
    assert "UNSUPPORTED_FILE_TYPE" in capsys.readouterr().err


def test_cli_continues_past_one_bad_file_and_still_fails_overall(
    three_page_pdf: Path,
    html_file_named_pdf: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            str(html_file_named_pdf),
            str(three_page_pdf),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "UNSUPPORTED_FILE_TYPE" in captured.err
    # The good file was still processed rather than abandoned.
    assert "pages              : 3" in captured.out


def test_cli_reports_warnings_for_a_blank_page(
    pdf_with_blank_middle_page: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([str(pdf_with_blank_middle_page), "--no-write"])
    captured = capsys.readouterr().out
    assert "extraction_status  : partial" in captured
    assert "empty pages        : 1" in captured
    assert "page 2" in captured


def test_cli_rejects_output_with_multiple_inputs(
    three_page_pdf: Path, pdf_with_text_poor_page: Path, tmp_path: Path
) -> None:
    """argparse exits with code 2 on a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                str(three_page_pdf),
                str(pdf_with_text_poor_page),
                "--output",
                str(tmp_path / "x.json"),
            ]
        )
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# Overwrite policy
# --------------------------------------------------------------------------- #


def test_write_refuses_to_overwrite_without_force(
    three_page_pdf: Path, tmp_path: Path
) -> None:
    result = extract_pdf(three_page_pdf)
    target = tmp_path / "out.json"
    write_result(result, target)
    with pytest.raises(OutputExistsError):
        write_result(result, target)


def test_write_overwrites_with_force(three_page_pdf: Path, tmp_path: Path) -> None:
    result = extract_pdf(three_page_pdf)
    target = tmp_path / "out.json"
    target.write_text("stale", encoding="utf-8")
    write_result(result, target, force=True)
    assert json.loads(target.read_text(encoding="utf-8"))["document"]["page_count"] == 3


def test_cli_skips_an_existing_output_and_leaves_it_untouched(
    three_page_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The safe default: never clobber a file somebody may have inspected."""
    out_dir = tmp_path / "processed"
    assert main([str(three_page_pdf), "--output-dir", str(out_dir)]) == 0

    version_id = extract_pdf(three_page_pdf).document.version_id
    written = default_output_path(out_dir, version_id)
    written.write_text("hand-edited", encoding="utf-8")

    exit_code = main([str(three_page_pdf), "--output-dir", str(out_dir)])
    assert exit_code == 1
    assert "OUTPUT_EXISTS" in capsys.readouterr().err
    assert written.read_text(encoding="utf-8") == "hand-edited"

    assert main([str(three_page_pdf), "--output-dir", str(out_dir), "--force"]) == 0
    assert written.read_text(encoding="utf-8") != "hand-edited"


# --------------------------------------------------------------------------- #
# Metadata and tuning flags
# --------------------------------------------------------------------------- #


def test_cli_passes_source_metadata_through(
    three_page_pdf: Path, tmp_path: Path
) -> None:
    target = tmp_path / "meta.json"
    main(
        [
            str(three_page_pdf),
            "--output",
            str(target),
            "--title",
            "Fixture Notification",
            "--document-group",
            "fixture-policy",
            "--version-label",
            "v1",
        ]
    )
    document = json.loads(target.read_text(encoding="utf-8"))["document"]
    assert document["source_metadata"]["title"] == "Fixture Notification"
    assert document["source_metadata"]["version_label"] == "v1"
    assert document["source_metadata"]["document_group"] == "fixture-policy"


def test_cli_no_normalize_leaves_the_text_untouched(
    three_page_pdf: Path, tmp_path: Path
) -> None:
    target = tmp_path / "raw.json"
    main([str(three_page_pdf), "--output", str(target), "--no-normalize"])
    payload = json.loads(target.read_text(encoding="utf-8"))
    page = payload["pages"][0]
    assert page["normalized_text"] == page["original_text"]
    assert payload["document"]["normalization_options"]["unicode_nfkc"] is False
