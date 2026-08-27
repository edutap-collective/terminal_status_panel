# Requirements and stability

- Python 3.11+
- Linux (Debian/Ubuntu) in production; macOS supported for development.
- Optional: a running Docker daemon for the services panel.

## There is no public Python API before 1.0

This package is a set of console scripts. `status-full`, `status-server`,
`status-docker`, `status-health`, `status-traefik` and `install-panel` are the
whole supported surface, and this README is their contract.

Everything importable from `terminal_status_panel` is internal. Module layout,
function signatures, dataclass fields and the meaning of individual values may
change in any release, including a patch one, without a note in the changelog.
`__all__` is empty rather than absent so that the intent is stated where a
reader looks for it.

This is not an oversight waiting to be corrected by declaring the current
shape stable. The collectors and the model are still being cut along the lines
the panel turns out to need — three of them moved in 0.10 alone — and freezing
them now would fix today's mistakes in place. If you want to build on this,
open an issue: a small, explicitly listed surface is a reasonable thing to add
for a real caller and a bad thing to guess at without one.
