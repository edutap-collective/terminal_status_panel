"""When Docker's leftovers deserve a Cleanup line, and how loudly.

Two severities, and they mean different things:

* **warning** (yellow) -- leftovers are piling up. Worth a prune at the next
  opportunity; nothing is wrong yet.
* **critical** (red) -- something is wrong. Either the disk holding Docker's
  data is past the critical filesystem threshold with bytes to be had back, or
  unused volumes outnumber the used ones tenfold. The second is never plain
  accumulation: some process creates a volume on every run and nothing ever
  removes it, and the count grows until the inode table or the volume store
  gives out. Measured on 2026-11-18: 2,175 unused volumes beside 3 in use.

Pure: no Rich layout, no I/O, so every rule is testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config, Thresholds
from ..model import DockerDiskUsage, ResourceUsage
from .bars import format_bytes

WARNING = "warning"
CRITICAL = "critical"

#: Colour per severity, shared by the Disk, Objects and Cleanup rows.
STYLES = {WARNING: "yellow", CRITICAL: "red"}

#: Below this a full disk is not a cleanup case: pruning would not free enough
#: to matter, and the line would send a reader off to run a prune that cannot
#: help. The disk row's own colour still says the filesystem is full.
_RECLAIM_WORTH_NAMING = 2**30


@dataclass(frozen=True)
class CleanupFinding:
    """One reason to clean up, as the Cleanup line names it."""

    #: What it is about: ``disk``, ``volumes``, ``containers`` or ``images``.
    subject: str
    severity: str
    text: str


def disk_pressure(
    disk: DockerDiskUsage | None, resources: ResourceUsage | None, thresholds: Thresholds
) -> str | None:
    """How full the filesystem holding Docker's data is, as a severity.

    The mount is found by longest match rather than by assuming "/", because a
    node that gives Docker its own volume is exactly the node where the two
    filesystems disagree. Without filesystem data there is no severity at all
    -- an unmeasured disk is not evidence of comfort, but claiming pressure
    would be a finding nobody took.
    """
    if disk is None or disk.error or resources is None or not disk.root_dir:
        return None
    best = None
    for fs in resources.filesystems:
        base = fs.mountpoint.rstrip("/")
        if disk.root_dir == fs.mountpoint or disk.root_dir.startswith(f"{base}/"):
            if best is None or len(fs.mountpoint) > len(best.mountpoint):
                best = fs
    if best is None or best.percent is None:
        return None
    if best.percent > thresholds.filesystem_critical:
        return CRITICAL
    if best.percent > thresholds.filesystem_warning:
        return WARNING
    return None


def _disk_finding(disk, resources, thresholds) -> CleanupFinding | None:
    pressure = disk_pressure(disk, resources, thresholds)
    if pressure is None or disk.reclaimable < _RECLAIM_WORTH_NAMING:
        return None
    return CleanupFinding(
        "disk", pressure, f"Docker disk full, {format_bytes(disk.reclaimable)} reclaimable"
    )


def _volume_finding(disk, cfg: Config) -> CleanupFinding | None:
    rules = cfg.docker_cleanup
    unused = disk.volumes_unused
    in_use = disk.volumes_total - unused
    if unused < rules.unused_volumes:
        return None
    if unused >= rules.unused_volumes_ratio * max(in_use, 1):
        factor = unused // max(in_use, 1)
        return CleanupFinding(
            "volumes", CRITICAL, f"{unused} unused volumes, {factor}× the {in_use} in use"
        )
    if unused > in_use:
        return CleanupFinding("volumes", WARNING, f"{unused} unused volumes ({in_use} in use)")
    return None


def _container_finding(disk, cfg: Config) -> CleanupFinding | None:
    stopped = disk.containers_stopped
    if stopped is None or stopped < cfg.docker_cleanup.stopped_containers:
        return None
    return CleanupFinding("containers", WARNING, f"{stopped} stopped containers")


def _image_finding(disk, cfg: Config) -> CleanupFinding | None:
    rules = cfg.docker_cleanup
    parts = []
    if disk.images_dangling is not None and disk.images_dangling >= rules.dangling_images:
        parts.append(f"{disk.images_dangling} dangling images")
    unused = disk.images_unused
    if unused is not None and disk.images_total is not None:
        in_use = disk.images_total - unused
        if unused >= rules.unused_images and unused > in_use:
            parts.append(f"{unused} unused images ({format_bytes(disk.images_reclaimable)})")
    if not parts:
        return None
    return CleanupFinding("images", WARNING, ", ".join(parts))


def cleanup_findings(
    disk: DockerDiskUsage | None, resources: ResourceUsage | None, cfg: Config
) -> list[CleanupFinding]:
    """Every reason to clean up this node, worst first.

    A failed reading yields none: the Disk row already says n/a and why, and a
    rule evaluated on the zeros of a reading that never happened would invent
    a clean bill of health -- or, worse, a finding.
    """
    if disk is None or disk.error:
        return []
    found = [
        _disk_finding(disk, resources, cfg.thresholds),
        _volume_finding(disk, cfg),
        _container_finding(disk, cfg),
        _image_finding(disk, cfg),
    ]
    findings = [f for f in found if f is not None]
    # Stable: equal severities keep the order above, disk first.
    return sorted(findings, key=lambda f: f.severity != CRITICAL)
