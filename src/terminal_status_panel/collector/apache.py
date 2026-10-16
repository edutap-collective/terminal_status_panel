"""Apache httpd from mod_status ``?auto``, as a rate over the collector's window.

``ReqPerSec`` in the status page is an average since the server started, a
number that barely moves on a server up for weeks. The rate here is the
difference in ``Total Accesses`` between two runs, divided by the time between
them.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..model.host import ApacheState


def _fetch(url: str, timeout: float) -> str:
    """The status page body. The URL is the configured local one."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("ascii", "replace")


def parse_status(text: str) -> dict:
    """The four fields used, from the machine-readable status page.

    A missing ``Total Accesses`` raises KeyError; every other field may be absent.
    """
    fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
    busy = fields.get("BusyWorkers")
    idle = fields.get("IdleWorkers")
    return {
        "total": int(fields["Total Accesses"]),
        "busy": int(busy) if busy is not None else None,
        "idle": int(idle) if idle is not None else None,
        "scoreboard": fields.get("Scoreboard", ""),
    }


def _previous(previous_path: Path) -> dict | None:
    """The totals the last run left behind, or None when there are none usable."""
    try:
        data = json.loads(previous_path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _window_and_rate(total: int, previous: dict | None, now: float) -> tuple[float, float | None]:
    """The seconds since the previous run and the request rate over them.

    The window is 0.0 unless it is a positive finite number. The rate is None
    without a usable previous run, on a zero or negative window, and after a
    restart (the total went down).
    """
    if previous is None:
        return 0.0, None
    try:
        elapsed = now - float(previous["at"])
        before = int(previous["total"])
    except (KeyError, TypeError, ValueError):
        return 0.0, None
    window = elapsed if math.isfinite(elapsed) and elapsed > 0 else 0.0
    if window == 0.0 or total < before:
        return window, None
    return window, (total - before) / window


def _write_atomic(path: Path, payload: dict) -> None:
    """Replace *path* in one step, so a reader never sees half a file."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload))
    os.replace(temporary, path)


def collect_apache(
    url: str,
    previous_path: Path,
    now: float,
    fetch: Callable[[str, float], str] = _fetch,
) -> tuple[ApacheState | None, str | None]:
    """The Apache block and an error code; remembers the totals for the next run.

    The totals file is written only after a successful read. If it cannot be
    written, the state is still returned; the next run then has no rate.
    """
    try:
        body = fetch(url + "?auto", 2.0)
    except (OSError, http.client.HTTPException):
        return None, "unreachable"
    try:
        status = parse_status(body)
    except (KeyError, ValueError):
        return None, "parse"

    window, rate = _window_and_rate(status["total"], _previous(previous_path), now)
    try:
        _write_atomic(previous_path, {"total": status["total"], "at": now})
    except OSError:
        pass  # no totals saved: the next run simply has no rate

    board = status["scoreboard"]
    slots = len(board)
    return (
        ApacheState(
            window_s=window,
            requests_per_s=rate,
            slots_busy=1 - board.count(".") / slots if slots else None,
            workers_busy=status["busy"] / slots if slots and status["busy"] is not None else None,
            slots_finishing=board.count("G") if slots else None,
        ),
        None,
    )
