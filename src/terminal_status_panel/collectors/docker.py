"""Collect Docker Swarm health via the Docker SDK.

Everything here comes from the Docker/Swarm API — node state, service replica
counts, the ``com.docker.stack.namespace`` grouping label, a curated
description label, and per-task node placement. No database or broker protocol
is ever spoken to.

Time-boxed and exception-safe: any failure yields SwarmInfo(reachable=False)
so a missing or hung Docker socket can never block login.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import NamedTuple

import docker

from ..config import DEFAULT_DESCRIPTION_LABEL, LEGACY_DESCRIPTION_LABEL
from ..model import (
    TROUBLE_WINDOW_SECONDS,
    DockerDiskUsage,
    JobRun,
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    SwarmNode,
    TroubleEntry,
)
from ._labels import (
    COMPOSE_PROJECT_LABEL,
    SWARM_SERVICE_LABEL,
    compose_identity,
    container_labels,
)

STACK_LABEL = "com.docker.stack.namespace"

#: Label prefix used by swarm-cronjob (https://github.com/crazy-max/swarm-cronjob),
#: which drives a Swarm service on a cron schedule by scaling it up and letting
#: it exit again. Such a service sits at zero replicas between runs.
SWARM_CRONJOB_PREFIX = "swarm.cronjob"

#: Swarm's own job modes (Docker 20.10+). A service in one of them is expected
#: to end, so its resting state is zero running tasks.
_JOB_MODES = ("ReplicatedJob", "GlobalJob")

#: Raw Docker states a container without Compose labels must be in to appear at
#: all. Anything else is a leftover from a one-off `docker run`, and on a
#: development machine those accumulate without end.
_LIVE_STATES = frozenset({"running", "restarting"})


def _node_map(client) -> tuple[list[SwarmNode], dict[str, str]]:
    """Return (node list, node-id → hostname map)."""
    nodes: list[SwarmNode] = []
    id_to_name: dict[str, str] = {}
    try:
        raw_nodes = client.nodes.list()
    except Exception:
        return nodes, id_to_name
    for node in raw_nodes:
        attrs = getattr(node, "attrs", {}) or {}
        name = attrs.get("Description", {}).get("Hostname") or getattr(node, "id", "?")
        node_id = attrs.get("ID") or getattr(node, "id", "")
        if node_id:
            id_to_name[node_id] = name
        manager = attrs.get("ManagerStatus") or {}
        state = attrs.get("Status", {}).get("State")
        spec = attrs.get("Spec", {})
        # Absence and unreachability are kept apart. A worker carries no
        # ManagerStatus, so the key is missing rather than negative, and
        # reading that as "unreachable" would report every worker in the
        # cluster as a broken manager. Only a manager that actually reported a
        # reachability gets a boolean here.
        reachability = manager.get("Reachability")
        nodes.append(
            SwarmNode(
                name=name,
                reachable=state == "ready",
                role=spec.get("Role"),
                leader=bool(manager.get("Leader", False)),
                state=state,
                # active / pause / drain — a drained node is ready but idle.
                availability=spec.get("Availability"),
                engine_version=(attrs.get("Description", {}).get("Engine") or {}).get(
                    "EngineVersion"
                ),
                reachable_by_managers=(
                    None if reachability is None else reachability == "reachable"
                ),
            )
        )
    nodes.sort(key=lambda n: n.name)  # stable, alphabetical hostname order
    return nodes, id_to_name


def _labels(service) -> dict:
    spec = (getattr(service, "attrs", {}) or {}).get("Spec", {})
    labels = dict(spec.get("Labels") or {})
    container = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
    for key, value in (container.get("Labels") or {}).items():
        labels.setdefault(key, value)
    return labels


def _without_digest(reference: str | None) -> str | None:
    """An image reference with its pinned digest removed.

    Swarm rewrites every reference to ``tag@sha256:...`` when the service is
    created, and Compose does the same once an image has been pulled. Those 71
    characters are identical for every service running the same tag, so they
    buy nothing a panel row could show and cost more width than the whole rest
    of the reference.
    """
    if not reference:
        return None
    return reference.split("@", 1)[0] or None


def _service_image(service) -> str | None:
    """The image the service's task template runs, or None."""
    spec = (getattr(service, "attrs", {}) or {}).get("Spec", {})
    container = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
    return _without_digest(container.get("Image"))


