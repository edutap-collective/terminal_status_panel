"""Collect resource usage: memory, swap, filesystems, load average."""

from __future__ import annotations

import platform

import psutil

from ..model import FilesystemUsage, ResourceUsage

PSEUDO_FSTYPES: set[str] = {
    "tmpfs",
    "devtmpfs",
    "overlay",
    "squashfs",
    "proc",
    "sysfs",
    "devfs",
    "autofs",
    "cgroup",
    "cgroup2",
    "mqueue",
    "debugfs",
    "tracefs",
}

#: On APFS the sealed system volume mounted at "/" and the writable data volume
#: share one container. "/" therefore reports the container's size against only
#: the system's own usage -- 26% where the machine is in fact 96% full.
DARWIN_DATA_VOLUME = "/System/Volumes/Data"


def _merge_darwin_root(entries: list[FilesystemUsage]) -> list[FilesystemUsage]:
    """Let "/" carry the data volume's figures and drop the data volume.

    Does nothing unless both are present, so it cannot break what it does not
    find -- and nothing at all off Darwin.
    """
    if platform.system() != "Darwin":
        return entries
    by_mount = {entry.mountpoint: entry for entry in entries}
    data = by_mount.get(DARWIN_DATA_VOLUME)
    root = by_mount.get("/")
    if data is None or root is None:
        return entries
    root.total, root.used, root.percent = data.total, data.used, data.percent
    return [entry for entry in entries if entry.mountpoint != DARWIN_DATA_VOLUME]


def _collect_filesystems(ignore_mountpoints: list[str]) -> list[FilesystemUsage]:
    """Real filesystems, merged and filtered, in mount order.

    The order of the two steps is load-bearing: merging after filtering would
    let the Darwin default discard ``/System/Volumes/Data`` before anything read
    its numbers, leaving only the reassuring lie that "/" tells.
    """
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
    result = _merge_darwin_root(result)
    return [
        entry
        for entry in result
        if not any(entry.mountpoint.startswith(p) for p in ignore_mountpoints)
    ]


def _safe(func, default=None):
    try:
        return func()
    except Exception:
        return default


def collect_resources(ignore_mountpoints: list[str] | None = None) -> ResourceUsage:
    """Return resource usage; never raises. Unavailable parts stay None/empty."""
    res = ResourceUsage()
    ignored = list(ignore_mountpoints or [])

    mem = _safe(psutil.virtual_memory)
    if mem is not None:
        res.mem_total = mem.total
        # psutil's own `used` excludes reclaimable memory -- compressed and
        # cached pages the kernel can drop under pressure -- while `percent`
        # is `(total - available) / total`, a different measure entirely.
        # Reporting `used` next to a `percent` derived from `available`
        # produces a row that contradicts itself (e.g. "75.7%" beside a byte
        # figure that only implies 38%). Deriving `used` the same way as
        # `percent` keeps the two numbers describing one thing. This holds
        # on Linux too -- there `used` similarly excludes buffers/cache --
        # so it is unconditional rather than a Darwin-only branch.
        res.mem_used = mem.total - mem.available
        res.mem_percent = mem.percent

    swap = _safe(psutil.swap_memory)
    if swap is not None:
        res.swap_total, res.swap_used, res.swap_percent = swap.total, swap.used, swap.percent

    res.filesystems = _safe(lambda: _collect_filesystems(ignored), []) or []
    res.cpu_count = _safe(psutil.cpu_count)

    # One short sample yields both the per-core and the aggregate figures.
    per_core = _safe(lambda: psutil.cpu_percent(interval=0.15, percpu=True), []) or []
    res.cpu_per_core = list(per_core)
    if res.cpu_per_core:
        res.cpu_percent = sum(res.cpu_per_core) / len(res.cpu_per_core)
    else:
        res.cpu_percent = _safe(lambda: psutil.cpu_percent(interval=0.1))

    load = _safe(lambda: psutil.getloadavg())
    if load is not None:
        res.load_avg = (load[0], load[1], load[2])

    return res
