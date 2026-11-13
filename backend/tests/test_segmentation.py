"""Paragraph and clause-aware segmentation.

Grouped as:

* **The provenance invariant** — the rules that must never be violable, tested
  by trying to violate them.
* **Clause marker detection** — every numbering style, including styles the
  corpus does not contain (to prove graceful failure rather than a crash) and
  the false positives found in the real documents.
* **Paragraph segmentation** — the baseline, especially page-boundary handling.
* **Clause segmentation** — hierarchy, page spanning, unclassified text.
* **Contract and determinism** — JSON shape and reproducibility.

Fixtures are synthetic page text. Real-document numbers are reported in
``evaluation/experiment-b-segmentation.md``; a unit test asserting "the
capital-adequacy document yields 254 clauses" would be a change-detector, not a
test of correctness.
"""

from __future__ import annotations

import json
from typing import List, Tuple

import pytest

from backend.app.document_processing.errors import SegmentProvenanceError
from backend.app.document_processing.page_cleaning import CleaningOptions, clean_pages
from backend.app.document_processing.segmentation import (
    METHOD_CLAUSE_AWARE,
    METHOD_PARAGRAPH,
    OFFSET_BASIS_NORMALIZED,
    TYPE_ANNEXURE,
    TYPE_CLAUSE,
    TYPE_PREAMBLE,
    TYPE_UNCLASSIFIED,
    PageOffset,
    Segment,
    SourceLine,
    build_source_lines,
    detect_clause_marker,
    segment_clauses,
    segment_paragraphs,
)

DOC = "DOC-test"
VER = "VER-test"

#: Cleaning switched off, so a segmentation test exercises segmentation only.
NO_CLEANING = CleaningOptions(
    remove_repeated_headers=False,
    remove_repeated_footers=False,
    remove_page_numbers=False,
)


def lines_from(pages: List[Tuple[int, str]]) -> List[SourceLine]:
    """Build source lines from raw page text, with cleaning disabled."""
    cleaned, _ = clean_pages(pages, NO_CLEANING)
    return build_source_lines(cleaned)


def one_page(text: str) -> List[SourceLine]:
    return lines_from([(1, text)])


# --------------------------------------------------------------------------- #
# The provenance invariant
# --------------------------------------------------------------------------- #


def test_a_segment_cannot_be_built_without_page_offsets() -> None:
    """``CLAUDE.md`` §10.2, enforced in the constructor so no path can bypass it."""
    with pytest.raises(SegmentProvenanceError, match="no page offsets"):
        Segment(
            segment_id="SEG-x",
            document_id=DOC,
            version_id=VER,
            segmentation_method=METHOD_CLAUSE_AWARE,
            ordinal=0,
            segment_type=TYPE_CLAUSE,
            text="Some clause text.",
            page_start=1,
            page_end=1,
            page_offsets=[],
        )


def test_a_segment_cannot_have_a_zero_or_negative_page_number() -> None:
    with pytest.raises(SegmentProvenanceError, match="1-based"):
        PageOffset(
            page_number=0, char_start=0, char_end=5,
            line_index_start=0, line_index_end=0,
        )


def test_a_segment_cannot_have_a_reversed_page_range() -> None:
    with pytest.raises(SegmentProvenanceError, match="precedes"):
        Segment(
            segment_id="SEG-x",
            document_id=DOC,
            version_id=VER,
            segmentation_method=METHOD_CLAUSE_AWARE,
            ordinal=0,
            segment_type=TYPE_CLAUSE,
            text="Some clause text.",
            page_start=4,
            page_end=2,
            page_offsets=[
                PageOffset(
                    page_number=4, char_start=0, char_end=5,
                    line_index_start=0, line_index_end=0,
                )
            ],
        )


