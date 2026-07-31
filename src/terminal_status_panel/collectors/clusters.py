"""Probe the clustered infrastructure services.

Every probe follows the same shape: find a locally running container of the
service, run one read-only status command inside it through the Docker API,
and parse the output into a ``ClusterService``.

The panel itself opens no database or broker connection and holds no
credentials — its only privilege is the Docker socket it already uses for the
DOCKER INFOS section.

A node that runs no member of a service is *not applicable*, not broken: no
MongoDB on lrz_cc and no Kafka on vzd-app are the normal case.
"""

from __future__ import annotations

import re

from ..model import ClusterMember, ClusterService

# Container name substrings, matched case-insensitively. Deliberately narrow:
# "_pg-" rather than "pg-", and "kafka_kafka-" rather than "kafka", so the
# admin UIs (kafbat-ui, kafka-ui, pgadmin) can never be mistaken for a member.
POSTGRES_PATTERNS = ("_pg-",)

_PG_NAME_PREFIX = re.compile(r"^pg\d*-")


def find_container(client, patterns: tuple[str, ...]):
    """First locally running container whose name contains one of *patterns*."""
    try:
        containers = client.containers.list()
    except Exception:
        return None
    for container in containers:
        name = (getattr(container, "name", "") or "").lower()
        if any(pattern.lower() in name for pattern in patterns):
            return container
    return None


def exec_text(container, command: list[str]) -> str:
    """Run *command* inside *container* and return stdout as text."""
    exit_code, output = container.exec_run(command)
    text = (output or b"").decode("utf-8", "replace")
    if exit_code != 0:
        raise RuntimeError(text.strip()[:200] or f"exit code {exit_code}")
    return text


def _node_from_member(name: str) -> str | None:
    """``pg18-lmzvd06-ccn-02`` -> ``lmzvd06-ccn-02``."""
    stripped = _PG_NAME_PREFIX.sub("", name)
    return stripped or None


def parse_pg_state(output: str) -> ClusterService:
    """Parse the fixed-width table of ``pg_autoctl show state``."""
    members: list[ClusterMember] = []
    primary_lsn: str | None = None

    for line in output.splitlines():
        # The separator row uses '+' rather than '|', so it drops out here.
        if "|" not in line:
            continue
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 7 or columns[0] == "Name":
            continue
        name, _node, _host_port, tli_lsn, _connection, reported, assigned = columns[:7]
        lsn = tli_lsn.split(":", 1)[1].strip() if ":" in tli_lsn else tli_lsn
        if reported == "primary":
            primary_lsn = lsn
        members.append(
            ClusterMember(
                name=name,
                node=_node_from_member(name),
                role=reported,
                healthy=reported in ("primary", "secondary"),
                detail=lsn,
                # Reported != assigned means the cluster is mid-transition:
                # neither healthy nor broken, so it is a warning.
                warning=f"→ {assigned}" if reported != assigned else None,
            )
        )

    leader = next((m.name for m in members if m.role == "primary"), None)
    for member in members:
        if member.role == "secondary" and primary_lsn and member.detail != primary_lsn:
            member.warning = member.warning or "lag"

    healthy_count = sum(1 for m in members if m.healthy)
    return ClusterService(
        kind="postgres",
        reachable=bool(members),
        leader=leader,
        quorum_ok=bool(members) and healthy_count * 2 > len(members),
        members=members,
    )


def probe_postgres(client) -> ClusterService:
    """``pg_autoctl show state`` — works from any data node, not only the monitor."""
    container = find_container(client, POSTGRES_PATTERNS)
    if container is None:
        return ClusterService(kind="postgres", applicable=False)
    try:
        output = exec_text(container, ["pg_autoctl", "show", "state"])
    except Exception as exc:
        return ClusterService(kind="postgres", error=str(exc))
    service = parse_pg_state(output)
    stack = (getattr(container, "name", "") or "").split("_", 1)[0]
    service.name = stack or None
    return service


_PROBES = {
    "postgres": probe_postgres,
}


def collect_clusters(client, kinds: list[str]) -> list[ClusterService]:
    """Probe each requested kind. Never raises."""
    services: list[ClusterService] = []
    for kind in kinds:
        probe = _PROBES.get(kind)
        if probe is None:
            continue
        try:
            services.append(probe(client))
        except Exception as exc:
            services.append(ClusterService(kind=kind, error=f"{type(exc).__name__}: {exc}"))
    return services
