"""Download the genuine RBI version-pair corpus from www.rbi.org.in.

Run from the repository root::

    .venv\\Scripts\\python data/version_pairs/fetch_version_pairs.py
    .venv\\Scripts\\python data/version_pairs/fetch_version_pairs.py --verify

Same conventions as ``data/sample_corpus/fetch_sample_corpus.py``, for the
same reasons, and the same guarantees:

* **Official publisher host only.** ``sources.ALLOWED_HOSTS`` is checked
  before the request is made.
* **It never overwrites an existing file.** An already-present document is
  reported as skipped and its checksum is recomputed, so a second run is a
  verification pass.
* **It validates before writing.** A 200 response carrying an HTML
  bot-protection challenge is the common failure mode on RBI hosts; a file
  that does not start with ``%PDF-`` is rejected and never lands in ``raw/``.
* **It is not part of the pipeline.** Nothing in ``backend/`` imports it and
  no test runs it, so the suite never needs a network connection
  (``CLAUDE.md`` §19).
* **It touches nothing outside ``data/version_pairs/``.** The original
  four-document corpus in ``data/raw/`` and the Library & Search sample corpus
  in ``data/sample_corpus/`` are left exactly as they are.

One check this script adds over the sample-corpus fetcher
---------------------------------------------------------
``--verify`` also re-reads page 1 of each PDF and checks that the reference
number recorded in ``sources.py`` actually appears in it. The whole value of
this corpus is that the version relationships are declared by the issuing
authority, and a URL that silently started serving a different year's circular
would quietly turn a genuine version pair into a comparison of two unrelated
documents. Catching that needs the document's own text, not its checksum.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sources import ALLOWED_HOSTS, CHAINS, SAMPLES  # noqa: E402

RAW_DIR = HERE / "raw"
MANIFEST = HERE / "manifest.csv"
FETCH_LOG = HERE / "fetch-log.csv"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/131.0 Safari/537.36"
)

#: The manifest columns ``backend/app/document_processing/manifest.py``
#: requires, in the order ``data/manifest.csv`` uses them. Written out rather
#: than imported so this script stays runnable on its own.
MANIFEST_COLUMNS = [
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


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_host(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host not in ALLOWED_HOSTS:
        raise SystemExit(
            f"refusing to download from {host!r}: not in ALLOWED_HOSTS "
            f"({', '.join(sorted(ALLOWED_HOSTS))}). A document whose origin "
            f"cannot be verified is not ingested (CLAUDE.md §16)."
        )


def original_filename(url: str) -> str:
    """The publisher's own filename, decoded, without the query string."""
    path = urllib.parse.urlparse(url).path
    segments = [segment for segment in path.split("/") if segment]
    for segment in reversed(segments):
        decoded = urllib.parse.unquote_plus(segment)
        if decoded.lower().endswith(".pdf"):
            return decoded
    return urllib.parse.unquote_plus(segments[-1]) if segments else ""


def download(sample: Dict[str, object], *, force: bool = False) -> Dict[str, str]:
    """Fetch one document. Returns the row written to the fetch log."""
    url = str(sample["url"])
    check_host(url)
    target = RAW_DIR / str(sample["local_filename"])
    row: Dict[str, str] = {
        "local_filename": str(sample["local_filename"]),
        "document_group": str(sample["document_group"]),
        "version_label": str(sample["version_label"]),
        "url": url,
        "status": "",
        "http_status": "",
        "content_type": "",
        "bytes": "",
        "sha256": "",
        "pages": "",
        "retrieved_at": time.strftime("%Y-%m-%d"),
        "note": "",
    }

    if target.exists() and not force:
        row.update(
            status="skipped_present",
            bytes=str(target.stat().st_size),
            sha256=sha256_of(target),
            note="already downloaded; checksum recomputed, file not touched",
        )
        return row

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
            row["http_status"] = str(response.status)
            row["content_type"] = response.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        row.update(status="failed", note=f"{type(exc).__name__}: {str(exc)[:160]}")
        return row

    if not payload.startswith(b"%PDF-"):
        head = payload[:40].decode("latin-1", "replace").replace("\n", " ")
        row.update(
            status="failed",
            bytes=str(len(payload)),
            note=(
                f"response did not start with %PDF- (begins {head!r}); "
                f"an error page or a bot-protection challenge"
            ),
        )
        return row

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    row.update(status="downloaded", bytes=str(len(payload)), sha256=sha256_of(target))
    return row


