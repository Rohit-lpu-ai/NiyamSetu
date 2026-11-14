"""The Library & Search module's **boundaries**, as tests.

This file used to pin the module as a scaffold: every entry point raising
``NotImplementedError``, nothing wired into the API, no models defined. Those
assertions were correct until the module was implemented, and the file said at
the time:

    When Person 5 implements ``search_segments``, test 2 for that function is
    *meant* to fail. Replace it with real tests -- do not delete it silently.

This is that replacement. The scaffold assertions are gone because they are
now false; what survives is every assertion that was never about the scaffold
and is a permanent property of the module:

* it is **read-only** -- no write statement anywhere in the package;
* it does not define a **second** document, page or clause payload;
* it does not reach around the API's shared machinery to build its own
  engine, its own error envelope or its own configuration;
* the API surface is **exactly** what is documented, so an accidentally
  exposed route fails a test;
* the strategies it does not implement are not advertised as working.

``test_library_search_unit.py`` covers the logic, ``_api.py`` the HTTP layer
and ``_integration.py`` the SQL. This module covers the architecture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is a Phase 3 dependency")

from backend.app import library_search  # noqa: E402
from backend.app.library_search import api, repository, schemas, service  # noqa: E402

PACKAGE_DIR = Path(library_search.__file__).resolve().parent


def _sources() -> dict:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE_DIR.glob("*.py"))
    }


def _string_literals(source: str) -> list:
    """Every string literal in a module **except** docstrings.

    Needed because this module's own prose quotes SQL: ``repository.py``
    documents the migration's ``CREATE INDEX segments_text_search_idx`` in its
    docstring, and the first version of the write-statement guard flagged that
    as a write. Reading documentation as code is how a guard becomes noise.

    f-strings are included via their constant parts, which is where the SQL
    in this package lives (the interpolated pieces are module constants that
    are themselves scanned).
    """
    import ast

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    literals = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            literals.append(node.value)
    return literals


# --------------------------------------------------------------------------- #
# Read-only
# --------------------------------------------------------------------------- #


#: SQL write statements, as word-boundary patterns rather than substrings.
#:
#: The first version of this test searched for the bare strings and failed on
#: its own module: ``truncated`` (a variable) contains "TRUNCATE", and
#: ``offset`` contains "SET ". A guard that cries wolf gets deleted, so it
#: matches statement *shapes* instead.
WRITE_STATEMENTS = {
    "INSERT": r"\bINSERT\s+INTO\b",
    "UPDATE": r"\bUPDATE\s+\w+\s+SET\b",
    "DELETE": r"\bDELETE\s+FROM\b",
    "DROP": r"\bDROP\s+(?:TABLE|INDEX|VIEW|DATABASE|SCHEMA)\b",
    "TRUNCATE": r"\bTRUNCATE\s+(?:TABLE\s+)?\w",
    "ALTER": r"\bALTER\s+(?:TABLE|SEQUENCE|DATABASE)\b",
    "CREATE": r"\bCREATE\s+(?:TABLE|INDEX|VIEW|DATABASE)\b",
    "GRANT": r"\bGRANT\b",
}


@pytest.mark.parametrize("label", sorted(WRITE_STATEMENTS))
def test_no_write_statement_appears_in_the_package(label):
    """``CLAUDE.md`` §9: nothing downstream of extraction may write documents.

    A rule that is only written down is one that gets broken during a late
    evening of debugging, so it is also a test.
    """
    import re

    pattern = re.compile(WRITE_STATEMENTS[label], re.IGNORECASE)
    for name, source in _sources().items():
        for literal in _string_literals(source):
            match = pattern.search(literal)
            assert match is None, (
                f"{name} contains a {label} statement in executable SQL: "
                f"{match.group(0)!r}"
            )


def test_the_only_sql_set_statement_is_a_planner_setting():
    """One documented exception, and it is scoped to a single transaction.

    ``SET LOCAL enable_seqscan`` is how ``explain_clause_search`` answers "is
    the index applicable?". It changes a planner setting, not data, and
    ``SET LOCAL`` cannot outlive its transaction or leak to another
    connection. Any *other* SET would be a session-wide change made from a
    pooled connection, which is a different and much worse thing.
    """
    import re

    #: A SET issued as SQL, i.e. inside a quoted statement string.
    set_statements = re.findall(r'["\']\s*SET\s+[^"\']*', "".join(_sources().values()))
    assert set_statements, "the explain helper's SET LOCAL has gone missing"
    for statement in set_statements:
        assert "SET LOCAL enable_seqscan" in statement, statement


def test_the_package_uses_the_existing_engine():
    """One pool, one place the password is read. Not a second engine here."""
    source = _sources()["repository.py"]
    assert "from backend.app.db.engine import connection" in source
    assert "create_engine" not in source


# --------------------------------------------------------------------------- #
# One definition of a payload
# --------------------------------------------------------------------------- #


def test_schemas_does_not_redefine_the_existing_payloads():
    """A second ClauseOut is two things to keep in step, and they never are.

    The module adds ranking metadata and facets; documents, pages and clauses
    keep their single definition in ``api/schemas.py``.
    """
    names = set(schemas.__all__)
    forbidden = {
        "ClauseModel",
        "ClauseListResponse",
        "PageModel",
        "PageListResponse",
        "DocumentSummary",
        "DocumentListResponse",
        "DocumentDetail",
        "VersionDetail",
        "IngestResponse",
    }
    assert not names & forbidden, names & forbidden


def test_the_api_module_reuses_the_shared_error_envelope():
    source = _sources()["api.py"]
    assert "from backend.app.api.errors import ApiError" in source
    assert "JSONResponse" not in source, "the envelope is built in api/errors.py"


def test_the_module_declares_an_implemented_contract_version():
    """The scaffold's version was '0.0.0-scaffold'. It is implemented now."""
    assert library_search.CONTRACT_VERSION == "1.0.0"
    assert "scaffold" not in library_search.CONTRACT_VERSION


