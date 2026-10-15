"""Read the collector's state file. One open(), never raises, never blocks."""

from __future__ import annotations

import json
import time

from ..model.host import (
    HOST_EVENT_KINDS,
    STALE_AFTER_SECONDS,
    STATE_PATH,
    HostState,
    HostStateRead,
    UnknownSchemaError,
)

__all__ = ["HOST_EVENT_KINDS", "STALE_AFTER_SECONDS", "HostStateRead", "read_host_state"]


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
