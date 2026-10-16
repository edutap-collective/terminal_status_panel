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


def _simulate() -> tuple[int, str]:
    """The exit code and the standard output of the simulated upgrade."""
    proc = subprocess.run(  # noqa: S603 - fixed argv
        _ARGV,
        capture_output=True,
        text=True,
        timeout=20.0,
        env={**os.environ, "LC_ALL": "C"},
    )
    return proc.returncode, proc.stdout


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
    previous: UpdateCounts | None,
    now: float,
    runner=_simulate,
) -> tuple[UpdateCounts | None, str | None]:
    """At most hourly; the previous answer stands in between.

    A failed or timed-out simulation keeps *previous* and its timestamp, so a
    broken apt is never reported as a fresh "0 updates".
    """
    if previous is not None and now - previous.checked_at < UPDATE_INTERVAL:
        return previous, None
    try:
        code, text = runner()
    except FileNotFoundError:
        return None, "not-installed"
    except subprocess.TimeoutExpired:
        return previous, "timeout"
    except OSError:
        return previous, "apt-failed"
    if code != 0:
        return previous, "apt-failed"
    available, security = parse_simulation(text)
    return UpdateCounts(
        checked_at=now, available=available, security=security, source="apt-simulate"
    ), None
