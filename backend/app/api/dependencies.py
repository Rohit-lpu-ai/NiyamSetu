"""Dependency injection seams.

Two things are injected, and both exist so the API can be tested without a
PostgreSQL server:

* :class:`Repository` -- a thin object wrapping the read queries in
  ``backend.app.db.repository``. The module is a set of functions taking a
  ``url=``; this gives the routes one object to depend on and the tests one
  object to replace.
* :class:`Settings` -- configuration read from the environment, never
  hardcoded (``CLAUDE.md`` §20).

The wrapper adds no logic. It forwards, and that is the point: if it started
reshaping payloads there would be two places defining what a clause looks
like.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.db import repository as repository_module
from backend.app.db.config import REPO_ROOT, database_url, load_dotenv
from backend.app.db.engine import ping
from backend.app.document_processing import DEFAULT_MAX_FILE_SIZE_BYTES

__all__ = [
    "Settings",
    "Repository",
    "get_settings",
    "get_repository",
    "get_database_status",
]

API_VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. No secrets are stored on this object.

    The database URL is resolved lazily, per call, rather than held here --
    a frozen dataclass containing a password is a password that ends up in a
    repr, a log line or a traceback.
    """

    api_version: str = API_VERSION
    max_upload_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    processed_dir: Path = REPO_ROOT / "data" / "processed"
    raw_dir: Path = REPO_ROOT / "data" / "raw"
    docs_enabled: bool = True
    #: Browser origins allowed to call this API. The search UI runs on its own
    #: port, so without this it cannot read a single response. A tuple rather
    #: than a list because Settings is frozen and a list is not hashable.
    cors_origins: tuple = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            max_upload_bytes=int(
                os.environ.get(
                    "NIYAMSETU_MAX_UPLOAD_BYTES", DEFAULT_MAX_FILE_SIZE_BYTES
                )
            ),
            processed_dir=Path(
                os.environ.get("NIYAMSETU_PROCESSED_DIR", REPO_ROOT / "data" / "processed")
            ),
            raw_dir=Path(
                os.environ.get("NIYAMSETU_RAW_DIR", REPO_ROOT / "data" / "raw")
            ),
            # Swagger is on by default for development and can be switched off
            # for a deployment that should not advertise its surface.
            docs_enabled=os.environ.get("NIYAMSETU_DOCS", "1") != "0",
            cors_origins=_cors_origins(),
        )


def _cors_origins() -> tuple:
    """Allowed browser origins, from NIYAMSETU_CORS_ORIGINS if set.

    Comma-separated. ``*`` is accepted for a throwaway local demo and is
    deliberately not the default: this API serves document text, and a
    wide-open default is the kind of thing that survives into a deployment.
    """
    raw = os.environ.get("NIYAMSETU_CORS_ORIGINS")
    if raw is None:
        return Settings.__dataclass_fields__["cors_origins"].default
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    return parts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


class Repository:
    """Read access to the stored corpus. Forwards to the repository module.

    Holds a URL so a test can point one instance at the throwaway database
    without touching process-wide state.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = url

    @property
    def url(self) -> Optional[str]:
        return self._url

    def list_documents(self, **kwargs: Any) -> Dict[str, Any]:
        return repository_module.list_documents(url=self._url, **kwargs)

    def get_document(self, document_id: str) -> Dict[str, Any]:
        return repository_module.get_document(document_id, url=self._url)

    def get_pages(self, document_id: str, **kwargs: Any) -> Dict[str, Any]:
        return repository_module.get_pages(document_id, url=self._url, **kwargs)

    def get_segments(self, document_id: str, **kwargs: Any) -> Dict[str, Any]:
        return repository_module.get_segments(document_id, url=self._url, **kwargs)

    def get_segment(self, clause_id: str) -> Dict[str, Any]:
        return repository_module.get_segment(clause_id, url=self._url)

    def get_citation(self, clause_id: str) -> Dict[str, Any]:
        return repository_module.get_citation(clause_id, url=self._url)

    def corpus_counts(self) -> Dict[str, Any]:
        return repository_module.corpus_counts(url=self._url)

    def ping(self) -> bool:
        return ping(self._url)


def get_repository() -> Repository:
    """The default repository, bound to the configured database.

    Overridden wholesale in tests via ``app.dependency_overrides``.
    """
    return Repository()


def get_database_status() -> str:
    """``'ok'`` or ``'unreachable'``. Never raises.

    Resolving the URL can itself fail (a missing or malformed ``.env``), and a
    health check that returns 500 because configuration is wrong tells the
    operator less than one that says the database is unreachable.
    """
    try:
        return "ok" if ping(database_url()) else "unreachable"
    except Exception:  # noqa: BLE001 - deliberately total; see the docstring
        return "unreachable"
