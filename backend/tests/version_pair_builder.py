"""Synthetic version pairs for Compare, built to exercise named cases.

**These are fixtures, not government documents.** ``CLAUDE.md`` §16 forbids
synthetic text in the corpus, and nothing here goes near it: the PDFs are
generated into ``tmp_path`` at test time, loaded into the throwaway test
database, and never written to ``data/``. Every clause below is invented and
reads as invented. The report says so, and
``test_comparison_integration.py`` keeps the real-corpus results and the
synthetic results in separate sections for the same reason.

Why synthetic fixtures *as well as* the real corpus
---------------------------------------------------
The nine real RBI master-circular issues in ``data/version_pairs/`` are what
prove the feature works on actual policy documents. What they cannot do is
prove a *specific* behaviour, because nobody has annotated them clause by
clause: when the matcher reports 54 removals in the 2016 -> 2017 SC/ST pair,
there is no ground truth saying whether 54 is right.

These fixtures have ground truth by construction. Each one isolates one case
the brief names -- an added clause, a removed clause, a renumbered clause, an
ambiguous alignment -- so a test can assert the exact classification and fail
if the behaviour regresses. Real documents show that it works; fixtures show
*what* it does.

They run through the **real pipeline**, not around it: ``build_pdf_bytes`` ->
the extraction CLI's library -> header/footer cleaning -> clause-aware
segmentation -> the loader -> PostgreSQL. So a fixture exercises the same page
provenance, the same offsets and the same ``segment_pages`` rows a real
document does, and a defect anywhere in that chain fails these tests too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from backend.tests.pdf_builder import build_pdf_bytes

__all__ = [
    "VersionSpec",
    "PairSpec",
    "PAIRS",
    "write_pair",
    "build_and_process",
]


@dataclass(frozen=True)
class VersionSpec:
    """One side of a synthetic pair: the pages of a PDF, plus its metadata."""

    version_label: str
    publication_date: str
    pages: Sequence[Sequence[str]]
    supersedes: Optional[str] = None


@dataclass(frozen=True)
class PairSpec:
    """A named case, its two versions, and what the matcher must report.

    ``expect`` is the assertion the case exists for, written next to the text
    it is about. A fixture whose expectation lived in the test file would
    drift from its data the first time either was edited.
    """

    name: str
    document_group: str
    title: str
    base: VersionSpec
    compare: VersionSpec
    expect: Dict[str, object] = field(default_factory=dict)
    note: str = ""


#: A shared opening, **byte-identical on both sides of every pair**, so that
#: each fixture varies in exactly one way and a test can attribute what it
#: sees. An earlier version of this file put the issue label in the preamble;
#: every pair then reported one extra modified clause, and the
#: ``renumbered_only`` fixture could no longer assert "nothing was modified"
#: -- the fixture was testing the fixture.
#:
#: The two PDFs of a pair still have different checksums even when their page
#: text is identical, because :func:`write_pair` varies the PDF ``/Info``
#: title. That matters for the ``identical_versions`` fixture: the loader
#: de-duplicates on ``file_checksum``, so two byte-identical PDFs would load
#: as one version and there would be nothing to compare.
_PREAMBLE: List[str] = [
    "FIXTURE CIRCULAR - NOT A GOVERNMENT DOCUMENT",
    "Synthetic test fixture for the comparison module",
    "To all participating fixture institutions",
    "",
]


def _preamble() -> List[str]:
    return list(_PREAMBLE)


PAIRS: List[PairSpec] = [
    PairSpec(
        name="added_removed_modified_unchanged",
        document_group="fixture-core-cases",
        title="Fixture Circular - Core Change Types",
        note=(
            "The baseline case: one clause of each kind, so a single "
            "comparison covers all four classifications. The removed clause "
            "and the added clause deliberately share **no vocabulary and no "
            "clause number** -- an earlier draft gave both the number '3.' "
            "and near-identical wording, and the matcher correctly paired "
            "them as a modification, so the fixture tested nothing."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-01-01",
            pages=[
                _preamble()
                + [
                    "1. Scope and application of this fixture circular.",
                    "This clause is identical in both issues and must be",
                    "reported as unchanged.",
                    "",
                    "2. Participating institutions shall submit the fixture",
                    "return within 30 days of the end of each quarter.",
                    "",
                    "3. Maintenance of the fixture register at every branch",
                    "office, and the manner of its periodic inspection.",
                    "",
                    "9. Closing provisions of the fixture circular.",
                ]
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-01-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "1. Scope and application of this fixture circular.",
                    "This clause is identical in both issues and must be",
                    "reported as unchanged.",
                    "",
                    "2. Participating institutions shall submit the fixture",
                    "return within 60 days of the end of each quarter.",
                    "",
                    "5. Tabular statement of district-wise population figures",
                    "compiled for the relevant reference year.",
                    "",
                    "9. Closing provisions of the fixture circular.",
                ]
            ],
        ),
        expect={
            "has_unchanged": True,
            "has_modified": True,
            "has_added": True,
            "has_removed": True,
            # '30 days' -> '60 days' must be found by the entity delta, not
            # merely by the similarity score.
            "modified_period_change": ("30 days", "60 days"),
        },
    ),
    PairSpec(
        name="renumbered_only",
        document_group="fixture-renumbering",
        title="Fixture Circular - Renumbering Only",
        note=(
            "The case the brief calls out explicitly: a clause must not be "
            "reported as modified merely because its number changed. Every "
            "clause here keeps its wording and shifts by one number, because "
            "a new clause 1 was inserted."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-02-01",
            pages=[
                _preamble()
                + [
                    "1. The first substantive fixture obligation applies to",
                    "every participating institution without exception.",
                    "",
                    "2. The second substantive fixture obligation concerns the",
                    "quarterly fixture statement and its annexures.",
                    "",
                    "3. The third substantive fixture obligation concerns the",
                    "designated fixture officer at each head office.",
                ]
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-02-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "1. A newly inserted definitions clause, which has no",
                    "counterpart in the earlier issue at all.",
                    "",
                    "2. The first substantive fixture obligation applies to",
                    "every participating institution without exception.",
                    "",
                    "3. The second substantive fixture obligation concerns the",
                    "quarterly fixture statement and its annexures.",
                    "",
                    "4. The third substantive fixture obligation concerns the",
                    "designated fixture officer at each head office.",
                ]
            ],
        ),
        expect={
            "no_modified_clauses": True,
            "renumbered_at_least": 3,
            "added_exactly": 1,
            "removed_exactly": 0,
        },
    ),
    PairSpec(
        name="reordered_only",
        document_group="fixture-reordering",
        title="Fixture Circular - Reordering Only",
        note=(
            "Two clauses swap places and keep their wording *and* their "
            "numbers. Nothing changed, so nothing may be reported as "
            "modified; the pair that moved must be flagged reordered."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-03-01",
            pages=[
                _preamble()
                + [
                    "1. Alpha provision concerning the fixture register and",
                    "the manner in which it is to be maintained.",
                    "",
                    "2. Beta provision concerning the fixture inspection and",
                    "the officer who is to carry it out.",
                    "",
                    "3. Gamma provision concerning the fixture appeal and the",
                    "period within which it must be filed.",
                ]
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-03-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "3. Gamma provision concerning the fixture appeal and the",
                    "period within which it must be filed.",
                    "",
                    "1. Alpha provision concerning the fixture register and",
                    "the manner in which it is to be maintained.",
                    "",
                    "2. Beta provision concerning the fixture inspection and",
                    "the officer who is to carry it out.",
                ]
            ],
        ),
        expect={
            "no_modified_clauses": True,
            "reordered_at_least": 1,
            "added_exactly": 0,
            "removed_exactly": 0,
        },
    ),
    PairSpec(
        name="ambiguous_alignment",
        document_group="fixture-ambiguity",
        title="Fixture Circular - Ambiguous Alignment",
        note=(
            "Two clauses in the new issue are near-identical to each other "
            "and both resemble one clause in the old issue. The matcher must "
            "pick one and say the choice was uncertain -- not silently "
            "commit, and not refuse to match."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-04-01",
            pages=[
                _preamble()
                + [
                    "1. The fixture authority shall maintain a register of",
                    "participating fixture institutions in the prescribed",
                    "form and shall review it annually.",
                ]
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-04-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "1. The fixture authority shall maintain a register of",
                    "participating fixture institutions in the prescribed",
                    "form and shall review it every second year.",
                    "",
                    "2. The fixture authority shall maintain a register of",
                    "participating fixture institutions in the prescribed",
                    "form and shall review it every third year.",
                ]
            ],
        ),
        expect={
            "expect_ambiguous": True,
            "added_exactly": 1,
        },
    ),
    PairSpec(
        name="multi_page_clause",
        document_group="fixture-multipage",
        title="Fixture Circular - Clause Spanning A Page Break",
        note=(
            "A clause that begins on page 1 and finishes on page 2, so the "
            "comparison must carry two segment_pages rows through to the "
            "evidence and resolve both. The second issue changes a figure "
            "inside the spanning clause, so the change itself is on the far "
            "side of the page break."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-05-01",
            pages=[
                _preamble()
                + [
                    "1. Every participating fixture institution shall",
                    "prepare, maintain and preserve the fixture register in",
                    "the prescribed form, and shall ensure that the register",
                    "records each transaction, each exemption claimed and",
                    "each direction received from the fixture authority,",
                    "together with the date on which it was received and",
                ],
                [
                    "the name of the officer who received it, and shall",
                    "retain the register for a period of 5 years from the",
                    "close of the financial year to which it relates.",
                    "",
                    "2. A short closing clause entirely on the second page.",
                ],
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-05-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "1. Every participating fixture institution shall",
                    "prepare, maintain and preserve the fixture register in",
                    "the prescribed form, and shall ensure that the register",
                    "records each transaction, each exemption claimed and",
                    "each direction received from the fixture authority,",
                    "together with the date on which it was received and",
                ],
                [
                    "the name of the officer who received it, and shall",
                    "retain the register for a period of 8 years from the",
                    "close of the financial year to which it relates.",
                    "",
                    "2. A short closing clause entirely on the second page.",
                ],
            ],
        ),
        expect={
            "spanning_clause_required": True,
            "modified_period_change": ("5 years", "8 years"),
        },
    ),
    PairSpec(
        name="identical_versions",
        document_group="fixture-identical",
        title="Fixture Circular - Two Identical Issues",
        note=(
            "The degenerate case, and a real one: a circular reissued with "
            "no change. Every clause must be unchanged and the result must be "
            "a successful comparison, not an error or an empty list. Only the "
            "page footer differs, which the header/footer cleaner removes -- "
            "so this also checks that cleaning does not leak into the diff."
        ),
        base=VersionSpec(
            version_label="fixture-v1",
            publication_date="2024-06-01",
            pages=[
                _preamble()
                + [
                    "1. The sole substantive clause of this fixture circular,",
                    "which is reissued without amendment.",
                    "",
                    "2. A second clause, likewise unamended.",
                ]
            ],
        ),
        compare=VersionSpec(
            version_label="fixture-v2",
            publication_date="2025-06-01",
            supersedes="fixture-v1",
            pages=[
                _preamble()
                + [
                    "1. The sole substantive clause of this fixture circular,",
                    "which is reissued without amendment.",
                    "",
                    "2. A second clause, likewise unamended.",
                ]
            ],
        ),
        expect={
            "all_unchanged": True,
            "added_exactly": 0,
            "removed_exactly": 0,
        },
    ),
]

PAIRS_BY_NAME: Dict[str, PairSpec] = {pair.name: pair for pair in PAIRS}


def write_pair(pair: PairSpec, directory: Path) -> Dict[str, Path]:
    """Write the pair's two PDFs into ``directory``. Returns their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    for side, spec in (("base", pair.base), ("compare", pair.compare)):
        path = directory / f"{pair.name}_{spec.version_label}.pdf"
        path.write_bytes(
            build_pdf_bytes(
                [list(page) for page in spec.pages],
                info={"Title": f"{pair.title} ({spec.version_label})"},
            )
        )
        out[side] = path
    return out


