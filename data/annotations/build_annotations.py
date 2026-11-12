"""Build the Experiment B annotation files from hand-decided clause boundaries.

The *annotation* is the boundary decisions below: which cleaned-text line each
clause starts and ends on, its printed clause number, its heading, and whether
the case is uncertain. Those were decided by reading the extracted text of each
page against data/annotations/README.md.

The marker strings are derived mechanically from those decisions, and verified
unique against the page's normalized_text, because hand-typing 40-character
verbatim snippets is the single most error-prone part of annotating and the
guideline (§3.2) requires them to be exact.
"""
import json, pathlib, sys
sys.path.insert(0, ".")
from backend.app.document_processing.page_cleaning import clean_pages

ANNOTATOR = "person1-claude-code"
GUIDELINE = "1.0"
REVIEW = "awaiting_owner_review"

# (page, first_line, last_line, clause_number, heading, uncertain, note)
PLANS = {
"VER-b8a13c2a7651eb84": [
 (2,  3,  7, None,
  "Master Circular - Credit Facilities to Scheduled Castes (SCs) & Scheduled Tribes (STs)",
  False, "document title heading on page 2 with its own body text (guideline 4.6)"),
 (2,  9,  9, "1", None, False, "numbered heading clause; span ends where 1.1 begins (guideline 4.2)"),
 (2, 11, 15, "1.1", None, False, None),
 (2, 17, 18, "1.2", None, False, None),
 (2, 20, 25, "1.3", None, False, None),
 (2, 27, 29, "1.4", None, False, None),
 (2, 31, 33, "1.5", None, False, None),
 (3,  3,  3, "2", None, False, "numbered heading clause; span ends where 2.1 begins"),
 (3,  5,  7, "2.1", None, False, None),
 (3,  9, 15, "2.2", None, False, None),
 (3, 17, 18, "2.3", None, False, None),
 (3, 20, 25, "2.4", None, False, None),
 (3, 27, 32, "2.5", None, False, None),
 (3, 34, 37, "2.6.", None, False,
  "printed with a trailing stop as '2.6.' unlike its siblings; recorded as printed (guideline 5.3)"),
],
"VER-6507f32084d73257": [
 (1,  2,  8, None, None, False, "preamble: reference number, date, addressee, salutation, subject (guideline 4.7)"),
 (1,  9, 13, None, None, False, "unnumbered opening recital stating the background (guideline 4.7)"),
 (1, 14, 29, "2", None, True,
  "clause plus the Sr. No table it introduces, annotated as one clause (guideline 4.5); "
  "table row structure is lost in extraction, and the row labels 1./2./3. collide with "
  "the document's own clause numbers"),
 (2,  2, 10, "3", None, True,
  "clause plus its table (guideline 4.5); row labels 1./2. collide with clause numbering"),
 (2, 12, 16, "4", None, True,
  "clause plus its table (guideline 4.5); row label 1 collides with clause numbering"),
 (2, 18, 19, "5", None, False, None),
 (2, 20, 22, None, None, False, "closing matter: signature block (guideline 4.8)"),
],
}

MIN_MARKER, MAX_MARKER = 24, 60


def unique_marker(line: str, page_text: str, from_end: bool) -> str:
    """Grow a snippet of `line` until it occurs exactly once in `page_text`."""
    line = line.strip()
    for length in range(MIN_MARKER, max(len(line), MIN_MARKER) + 1):
        snippet = line[-length:] if from_end else line[:length]
        if page_text.count(snippet) == 1:
            return snippet
    if page_text.count(line) == 1:
        return line
    raise SystemExit(f"cannot make a unique marker from {line[:60]!r}")


for version_id, plan in PLANS.items():
    data = json.loads(
        (pathlib.Path("data/processed") / version_id / "extraction.json").read_text(encoding="utf-8")
    )
    pages = [(p["page_number"], p["normalized_text"]) for p in data["pages"]]
    page_text = dict(pages)
    cleaned, _ = clean_pages(pages)
    cleaned_lines = {c.page_number: c.cleaned_text.splitlines() for c in cleaned}

    records = []
    for index, (page, first, last, number, heading, uncertain, note) in enumerate(plan):
        lines = cleaned_lines[page]
        start_marker = unique_marker(lines[first], page_text[page], from_end=False)
        end_marker = unique_marker(lines[last], page_text[page], from_end=True)
        records.append({
            "version_id": version_id,
            "guideline_version": GUIDELINE,
            "annotator": ANNOTATOR,
            "review_status": REVIEW,
            "clause_index": index,
            "page_start": page,
            "page_end": page,
            "clause_number": number,
            "heading": heading,
            "text_start_marker": start_marker,
            "text_end_marker": end_marker,
            "uncertain": uncertain,
            "note": note,
        })

    out_dir = pathlib.Path("data/annotations/segmentation")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{version_id}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    annotated_pages = sorted({r["page_start"] for r in records})
    (out_dir / f"{version_id}.meta.json").write_text(
        json.dumps({
            "version_id": version_id,
            "guideline_version": GUIDELINE,
            "annotator": ANNOTATOR,
            "review_status": REVIEW,
            "annotated_pages": annotated_pages,
            "clause_count": len(records),
            "uncertain_count": sum(1 for r in records if r["uncertain"]),
            "pages_reviewed_and_empty": [],
            "inter_annotator_agreement": "PENDING - no second annotator",
            "note": (
                "Boundaries decided by reading the extracted text of each listed page "
                "against data/annotations/README.md. Marker strings derived mechanically "
                "from those boundary decisions and verified unique within the page."
            ),
        }, indent=2), encoding="utf-8",
    )
    print(f"{version_id}: {len(records)} records, pages {annotated_pages}, "
          f"{sum(1 for r in records if r['uncertain'])} uncertain")
