"""Collect available package updates.

Two sources, ``apt-check`` first:

- Ubuntu's ``update-notifier`` helper prints ``<available>;<security>`` to
  stderr. It is read directly whenever it is present.
- Debian has no ``apt-check``. There the counts come from the host state file,
  which the collector takes from a simulated upgrade.

Where neither source exists the result degrades to ``supported=False`` so the
panel simply says so rather than failing.
"""

from __future__ import annotations

import subprocess

from ..model import UpdateInfo
from .hoststate import HostStateRead

APT_CHECK = "/usr/lib/update-notifier/apt-check"


def collect_updates(timeout: float = 1.5, host: HostStateRead | None = None) -> UpdateInfo:
    """Return the update counts; never raises.

    ``apt-check`` (Ubuntu) first; otherwise the counts the collector took from
    a simulated upgrade (Debian has no ``apt-check``); otherwise unsupported.
    """
    info = _from_apt_check(timeout)
    if info.supported:
        return info
    counts = host.state.updates if host and host.state else None
    if counts is None:
        return info
    return UpdateInfo(
        supported=True,
        available=counts.available,
        security=counts.security,
        standard=max(counts.available - counts.security, 0),
    )


def _from_apt_check(timeout: float) -> UpdateInfo:
    """Read the counts from ``apt-check``; unsupported when it is absent."""
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
