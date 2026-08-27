"""Docker and Swarm: services, their tasks, what fell over, what it occupies.

The largest of the four, and the reason the model was split: this domain has
its own vocabulary -- a task is not a service, a job is not a replica, a
stopped container is not a failed one -- and reading it beside the system
fields made both harder to hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    #: An explicit grouping key from the configured label. Services of one
    #: stack sharing it render as one row. ``None`` means the label was not
    #: set and the name heuristic decides instead; ``""`` means it was set to
    #: nothing, which groups with no one -- presence, not truthiness, the same
    #: rule ``description`` follows.
    group: str | None = None
    #: Whether this service is nailed to a node by a placement constraint.
    #:
    #: It changes what a collapsed row means. ``3/3`` on a replicated service
    #: says "the orchestrator places three, and it will move them"; the same
    #: figure over pinned instances says "three that cannot go anywhere". Read
    #: as the first, the second borrows a resilience it does not have -- and
    #: that is exactly the property that decides what happens when a node dies.
    pinned: bool = False
    #: Memory held by this service's tasks **on this node**, page cache
    #: already subtracted -- the figure ``docker stats`` prints. ``None``
    #: means nothing of it runs here, which is a different statement from
    #: zero and must render differently.
    memory_bytes: int | None = None
    #: The cgroup limit, summed over the local tasks. ``None`` means no limit
    #: was set -- deliberately not the host's RAM, which is what the Docker
    #: API reports in that case and which would make every service look
    #: comfortable at a fraction of a percent.
    memory_limit: int | None = None
    #: The reservation, likewise summed. Not a limit: exceeding it kills
    #: nothing. It is the figure the orchestrator places against, so a service
    #: living above it makes the cluster's capacity arithmetic wrong while
    #: nothing anywhere turns red.
    memory_reservation: int | None = None
    #: How many of this service's tasks run on this node. It is what the
    #: summed figures above were summed over.
    local_tasks: int = 0


#: How far back a fall still counts. Twelve hours spans a night, so what broke
#: at 03:00 is still on the panel at the login that follows.
#:
#: It lives here rather than in the collector because the renderer states it in
#: the heading. Two copies would be free to drift, and then the block would
#: promise a window it does not apply.
TROUBLE_WINDOW_SECONDS: int = 12 * 3600


#: Severities a TroubleEntry can carry, worst first. The order is the render
#: order: what is down outranks what is still thrashing, which outranks what
#: has already caught itself. A service standing right now is the least urgent
#: of the three and must not push a dead one off a capped list.
TROUBLE_SEVERITIES: tuple[str, ...] = ("dead", "restarting", "recovered")


@dataclass
class TroubleEntry:
    """One service that fell, or never got up, within the reporting window.

    Built from two sources that can answer differently. A local container
    inspect gives a restart count but loses the *cause* the moment the
    container comes back -- measured against Docker 29.7.2, a container that
    failed and recovered reports ``ExitCode 0`` and ``OOMKilled false``. Swarm
    keeps each attempt as its own task, so there the cause survives but the
    count is capped by the history Swarm retains. The renderer keeps that
    difference visible instead of smoothing it over.
    """

    name: str
    node: str | None = None
    #: How often it fell. ``None`` means no counter applies -- a service that
    #: was never placed anywhere has not fallen, it never started.
    fails: int | None = None
    #: Whether *fails* hit Swarm's task history limit and is a floor rather
    #: than a count. Rendered as ``≥``: understating a twelve-fold crash as
    #: fivefold would soften exactly the worst case.
    fails_capped: bool = False
    #: How long the current run has lasted. ``None`` when nothing is running.
    uptime_seconds: float | None = None
    #: Why it fell, where that is still knowable. ``None`` renders as a dash --
    #: Docker overwrote it, and inventing a plausible reason would be worse
    #: than admitting the gap.
    cause: str | None = None
    severity: str = "dead"

    @property
    def rank(self) -> int:
        """Sort key: lower is worse."""
        try:
            return TROUBLE_SEVERITIES.index(self.severity)
        except ValueError:
            return len(TROUBLE_SEVERITIES)


@dataclass
class DockerDiskUsage:
    """What Docker itself occupies on this node, from ``/system/df``.

    Knowingly node-local: the endpoint knows only the daemon it is asked, so
    on a manager these figures describe that manager and nothing else. The
    node name travels with them so the rendered line cannot be misread as a
    statement about the cluster.

    ``reclaimable`` is the figure worth leading with. "Docker occupies 43 GB"
    invites no action; "28 GB of it can be had back" does, and on a node whose
    disk is filling it is usually the answer.
    """

    node: str | None = None
    #: Where the daemon stores its data. Kept so the renderer can find the
    #: filesystem these bytes actually sit on, rather than assuming "/".
    root_dir: str | None = None
    used: int = 0
    reclaimable: int = 0
    images: int = 0
    build_cache: int = 0
    volumes: int = 0
    containers: int = 0
    #: Volumes nothing references any more, and the total. A volume with no
    #: container still holds its bytes, and nothing else on the panel would
    #: ever mention it.
    volumes_unused: int = 0
    volumes_total: int = 0
    #: Why there are no figures, where there are none. ``None`` when the
    #: reading succeeded.
    #:
    #: It exists because the first version rendered "n/a (timeout)" for every
    #: failure, and the first real incident was therefore spent looking for a
    #: timeout that had never happened -- the daemon had answered in 84 ms, in
    #: a response shape the collector did not read. Naming the cause is the
    #: difference between a diagnosis and a guess.
    error: str | None = None


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
    #: Docker's own disk footprint on this node. ``None`` when the reading
    #: could not be taken -- which the renderer states, rather than dropping
    #: the line: a vanished line reads as "nothing to report".
    disk: DockerDiskUsage | None = None
    #: Services that fell, or never started, inside the reporting window.
    #: Empty is the normal state, and the renderer omits the whole block then
    #: rather than printing an empty heading.
    trouble: list[TroubleEntry] = field(default_factory=list)
