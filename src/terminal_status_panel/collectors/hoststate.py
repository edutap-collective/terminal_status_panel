"""Read the collector's state file. One open(), never raises, never blocks."""

from __future__ import annotations

import json
import time

from ..model.host import HOST_EVENT_KINDS, STALE_AFTER_SECONDS, STATE_PATH, HostState, HostStateRead

__all__ = ["HOST_EVENT_KINDS", "STALE_AFTER_SECONDS", "HostStateRead", "read_host_state"]


def read_host_state(path: str = STATE_PATH, now: float | None = None) -> HostStateRead:
    """The state file at *path*, measured against *now* (default: the clock)."""
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return HostStateRead(problem="missing")
    except (OSError, ValueError):
        return HostStateRead(problem="unreadable")
    try:
        state = HostState.from_dict(data)
    except ValueError as exc:
        return HostStateRead(problem="unknown schema" if "schema" in str(exc) else "unreadable")
    except (KeyError, TypeError):
        return HostStateRead(problem="unreadable")
    return HostStateRead(state=state, age_s=max(0.0, now - state.written_at))
