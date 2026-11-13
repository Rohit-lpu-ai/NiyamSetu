"""FastAPI layer (Phase 4).

Exposes the existing pipeline and database through HTTP. Contains no
extraction, cleaning, segmentation or persistence logic of its own -- every
endpoint delegates to ``document_processing`` or ``db``, so the API and the
CLI cannot produce different results from the same PDF.

    create_app()  -- the application factory
    app           -- the module-level instance uvicorn serves
"""

from __future__ import annotations

__all__ = ["create_app", "app"]


def __getattr__(name: str):
    # Imported lazily so `import backend.app.api` does not require fastapi to
    # be installed -- the document_processing tests import sibling packages and
    # must keep working on a machine with only pypdf and pytest.
    if name in ("create_app", "app"):
        from backend.app.api.main import app, create_app

        return {"create_app": create_app, "app": app}[name]
    raise AttributeError(name)
