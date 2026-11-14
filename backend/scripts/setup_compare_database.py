"""Build the isolated database holding the genuine RBI version-pair corpus.

Run from the repository root::

    .venv\\Scripts\\python -m backend.scripts.setup_compare_database
    .venv\\Scripts\\python -m backend.scripts.setup_compare_database --status

Why a third database
--------------------
``niyamsetu`` holds the verified 4-document corpus every number in
``backend/docs/final-backend-evaluation.md`` was measured on.
``niyamsetu_sample`` holds the 17-document corpus the Library & Search
relevance numbers were measured on. Adding nine documents to either would make
a published measurement unreproducible, and there would be no way back short
of deleting rows.

So Compare's corpus lives in its own database, ``niyamsetu_compare``. The
other two are never read-modified-written by this script: the development
corpus is **re-loaded from the same JSON artefacts** into the new database,
which is a copy and not a move, so a comparison can be demonstrated against
the familiar four documents as well as the nine new ones.

Safety rules this script enforces rather than trusts
----------------------------------------------------
* The target database name **must contain "compare"**. Pointing it at
  ``niyamsetu`` or ``niyamsetu_sample`` raises before a single statement runs.
* It issues ``CREATE DATABASE`` and nothing destructive. There is no ``DROP``,
  no ``TRUNCATE`` and no ``--replace`` passthrough anywhere in this file
  (``CLAUDE.md`` §24 rule 10).
* Loading is idempotent, because ``db.loader`` is: a version already present
  is skipped by checksum, so re-running costs nothing and changes nothing.
* It loads only the versions listed in the version-pair manifest, plus the
  original development corpus.

What it does that the sample-database script does not
-----------------------------------------------------
It calls :func:`backend.app.db.loader.link_supersedes` after loading, which
resolves each manifest's ``supersedes`` (a ``version_label``, asserted by a
human who read the PDF -- D-9) into
``document_versions.supersedes_version_id``. That column is what makes a
version pair *declared* rather than inferred, and it is what
``GET /api/compare/options`` reports as ``supersedes_declared``. Without this
step the direction of every diff would rest on publication date alone, which
``CLAUDE.md`` §11.1 says explicitly is not a reliable version order.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text

from backend.app.db.config import REPO_ROOT, DatabaseConfig, load_dotenv
from backend.app.db.engine import dispose_engine
from backend.app.db.loader import (
    declarations_from_tree,
    link_supersedes,
    load_version_directory,
)
from backend.app.db.migrate import migrate
from backend.app.db.repository import corpus_counts

__all__ = [
    "compare_database_url",
    "create_database",
    "load_corpora",
    "manifest_versions",
    "link_version_chains",
    "analyze",
    "main",
]

#: Where the version-pair corpus lives. Overridable, but the name must still
#: contain "compare" -- see :func:`_assert_is_compare`.
DEFAULT_COMPARE_DB = "niyamsetu_compare"

ORIGINAL_PROCESSED = REPO_ROOT / "data" / "processed"
ORIGINAL_RAW = REPO_ROOT / "data" / "raw"
PAIRS_DIR = REPO_ROOT / "data" / "version_pairs"
PAIRS_PROCESSED = PAIRS_DIR / "processed"
PAIRS_RAW = PAIRS_DIR / "raw"
PAIRS_MANIFEST = PAIRS_DIR / "manifest.csv"


def _assert_is_compare(database: str) -> None:
    """Refuse any database whose name does not say it is this corpus's own."""
    if "compare" not in database.lower():
        raise SystemExit(
            f"refusing to build the version-pair corpus in database "
            f"{database!r}: the name must contain 'compare'. The development "
            f"database holds the verified 4-document corpus and the sample "
            f"database holds the corpus the search numbers were measured on; "
            f"neither may be modified."
        )


def compare_database_url(database: str = DEFAULT_COMPARE_DB) -> str:
    """The compare database's URL, built from the same env vars as the dev one.

    Host, port, user and password are shared with the development connection
    (one local PostgreSQL server); only the database name differs. The
    password still comes from ``.env`` and never from the command line.
    """
    _assert_is_compare(database)
    load_dotenv()
    config = DatabaseConfig.from_env()
    if config.url:
        raise SystemExit(
            "NIYAMSETU_DATABASE_URL is set to a complete URL, so the database "
            "name cannot be substituted. Unset it, or point it at the compare "
            "database yourself."
        )
    return DatabaseConfig(
        host=config.host,
        port=config.port,
        database=database,
        user=config.user,
        password=config.password,
    ).to_url()


def _maintenance_url(database: str = DEFAULT_COMPARE_DB) -> str:
    """A URL for the ``postgres`` database, used only to CREATE DATABASE."""
    _assert_is_compare(database)
    load_dotenv()
    config = DatabaseConfig.from_env()
    return DatabaseConfig(
        host=config.host,
        port=config.port,
        database="postgres",
        user=config.user,
        password=config.password,
    ).to_url()


def create_database(database: str = DEFAULT_COMPARE_DB) -> str:
    """Create the compare database if it does not exist. Never drops anything."""
    _assert_is_compare(database)
    engine = create_engine(_maintenance_url(database), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            present = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).first()
            if present:
                return "exists"
            conn.execute(text(f'CREATE DATABASE "{database}"'))
            return "created"
    finally:
        engine.dispose()


