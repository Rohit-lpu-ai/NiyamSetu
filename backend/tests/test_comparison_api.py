"""The four compare endpoints, tested with a fake service and no database.

Same contract as ``test_api.py`` and ``test_library_search_api.py``, and the
same warning: **nothing here proves PostgreSQL integration.** The service is
replaced through ``app.dependency_overrides``, so what is asserted is the
API's own behaviour -- routing, request validation, status codes, the §12.1
error envelope, and that a payload survives Pydantic serialisation with its
provenance and its evidence status intact.

Whether the real SQL and the real matcher produce those payloads is answered
by ``test_comparison_integration.py``, which needs a server and skips without
one.

The fake records the keyword arguments it was called with, because much of
what these endpoints do is *forwarding*: a threshold accepted by the route and
then dropped before the matcher runs is a defect no response-shape assertion
would catch.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("fastapi", reason="FastAPI is a Phase 4 dependency")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api.main import create_app  # noqa: E402
from backend.app.comparison.api import get_comparison_service  # noqa: E402
from backend.app.comparison.service import (  # noqa: E402
    ComparisonNotFound,
    InvalidComparison,
)

# --------------------------------------------------------------------------- #
# Fixture payloads, shaped exactly like the service's real output
# --------------------------------------------------------------------------- #

EVIDENCE: Dict[str, Any] = {
    "document_id": "DOC-0006",
    "version_id": "VER-0010",
    "clause_id": "CLS-0003773",
    "page_start": 2,
    "page_end": 3,
    "offset_basis": "normalized_text",
    "spans": [
        {
            "page_number": 2,
            "char_start": 412,
            "char_end": 1104,
            "quote": "1. Credit Facilities to Minority Communities:",
            "resolved": True,
        },
        {
            "page_number": 3,
            "char_start": 0,
            "char_end": 118,
            "quote": "continued on the following page.",
            "resolved": True,
        },
    ],
    "status": "resolved",
    "resolved": True,
    "problems": [],
    "quote": "1. Credit Facilities to Minority Communities:",
}

OLD_SIDE: Dict[str, Any] = {
    "clause_id": "CLS-0003773",
    "ordinal": 4,
    "clause_number": "1",
    "clause_number_ambiguous": False,
    "heading": None,
    "segment_type": "clause",
    "level": 0,
    "marker_style": "numeric_dotted",
    "text": "1. Credit Facilities to Minority Communities: 40 percent applies.",
    "page_start": 2,
    "page_end": 3,
    "pages": [
        {
            "page_number": 2,
            "char_start": 412,
            "char_end": 1104,
            "line_index_start": 3,
            "line_index_end": 18,
        },
        {
            "page_number": 3,
            "char_start": 0,
            "char_end": 118,
            "line_index_start": 0,
            "line_index_end": 2,
        },
    ],
    "fingerprint": "SEG-0a1b2c3d4e5f6071",
    "evidence": EVIDENCE,
    "entities": None,
}

NEW_SIDE: Dict[str, Any] = copy.deepcopy(OLD_SIDE)
NEW_SIDE.update(
    clause_id="CLS-0003901",
    text="1. Credit Facilities to Minority Communities: 75 percent applies.",
    fingerprint="SEG-1111222233334444",
)
NEW_SIDE["evidence"] = dict(EVIDENCE, version_id="VER-0011", clause_id="CLS-0003901")

MODIFIED_CHANGE: Dict[str, Any] = {
    "change_id": "MOD-CLS-0003901",
    "change_type": "modified",
    "similarity": 0.886692,
    "runner_up_similarity": 0.41,
    "runner_up_clause_id": None,
    "match_basis": "clause_number",
    "confidence": "high",
    "ambiguous": False,
    "renumbered": False,
    "reordered": False,
    "notes": [],
    "old": OLD_SIDE,
    "new": NEW_SIDE,
    "explanation": "The percentage changed from 40 percent to 75 percent.",
    "word_diff": [
        {"op": "equal", "old": "1. Credit ", "new": "1. Credit "},
        {"op": "replace", "old": "40", "new": "75"},
    ],
    "diff_stats": {
        "words_added": 1,
        "words_removed": 1,
        "words_kept": 7,
        "old_word_count": 8,
        "new_word_count": 8,
        "word_count_delta": 0,
        "token_overlap": 0.8,
    },
    "entity_deltas": [
        {
            "label": "PERCENT",
            "friendly_label": "percentage",
            "old": ["40 percent"],
            "new": ["75 percent"],
            "added": ["75 percent"],
            "removed": ["40 percent"],
        }
    ],
}

ADDED_CHANGE: Dict[str, Any] = {
    "change_id": "ADD-CLS-0003999",
    "change_type": "added",
    "similarity": None,
    "runner_up_similarity": None,
    "runner_up_clause_id": None,
    "match_basis": None,
    "confidence": "medium",
    "ambiguous": False,
    "renumbered": False,
    "reordered": False,
    "notes": [],
    "old": None,
    "new": dict(NEW_SIDE, clause_id="CLS-0003999"),
    "explanation": "This clause appears in the newer version only.",
    "word_diff": None,
    "diff_stats": None,
    "entity_deltas": None,
}

BASE_VERSION: Dict[str, Any] = {
    "version_id": "VER-0010",
    "document_id": "DOC-0006",
    "document_title": "Master Circular - Credit Facilities to Minority Communities",
    "department": "Financial Inclusion & Development Dept., Central Office",
    "document_type": "circular",
    "document_reference_no": "RBI/2017-2018/6",
    "version_label": "2017-07-01",
    "publication_date": "2017-07-01",
    "page_count": 17,
    "extraction_status": "ok",
    "offset_basis": "normalized_text",
    "supersedes_version_id": None,
    "source_url": "https://www.rbi.org.in/example.pdf",
    "file_checksum": "7b7d0b4186aed2c7" + "0" * 48,
    "original_filename": "06MCE03072017.pdf",
}

COMPARE_VERSION: Dict[str, Any] = dict(
    BASE_VERSION,
    version_id="VER-0011",
    version_label="2018-07-02",
    publication_date="2018-07-02",
    supersedes_version_id="VER-0010",
    document_reference_no="RBI/2018-19/8",
    file_checksum="a7e81798d12adc57" + "0" * 48,
    original_filename="08MC04072018.pdf",
)

SUMMARY: Dict[str, Any] = {
    "total_changes": 277,
    "counts": {"added": 2, "removed": 0, "modified": 40, "unchanged": 235},
    "matched_pairs": 275,
    "confidence_counts": {"high": 272, "medium": 5, "low": 0},
    "match_basis_counts": {"exact_text": 235, "clause_number": 39, "similarity": 1},
    "ambiguous_matches": 0,
    "renumbered_matches": 0,
    "reordered_matches": 0,
    "similarity_mean": 0.98771,
    "similarity_mean_note": (
        "Mean TF-IDF cosine over matched clause pairs only. It measures how "
        "confidently clauses lined up, not how similar the two policies are, "
        "and it is not a legal-equivalence score."
    ),
    "unchanged_ratio": 0.848375,
}

COMPARE_PAYLOAD: Dict[str, Any] = {
    "comparison_id": "CMP-0123456789abcdef01234567",
    "base_version": BASE_VERSION,
    "compare_version": COMPARE_VERSION,
    "same_document": True,
    "segmentation_method": "clause_aware",
    "config": {
        "match_threshold": 0.45,
        "number_match_threshold": 0.2,
        "ambiguity_margin": 0.05,
        "high_confidence_similarity": 0.75,
        "fold_clause_numbers": True,
    },
    "summary": SUMMARY,
    "clause_counts": {"base": 275, "compare": 277},
    "total": 2,
    "total_unfiltered": 277,
    "limit": 100,
    "offset": 0,
    "filters": {
        "change_types": None,
        "confidence": None,
        "min_similarity": None,
        "max_similarity": None,
        "only_ambiguous": False,
    },
    "warnings": [],
    "took_ms": 287.189,
    "items": [MODIFIED_CHANGE, ADDED_CHANGE],
}

OPTIONS_PAYLOAD: Dict[str, Any] = {
    "documents": [
        {
            "document_id": "DOC-0006",
            "title": "Master Circular - Credit Facilities to Minority Communities",
            "department": "Financial Inclusion & Development Dept., Central Office",
            "document_type": "circular",
            "reference_no": None,
            "version_count": 3,
            "versions": [
                {
                    "version_id": "VER-0007",
                    "version_label": "2019-07-01",
                    "publication_date": "2019-07-01",
                    "page_count": 17,
                    "clause_count": 286,
                    "supersedes_version_id": "VER-0011",
                },
                {
                    "version_id": "VER-0011",
                    "version_label": "2018-07-02",
                    "publication_date": "2018-07-02",
                    "page_count": 17,
                    "clause_count": 277,
                    "supersedes_version_id": "VER-0010",
                },
            ],
            "pairs": [
                {
                    "base_version_id": "VER-0011",
                    "compare_version_id": "VER-0007",
                    "base_label": "2018-07-02",
                    "compare_label": "2019-07-01",
                    "base_publication_date": "2018-07-02",
                    "compare_publication_date": "2019-07-01",
                    "consecutive": True,
                    "supersedes_declared": True,
                }
            ],
        }
    ],
    "corpus": {
        "documents": 6,
        "versions": 13,
        "ok_versions": 13,
        "documents_with_pairs": 3,
    },
    "comparable_pairs": 12,
    "declared_supersedes_pairs": 6,
    "methods": ["clause_aware", "paragraph"],
    "default_method": "clause_aware",
    "defaults": {
        "match_threshold": 0.45,
        "number_match_threshold": 0.2,
        "ambiguity_margin": 0.05,
        "high_confidence_similarity": 0.75,
        "fold_clause_numbers": True,
    },
    "note": "A version is offered only when extraction_status = 'ok'.",
}

ENTITIES_PAYLOAD: Dict[str, Any] = {
    "version_id": "VER-0010",
    "document_id": "DOC-0006",
    "segmentation_method": "clause_aware",
    "condition": "regex",
    "total": 275,
    "limit": 100,
    "offset": 0,
    "mentions_by_label": {"DATE": 12, "PERIOD": 5, "OBLIGATION": 48},
    "took_ms": 4.2,
    "note": "Rule-based baseline.",
    "items": [
        {
            "clause_id": "CLS-0003773",
            "ordinal": 4,
            "clause_number": "1",
            "page_start": 2,
            "page_end": 3,
            "entities": [
                {"label": "PERCENT", "text": "40 percent", "start": 46, "end": 56}
            ],
        }
    ],
}


# --------------------------------------------------------------------------- #
# The fake service
# --------------------------------------------------------------------------- #


class FakeService:
    """Records its calls and returns fixture payloads, or raises on demand."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.raise_invalid: Optional[str] = None
        self.raise_missing: Optional[str] = None
        self.compare_payload: Dict[str, Any] = copy.deepcopy(COMPARE_PAYLOAD)
        self.options_payload: Dict[str, Any] = copy.deepcopy(OPTIONS_PAYLOAD)
        self.entities_payload: Dict[str, Any] = copy.deepcopy(ENTITIES_PAYLOAD)

    def _maybe_raise(self) -> None:
        if self.raise_invalid:
            raise InvalidComparison(self.raise_invalid)
        if self.raise_missing:
            raise ComparisonNotFound(self.raise_missing)

    def options(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"method": "options", **kwargs})
        self._maybe_raise()
        return copy.deepcopy(self.options_payload)

    def compare(self, base: Any, target: Any, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(
            {"method": "compare", "base": base, "target": target, **kwargs}
        )
        self._maybe_raise()
        return copy.deepcopy(self.compare_payload)

    def entities(self, version_id: Any, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(
            {"method": "entities", "version_id": version_id, **kwargs}
        )
        self._maybe_raise()
        return copy.deepcopy(self.entities_payload)


@pytest.fixture
def fake() -> FakeService:
    return FakeService()


@pytest.fixture
def client(fake: FakeService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_comparison_service] = lambda: fake
    return TestClient(app)


def _assert_envelope(response: Any, code: str) -> Dict[str, Any]:
    """Every non-2xx body must be the single §12.1 envelope."""
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body


# --------------------------------------------------------------------------- #
# GET /api/compare/options
# --------------------------------------------------------------------------- #


def test_options_returns_the_pairs_and_the_corpus_shape(client: TestClient):
    response = client.get("/api/compare/options")
    assert response.status_code == 200
    body = response.json()
    assert body["comparable_pairs"] == 12
    assert body["declared_supersedes_pairs"] == 6
    assert body["corpus"]["documents_with_pairs"] == 3


def test_options_reports_whether_a_pair_order_is_declared(client: TestClient):
    pair = client.get("/api/compare/options").json()["documents"][0]["pairs"][0]
    assert pair["supersedes_declared"] is True
    assert pair["consecutive"] is True


def test_options_forwards_include_single_version(client: TestClient, fake: FakeService):
    client.get("/api/compare/options", params={"include_single_version": "true"})
    assert fake.calls[-1]["include_single_version"] is True


def test_options_defaults_to_hiding_single_version_documents(
    client: TestClient, fake: FakeService
):
    client.get("/api/compare/options")
    assert fake.calls[-1]["include_single_version"] is False


def test_an_empty_corpus_still_explains_itself(client: TestClient, fake: FakeService):
    """An empty list must come with the counts that say why it is empty."""
    fake.options_payload["documents"] = []
    fake.options_payload["comparable_pairs"] = 0
    fake.options_payload["declared_supersedes_pairs"] = 0
    fake.options_payload["corpus"] = {
        "documents": 17,
        "versions": 17,
        "ok_versions": 17,
        "documents_with_pairs": 0,
    }
    body = client.get("/api/compare/options").json()
    assert body["documents"] == []
    assert body["corpus"]["documents"] == 17
    assert body["corpus"]["documents_with_pairs"] == 0


# --------------------------------------------------------------------------- #
# POST /api/compare -- happy path
# --------------------------------------------------------------------------- #


def _post(client: TestClient, **body: Any) -> Any:
    payload = {"base_version_id": "VER-0010", "compare_version_id": "VER-0011"}
    payload.update(body)
    return client.post("/api/compare", json=payload)


def test_a_comparison_returns_its_summary_and_items(client: TestClient):
    response = _post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["counts"]["modified"] == 40
    assert len(body["items"]) == 2


def test_the_comparison_id_is_returned(client: TestClient):
    assert _post(client).json()["comparison_id"].startswith("CMP-")


def test_both_versions_are_described_in_the_response(client: TestClient):
    body = _post(client).json()
    assert body["base_version"]["version_id"] == "VER-0010"
    assert body["compare_version"]["version_id"] == "VER-0011"
    assert body["compare_version"]["supersedes_version_id"] == "VER-0010"


def test_the_thresholds_used_are_reported(client: TestClient):
    """A result whose thresholds are not reported cannot be reproduced."""
    config = _post(client).json()["config"]
    assert config["match_threshold"] == 0.45
    assert config["ambiguity_margin"] == 0.05


def test_multi_page_provenance_survives_serialisation(client: TestClient):
    """A clause spanning two pages must arrive with both, never collapsed."""
    side = _post(client).json()["items"][0]["old"]
    assert side["page_start"] == 2 and side["page_end"] == 3
    assert [page["page_number"] for page in side["pages"]] == [2, 3]
    assert [span["page_number"] for span in side["evidence"]["spans"]] == [2, 3]


def test_every_side_carries_an_evidence_status(client: TestClient):
    for item in _post(client).json()["items"]:
        for side in ("old", "new"):
            if item[side] is not None:
                assert item[side]["evidence"]["status"] in (
                    "resolved",
                    "partial",
                    "unresolved",
                )


def test_the_offset_basis_is_named_on_every_evidence_object(client: TestClient):
    for item in _post(client).json()["items"]:
        for side in ("old", "new"):
            if item[side] is not None:
                assert item[side]["evidence"]["offset_basis"] == "normalized_text"


def test_an_added_change_carries_no_similarity_and_no_old_side(client: TestClient):
    added = [i for i in _post(client).json()["items"] if i["change_type"] == "added"]
    assert added
    assert added[0]["old"] is None
    assert added[0]["similarity"] is None
    assert added[0]["match_basis"] is None


def test_a_withheld_field_is_null_over_http_not_absent(client: TestClient, fake: FakeService):
    """The contract the response model fixes, pinned because it is observable.

    The service omits the key from its dict when ``include_diff=false``, but
    the Pydantic response model declares the field, so the JSON carries
    ``"word_diff": null``. That is the documented behaviour -- one thing for a
    consumer to test rather than two -- and it was found by exercising the
    real endpoint over HTTP rather than the service directly.
    """
    for item in fake.compare_payload["items"]:
        item.pop("word_diff", None)
        item.pop("diff_stats", None)
    body = _post(client, include_diff=False).json()
    for item in body["items"]:
        assert "word_diff" in item
        assert item["word_diff"] is None
        assert item["diff_stats"] is None


def test_the_word_diff_and_entity_deltas_survive(client: TestClient):
    modified = [
        i for i in _post(client).json()["items"] if i["change_type"] == "modified"
    ][0]
    assert modified["word_diff"][1]["op"] == "replace"
    assert modified["entity_deltas"][0]["label"] == "PERCENT"
    assert modified["diff_stats"]["words_added"] == 1


def test_the_similarity_mean_caveat_travels_with_the_number(client: TestClient):
    note = _post(client).json()["summary"]["similarity_mean_note"]
    assert "not a legal-equivalence score" in note


# --------------------------------------------------------------------------- #
# POST /api/compare -- the parameters are forwarded, not dropped
# --------------------------------------------------------------------------- #


def test_the_method_is_forwarded(client: TestClient, fake: FakeService):
    _post(client, method="paragraph")
    assert fake.calls[-1]["method"] == "paragraph"


def test_the_threshold_overrides_reach_the_matcher(client: TestClient, fake: FakeService):
    _post(client, match_threshold=0.8, ambiguity_margin=0.01)
    config = fake.calls[-1]["config"]
    assert config is not None
    assert config.match_threshold == 0.8
    assert config.ambiguity_margin == 0.01


def test_no_override_means_no_config_object(client: TestClient, fake: FakeService):
    """The service's own defaults must be used, not a copy of them here."""
    _post(client)
    assert fake.calls[-1]["config"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("change_types", ["added", "removed"]),
        ("confidence", ["low"]),
        ("min_similarity", 0.5),
        ("max_similarity", 0.9),
        ("only_ambiguous", True),
        ("include_text", False),
        ("include_diff", False),
        ("include_entities", True),
        ("include_explanation", False),
        ("allow_cross_document", True),
        ("document_id", "DOC-0006"),
        ("limit", 10),
        ("offset", 20),
    ],
)
def test_every_filter_is_forwarded(
    client: TestClient, fake: FakeService, field: str, value: Any
):
    _post(client, **{field: value})
    assert fake.calls[-1][field] == value


# --------------------------------------------------------------------------- #
# POST /api/compare -- validation and errors
# --------------------------------------------------------------------------- #


def test_a_missing_version_id_is_a_422_envelope(client: TestClient):
    response = client.post("/api/compare", json={"base_version_id": "VER-0010"})
    assert response.status_code == 422
    _assert_envelope(response, "VALIDATION_ERROR")


def test_an_unknown_field_is_refused(client: TestClient):
    """``extra="forbid"``: a typo'd parameter must not be silently ignored."""
    response = _post(client, treshold=0.5)
    assert response.status_code == 422


def test_an_unknown_method_is_a_422(client: TestClient):
    assert _post(client, method="sentence").status_code == 422


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_an_out_of_range_threshold_is_a_422(client: TestClient, value: float):
    assert _post(client, match_threshold=value).status_code == 422


def test_an_unknown_change_type_is_a_422(client: TestClient):
    assert _post(client, change_types=["rewritten"]).status_code == 422


def test_an_unknown_confidence_level_is_a_422(client: TestClient):
    assert _post(client, confidence=["certain"]).status_code == 422


def test_an_over_large_limit_is_a_422(client: TestClient):
    assert _post(client, limit=10_000).status_code == 422


def test_a_negative_offset_is_a_422(client: TestClient):
    assert _post(client, offset=-1).status_code == 422


def test_a_service_validation_error_becomes_a_422_envelope(
    client: TestClient, fake: FakeService
):
    fake.raise_invalid = "base_version_id and compare_version_id are the same"
    response = _post(client)
    assert response.status_code == 422
    body = _assert_envelope(response, "VALIDATION_ERROR")
    assert "same" in body["error"]["message"]


def test_an_unknown_version_becomes_a_404_envelope(
    client: TestClient, fake: FakeService
):
    fake.raise_missing = "no version with id VER-9999"
    response = _post(client)
    assert response.status_code == 404
    _assert_envelope(response, "VERSION_NOT_FOUND")


def test_a_cross_document_warning_reaches_the_client(
    client: TestClient, fake: FakeService
):
    fake.compare_payload["same_document"] = False
    fake.compare_payload["warnings"] = [
        "CROSS_DOCUMENT: these two versions are not declared versions of the "
        "same document"
    ]
    body = _post(client, allow_cross_document=True).json()
    assert body["same_document"] is False
    assert any(w.startswith("CROSS_DOCUMENT") for w in body["warnings"])


def test_a_direction_swap_warning_reaches_the_client(
    client: TestClient, fake: FakeService
):
    fake.compare_payload["warnings"] = ["DIRECTION_SWAPPED: the sides were swapped"]
    body = _post(client).json()
    assert any(w.startswith("DIRECTION_SWAPPED") for w in body["warnings"])


def test_an_unresolved_evidence_status_is_not_hidden(
    client: TestClient, fake: FakeService
):
    """The brief's requirement: expose the problem, never a misleading quote."""
    item = fake.compare_payload["items"][0]
    item["old"]["evidence"] = dict(
        item["old"]["evidence"],
        status="unresolved",
        resolved=False,
        quote=None,
        problems=["the clause has no segment_pages rows"],
    )
    evidence = _post(client).json()["items"][0]["old"]["evidence"]
    assert evidence["status"] == "unresolved"
    assert evidence["quote"] is None
    assert evidence["problems"]


# --------------------------------------------------------------------------- #
# GET /api/compare/entities
# --------------------------------------------------------------------------- #


def test_entities_returns_mentions_with_spans(client: TestClient):
    response = client.get("/api/compare/entities", params={"version_id": "VER-0010"})
    assert response.status_code == 200
    entity = response.json()["items"][0]["entities"][0]
    assert entity["label"] == "PERCENT"
    assert entity["start"] < entity["end"]


def test_entities_reports_its_condition_and_its_limits(client: TestClient):
    body = client.get("/api/compare/entities", params={"version_id": "VER-0010"}).json()
    assert body["condition"] == "regex"
    assert body["note"]


def test_entities_requires_a_version_id(client: TestClient):
    assert client.get("/api/compare/entities").status_code == 422


def test_entities_forwards_its_filters(client: TestClient, fake: FakeService):
    client.get(
        "/api/compare/entities",
        params={
            "version_id": "VER-0010",
            "method": "paragraph",
            "clause_id": "CLS-0003773",
            "labels": ["DATE", "PERIOD"],
            "limit": 5,
            "offset": 2,
        },
    )
    call = fake.calls[-1]
    assert call["method"] == "paragraph"
    assert call["clause_id"] == "CLS-0003773"
    assert call["labels"] == ["DATE", "PERIOD"]
    assert call["limit"] == 5 and call["offset"] == 2


def test_an_unknown_entity_label_is_a_422_envelope(
    client: TestClient, fake: FakeService
):
    fake.raise_invalid = "unknown entity label(s): ['DEADLINE']"
    response = client.get(
        "/api/compare/entities",
        params={"version_id": "VER-0010", "labels": ["DEADLINE"]},
    )
    assert response.status_code == 422
    _assert_envelope(response, "VALIDATION_ERROR")


def test_an_unknown_version_for_entities_is_a_404(client: TestClient, fake: FakeService):
    fake.raise_missing = "no version with id VER-9999"
    response = client.get("/api/compare/entities", params={"version_id": "VER-9999"})
    assert response.status_code == 404
    _assert_envelope(response, "VERSION_NOT_FOUND")


# --------------------------------------------------------------------------- #
# GET /api/compare/entities/labels
# --------------------------------------------------------------------------- #


def test_the_label_vocabulary_is_published(client: TestClient):
    body = client.get("/api/compare/entities/labels").json()
    assert set(body["labels"]) >= {
        "DATE",
        "PERIOD",
        "MONEY",
        "PERCENT",
        "OBLIGATION",
        "AUTHORITY",
    }
    assert all(description.strip() for description in body["labels"].values())


def test_the_limitations_are_published_through_the_api(client: TestClient):
    """A consumer must be able to read the limits from the running system."""
    limitations = client.get("/api/compare/entities/labels").json()["limitations"]
    joined = " ".join(limitations).lower()
    assert "modal language" in joined
    assert "crf" in joined or "transformer" in joined


def test_the_labels_endpoint_needs_no_service(client: TestClient, fake: FakeService):
    """It is static data, so it must not touch the database."""
    client.get("/api/compare/entities/labels")
    assert not fake.calls


# --------------------------------------------------------------------------- #
# Regression: the existing API must be unchanged
# --------------------------------------------------------------------------- #


def test_the_four_compare_paths_are_mounted_under_api():
    paths = set(create_app().openapi()["paths"])
    assert {
        "/api/compare",
        "/api/compare/options",
        "/api/compare/entities",
        "/api/compare/entities/labels",
    } <= paths


def test_the_ten_pre_existing_endpoints_are_still_present():
    """Compare is additive. Removing a path would break four modules."""
    paths = set(create_app().openapi()["paths"])
    assert {
        "/api/health",
        "/api/documents",
        "/api/documents/ingest",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/pages",
        "/api/documents/{document_id}/clauses",
        "/api/search",
        "/api/search/clauses",
        "/api/search/suggest",
        "/api/library/facets",
    } <= paths


def test_compare_adds_no_version_listing_endpoint():
    """Versions are served by ``GET /api/documents/{id}``, not a copy of it."""
    from backend.app.comparison import api as compare_api

    routes = {route.path for route in compare_api.compare_router.routes}
    assert not any("documents" in path for path in routes), routes


def test_there_is_no_persisted_comparison_endpoint():
    """A comparison is computed on demand; no table, so no GET by id.

    If persistence is ever added this test should be deleted deliberately,
    with the justification the brief requires -- not because it started
    failing.
    """
    paths = set(create_app().openapi()["paths"])
    assert "/api/compare/{comparison_id}" not in paths


def test_compare_endpoints_document_the_error_envelope_in_openapi():
    spec = create_app().openapi()
    assert "422" in spec["paths"]["/api/compare"]["post"]["responses"]
    assert "404" in spec["paths"]["/api/compare"]["post"]["responses"]


def test_an_unknown_compare_subpath_is_a_404_envelope(client: TestClient):
    response = client.get("/api/compare/nonexistent")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_service_is_injected_and_overridable():
    """Without this seam the endpoints could not be tested without a server."""
    from backend.app.comparison.api import get_comparison_service
    from backend.app.comparison.service import ComparisonService

    assert isinstance(get_comparison_service(), ComparisonService)


# --------------------------------------------------------------------------- #
# Regression: a document-level field must not look version-level
# --------------------------------------------------------------------------- #


def test_the_version_summary_names_the_reference_as_the_documents(client: TestClient):
    """Found in a browser: the 2019 issue displayed the 2026 issue's number.

    ``reference_no`` is a column on ``documents``, so every issue of one
    circular shares a single value. Carrying it as ``reference_no`` inside a
    per-version payload invited exactly the misreading that happened, so the
    key says ``document_`` and this test keeps it that way.
    """
    body = _post(client).json()
    for side in ("base_version", "compare_version"):
        assert "document_reference_no" in body[side]
        assert "reference_no" not in body[side], (
            "a bare reference_no on a version payload reads as that version's "
            "own number, which the schema cannot provide"
        )


def test_the_openapi_schema_explains_the_reference_is_document_level():
    """A consumer must learn this from the contract, not by being surprised."""
    spec = create_app().openapi()
    field = spec["components"]["schemas"]["VersionSummary"]["properties"][
        "document_reference_no"
    ]
    assert "document" in field["description"].lower()
    assert "version" in field["description"].lower()
