"""What the machine itself reports: identity, load, storage, processes.

Nothing here needs Docker or a cluster. These are the fields a panel on a
plain host still fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemInfo:
    """Host identity and uptime, as the panel's header line reports it."""

    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    kernel: str | None = None
    uptime_seconds: float | None = None
    user: str | None = None
    ip_addresses: list[str] = field(default_factory=list)


@dataclass
class FilesystemUsage:
    """One mounted filesystem, as a DISK bar shows it."""

    mountpoint: str
    total: int
    used: int
    percent: float


@dataclass
class ResourceUsage:
    """Memory, swap, CPU and filesystem figures for the RESOURCES block."""

    mem_total: int | None = None
    mem_used: int | None = None
    mem_percent: float | None = None
    swap_total: int | None = None
    swap_used: int | None = None
    swap_percent: float | None = None
    filesystems: list[FilesystemUsage] = field(default_factory=list)
    load_avg: tuple[float, float, float] | None = None
    cpu_count: int | None = None
    cpu_percent: float | None = None
    cpu_per_core: list[float] = field(default_factory=list)


@dataclass
class ProcessInfo:
    """One process in the TOP CPU / TOP RAM lists.

    ``cpu_percent`` is ``None`` rather than ``0.0`` when no sampling window was
    used: a zero is a measurement, and nothing was measured. ``origin`` is the
    systemd unit or the short container ID the process runs under, or ``None``
    where neither could be read -- on Darwin and FreeBSD there is no cgroup to
    read at all.
    """

    pid: int
    name: str
    cpu_percent: float | None = None
    memory_percent: float | None = None
    #: Resident set size in bytes. The figure ``memory_percent`` is computed
    #: from -- psutil's ``memtype`` default is ``rss`` -- so the two are one
    #: quantity in two units rather than two numbers that could disagree.
    memory_bytes: int | None = None
    origin: str | None = None


@dataclass
class ProcessSnapshot:
    """The TOP CPU and TOP RAM lists, with the window they were measured over."""

    top_cpu: list[ProcessInfo] = field(default_factory=list)
    top_memory: list[ProcessInfo] = field(default_factory=list)
    #: The window the CPU figures were measured over, in seconds. A percentage
    #: without its window is an unanswered question, so the renderer shows it.
    sampled: float = 0.0


@dataclass
class UpdateInfo:
    """Pending package updates, where the platform can report them at all."""

    supported: bool = False
    available: int | None = None
    security: int | None = None
    standard: int | None = None