def test_a_page_range_must_agree_with_the_pages_its_offsets_touch() -> None:
    """Catches a range computed independently of the offsets and drifting."""
    with pytest.raises(SegmentProvenanceError, match="disagrees"):
        Segment(
            segment_id="SEG-x",
            document_id=DOC,
            version_id=VER,
            segmentation_method=METHOD_CLAUSE_AWARE,
            ordinal=0,
            segment_type=TYPE_CLAUSE,
            text="Some clause text.",
            page_start=1,
            page_end=5,  # claims 5 pages
            page_offsets=[
                PageOffset(
                    page_number=1, char_start=0, char_end=5,
                    line_index_start=0, line_index_end=0,
                )
            ],  # but touches only page 1
        )


def test_an_empty_segment_is_rejected() -> None:
    with pytest.raises(SegmentProvenanceError, match="empty segments"):
        Segment(
            segment_id="SEG-x",
            document_id=DOC,
            version_id=VER,
            segmentation_method=METHOD_PARAGRAPH,
            ordinal=0,
            segment_type=TYPE_CLAUSE,
            text="   \n  ",
            page_start=1,
            page_end=1,
            page_offsets=[
                PageOffset(
                    page_number=1, char_start=0, char_end=0,
                    line_index_start=0, line_index_end=0,
                )
            ],
        )


def test_blank_runs_never_become_segments() -> None:
    """"Avoid creating meaningless empty segments" - for both methods."""
    pages = [(1, "\n\n\n   \n\nReal content here.\n\n\n\n")]
    for result in (
        segment_paragraphs(lines_from(pages), DOC, VER),
        segment_clauses(lines_from(pages), DOC, VER),
    ):
        assert len(result.segments) == 1
        assert result.segments[0].text == "Real content here."


def test_every_real_segment_has_resolvable_offsets() -> None:
    """Offsets must index text that actually exists, for both methods."""
    pages = [
        (1, "1. First clause on page one.\n\n2. Second clause which runs on"),
        (2, "to the second page and finishes here.\n\n3. Third clause."),
    ]
    page_text = dict(pages)
    for result in (
        segment_paragraphs(lines_from(pages), DOC, VER),
        segment_clauses(lines_from(pages), DOC, VER),
    ):
        assert result.segments
        for segment in result.segments:
            words_in_slices: set[str] = set()
            for offset in segment.page_offsets:
                text = page_text[offset.page_number]
                assert 0 <= offset.char_start <= offset.char_end <= len(text)
                words_in_slices |= set(
                    text[offset.char_start : offset.char_end].split()
                )
            assert set(segment.text.split()) <= words_in_slices


def test_build_source_lines_rejects_a_desynchronised_offset_map() -> None:
    """A mismatch would make every downstream offset silently wrong."""
    cleaned, _ = clean_pages([(1, "line one\nline two")], NO_CLEANING)
    cleaned[0].line_offset_map = [0]  # now shorter than cleaned_text
    with pytest.raises(SegmentProvenanceError, match="cannot be trusted"):
        build_source_lines(cleaned)


def test_offsets_point_past_removed_header_lines() -> None:
    """The point of the offset map: cleaning shifts lines, offsets must not drift."""
    pages = [
        (number, f"RUNNING HEADER\n{number}\n\nBody of page {number} is unique.")
        for number in range(1, 9)
    ]
    cleaned, _ = clean_pages(pages)
    source_lines = build_source_lines(cleaned)
    assert source_lines
    for line in source_lines:
        if not line.stripped:
            continue
        original = dict(pages)[line.page_number]
        assert original[line.char_start : line.char_end] == line.text