def _page_one_text(path: Path) -> str:
    """Page 1 as text, or an empty string if it cannot be read.

    ``pypdf`` is already the project's pinned extractor, so this adds no
    dependency. Failures are swallowed into an empty string: this is a
    provenance check, and a check that crashes on an odd PDF is a check nobody
    runs.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if not reader.pages:
            return ""
        return reader.pages[0].extract_text() or ""
    except Exception:  # pragma: no cover - a corrupt file is the finding
        return ""


def _page_count(path: Path) -> Optional[int]:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:  # pragma: no cover
        return None


def _normalise(value: str) -> str:
    """Strip the whitespace ``pypdf`` sprinkles into justified reference numbers."""
    return re.sub(r"\s+", "", value or "").casefold()


def verify() -> int:
    """Re-check every downloaded file: checksum, page count and reference number.

    Returns a process exit code. Non-zero means at least one document on disk
    is not the document ``sources.py`` says it is, which must fail loudly
    rather than be reported as a note.
    """
    problems = 0
    print(f"{'document':58} {'pages':>6}  reference number")
    print("-" * 100)
    for sample in SAMPLES:
        name = str(sample["local_filename"])
        path = RAW_DIR / name
        if not path.exists():
            print(f"{name[:58]:58} {'-':>6}  MISSING")
            problems += 1
            continue

        pages = _page_count(path)
        expected_pages = sample.get("expected_pages")
        page_note = ""
        if expected_pages is not None and pages != expected_pages:
            page_note = f" (expected {expected_pages})"
            problems += 1

        text = _page_one_text(path)
        reference = str(sample["reference_no"])
        # The reference number is printed across two lines in several of these
        # PDFs, so both halves are checked rather than the whole string.
        parts = [part for part in reference.split() if len(part) > 6]
        found = all(_normalise(part) in _normalise(text) for part in parts) if parts else False
        status = "ok" if found else "REFERENCE NOT FOUND ON PAGE 1"
        if not found:
            problems += 1
        print(f"{name[:58]:58} {pages!s:>6}{page_note}  {status}")
        if not found:
            print(f"{'':58}        expected: {reference}")

    print("-" * 100)
    print(f"{len(SAMPLES)} documents, {problems} problem(s)")
    return 1 if problems else 0


def write_manifest(rows: List[Dict[str, str]]) -> int:
    """Write a manifest in the same format ``data/manifest.csv`` uses.

    Only documents that are on disk get a row: a manifest listing a file that
    was never downloaded would make the extraction CLI fail on a missing path.

    ``supersedes`` is the field that makes this corpus worth having. It holds
    the ``version_label`` of the issue the document's own text names as its
    predecessor -- a human reading the PDF, which is what D-9 requires -- and
    ``backend/app/db/loader.link_supersedes`` resolves it into
    ``document_versions.supersedes_version_id`` after loading.
    """
    on_disk = {row["local_filename"]: row for row in rows if row["sha256"]}
    written = 0
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for sample in SAMPLES:
            name = str(sample["local_filename"])
            fetched = on_disk.get(name)
            if fetched is None:
                continue
            writer.writerow(
                {
                    "local_filename": name,
                    "original_filename": original_filename(str(sample["url"])),
                    "sha256": fetched["sha256"],
                    "title": sample["title"],
                    "department": sample["department"],
                    "document_type": sample["document_type"],
                    "reference_no": sample["reference_no"],
                    "publication_date": sample["publication_date"],
                    "source_url": sample["url"],
                    "retrieved_at": fetched["retrieved_at"],
                    "document_group": sample["document_group"],
                    "version_label": sample["version_label"],
                    "supersedes": sample["supersedes"],
                    "is_scanned": "false",
                    "notes": (
                        f"Genuine version chain, position "
                        f"{sample['chain_position']} of 3. "
                        f"Predecessor evidence: {sample['supersedes_evidence']} "
                        f"Index page: {sample['index_page']}"
                    ),
                }
            )
            written += 1
    return written


def write_fetch_log(rows: List[Dict[str, str]]) -> None:
    FETCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "local_filename",
                "document_group",
                "version_label",
                "url",
                "status",
                "http_status",
                "content_type",
                "bytes",
                "sha256",
                "pages",
                "retrieved_at",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the genuine RBI version-pair corpus."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Re-check checksums, page counts and reference numbers only.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even when the file is already present.",
    )
    args = parser.parse_args(argv)

    if args.verify:
        return verify()

    print(
        f"{len(CHAINS)} chains, {len(SAMPLES)} documents, "
        f"host(s): {', '.join(sorted(ALLOWED_HOSTS))}"
    )
    rows: List[Dict[str, str]] = []
    for sample in SAMPLES:
        row = download(sample, force=args.force)
        path = RAW_DIR / str(sample["local_filename"])
        if path.exists():
            count = _page_count(path)
            row["pages"] = "" if count is None else str(count)
        rows.append(row)
        print(
            f"  {row['status']:16} {str(sample['local_filename'])[:52]:52} "
            f"{row['bytes']:>8}b {row['pages']:>3}p {row['note']}"
        )

    write_fetch_log(rows)
    written = write_manifest(rows)
    failed = sum(1 for row in rows if row["status"] == "failed")
    print(f"\nmanifest rows written : {written}")
    print(f"failed downloads      : {failed}")
    print(f"manifest              : {MANIFEST}")
    print(f"fetch log             : {FETCH_LOG}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
