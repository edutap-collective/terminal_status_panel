"""The package's stated surface, as an assertion rather than a promise in prose.

There is no public Python API before 1.0. That is a decision, not an accident,
and it is the kind of decision a later refactor quietly reverses by exporting
something convenient.
"""

from __future__ import annotations

import importlib.metadata

import terminal_status_panel


def test_the_package_exports_nothing():
    """`__all__` empty and present: the intent is stated, not merely implied."""
    assert terminal_status_panel.__all__ == []


def test_the_policy_is_written_where_an_importer_would_look():
    doc = terminal_status_panel.__doc__ or ""
    assert "no public Python API before 1.0" in doc


def test_the_version_is_read_from_the_metadata_not_duplicated():
    """A second copy in source is a second thing to forget at release time."""
    assert not hasattr(terminal_status_panel, "__version__")
    assert importlib.metadata.version("terminal-status-panel")


def test_the_console_scripts_are_the_supported_surface():
    """Each script named in the README resolves to something callable."""
    entry_points = importlib.metadata.entry_points(group="console_scripts")
    ours = {ep.name: ep for ep in entry_points if ep.module.startswith("terminal_status_panel")}

    assert set(ours) == {
        "status-full",
        "status-server",
        "status-docker",
        "status-health",
        "status-traefik",
        "install-panel",
    }
    for entry_point in ours.values():
        assert callable(entry_point.load())