def _container_image(container) -> str | None:
    """The image the container was started from, or None.

    ``Config.Image`` is the reference as it was written -- the name a reader
    recognises. ``attrs["Image"]`` names the same image as a resolved sha256
    ID, which identifies it exactly and says nothing to anyone reading a panel.
    """
    attrs = getattr(container, "attrs", {}) or {}
    return _without_digest((attrs.get("Config") or {}).get("Image"))


def _job_schedule(labels: dict) -> str | None:
    return labels.get(f"{SWARM_CRONJOB_PREFIX}.schedule")


def _is_job(service, labels: dict) -> bool:
    """Whether this service runs to completion rather than staying up.

    Two independent sources say so, and either is enough: the swarm-cronjob
    labels, and Swarm's own job modes (Docker 20.10+). The latter carry no
    schedule -- something outside the cluster decides when they run -- so the
    two are read separately rather than one implying the other.
    """
    if str(labels.get(f"{SWARM_CRONJOB_PREFIX}.enable", "")).lower() == "true":
        return True
    mode = (getattr(service, "attrs", {}) or {}).get("Spec", {}).get("Mode") or {}
    return any(job_mode in mode for job_mode in _JOB_MODES)


def _now() -> float:
    """Wall clock as epoch seconds. A seam, so tests can fix "now"."""
    return time.time()


def _parse_timestamp(value) -> float | None:
    """Epoch seconds from a Docker task timestamp, or None if unreadable.

    Docker sends RFC 3339 with nanosecond precision and a trailing ``Z``
    (``2026-08-12T07:28:29.81745826Z``). Measured on 2026-08-12: CPython 3.11
    through 3.14 all parse that as-is, nine-digit fraction included, so every
    version this package supports is covered without hand-rolled parsing.
    """
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        # Docker reports UTC. Letting a zone-less stamp default to local time
        # would shift every age by the host's offset, silently and only off UTC.
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.timestamp()


def _last_run(service, id_to_name: dict[str, str]) -> JobRun | None:
    """The newest task of a job, whatever state it ended in.

    Newest by timestamp, deliberately: a job that failed yesterday and
    succeeded this morning is healthy, and picking the most *severe* task
    instead would pin yesterday's failure to the panel for as long as Swarm
    keeps the history. Tasks are listed unfiltered here -- a finished job has
    no desired-state ``running`` task left, which is the whole point.
    """
    try:
        tasks = service.tasks()
    except Exception:
        return None
    newest = None
    newest_at: float | None = None
    for task in tasks:
        status = task.get("Status") or {}
        at = _parse_timestamp(status.get("Timestamp"))
        if at is None:
            continue  # undatable, so it cannot be compared against the others
        if newest_at is None or at > newest_at:
            newest, newest_at = task, at
    if newest is None or newest_at is None:
        return None
    node_id = newest.get("NodeID")
    return JobRun(
        state=(newest.get("Status") or {}).get("State", "unknown"),
        age_seconds=max(0.0, _now() - newest_at),
        node=id_to_name.get(node_id, node_id) if node_id else None,
    )


def _desired_count(service) -> int | None:
    try:
        return service.attrs["Spec"]["Mode"]["Replicated"]["Replicas"]
    except (KeyError, TypeError):
        return None  # e.g. global-mode services


