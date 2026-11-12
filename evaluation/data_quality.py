"""Experiment C — corpus-wide data-quality checks.

Implements the checks specified in ``data/README.md`` §8 and ``CLAUDE.md`` §15
Experiment C, over the artefacts the pipeline actually produces: the manifest,
the extraction JSON and the two segment JSON files per version.

Design rules
------------
**Nothing is repaired.** Every check reports; none writes. ``data/README.md``
§8's requirement is "a count and the offending identifiers", and the brief adds
"do not silently repair invalid data without recording the correction". The
simplest way to honour both is for this module to have no write path at all, so
a correction has to be made deliberately, somewhere else, by a person.

**Severity is part of the finding.** A missing optional field is a documented
gap; a segment whose page reference is outside ``1..page_count`` is a
provenance bug that invalidates a citation. Grading them the same would bury
the second in a list of the first.

====================  ==========================================================
``error``             A broken invariant. Must be zero. Wired into pytest, so a
                      regression fails the build rather than quietly degrading
                      the corpus (``data/README.md`` §8).
``warning``           Probably wrong, or worth a human look.
``info``              A documented gap or a fact worth counting. Not a defect.
====================  ==========================================================

**A check that cannot run says so.** If the segment files are absent, the
segment checks report ``not_applicable`` rather than passing vacuously. A check
that silently passes because it had no input is worse than no check.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from backend.app.document_processing.manifest import ManifestError, load_manifest
from backend.app.document_processing.segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
)
from backend.app.document_processing.segmentation_cli import (
    CLEANING_FILENAME,
    EXTRACTION_FILENAME,
    SEGMENT_FILENAMES,
)

DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_MANIFEST = Path("data/manifest.csv")
DEFAULT_OUTPUT_DIR = Path("evaluation/data-quality")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

#: Identifier shapes the pipeline promises. A malformed one breaks every
#: downstream citation, so the format is asserted rather than assumed.
_DOCUMENT_ID_RE = re.compile(r"^DOC-[0-9a-f]{16}$")
_VERSION_ID_RE = re.compile(r"^VER-[0-9a-f]{16}$")
_SEGMENT_ID_RE = re.compile(r"^SEG-[0-9a-f]{16}$")
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")

#: Metadata fields `data/README.md` §5 lists as mandatory, and as nullable.
MANDATORY_METADATA = ("title", "source_url", "retrieved_at", "document_group", "version_label")
OPTIONAL_METADATA = (
    "issuing_department", "document_type", "reference_no", "publication_date", "supersedes",
)

#: A clause_aware segment covering more than this fraction of its version's
#: pages is a segmentation anomaly: "one clause covering a whole document"
#: (``data/README.md`` §8).
_WHOLE_DOCUMENT_SEGMENT_FRACTION = 0.8

#: Near-duplicate threshold for document text, as a Jaccard overlap of word
#: sets. Two versions of one policy legitimately score high here, so this is a
#: warning that asks a human "are these declared as versions of each other?",
#: never an error.
_NEAR_DUPLICATE_THRESHOLD = 0.9


@dataclass
class Finding:
    """One data-quality observation."""

    check: str
    severity: str
    message: str
    #: The offending identifiers, so a finding is actionable without re-running.
    subjects: List[str] = field(default_factory=list)
    count: int = 0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class QualityReport:
    """Every finding from one run, plus what was actually inspected."""

    generated_at: str
    processed_dir: str
    manifest: Optional[str]
    versions_inspected: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    checks_not_applicable: Dict[str, str] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)

    def add(
        self,
        check: str,
        severity: str,
        message: str,
        subjects: Optional[Sequence[str]] = None,
    ) -> None:
        subjects = list(subjects or [])
        self.findings.append(
            Finding(
                check=check,
                severity=severity,
                message=message,
                subjects=subjects,
                count=len(subjects),
            )
        )

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(
            1 for finding in self.findings if finding.severity == SEVERITY_WARNING
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "experiment": "C - data quality",
            "generated_at": self.generated_at,
            "processed_dir": self.processed_dir,
            "manifest": self.manifest,
            "versions_inspected": self.versions_inspected,
            "checks_run": self.checks_run,
            "checks_not_applicable": self.checks_not_applicable,
            "totals": {
                "findings": len(self.findings),
                "errors": self.error_count,
                "warnings": self.warning_count,
                "info": sum(
                    1 for f in self.findings if f.severity == SEVERITY_INFO
                ),
            },
            "findings": [finding.to_dict() for finding in self.findings],
        }


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


@dataclass
class VersionArtifacts:
    """Everything on disk for one version. Missing files stay ``None``."""

    version_id: str
    directory: Path
    extraction: Optional[Dict[str, object]] = None
    segments: Dict[str, Dict[str, object]] = field(default_factory=dict)
    cleaning: Optional[Dict[str, object]] = None
    load_errors: List[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        if not self.extraction:
            return 0
        return int(self.extraction["document"]["page_count"])  # type: ignore[index]


def _collapse_whitespace(text: str) -> str:
    """Whitespace-insensitive comparison key. See check_offset_resolution."""
    return re.sub(r"\s+", " ", text).strip()


def _read_json(path: Path) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name} is not valid JSON: {exc}"


def load_version_artifacts(directory: Path) -> VersionArtifacts:
    """Load every artefact for one version directory, tolerating absences."""
    artifacts = VersionArtifacts(version_id=directory.name, directory=directory)

    extraction, error = _read_json(directory / EXTRACTION_FILENAME)
    if error:
        artifacts.load_errors.append(error)
    artifacts.extraction = extraction

    for method, filename in SEGMENT_FILENAMES.items():
        payload, error = _read_json(directory / filename)
        if error:
            artifacts.load_errors.append(error)
        if payload is not None:
            artifacts.segments[method] = payload

    cleaning, error = _read_json(directory / CLEANING_FILENAME)
    if error:
        artifacts.load_errors.append(error)
    artifacts.cleaning = cleaning
    return artifacts


def discover_versions(processed_dir: Path) -> List[VersionArtifacts]:
    if not processed_dir.exists():
        return []
    return [
        load_version_artifacts(child)
        for child in sorted(processed_dir.iterdir())
        if child.is_dir()
    ]


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_identifiers(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Identifier presence and format, at every level.

    "Missing document IDs" and "invalid version IDs" from the brief. A malformed
    identifier breaks every citation that would have referenced it, so these are
    errors.
    """
    report.checks_run.append("identifiers")
    missing_document: List[str] = []
    malformed_document: List[str] = []
    malformed_version: List[str] = []
    directory_mismatch: List[str] = []
    malformed_checksum: List[str] = []
    malformed_segment: List[str] = []

    for artifacts in versions:
        if not artifacts.extraction:
            continue
        document = artifacts.extraction["document"]  # type: ignore[index]
        document_id = document.get("document_id")  # type: ignore[union-attr]
        version_id = document.get("version_id")  # type: ignore[union-attr]
        checksum = document.get("checksum")  # type: ignore[union-attr]

        if not document_id:
            missing_document.append(artifacts.version_id)
        elif not _DOCUMENT_ID_RE.match(str(document_id)):
            malformed_document.append(f"{artifacts.version_id}:{document_id}")
        if not version_id or not _VERSION_ID_RE.match(str(version_id)):
            malformed_version.append(f"{artifacts.version_id}:{version_id}")
        elif str(version_id) != artifacts.directory.name:
            # The directory name is how every other tool finds this version.
            directory_mismatch.append(
                f"{artifacts.directory.name} holds {version_id}"
            )
        if not checksum or not _CHECKSUM_RE.match(str(checksum)):
            malformed_checksum.append(f"{artifacts.version_id}:{checksum}")

        for payload in artifacts.segments.values():
            for segment in payload.get("segments", []):  # type: ignore[union-attr]
                segment_id = segment.get("segment_id")
                if not segment_id or not _SEGMENT_ID_RE.match(str(segment_id)):
                    malformed_segment.append(
                        f"{artifacts.version_id}:{segment_id}"
                    )

    for subjects, message in (
        (missing_document, "version has no document_id"),
        (malformed_document, "document_id does not match DOC-<16 hex>"),
        (malformed_version, "version_id does not match VER-<16 hex>"),
        (directory_mismatch, "directory name does not match the version_id inside"),
        (malformed_checksum, "checksum is not 64 lowercase hex characters"),
        (malformed_segment, "segment_id does not match SEG-<16 hex>"),
    ):
        if subjects:
            report.add("identifiers", SEVERITY_ERROR, message, subjects)
    if not any(
        [
            missing_document, malformed_document, malformed_version,
            directory_mismatch, malformed_checksum, malformed_segment,
        ]
    ):
        report.add(
            "identifiers",
            SEVERITY_INFO,
            f"all identifiers well-formed across {len(versions)} version(s)",
        )


