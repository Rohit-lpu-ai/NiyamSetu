"""Evaluation scripts and reusable measurement helpers.

This package holds the *measuring* code, kept separate from the pipeline in
``backend/app/`` on purpose: a metric must never be able to change what the
pipeline produces. Nothing in ``backend/app/`` imports from here.

Modules:
    ``text_artifacts``
        Detectors for the extraction defects catalogued by hand in
        ``evaluation/2026-10-28-phase1-extraction-validation.md`` §4, turned
        into counters so they can be compared across extractors and tracked
        over time.
    ``experiment_a_extractor_comparison``
        Experiment A: pypdf vs PyMuPDF vs pdfplumber on the real corpus.
    ``experiment_b_segmentation``
        Experiment B: paragraph vs clause-aware segmentation, scored against
        committed annotations.
    ``data_quality``
        Experiment C: the corpus-wide data-quality checks.
"""
