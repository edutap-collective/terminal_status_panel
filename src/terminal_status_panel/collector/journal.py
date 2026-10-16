"""Host events from the journal, read incrementally.

Each kind is bound to its source -- transport, identifier, unit or PID 1 --
and never to text alone: a free-text search counts every
``sudo journalctl -g ...`` as a hit. The source also filters on the server
side (``SOURCES``), so the collector never parses the journal's bulk. Only
counts and timestamps leave this module; message text never does, and never
reaches the state file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..model.host import EventCount

WINDOW = 12 * 3600
#: The first read covers the window; an incremental read covers a few seconds.
WINDOW_TIMEOUT = 20.0
INCREMENTAL_TIMEOUT = 5.0


def _message(entry: dict) -> str:
    """The MESSAGE text, or "" when the journal stored binary data instead."""
    text = entry.get("MESSAGE", "")
    return text if isinstance(text, str) else ""


PATTERNS: dict[str, Callable[[dict], bool]] = {
    "oom_kill": lambda e: e.get("_TRANSPORT") == "kernel" and "Killed process" in _message(e),
    "vrrp_state": lambda e: (
        e.get("SYSLOG_IDENTIFIER") == "Keepalived_vrrp"
        and re.search(r"Entering (MASTER|BACKUP|FAULT) STATE", _message(e)) is not None
    ),
    "apache_slots_full": lambda e: (
        e.get("_SYSTEMD_UNIT") in ("apache2.service", "httpd.service") and "AH03490" in _message(e)
    ),
    "unit_failed": lambda e: e.get("_PID") == "1" and "Failed with result" in _message(e),
    "apache_reload": lambda e: (
        e.get("_PID") == "1"
        and re.match(r"Reloading (apache2|httpd)\.service", _message(e)) is not None
    ),
}
KINDS = tuple(PATTERNS)

#: The journal field match each kind needs, as journalctl takes it. Every
#: entry is one journalctl match; the server-side filter ORs them all.
SOURCES: dict[str, tuple[str, ...]] = {
    "oom_kill": ("_TRANSPORT=kernel",),
    "vrrp_state": ("SYSLOG_IDENTIFIER=Keepalived_vrrp",),
    "apache_slots_full": ("_SYSTEMD_UNIT=apache2.service", "_SYSTEMD_UNIT=httpd.service"),
    "unit_failed": ("_PID=1",),
    "apache_reload": ("_PID=1",),
}
_FIELDS = "MESSAGE,_TRANSPORT,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT,_PID"


def _filter_args() -> list[str]:
    """The matches joined by ``+``: journalctl ORs matches of different fields."""
    matches = list(dict.fromkeys(m for kind in KINDS for m in SOURCES[kind]))
    args: list[str] = []
    for match in matches:
        if args:
            args.append("+")
        args.append(match)
    return args


def _journal(args: list[str], timeout: float) -> tuple[int, str]:
    # PATH-resolved, as the other collectors do.
    argv = [
        "journalctl",
        "-o",
        "json",
        "--no-pager",
        f"--output-fields={_FIELDS}",
        *args,
        *_filter_args(),
    ]
    proc = subprocess.run(  # noqa: S603 - fixed argv plus a cursor/since value
        argv,  # noqa: S607 - see above
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def _write_atomic(path: Path, payload: dict) -> None:
    """Replace *path* in one step, so a reader never sees half a file."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload))
    os.replace(temporary, path)


def _load(state_path: Path) -> dict:
    """The saved cursor and timestamps, or an empty state when there are none usable."""
    try:
        saved = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _timestamp(entry: dict) -> float | None:
    """The entry's time in seconds, or None when the journal gave no number."""
    try:
        return int(entry.get("__REALTIME_TIMESTAMP", "")) / 1e6
    except (TypeError, ValueError):
        return None


def _read_window(runner, cursor: str | None, since: str) -> tuple[int, str, bool]:
    """The journal lines after *cursor*, or the window when there is no usable cursor.

    The third value is True when the cursor was tried and lost, so the window
    was read in full instead.
    """
    if cursor:
        code, out = runner([f"--after-cursor={cursor}"], INCREMENTAL_TIMEOUT)
        if code == 0:
            return code, out, False
    code, out = runner([since], WINDOW_TIMEOUT)
    return code, out, bool(cursor)


def _scan(
    out: str, enabled: tuple[str, ...], stamps: dict[str, list[float]], cursor: str | None
) -> str | None:
    """Add the matching entries of *out* to *stamps*; return the last cursor seen.

    Lines that are not JSON objects, and entries whose time is not a number,
    are skipped.
    """
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        cursor = entry.get("__CURSOR", cursor)
        at = _timestamp(entry)
        if at is None:
            continue
        for kind in enabled:
            if PATTERNS[kind](entry):
                stamps.setdefault(kind, []).append(at)
    return cursor


def collect_events(
    state_path: Path,
    now: float,
    enabled: tuple[str, ...],
    runner=_journal,
) -> tuple[dict[str, EventCount], str | None]:
    """Counts per enabled kind over the window, and an error code.

    Never raises. On a failed or timed-out read the last known counts are
    returned with the error code and the saved state is left alone. A failed
    state write loses only the saved cursor, so the next run reads the window
    again; the counts returned are still correct.
    """
    saved = _load(state_path)
    known: dict[str, list[float]] = {}
    for kind, values in (saved.get("events") or {}).items():
        if isinstance(values, list):
            known[kind] = [t for t in values if isinstance(t, (int, float))]
    since = f"--since=@{int(now - WINDOW)}"
    try:
        code, out, fell_back = _read_window(runner, saved.get("cursor"), since)
    except FileNotFoundError:
        return _counts(known, now, enabled), "not-installed"
    except subprocess.TimeoutExpired:
        return _counts(known, now, enabled), "timeout"
    if code != 0:
        return _counts(known, now, enabled), "no-permission"
    # A lost cursor means the window was read in full: the saved stamps would count twice.
    stamps: dict[str, list[float]] = {} if fell_back else known
    cursor = _scan(out, enabled, stamps, None if fell_back else saved.get("cursor"))
    stamps = {k: [t for t in v if t > now - WINDOW] for k, v in stamps.items()}
    try:
        _write_atomic(state_path, {"cursor": cursor, "events": stamps})
    except (OSError, ValueError):
        pass  # nothing saved: the next run reads the window again
    return _counts(stamps, now, enabled), None


def _counts(
    stamps: dict[str, list[float]], now: float, enabled: tuple[str, ...]
) -> dict[str, EventCount]:
    result = {}
    for kind in enabled:
        recent = [t for t in stamps.get(kind, []) if t > now - WINDOW]
        result[kind] = EventCount(count=len(recent), last=max(recent) if recent else None)
    return result
