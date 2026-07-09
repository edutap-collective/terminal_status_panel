"""Collect resource usage: memory, swap, filesystems, load average."""

from __future__ import annotations

import psutil

from ..model import FilesystemUsage, ResourceUsage

PSEUDO_FSTYPES: set[str] = {
    "tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "devfs",
    "autofs", "cgroup", "cgroup2", "mqueue", "debugfs", "tracefs",
}


def _collect_filesystems() -> list[FilesystemUsage]:
    result: list[FilesystemUsage] = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in PSEUDO_FSTYPES or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        result.append(
            FilesystemUsage(
                mountpoint=part.mountpoint,
                total=usage.total,
                used=usage.used,
                percent=usage.percent,
            )
        )
    return result


def _safe(func, default=None):
    try:
        return func()
    except Exception:
        return default


def collect_resources() -> ResourceUsage:
    """Return resource usage; never raises. Unavailable parts stay None/empty."""
    res = ResourceUsage()

    mem = _safe(psutil.virtual_memory)
    if mem is not None:
        res.mem_total, res.mem_used, res.mem_percent = mem.total, mem.used, mem.percent

    swap = _safe(psutil.swap_memory)
    if swap is not None:
        res.swap_total, res.swap_used, res.swap_percent = swap.total, swap.used, swap.percent

    res.filesystems = _safe(_collect_filesystems, []) or []
    res.cpu_count = _safe(psutil.cpu_count)

    load = _safe(lambda: psutil.getloadavg())
    if load is not None:
        res.load_avg = (load[0], load[1], load[2])

    return res
