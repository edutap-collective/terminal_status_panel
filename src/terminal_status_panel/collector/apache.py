"""Apache httpd from mod_status ``?auto``, as a rate over the collector's window.

``ReqPerSec`` in the status page is an average since the server started, a
number that barely moves on a server up for weeks. The rate here is the
difference in ``Total Accesses`` between two runs, divided by the time between
them.
"""

from __future__ import annotations

import json
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


def collect_apache(
    url: str,
    previous_path: Path,
    now: float,
    fetch: Callable[[str, float], str] = _fetch,
) -> tuple[ApacheState | None, str | None]:
    """The Apache block and an error code; remembers the totals for the next run."""
    try:
        body = fetch(url + "?auto", 2.0)
    except OSError:
        return None, "unreachable"
    try:
        status = parse_status(body)
    except (KeyError, ValueError):
        return None, "parse"

    rate = None
    window = 0.0
    try:
        previous = json.loads(previous_path.read_text())
        window = now - float(previous["at"])
        delta = status["total"] - int(previous["total"])
        if window > 0 and delta >= 0:
            rate = delta / window
    except (OSError, ValueError, KeyError, TypeError):
        pass  # first run, or a state file this code cannot read: no rate yet
    previous_path.write_text(json.dumps({"total": status["total"], "at": now}))

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
