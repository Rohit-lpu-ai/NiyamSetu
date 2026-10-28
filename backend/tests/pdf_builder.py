"""A minimal PDF writer, for tests only.

Why this exists instead of a dependency
---------------------------------------
Deterministic tests need PDFs whose exact text we control. The obvious way is
``reportlab``, but that is a sizeable dependency added purely for tests, and
``CLAUDE.md`` §24 rule 5 says to keep the stack small. A PDF carrying Helvetica
text is about forty lines of bytes to write by hand, so we write it.

What it produces
----------------
A PDF 1.4 file with one page object per requested page, a WinAnsi-encoded
base-14 Helvetica font, and a content stream per page. Text is encoded as
cp1252, which *is* WinAnsi, so the typographic punctuation real circulars use
(en-dashes, curly quotes) round-trips correctly. A page requested with no
lines gets an empty content stream, which is how we simulate the image-only /
scanned page case *without* claiming to have a real scanned document.

IMPORTANT
---------
These are **fixtures**, not documents. They are never part of the corpus and
must never be quoted as real government policy text (``CLAUDE.md`` §16).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

#: Characters that must be escaped inside a PDF literal string.
_ESCAPES = {"\\": r"\\", "(": r"\(", ")": r"\)"}


def _escape(text: str) -> str:
    for raw, replacement in _ESCAPES.items():
        text = text.replace(raw, replacement)
    return text


def _content_stream(lines: Sequence[str]) -> str:
    """Build a text-drawing content stream, or an empty one for a blank page."""
    if not lines:
        return ""
    parts = ["BT /F1 12 Tf 72 720 Td 16 TL\n"]
    parts.extend(f"({_escape(line)}) Tj T*\n" for line in lines)
    parts.append("ET\n")
    return "".join(parts)


def build_pdf_bytes(
    pages: Sequence[Sequence[str]],
    info: Optional[Dict[str, str]] = None,
) -> bytes:
    """Return the bytes of a valid PDF.

    Args:
        pages: One entry per page, each a sequence of text lines. An empty
            sequence produces a page with no text at all.
        info: Optional ``/Info`` dictionary entries, e.g.
            ``{"Title": "Fixture", "Author": "NiyamSetu tests"}``.

    Returns:
        The complete PDF file as bytes, with a correct cross-reference table.
    """
    if not pages:
        raise ValueError("a PDF needs at least one page")

    page_total = len(pages)
    # Object numbering: 1 catalog, 2 page tree, 3 font, 4..(3+n) page objects,
    # (4+n).. content streams, and optionally a trailing /Info object.
    first_page_obj = 4
    first_stream_obj = first_page_obj + page_total
    info_obj = first_stream_obj + page_total if info else None

    objects: Dict[int, Optional[str]] = {}
    streams: Dict[int, str] = {}

    kids = " ".join(f"{first_page_obj + i} 0 R" for i in range(page_total))
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Count {page_total} /Kids [{kids}] >>"
    objects[3] = (
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        "/Encoding /WinAnsiEncoding >>"
    )
    for index in range(page_total):
        objects[first_page_obj + index] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {first_stream_obj + index} 0 R >>"
        )
    for index, lines in enumerate(pages):
        number = first_stream_obj + index
        objects[number] = None  # marker: this object is a stream
        streams[number] = _content_stream(lines)
    if info_obj is not None and info:
        entries = " ".join(f"/{k} ({_escape(v)})" for k, v in info.items())
        objects[info_obj] = f"<< {entries} >>"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: Dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        body = objects[number]
        if body is None:
            payload = streams[number].encode("cp1252")
            out += f"{number} 0 obj\n<< /Length {len(payload)} >>\nstream\n".encode("cp1252")
            out += payload + b"\nendstream\nendobj\n"
        else:
            out += f"{number} 0 obj\n{body}\nendobj\n".encode("cp1252")

    xref_offset = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("cp1252")
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode("cp1252")

    trailer = f"<< /Size {size} /Root 1 0 R"
    if info_obj is not None:
        trailer += f" /Info {info_obj} 0 R"
    trailer += " >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref_offset}\n%%EOF\n".encode("cp1252")
    return bytes(out)


def page_lines(page_number: int, filler_lines: int = 6) -> List[str]:
    """Build predictable, page-identifying fixture text.

    Each page says its own number, which is how the page-ordering tests prove
    that page N of the output really is page N of the input rather than a
    plausible-looking shuffle.
    """
    lines = [f"FIXTURE PAGE {page_number} OF A SYNTHETIC TEST DOCUMENT"]
    lines.extend(
        f"Page {page_number} filler line {i + 1} with enough words to be meaningful."
        for i in range(filler_lines)
    )
    return lines
