"""The API error envelope and the handlers that produce it.

Every non-2xx response in this application has the shape ``CLAUDE.md`` §12.1
mandates, with no exceptions:

```json
{"error": {"code": "DOCUMENT_NOT_FOUND", "message": "...", "details": {}}}
```

That is enforced here rather than left to each route, because an error shape
that is *usually* consistent is one four consumers have to write defensive
parsing for.

The second rule this module exists to keep: **an internal exception never
reaches the client** (``CLAUDE.md`` §20). Stack traces, file paths, SQL and
connection strings go to the server log; the client gets a stable code and a
sentence. The catch-all handler is deliberately the least informative one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.db.loader import LoadError, VersionConflict
from backend.app.db.repository import NotFound
from backend.app.document_processing.errors import PdfIngestionError

logger = logging.getLogger("niyamsetu.api")

__all__ = [
    "ApiError",
    "error_body",
    "install_exception_handlers",
]


class ApiError(Exception):
    """An error the API raises deliberately, carrying its own code and status."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details: Dict[str, Any] = details or {}


def error_body(
    code: str, message: str, details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build the §12.1 envelope. The single place this shape is constructed."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _json(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=error_body(code, message, details)
    )


#: Maps a document-processing exception's stable ``code`` to an HTTP status.
#: The codes come from ``document_processing/errors.py`` and are reused rather
#: than re-invented, so one vocabulary spans the pipeline and the API.
_PIPELINE_STATUS: Dict[str, int] = {
    "UNSUPPORTED_FILE_TYPE": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "FILE_TOO_LARGE": status.HTTP_413_CONTENT_TOO_LARGE,
    "EMPTY_FILE": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "PDF_ENCRYPTED": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "PDF_HAS_NO_PAGES": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "EXTRACTION_FAILED": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "SEGMENT_PROVENANCE_ERROR": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "SOURCE_FILE_NOT_FOUND": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def install_exception_handlers(app: FastAPI) -> None:
    """Register every handler. Order does not matter; specificity does."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _json(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return _json(status.HTTP_404_NOT_FOUND, "NOT_FOUND", str(exc))

    @app.exception_handler(VersionConflict)
    async def _conflict(_: Request, exc: VersionConflict) -> JSONResponse:
        # 409: the request was well formed and the server refuses because
        # existing data disagrees. Nothing was changed -- say so, because the
        # client's next move depends on it.
        return _json(
            status.HTTP_409_CONFLICT,
            "VERSION_CONFLICT",
            str(exc),
            {"changed": False},
        )

    @app.exception_handler(LoadError)
    async def _load_error(_: Request, exc: LoadError) -> JSONResponse:
        logger.warning("ingestion load failed: %s", exc)
        return _json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            getattr(exc, "code", "LOAD_ERROR"),
            str(exc),
        )

    @app.exception_handler(PdfIngestionError)
    async def _pipeline_error(_: Request, exc: PdfIngestionError) -> JSONResponse:
        code = getattr(exc, "code", "INTERNAL_ERROR")
        status_code = _PIPELINE_STATUS.get(
            code, status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        if status_code >= 500:
            # A 5xx from the pipeline is our bug, not the caller's. Log the
            # detail and hand back the generic sentence.
            logger.exception("pipeline failure: %s", exc)
            return _json(
                status_code, "INTERNAL_ERROR", "An internal error occurred."
            )
        return _json(status_code, code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body is a bare list; reshaped into the envelope
        # so a client parses one error format, not two.
        return _json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            "Request validation failed.",
            {"errors": _safe_validation_errors(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            413: "FILE_TOO_LARGE",
            415: "UNSUPPORTED_FILE_TYPE",
        }.get(exc.status_code, "HTTP_ERROR")
        return _json(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # The catch-all. Deliberately says nothing useful to the client: an
        # unhandled exception's message can contain a DSN, a file path or a
        # fragment of SQL.
        logger.exception("unhandled exception: %s", exc)
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An internal error occurred.",
        )


def _safe_validation_errors(exc: RequestValidationError) -> list:
    """Field names and reasons only -- never the submitted value.

    Pydantic includes the offending input in ``ctx``/``input``. For a file
    upload that can be a chunk of the document, and echoing a rejected value
    back is how a validation message becomes a data leak.
    """
    safe = []
    for error in exc.errors():
        safe.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", "invalid"),
                "type": error.get("type", "value_error"),
            }
        )
    return safe
