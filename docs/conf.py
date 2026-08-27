"""Sphinx configuration.

Deliberately small, and matching the sibling eduTAP packages: MyST for the
markup, alabaster for the theme, no extension that the pages do not use. A
documentation build is a thing that has to keep working; every extension in
here is one more thing that can break a release.

`superpowers/` is excluded rather than published. Those are working documents
-- specs, plans, handoffs -- and they are snapshots of a decision on its date.
Publishing them as if they were current documentation would be the opposite of
what they are.
"""

project = "terminal-status-panel"
author = "Alexander Loechel"
copyright = "2026, Ludwig-Maximilians-Universität München"  # noqa: A001

extensions = ["myst_parser"]

myst_enable_extensions = ["colon_fence", "deflist", "attrs_inline"]

html_theme = "alabaster"

exclude_patterns = ["_build", "superpowers/**"]

# -W turns warnings into errors in the Makefile, so an unreferenced page or a
# broken cross-reference fails the build rather than being noticed later.
nitpicky = False

linkcheck_ignore = [
    # Not published yet; the link is correct and will resolve on first release.
    r"https://pypi\.org/project/terminal-status-panel",
]
linkcheck_timeout = 15
