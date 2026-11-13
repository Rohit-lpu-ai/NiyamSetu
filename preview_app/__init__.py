"""Developer preview frontend for NiyamSetu — a throwaway internal tool.

**This is not the product frontend.** Person 2 owns the real UI
(``CLAUDE.md`` §5, §7). This package exists so Person 1 can see what the
backend and the document-processing pipeline actually produce, on screen,
during development.

It is deliberately disposable: nothing in ``backend/`` imports from here, and
the whole directory can be deleted without affecting the pipeline, the tests or
the evaluation. Its only route to project data is
``preview_app.components.data_access``, which is read-only.
"""