class _TaskFacts(NamedTuple):
    """What one filtered task listing yields, beyond the placement itself.

    The last two fields exist so the trouble pre-selection and the
    "never placed" case are answered from the listing the collector makes
    anyway, rather than from a second call per service.
    """

    tasks: list[ServiceTask]
    unassigned: int
    #: When the youngest running task last changed state -- close enough to
    #: "since when is this up". None where nothing is running.
    newest_running_at: float | None
    #: Why an unplaced task is unplaced, verbatim from Swarm: "no suitable
    #: node (insufficient memory on 3 nodes)". This is the sentence people
    #: open an SSH session to read.
    placement_error: str | None


def _service_tasks(service, id_to_name: dict[str, str]) -> _TaskFacts:
    """Facts about the tasks that *should* be running.

    Mirrors the operations script: filter to desired-state ``running``, split
    into node-assigned tasks (reporting their *actual* state) and orphaned
    tasks with no node (e.g. pinned to a node that is down).
    """
    try:
        all_tasks = service.tasks(filters={"desired-state": "running"})
    except Exception:
        return _TaskFacts([], 0, None, None)
    tasks: list[ServiceTask] = []
    unassigned = 0
    newest_running_at: float | None = None
    placement_error: str | None = None
    for t in all_tasks:
        node_id = t.get("NodeID")
        status = t.get("Status", {})
        state = status.get("State", "unknown")
        if node_id:
            tasks.append(ServiceTask(node=id_to_name.get(node_id, node_id), state=state))
            if state == "running":
                at = _parse_timestamp(status.get("Timestamp"))
                if at is not None and (newest_running_at is None or at > newest_running_at):
                    newest_running_at = at
        else:
            unassigned += 1
            if placement_error is None:
                placement_error = (status.get("Err") or "").strip() or None
    tasks.sort(key=lambda task: task.node or "")
    return _TaskFacts(tasks, unassigned, newest_running_at, placement_error)


#: Task states Swarm uses for an attempt that went wrong. The same set
#: ``JobRun.failed`` names, and deliberately excluding ``shutdown`` and
#: ``complete``: a rolling update ends its old tasks cleanly, and counting
#: those would report every ordinary image bump as a crash.
_FAILED_TASK_STATES = frozenset({"failed", "rejected", "orphaned"})


def _retention_limit(client) -> int | None:
    """How many historic tasks Swarm keeps per slot, or None if unreadable.

    It bounds every count taken from the history: at the Docker default of 5,
    a service that fell twelve times is indistinguishable from one that fell
    five. Knowing the bound is what lets the renderer say ``≥`` instead of
    quietly understating the worst case. Unreadable is not fatal -- the count
    is then reported as it stands, which is what was actually seen.
    """
    try:
        orchestration = client.swarm.attrs["Spec"]["Orchestration"]
        limit = int(orchestration["TaskHistoryRetentionLimit"])
    except Exception:
        return None
    return limit if limit > 0 else None


def _task_cause(status: dict) -> str | None:
    """Why one finished task ended, from what Swarm kept about it."""
    exit_code = ((status.get("ContainerStatus") or {}).get("ExitCode")) or 0
    error = (status.get("Err") or "").strip()
    if exit_code:
        return f'exit {exit_code} · "{error}"' if error else f"exit {exit_code}"
    return error or None


def _failed_history(service, id_to_name: dict[str, str]) -> tuple[int, str | None, str | None]:
    """(failures in the window, newest cause, newest node) from the task history.

    The one expensive call in this module, which is why only pre-selected
    services reach it. It is also the only place a *cause* survives: Swarm
    keeps each attempt as its own task, where a local container overwrites its
    exit code the moment it comes back up.
    """
    try:
        tasks = service.tasks()
    except Exception:
        return 0, None, None
    fails = 0
    newest_at: float | None = None
    cause: str | None = None
    node: str | None = None
    for task in tasks:
        status = task.get("Status") or {}
        if status.get("State") not in _FAILED_TASK_STATES:
            continue
        at = _parse_timestamp(status.get("Timestamp"))
        if at is None or _now() - at > _TROUBLE_WINDOW_SECONDS:
            continue
        fails += 1
        if newest_at is None or at > newest_at:
            newest_at = at
            cause = _task_cause(status)
            node_id = task.get("NodeID")
            node = id_to_name.get(node_id, node_id) if node_id else None
    return fails, cause, node