# --------------------------------------------------------------------------- #
# Clause marker detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line,expected_style,expected_raw",
    [
        ("2. Accordingly, in exercise of the powers", "numeric_dotted", "2"),
        ("3.2 The District Level Committees shall", "numeric_dotted", "3.2"),
        ("3.2.1 Sub-sub clause text follows here", "numeric_dotted", "3.2.1"),
        ("(a) the bank shall maintain", "paren_alpha", "(a)"),
        ("(A) Capital requirements", "paren_alpha", "(A)"),
        ("(iv) SFT exposures; and", "paren_roman", "(iv)"),
        ("(vii) other adjustments", "paren_roman", "(vii)"),
        ("(12) sub-item twelve", "paren_number", "(12)"),
        ("CHAPTER IV", "structural", "CHAPTER IV"),
        ("PART A - Preliminary", "structural", "PART A"),
        ("Annexure", "annexure", "Annexure"),
        ("Schedule I - Reporting format", "annexure", "Schedule I"),
        ("Appendix", "annexure", "Appendix"),
    ],
)
def test_each_numbering_style_is_detected(
    line: str, expected_style: str, expected_raw: str
) -> None:
    marker = detect_clause_marker(line)
    assert marker is not None, line
    assert marker.style == expected_style
    assert marker.raw == expected_raw


@pytest.mark.parametrize(
    "line,expected_raw",
    [
        ("Clause 5 shall be omitted", "Clause 5"),
        ("Section 7 is hereby amended", "Section 7"),
        ("Paragraph 29 shall be substituted", "Paragraph 29"),
        ("Regulation 12 applies", "Regulation 12"),
    ],
)
def test_spelled_out_markers_are_detected(line: str, expected_raw: str) -> None:
    """Required by the brief, and reported as **absent** from this corpus.

    In all four RBI documents these words occur only mid-sentence ("conferred by
    Section 35A"), never opening a line. The implementation exists so an
    unseen document is handled; Experiment B reports the zero occurrence count
    rather than implying the feature was exercised.
    """
    marker = detect_clause_marker(line)
    assert marker is not None
    assert marker.style == "spelled"
    assert marker.raw == expected_raw


def test_a_mid_sentence_section_reference_is_not_a_marker() -> None:
    assert detect_clause_marker("conferred by Section 35A of the Act") is None
    assert detect_clause_marker("as described in paragraph 266 below") is None


@pytest.mark.parametrize("line", ["(STs) The Reserve Bank has issued", "(SCs) and others"])
def test_a_bracketed_abbreviation_is_not_a_clause_marker(line: str) -> None:
    """Regression test for a bug found by reading the real output.

    The master-circular's line "...credit facilities to Scheduled Castes (SCs) &
    Scheduled Tribes" wraps so that "(STs)" begins the next line. An earlier
    version classified any multi-character bracketed token as a list letter and
    opened a clause numbered "(STs)".
    """
    assert detect_clause_marker(line) is None


def test_a_percentage_table_row_is_not_a_numeric_clause() -> None:
    """Regression test, also from the real corpus.

    "5.5%-6.375% 3.5%-3.625% 100%" is a row of the capital-adequacy document's
    flattened table and matched an earlier version of the dotted-numeric
    pattern. A clause number must be followed by prose, not by a percent sign.
    """
    assert detect_clause_marker("5.5%–6.375% 3.5%–3.625% 100%") is None
    assert detect_clause_marker("12.5 14.0 16.5") is None


def test_a_schedule_cross_reference_in_a_table_cell_is_not_a_heading() -> None:
    """"Schedule 13, 14" is a cross-reference inside a flattened table."""
    assert detect_clause_marker("Schedule 13, 14") is None
    assert detect_clause_marker("Schedule 16") is None
    # A real schedule heading, with a title, still works.
    assert detect_clause_marker("Schedule II - Form of return") is not None


def test_ambiguous_bracketed_roman_is_flagged_not_silently_resolved() -> None:
    """``(i)`` is both roman one and letter nine; the ambiguity must be visible."""
    marker = detect_clause_marker("(i) the first item")
    assert marker is not None
    assert marker.ambiguous is True
    assert marker.style == "paren_roman"


def test_an_unambiguous_bracketed_roman_is_not_flagged() -> None:
    marker = detect_clause_marker("(ii) the second item")
    assert marker is not None
    assert marker.ambiguous is False


