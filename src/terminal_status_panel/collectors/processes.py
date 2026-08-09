"""Who is using the machine: the top processes by CPU and by memory.

``ps -eo %cpu`` answers a different question than the one asked at a login
prompt. Its figure is a lifetime average -- CPU time divided by elapsed time
since the process started -- so a container running for weeks barely moves,
whatever it is doing right now. This module samples instead: it primes every
process, waits, and reads, so the number is the share of CPU used during that
window.

It also excludes itself. The measuring process is the one guaranteed to be
running while the measurement happens, which is why `ps` habitually ranks its
own process first.
"""

from __future__ import annotations

import os
import re
import time

import psutil

from ..model import ProcessInfo, ProcessSnapshot

#: Overridden in tests. Reading it through a module attribute rather than
#: hard-coding "/proc" is what lets the cgroup parser be tested on a machine
#: that has no /proc at all.
PROC = "/proc"

#: `0::/system.slice/docker-<64 hex>.scope` -- the shape Docker gives a
#: container's cgroup under systemd. Only the ID is in there; turning it into
#: a service name needs the Docker socket, which is the renderer's job.
_DOCKER_SCOPE = re.compile(r"docker-(?P<id>[0-9a-f]{12,64})\.scope")

#: `0::/system.slice/glusterd.service` -- a plain systemd unit, which needs no
#: resolving at all.
_SYSTEMD_UNIT = re.compile(r"/(?P<unit>[^/]+\.service)\b")


def cgroup_origin(pid: int) -> str | None:
    """The systemd unit or short container ID this process runs under.

    ``None`` where the cgroup says neither -- a user session, a kernel thread,
    or a platform without ``/proc`` at all. Absent rather than guessed: naming
    a service the file did not name is the failure mode this whole panel is
    built to avoid.
    """
    try:
        with open(f"{PROC}/{pid}/cgroup", encoding="utf-8") as handle:
            line = handle.readline()
    except (OSError, ValueError):
        return None
    scope = _DOCKER_SCOPE.search(line)
    if scope:
        return f"container {scope.group('id')[:12]}"
    unit = _SYSTEMD_UNIT.search(line)
    if unit:
        return unit.group("unit")
    return None


def _sample(procs: list, sample: float) -> None:
    """Prime every process, then wait out the window.

    ``cpu_percent(None)`` returns the share since the previous call on that
    same object, so the first call is worthless and only sets the baseline.
    Priming every process before sleeping -- rather than sleeping per process
    -- is what keeps the whole measurement to one window instead of one per
    process.
    """
    for proc in procs:
        try:
            proc.cpu_percent(None)
        except psutil.Error:
            continue
    time.sleep(sample)


def collect_processes(sample: float, limit: int = 5) -> ProcessSnapshot | None:
    """The top *limit* processes by CPU and by memory. Never raises.

    ``None`` only when nothing at all could be read. A process that vanishes
    mid-sample, or refuses to be inspected, is skipped and the rest are
    reported: a partial answer is still an answer, and this runs on a login
    path where an exception would cost the shell.
    """
    own = os.getpid()
    try:
        procs = [proc for proc in psutil.process_iter() if proc.pid != own]
    except Exception:
        return None

    sampling = sample > 0
    if sampling:
        _sample(procs, sample)

    rows: list[ProcessInfo] = []
    for proc in procs:
        try:
            rows.append(ProcessInfo(
                pid=proc.pid,
                name=proc.name(),
                cpu_percent=proc.cpu_percent(None) if sampling else None,
                memory_percent=proc.memory_percent(),
                memory_bytes=proc.memory_info().rss,
                origin=cgroup_origin(proc.pid),
            ))
        except (psutil.Error, OSError):
            continue

    top_cpu = (
        sorted(rows, key=lambda row: row.cpu_percent or 0.0, reverse=True)[:limit]
        if sampling
        else []
    )
    top_memory = sorted(
        rows, key=lambda row: row.memory_percent or 0.0, reverse=True
    )[:limit]
    return ProcessSnapshot(
        top_cpu=top_cpu,
        top_memory=top_memory,
        sampled=sample if sampling else 0.0,
    )
