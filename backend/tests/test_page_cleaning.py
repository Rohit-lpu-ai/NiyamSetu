"""Header, footer and page-number removal: detection, safety and reversibility.

The single most important test in this module is
:func:`test_removal_is_always_reversible` and its variants. Header removal is
the only stage in the pipeline that *deletes* text, so "we can always put it
back" is the property that keeps it from violating the project's rule against
losing source evidence (``CLAUDE.md`` §24 rule 6).

Fixtures here are synthetic page text, not PDFs: cleaning operates on strings,
so building a PDF to test it would only add a slow dependency on extraction.
The real-document behaviour is measured separately and reported in
``evaluation/experiment-b-segmentation.md``.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from backend.app.document_processing.page_cleaning import (
    REASON_PAGE_NUMBER,
    REASON_REPEATED_FOOTER,
    REASON_REPEATED_HEADER,
    CleaningOptions,
    clean_pages,
    detect_running_lines,
)


def make_pages(
    body_by_page: List[str],
    header: str | None = None,
    footer: str | None = None,
    page_numbers: bool = False,
) -> List[Tuple[int, str]]:
    """Build ``[(page_number, text), ...]`` with optional running furniture."""
    pages = []
    for index, body in enumerate(body_by_page, start=1):
        lines: List[str] = []
        if header:
            lines.append(header)
        if page_numbers:
            lines.append(str(index))
        lines.append("")
        lines.extend(body.split("\n"))
        if footer:
            lines.append("")
            lines.append(footer)
        pages.append((index, "\n".join(lines)))
    return pages


# --------------------------------------------------------------------------- #
# Reversibility - the non-negotiable property
# --------------------------------------------------------------------------- #


def test_removal_is_always_reversible() -> None:
    """Every cleaned page must reconstruct its input exactly.

    If this fails, header removal is destroying source evidence, which is worse
    than leaving every header in place.
    """
    pages = make_pages(
        [f"Body of page {n}. It says something different each time." for n in range(1, 11)],
        header="Reserve Bank of India - Some Directions, 2026",
        page_numbers=True,
    )
    cleaned, _ = clean_pages(pages)
    assert len(cleaned) == 10
    for page, (_, original) in zip(cleaned, pages):
        assert page.restore() == original
        assert page.source_text == original


def test_reversibility_holds_when_nothing_is_removed() -> None:
    pages = make_pages(["Unique body one.", "Unique body two."])
    cleaned, _ = clean_pages(pages)
    for page in cleaned:
        assert page.removed_line_count == 0
        assert page.cleaned_text == page.source_text
        assert page.restore() == page.source_text


def test_source_text_is_never_mutated() -> None:
    pages = make_pages(
        [f"Body {n}" for n in range(1, 6)],
        header="RUNNING HEADER LINE",
    )
    originals = [text for _, text in pages]
    cleaned, _ = clean_pages(pages)
    assert [page.source_text for page in cleaned] == originals


def test_line_offset_map_aligns_with_cleaned_text() -> None:
    """The map is what lets segments report offsets into the uncleaned text.

    A length mismatch would make every downstream offset silently wrong, so the
    alignment is asserted directly rather than relied upon.
    """
    pages = make_pages(
        [f"Body of page {n}." for n in range(1, 8)],
        header="A RUNNING HEADER",
        page_numbers=True,
    )
    cleaned, _ = clean_pages(pages)
    for page in cleaned:
        kept = page.cleaned_text.split("\n")
        assert len(kept) == len(page.line_offset_map)
        original_lines = page.source_text.split("\n")
        for text, original_index in zip(kept, page.line_offset_map):
            assert original_lines[original_index] == text


# --------------------------------------------------------------------------- #
# Running header detection
# --------------------------------------------------------------------------- #


def test_running_header_on_nearly_every_page_is_removed() -> None:
    """The capital-adequacy case: a header on 36 of 37 pages (97%)."""
    bodies = [f"Clause text for page {n}, which differs per page." for n in range(1, 38)]
    pages = make_pages(bodies, header="Reserve Bank of India - Prudential Norms")
    # Drop the header from one page, to mirror the real document.
    pages[0] = (1, pages[0][1].replace("Reserve Bank of India - Prudential Norms\n", ""))

    cleaned, plan = clean_pages(pages)
    removed_header_pages = [
        page.page_number
        for page in cleaned
        if any(line.reason == REASON_REPEATED_HEADER for line in page.removed_lines)
    ]
    assert len(removed_header_pages) == 36
    assert 1 not in removed_header_pages
    assert any(
        candidate["removed"] and candidate["pages"] == 36
        for candidate in plan.header_candidates
    )


def test_repeated_table_column_headers_are_not_removed() -> None:
    """The commercial-banks case, and the reason the threshold is 0.90.

    A seven-page table repeats its column headings on 7 of 10 pages (70%).
    Those headings are clause content. At the 0.60 threshold CLAUDE.md §13.1
    originally proposed, they would be deleted.
    """
    bodies = []
    for n in range(1, 11):
        if n <= 7:
            bodies.append(f"BI\nconstituents\nRow data for page {n}")
        else:
            bodies.append(f"Ordinary prose on page {n} with no table at all.")
    pages = make_pages(bodies)

    cleaned, plan = clean_pages(pages)
    assert all(
        not any(line.reason == REASON_REPEATED_HEADER for line in page.removed_lines)
        for page in cleaned
    )
    # Detection still *reports* the 70% candidate, so the near-miss is visible.
    assert any(
        candidate["pages"] == 7 and not candidate["removed"]
        for candidate in plan.header_candidates
    )
    # And at the old threshold it would indeed have been removed - the
    # regression this test exists to prevent.
    _, loose_plan = clean_pages(pages, CleaningOptions(frequency_threshold=0.60))
    assert any(candidate["removed"] for candidate in loose_plan.header_candidates)


def test_header_block_detection_stops_at_the_first_non_repeating_line() -> None:
    """Contiguity: a repeated phrase deeper in the body is not part of the header.

    The repeated phrase sits in the **middle** of the body on purpose. An
    earlier version of this test put it on the last line, where it was removed —
    correctly — as a running *footer*, which is not what this test is about.
    That behaviour has its own test at the bottom of this module.
    """
    bodies = [
        f"Unique opening sentence for page {n}.\n"
        f"A PHRASE THAT REPEATS EVERYWHERE\n"
        f"A different closing sentence for page {n}."
        for n in range(1, 9)
    ]
    pages = make_pages(bodies, header="THE REAL RUNNING HEADER")
    cleaned, _ = clean_pages(pages)
    for page in cleaned:
        removed = [line.text for line in page.removed_lines]
        assert "THE REAL RUNNING HEADER" in removed
        assert "A PHRASE THAT REPEATS EVERYWHERE" not in removed
        assert "A PHRASE THAT REPEATS EVERYWHERE" in page.cleaned_text


def test_repeated_footer_is_removed_and_labelled_as_a_footer() -> None:
    bodies = [f"Body text that differs on page {n}." for n in range(1, 9)]
    pages = make_pages(bodies, footer="Confidential - internal circulation only")
    cleaned, _ = clean_pages(pages)
    reasons = {
        line.reason for page in cleaned for line in page.removed_lines
    }
    assert REASON_REPEATED_FOOTER in reasons
    assert all(
        "Confidential" not in page.cleaned_text for page in cleaned
    )


def test_a_single_page_document_has_no_running_anything() -> None:
    pages = make_pages(["The only page of this document."])
    cleaned, plan = clean_pages(pages)
    assert cleaned[0].removed_line_count == 0
    assert any("page(s)" in warning for warning in plan.warnings)


def test_two_page_document_is_cleaned_but_flagged_low_confidence() -> None:
    """The lead-bank case: real letterhead, but 2 pages makes 100% meaningless.

    Cleaning still runs, because the RBI letterhead is genuinely furniture, but
    the plan must say the evidence is weak so nobody reads it as confident.
    """
    pages = make_pages(
        ["First page body.", "Second page body."],
        header="RESERVE BANK OF INDIA LETTERHEAD",
    )
    cleaned, plan = clean_pages(pages)
    assert sum(page.removed_line_count for page in cleaned) == 2
    assert any("confidence floor" in warning for warning in plan.warnings)


def test_detection_can_be_switched_off_per_rule() -> None:
    pages = make_pages(
        [f"Body {n}" for n in range(1, 9)],
        header="A RUNNING HEADER",
        page_numbers=True,
    )
    cleaned, _ = clean_pages(
        pages, CleaningOptions(remove_repeated_headers=False)
    )
    reasons = {line.reason for page in cleaned for line in page.removed_lines}
    assert REASON_REPEATED_HEADER not in reasons
    assert REASON_PAGE_NUMBER in reasons

    cleaned, _ = clean_pages(
        pages,
        CleaningOptions(
            remove_repeated_headers=False,
            remove_repeated_footers=False,
            remove_page_numbers=False,
        ),
    )
    assert all(page.removed_line_count == 0 for page in cleaned)


# --------------------------------------------------------------------------- #
# Page numbers
# --------------------------------------------------------------------------- #


def test_printed_page_numbers_matching_physical_pages_are_removed() -> None:
    """The master-circular case: no repeated header, but a number on every page.

    This is the document the Phase 1 report flagged as un-cleanable by a purely
    frequency-based rule. The page-number rule is what handles it.
    """
    pages = make_pages([f"Body {n}" for n in range(1, 9)], page_numbers=True)
    cleaned, _ = clean_pages(pages)
    for page in cleaned:
        assert page.removed_line_count == 1
        removed = page.removed_lines[0]
        assert removed.reason == REASON_PAGE_NUMBER
        assert removed.text.strip() == str(page.page_number)
        assert "matches the physical page number" in removed.evidence


@pytest.mark.parametrize(
    "rendered",
    ["7", "- 7 -", "Page 7", "Page 7 of 9", "7/9", "7 of 9"],
)
def test_page_number_forms_are_all_recognised(rendered: str) -> None:
    pages = []
    for number in range(1, 10):
        printed = rendered.replace("7", str(number))
        pages.append((number, f"{printed}\n\nBody text of page {number}."))
    cleaned, _ = clean_pages(pages)
    assert all(page.removed_line_count == 1 for page in cleaned)


def test_an_offset_page_number_sequence_is_recognised() -> None:
    """Printed numbering need not match physical numbering.

    A cover page or an annexure that restarts means the printed number can be
    offset. The sequence rule catches that; requiring the numbers to increase is
    what stops a column of figures being read as page numbering.
    """
    pages = [
        (number, f"{number + 4}\n\nBody of physical page {number}.")
        for number in range(1, 11)
    ]
    cleaned, _ = clean_pages(pages)
    assert all(page.removed_line_count == 1 for page in cleaned)
    assert all(
        "increasing sequence" in page.removed_lines[0].evidence for page in cleaned
    )


def test_a_line_with_a_number_and_words_is_not_a_page_number() -> None:
    """A numbered clause must never be mistaken for printed page numbering.

    The assertion is specifically about the *page-number* rule. The line here is
    identical on all eight pages, so the repeated-header rule may well remove it
    — a separate and defensible decision — and asserting that the line survives
    would be testing two rules at once.
    """
    pages = [
        (number, f"3. Clause three applies.\n\nBody of page {number}.")
        for number in range(1, 9)
    ]
    _, plan = clean_pages(pages)
    page_number_reasons = [
        reason
        for reasons in plan.reasons.values()
        for reason, _ in reasons.values()
        if reason == REASON_PAGE_NUMBER
    ]
    assert page_number_reasons == []


def test_non_increasing_numbers_are_not_treated_as_page_numbering() -> None:
    """A column of figures must not be mistaken for a numbering sequence."""
    pages = [
        (number, f"{100 - number * 7}\n\nBody of page {number}.")
        for number in range(1, 11)
    ]
    cleaned, _ = clean_pages(pages)
    assert all(page.removed_line_count == 0 for page in cleaned)


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #


def test_a_page_is_left_alone_when_detection_wants_too_much_of_it() -> None:
    """The safety valve. A page that is mostly "header" is a detection failure.

    Eight pages that are almost entirely identical would otherwise be stripped
    to nothing. Leaving them intact and recording why is the conservative
    choice: noise in a segment is recoverable, deleted policy text is not.
    """
    identical = "\n".join(f"IDENTICAL LINE {n}" for n in range(10))
    pages = [
        (number, f"{identical}\nUnique tail for page {number}.")
        for number in range(1, 9)
    ]
    cleaned, plan = clean_pages(pages)
    assert all(page.removed_line_count == 0 for page in cleaned)
    assert any("safety limit" in warning for warning in plan.warnings)


def test_every_removed_line_carries_a_reason_and_evidence() -> None:
    """A line removed without a stated reason is indistinguishable from a bug."""
    # Bodies of several lines each, so the three removals stay well under the
    # 50% safety limit. A one-line body would make header+number+footer 75% of
    # the page, and the safety valve would - correctly - remove nothing.
    pages = make_pages(
        [
            "\n".join(f"Body line {line} of page {n}." for line in range(1, 7))
            for n in range(1, 12)
        ],
        header="A RUNNING HEADER",
        footer="A RUNNING FOOTER",
        page_numbers=True,
    )
    cleaned, _ = clean_pages(pages)
    removed = [line for page in cleaned for line in page.removed_lines]
    assert removed
    for line in removed:
        assert line.reason in {
            REASON_REPEATED_HEADER,
            REASON_REPEATED_FOOTER,
            REASON_PAGE_NUMBER,
        }
        assert line.evidence
        assert line.page_number >= 1
        assert line.line_index >= 0


def test_detect_running_lines_changes_no_text() -> None:
    """Detection and application are separate so a plan can be inspected first."""
    pages = make_pages([f"Body {n}" for n in range(1, 9)], header="HEADER LINE")
    plan = detect_running_lines(pages)
    assert plan.marked
    assert plan.page_count == 8
    # Nothing was cleaned - the plan is a decision, not an edit.
    assert all("HEADER LINE" in text for _, text in pages)


def test_a_supplied_plan_is_used_rather_than_recomputed() -> None:
    """Lets a caller review or override detection before it is applied."""
    pages = make_pages([f"Body {n}" for n in range(1, 9)], header="HEADER LINE")
    plan = detect_running_lines(pages)
    plan.marked.pop(3, None)  # spare page 3 by hand
    cleaned, _ = clean_pages(pages, plan=plan)
    by_page = {page.page_number: page for page in cleaned}
    assert by_page[3].removed_line_count == 0
    assert by_page[4].removed_line_count == 1


def test_empty_page_list_is_handled() -> None:
    cleaned, plan = clean_pages([])
    assert cleaned == []
    assert plan.page_count == 0


def test_plan_serialises_to_json_safe_primitives() -> None:
    import json

    pages = make_pages([f"Body {n}" for n in range(1, 9)], header="HEADER LINE")
    cleaned, plan = clean_pages(pages)
    assert json.loads(json.dumps(plan.to_dict()))
    assert json.loads(json.dumps([page.to_dict() for page in cleaned]))


def test_a_phrase_on_the_last_line_of_every_page_is_treated_as_a_footer() -> None:
    """Recorded because it surprised me while writing these tests.

    A line that repeats on every page *and* sits at the end of the page is a
    running footer by any reasonable definition, even if its wording looks like
    body text. There is no information available to the cleaner that would
    distinguish the two, so this documents the behaviour rather than asserting a
    preference - and it is listed as a failure mode in
    ``evaluation/experiment-b-segmentation.md``.
    """
    bodies = [
        f"Unique body for page {n}.\n"
        f"More unique body for page {n}.\n"
        f"This sentence is identical on every page."
        for n in range(1, 9)
    ]
    pages = make_pages(bodies)
    cleaned, _ = clean_pages(pages)
    reasons = {line.reason for page in cleaned for line in page.removed_lines}
    assert reasons == {REASON_REPEATED_FOOTER}
