"""Migration runner for the plain ``.sql`` files in ``migrations/``.

No Alembic (D-4, answered by the owner). The whole mechanism is this:

1. list ``migrations/*.sql`` in filename order;
2. ask the database which filenames it has already applied;
3. apply the rest, each inside its own transaction, recording the filename and
   the SHA-256 of the file as applied.

Recording the checksum is the part that earns its keep. A migration file edited
after it was applied is a silent divergence between what the database contains
and what the repository claims it contains — the runner detects that and
refuses to continue rather than pretending the database is up to date.

Each migration file supplies its own ``BEGIN``/``COMMIT``, so the runner
executes it with ``AUTOCOMMIT`` and lets the file control its own transaction.
That keeps the DDL atomic in exactly the way the file's author intended, and
PostgreSQL makes DDL transactional, so a failed migration rolls back whole.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.config import database_url
from backend.app.db.engine import get_engine

__all__ = [
    "MigrationError",
    "Migration",
    "discover_migrations",
    "applied_migrations",
    "pending_migrations",
    "apply_migration",
    "migrate",
    "main",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: The bookkeeping table. Created by 0001 itself, hence the existence check.
MIGRATIONS_TABLE = "schema_migrations"

_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """A migration cannot be applied. Carries a stable code."""

    code = "MIGRATION_ERROR"


@dataclass(frozen=True)
class Migration:
    """One ``.sql`` file on disk."""

    path: Path
    sql: str

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Optional[Path] = None) -> List[Migration]:
    """Every migration file, in filename order.

    The ``NNNN_name.sql`` shape is enforced rather than assumed: ordering is
    lexical, so a file called ``2_fix.sql`` would sort after ``10_x.sql`` and
    apply in the wrong order. Rejecting the name is better than running the
    migrations out of sequence.
    """
    base = Path(directory) if directory is not None else MIGRATIONS_DIR
    if not base.exists():
        raise MigrationError(f"migrations directory not found: {base}")

    migrations: List[Migration] = []
    for path in sorted(base.glob("*.sql")):
        if not _FILENAME.match(path.name):
            raise MigrationError(
                f"migration filename {path.name!r} does not match "
                f"NNNN_lower_snake_case.sql; lexical order is the apply order, "
                f"so the zero padding is required"
            )
        migrations.append(
            Migration(path=path, sql=path.read_text(encoding="utf-8"))
        )
    if not migrations:
        raise MigrationError(f"no .sql migrations found in {base}")
    return migrations


def _migrations_table_exists(conn) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :name)"
            ),
            {"name": MIGRATIONS_TABLE},
        ).scalar_one()
    )


def applied_migrations(url: Optional[str] = None) -> dict:
    """``{filename: checksum}`` for what the database says it has applied."""
    engine = get_engine(url)
    with engine.connect() as conn:
        if not _migrations_table_exists(conn):
            return {}
        rows = conn.execute(
            text(f"SELECT filename, checksum FROM {MIGRATIONS_TABLE}")
        ).all()
    return {row.filename: row.checksum for row in rows}


def pending_migrations(
    migrations: Sequence[Migration], applied: dict
) -> List[Migration]:
    """Which migrations still need applying, verifying the applied ones match.

    A checksum mismatch raises. The alternative — carrying on — means the
    database and the repository disagree about the schema while every status
    display claims they agree, which is the worst of the available outcomes.
    """
    pending: List[Migration] = []
    for migration in migrations:
        recorded = applied.get(migration.filename)
        if recorded is None:
            pending.append(migration)
            continue
        if recorded != migration.checksum:
            raise MigrationError(
                f"{migration.filename} was applied with checksum "
                f"{recorded[:12]}... but the file on disk now hashes to "
                f"{migration.checksum[:12]}.... An applied migration must not "
                f"be edited; add a new migration instead."
            )
    return pending


def apply_migration(migration: Migration, url: Optional[str] = None) -> None:
    """Run one migration and record it, or leave the database untouched.

    The file carries its own ``BEGIN``/``COMMIT``, so this runs in AUTOCOMMIT
    and the file's own transaction provides atomicity. The bookkeeping row is
    then written separately — if that write failed we would re-run the
    migration next time, which is why every migration is written to be
    re-runnable or to fail loudly rather than half-apply.
    """
    engine = get_engine(url)
    raw = engine.execution_options(isolation_level="AUTOCOMMIT")
    try:
        with raw.connect() as conn:
            # Execute through the raw DBAPI cursor with **no** parameters.
            #
            # Not exec_driver_sql(): that hands the string to psycopg with an
            # empty parameter set, which switches on %-interpolation -- and our
            # PL/pgSQL trigger bodies are full of RAISE EXCEPTION format
            # strings containing '%'. psycopg then reads those as placeholders
            # and fails with "incomplete placeholder: '%'".
            #
            # Doubling the '%' in the SQL would "fix" it by corrupting the
            # error messages the triggers raise, so the fix belongs here. A
            # migration is a literal script: it must never be parameterised.
            cursor = conn.connection.cursor()
            try:
                cursor.execute(migration.sql)
            finally:
                cursor.close()
            conn.execute(
                text(
                    f"INSERT INTO {MIGRATIONS_TABLE} (filename, checksum) "
                    f"VALUES (:filename, :checksum) "
                    f"ON CONFLICT (filename) DO UPDATE SET checksum = :checksum"
                ),
                {
                    "filename": migration.filename,
                    "checksum": migration.checksum,
                },
            )
    except SQLAlchemyError as exc:
        raise MigrationError(
            f"{migration.filename} failed to apply: {exc}"
        ) from exc


def migrate(
    url: Optional[str] = None,
    directory: Optional[Path] = None,
    *,
    dry_run: bool = False,
) -> List[str]:
    """Apply every pending migration. Returns the filenames applied."""
    migrations = discover_migrations(directory)
    pending = pending_migrations(migrations, applied_migrations(url))
    if dry_run:
        return [migration.filename for migration in pending]
    applied_now: List[str] = []
    for migration in pending:
        apply_migration(migration, url)
        applied_now.append(migration.filename)
    return applied_now


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.db.migrate",
        description=(
            "Apply the plain-SQL migrations in backend/app/db/migrations/. "
            "Reads the connection URL from the environment (.env); never from "
            "the command line, so a password cannot land in shell history."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be applied and exit without touching anything.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show applied and pending migrations.",
    )
    parser.add_argument(
        "--directory", type=Path, default=None,
        help=f"Migrations directory (default: {MIGRATIONS_DIR}).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    url = database_url()

    try:
        migrations = discover_migrations(args.directory)
        applied = applied_migrations(url)

        if args.status:
            print(f"{'migration':40} status")
            print("-" * 56)
            for migration in migrations:
                recorded = applied.get(migration.filename)
                if recorded is None:
                    status = "PENDING"
                elif recorded != migration.checksum:
                    status = "MISMATCH - file edited after apply"
                else:
                    status = "applied"
                print(f"{migration.filename:40} {status}")
            return 0

        pending = pending_migrations(migrations, applied)
        if not pending:
            print("database is up to date; nothing to apply")
            return 0

        if args.dry_run:
            print("would apply:")
            for migration in pending:
                print(f"  {migration.filename}")
            return 0

        for migration in pending:
            print(f"applying {migration.filename} ...", end=" ", flush=True)
            apply_migration(migration, url)
            print("ok")
        print(f"applied {len(pending)} migration(s)")
        return 0
    except MigrationError as exc:
        print(f"ERROR [{exc.code}] {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
