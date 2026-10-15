"""Read the collector's state file. One open(), never raises, never blocks."""

from __future__ import annotations

import json
import time

from ..model import TroubleEntry
from ..model.host import (
    HOST_EVENT_KINDS,
    STALE_AFTER_SECONDS,
    STATE_PATH,
    HostState,
    HostStateRead,
    UnknownSchemaError,
)

__all__ = [
    "HOST_EVENT_KINDS",
    "STALE_AFTER_SECONDS",
    "HostStateRead",
    "host_trouble",
    "read_host_state",
]


def read_host_state(path: str = STATE_PATH, now: float | None = None) -> HostStateRead:
    """The state file at *path*, measured against *now* (default: the clock).

    Any content that is not a readable state of this schema comes back as a
    problem, never as an exception: a JSON parser can fail in more ways than
    ValueError (deep nesting, for one), and the panel must render regardless.
    """
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return HostStateRead(problem="missing")
    except Exception:  # noqa: BLE001 -- any parse or read failure is "unreadable"
        return HostStateRead(problem="unreadable")
    try:
        state = HostState.from_dict(data)
    except UnknownSchemaError:
        return HostStateRead(problem="unknown schema")
    except Exception:  # noqa: BLE001 -- malformed content of any shape
        return HostStateRead(problem="unreadable")
    return HostStateRead(state=state, age_s=max(0.0, now - state.written_at))


_EVENT_LABELS = {
    "oom_kill": ("host: OOM kill", "dead"),
    "unit_failed": ("host: unit failed", "dead"),
    "apache_slots_full": ("host: Apache slots full", "restarting"),
    "vrrp_state": ("host: VRRP state change", "restarting"),
    "apache_reload": ("host: Apache reload", "restarting"),
}


def _age(seconds: float) -> str:
    """Same wording as the HEALTH block's state age."""
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds // 60:.0f} min"


def _ago(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    return f"{total // 3600}h"


def host_trouble(read: HostStateRead, now: float, enabled: tuple[str, ...]) -> list[TroubleEntry]:
    """One row per enabled host event kind that happened in the window."""
    if read.state is None:
        return []
    rows = []
    stale = f"state {_age(read.age_s)} old" if read.stale and read.age_s is not None else None
    for kind, count in read.state.events.items():
        if kind not in enabled or count.count <= 0 or kind not in _EVENT_LABELS:
            continue
        label, severity = _EVENT_LABELS[kind]
        cause = None if count.last is None else f"last {_ago(max(0.0, now - count.last))} ago"
        if stale:
            cause = f"{cause} ({stale})" if cause else f"({stale})"
        rows.append(
            TroubleEntry(
                name=label, fails=count.count, cause=cause, severity=severity, source="host"
            )
        )
    return rows
