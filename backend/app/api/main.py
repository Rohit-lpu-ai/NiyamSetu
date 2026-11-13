"""The FastAPI application factory.

Run it:

    .venv\\Scripts\\python -m uvicorn backend.app.api.main:app --reload

Swagger UI is at ``/docs``, ReDoc at ``/redoc``, and the OpenAPI document at
``/openapi.json``.

A factory (``create_app``) rather than a module-level app built by side
effect: the tests need to build an application with overridden dependencies
and no database, and that is only possible if construction is a function call.
The module-level ``app`` exists for uvicorn and simply calls the factory.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from backend.app.api.dependencies import Settings, get_settings
from backend.app.api.errors import install_exception_handlers
from backend.app.api.routes import documents_router, health_router

__all__ = ["create_app", "app", "API_PREFIX"]

#: Everything lives under /api. CLAUDE.md §12 fixes this prefix, and the four
#: consumers code against it.
API_PREFIX = "/api"

DESCRIPTION = """
Backend for **NiyamSetu**, an NLP-powered government policy intelligence system.

Ingests official policy PDFs and exposes them as documents, versions, pages and
clauses — with **page-level provenance preserved end to end**.

### What makes this API unusual

Every clause carries the pages it came from and the character range on each.
A clause spanning two pages returns **both**, never collapsed onto one. The
`offset_basis` field on every response names which page text those character
ranges index into (`normalized_text`) — slice a different field and the span
will be wrong.

### Not here yet

Ask (RAG), Compare, classification and library search are other modules'
work. This API is the contract they read through.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Pass ``settings`` to override configuration."""
    resolved = settings or get_settings()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    application = FastAPI(
        title="NiyamSetu API",
        version=resolved.api_version,
        description=DESCRIPTION,
        # Switchable so a deployment need not advertise its surface; on by
        # default for development.
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url="/redoc" if resolved.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )

    install_exception_handlers(application)
    application.include_router(health_router, prefix=API_PREFIX)
    application.include_router(documents_router, prefix=API_PREFIX)

    if settings is not None:
        # Honour an explicitly supplied Settings everywhere get_settings is
        # injected, instead of the lru_cached environment one.
        application.dependency_overrides[get_settings] = lambda: resolved

    return application


app = create_app()
