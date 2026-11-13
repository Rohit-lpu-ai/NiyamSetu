"""Screens and the data-access layer for the developer preview.

``data_access`` is the only module here that touches the filesystem. The screen
modules receive a loaded :class:`~preview_app.components.data_access.ProjectData`
and render it, so a screen cannot accidentally invent a number that is not in an
artefact.
"""
