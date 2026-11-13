"""The Library & Search scaffold is a scaffold, and stays one until built.

These tests exist for the same reason the ``PENDING`` evaluation tests exist:
an unimplemented thing must not be able to start looking implemented quietly.
They assert three properties:

1. the package imports with no side effects and no database;
2. every entry point still raises ``NotImplementedError`` with a message that
   points at the README, so a caller is told where the plan is;
3. the scaffold is not wired into the FastAPI application, so the verified
   six-endpoint surface is unchanged.

When Person 5 implements ``search_segments``, test 2 for that function is
*meant* to fail. Replace it with real tests -- do not delete it silently.
"""

from __future__ import annotations

import pytest

from backend.app import library_search
from backend.app.library_search import repository, schemas, service


def test_package_imports_and_exports_nothing_yet() -> None:
    assert library_search.__all__ == []
    assert library_search.CONTRACT_VERSION.endswith("scaffold")


def test_schemas_defines_no_models_yet() -> None:
    """A second definition of a clause payload is the failure mode here."""
    assert schemas.__all__ == []


def test_declared_strategies_include_the_keyword_baseline() -> None:
    assert service.DEFAULT_STRATEGY == "keyword"
    assert service.DEFAULT_STRATEGY in service.STRATEGIES
    # The comparison conditions are named now so the experiment has a shape,
    # even though neither is implementable until D-7 is answered.
    assert set(service.STRATEGIES) == {"keyword", "semantic", "hybrid"}


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: service.search("deposit"), id="service.search"),
        pytest.param(lambda: service.suggest("dep"), id="service.suggest"),
        pytest.param(
            lambda: repository.search_documents("deposit"),
            id="repository.search_documents",
        ),
        pytest.param(
            lambda: repository.search_segments("deposit"),
            id="repository.search_segments",
        ),
        pytest.param(lambda: repository.facet_counts(), id="repository.facet_counts"),
    ],
)
def test_every_entry_point_is_still_a_stub(call) -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        call()
    assert "scaffold" in str(excinfo.value)
    assert "README" in str(excinfo.value)


def test_scaffold_is_not_wired_into_the_api() -> None:
    """The verified endpoint surface must not grow because of a scaffold."""
    from backend.app.api.main import create_app

    paths = set(create_app().openapi()["paths"])
    assert paths == {
        "/api/health",
        "/api/documents/ingest",
        "/api/documents",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/pages",
        "/api/documents/{document_id}/clauses",
    }
    assert not any(path.startswith("/api/search") for path in paths)


def test_scaffold_contains_no_write_statements() -> None:
    """Read-only is a rule (CLAUDE.md section 9), so it gets a test."""
    from pathlib import Path

    package = Path(library_search.__file__).parent
    for module in sorted(package.glob("*.py")):
        source = module.read_text(encoding="utf-8").upper()
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP "):
            assert statement not in source, f"{module.name} mentions {statement}"
