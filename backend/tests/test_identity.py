"""Identifier derivation: stable, content-derived, and never guessed."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.document_processing import (
    compute_bytes_checksum,
    compute_file_checksum,
    document_id_from_group,
    version_id_from_checksum,
)

#: Known-answer test: the SHA-256 of b"" is a published constant, so this check
#: catches an accidental change of hash algorithm, not just a change of output.
SHA256_OF_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_bytes_checksum_matches_the_known_sha256_of_empty_input() -> None:
    assert compute_bytes_checksum(b"") == SHA256_OF_EMPTY


def test_file_and_bytes_checksums_agree(tmp_path: Path) -> None:
    payload = b"government notification 42/2026" * 100
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)
    assert compute_file_checksum(path) == compute_bytes_checksum(payload)


def test_file_checksum_is_chunk_boundary_safe(tmp_path: Path) -> None:
    """A file larger than the 1 MiB read chunk must hash the same as in memory."""
    payload = b"x" * (1024 * 1024 + 7)
    path = tmp_path / "big.bin"
    path.write_bytes(payload)
    assert compute_file_checksum(path) == compute_bytes_checksum(payload)


def test_version_id_is_derived_deterministically_from_the_checksum() -> None:
    checksum = compute_bytes_checksum(b"anything")
    assert version_id_from_checksum(checksum) == f"VER-{checksum[:16]}"
    assert version_id_from_checksum(checksum) == version_id_from_checksum(checksum)


def test_version_id_rejects_a_truncated_checksum() -> None:
    with pytest.raises(ValueError):
        version_id_from_checksum("abc")


def test_same_group_key_yields_the_same_document_id_for_different_files() -> None:
    a = document_id_from_group("scheme-x", compute_bytes_checksum(b"file one"))
    b = document_id_from_group("scheme-x", compute_bytes_checksum(b"file two"))
    assert a == b


def test_group_key_is_whitespace_insensitive() -> None:
    checksum = compute_bytes_checksum(b"f")
    assert document_id_from_group("  scheme-x  ", checksum) == document_id_from_group(
        "scheme-x", checksum
    )


def test_absent_group_key_falls_back_to_the_file_checksum() -> None:
    """No group key means no asserted relationship, so no shared identity."""
    one = compute_bytes_checksum(b"file one")
    two = compute_bytes_checksum(b"file two")
    assert document_id_from_group(None, one) == f"DOC-{one[:16]}"
    assert document_id_from_group(None, one) != document_id_from_group(None, two)


def test_blank_group_key_is_treated_as_absent() -> None:
    checksum = compute_bytes_checksum(b"f")
    assert document_id_from_group("   ", checksum) == document_id_from_group(
        None, checksum
    )


def test_document_and_version_ids_use_distinct_prefixes() -> None:
    checksum = compute_bytes_checksum(b"f")
    assert document_id_from_group(None, checksum).startswith("DOC-")
    assert version_id_from_checksum(checksum).startswith("VER-")
