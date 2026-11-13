"""Pluggable page-text extraction backends, for Experiment A.

Why this module exists
----------------------
``pdf_extractor.extract_pdf`` is the project's one ingestion path and it uses
``pypdf``. That has not changed and is not changed here. But
``CLAUDE.md`` §15 Experiment A requires comparing extraction libraries on the
real corpus *before* any dependency swap is justified, and a comparison needs
all three libraries reachable behind one interface.

So this module provides that interface and nothing else. It is deliberately
**not** wired into ``extract_pdf``: the production pipeline still calls
``pypdf`` directly through ``_extract_page_text``, which remains the documented
seam (``backend/docs/pdf-extraction.md`` §2). If Experiment A ever justifies a switch,
the migration is to make ``extract_pdf`` take a backend from here — an additive
change that leaves the JSON contract untouched.

Import policy
-------------
``pypdf`` is imported at module load because it is Phase 1's only runtime
dependency. ``pymupdf`` and ``pdfplumber`` are imported **lazily, inside the
backend that needs them**, so this module — and therefore the whole
``document_processing`` package — stays importable on a machine that has
installed only ``pypdf``. :func:`available_backends` reports which ones actually
loaded, so the experiment runner can report "not installed" rather than crash.

Fairness rules for the comparison
---------------------------------
Every backend is asked for exactly the same thing: the text of page *i*, in the
library's own default mode, with no per-document tuning. Where a library offers
a better mode (PyMuPDF's ``sort=True`` reading-order pass, pdfplumber's layout
mode), that is run as a **separate named backend** rather than silently swapped
in, because "pdfplumber is better" and "pdfplumber in layout mode is better" are
different claims and the report has to be able to tell them apart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import pypdf
from pypdf import PdfReader

__all__ = [
    "BackendExtraction",
    "BackendUnavailableError",
    "ExtractorBackend",
    "available_backends",
    "get_backend",
    "registered_backend_names",
]


class BackendUnavailableError(RuntimeError):
    """The backend's library is not installed in this environment.

    Raised at *use* time rather than import time so that a missing optional
    library degrades the experiment to "two of three backends compared", which
    is a reportable result, instead of breaking the package.
    """


@dataclass
class BackendExtraction:
    """What one backend produced for one document.

    ``page_texts`` is positional: index 0 is physical page 1. The list is always
    exactly ``page_count`` long, even when a page yielded nothing, because a
    comparison that silently drops pages cannot be aligned against another
    backend's output.
    """

    backend: str
    tool_version: str
    page_count: int
    page_texts: List[str]
    elapsed_seconds: float
    #: Per-page extraction failures: ``{page_number: "ExceptionType: message"}``.
    #: Recorded rather than raised — one unreadable page out of thirty-seven is
    #: itself a comparison result.
    page_errors: Dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.page_texts) != self.page_count:
            raise ValueError(
                f"{self.backend} returned {len(self.page_texts)} page texts for "
                f"a {self.page_count}-page document; the lists must align"
            )

    def text_for_page(self, page_number: int) -> str:
        """Return the text of a 1-based page number."""
        if not 1 <= page_number <= self.page_count:
            raise ValueError(
                f"page {page_number} is outside 1..{self.page_count}"
            )
        return self.page_texts[page_number - 1]


class ExtractorBackend:
    """One extraction library, wrapped behind a uniform call.

    Subclasses implement :meth:`_open_page_texts` and :meth:`_tool_version`.
    Timing and the page-error contract are handled here so every backend is
    measured the same way.
    """

    #: Short stable key used in output JSON and report tables.
    name: str = "unnamed"
    #: Whether this backend can return structured tables (rows and columns).
    supports_tables: bool = False

    def tool_version(self) -> str:
        """Return a ``"library x.y.z"`` string, recorded with every result."""
        raise NotImplementedError

    def _open_page_texts(self, path: Path) -> tuple[int, List[str], Dict[int, str]]:
        """Return ``(page_count, page_texts, page_errors)`` for one document."""
        raise NotImplementedError

    def extract(self, path: Path | str) -> BackendExtraction:
        """Extract every page, timed.

        The clock covers opening the file as well as reading the pages, because
        for a user "how long does ingestion take" includes the parse. Each
        backend opens the same file from disk, so the measurement is comparable
        even though it is not a micro-benchmark.
        """
        path = Path(path)
        started = time.perf_counter()
        page_count, page_texts, page_errors = self._open_page_texts(path)
        elapsed = time.perf_counter() - started
        return BackendExtraction(
            backend=self.name,
            tool_version=self.tool_version(),
            page_count=page_count,
            page_texts=page_texts,
            elapsed_seconds=elapsed,
            page_errors=page_errors,
        )

    def extract_tables(self, path: Path | str, page_number: int) -> Optional[List[List[List[Optional[str]]]]]:
        """Return structured tables on a 1-based page, or ``None`` if unsupported.

        A table is a list of rows; a row is a list of cell strings, where
        ``None`` marks a cell the library could not fill. ``None`` for the whole
        return value means "this library has no table API" — which is itself the
        finding for pypdf, and is different from ``[]``, meaning "it looked and
        found no table".
        """
        return None


def _safe_page_loop(
    page_count: int,
    read_page: Callable[[int], str],
) -> tuple[List[str], Dict[int, str]]:
    """Read every page, converting a per-page failure into a recorded error.

    Shared by all backends so the "one bad page does not lose the document"
    policy from ``pdf_extractor`` is applied identically in the comparison.
    """
    texts: List[str] = []
    errors: Dict[int, str] = {}
    for index in range(page_count):
        try:
            texts.append(read_page(index) or "")
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            texts.append("")
            errors[index + 1] = f"{type(exc).__name__}: {exc}"
    return texts, errors


# --------------------------------------------------------------------------- #
# pypdf - the baseline, and the library the production pipeline actually uses
# --------------------------------------------------------------------------- #


class PypdfBackend(ExtractorBackend):
    """The project's current extractor. The control condition for Experiment A.

    This calls ``page.extract_text()`` exactly as
    ``pdf_extractor._extract_page_text`` does, so a difference in the comparison
    is a difference in the *library*, not in how we drive it.
    """

    name = "pypdf"

    def tool_version(self) -> str:
        return f"pypdf {pypdf.__version__}"

    def _open_page_texts(self, path: Path) -> tuple[int, List[str], Dict[int, str]]:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        texts, errors = _safe_page_loop(
            page_count, lambda i: reader.pages[i].extract_text()
        )
        return page_count, texts, errors


# --------------------------------------------------------------------------- #
# PyMuPDF
# --------------------------------------------------------------------------- #


def _import_pymupdf() -> Any:
    try:
        import pymupdf  # noqa: PLC0415 - lazy by design, see module docstring
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise BackendUnavailableError(
            "PyMuPDF is not installed in this environment "
            "(pip install -r requirements.txt)"
        ) from exc
    return pymupdf


class PyMuPDFBackend(ExtractorBackend):
    """PyMuPDF in its default text mode.

    ``page.get_text()`` returns text in the order the glyphs appear in the
    content stream, which is the closest analogue to what pypdf does and so the
    fair head-to-head.
    """

    name = "pymupdf"
    supports_tables = True
    _sort_by_position = False

    def tool_version(self) -> str:
        pymupdf = _import_pymupdf()
        # PyMuPDF exposes a (wrapper, mupdf, date) tuple; both numbers matter,
        # because the MuPDF build is what actually does the text layout.
        version = getattr(pymupdf, "version", None)
        if isinstance(version, (tuple, list)) and len(version) >= 2:
            return f"PyMuPDF {version[0]} (MuPDF {version[1]})"
        return f"PyMuPDF {getattr(pymupdf, '__version__', 'unknown')}"

    def _open_page_texts(self, path: Path) -> tuple[int, List[str], Dict[int, str]]:
        pymupdf = _import_pymupdf()
        with pymupdf.open(str(path)) as document:
            page_count = document.page_count
            texts, errors = _safe_page_loop(
                page_count,
                lambda i: document[i].get_text("text", sort=self._sort_by_position),
            )
        return page_count, texts, errors

    def extract_tables(
        self, path: Path | str, page_number: int
    ) -> Optional[List[List[List[Optional[str]]]]]:
        pymupdf = _import_pymupdf()
        with pymupdf.open(str(path)) as document:
            page = document[page_number - 1]
            try:
                finder = page.find_tables()
            except Exception:  # noqa: BLE001 - "no table API result" is a finding
                return []
            return [table.extract() for table in finder.tables]


class PyMuPDFSortedBackend(PyMuPDFBackend):
    """PyMuPDF with ``sort=True``: text re-ordered by position on the page.

    Kept separate from :class:`PyMuPDFBackend` because this is the library's
    reading-order *fix*, not its default. Reporting it as its own condition is
    what lets the write-up say whether multi-column reading order improves
    because of the library or because of the flag.
    """

    name = "pymupdf-sorted"
    _sort_by_position = True


# --------------------------------------------------------------------------- #
# pdfplumber
# --------------------------------------------------------------------------- #


def _import_pdfplumber() -> Any:
    try:
        import pdfplumber  # noqa: PLC0415 - lazy by design
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise BackendUnavailableError(
            "pdfplumber is not installed in this environment "
            "(pip install -r requirements.txt)"
        ) from exc
    return pdfplumber


class PdfplumberBackend(ExtractorBackend):
    """pdfplumber in its default text mode, built on ``pdfminer.six``."""

    name = "pdfplumber"
    supports_tables = True
    _layout_mode = False

    def tool_version(self) -> str:
        pdfplumber = _import_pdfplumber()
        return f"pdfplumber {getattr(pdfplumber, '__version__', 'unknown')}"

    def _open_page_texts(self, path: Path) -> tuple[int, List[str], Dict[int, str]]:
        pdfplumber = _import_pdfplumber()
        with pdfplumber.open(str(path)) as document:
            page_count = len(document.pages)
            texts, errors = _safe_page_loop(
                page_count,
                lambda i: document.pages[i].extract_text(layout=self._layout_mode),
            )
        return page_count, texts, errors

    def extract_tables(
        self, path: Path | str, page_number: int
    ) -> Optional[List[List[List[Optional[str]]]]]:
        pdfplumber = _import_pdfplumber()
        with pdfplumber.open(str(path)) as document:
            page = document.pages[page_number - 1]
            try:
                return page.extract_tables()
            except Exception:  # noqa: BLE001 - "found nothing" is a finding
                return []


class PdfplumberLayoutBackend(PdfplumberBackend):
    """pdfplumber with ``layout=True``: whitespace padded to mimic the page.

    This preserves visual column alignment, which can keep a table's rows
    readable — at the cost of inserting a lot of spaces, which matters for the
    exact-substring citation rule in ``CLAUDE.md`` §12.2. A separate condition
    for exactly that reason.
    """

    name = "pdfplumber-layout"
    _layout_mode = True


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

#: Every backend the experiment can run, keyed by its stable name. ``pypdf``
#: is first because it is the baseline and every table in the report leads with
#: it.
_REGISTRY: Dict[str, Callable[[], ExtractorBackend]] = {
    PypdfBackend.name: PypdfBackend,
    PyMuPDFBackend.name: PyMuPDFBackend,
    PyMuPDFSortedBackend.name: PyMuPDFSortedBackend,
    PdfplumberBackend.name: PdfplumberBackend,
    PdfplumberLayoutBackend.name: PdfplumberLayoutBackend,
}


def registered_backend_names() -> List[str]:
    """Return every backend name, baseline first."""
    return list(_REGISTRY)


def get_backend(name: str) -> ExtractorBackend:
    """Instantiate a backend by name.

    Raises:
        KeyError: No backend is registered under that name.
    """
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown extractor backend {name!r}; "
            f"available: {', '.join(_REGISTRY)}"
        ) from None
    return factory()


def available_backends(names: Optional[Sequence[str]] = None) -> tuple[List[ExtractorBackend], Dict[str, str]]:
    """Return ``(usable_backends, unavailable_reasons)``.

    Availability is probed by asking each backend for its version string, which
    is the cheapest call that still forces the lazy import. A backend whose
    library is missing is reported in the second element rather than raised, so
    the experiment can run — and say so — with a partial set.
    """
    wanted = list(names) if names is not None else registered_backend_names()
    usable: List[ExtractorBackend] = []
    unavailable: Dict[str, str] = {}
    for name in wanted:
        backend = get_backend(name)
        try:
            backend.tool_version()
        except BackendUnavailableError as exc:
            unavailable[name] = str(exc)
            continue
        usable.append(backend)
    return usable, unavailable
