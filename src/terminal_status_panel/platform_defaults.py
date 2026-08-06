"""Default values that differ per operating system.

The panel was written for Linux servers. A few defaults are wrong elsewhere --
macOS hides two large families of uninteresting mounts and swaps continuously by
design; the BSDs are not Linux and must not be labelled with Tux.

Collecting those differences here keeps ``config.py`` free of platform names and
gives a single place to extend when another system is added. Anything beyond a
default belongs in the collector that owns it, not here.
"""

from __future__ import annotations

import platform

#: macOS system volumes (``/System/Volumes/VM``, ``Preboot``, ``Update`` ...) and
#: the Xcode simulator runtimes, which on a development machine outnumber the
#: real filesystems roughly seven to one.
_DARWIN_IGNORE_MOUNTPOINTS = (
    "/System/Volumes/",
    "/Library/Developer/CoreSimulator/",
)

#: Systems whose logo is decided by the platform rather than by a distribution
#: name. Everything absent from this mapping falls through to the distribution
#: matching in ``render/logo.py``, whose fallback is Tux -- a true statement
#: about any Linux and a false one about anything else.
_LOGO_KEYS = {
    "Darwin": "macos",
    "FreeBSD": "freebsd",
    "OpenBSD": "bsd",
    "NetBSD": "bsd",
}


def ignore_mountpoints() -> list[str]:
    """Mountpoint prefixes hidden unless the config says otherwise."""
    if platform.system() == "Darwin":
        return list(_DARWIN_IGNORE_MOUNTPOINTS)
    return []


def swap_warning() -> float:
    """Percentage of swap in use above which the panel warns.

    macOS allocates swap dynamically and keeps it populated as a matter of
    course, so the Linux default of 1% would warn on every healthy Mac.
    """
    if platform.system() == "Darwin":
        return 80.0
    return 1.0


def logo_key() -> str | None:
    """Logo file stem decided by the platform, or None to defer to the distro."""
    return _LOGO_KEYS.get(platform.system())
