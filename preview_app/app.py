"""NiyamSetu developer preview — Streamlit entry point.

Run it from the repository root, so the ``backend`` and ``preview_app`` packages
resolve:

.. code-block:: bash

    .venv\\Scripts\\python.exe -m streamlit run preview_app/app.py

This file does routing and chrome only. Every screen lives in
``preview_app/components/screens.py`` and every read goes through
``preview_app/components/data_access.py``.

**This is not the product frontend.** Person 2 owns that (``CLAUDE.md`` §5).
This exists so the pipeline's output can be looked at during development, and
it is built to be deleted.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit runs this file as a script, not as a module, so the repository root
# is not on sys.path and `import preview_app...` would fail. Inserting it here
# is what lets the app be launched with `streamlit run preview_app/app.py`
# from the repository root, which is the documented command.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st  # noqa: E402 - must follow the sys.path insert

from preview_app.components.data_access import (  # noqa: E402
    DataAccessError,
    cached_project_data,
    clear_cache,
)
from preview_app.components.screens import (  # noqa: E402
    render_cleaning,
    render_dashboard,
    render_evaluation,
    render_extractor_comparison,
    render_library,
    render_segmentation,
    render_viewer,
)
from preview_app.components.styles import CSS, escape  # noqa: E402

#: Sidebar label -> render function. Ordered as the pipeline runs, so the
#: navigation itself explains the system: corpus, then documents, then text,
#: then segments, then the experiments that measure them.
SCREENS = {
    "Dashboard": render_dashboard,
    "Document library": render_library,
    "Document viewer": render_viewer,
    "Segmentation explorer": render_segmentation,
    "Header/footer cleaning": render_cleaning,
    "Extractor comparison (A)": render_extractor_comparison,
    "Evaluation (B and C)": render_evaluation,
}


def main() -> None:
    st.set_page_config(
        page_title="NiyamSetu — developer preview",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("### NiyamSetu")
        st.caption("Developer preview · not the product frontend")
        choice = st.radio("Screen", list(SCREENS), label_visibility="collapsed")

        st.divider()
        if st.button("Reload artefacts from disk", use_container_width=True):
            # The only cache-invalidation path. Streamlit re-runs the script on
            # every interaction, so without the cache every click would re-parse
            # several megabytes of JSON; without this button a freshly
            # regenerated artefact would never be picked up.
            clear_cache()
            st.rerun()

    try:
        data = cached_project_data()
    except DataAccessError as exc:
        st.error(f"Could not load project data: {exc}")
        st.info(
            "This means an artefact exists but is unreadable, which is different "
            "from not having run the pipeline. Regenerate the file rather than "
            "editing it by hand."
        )
        return

    with st.sidebar:
        st.caption(
            f"{data.version_count} version(s) · {data.total_pages} page(s) · "
            f"{sum(data.segment_totals.values())} segment(s) loaded"
        )
        if data.load_errors:
            st.warning(f"{len(data.load_errors)} artefact(s) unreadable")

    SCREENS[choice](data)

    st.divider()
    st.caption(
        "Every figure on this page is a count of records loaded from "
        "data/processed/ and evaluation/, or a value copied from a committed "
        "results.json. Nothing is sample data and nothing is computed here."
    )


if __name__ == "__main__":
    main()
else:  # pragma: no cover - Streamlit imports the module rather than running it
    main()
