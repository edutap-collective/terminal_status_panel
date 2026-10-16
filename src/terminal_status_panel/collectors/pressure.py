"""Pressure stall information: how much of the time something had to wait.

The kernel's answer to "is this host short of CPU right now", over 10 and 60
seconds -- shorter than any load average, and without the I/O waits Linux
folds into load. Absent on macOS, FreeBSD and kernels built without PSI; then
there is simply nothing to report.
"""

from __future__ import annotations

import os
import re

_SOME = re.compile(r"^some avg10=(?P<avg10>[0-9.]+) avg60=(?P<avg60>[0-9.]+)\b")

RESOURCES = ("cpu", "memory", "io")


def read_pressure(root: str = "/proc/pressure") -> dict[str, tuple[float, float]]:
    """``{resource: (avg10, avg60)}`` of the ``some`` line; missing files skipped."""
    result: dict[str, tuple[float, float]] = {}
    for resource in RESOURCES:
        try:
            with open(os.path.join(root, resource), encoding="ascii") as handle:
                first = handle.readline()
        except OSError:
            continue
        match = _SOME.match(first)
        if match:
            result[resource] = (float(match["avg10"]), float(match["avg60"]))
    return result
