"""Download the Library & Search sample corpus from official publisher sites.

Run from the repository root::

    .venv\\Scripts\\python data/sample_corpus/fetch_sample_corpus.py
    .venv\\Scripts\\python data/sample_corpus/fetch_sample_corpus.py --verify

What this script does, and what it deliberately does not do
-----------------------------------------------------------
The original 4-document validation corpus was downloaded by hand. That does
not scale to a dozen, and more importantly it is not reproducible: a reader
cannot check that the file in ``raw/`` is the file the URL serves. So the URL
list lives in ``sources.py``, in the repository, and every download is
recorded with the SHA-256 of the bytes that actually arrived.

* **Official publisher hosts only.** ``sources.ALLOWED_HOSTS`` is checked
  before the request is made, so a mirror or a blog cannot enter the corpus
  through a typo.
* **It never overwrites an existing file.** An already-present document is
  reported as skipped and its checksum is recomputed, so a second run is a
  verification pass rather than a re-download.
* **It validates before writing.** A 200 response carrying an HTML error page
  is the common failure mode; a file that does not start with ``%PDF-`` is
  rejected and never lands in ``raw/``.
* **It is not part of the pipeline.** Nothing in ``backend/`` imports it and
  no test runs it: the tests treat the downloaded files as fixed fixtures, so
  the suite never needs a network connection (``CLAUDE.md`` §19).
* **It does not touch ``data/raw/`` or ``data/manifest.csv``.** The original
  four-document corpus is left exactly as it is; this writes only inside
  ``data/sample_corpus/``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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

from sources import ALLOWED_HOSTS, SAMPLES, UNREACHABLE  # noqa: E402

RAW_DIR = HERE / "raw"
MANIFEST = HERE / "manifest.csv"
FETCH_LOG = HERE / "fetch-log.csv"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/131.0 Safari/537.36"
)

#: Seconds between requests. Deliberate: these are public-service sites and a
#: dozen documents is not worth hammering them for.
DELAY_SECONDS = 1.5

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
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_host(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"refusing to download from {host!r}: not an allowed publisher "
            f"host. Allowed: {sorted(ALLOWED_HOSTS)}"
        )


def original_filename(url: str) -> str:
    """The publisher's own filename, decoded, without the query string.

    IRDAI serves ``…/<percent-encoded bilingual name>.pdf/<uuid>?version=…``,
    so the last path segment is a UUID and the name is the segment before it.
    Recorded because the manifest's ``original_filename`` is meant to be what
    the publisher called the file, not what we called it.
    """
    path = urllib.parse.urlparse(url).path
    segments = [segment for segment in path.split("/") if segment]
    for segment in reversed(segments):
        decoded = urllib.parse.unquote_plus(segment)
        if decoded.lower().endswith(".pdf"):
            return decoded
    return urllib.parse.unquote_plus(segments[-1]) if segments else ""


def download(sample: Dict[str, str], *, force: bool = False) -> Dict[str, str]:
    """Fetch one document. Returns the row written to the fetch log."""
    check_host(sample["url"])
    target = RAW_DIR / sample["local_filename"]
    row: Dict[str, str] = {
        "local_filename": sample["local_filename"],
        "authority": sample["authority"],
        "url": sample["url"],
        "status": "",
        "http_status": "",
        "content_type": "",
        "bytes": "",
        "sha256": "",
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

    request = urllib.request.Request(sample["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            row["http_status"] = str(response.status)
            row["content_type"] = response.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        row.update(status="failed", note=f"{type(exc).__name__}: {str(exc)[:160]}")
        return row

    if not payload.startswith(b"%PDF-"):
        row.update(
            status="failed",
            bytes=str(len(payload)),
            note="response did not start with %PDF- (an error page?)",
        )
        return row

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    row.update(status="downloaded", bytes=str(len(payload)), sha256=sha256_of(target))
    return row


def write_manifest(rows: List[Dict[str, str]]) -> int:
    """Write a manifest in the same format ``data/manifest.csv`` uses.

    Only documents that are on disk get a row: a manifest listing a file that
    was never downloaded would make the extraction CLI fail on a missing path,
    and the gap is already recorded in the fetch log and in
    ``sources.UNREACHABLE``.
    """
    by_name = {row["local_filename"]: row for row in rows}
    written = 0
    excluded: List[Dict[str, str]] = []
    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for sample in SAMPLES:
            fetched = by_name.get(sample["local_filename"], {})
            if fetched.get("status") not in {"downloaded", "skipped_present"}:
                continue
            # A document can be downloaded and still not belong in the corpus.
            # The exclusion and its reason live on the row in sources.py; the
            # file stays on disk and in the fetch log, so the decision can be
            # reviewed instead of being inferred from an absence.
            if sample.get("excluded_reason"):
                excluded.append(sample)
                continue
            writer.writerow(
                {
                    "local_filename": sample["local_filename"],
                    "original_filename": original_filename(sample["url"]),
                    "sha256": fetched["sha256"],
                    "title": sample["title"],
                    "department": sample["department"],
                    "document_type": sample["document_type"],
                    "reference_no": sample.get("reference_no", ""),
                    "publication_date": sample["publication_date"],
                    "source_url": sample["url"],
                    "retrieved_at": fetched["retrieved_at"],
                    "document_group": sample["document_group"],
                    "version_label": sample["version_label"],
                    "supersedes": "",
                    "is_scanned": "",
                    "notes": f"{sample['authority']}. {sample['notes']} "
                    f"Found on {sample['index_page']}.",
                }
            )
            written += 1
    for sample in excluded:
        print(
            f"EXCLUDED from the manifest: {sample['local_filename']}\n"
            f"    reason: {sample['excluded_reason']}"
        )
    return written


def write_fetch_log(rows: List[Dict[str, str]]) -> None:
    with FETCH_LOG.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    # The documents that could not be fetched belong in the same record, or
    # the log silently describes a smaller selection than was actually made.
    with FETCH_LOG.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        for entry in UNREACHABLE:
            writer.writerow(
                {
                    "local_filename": "",
                    "authority": entry["authority"],
                    "url": entry["url"],
                    "status": "unreachable",
                    "http_status": "",
                    "content_type": "",
                    "bytes": "",
                    "sha256": "",
                    "retrieved_at": time.strftime("%Y-%m-%d"),
                    "note": f"{entry['reason']}; title: {entry['title']}",
                }
            )


def verify() -> int:
    """Re-check every manifest checksum against the file on disk."""
    if not MANIFEST.exists():
        print("no manifest yet; run without --verify first")
        return 1
    bad = 0
    with MANIFEST.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = RAW_DIR / row["local_filename"]
            if not path.exists():
                print(f"MISSING  {row['local_filename']}")
                bad += 1
                continue
            actual = sha256_of(path)
            if actual != row["sha256"]:
                print(f"MISMATCH {row['local_filename']}")
                print(f"         manifest {row['sha256']}")
                print(f"         on disk  {actual}")
                bad += 1
            else:
                print(f"ok       {actual[:16]}  {row['local_filename']}")
    print(f"\n{bad} problem(s)")
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the sample corpus.")
    parser.add_argument("--verify", action="store_true", help="Re-check checksums only.")
    parser.add_argument(
        "--force", action="store_true", help="Re-download files already present."
    )
    args = parser.parse_args(argv)

    if args.verify:
        return verify()

    rows: List[Dict[str, str]] = []
    for index, sample in enumerate(SAMPLES):
        row = download(sample, force=args.force)
        rows.append(row)
        print(f"{row['status']:<16} {row['bytes'] or '-':>9}  {sample['local_filename']}")
        if row["note"]:
            print(f"{'':<16} {'':>9}  {row['note']}")
        if index < len(SAMPLES) - 1 and row["status"] == "downloaded":
            time.sleep(DELAY_SECONDS)

    write_fetch_log(rows)
    written = write_manifest(rows)
    downloaded = sum(1 for row in rows if row["status"] == "downloaded")
    present = sum(1 for row in rows if row["status"] == "skipped_present")
    failed = sum(1 for row in rows if row["status"] == "failed")
    print(
        f"\n{downloaded} downloaded, {present} already present, {failed} failed."
        f"\n{len(UNREACHABLE)} further documents were selected but are unreachable "
        f"from this machine (see sources.UNREACHABLE)."
        f"\nmanifest rows: {written}  ->  {MANIFEST}"
        f"\nfetch log:            {FETCH_LOG}"
    )
    return 0 if (downloaded or present) else 1


if __name__ == "__main__":
    sys.exit(main())
