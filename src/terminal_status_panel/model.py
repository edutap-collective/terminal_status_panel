"""Dataclasses shared by collectors and renderers.

All aggregate fields default to empty/None so a failed collector degrades
gracefully instead of raising.
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


@dataclass
class ServiceTask:
    """One task of a service, and the node Swarm placed it on."""

    node: str | None  # hostname running the task, or None when unassigned
    state: str  # actual task state: running / preparing / failed / ...

    # The states Docker walks through before `running`. A task in one of them
    # has not been measured yet -- rendering it as 💀 claims a failure that
    # nobody observed.
    _STARTING = frozenset(
        {"new", "allocated", "pending", "assigned", "accepted", "preparing", "ready", "starting"}
    )

    @property
    def running(self) -> bool:
        """True while this task is the one actually serving."""
        return self.state == "running"

    @property
    def starting(self) -> bool:
        """True while the task is on its way up and nothing is measured yet."""
        return self.state in self._STARTING


@dataclass
class JobRun:
    """The most recent task of a job: what it did, and how long ago.

    An *age* rather than a timestamp, for the same reason ``SystemInfo`` keeps
    ``uptime_seconds``: the measurement happens in the collector, and a renderer
    that subtracted its own clock could disagree with the one that measured.
    """

    state: str  # complete / failed / rejected / running / ...
    age_seconds: float | None = None
    node: str | None = None  # hostname the run happened on

    @property
    def ok(self) -> bool:
        """True when the run finished the work it was started for."""
        return self.state == "complete"

    @property
    def failed(self) -> bool:
        """True when the run ended in a state Swarm counts as a failure."""
        return self.state in ("failed", "rejected", "orphaned")


@dataclass
class ServiceStatus:
    """One DOCKER INFOS row: a service, its replicas and its placement."""

    name: str
    running_replicas: int
    desired_replicas: int | None
    critical: bool = False
    stack: str | None = None  # com.docker.stack.namespace label
    description: str | None = None  # from a curated service label
    tasks: list[ServiceTask] = field(default_factory=list)  # per-node placement
    unassigned: int = 0  # desired-running tasks with no node
    #: A service that is meant to run to completion rather than stay up. For
    #: those, "no task running" is the normal resting state, not an outage.
    job: bool = False
    schedule: str | None = None  # cron expression, when the job carries one
    last_run: JobRun | None = None  # None when the job has never run
    #: The image reference this service runs, digest removed but otherwise
    #: whole: ``registry.example.org:5005/group/app:2026-08-14_1206``. Kept
    #: unshortened on purpose -- which part of it is worth the width is a
    #: rendering question, and a model that stored the shortened form could not
    #: answer the fuller one later.
    image: str | None = None


@dataclass
class SwarmNode:
    """One node of the Swarm, as its managers describe it."""

    name: str
    reachable: bool = False
    role: str | None = None  # manager / worker
    leader: bool = False
    state: str | None = None  # raw node state (ready / down / ...)
    availability: str | None = None  # active / pause / drain (Spec.Availability)
    #: The engine this node runs, from ``Description.Engine.EngineVersion``.
    #: ``None`` where the daemon did not report one, which must render as
    #: nothing rather than as agreement with the others -- a node whose version
    #: is unknown has not been shown to match.
    engine_version: str | None = None
    #: Whether the other managers can reach this one, from
    #: ``ManagerStatus.Reachability``. Tri-state on purpose: a worker has no
    #: ``ManagerStatus`` at all, and ``None`` there means *not applicable*.
    #: Collapsing that into ``False`` would report every worker in the cluster
    #: as an unreachable manager.
    #:
    #: This is a different question from ``reachable``, which is the manager's
    #: view of the node as a whole. A node can be ``ready`` to the orchestrator
    #: and unreachable to its fellow managers at the same time, and that
    #: combination is precisely the one worth showing: it is a quorum risk that
    #: otherwise renders as a healthy tick.
    reachable_by_managers: bool | None = None

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
    """What the Docker daemon reports: the Swarm, its nodes, its services."""

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
    #: Container ID to the service name DOCKER INFOS shows for it. Collected
    #: while the containers are being listed anyway; the process rows use it to
    #: turn a cgroup's bare container ID into a name a reader recognises.
    container_services: dict[str, str] = field(default_factory=dict)
    #: This host's own engine version, from ``info()``. It matters only where
    #: ``nodes`` is empty because the host could not enumerate the swarm -- a
    #: worker may not -- and it is then the sole version there is. The renderer
    #: marks it as local for that reason: an unqualified version in the swarm
    #: header reads as a statement about the swarm, and on a worker that would
    #: be exactly wrong.
    local_engine_version: str | None = None


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
    """Whether one configured peer answered, and how it was asked."""

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
    """One name-resolution check and its verdict."""

    label: str
    ok: bool | None = None  # None = warning (inconsistent, not broken)
    detail: str = ""


@dataclass
class HealthInfo:
    """The CLUSTER HEALTH section: clustered services, peers and DNS checks."""

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
    """One middleware attached to a router."""

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
    """One Traefik router: what it matches, and where it forwards."""

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
    """The Traefik wiring as configured: entrypoints, routers, services."""

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
    # Set only when neither the Swarm services nor the container listing
    # could be read -- the one case with genuinely nothing to show. Either
    # one failing alone is a partial read, recorded below instead.
    error: str | None = None
    # A partial failure: the labels were read but the file provider was not.
    # Distinct from `error`, which means nothing could be read at all.
    file_provider_error: str | None = None
    # Labels were read from services but not from containers. Distinct from
    # `error`, which means nothing could be read at all.
    container_error: str | None = None
    # Labels were read from containers but not from Swarm services. Distinct
    # from `error`, which means nothing could be read at all. Expected and
    # permanent on a host with no Swarm manager to ask -- see how the
    # renderer decides whether this is worth a line.
    service_error: str | None = None


@dataclass
class PanelData:
    """Everything the collectors gathered, ready for the renderers."""

    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
    updates: UpdateInfo | None = None
    health: HealthInfo | None = None
    traefik: TraefikInfo | None = None
    processes: ProcessSnapshot | None = None