def test_the_package_exports_its_public_surface():
    exported = set(library_search.__all__)
    assert {"SearchService", "InvalidQuery", "STRATEGIES", "DEFAULT_STRATEGY"} <= exported
    for name in exported:
        assert hasattr(library_search, name), name


# --------------------------------------------------------------------------- #
# Unimplemented things are not advertised
# --------------------------------------------------------------------------- #


def test_planned_strategies_are_not_offered_as_available():
    """semantic and hybrid need D-7. The seam exists; the claim does not."""
    assert service.DEFAULT_STRATEGY == "fts"
    assert set(service.STRATEGIES) == {"fts", "ilike"}
    assert set(service.PLANNED_STRATEGIES).isdisjoint(service.STRATEGIES)


def test_the_openapi_description_does_not_claim_semantic_search():
    """The documented surface must not promise what D-7 still gates."""
    from backend.app.api.main import create_app

    spec = create_app().openapi()
    strategy = spec["paths"]["/api/search"]["get"]["parameters"]
    descriptions = " ".join(
        str(parameter.get("description", "")) for parameter in strategy
    )
    assert "not implemented" in descriptions
    assert "D-7" in descriptions


# --------------------------------------------------------------------------- #
# The API surface is exactly what is documented
# --------------------------------------------------------------------------- #


def test_the_api_exposes_exactly_the_documented_paths():
    """A count plus an exact set, so a stray route is a failing test."""
    from backend.app.api.main import create_app

    assert set(create_app().openapi()["paths"]) == {
        # Phase 4, unchanged by this module.
        "/api/health",
        "/api/documents",
        "/api/documents/ingest",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/pages",
        "/api/documents/{document_id}/clauses",
        # Library & Search.
        "/api/search",
        "/api/search/clauses",
        "/api/search/suggest",
        "/api/library/facets",
        # Compare. Added by backend/app/comparison/, which mounts its own
        # router at the same composition point. The set stays exact, so a
        # stray route is still a failing test.
        "/api/compare",
        "/api/compare/options",
        "/api/compare/entities",
        "/api/compare/entities/labels",
    }


def test_the_module_adds_no_document_listing_endpoint():
    """The Library browses through ``GET /api/documents``, not a copy of it."""
    routes = {route.path for router in (api.search_router, api.library_router)
              for route in router.routes}
    assert not any("documents" in path for path in routes), routes


def test_the_search_routers_are_mounted_under_the_api_prefix():
    from backend.app.api.main import API_PREFIX, create_app

    paths = set(create_app().openapi()["paths"])
    for path in paths:
        assert path.startswith(API_PREFIX)


# --------------------------------------------------------------------------- #
# Dependency injection, so the endpoints stay testable without a server
# --------------------------------------------------------------------------- #


def test_the_service_is_injected_and_overridable():
    """39 existing API tests run with no database because of this pattern."""
    from backend.app.api.main import create_app

    app = create_app()
    sentinel = object()
    app.dependency_overrides[api.get_search_service] = lambda: sentinel
    assert app.dependency_overrides[api.get_search_service]() is sentinel
    app.dependency_overrides.clear()


def test_the_default_service_binds_to_the_configured_database():
    assert api.get_search_service().url is None, (
        "a None url means 'the configured database', which is what a request "
        "should use; a hardcoded url here would bypass .env"
    )


def test_every_repository_entry_point_accepts_a_url_override():
    """How a test points the module at a throwaway database."""
    import inspect

    for name in (
        "search_clauses",
        "search_documents",
        "search_clauses_ilike",
        "facet_counts",
        "suggest",
        "parse_query",
        "explain_clause_search",
        "clause_search_rows",
    ):
        signature = inspect.signature(getattr(repository, name))
        assert "url" in signature.parameters, name