def check_duplicate_files(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """The same physical file ingested twice as two versions."""
    report.checks_run.append("duplicate_files")
    by_checksum: Dict[str, List[str]] = defaultdict(list)
    for artifacts in versions:
        if not artifacts.extraction:
            continue
        checksum = artifacts.extraction["document"].get("checksum")  # type: ignore[union-attr]
        if checksum:
            by_checksum[str(checksum)].append(artifacts.version_id)

    duplicates = [
        f"{checksum[:12]}...: {', '.join(ids)}"
        for checksum, ids in by_checksum.items()
        if len(ids) > 1
    ]
    if duplicates:
        report.add(
            "duplicate_files",
            SEVERITY_ERROR,
            "the same file (identical SHA-256) is present as more than one version",
            duplicates,
        )
    else:
        report.add(
            "duplicate_files",
            SEVERITY_INFO,
            f"{len(by_checksum)} distinct checksum(s); no duplicate files",
        )


def check_near_duplicate_documents(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Different files whose text is nearly identical.

    A warning, never an error: two versions of one policy *should* score high
    here. The question it asks a human is whether they are declared as versions
    of each other in the manifest, or silently unrelated (``CLAUDE.md`` D-9).
    """
    report.checks_run.append("near_duplicate_documents")
    word_sets: Dict[str, Set[str]] = {}
    groups: Dict[str, Optional[str]] = {}
    for artifacts in versions:
        if not artifacts.extraction:
            continue
        words: Set[str] = set()
        for page in artifacts.extraction["pages"]:  # type: ignore[index]
            words.update(str(page.get("normalized_text", "")).split())
        if words:
            word_sets[artifacts.version_id] = words
            metadata = artifacts.extraction["document"].get("source_metadata") or {}  # type: ignore[union-attr]
            groups[artifacts.version_id] = metadata.get("document_group")

    flagged: List[str] = []
    identifiers = sorted(word_sets)
    for index, left in enumerate(identifiers):
        for right in identifiers[index + 1 :]:
            left_words, right_words = word_sets[left], word_sets[right]
            union = len(left_words | right_words)
            if not union:
                continue
            overlap = len(left_words & right_words) / union
            if overlap >= _NEAR_DUPLICATE_THRESHOLD:
                same_group = groups[left] == groups[right] and groups[left] is not None
                flagged.append(
                    f"{left} / {right}: {overlap:.0%} word overlap, "
                    f"{'same' if same_group else 'DIFFERENT'} document_group"
                )
    if flagged:
        report.add(
            "near_duplicate_documents",
            SEVERITY_WARNING,
            "near-duplicate text; confirm whether these are declared versions "
            "of the same policy",
            flagged,
        )
    else:
        report.add(
            "near_duplicate_documents",
            SEVERITY_INFO,
            f"no pair of {len(word_sets)} version(s) exceeded "
            f"{_NEAR_DUPLICATE_THRESHOLD:.0%} word overlap",
        )


def check_pages(versions: Sequence[VersionArtifacts], report: QualityReport) -> None:
    """Page sequence integrity and empty-page counts."""
    report.checks_run.append("pages")
    sequence_errors: List[str] = []
    count_mismatch: List[str] = []
    empty_pages: List[str] = []
    missing_normalized: List[str] = []

    for artifacts in versions:
        if not artifacts.extraction:
            continue
        pages = artifacts.extraction["pages"]  # type: ignore[index]
        numbers = [page.get("page_number") for page in pages]
        if numbers != list(range(1, len(pages) + 1)):
            sequence_errors.append(
                f"{artifacts.version_id}: page numbers are {numbers[:8]}..."
            )
        if artifacts.page_count != len(pages):
            count_mismatch.append(
                f"{artifacts.version_id}: page_count={artifacts.page_count} "
                f"but {len(pages)} page record(s)"
            )
        for page in pages:
            if page.get("is_empty"):
                empty_pages.append(
                    f"{artifacts.version_id} p{page.get('page_number')}"
                )
            if page.get("normalized_text") is None:
                missing_normalized.append(
                    f"{artifacts.version_id} p{page.get('page_number')}"
                )

    if sequence_errors:
        report.add("pages", SEVERITY_ERROR, "page numbers are not a dense 1-based sequence", sequence_errors)
    if count_mismatch:
        report.add("pages", SEVERITY_ERROR, "document.page_count disagrees with the page records", count_mismatch)
    if missing_normalized:
        report.add("pages", SEVERITY_ERROR, "page has no normalized_text", missing_normalized)
    if empty_pages:
        report.add(
            "pages",
            SEVERITY_WARNING,
            "page extracted zero characters - likely scanned or image-only "
            "(no OCR in this phase)",
            empty_pages,
        )
    if not (sequence_errors or count_mismatch or missing_normalized or empty_pages):
        total = sum(artifacts.page_count for artifacts in versions)
        report.add("pages", SEVERITY_INFO, f"{total} page(s) across {len(versions)} version(s), all non-empty and in sequence")


def check_metadata_completeness(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Count the documented metadata gaps.

    Mandatory fields missing is an error; optional fields missing is ``info``.
    ``data/README.md`` §4 is explicit that a blank optional field is a
    *documented gap*, not a defect — "empty is better than wrong".
    """
    report.checks_run.append("metadata_completeness")
    missing_mandatory: List[str] = []
    gaps: Counter[str] = Counter()

    for artifacts in versions:
        if not artifacts.extraction:
            continue
        metadata = artifacts.extraction["document"].get("source_metadata") or {}  # type: ignore[union-attr]
        for field_name in MANDATORY_METADATA:
            if not metadata.get(field_name):
                missing_mandatory.append(f"{artifacts.version_id}:{field_name}")
        for field_name in OPTIONAL_METADATA:
            if not metadata.get(field_name):
                gaps[field_name] += 1

    if missing_mandatory:
        report.add(
            "metadata_completeness",
            SEVERITY_ERROR,
            "a field data/README.md §5 lists as mandatory is blank",
            missing_mandatory,
        )
    if gaps:
        report.add(
            "metadata_completeness",
            SEVERITY_INFO,
            "optional metadata gaps, by field (a documented gap, not a defect)",
            [f"{name}: {count} version(s)" for name, count in sorted(gaps.items())],
        )


def check_extraction_status(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Versions absent from the usable corpus because extraction failed."""
    report.checks_run.append("extraction_status")
    failed: List[str] = []
    partial: List[str] = []
    for artifacts in versions:
        if not artifacts.extraction:
            continue
        status = artifacts.extraction["document"].get("extraction_status")  # type: ignore[union-attr]
        if status == "failed":
            failed.append(artifacts.version_id)
        elif status == "partial":
            partial.append(artifacts.version_id)
    if failed:
        report.add("extraction_status", SEVERITY_ERROR, "extraction_status='failed': this version is unusable", failed)
    if partial:
        report.add("extraction_status", SEVERITY_WARNING, "extraction_status='partial': some pages carry no usable text", partial)
    if not (failed or partial):
        report.add("extraction_status", SEVERITY_INFO, f"all {len(versions)} version(s) extracted with status 'ok'")


def check_segment_provenance(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """The project's load-bearing invariant, checked on the artefacts.

    ``Segment.__post_init__`` enforces this in memory, but the files on disk are
    what Persons 2-5 actually read, and they could have been hand-edited or
    written by an older version of the pipeline. Checking the artefact is what
    makes the guarantee hold end to end.
    """
    check = "segment_provenance"
    with_segments = [a for a in versions if a.segments]
    if not with_segments:
        report.checks_not_applicable[check] = (
            "no segment files found; run the segmentation CLI first"
        )
        return
    report.checks_run.append(check)

    missing_offsets: List[str] = []
    out_of_range: List[str] = []
    reversed_range: List[str] = []
    range_disagreement: List[str] = []
    bad_char_offsets: List[str] = []
    empty_text: List[str] = []
    spanning_single_offset: List[str] = []

    for artifacts in with_segments:
        page_count = artifacts.page_count
        page_lengths = {}
        if artifacts.extraction:
            basis_field = "normalized_text"
            for page in artifacts.extraction["pages"]:  # type: ignore[index]
                page_lengths[page["page_number"]] = len(
                    str(page.get(basis_field) or "")
                )

        for method, payload in artifacts.segments.items():
            for segment in payload.get("segments", []):  # type: ignore[union-attr]
                label = f"{artifacts.version_id}/{method}/{segment.get('segment_id')}"
                offsets = segment.get("page_offsets") or []
                if not offsets:
                    missing_offsets.append(label)
                    continue
                start, end = segment.get("page_start"), segment.get("page_end")
                if not isinstance(start, int) or not isinstance(end, int):
                    missing_offsets.append(label)
                    continue
                if end < start:
                    reversed_range.append(f"{label}: {start}-{end}")
                if page_count and not (1 <= start <= page_count and 1 <= end <= page_count):
                    out_of_range.append(
                        f"{label}: pages {start}-{end} outside 1..{page_count}"
                    )
                touched = sorted({offset["page_number"] for offset in offsets})
                if touched and (touched[0] != start or touched[-1] != end):
                    range_disagreement.append(
                        f"{label}: range {start}-{end} but offsets touch {touched}"
                    )
                if end > start and len(offsets) < 2:
                    spanning_single_offset.append(
                        f"{label}: spans {start}-{end} with only "
                        f"{len(offsets)} offset entry"
                    )
                for offset in offsets:
                    page_number = offset["page_number"]
                    char_start, char_end = offset["char_start"], offset["char_end"]
                    limit = page_lengths.get(page_number)
                    if char_start is None or char_end is None or char_end < char_start:
                        bad_char_offsets.append(f"{label} p{page_number}: {char_start}-{char_end}")
                    elif limit is not None and char_end > limit:
                        bad_char_offsets.append(
                            f"{label} p{page_number}: char_end {char_end} "
                            f"beyond the page's {limit} characters"
                        )
                if not str(segment.get("text") or "").strip():
                    empty_text.append(label)

    for subjects, message in (
        (missing_offsets, "segment has no page_offsets - the provenance invariant is violated"),
        (out_of_range, "segment page reference is outside 1..page_count"),
        (reversed_range, "segment page_end precedes page_start"),
        (range_disagreement, "segment page range disagrees with the pages its offsets touch"),
        (bad_char_offsets, "segment character offset is negative, reversed, or past the end of its page"),
        (empty_text, "segment has empty text"),
        (spanning_single_offset, "page-spanning segment has fewer than two page_offsets entries"),
    ):
        if subjects:
            report.add(check, SEVERITY_ERROR, message, subjects)

    if not any([
        missing_offsets, out_of_range, reversed_range, range_disagreement,
        bad_char_offsets, empty_text, spanning_single_offset,
    ]):
        total = sum(
            len(payload.get("segments", []))  # type: ignore[union-attr]
            for artifacts in with_segments
            for payload in artifacts.segments.values()
        )
        report.add(check, SEVERITY_INFO, f"all {total} segment(s) carry valid page provenance")


def check_offset_resolution(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Resolve every segment's text *back* from its stored character offsets.

    The other provenance check asks whether the offsets are *well formed* --
    present, ordered, inside the page. This one asks the harder question the
    project's traceability claim actually rests on: if a consumer takes a
    stored ``(page_number, char_start, char_end)`` and slices the page text
    named by ``offset_basis``, do they get the clause back?

    That is the operation Person 2's highlighting and Person 3's citations both
    perform, so an unresolvable offset is a broken citation even when every
    bound is individually legal.

    Comparison is whitespace-insensitive, and that tolerance is the finding
    rather than a convenience: segmentation joins wrapped source lines with a
    space, so a slice of the page carries the newline the segment text does
    not. Character *ranges* are exact; only intra-segment whitespace differs.
    Anything beyond whitespace -- an off-by-one, a stale offset, the wrong
    basis -- still fails here.
    """
    check = "offset_resolution"
    usable = [
        a for a in versions
        if a.segments and a.extraction and not a.load_errors
    ]
    if not usable:
        report.checks_not_applicable[check] = (
            "needs both extraction.json and a segment file per version"
        )
        return
    report.checks_run.append(check)

    unresolved: List[str] = []
    missing_page: List[str] = []
    wrong_basis: List[str] = []
    resolved = 0

    for artifacts in usable:
        declared_bases = {
            str(payload.get("offset_basis"))
            for payload in artifacts.segments.values()
        }
        for basis in sorted(declared_bases):
            if basis != "normalized_text":
                wrong_basis.append(f"{artifacts.version_id}: offset_basis={basis!r}")

        pages = {
            int(page["page_number"]): str(page.get("normalized_text") or "")
            for page in artifacts.extraction["pages"]  # type: ignore[index]
        }

        for method, payload in artifacts.segments.items():
            if str(payload.get("offset_basis")) != "normalized_text":
                continue
            for segment in payload.get("segments", []):  # type: ignore[union-attr]
                label = f"{artifacts.version_id}/{method}/{segment.get('segment_id')}"
                fragments = []
                absent = False
                for offset in segment.get("page_offsets") or []:
                    page_text = pages.get(int(offset["page_number"]))
                    if page_text is None:
                        missing_page.append(
                            f"{label}: references page {offset['page_number']}, "
                            f"which is not in extraction.json"
                        )
                        absent = True
                        break
                    fragments.append(
                        page_text[int(offset["char_start"]):int(offset["char_end"])]
                    )
                if absent or not fragments:
                    continue
                rebuilt = _collapse_whitespace(" ".join(fragments))
                stored = _collapse_whitespace(str(segment.get("text") or ""))
                if rebuilt == stored:
                    resolved += 1
                else:
                    unresolved.append(
                        f"{label}: offsets rebuild {len(rebuilt)} chars, "
                        f"segment text is {len(stored)}"
                    )

    for subjects, message in (
        (wrong_basis, "segment file declares an offset_basis other than normalized_text (D-11)"),
        (missing_page, "segment offset references a page absent from extraction.json"),
        (unresolved, "segment text cannot be rebuilt from its stored character offsets"),
    ):
        if subjects:
            report.add(check, SEVERITY_ERROR, message, subjects)

    if not any([wrong_basis, missing_page, unresolved]):
        report.add(
            check,
            SEVERITY_INFO,
            f"all {resolved} segment(s) rebuild exactly from their stored "
            f"offsets into normalized_text (whitespace-insensitive)",
        )


def check_segment_structure(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Ordinals, duplicate text, parent pointers and segmentation anomalies."""
    check = "segment_structure"
    with_segments = [a for a in versions if a.segments]
    if not with_segments:
        report.checks_not_applicable[check] = "no segment files found"
        return
    report.checks_run.append(check)

    ordinal_problems: List[str] = []
    duplicate_ids: List[str] = []
    duplicate_text: List[str] = []
    dangling_parents: List[str] = []
    self_parents: List[str] = []
    whole_document_segments: List[str] = []
    unknown_method: List[str] = []

    for artifacts in with_segments:
        for method, payload in artifacts.segments.items():
            segments = payload.get("segments", [])  # type: ignore[union-attr]
            declared = payload.get("segmentation_method")
            if declared != method:
                unknown_method.append(
                    f"{artifacts.version_id}/{method}: file declares "
                    f"segmentation_method={declared!r}"
                )

            ordinals = [segment.get("ordinal") for segment in segments]
            if ordinals != list(range(len(segments))):
                ordinal_problems.append(
                    f"{artifacts.version_id}/{method}: ordinals are not dense "
                    f"and ordered ({ordinals[:8]}...)"
                )

            identifiers = [segment.get("segment_id") for segment in segments]
            for segment_id, count in Counter(identifiers).items():
                if count > 1:
                    duplicate_ids.append(
                        f"{artifacts.version_id}/{method}:{segment_id} x{count}"
                    )

            # Duplicate clause text within one version and method. Short
            # segments are excluded: a one-line "Annexure" heading repeating is
            # expected, and flagging it would bury the real cases.
            texts = Counter(
                " ".join(str(segment.get("text", "")).split())
                for segment in segments
                if len(str(segment.get("text", ""))) >= 80
            )
            for text, count in texts.items():
                if count > 1:
                    duplicate_text.append(
                        f"{artifacts.version_id}/{method}: {count}x {text[:60]!r}"
                    )

            known_ids = set(identifiers)
            for segment in segments:
                parent = segment.get("parent_segment_id")
                if parent is None:
                    continue
                if parent == segment.get("segment_id"):
                    self_parents.append(
                        f"{artifacts.version_id}/{method}:{parent}"
                    )
                elif parent not in known_ids:
                    dangling_parents.append(
                        f"{artifacts.version_id}/{method}:"
                        f"{segment.get('segment_id')} -> {parent}"
                    )

            if method == METHOD_CLAUSE_AWARE and artifacts.page_count > 2:
                for segment in segments:
                    start, end = segment.get("page_start"), segment.get("page_end")
                    if not isinstance(start, int) or not isinstance(end, int):
                        continue
                    span = end - start + 1
                    if span / artifacts.page_count >= _WHOLE_DOCUMENT_SEGMENT_FRACTION:
                        whole_document_segments.append(
                            f"{artifacts.version_id}/{method}:"
                            f"{segment.get('segment_id')} spans {span} of "
                            f"{artifacts.page_count} pages"
                        )

    for subjects, severity, message in (
        (ordinal_problems, SEVERITY_ERROR, "segment ordinals are not dense and in document order"),
        (duplicate_ids, SEVERITY_ERROR, "duplicate segment_id within one version and method"),
        (self_parents, SEVERITY_ERROR, "segment is its own parent"),
        (dangling_parents, SEVERITY_ERROR, "parent_segment_id names a segment that does not exist"),
        (unknown_method, SEVERITY_ERROR, "segment file's declared method does not match its filename"),
        (duplicate_text, SEVERITY_WARNING, "identical segment text (>=80 chars) appears more than once in one version"),
        (whole_document_segments, SEVERITY_WARNING, "a single clause_aware segment covers almost the whole document - segmentation probably failed"),
    ):
        if subjects:
            report.add(check, severity, message, subjects)

    if not any([
        ordinal_problems, duplicate_ids, self_parents, dangling_parents,
        unknown_method, duplicate_text, whole_document_segments,
    ]):
        report.add(check, SEVERITY_INFO, "segment structure is consistent in every file")


def check_segmentation_consistency(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Do the two methods agree about the document they segmented?

    "Inconsistent segmentation outputs" from the brief. Both methods run on the
    same page text, so they must agree on the version, the document and the
    offset basis, and neither may be empty while the other is not.
    """
    check = "segmentation_consistency"
    with_both = [a for a in versions if len(a.segments) == 2]
    if not with_both:
        report.checks_not_applicable[check] = (
            "no version has both segment files; run the segmentation CLI"
        )
        return
    report.checks_run.append(check)

    disagreements: List[str] = []
    one_sided: List[str] = []
    identical_counts: List[str] = []

    for artifacts in with_both:
        paragraph = artifacts.segments[METHOD_PARAGRAPH]
        clause = artifacts.segments[METHOD_CLAUSE_AWARE]
        for key in ("version_id", "document_id", "offset_basis"):
            if paragraph.get(key) != clause.get(key):
                disagreements.append(
                    f"{artifacts.version_id}: {key} differs between methods "
                    f"({paragraph.get(key)!r} vs {clause.get(key)!r})"
                )
        paragraph_count = len(paragraph.get("segments", []))  # type: ignore[union-attr]
        clause_count = len(clause.get("segments", []))  # type: ignore[union-attr]
        if bool(paragraph_count) != bool(clause_count):
            one_sided.append(
                f"{artifacts.version_id}: paragraph={paragraph_count}, "
                f"clause_aware={clause_count}"
            )
        elif paragraph_count and paragraph_count == clause_count:
            # Not necessarily wrong - a document where every clause is exactly
            # one paragraph would do this - but it is also what a silent
            # fallback to the baseline looks like, so it asks for a look.
            identical_counts.append(
                f"{artifacts.version_id}: both methods produced "
                f"{paragraph_count} segments"
            )

    if disagreements:
        report.add(check, SEVERITY_ERROR, "the two segment files disagree about what they segmented", disagreements)
    if one_sided:
        report.add(check, SEVERITY_ERROR, "one segmentation method produced nothing while the other produced segments", one_sided)
    if identical_counts:
        report.add(check, SEVERITY_WARNING, "both methods produced the same segment count - verify the clause method is not silently falling back to the baseline", identical_counts)
    if not (disagreements or one_sided or identical_counts):
        report.add(check, SEVERITY_INFO, f"both methods consistent on {len(with_both)} version(s)")


def check_cleaning_reversibility(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    """Header removal must be reversible on every page of every version."""
    check = "cleaning_reversibility"
    with_cleaning = [a for a in versions if a.cleaning]
    if not with_cleaning:
        report.checks_not_applicable[check] = "no page_cleaning.json found"
        return
    report.checks_run.append(check)

    irreversible: List[str] = []
    unexplained: List[str] = []
    for artifacts in with_cleaning:
        cleaning = artifacts.cleaning or {}
        totals = cleaning.get("totals") or {}
        if not totals.get("reversible", False):
            irreversible.append(artifacts.version_id)
        for page in cleaning.get("pages", []):  # type: ignore[union-attr]
            for line in page.get("removed_lines", []):
                if not line.get("reason") or not line.get("evidence"):
                    unexplained.append(
                        f"{artifacts.version_id} p{page.get('page_number')} "
                        f"line {line.get('line_index')}"
                    )

    if irreversible:
        report.add(check, SEVERITY_ERROR, "header/footer removal is not reversible - source evidence has been lost", irreversible)
    if unexplained:
        report.add(check, SEVERITY_ERROR, "a removed line has no reason or evidence recorded", unexplained)
    if not (irreversible or unexplained):
        removed = sum(
            (artifacts.cleaning or {}).get("totals", {}).get("removed_lines", 0)
            for artifacts in with_cleaning
        )
        report.add(check, SEVERITY_INFO, f"{removed} removed line(s) across {len(with_cleaning)} version(s), all reversible and all with recorded evidence")


def check_manifest(
    manifest_path: Path,
    versions: Sequence[VersionArtifacts],
    report: QualityReport,
) -> None:
    """Manifest integrity, and the version-relationship checks."""
    check = "manifest"
    if not manifest_path.exists():
        report.checks_not_applicable[check] = f"no manifest at {manifest_path}"
        return
    try:
        entries = load_manifest(manifest_path)
    except ManifestError as exc:
        report.add(check, SEVERITY_ERROR, f"manifest could not be read: {exc}")
        return
    report.checks_run.append(check)

    duplicate_filenames = [
        name
        for name, count in Counter(entry.pdf_path.name for entry in entries).items()
        if count > 1
    ]
    duplicate_checksums = [
        f"{checksum[:12]}..."
        for checksum, count in Counter(
            entry.expected_sha256 for entry in entries if entry.expected_sha256
        ).items()
        if count > 1
    ]
    if duplicate_filenames:
        report.add(check, SEVERITY_ERROR, "manifest lists the same local_filename more than once", duplicate_filenames)
    if duplicate_checksums:
        report.add(check, SEVERITY_ERROR, "manifest lists the same sha256 more than once", duplicate_checksums)

    # Checksum drift: the manifest says one thing, the extracted artefact
    # another. Means the document was re-published or the file was altered.
    extracted = {
        str(a.extraction["document"]["checksum"]): a.version_id  # type: ignore[index]
        for a in versions
        if a.extraction
    }
    recorded = {
        entry.expected_sha256.lower()
        for entry in entries
        if entry.expected_sha256
    }
    drifted = [
        f"{checksum[:12]}... ({version_id}) is not in the manifest"
        for checksum, version_id in extracted.items()
        if checksum.lower() not in recorded
    ]
    if drifted:
        report.add(check, SEVERITY_WARNING, "an extracted version's checksum is not recorded in the manifest", drifted)

    # Version relationships. `supersedes` must name a version_label that
    # exists, and within the same document_group - a chain crossing documents,
    # or looping, breaks Person 4's Compare ordering.
    labels_by_group: Dict[str, Set[str]] = defaultdict(set)
    for entry in entries:
        metadata = entry.source_metadata
        if metadata.document_group and metadata.version_label:
            labels_by_group[metadata.document_group].add(metadata.version_label)

    bad_supersedes: List[str] = []
    self_supersedes: List[str] = []
    for entry in entries:
        metadata = entry.source_metadata
        if not metadata.supersedes:
            continue
        if metadata.supersedes == metadata.version_label:
            self_supersedes.append(
                f"{entry.pdf_path.name}: supersedes itself ({metadata.supersedes})"
            )
            continue
        group = metadata.document_group
        if group is None or metadata.supersedes not in labels_by_group.get(group, set()):
            bad_supersedes.append(
                f"{entry.pdf_path.name}: supersedes "
                f"{metadata.supersedes!r}, which is not a version_label in "
                f"document_group {group!r}"
            )
    if self_supersedes:
        report.add(check, SEVERITY_ERROR, "a version supersedes itself - a cyclic chain", self_supersedes)
    if bad_supersedes:
        report.add(check, SEVERITY_ERROR, "supersedes names a version_label that does not exist in the same document_group", bad_supersedes)

    # Corpus composition facts, reported as info so collection gaps are visible.
    groups = Counter(
        entry.source_metadata.document_group
        for entry in entries
        if entry.source_metadata.document_group
    )
    version_pairs = [group for group, count in groups.items() if count > 1]
    report.add(
        check,
        SEVERITY_INFO,
        f"{len(entries)} manifest row(s), {len(groups)} document_group(s), "
        f"{len(version_pairs)} group(s) with more than one version",
    )
    if not version_pairs:
        report.add(
            check,
            SEVERITY_WARNING,
            "no document_group has two or more versions, so there is no "
            "version pair for Person 4's Compare feature to run on "
            "(data/README.md §2.4, CLAUDE.md D-10)",
        )
    scanned = [entry.pdf_path.name for entry in entries if entry.is_scanned]
    if scanned:
        report.add(check, SEVERITY_WARNING, "manifest marks these as scanned; they are excluded from the MVP corpus (D-6)", scanned)


def check_load_errors(
    versions: Sequence[VersionArtifacts], report: QualityReport
) -> None:
    report.checks_run.append("artifact_readability")
    problems = [
        f"{artifacts.version_id}: {error}"
        for artifacts in versions
        for error in artifacts.load_errors
    ]
    missing_extraction = [
        artifacts.version_id for artifacts in versions if artifacts.extraction is None
    ]
    if problems:
        report.add("artifact_readability", SEVERITY_ERROR, "artefact could not be parsed", problems)
    if missing_extraction:
        report.add("artifact_readability", SEVERITY_ERROR, "version directory has no readable extraction.json", missing_extraction)
    if not (problems or missing_extraction):
        report.add("artifact_readability", SEVERITY_INFO, f"every artefact in {len(versions)} version(s) parsed cleanly")


ALL_CHECKS = (
    check_load_errors,
    check_identifiers,
    check_duplicate_files,
    check_near_duplicate_documents,
    check_pages,
    check_metadata_completeness,
    check_extraction_status,
    check_cleaning_reversibility,
    check_segment_provenance,
    check_offset_resolution,
    check_segment_structure,
    check_segmentation_consistency,
)


def run_checks(
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    manifest_path: Optional[Path] = DEFAULT_MANIFEST,
) -> QualityReport:
    """Run every check and return the report. Writes nothing."""
    versions = discover_versions(processed_dir)
    report = QualityReport(
        generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        processed_dir=str(processed_dir),
        manifest=str(manifest_path) if manifest_path else None,
        versions_inspected=[artifacts.version_id for artifacts in versions],
    )
    if not versions:
        report.add(
            "artifact_readability",
            SEVERITY_WARNING,
            f"no version directories under {processed_dir}; nothing to check. "
            f"Run the extraction CLI first (data/README.md §9).",
        )
        return report

    for check in ALL_CHECKS:
        check(versions, report)
    if manifest_path:
        check_manifest(manifest_path, versions, report)
    return report


def format_report(report: QualityReport) -> str:
    """Render the human-readable summary."""
    lines = [
        "NiyamSetu data-quality report (Experiment C)",
        f"generated   : {report.generated_at}",
        f"processed   : {report.processed_dir}",
        f"versions    : {len(report.versions_inspected)}",
        f"checks run  : {len(report.checks_run)}",
        f"findings    : {len(report.findings)} "
        f"({report.error_count} error, {report.warning_count} warning, "
        f"{sum(1 for f in report.findings if f.severity == SEVERITY_INFO)} info)",
        "",
    ]
    if report.checks_not_applicable:
        lines.append("NOT APPLICABLE (reported rather than passed vacuously):")
        for check, reason in sorted(report.checks_not_applicable.items()):
            lines.append(f"  - {check}: {reason}")
        lines.append("")

    for severity, label in (
        (SEVERITY_ERROR, "ERRORS - broken invariants, must be zero"),
        (SEVERITY_WARNING, "WARNINGS - probably wrong, or worth a look"),
        (SEVERITY_INFO, "INFO - counts and documented gaps"),
    ):
        matching = [f for f in report.findings if f.severity == severity]
        if not matching:
            continue
        lines.append(f"{label}:")
        for finding in matching:
            suffix = f" ({finding.count})" if finding.count else ""
            lines.append(f"  [{finding.check}] {finding.message}{suffix}")
            for subject in finding.subjects[:8]:
                lines.append(f"      - {subject}")
            if finding.count > 8:
                lines.append(f"      ... and {finding.count - 8} more (see the JSON)")
        lines.append("")

    lines.append(
        "No check in this module repairs anything. Every finding above is a "
        "report only (data/README.md §8)."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.data_quality",
        description=(
            "Experiment C: corpus-wide data-quality checks over the manifest, "
            "extraction and segment artefacts. Reports; never repairs."
        ),
    )
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--no-manifest", action="store_true", help="Skip the manifest checks.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-write", action="store_true", help="Print the summary; write no files.")
    parser.add_argument(
        "--fail-on-warning", action="store_true",
        help="Exit non-zero on warnings as well as errors.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    report = run_checks(
        args.processed_dir, None if args.no_manifest else args.manifest
    )
    summary = format_report(report)
    print(summary)

    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / "results.json"
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        text_path = args.output_dir / "summary.txt"
        text_path.write_text(summary + "\n", encoding="utf-8")
        print(f"\nwrote {json_path}\nwrote {text_path}")

    if report.error_count:
        return 1
    if args.fail_on_warning and report.warning_count:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
