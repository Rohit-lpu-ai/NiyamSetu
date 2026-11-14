"""Build the isolated sample database for Library & Search testing.

Run from the repository root::

    .venv\\Scripts\\python -m backend.scripts.setup_sample_database
    .venv\\Scripts\\python -m backend.scripts.setup_sample_database --status

Why a separate database
-----------------------
The development database ``niyamsetu`` holds the verified 4-document corpus
that every number in ``backend/docs/final-backend-evaluation.md`` was measured
on. Ingesting a dozen experimental documents into it would make those numbers
unreproducible, and there would be no way back short of deleting rows.

So the enlarged corpus lives in its own database. The development corpus is
never read-modified-written by this script: it is *re-loaded from the same
JSON artefacts* into the new database, which is a copy, not a move. Nothing in
``niyamsetu`` is touched.

Safety rules this script enforces rather than trusts
----------------------------------------------------
* The target database name **must contain "sample"**. Pointing it at
  ``niyamsetu`` raises before a single statement runs.
* It issues ``CREATE DATABASE`` and nothing destructive. There is no
  ``DROP``, no ``TRUNCATE`` and no ``--replace`` passthrough anywhere in this
  file (``CLAUDE.md`` §24 rule 10).
* Loading is idempotent, because ``db.loader`` is: a version already present
  is skipped by checksum, so re-running costs nothing and changes nothing.
* It loads only the versions listed in the sample manifest. A version
  directory that exists on disk but was deliberately excluded from the corpus
  (see ``data/sample_corpus/sources.py``) stays out of the database.
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
from backend.app.db.loader import load_version_directory
from backend.app.db.migrate import migrate
from backend.app.db.repository import corpus_counts

__all__ = [
    "sample_database_url",
    "create_database",
    "load_corpora",
    "manifest_versions",
    "analyze",
    "main",
]

#: Where the enlarged corpus lives. Overridable, but the name must still
#: contain "sample" -- see :func:`_assert_is_sample`.
DEFAULT_SAMPLE_DB = "niyamsetu_sample"

ORIGINAL_PROCESSED = REPO_ROOT / "data" / "processed"
ORIGINAL_RAW = REPO_ROOT / "data" / "raw"
SAMPLE_DIR = REPO_ROOT / "data" / "sample_corpus"
SAMPLE_PROCESSED = SAMPLE_DIR / "processed"
SAMPLE_RAW = SAMPLE_DIR / "raw"
SAMPLE_MANIFEST = SAMPLE_DIR / "manifest.csv"


def _assert_is_sample(database: str) -> None:
    """Refuse any database whose name does not say it is disposable.

    The same belt-and-braces idea as ``config.assert_is_test_url``: the
    separate environment variable is the first line of defence, and this is
    the one that still holds when someone sets it wrongly.
    """
    if "sample" not in database.lower():
        raise SystemExit(
            f"refusing to build the sample corpus in database {database!r}: "
            "the name must contain 'sample'. The development database holds "
            "the verified 4-document corpus and must not be modified."
        )


def sample_database_url(database: str = DEFAULT_SAMPLE_DB) -> str:
    """The sample database's URL, built from the same env vars as the dev one.

    Host, port, user and password are shared with the development connection
    (one local PostgreSQL server); only the database name differs. The
    password still comes from ``.env`` and never from the command line.
    """
    _assert_is_sample(database)
    load_dotenv()
    config = DatabaseConfig.from_env()
    if config.url:
        raise SystemExit(
            "NIYAMSETU_DATABASE_URL is set to a complete URL, so the database "
            "name cannot be substituted. Unset it, or point it at the sample "
            "database yourself."
        )
    return DatabaseConfig(
        host=config.host,
        port=config.port,
        database=database,
        user=config.user,
        password=config.password,
    ).to_url()


def _maintenance_url(database: str = DEFAULT_SAMPLE_DB) -> str:
    """A URL for the ``postgres`` database, used only to CREATE DATABASE.

    ``CREATE DATABASE`` cannot run inside a transaction and cannot run while
    connected to the database being created, so it needs a connection
    somewhere else.
    """
    _assert_is_sample(database)
    load_dotenv()
    config = DatabaseConfig.from_env()
    return DatabaseConfig(
        host=config.host,
        port=config.port,
        database="postgres",
        user=config.user,
        password=config.password,
    ).to_url()


def create_database(database: str = DEFAULT_SAMPLE_DB) -> str:
    """Create the sample database if it does not exist. Never drops anything.

    Returns ``"created"`` or ``"exists"``.
    """
    _assert_is_sample(database)
    engine = create_engine(_maintenance_url(database), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            present = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).first()
            if present:
                return "exists"
            # Identifier, not a value, so it cannot be bound. The name is
            # validated above and is a module constant or a CLI argument that
            # had to contain 'sample'; quote it anyway.
            conn.execute(text(f'CREATE DATABASE "{database}"'))
            return "created"
    finally:
        engine.dispose()


def manifest_versions() -> Dict[str, str]:
    """``{version_id: title}`` for every row of the sample manifest.

    The manifest is the authority on what belongs in the corpus; the
    version id is read back out of each extraction artefact so the two cannot
    drift (a manifest row whose document was never extracted simply has no
    match and is reported).
    """
    if not SAMPLE_MANIFEST.exists():
        raise SystemExit(
            f"no sample manifest at {SAMPLE_MANIFEST}. Run "
            "data/sample_corpus/fetch_sample_corpus.py first."
        )
    wanted = {
        row["local_filename"]: row["title"]
        for row in csv.DictReader(SAMPLE_MANIFEST.open(encoding="utf-8"))
    }
    found: Dict[str, str] = {}
    for directory in sorted(SAMPLE_PROCESSED.iterdir()):
        artefact = directory / "extraction.json"
        if not artefact.exists():
            continue
        document = json.loads(artefact.read_text(encoding="utf-8"))["document"]
        if document["filename"] in wanted:
            found[document["version_id"]] = wanted[document["filename"]]
    missing = set(wanted) - {
        json.loads((d / "extraction.json").read_text(encoding="utf-8"))["document"][
            "filename"
        ]
        for d in SAMPLE_PROCESSED.iterdir()
        if (d / "extraction.json").exists()
    }
    for name in sorted(missing):
        print(f"  manifest row has no extraction artefact: {name}")
    return found


def load_corpora(url: str) -> List[str]:
    """Load the original corpus and the sample corpus. Idempotent."""
    messages: List[str] = []

    print("\nloading the original 4-document corpus (copied, not moved):")
    for directory in sorted(ORIGINAL_PROCESSED.iterdir()):
        if not (directory / "extraction.json").exists():
            continue
        outcome = load_version_directory(
            directory, url=url, raw_dir=ORIGINAL_RAW, replace=False
        )
        line = f"  {outcome.status:<18} {directory.name}"
        print(line)
        messages.append(line)

    print("\nloading the sample corpus (manifest rows only):")
    for version_id, title in manifest_versions().items():
        outcome = load_version_directory(
            SAMPLE_PROCESSED / version_id,
            url=url,
            raw_dir=SAMPLE_RAW,
            replace=False,
        )
        line = f"  {outcome.status:<18} {version_id}  {title[:44]}"
        print(line)
        messages.append(line)

    skipped = [
        d.name
        for d in sorted(SAMPLE_PROCESSED.iterdir())
        if (d / "extraction.json").exists() and d.name not in manifest_versions()
    ]
    for name in skipped:
        print(f"  not in manifest, not loaded: {name}")

    analyze(url)
    return messages


#: Tables the planner needs statistics for before it can cost a search.
ANALYZED_TABLES = (
    "documents",
    "document_versions",
    "pages",
    "segments",
    "segment_pages",
)


def analyze(url: str) -> None:
    """``ANALYZE`` the loaded tables. Not optional after a bulk load.

    Measured, not assumed: immediately after loading, the planner has no
    statistics for ``segments``, estimates every ``text_search @@ query``
    match at one row, and picks ``segments_version_method_ordinal_idx`` over
    the GIN index -- so the search runs as an index scan with a filter instead
    of a bitmap index scan. After ``ANALYZE`` the same query uses
    ``segments_text_search_idx``.

    It collects statistics and writes none of the corpus, so it is safe under
    the read-only rule that governs everything else here.
    """
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for table in ANALYZED_TABLES:
                conn.execute(text(f"ANALYZE {table}"))
        print(
            f"\nANALYZE run on {len(ANALYZED_TABLES)} tables "
            "(the planner needs statistics to choose the GIN index)"
        )
    finally:
        engine.dispose()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the sample database.")
    parser.add_argument(
        "--database", default=DEFAULT_SAMPLE_DB,
        help=f"Target database name; must contain 'sample' (default: {DEFAULT_SAMPLE_DB}).",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Report what is in the sample database and exit without changing it.",
    )
    args = parser.parse_args(argv)

    url = sample_database_url(args.database)
    safe = url.split("@", 1)[-1]
    print(f"target: postgresql+psycopg://***@{safe}")

    if args.status:
        try:
            counts = corpus_counts(url=url)
        except Exception as exc:  # noqa: BLE001 - a status call reports, never raises
            print(f"cannot read the sample database: {type(exc).__name__}: {exc}")
            return 1
        print(json.dumps(counts, indent=2))
        return 0

    state = create_database(args.database)
    print(f"database: {state}")

    applied = migrate(url=url)
    if applied:
        for migration in applied:
            print(f"migration applied: {migration}")
    else:
        print("migrations: already up to date")

    load_corpora(url)

    counts = corpus_counts(url=url)
    print("\nsample corpus now holds:")
    for key, value in counts.items():
        print(f"  {key:<24} {value}")

    dispose_engine()
    print(
        "\nPoint the API at it with:\n"
        f"  set NIYAMSETU_DB_NAME={args.database}\n"
        "  .venv\\Scripts\\python -m uvicorn backend.app.api.main:app --reload\n"
        "The development database is untouched."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
