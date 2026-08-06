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

    # The states Docker walks through before `running`. A task in one of them
    # has not been measured yet -- rendering it as 💀 claims a failure that
    # nobody observed.
    _STARTING = frozenset({"new", "allocated", "pending", "assigned",
                           "accepted", "preparing", "ready", "starting"})

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def starting(self) -> bool:
        return self.state in self._STARTING


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
    # Plain and Compose containers, kept apart from Swarm services so the
    # SWARM summary line can stay honest about what the Swarm actually runs.
    # render/traefik.py matches router targets against both lists.
    containers: list[ServiceStatus] = field(default_factory=list)
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
    name: str | None = None  # stack name, cluster id, volume name
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
    rx_bytes: int | None = None  # None for the tcp fallback, which cannot know
    tx_bytes: int | None = None
    endpoint: str | None = None  # as WireGuard resolved it, host:port
    family: str | None = None  # IPv4 | IPv6, derived from endpoint

    @property
    def one_way(self) -> bool:
        """We send and nothing comes back.

        The signature of a filtered or mis-keyed tunnel, and a different fault
        from a peer that is simply gone — there both counters stand still. The
        distinction decides where to look: a packet filter and a key mismatch
        both produce this, a dead host does not.

        Both counters must be present. A missing one means the transport could
        not be read (the TCP fallback, or a malformed dump), and answering from
        that would turn absent data into a diagnosis.
        """
        if self.rx_bytes is None or self.tx_bytes is None:
            return False
        return self.tx_bytes > 0 and self.rx_bytes == 0


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
    # False when no collector produced this HealthInfo at all — the renderer's
    # own fallback for a missing section, or a hand-built instance. Unlike the
    # two flags above it cannot distinguish anything within a real run:
    # ``collect_health`` always registers the DNS check, so a collected
    # HealthInfo always has this True. It exists so that an empty ``dns`` list
    # on a default instance cannot read as "checked, nothing configured".
    dns_probed: bool = False


@dataclass
class TraefikEntrypoint:
    """A port Traefik listens on, as declared in its static configuration."""

    name: str
    address: str  # ":2020"
    port: int | None = None


@dataclass
class TraefikMiddleware:
    name: str
    kind: str | None = None  # stripprefix, headers, …
    detail: str | None = None  # the first configured key, for display


@dataclass
class TraefikServiceRef:
    """What a router points at — a Docker service, or one of Traefik's own."""

    name: str
    port: int | None = None
    scheme: str | None = None
    docker_service: str | None = None  # the Swarm service backing it, when known
    source: str = "swarm"  # swarm | file
    # Where a file-provider service sends traffic. Docker knows nothing about
    # these, so they are shown rather than measured.
    upstreams: list[str] = field(default_factory=list)


@dataclass
class TraefikRouter:
    name: str
    entrypoints: list[str] = field(default_factory=list)
    rule: str | None = None
    middlewares: list[str] = field(default_factory=list)
    service: str | None = None
    tls: bool = False
    source: str = "swarm"  # swarm | file
    origin: str | None = None  # the Docker service, container, or config it was read from
    # None means the Traefik API was never asked. It must not render as
    # "accepted": not consulted is not the same as confirmed.
    rejected: bool | None = None


@dataclass
class TraefikInfo:
    reachable: bool = False
    entrypoints: list[TraefikEntrypoint] = field(default_factory=list)
    routers: list[TraefikRouter] = field(default_factory=list)
    middlewares: dict[str, TraefikMiddleware] = field(default_factory=dict)
    services: dict[str, TraefikServiceRef] = field(default_factory=dict)
    api_consulted: bool = False
    # The entrypoint Traefik answers its own health check on. It carries no
    # router by design, which is the one case where "— no router" is not a
    # finding.
    ping_entrypoint: str | None = None
    error: str | None = None
    # A partial failure: the labels were read but the file provider was not.
    # Distinct from `error`, which means nothing could be read at all.
    file_provider_error: str | None = None
    # Labels were read from services but not from containers. Distinct from
    # `error`, which means nothing could be read at all.
    container_error: str | None = None


@dataclass
class PanelData:
    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
    updates: UpdateInfo | None = None
    health: HealthInfo | None = None
    traefik: TraefikInfo | None = None