def _service_trouble(
    service,
    name: str,
    id_to_name: dict[str, str],
    facts: _TaskFacts,
    running: int,
    desired: int | None,
    retention: int | None,
) -> TroubleEntry | None:
    """A trouble entry for one Swarm service, or None when it is fine.

    The pre-selection is the cost argument of this whole feature. A service
    that meets its replica count and has been up longer than the window is
    dismissed without the history call, so in steady state -- which is nearly
    always -- the extra traffic is zero. In an incident the few services that
    qualify each pay one call, and in exchange the panel says what an SSH
    session would have said.
    """
    short = desired is not None and running < desired
    recent = (
        facts.newest_running_at is not None
        and _now() - facts.newest_running_at <= _TROUBLE_WINDOW_SECONDS
    )
    if not short and not recent:
        return None

    fails, cause, node = _failed_history(service, id_to_name)
    if not fails:
        # Nothing fell, so the only thing worth reporting is a service that
        # never got anywhere -- and then Swarm's own sentence is the report.
        if facts.placement_error:
            return TroubleEntry(name=name, cause=facts.placement_error, severity="dead")
        return None

    up = running > 0
    return TroubleEntry(
        name=name,
        node=node or (facts.tasks[0].node if facts.tasks else None),
        fails=fails,
        fails_capped=retention is not None and fails >= retention,
        uptime_seconds=(
            max(0.0, _now() - facts.newest_running_at)
            if up and facts.newest_running_at is not None
            else None
        ),
        cause=cause,
        severity="recovered" if up else "dead",
    )


def _swarm_services(
    client, critical: set[str], description_label: str, id_to_name: dict[str, str]
) -> tuple[list[ServiceStatus], list[TroubleEntry]]:
    services = []
    trouble: list[TroubleEntry] = []
    retention = _retention_limit(client)
    for svc in client.services.list():
        labels = _labels(svc)
        facts = _service_tasks(svc, id_to_name)
        tasks, unassigned = facts.tasks, facts.unassigned
        # The second task listing costs an API call, so only job rows pay for
        # it -- for a long-running service the current replicas are the answer
        # and its task history says nothing the panel shows.
        job = _is_job(svc, labels)
        running = sum(1 for t in tasks if t.running)
        desired = _desired_count(svc)
        if not job:
            # Jobs are excluded before the pre-selection even runs. One that
            # is meant to start, finish and vanish would qualify on every
            # scheduled run, and a quarter-hourly job would report dozens of
            # "failures" for doing precisely its work. Their own rendering
            # already carries the outcome, beside the schedule that makes it
            # readable.
            entry = _service_trouble(
                svc, svc.name, id_to_name, facts, running, desired, retention
            )
            if entry is not None:
                trouble.append(entry)
        services.append(
            ServiceStatus(
                name=svc.name,
                running_replicas=running,
                desired_replicas=desired,
                critical=svc.name in critical,
                stack=labels.get(STACK_LABEL),
                # The configured key wins; the legacy key is the fallback, so a
                # service carrying both is not pinned to its older text.
                #
                # Presence, not truthiness: a service that sets the configured
                # key to "" is saying "no description here", and falling back
                # would resurrect the very text it was migrated away from.
                description=(
                    labels[description_label]
                    if description_label in labels
                    else labels.get(LEGACY_DESCRIPTION_LABEL)
                ),
                tasks=tasks,
                unassigned=unassigned,
                job=job,
                schedule=_job_schedule(labels),
                last_run=_last_run(svc, id_to_name) if job else None,
                image=_service_image(svc),
            )
        )
    return services, trouble


def _raw_state(container) -> str:
    """The Docker state, ignoring any healthcheck verdict."""
    attrs = getattr(container, "attrs", {}) or {}
    state = attrs.get("State") or {}
    return state.get("Status") or getattr(container, "status", "") or "unknown"


