"""Database configuration, read from the environment.

Credentials never appear in source (``CLAUDE.md`` §20). The connection URL is
assembled from environment variables, optionally seeded from a git-ignored
``.env`` at the repository root. ``.env.example`` is committed with
placeholders.

A deliberately small ``.env`` reader lives here rather than a dependency on
python-dotenv: the file format we need is ``KEY=value`` with ``#`` comments, and
adding a package for that would not survive the "written justification" rule
(``CLAUDE.md`` §24 rule 5).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote_plus

__all__ = [
    "DatabaseConfig",
    "ConfigError",
    "load_dotenv",
    "database_url",
    "test_database_url",
    "REPO_ROOT",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOTENV = REPO_ROOT / ".env"

#: The variable holding a complete URL, which wins over the parts if set.
URL_VAR = "NIYAMSETU_DATABASE_URL"
#: The separate variable for the throwaway database the tests may destroy.
TEST_URL_VAR = "NIYAMSETU_TEST_DATABASE_URL"

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ConfigError(RuntimeError):
    """Configuration is missing or malformed. Carries a stable code."""

    code = "DB_CONFIG_ERROR"


def load_dotenv(path: Optional[Path] = None, *, override: bool = False) -> Dict[str, str]:
    """Read ``KEY=value`` pairs from ``path`` into ``os.environ``.

    Existing environment variables win unless ``override`` is set, so a value
    exported in the shell (or by CI) beats the file on disk. Returns what was
    parsed, which is what the tests assert on — a loader that silently did
    nothing would otherwise be indistinguishable from a correct one.
    """
    dotenv_path = Path(path) if path is not None else DEFAULT_DOTENV
    parsed: Dict[str, str] = {}
    if not dotenv_path.exists():
        return parsed

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


@dataclass(frozen=True)
class DatabaseConfig:
    """The pieces of a PostgreSQL connection, or a ready-made URL."""

    host: str = "localhost"
    port: int = 5432
    database: str = "niyamsetu"
    user: str = "postgres"
    password: str = ""
    url: Optional[str] = None

    @classmethod
    def from_env(cls, prefix: str = "NIYAMSETU_DB_") -> "DatabaseConfig":
        """Build from ``NIYAMSETU_DB_*``, or from a complete URL if given."""
        complete = os.environ.get(URL_VAR)
        if complete:
            return cls(url=complete)
        port_raw = os.environ.get(f"{prefix}PORT", "5432")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(
                f"{prefix}PORT must be an integer, got {port_raw!r}"
            ) from exc
        return cls(
            host=os.environ.get(f"{prefix}HOST", "localhost"),
            port=port,
            database=os.environ.get(f"{prefix}NAME", "niyamsetu"),
            user=os.environ.get(f"{prefix}USER", "postgres"),
            password=os.environ.get(f"{prefix}PASSWORD", ""),
        )

    def to_url(self) -> str:
        """Render a SQLAlchemy URL for the psycopg 3 driver.

        The password is percent-encoded, so a password containing ``@`` or
        ``/`` cannot silently corrupt the URL and produce a baffling
        "database does not exist".
        """
        if self.url:
            return self.url
        if not self.database:
            raise ConfigError("database name is empty")
        credentials = quote_plus(self.user)
        if self.password:
            credentials = f"{credentials}:{quote_plus(self.password)}"
        return (
            f"postgresql+psycopg://{credentials}@"
            f"{self.host}:{self.port}/{self.database}"
        )

    def safe_repr(self) -> str:
        """A form safe to log or print: never the password (``CLAUDE.md`` §20)."""
        if self.url:
            return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", self.url)
        return (
            f"postgresql+psycopg://{self.user}:***@"
            f"{self.host}:{self.port}/{self.database}"
        )


def database_url(*, load_env: bool = True) -> str:
    """The development/production URL."""
    if load_env:
        load_dotenv()
    return DatabaseConfig.from_env().to_url()


def test_database_url(*, load_env: bool = True) -> Optional[str]:
    """The URL for the throwaway test database, or ``None`` if unset.

    Separate variable, and ``None`` rather than a fallback to
    :func:`database_url`, on purpose. The database tests create and drop
    schemas; silently inheriting the development URL is how a test suite
    deletes real work. ``None`` makes those tests skip, which is a visible
    "not run" rather than an invisible catastrophe.

    The name is additionally required to contain ``test`` — see
    :func:`assert_is_test_url`.
    """
    if load_env:
        load_dotenv()
    return os.environ.get(TEST_URL_VAR) or None


def assert_is_test_url(url: str) -> None:
    """Refuse a URL that does not look like a disposable test database.

    Belt and braces over the separate variable above: even if someone pastes
    the development URL into ``NIYAMSETU_TEST_DATABASE_URL``, the database name
    must still contain ``test`` before anything destructive runs.
    """
    database = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in database.lower():
        raise ConfigError(
            f"refusing to run destructive tests against database {database!r}: "
            f"the name must contain 'test'. Set {TEST_URL_VAR} to a throwaway "
            f"database (CLAUDE.md §24 rule 10)."
        )