def manifest_versions() -> Dict[str, str]:
    """``{version_id: title}`` for every row of the version-pair manifest.

    The manifest is the authority on what belongs in the corpus; the version
    id is read back out of each extraction artefact so the two cannot drift.
    """
    if not PAIRS_MANIFEST.exists():
        raise SystemExit(
            f"no manifest at {PAIRS_MANIFEST}. Run "
            "data/version_pairs/fetch_version_pairs.py first."
        )
    wanted = {
        row["local_filename"]: row["title"]
        for row in csv.DictReader(PAIRS_MANIFEST.open(encoding="utf-8"))
    }
    found: Dict[str, str] = {}
    if not PAIRS_PROCESSED.exists():
        raise SystemExit(
            f"no extraction artefacts under {PAIRS_PROCESSED}. Run the "
            "extraction and segmentation CLIs first; the module docstring of "
            "data/version_pairs/README.md has the exact commands."
        )
    on_disk = set()
    for directory in sorted(PAIRS_PROCESSED.iterdir()):
        artefact = directory / "extraction.json"
        if not artefact.exists():
            continue
        document = json.loads(artefact.read_text(encoding="utf-8"))["document"]
        on_disk.add(document["filename"])
        if document["filename"] in wanted:
            found[document["version_id"]] = wanted[document["filename"]]
    for name in sorted(set(wanted) - on_disk):
        print(f"  manifest row has no extraction artefact: {name}")
    return found


def load_corpora(url: str) -> List[str]:
    """Load the development corpus and the version-pair corpus. Idempotent."""
    messages: List[str] = []

    if ORIGINAL_PROCESSED.exists():
        print("\nloading the original 4-document corpus (copied, not moved):")
        for directory in sorted(ORIGINAL_PROCESSED.iterdir()):
            if not (directory / "extraction.json").exists():
                continue
            outcome = load_version_directory(
                directory, url=url, raw_dir=ORIGINAL_RAW
            )
            print(f"  {outcome.status:24} {outcome.version_fingerprint}")
            messages.append(f"{outcome.version_fingerprint}: {outcome.status}")
    else:
        print(f"\n{ORIGINAL_PROCESSED} is absent; skipping the dev corpus.")

    print("\nloading the version-pair corpus (by manifest):")
    wanted = manifest_versions()
    for version_id in sorted(wanted):
        directory = PAIRS_PROCESSED / version_id
        if not (directory / "extraction.json").exists():
            print(f"  missing artefacts for {version_id}")
            continue
        outcome = load_version_directory(directory, url=url, raw_dir=PAIRS_RAW)
        print(f"  {outcome.status:24} {version_id}  {wanted[version_id][:46]}")
        messages.append(f"{version_id}: {outcome.status}")
    return messages


def link_version_chains(url: str) -> Dict[str, object]:
    """Resolve every declared ``supersedes`` into the real column.

    Reads the declarations out of both corpora's artefacts and links them in
    one pass, because a version's predecessor may be in the other tree -- the
    SC/ST chain's 2026 issue lives in the development corpus while its 2016,
    2017 and 2019 issues live in the version-pair corpus, and they share a
    ``document_group``.
    """
    declarations: Dict[str, str] = {}
    for tree in (ORIGINAL_PROCESSED, PAIRS_PROCESSED):
        declarations.update(declarations_from_tree(tree))
    result = link_supersedes(declarations, url=url)
    print(f"\nsupersedes links: {result['linked_count']} written")
    for line in result["linked"]:
        print(f"  {line}")
    for entry in result["unresolved"]:
        print(f"  unresolved: {entry['version']} -> {entry['supersedes']}")
        print(f"              {entry['reason']}")
    for entry in result["refused"]:
        print(f"  REFUSED   : {entry['version']} -> {entry['supersedes']}")
        print(f"              {entry['reason']}")
    return result


def analyze(url: str) -> None:
    """``ANALYZE`` so the planner has real statistics.

    Same reason the sample-database script does it: without statistics
    PostgreSQL estimates a full-text match at one row and picks the wrong
    index, so a freshly loaded database answers queries with a plan no
    deployment would ever have.
    """
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("ANALYZE"))
    finally:
        engine.dispose()


def _print_status(url: str) -> int:
    counts = corpus_counts(url=url)
    print("\ncorpus in the compare database:")
    for key in sorted(counts):
        print(f"  {key:24} {counts[key]}")

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT d.public_id, d.title, count(v.id) AS versions,
                           count(v.supersedes_version_id) AS declared_links
                      FROM documents d
                      JOIN document_versions v ON v.document_id = d.id
                     GROUP BY d.public_id, d.title
                     HAVING count(v.id) > 1
                     ORDER BY d.public_id
                    """
                )
            ).all()
            print(f"\ndocuments with more than one version: {len(rows)}")
            for row in rows:
                print(
                    f"  {row.public_id}  {row.versions} versions, "
                    f"{row.declared_links} declared supersedes link(s)"
                )
                print(f"      {row.title[:86]}")
            if not rows:
                print(
                    "  none -- Compare has nothing real to run on in this "
                    "database. Run without --status to build it."
                )
    finally:
        engine.dispose()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.setup_compare_database",
        description=(
            "Create and populate the isolated version-pair database. Creates "
            "nothing destructive and never touches niyamsetu or "
            "niyamsetu_sample."
        ),
    )
    parser.add_argument("--database", default=DEFAULT_COMPARE_DB)
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report what is in the database and exit, changing nothing.",
    )
    args = parser.parse_args(argv)

    url = compare_database_url(args.database)
    safe = DatabaseConfig(url=url).safe_repr()

    if args.status:
        print(f"target: {safe}")
        try:
            return _print_status(url)
        finally:
            dispose_engine()

    print(f"target: {safe}")
    state = create_database(args.database)
    print(f"database: {state}")

    applied = migrate(url)
    print(f"migrations applied this run: {len(applied)}")
    for name in applied:
        print(f"  {name}")

    try:
        load_corpora(url)
        link_version_chains(url)
        analyze(url)
        return _print_status(url)
    finally:
        dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
