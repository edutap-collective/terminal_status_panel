"""``status-collect``: one collection, one state file, exit.

Run by a systemd timer as an unprivileged dynamic user (see
``data/terminal-status-panel-collect.service.in``). systemd hands it
``RUNTIME_DIRECTORY`` (the world-readable state file, the keepalived dump)
and ``STATE_DIRECTORY`` (cursor and totals between runs).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..model.host import HostState
from ..render.layout import panel_version
from . import apache, apt, journal, vrrp

DEFAULT_RUNTIME_DIR = "/run/terminal-status-panel"
DEFAULT_STATE_DIR = "/var/lib/terminal-status-panel"
STATE_FILE_NAME = "host.json"
#: The copy of keepalived's dump the root-side helper hands over (see keepalived-dump).
DUMP_FILE_NAME = "keepalived.json"


@dataclass
class Sources:
    """The four sources, injectable for tests."""

    vrrp: Callable[..., Any] = vrrp.collect_vrrp
    apache: Callable[..., Any] = apache.collect_apache
    updates: Callable[..., Any] = apt.collect_updates
    events: Callable[..., Any] = journal.collect_events


def write_state(state: HostState, path: Path) -> None:
    """Write *state* to *path* so that a reader never sees half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _previous(path: Path) -> HostState | None:
    """The state the last run wrote, or None when it cannot be read as one."""
    try:
        return HostState.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


def _guarded(source: Callable[..., Any], *args: Any) -> tuple[Any, str | None]:
    """Call one source; an exception from it becomes the error code ``internal``.

    Every source runs through here so that no single failure, whatever its
    cause, can stop the state file from being written.
    """
    try:
        return source(*args)
    except Exception:
        return None, "internal"


def run(
    cfg: Config,
    runtime_dir: Path,
    state_dir: Path,
    now: float,
    sources: Sources | None = None,
) -> HostState:
    """Collect every enabled source, write the state file, return what was written."""
    sources = sources or Sources()
    target = runtime_dir / STATE_FILE_NAME
    dump = runtime_dir / DUMP_FILE_NAME
    previous = _previous(target)
    errors: dict[str, str | None] = {"vrrp": None, "apache": None, "updates": None, "events": None}
    state = HostState(written_at=now, collector=panel_version())
    try:
        if "vrrp" in cfg.health.enabled:
            state.vrrp, errors["vrrp"] = _guarded(sources.vrrp, dump)
        if "apache" in cfg.health.enabled and cfg.health.apache.url:
            state.apache, errors["apache"] = _guarded(
                sources.apache, cfg.health.apache.url, state_dir / "apache.json", now
            )
        state.updates, errors["updates"] = _guarded(
            sources.updates, previous.updates if previous else None, now
        )
        events, errors["events"] = _guarded(
            sources.events, state_dir / "journal.json", now, cfg.trouble_host_events
        )
        state.events = events or {}
    finally:
        # The helper runs before every collection, whether vrrp is enabled or
        # not, and the copy holds the VRRP password: it never outlives a run,
        # whatever happened to the VRRP source.
        if vrrp.remove_dump(dump):
            errors["vrrp"] = "cleanup"
    state.errors = errors
    write_state(state, target)
    return state


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``status-collect``. Exits 0 unless the state cannot be written."""
    parser = argparse.ArgumentParser(prog="status-collect")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    runtime_dir = Path(os.environ.get("RUNTIME_DIRECTORY", DEFAULT_RUNTIME_DIR))
    state_dir = Path(os.environ.get("STATE_DIRECTORY", DEFAULT_STATE_DIR))
    try:
        run(cfg, runtime_dir, state_dir, now=time.time())
    except OSError:
        # One line, the target path only: the exception text is not printed.
        print(f"status-collect: cannot write {runtime_dir / STATE_FILE_NAME}", file=sys.stderr)
        return 1
    return 0