def _reported_state(container) -> str:
    """The state the panel shows: the healthcheck overrides `running`.

    A container that is up but failing its own healthcheck is not working, and
    rendering it green would be the panel agreeing with the wrong half of the
    evidence. ``starting`` maps onto the start-phase set in model.py, which
    renders as "not measured yet" rather than as a failure.
    """
    attrs = getattr(container, "attrs", {}) or {}
    health = ((attrs.get("State") or {}).get("Health") or {}).get("Status")
    if health in ("unhealthy", "starting"):
        return health
    return _raw_state(container)


def _is_completed_job(container) -> bool:
    """Exited cleanly: the work is done, not broken."""
    attrs = getattr(container, "attrs", {}) or {}
    state = attrs.get("State") or {}
    return state.get("Status") == "exited" and state.get("ExitCode") == 0


#: The window is not decoration, it is half the qualifying rule. `RestartCount`
#: is cumulative over a container's whole life and is not reset by a manual
#: start (measured, Docker 29.7.2), so on its own it would pin a stumble from
#: three months ago to the panel for ever, and the block would never be empty
#: again. The counter says "it has fallen"; the window says "recently".
#:
#: Defined in model.py because the renderer names it in the heading.
_TROUBLE_WINDOW_SECONDS = TROUBLE_WINDOW_SECONDS


def _container_cause(state: dict) -> str | None:
    """Why the container fell, where Docker still knows.

    ``None`` for a container that is running again. This is not an oversight
    but the measured behaviour: once it comes back, ``ExitCode`` reads 0 and
    ``OOMKilled`` false, and the reason is simply gone. The renderer shows a
    dash there. Swarm, whose finished tasks each keep their own exit code,
    answers this better -- see the task history.
    """
    if state.get("Status") == "running":
        return None
    exit_code = state.get("ExitCode")
    if state.get("OOMKilled"):
        return f"OOMKilled · exit {exit_code}"
    if not exit_code:
        return None
    cause = f"exit {exit_code}"
    # Presence, not truthiness: Docker leaves `Error` as an empty string far
    # more often than it fills it -- it was empty even for a confirmed OOM
    # kill -- and appending it blindly would produce a trailing `· ""`.
    error = (state.get("Error") or "").strip()
    return f'{cause} · "{error}"' if error else cause


def _container_trouble(container, name: str) -> TroubleEntry | None:
    """A trouble entry for one local container, or None when it is fine.

    Two conditions, and both are needed -- see ``_TROUBLE_WINDOW_SECONDS``.
    """
    attrs = getattr(container, "attrs", {}) or {}
    restarts = attrs.get("RestartCount") or 0
    if restarts <= 0:
        return None
    state = attrs.get("State") or {}
    started = _parse_timestamp(state.get("StartedAt"))
    if started is None:
        return None
    age = _now() - started
    if age > _TROUBLE_WINDOW_SECONDS:
        return None
    status = state.get("Status")
    if status == "running":
        severity, uptime = "recovered", max(0.0, age)
    elif status == "restarting":
        severity, uptime = "restarting", None
    else:
        severity, uptime = "dead", None
    return TroubleEntry(
        name=name,
        fails=restarts,
        uptime_seconds=uptime,
        cause=_container_cause(state),
        severity=severity,
    )


