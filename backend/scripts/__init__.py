"""Developer and maintenance scripts.

Not part of the runtime: nothing in ``backend/app/`` imports from here.
These are run by hand, as modules, from the repository root::

    .venv\Scripts\python -m backend.scripts.inspect_real_extraction

On Windows, set ``PYTHONIOENCODING=utf-8`` first: the inspection helper
prints Devanagari, which a cp1252 console cannot encode.

``inspect_real_extraction.py`` lived in ``backend/tests/`` until the 2026-11-13
cleanup. It is not a test -- it asserts nothing and scores nothing -- so it
moved here, leaving ``backend/tests/`` containing only tests.
"""