def test_bracketed_i_following_h_is_read_as_alphabetic() -> None:
    """Sequence context resolves the ambiguity where it can."""
    previous = [
        detect_clause_marker("(g) seventh item"),
        detect_clause_marker("(h) eighth item"),
    ]
    marker = detect_clause_marker("(i) ninth item", [m for m in previous if m])
    assert marker is not None
    assert marker.style == "paren_alpha"
    assert marker.ambiguous is False


def test_bracketed_v_following_iv_is_read_as_roman() -> None:
    previous = [detect_clause_marker("(iv) fourth item")]
    marker = detect_clause_marker("(v) fifth item", [m for m in previous if m])
    assert marker is not None
    assert marker.style == "paren_roman"
    assert marker.ambiguous is False


def test_an_unrecognised_numbering_style_fails_gracefully() -> None:
    """A style the corpus does not contain must return ``None``, not crash.

    Required by the roadmap's test list. The text is preserved as an
    unclassified segment by the caller; nothing is discarded and no clause
    number is invented.
    """
    for line in [
        "२. A Devanagari numeral opens this line",
        "§ 14 A section-sign reference",
        "Art. 21 of the Constitution",
        "<1> an angle-bracketed marker",
        "FIRST. A spelled-out ordinal",
    ]:
        assert detect_clause_marker(line) is None

    result = segment_clauses(one_page("§ 14 A section-sign reference"), DOC, VER)
    assert len(result.segments) == 1
    assert result.segments[0].clause_number is None
    assert result.segments[0].text == "§ 14 A section-sign reference"
    assert any("no clause marker" in warning for warning in result.warnings)


def test_detect_clause_marker_is_safe_on_blank_and_odd_input() -> None:
    for line in ["", "   ", "\n", "(", "()", "(((", "..."]:
        assert detect_clause_marker(line) is None


# --------------------------------------------------------------------------- #
# Paragraph segmentation (the baseline)
# --------------------------------------------------------------------------- #


def test_blank_lines_split_paragraphs() -> None:
    result = segment_paragraphs(
        one_page("First paragraph here.\n\nSecond paragraph here.\n\nThird."),
        DOC,
        VER,
    )
    assert [segment.text for segment in result.segments] == [
        "First paragraph here.",
        "Second paragraph here.",
        "Third.",
    ]
    assert result.stats["blank_line_splits"] == 2


def test_lines_within_a_paragraph_are_joined_with_spaces() -> None:
    """PDF line breaks are layout artefacts, not meaning."""
    result = segment_paragraphs(
        one_page("A clause that wraps\nacross three\nseparate lines."), DOC, VER
    )
    assert len(result.segments) == 1
    assert result.segments[0].text == "A clause that wraps across three separate lines."


def test_an_incomplete_fragment_is_joined_across_a_page_break() -> None:
    """Rule 2: both sides must agree before a join happens."""
    pages = [
        (1, "The bank shall maintain a minimum ratio of"),
        (2, "four per cent at all times."),
    ]
    result = segment_paragraphs(lines_from(pages), DOC, VER)
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.page_start == 1
    assert segment.page_end == 2
    assert segment.spans_pages
    assert len(segment.page_offsets) == 2
    assert [offset.page_number for offset in segment.page_offsets] == [1, 2]
    assert result.stats["cross_page_joins"] == 1


def test_a_completed_sentence_is_not_joined_across_a_page_break() -> None:
    pages = [
        (1, "The bank shall maintain a minimum ratio of four per cent."),
        (2, "A separate obligation begins on this page."),
    ]
    result = segment_paragraphs(lines_from(pages), DOC, VER)
    assert len(result.segments) == 2
    assert result.stats["cross_page_joins"] == 0
    assert result.stats["page_boundary_splits"] == 1


def test_an_incomplete_fragment_is_not_joined_to_a_new_clause() -> None:
    """Checking both sides is what stops the rule swallowing a clause marker."""
    pages = [
        (1, "The bank shall maintain a minimum ratio of"),
        (2, "3. A new numbered clause starts here."),
    ]
    result = segment_paragraphs(lines_from(pages), DOC, VER)
    assert len(result.segments) == 2
    assert result.stats["cross_page_joins"] == 0


