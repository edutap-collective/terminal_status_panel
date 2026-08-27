"""terminal_status_panel — colorful server status panel for update-motd.d.

**There is no public Python API before 1.0.** This package is a set of console
scripts; `status-full`, `status-server`, `status-docker`, `status-health`,
`status-traefik` and `install-panel` are the whole supported surface, and the
README is their contract.

Everything importable from here is internal. Module layout, function
signatures, dataclass fields and the meaning of individual values may change
in any release, including a patch one, without a note in the changelog. That
is not an oversight to be corrected later by declaring the current shape
stable: the collectors and the model are still being cut along the lines the
panel turns out to need, and freezing them now would fix mistakes in place.

If you want to build on this, say so in an issue. A small, explicitly listed
surface -- the model dataclasses, one collector entry point -- is a reasonable
thing to add for a real caller, and a bad thing to guess at without one. Until
then, `__all__` stays empty rather than absent, so the intent is stated rather
than merely implied.

Deliberately no version constant here. `importlib.metadata.version(
"terminal-status-panel")` reads the one in the package metadata, and a second
copy in source is a second thing to forget at release time.
"""

#: Empty on purpose. See the module docstring: there is no public Python API
#: before 1.0, and an empty `__all__` says that in the place a reader looks.
__all__: list[str] = []
