"""Request/response shapes for Library & Search. **SCAFFOLD — no models yet.**

Why this file is empty of models
--------------------------------
``backend/app/api/schemas.py`` already defines every document, page and clause
response model the API serves, and ``CLAUDE.md`` §12 fixes those shapes for
four consumers. Declaring a second ``ClauseOut`` here — even a faithful copy —
would create two definitions of the same payload that can drift, which is the
one failure mode the Phase 4 layer was built to avoid (see the note in
``api/dependencies.py``: the repository wrapper "forwards, and that is the
point").

So the rule for this module is: **reuse the existing models, add only what is
genuinely new.** What is genuinely new is small, and it is the ranking
metadata:

``SearchHit``
    an existing ``DocumentSummary`` or ``ClauseOut`` **plus** ``score``
    (float), ``strategy`` (which condition produced it) and ``matched_terms``.

``SearchResponse``
    the existing ``{total, limit, offset, items}`` envelope **plus**
    ``strategy`` and ``query`` echoed back, so a result is always attributable
    to the condition that produced it.

``FacetsResponse``
    ``{department: [{value, count}], document_type: [...], years: [...]}``
    with an explicit ``null`` bucket, because both columns are nullable in
    real documents.

These are written here as prose, not as classes, until the endpoints in
``README.md`` §4 are agreed. A model with no route is dead code, and a model
whose route is still being argued about is a contract nobody has signed.
"""

from __future__ import annotations

#: Nothing is defined yet, deliberately. See the module docstring.
__all__: list[str] = []
