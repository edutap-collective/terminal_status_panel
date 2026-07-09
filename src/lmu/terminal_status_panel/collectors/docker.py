"""Collect Docker Swarm + curated service health via the Docker SDK.

Time-boxed and exception-safe: any failure yields SwarmInfo(reachable=False)
so a missing or hung Docker socket can never block login.
"""

from __future__ import annotations

import docker

from ..model import ServiceStatus, SwarmInfo


def _running_count(service) -> int:
    try:
        tasks = service.tasks(filters={"desired-state": "running"})
    except Exception:
        return 0
    return sum(1 for t in tasks if t.get("Status", {}).get("State") == "running")


def _desired_count(service) -> int | None:
    try:
        return service.attrs["Spec"]["Mode"]["Replicated"]["Replicas"]
    except (KeyError, TypeError):
        return None  # e.g. global-mode services


def _swarm_services(client, critical: set[str]) -> list[ServiceStatus]:
    services = []
    for svc in client.services.list():
        services.append(
            ServiceStatus(
                name=svc.name,
                running_replicas=_running_count(svc),
                desired_replicas=_desired_count(svc),
                critical=svc.name in critical,
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


def collect_docker(timeout: float = 1.5, critical: list[str] | None = None) -> SwarmInfo:
    """Return Swarm/service health; never raises."""
    critical_set = set(critical or [])
    try:
        client = docker.from_env(timeout=timeout)
        info = client.info()
        swarm = info.get("Swarm", {}) if isinstance(info, dict) else {}
        active = swarm.get("LocalNodeState") == "active"

        if active:
            role = "manager" if swarm.get("ControlAvailable") else "worker"
            return SwarmInfo(
                reachable=True,
                enabled=True,
                node_role=role,
                node_count=swarm.get("Nodes"),
                services=_swarm_services(client, critical_set),
            )
        return SwarmInfo(
            reachable=True,
            enabled=False,
            services=_container_services(client, critical_set),
        )
    except Exception:
        return SwarmInfo(reachable=False)