def test_blank_lines_at_a_page_edge_do_not_prevent_a_join() -> None:
    """The bug this rewrite fixed.

    Page text begins and ends with newlines, and header removal leaves them
    behind, so the first line of a cleaned page is blank. Applying the
    blank-line rule first made ``cross_page_joins`` structurally zero: no
    paragraph could ever span a page.
    """
    pages = [
        (1, "\nThe bank shall maintain a minimum ratio of\n"),
        (2, "\nfour per cent at all times.\n"),
    ]
    result = segment_paragraphs(lines_from(pages), DOC, VER)
    assert result.stats["cross_page_joins"] == 1
    assert len(result.segments) == 1
    assert result.segments[0].spans_pages


def test_the_baseline_detects_no_clause_numbers_at_all() -> None:
    """A baseline that quietly did clause detection would void Experiment B."""
    result = segment_paragraphs(
        one_page("3.2 A numbered clause.\n\n(a) A lettered sub-clause."), DOC, VER
    )
    assert all(segment.clause_number is None for segment in result.segments)
    assert all(segment.heading is None for segment in result.segments)
    assert all(segment.segment_type == "paragraph" for segment in result.segments)
    assert all(segment.level == 0 for segment in result.segments)
    assert all(segment.parent_segment_id is None for segment in result.segments)


# --------------------------------------------------------------------------- #
# Clause-aware segmentation
# --------------------------------------------------------------------------- #


CLAUSE_DOC = """1. Planning Process

1.1 The District Level Committees shall meet quarterly.

1.2 Banks shall establish closer liaison with the Centres.

2. Credit Facilities

2.1 The following shall apply:

(a) advances to the target group;

(b) advances for housing; and

(i) in the case of a shortfall,
the bank shall report the position.

3. Reporting

3.1 This clause has an internal blank line.

Still part of clause 3.1, after the blank line above.
"""


def test_clause_segmentation_builds_a_hierarchy() -> None:
    result = segment_clauses(one_page(CLAUSE_DOC), DOC, VER)
    by_number = {segment.clause_number: segment for segment in result.segments}

    assert by_number["1"].level == 1
    assert by_number["1.1"].level == 2
    assert by_number["1.1"].parent_segment_id == by_number["1"].segment_id
    assert by_number["1.2"].parent_segment_id == by_number["1"].segment_id
    assert by_number["2.1"].parent_segment_id == by_number["2"].segment_id
    # A bracketed letter nests one level below the enclosing numeric clause.
    assert by_number["(a)"].level == 3
    assert by_number["(a)"].parent_segment_id == by_number["2.1"].segment_id


def test_a_parent_id_always_names_a_segment_that_exists() -> None:
    """A dangling parent pointer would break Person 2's hierarchy display."""
    result = segment_clauses(one_page(CLAUSE_DOC), DOC, VER)
    known = {segment.segment_id for segment in result.segments}
    for segment in result.segments:
        if segment.parent_segment_id is not None:
            assert segment.parent_segment_id in known


def test_a_parent_always_has_a_shallower_level_than_its_child() -> None:
    result = segment_clauses(one_page(CLAUSE_DOC), DOC, VER)
    by_id = {segment.segment_id: segment for segment in result.segments}
    for segment in result.segments:
        if segment.parent_segment_id:
            assert by_id[segment.parent_segment_id].level < segment.level


def test_a_blank_line_does_not_end_a_clause() -> None:
    """Rule 4. Numbered clauses contain blank lines between their paragraphs.

    Splitting on them would reduce the clause method to the baseline, which the
    segment-count test below guards against from the other direction.
    """
    text = "3. The first paragraph of clause three.\n\nStill clause three here.\n\n4. Clause four."
    result = segment_clauses(one_page(text), DOC, VER)
    assert len(result.segments) == 2
    assert "Still clause three here." in result.segments[0].text


