"""Pending updates on Debian from a simulated upgrade.

``apt-get -s`` downloads nothing and changes nothing; the package caches are
switched off because the collector's file system is read-only. The package
lists are not refreshed here -- that is the system's own job.
"""

from __future__ import annotations

import os
import re
import subprocess

from ..model.host import UpdateCounts

UPDATE_INTERVAL = 3600
_ARGV = [
    "apt-get",
    "-s",
    "-o",
    "Debug::NoLocking=1",
    "-o",
    "Dir::Cache::pkgcache=",
    "-o",
    "Dir::Cache::srcpkgcache=",
    "dist-upgrade",
]
_INST = re.compile(r"^Inst \S+ .*\((?P<origin>[^)]*)\)")


def _simulate() -> str:
    return subprocess.run(  # noqa: S603 - fixed argv
        _ARGV, capture_output=True, text=True, timeout=20.0, env={**os.environ, "LC_ALL": "C"}
    ).stdout


def parse_simulation(text: str) -> tuple[int, int]:
    """(available, security) from the ``Inst`` lines."""
    available = security = 0
    for line in text.splitlines():
        match = _INST.match(line)
        if not match:
            continue
        available += 1
        if "-security" in match["origin"].lower():
            security += 1
    return available, security


def collect_updates(
    previous: UpdateCounts | None, now: float, runner=_simulate
) -> tuple[UpdateCounts | None, str | None]:
    """At most hourly; the previous answer stands in between."""
    if previous is not None and now - previous.checked_at < UPDATE_INTERVAL:
        return previous, None
    try:
        available, security = parse_simulation(runner())
    except FileNotFoundError:
        return None, "not-installed"
    except subprocess.TimeoutExpired:
        return previous, "timeout"
    return UpdateCounts(
        checked_at=now, available=available, security=security, source="apt-simulate"
    ), None