def build_and_process(
    pair: PairSpec, workdir: Path
) -> Dict[str, object]:
    """Run one pair all the way through the real pipeline, on disk.

    Returns ``{"raw_dir", "processed_dir", "version_ids": {side: fingerprint}}``.
    The caller loads ``processed_dir`` with ``db.loader`` exactly as it would
    load a real corpus -- there is no fixture-only load path, so a fixture
    cannot pass through a door a real document does not use.
    """
    # Imported here so the module is importable without the pipeline on the
    # path, which keeps `pytest --collect-only` cheap.
    #
    # These are the CLIs' *own* entry points, not reimplementations of them:
    # ``extract_pdf`` is what the extraction CLI calls, and
    # ``segment_extraction`` / ``result_payload`` are what the segmentation
    # CLI calls. A fixture therefore cannot drift from the pipeline, because
    # there is only one pipeline.
    from backend.app.document_processing.pdf_extractor import extract_pdf
    from backend.app.document_processing.schemas import SourceMetadata
    from backend.app.document_processing.segmentation_cli import (
        CLEANING_FILENAME,
        SEGMENT_FILENAMES,
        result_payload,
        segment_extraction,
    )

    raw_dir = workdir / "raw"
    processed_dir = workdir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    paths = write_pair(pair, raw_dir)
    version_ids: Dict[str, str] = {}

    for side, spec in (("base", pair.base), ("compare", pair.compare)):
        metadata = SourceMetadata(
            title=pair.title,
            issuing_department="Fixture Department (synthetic)",
            document_type="circular",
            publication_date=spec.publication_date,
            document_group=pair.document_group,
            version_label=spec.version_label,
            supersedes=spec.supersedes,
        )
        extraction = extract_pdf(paths[side], source_metadata=metadata)
        payload = extraction.to_dict()
        fingerprint = str(payload["document"]["version_id"])
        version_ids[side] = fingerprint

        target = processed_dir / fingerprint
        target.mkdir(parents=True, exist_ok=True)
        (target / "extraction.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        results, cleaning_report = segment_extraction(payload)
        (target / CLEANING_FILENAME).write_text(
            json.dumps(cleaning_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for method, result in results.items():
            (target / SEGMENT_FILENAMES[method]).write_text(
                json.dumps(
                    result_payload(result, payload), indent=2, ensure_ascii=False
                ),
                encoding="utf-8",
            )

    return {
        "raw_dir": raw_dir,
        "processed_dir": processed_dir,
        "version_ids": version_ids,
    }