def test_a_clause_continues_across_a_page_break() -> None:
    """A clause that runs onto the next page is one clause with two page offsets."""
    pages = [
        (1, "4. The Amendment Directions modify the Directions as follows, namely"),
        (2, "that paragraph 29 shall be substituted.\n\n5. This comes into force."),
    ]
    result = segment_clauses(lines_from(pages), DOC, VER)
    spanning = [segment for segment in result.segments if segment.spans_pages]
    assert len(spanning) == 1
    clause = spanning[0]
    assert clause.clause_number == "4"
    assert clause.page_start == 1
    assert clause.page_end == 2
    assert len(clause.page_offsets) == 2
    assert [offset.page_number for offset in clause.page_offsets] == [1, 2]
    assert result.stats["page_spanning_segments"] == 1


def test_text_before_the_first_marker_becomes_preamble() -> None:
    text = "RBI/2026-27/300\nOctober 26, 2026\nMadam/ Dear Sir,\n\n1. First clause."
    result = segment_clauses(one_page(text), DOC, VER)
    assert result.segments[0].segment_type == TYPE_PREAMBLE
    assert result.segments[0].clause_number is None
    assert "Madam/ Dear Sir," in result.segments[0].text


def test_unattributable_text_after_a_clause_is_labelled_unclassified() -> None:
    """Not discarded, and not given an invented clause number."""
    text = (
        "1. The first clause.\n\n"
        "Annexure - Reporting format\n\n"
        "२३ some text with a numbering style we do not handle"
    )
    result = segment_clauses(one_page(text), DOC, VER)
    types = [segment.segment_type for segment in result.segments]
    assert TYPE_ANNEXURE in types
    assert all(
        segment.clause_number is not None or segment.segment_type in
        {TYPE_PREAMBLE, TYPE_UNCLASSIFIED, "heading"}
        for segment in result.segments
    )
    # Nothing is lost: every input word appears somewhere in the output.
    joined = " ".join(segment.text for segment in result.segments)
    assert "numbering style we do not handle" in joined


def test_no_input_text_is_discarded_by_either_method() -> None:
    """The brief's rule: text that cannot be classified is preserved."""
    text = (
        "Unnumbered opening remarks.\n\n"
        "1. A numbered clause.\n\n"
        "Some trailing text with no marker at all.\n\n"
        "<9> an unsupported marker style\n"
    )
    expected = set(text.split())
    for result in (
        segment_paragraphs(one_page(text), DOC, VER),
        segment_clauses(one_page(text), DOC, VER),
    ):
        produced = set(" ".join(s.text for s in result.segments).split())
        assert expected <= produced


def test_the_two_methods_produce_different_segment_counts() -> None:
    """Guards against the clause method silently falling back to the baseline.

    The fixture ends with a clause containing an internal blank line, which is
    what makes the counts diverge: the paragraph method splits there, the clause
    method does not. Without such a clause the two methods coincidentally agree
    on a fixture where every clause is one paragraph — which an earlier version
    of this test did, making it pass for the wrong reason.
    """
    lines = one_page(CLAUSE_DOC)
    paragraph = segment_paragraphs(lines, DOC, VER)
    clause = segment_clauses(lines, DOC, VER)
    assert len(clause.segments) != len(paragraph.segments)
    assert any(segment.clause_number for segment in clause.segments)
    assert not any(segment.clause_number for segment in paragraph.segments)


def test_marker_style_counts_are_reported() -> None:
    result = segment_clauses(one_page(CLAUSE_DOC), DOC, VER)
    styles = result.stats["marker_styles"]
    assert styles["numeric_dotted"] >= 5
    assert styles["paren_alpha"] >= 2


def test_ambiguous_markers_are_counted_and_recorded_on_the_segment() -> None:
    result = segment_clauses(one_page("(i) the only item here."), DOC, VER)
    assert result.stats["ambiguous_markers"] == 1
    assert result.segments[0].metadata["clause_number_ambiguous"] is True
    assert any("ambiguous" in warning for warning in result.warnings)