def _container_groups(
    client,
) -> tuple[dict[tuple[str | None, str], list], dict[str, str], list[TroubleEntry]]:
    """Groups of non-Swarm containers, and every container's id → service name.

    The map is built here rather than in a pass of its own because
    ``containers.list(all=True)`` costs a full inspect per container; walking
    the same list twice would double that for a dict of strings.

    The names it yields are the ones DOCKER INFOS shows, so a process row and a
    service row refer to the same thing by the same name.
    """
    groups: dict[tuple[str | None, str], list] = {}
    origins: dict[str, str] = {}
    trouble: list[TroubleEntry] = []
    for container in client.containers.list(all=True):
        labels = container_labels(container)
        swarm_service = labels.get(SWARM_SERVICE_LABEL)
        if swarm_service:
            # Recorded before the `continue`: this container is reported
            # through services.list() and so is skipped for grouping, but the
            # process rows still need to resolve its id.
            origins[container.id] = swarm_service
            continue
        if _is_completed_job(container):
            continue
        project = labels.get(COMPOSE_PROJECT_LABEL)
        if project is None:
            # The two kinds part here, and deliberately: a Compose container
            # that exits non-zero belongs to a group it can be a shortfall
            # against and stays visible, while a standalone one has no group
            # to fall short of, so it is shown only while it lives -- else
            # every one-off `docker run` ever left behind would pile up here.
            if _raw_state(container) not in _LIVE_STATES:
                continue
        # Only the *visibility* rule above turns on the project label; the
        # name comes from compose_identity either way, which weighs the same
        # label itself. Deciding the name here as well would be a second copy
        # of that rule, free to drift from the one the Traefik collector
        # matches its router targets against.
        key = (project, compose_identity(labels, container.name))
        origins[container.id] = f"{key[0]}_{key[1]}" if key[0] else key[1]
        groups.setdefault(key, []).append((container, labels))
        # Deliberately after every `continue` above, so the exclusions already
        # decided there hold here too: a Swarm task is counted from the manager
        # API instead, and a job that exited cleanly finished its work rather
        # than falling over.
        entry = _container_trouble(container, origins[container.id])
        if entry is not None:
            trouble.append(entry)
    return groups, origins, trouble


def _group_image(members) -> str | None:
    """The image of the member that is serving, or of the first one.

    A Compose service whose tag changed keeps its old container around, stopped
    -- that is what a shortfall is made of. The row says how many replicas run,
    so its image must be the one they run; naming the leftover would contradict
    the same row's own count.
    """
    for container, _ in members:
        if _raw_state(container) in _LIVE_STATES:
            return _container_image(container)
    return _container_image(members[0][0])


def _container_services(
    client, critical: set[str], description_label: str, node_name: str | None
) -> tuple[list[ServiceStatus], dict[str, str], list[TroubleEntry]]:
    """Plain and Compose containers as ServiceStatus entries, the id map, trouble."""
    groups, origins, trouble = _container_groups(client)
    for entry in trouble:
        # Only known here: these are by definition the containers of this host,
        # and this is the layer that knows which node that is.
        entry.node = node_name
    services: list[ServiceStatus] = []
    for (stack, name), members in groups.items():
        states = [_reported_state(container) for container, _ in members]
        labels = members[0][1]
        services.append(
            ServiceStatus(
                name=name,
                running_replicas=sum(1 for state in states if state == "running"),
                # Every surviving container of the group is wanted: a stopped
                # Compose service is a shortfall, not an absence.
                desired_replicas=len(members),
                critical=name in critical,
                stack=stack,
                description=(
                    labels[description_label]
                    if description_label in labels
                    else labels.get(LEGACY_DESCRIPTION_LABEL)
                ),
                # Always emitted, node or no node: the tasks are what carries
                # the *state*, and the verdict reads `starting` off them to
                # tell a container on its way up from a dead one. Withholding
                # them off Swarm -- where `node_name` is None -- rendered every
                # container inside its `start_period` as 💀 0/1. Without Swarm
                # there are no node columns, so a task with no node changes
                # nothing else.
                tasks=[ServiceTask(node=node_name, state=state) for state in states],
                image=_group_image(members),
            )
        )
    services.sort(key=lambda svc: (svc.stack or "", svc.name))
    return services, origins, trouble


