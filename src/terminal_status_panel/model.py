"""Dataclasses shared by collectors and renderers.

All aggregate fields default to empty/None so a failed collector degrades
gracefully instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemInfo:
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    kernel: str | None = None
    uptime_seconds: float | None = None
    user: str | None = None
    ip_addresses: list[str] = field(default_factory=list)


@dataclass
class FilesystemUsage:
    mountpoint: str
    total: int
    used: int
    percent: float


@dataclass
class ResourceUsage:
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
class UpdateInfo:
    supported: bool = False
    available: int | None = None
    security: int | None = None
    standard: int | None = None


@dataclass
class ServiceTask:
    node: str | None  # hostname running the task, or None when unassigned
    state: str  # actual task state: running / preparing / failed / ...

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass
class ServiceStatus:
    name: str
    running_replicas: int
    desired_replicas: int | None
    critical: bool = False
    stack: str | None = None  # com.docker.stack.namespace label
    description: str | None = None  # from a curated service label
    tasks: list[ServiceTask] = field(default_factory=list)  # per-node placement
    unassigned: int = 0  # desired-running tasks with no node


@dataclass
class SwarmNode:
    name: str
    reachable: bool = False
    role: str | None = None  # manager / worker
    leader: bool = False
    state: str | None = None  # raw node state (ready / down / ...)
    availability: str | None = None  # active / pause / drain (Spec.Availability)

    @property
    def operational(self) -> bool:
        """Ready *and* accepting tasks.

        A drained or paused node still reports ``ready`` — it talks to the
        managers but runs no tasks, so it must not be shown as healthy.
        ``None`` means the daemon did not report availability; treat that as
        active so behaviour is unchanged against older daemons.
        """
        return self.reachable and self.availability in (None, "active")


@dataclass
class SwarmInfo:
    reachable: bool = False
    enabled: bool = False
    node_role: str | None = None
    node_count: int | None = None
    services: list[ServiceStatus] = field(default_factory=list)
    nodes: list[SwarmNode] = field(default_factory=list)


@dataclass
class ClusterMember:
    """One member of a clustered infrastructure service.

    ``healthy`` is tri-state on purpose: ``None`` means *not observable*. The
    MongoDB probe, for instance, learns the set members but not their state,
    and the panel must not render an unmeasured ✅.
    """

    name: str
    node: str | None = None  # derived Swarm hostname, when derivable
    role: str | None = None  # primary / secondary / leader / voter / observer / peer
    healthy: bool | None = None
    detail: str | None = None  # kind-specific: LSN, brick path, endpoint
    warning: str | None = None  # short note: "lag", "→ primary"


@dataclass
class ClusterService:
    """State of one clustered infrastructure service as seen from this node."""

    kind: str  # postgres | mongodb | kafka | glusterfs | rustfs
    name: str | None = None  # PostgreSQL-18, lrz_app, cluster id, volume name
    applicable: bool = True  # False when this node runs no member
    reachable: bool = False
    leader: str | None = None  # primary / controller leader; None when leaderless
    quorum_ok: bool | None = None
    detail: str | None = None  # service-level note, e.g. Kafka follower lag
    members: list[ClusterMember] = field(default_factory=list)
    error: str | None = None


@dataclass
class PeerReachability:
    name: str
    method: str  # wireguard | tcp
    ok: bool = False
    detail: str | None = None  # handshake age or probed port


@dataclass
class DnsCheck:
    label: str
    ok: bool | None = None  # None = warning (inconsistent, not broken)
    detail: str = ""


@dataclass
class HealthInfo:
    clusters: list[ClusterService] = field(default_factory=list)
    peers: list[PeerReachability] = field(default_factory=list)
    dns: list[DnsCheck] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    # False when the clusters check never ran at all (no Docker client, or no
    # kinds enabled). An empty ``clusters`` list means "nothing found" only
    # when this is True; otherwise it means "not attempted".
    clusters_probed: bool = False
    # False when there was nothing to probe: no peer names and no WireGuard
    # answer. An empty ``peers`` list means "no peers" only when this is True.
    peers_probed: bool = False


@dataclass
class PanelData:
    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
    updates: UpdateInfo | None = None
    health: HealthInfo | None = None
