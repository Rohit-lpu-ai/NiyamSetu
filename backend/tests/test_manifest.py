"""Manifest reading: provenance carried faithfully, blanks left blank."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from backend.app.document_processing.cli import main
from backend.app.document_processing.manifest import ManifestError, load_manifest

HEADER = [
    "local_filename",
    "original_filename",
    "sha256",
    "title",
    "department",
    "document_type",
    "reference_no",
    "publication_date",
    "source_url",
    "retrieved_at",
    "document_group",
    "version_label",
    "supersedes",
    "is_scanned",
    "notes",
]


def write_manifest(directory: Path, rows: list[dict[str, str]], header=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header or HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def base_row(filename: str, **overrides: str) -> dict[str, str]:
    row = {name: "" for name in HEADER}
    row.update(
        local_filename=filename,
        original_filename=f"ORIGINAL_{filename.upper()}",
        title="A Fixture Notification",
        source_url="https://example.invalid/fixture.pdf",
        retrieved_at="2026-10-28",
        document_group="fixture-policy",
        version_label="v1",
        is_scanned="false",
    )
    row.update(overrides)
    return row


def test_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="No manifest"):
        load_manifest(tmp_path / "manifest.csv")


def test_missing_required_column_raises(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "data",
        [{"local_filename": "a.pdf"}],
        header=["local_filename"],
    )
    with pytest.raises(ManifestError, match="missing required column"):
        load_manifest(path)


def test_blank_local_filename_raises_with_the_line_number(tmp_path: Path) -> None:
    path = write_manifest(tmp_path / "data", [base_row("")])
    with pytest.raises(ManifestError, match=":2 has a blank local_filename"):
        load_manifest(path)


def test_entries_resolve_against_the_raw_directory(tmp_path: Path) -> None:
    path = write_manifest(tmp_path / "data", [base_row("doc.pdf")])
    entry = load_manifest(path)[0]
    assert entry.pdf_path == tmp_path / "data" / "raw" / "doc.pdf"


def test_blank_optional_columns_become_none_not_empty_strings(tmp_path: Path) -> None:
    """``department=''`` is a documented gap, and must not become ``''``."""
    path = write_manifest(tmp_path / "data", [base_row("doc.pdf", department="")])
    metadata = load_manifest(path)[0].source_metadata
    assert metadata.issuing_department is None
    assert metadata.document_type is None
    assert metadata.publication_date is None
    assert metadata.title == "A Fixture Notification"


def test_populated_columns_are_carried_verbatim(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "data",
        [
            base_row(
                "doc.pdf",
                department="Fixture Department",
                document_type="circular",
                reference_no="FIX/2026-27/001",
                publication_date="2026-10-01",
                supersedes="v0",
            )
        ],
    )
    metadata = load_manifest(path)[0].source_metadata
    assert metadata.issuing_department == "Fixture Department"
    assert metadata.reference_no == "FIX/2026-27/001"
    assert metadata.supersedes == "v0"


def test_is_scanned_is_parsed_as_a_boolean(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "data",
        [base_row("a.pdf", is_scanned="true"), base_row("b.pdf", is_scanned="false")],
    )
    entries = load_manifest(path)
    assert entries[0].is_scanned is True
    assert entries[1].is_scanned is False


# --------------------------------------------------------------------------- #
# Manifest-driven CLI run
# --------------------------------------------------------------------------- #


def test_cli_manifest_mode_applies_per_row_metadata(
    tmp_path: Path, three_page_pdf: Path
) -> None:
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "doc.pdf").write_bytes(three_page_pdf.read_bytes())
    manifest = write_manifest(
        data_dir,
        [base_row("doc.pdf", title="Row Title", document_type="scheme")],
    )

    out_dir = tmp_path / "processed"
    assert main(["--manifest", str(manifest), "--output-dir", str(out_dir)]) == 0

    written = list(out_dir.rglob("extraction.json"))
    assert len(written) == 1
    document = json.loads(written[0].read_text(encoding="utf-8"))["document"]
    assert document["source_metadata"]["title"] == "Row Title"
    assert document["source_metadata"]["document_type"] == "scheme"
    assert document["source_metadata"]["document_group"] == "fixture-policy"


def test_cli_manifest_checksum_mismatch_is_reported(
    tmp_path: Path, three_page_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A changed file must be reported, not silently accepted."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "doc.pdf").write_bytes(three_page_pdf.read_bytes())
    manifest = write_manifest(data_dir, [base_row("doc.pdf", sha256="0" * 64)])

    exit_code = main(
        ["--manifest", str(manifest), "--verify-checksums", "--no-write"]
    )
    assert exit_code == 1
    assert "CHECKSUM_MISMATCH" in capsys.readouterr().err


def test_cli_manifest_continues_past_a_missing_download(
    tmp_path: Path, three_page_pdf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One absent file must not block the rest of the corpus."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "present.pdf").write_bytes(three_page_pdf.read_bytes())
    manifest = write_manifest(
        data_dir, [base_row("absent.pdf"), base_row("present.pdf")]
    )

    exit_code = main(["--manifest", str(manifest), "--no-write"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "SOURCE_FILE_NOT_FOUND" in captured.err
    assert "pages              : 3" in captured.out


def test_cli_rejects_manifest_together_with_named_pdfs(
    tmp_path: Path, three_page_pdf: Path
) -> None:
    manifest = write_manifest(tmp_path / "data", [base_row("doc.pdf")])
    with pytest.raises(SystemExit) as excinfo:
        main(["--manifest", str(manifest), str(three_page_pdf)])
    assert excinfo.value.code == 2


def test_cli_requires_either_a_path_or_a_manifest() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