def test_an_unnumbered_heading_attaches_to_the_following_clause() -> None:
    text = "REPORTING REQUIREMENTS\n\n3. Banks shall report quarterly."
    result = segment_clauses(one_page(text), DOC, VER)
    clause = next(s for s in result.segments if s.clause_number == "3")
    assert clause.heading == "REPORTING REQUIREMENTS"


def test_a_reference_number_is_not_mistaken_for_a_heading() -> None:
    """"RBI/2026-27/300" has the ALL-CAPS shape but is not a section heading."""
    result = segment_clauses(
        one_page("RBI/2026-27/300\nSome following text.\n\n1. A clause."), DOC, VER
    )
    assert result.segments[0].heading is None
    assert result.segments[0].segment_type == TYPE_PREAMBLE


# --------------------------------------------------------------------------- #
# Contract and determinism
# --------------------------------------------------------------------------- #


def test_segment_ids_are_stable_across_runs() -> None:
    """Re-segmenting must not invalidate every annotation and citation."""
    lines = one_page(CLAUSE_DOC)
    first = segment_clauses(lines, DOC, VER)
    second = segment_clauses(lines, DOC, VER)
    assert [s.segment_id for s in first.segments] == [
        s.segment_id for s in second.segments
    ]


def test_segment_ids_differ_between_the_two_methods() -> None:
    """Paragraph 3 and clause 3 are not the same segment."""
    lines = one_page(CLAUSE_DOC)
    paragraph_ids = {s.segment_id for s in segment_paragraphs(lines, DOC, VER).segments}
    clause_ids = {s.segment_id for s in segment_clauses(lines, DOC, VER).segments}
    assert not (paragraph_ids & clause_ids)


def test_segment_ids_are_unique_within_a_result() -> None:
    for result in (
        segment_paragraphs(one_page(CLAUSE_DOC), DOC, VER),
        segment_clauses(one_page(CLAUSE_DOC), DOC, VER),
    ):
        ids = [segment.segment_id for segment in result.segments]
        assert len(ids) == len(set(ids))


def test_ordinals_are_dense_and_in_document_order() -> None:
    for result in (
        segment_paragraphs(one_page(CLAUSE_DOC), DOC, VER),
        segment_clauses(one_page(CLAUSE_DOC), DOC, VER),
    ):
        assert [s.ordinal for s in result.segments] == list(
            range(len(result.segments))
        )


def test_the_json_contract_has_every_documented_field() -> None:
    """The shape Persons 2-5 consume. A missing field breaks four modules."""
    result = segment_clauses(one_page(CLAUSE_DOC), DOC, VER)
    payload = result.to_dict()
    assert payload["segmentation_method"] == METHOD_CLAUSE_AWARE
    assert payload["offset_basis"] == OFFSET_BASIS_NORMALIZED
    assert payload["segment_count"] == len(result.segments)

    required = {
        "segment_id", "document_id", "version_id", "segmentation_method",
        "ordinal", "segment_type", "clause_number", "heading", "text",
        "page_start", "page_end", "page_offsets", "parent_segment_id",
        "level", "metadata",
    }
    for segment in payload["segments"]:
        assert required <= set(segment)
        for offset in segment["page_offsets"]:
            assert {
                "page_number", "char_start", "char_end",
                "line_index_start", "line_index_end",
            } <= set(offset)
    # The whole payload must serialise - it is written to disk.
    assert json.loads(json.dumps(payload))


def test_segmenting_nothing_returns_an_empty_result_rather_than_failing() -> None:
    for result in (
        segment_paragraphs([], DOC, VER),
        segment_clauses([], DOC, VER),
    ):
        assert result.segments == []
        assert result.stats["segment_count"] == 0
        assert json.loads(json.dumps(result.to_dict()))
