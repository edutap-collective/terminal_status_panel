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

from ..model import ServiceStatus, ServiceTask, SwarmInfo, SwarmNode

STACK_LABEL = "com.docker.stack.namespace"


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
        nodes.append(
            SwarmNode(
                name=name,
                reachable=state == "ready",
                role=attrs.get("Spec", {}).get("Role"),
                leader=bool(manager.get("Leader", False)),
                state=state,
            )
        )
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
                description=labels.get(description_label),
                tasks=tasks,
                unassigned=unassigned,
            )
        )
    return services


def _container_services(client, critical: set[str]) -> list[ServiceStatus]:
    services = []
    for cont in client.containers.list():
        services.append(
            ServiceStatus(
                name=cont.name,
                running_replicas=1,
                desired_replicas=1,
                critical=cont.name in critical,
            )
        )
    return services


def collect_docker(timeout: float = 1.5, critical: list[str] | None = None,
                   description_label: str = "lmu.service.description") -> SwarmInfo:
    """Return Swarm/service health; never raises."""
    critical_set = set(critical or [])
    try:
        client = docker.from_env(timeout=timeout)
        info = client.info()
        swarm = info.get("Swarm", {}) if isinstance(info, dict) else {}
        active = swarm.get("LocalNodeState") == "active"

        if active:
            role = "manager" if swarm.get("ControlAvailable") else "worker"
            nodes, id_to_name = _node_map(client)
            return SwarmInfo(
                reachable=True,
                enabled=True,
                node_role=role,
                node_count=swarm.get("Nodes") or (len(nodes) or None),
                services=_swarm_services(client, critical_set, description_label, id_to_name),
                nodes=nodes,
            )
        return SwarmInfo(
            reachable=True,
            enabled=False,
            services=_container_services(client, critical_set),
        )
    except Exception:
        return SwarmInfo(reachable=False)
