"""Collect Docker Swarm health via the Docker SDK.

Everything here comes from the Docker/Swarm API — node state, service replica
counts, the ``com.docker.stack.namespace`` grouping label, a curated
description label, and per-task node placement. No database or broker protocol
is ever spoken to.

Time-boxed and exception-safe: any failure yields SwarmInfo(reachable=False)
so a missing or hung Docker socket can never block login.
"""

from __future__ import annotations

import docker

from ..config import DEFAULT_DESCRIPTION_LABEL, LEGACY_DESCRIPTION_LABEL
from ..model import ServiceStatus, ServiceTask, SwarmInfo, SwarmNode
from ._labels import SWARM_SERVICE_LABEL, container_labels

STACK_LABEL = "com.docker.stack.namespace"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

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
        nodes.append(
            SwarmNode(
                name=name,
                reachable=state == "ready",
                role=spec.get("Role"),
                leader=bool(manager.get("Leader", False)),
                state=state,
                # active / pause / drain — a drained node is ready but idle.
                availability=spec.get("Availability"),
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


def _desired_count(service) -> int | None:
    try:
        return service.attrs["Spec"]["Mode"]["Replicated"]["Replicas"]
    except (KeyError, TypeError):
        return None  # e.g. global-mode services


def _service_tasks(service, id_to_name: dict[str, str]) -> tuple[list[ServiceTask], int]:
    """Return (per-node tasks, unassigned count) for tasks that should run.

    Mirrors the operations script: filter to desired-state ``running``, split
    into node-assigned tasks (reporting their *actual* state) and orphaned
    tasks with no node (e.g. pinned to a node that is down)."""
    try:
        all_tasks = service.tasks(filters={"desired-state": "running"})
    except Exception:
        return [], 0
    tasks: list[ServiceTask] = []
    unassigned = 0
    for t in all_tasks:
        node_id = t.get("NodeID")
        state = t.get("Status", {}).get("State", "unknown")
        if node_id:
            tasks.append(ServiceTask(node=id_to_name.get(node_id, node_id), state=state))
        else:
            unassigned += 1
    tasks.sort(key=lambda task: task.node or "")
    return tasks, unassigned


def _swarm_services(client, critical: set[str], description_label: str,
                    id_to_name: dict[str, str]) -> list[ServiceStatus]:
    services = []
    for svc in client.services.list():
        labels = _labels(svc)
        tasks, unassigned = _service_tasks(svc, id_to_name)
        services.append(
            ServiceStatus(
                name=svc.name,
                running_replicas=sum(1 for t in tasks if t.running),
                desired_replicas=_desired_count(svc),
                critical=svc.name in critical,
                stack=labels.get(STACK_LABEL),
                # The configured key wins; the legacy key is the fallback, so a
                # service carrying both is not pinned to its older text.
                #
                # Presence, not truthiness: a service that sets the configured
                # key to "" is saying "no description here", and falling back
                # would resurrect the very text it was migrated away from.
                description=(labels[description_label]
                             if description_label in labels
                             else labels.get(LEGACY_DESCRIPTION_LABEL)),
                tasks=tasks,
                unassigned=unassigned,
            )
        )
    return services


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


def _container_groups(client) -> dict[tuple[str | None, str], list]:
    """Group live containers by (compose project, service name).

    Compose labels rather than name parsing: the container is called
    ``portal-web-1`` and only the labels say reliably which part is the project
    and which the service.
    """
    groups: dict[tuple[str | None, str], list] = {}
    for container in client.containers.list(all=True):
        labels = container_labels(container)
        if SWARM_SERVICE_LABEL in labels:
            continue  # already reported through services.list()
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
            key = (None, container.name)
        else:
            key = (project, labels.get(COMPOSE_SERVICE_LABEL) or container.name)
        groups.setdefault(key, []).append((container, labels))
    return groups


def _container_services(client, critical: set[str], description_label: str,
                        node_name: str | None) -> list[ServiceStatus]:
    """Plain and Compose containers as ServiceStatus entries."""
    services: list[ServiceStatus] = []
    for (stack, name), members in _container_groups(client).items():
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
                description=(labels[description_label]
                             if description_label in labels
                             else labels.get(LEGACY_DESCRIPTION_LABEL)),
                # Always emitted, node or no node: the tasks are what carries
                # the *state*, and the verdict reads `starting` off them to
                # tell a container on its way up from a dead one. Withholding
                # them off Swarm -- where `node_name` is None -- rendered every
                # container inside its `start_period` as 💀 0/1. Without Swarm
                # there are no node columns, so a task with no node changes
                # nothing else.
                tasks=[ServiceTask(node=node_name, state=state) for state in states],
            )
        )
    services.sort(key=lambda svc: (svc.stack or "", svc.name))
    return services


def collect_docker(timeout: float = 1.5, critical: list[str] | None = None,
                   description_label: str = DEFAULT_DESCRIPTION_LABEL) -> SwarmInfo:
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
            return SwarmInfo(
                reachable=True,
                enabled=True,
                node_role=role,
                node_count=swarm.get("Nodes") or (len(nodes) or None),
                services=_swarm_services(client, critical_set, description_label,
                                         id_to_name),
                containers=_container_services(client, critical_set,
                                               description_label, local_node),
                nodes=nodes,
            )
        return SwarmInfo(
            reachable=True,
            enabled=False,
            containers=_container_services(client, critical_set,
                                           description_label, None),
        )
    except Exception:
        return SwarmInfo(reachable=False)
