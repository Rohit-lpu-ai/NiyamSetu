"""Stable, content-derived identifiers for documents and versions.

Why this module exists
----------------------
Phase 1 has no database, so there is no sequence to hand out ``DOC-0001`` style
keys. We still need identifiers that are **stable across runs**: processing the
same PDF twice must yield the same ``version_id``, otherwise every re-run looks
like a brand new document and duplicate detection becomes impossible.

The rules
---------
``checksum``
    SHA-256 of the file's bytes. This *is* the identity of the physical file.
    Two uploads with the same checksum are the same file, full stop — that is
    how duplicates are detected without comparing text.

``version_id``
    Derived from the checksum: ``VER-<first 16 hex characters>``. Deterministic,
    so re-processing is idempotent, and it changes the moment a single byte of
    the source PDF changes.

``document_id``
    The *logical policy* identity — the thing that has versions. This cannot be
    derived from the file bytes, because two versions of one policy are two
    different files that must share one ``document_id``. So it is derived from a
    caller-supplied ``document_group`` key (the ``document_group`` column of
    ``data/manifest.csv``): ``DOC-<sha256(document_group)[:16]>``.

    If no ``document_group`` is supplied, the document id falls back to the
    file's own checksum, which means **every file becomes its own logical
    document**. That is intentional. ``CLAUDE.md`` Open Decision D-9 says the
    pipeline must never guess that an upload is a new version of an existing
    policy; a human asserts it by supplying the group key.

Bridging to PostgreSQL (Phase 3)
--------------------------------
``CLAUDE.md`` §11.3 specifies sequential ``public_id`` values (``DOC-0001``).
Those are assigned by the database when it exists. The identifiers here are the
*content fingerprint* that the database rows will be keyed on and joined to, so
the two schemes coexist: the fingerprint is how we recognise a file we have
already seen, the sequence is how the API refers to it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

#: How many hex characters of the digest to keep in an identifier. 16 hex chars
#: is 64 bits of the digest - far more than enough to keep a corpus of a few
#: thousand documents collision-free, and short enough to read in a log line.
_ID_HEX_LENGTH = 16

#: Read the file in chunks so a large PDF never has to sit in memory twice.
_CHUNK_BYTES = 1024 * 1024


def compute_file_checksum(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of the file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_bytes_checksum(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of an in-memory payload."""
    return hashlib.sha256(data).hexdigest()


def version_id_from_checksum(checksum: str) -> str:
    """Derive the deterministic version identifier from a file checksum."""
    if len(checksum) < _ID_HEX_LENGTH:
        raise ValueError(f"checksum is too short to derive an id: {checksum!r}")
    return f"VER-{checksum[:_ID_HEX_LENGTH]}"


def document_id_from_group(document_group: Optional[str], checksum: str) -> str:
    """Derive the logical document identifier.

    Args:
        document_group: The human-asserted key shared by every version of one
            policy, from ``data/manifest.csv``. ``None`` or blank means "this
            file is its own logical document".
        checksum: The file checksum, used only for the fallback.
    """
    if document_group is not None and document_group.strip():
        key = document_group.strip()
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"DOC-{digest[:_ID_HEX_LENGTH]}"
    return f"DOC-{checksum[:_ID_HEX_LENGTH]}"
