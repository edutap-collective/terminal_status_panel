"""Collect available package updates on Debian/Ubuntu.

Reads the counts produced by ``update-notifier``'s ``apt-check`` helper, which
prints ``<available>;<security>`` to stderr. On any platform where the helper
is missing (macOS dev, non-Debian) the result degrades to ``supported=False``
so the panel simply omits the section rather than failing.
"""

from __future__ import annotations

import subprocess

from ..model import UpdateInfo

APT_CHECK = "/usr/lib/update-notifier/apt-check"


def collect_updates(timeout: float = 1.5) -> UpdateInfo:
    """Return the update counts; never raises."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed absolute path, no shell
            [APT_CHECK],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # apt-check writes "available;security" to stderr (stdout on some versions).
        raw = (proc.stderr or proc.stdout).strip()
        available_str, security_str = raw.split(";")
        available = int(available_str)
        security = int(security_str)
        return UpdateInfo(
            supported=True,
            available=available,
            security=security,
            standard=max(available - security, 0),
        )
    except Exception:
        return UpdateInfo(supported=False)
