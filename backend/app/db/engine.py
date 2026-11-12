"""SQLAlchemy Core engine and connection helpers.

**Core, not the ORM** (D-4, confirmed by the owner). Every statement in this
package is written as SQL and executed through :func:`sqlalchemy.text` with
bound parameters. That choice is deliberate and defensible:

* the schema has exactly one definition — the ``.sql`` migration files — so
  there is no second, drifting Python description of the same tables;
* the SQL stays readable, which is the whole reason Core was chosen over the
  ORM (``CLAUDE.md`` §11.5): the schema must be explainable in a viva;
* parameters are bound, never interpolated, so injection is structurally
  impossible (``CLAUDE.md`` §20).

Connections come from a module-level engine with a small pool. There is no
session, no identity map, and no lazy loading, which means no query runs that
the reader of this code cannot see.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from backend.app.db.config import ConfigError, database_url

__all__ = [
    "get_engine",
    "dispose_engine",
    "connection",
    "transaction",
    "ping",
]

_ENGINE: Optional[Engine] = None
_ENGINE_URL: Optional[str] = None


def get_engine(url: Optional[str] = None, *, echo: bool = False) -> Engine:
    """Return the process-wide engine, creating it on first use.

    Passing a different ``url`` replaces the cached engine, which is what the
    tests need in order to point at the throwaway database without leaking a
    connection to the development one.
    """
    global _ENGINE, _ENGINE_URL
    target = url or database_url()
    if _ENGINE is None or target != _ENGINE_URL:
        dispose_engine()
        _ENGINE = create_engine(
            target,
            echo=echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=2,
            future=True,
        )
        _ENGINE_URL = target
    return _ENGINE


def dispose_engine() -> None:
    """Close every pooled connection. Called by test teardown."""
    global _ENGINE, _ENGINE_URL
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _ENGINE_URL = None


@contextmanager
def connection(url: Optional[str] = None) -> Iterator[Connection]:
    """A connection with **no** implicit transaction. Reads only.

    Use :func:`transaction` for anything that writes.
    """
    engine = get_engine(url)
    with engine.connect() as conn:
        yield conn


@contextmanager
def transaction(url: Optional[str] = None) -> Iterator[Connection]:
    """One explicit transaction: commit on success, roll back on any exception.

    This is the mechanism behind "one version per transaction"
    (``CLAUDE.md`` §10.2). A version that fails halfway through ingestion
    leaves **nothing** behind — not a document row, not a page, not a partially
    segmented clause — so the APIs can never read a half-ingested version.

    It also makes the schema's deferred provenance trigger meaningful: that
    trigger fires at COMMIT, so the commit here is the moment "every segment
    has a page" is actually checked.
    """
    engine = get_engine(url)
    with engine.connect() as conn:
        with conn.begin():
            yield conn


def ping(url: Optional[str] = None) -> bool:
    """Is the database reachable? Returns a bool rather than raising.

    This is what a future ``/api/health`` reports on (``CLAUDE.md`` §12.3), and
    a health check that raises is a health check that takes the process down.
    """
    try:
        with connection(url) as conn:
            return conn.execute(text("SELECT 1")).scalar_one() == 1
    except ConfigError:
        raise
    except Exception:
        return False