#: Categories `/system/df` reports, as (response key, attribute name). A daemon
#: that reports none of them is too old for this reading, and the collector
#: then returns None rather than a row of zeroes -- "Docker occupies nothing"
#: is a claim, and it would be a false one.
_DF_CATEGORIES = (
    ("ImageUsage", "images"),
    ("ContainerUsage", "containers"),
    ("VolumeUsage", "volumes"),
    ("BuildCacheUsage", "build_cache"),
)


def _disk_usage(node: str | None, root_dir: str | None, timeout: float) -> DockerDiskUsage | None:
    """Docker's own disk footprint, or None when it could not be read.

    Deliberately builds its **own** client. `/system/df` was measured at 510 ms
    against a daemon holding 47 images and 185 volumes, and the cost scales
    with the number of objects -- so on a busy node it can exceed the socket
    timeout the rest of this module runs on. That matters more than it looks:
    `collect_docker` catches every exception and degrades to
    SwarmInfo(reachable=False), so a slow reading taken on the shared client
    would erase the entire DOCKER INFOS section -- swarm, stacks, nodes and
    containers alike -- to report a disk figure. With its own client and its
    own guard, a failure here costs one line.
    """
    try:
        client = docker.from_env(timeout=timeout)
        raw = client.df()
        if not isinstance(raw, dict):
            return None
        usage = DockerDiskUsage(node=node, root_dir=root_dir)
        seen = False
        for key, attribute in _DF_CATEGORIES:
            category = raw.get(key)
            if not isinstance(category, dict):
                continue
            seen = True
            size = int(category.get("TotalSize") or 0)
            setattr(usage, attribute, size)
            usage.used += size
            usage.reclaimable += int(category.get("Reclaimable") or 0)
        if not seen:
            return None
        volumes = raw.get("Volumes") or []
        usage.volumes_total = len(volumes)
        usage.volumes_unused = sum(
            1 for v in volumes if (v.get("UsageData") or {}).get("RefCount", -1) == 0
        )
        return usage
    except Exception:
        return None


def collect_docker(
    timeout: float = 1.5,
    critical: list[str] | None = None,
    description_label: str = DEFAULT_DESCRIPTION_LABEL,
    df_timeout: float = 4.0,
) -> SwarmInfo:
    """Return Swarm and container health; never raises."""
    critical_set = set(critical or [])
    try:
        client = docker.from_env(timeout=timeout)
        info = client.info()
        swarm = info.get("Swarm", {}) if isinstance(info, dict) else {}
        active = swarm.get("LocalNodeState") == "active"

        if active:
            role = "manager" if swarm.get("ControlAvailable") else "worker"
            nodes, id_to_name = _node_map(client)
            local_node = id_to_name.get(swarm.get("NodeID") or "")
            containers, origins, trouble = _container_services(
                client, critical_set, description_label, local_node
            )
            swarm_services, swarm_trouble = _swarm_services(
                client, critical_set, description_label, id_to_name
            )
            trouble = trouble + swarm_trouble
            return SwarmInfo(
                reachable=True,
                enabled=True,
                node_role=role,
                node_count=swarm.get("Nodes") or (len(nodes) or None),
                services=swarm_services,
                containers=containers,
                nodes=nodes,
                container_services=origins,
                disk=_disk_usage(
                    local_node or info.get("Name"), info.get("DockerRootDir"), df_timeout
                ),
                trouble=trouble,
                # Carried unconditionally, though it is only ever rendered
                # where `nodes` came back empty: a worker may not list the
                # swarm, and then this is the only version anyone can state.
                local_engine_version=info.get("ServerVersion") if isinstance(info, dict) else None,
            )
        containers, origins, trouble = _container_services(
            client, critical_set, description_label, None
        )
        return SwarmInfo(
            reachable=True,
            enabled=False,
            containers=containers,
            container_services=origins,
            disk=_disk_usage(
                info.get("Name") if isinstance(info, dict) else None,
                info.get("DockerRootDir") if isinstance(info, dict) else None,
                df_timeout,
            ),
            trouble=trouble,
        )
    except Exception:
        return SwarmInfo(reachable=False)
