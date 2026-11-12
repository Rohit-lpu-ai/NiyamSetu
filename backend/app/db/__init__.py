"""PostgreSQL persistence for NiyamSetu (Phase 3).

SQLAlchemy Core plus plain ``.sql`` migrations, no ORM and no Alembic (D-4).
See ``README.md`` in this directory for the schema rationale, and
``docs/phase3-postgresql.md`` for the viva-facing explanation of every design
decision.

Nothing here is imported by ``backend/app/document_processing/``: the pipeline
runs to JSON artefacts with no database present, and this package loads those
artefacts. That direction is deliberate -- extraction and segmentation stay
testable and runnable on a machine with no PostgreSQL at all.
"""

from __future__ import annotations

__all__ = ["config", "engine", "migrate", "loader", "repository"]
