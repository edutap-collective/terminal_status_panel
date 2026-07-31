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

import json
import re
import subprocess
from xml.etree import ElementTree

from ..model import ClusterMember, ClusterService

# Container name substrings, matched case-insensitively. Deliberately narrow:
# "_pg-" rather than "pg-", and "kafka_kafka-" rather than "kafka", so the
# admin UIs (kafbat-ui, kafka-ui, pgadmin) can never be mistaken for a member.
POSTGRES_PATTERNS = ("_pg-",)

_PG_NAME_PREFIX = re.compile(r"^pg\d*-")


def find_container(client, patterns: tuple[str, ...]):
    """First locally running container whose name contains one of *patterns*."""
    containers = client.containers.list()
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
    try:
        container = find_container(client, POSTGRES_PATTERNS)
    except Exception as exc:
        return ClusterService(kind="postgres", error=str(exc))
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


MONGODB_PATTERNS = ("mongodb",)

# db.hello() is unauthenticated — the Ansible role's own healthcheck already
# relies on an unauthenticated ping. rs.status() would report per-member state
# but needs credentials, which are deliberately out of scope.
MONGO_EVAL = (
    "const h = db.hello(); "
    "JSON.stringify({set: h.setName, me: h.me, primary: h.primary, "
    "isPrimary: h.isWritablePrimary, hosts: h.hosts})"
)
MONGO_COMMAND = [
    "mongosh",
    "--tls",
    "--tlsAllowInvalidCertificates",
    "--quiet",
    "--eval",
    MONGO_EVAL,
]


def parse_mongo_hello(output: str) -> ClusterService:
    """Parse the JSON produced by ``db.hello()``."""
    line = next((raw for raw in reversed(output.splitlines()) if raw.strip()), "")
    data = json.loads(line)
    primary = data.get("primary")
    me = data.get("me")
    members = []
    for host in data.get("hosts") or []:
        if host == primary:
            role, healthy = "primary", True
        elif host == me:
            # We just executed a command against this member.
            role, healthy = "secondary", True
        else:
            # db.hello() lists membership, not state — claim nothing.
            role, healthy = "member", None
        members.append(ClusterMember(name=host, role=role, healthy=healthy))
    return ClusterService(
        kind="mongodb",
        name=data.get("set"),
        reachable=True,
        leader=primary,
        # For MongoDB this means exactly "a primary exists" and nothing more.
        quorum_ok=bool(primary),
        members=members,
    )


def probe_mongodb(client) -> ClusterService:
    """``db.hello()`` through mongosh — no credentials required."""
    try:
        container = find_container(client, MONGODB_PATTERNS)
    except Exception as exc:
        return ClusterService(kind="mongodb", error=str(exc))
    if container is None:
        return ClusterService(kind="mongodb", applicable=False)
    try:
        output = exec_text(container, MONGO_COMMAND)
        return parse_mongo_hello(output)
    except Exception as exc:
        return ClusterService(kind="mongodb", error=str(exc))


KAFKA_PATTERNS = ("kafka_kafka-",)

# The Kafka tools are NOT on $PATH in the image — the absolute path is required.
# /client.properties is mounted by the kafka Ansible role explicitly for
# "manuelle Abfragen per docker exec" and uses the broker certificate.
KAFKA_COMMAND = [
    "/opt/kafka/bin/kafka-metadata-quorum.sh",
    "--bootstrap-server",
    "localhost:9092",
    "--command-config",
    "/client.properties",
    "describe",
    "--status",
]


def _kafka_endpoint_host(entry: dict) -> str:
    """``CONTROLLER://kafka-lmzvd06-ccn-01:9093`` -> ``kafka-lmzvd06-ccn-01``."""
    endpoints = entry.get("endpoints") or []
    raw = endpoints[0] if endpoints else str(entry.get("id", "?"))
    without_scheme = raw.split("://", 1)[-1]
    return without_scheme.rsplit(":", 1)[0]


def parse_kafka_quorum(output: str) -> ClusterService:
    """Parse ``kafka-metadata-quorum.sh describe --status`` (KRaft)."""
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    def _json_list(key: str) -> list[dict]:
        try:
            return json.loads(fields.get(key, "[]"))
        except (ValueError, TypeError):
            return []

    leader_id = fields.get("LeaderId")
    members: list[ClusterMember] = []
    leader: str | None = None
    for entry in _json_list("CurrentVoters"):
        host = _kafka_endpoint_host(entry)
        is_leader = leader_id is not None and str(entry.get("id")) == leader_id
        if is_leader:
            leader = host
        members.append(
            ClusterMember(name=host, role="leader" if is_leader else "voter", healthy=True)
        )
    for entry in _json_list("CurrentObservers"):
        members.append(
            ClusterMember(name=_kafka_endpoint_host(entry), role="observer", healthy=True)
        )

    lag = fields.get("MaxFollowerLag", "?")
    lag_ms = fields.get("MaxFollowerLagTimeMs", "?")
    return ClusterService(
        kind="kafka",
        name=fields.get("ClusterId"),
        reachable=bool(fields),
        leader=leader,
        # Only "a leader exists": the status output does not say which follower
        # is behind, so anything stronger would need an invented lag threshold.
        quorum_ok=leader is not None,
        detail=f"Lag {lag} / {lag_ms} ms",
        members=members,
    )


def probe_kafka(client) -> ClusterService:
    """KRaft controller quorum. Costs ~2.6 s — JVM startup, not optimisable."""
    try:
        container = find_container(client, KAFKA_PATTERNS)
    except Exception as exc:
        return ClusterService(kind="kafka", error=str(exc))
    if container is None:
        return ClusterService(kind="kafka", applicable=False)
    try:
        return parse_kafka_quorum(exec_text(container, KAFKA_COMMAND))
    except Exception as exc:
        return ClusterService(kind="kafka", error=str(exc))


GLUSTER_TIMEOUT = 1.0


class _GlusterUnavailable(Exception):
    """The tool or the privilege is absent — not applicable, not an error."""


def _gluster(arguments: list[str]) -> str:
    """Run ``sudo -n gluster ... --xml`` and return stdout.

    Raises ``_GlusterUnavailable`` when there is no sudo/gluster on this host,
    or when sudo itself refused (no password configured). Any other failure
    — a broken daemon, lost quorum, a hang — propagates as a normal
    exception so the caller reports it as a real error.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["sudo", "-n", "gluster", *arguments, "--xml"],
            capture_output=True,
            text=True,
            timeout=GLUSTER_TIMEOUT,
        )
    except FileNotFoundError as exc:
        # No sudo (or no gluster reachable via it) on this host.
        raise _GlusterUnavailable(str(exc)) from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        if "sudo:" in stderr or "not allowed to execute" in stderr:
            # sudo itself refused — no passwordless rule for this user/host.
            raise _GlusterUnavailable(stderr)
        raise OSError(stderr or completed.stdout.strip()[:200] or "gluster failed")
    return completed.stdout


def parse_gluster(peer_xml: str, volume_xml: str) -> ClusterService:
    """Parse ``gluster peer status --xml`` and ``gluster volume status --xml``."""
    members: list[ClusterMember] = []

    peers = ElementTree.fromstring(peer_xml)
    connected = 0
    total_peers = 0
    for peer in peers.iter("peer"):
        hostname = (peer.findtext("hostname") or "").strip()
        is_connected = (peer.findtext("connected") or "0").strip() == "1"
        total_peers += 1
        connected += int(is_connected)
        members.append(
            ClusterMember(
                name=hostname,
                role="peer",
                healthy=is_connected,
                detail=(peer.findtext("stateStr") or "").strip() or None,
            )
        )

    volume = ElementTree.fromstring(volume_xml)
    # `gluster volume status --xml` with no volume name given (as we call it)
    # returns *all* volumes on the host as sibling <volume> elements. We only
    # report the first one — iterating <node> over the whole document would
    # silently merge bricks from unrelated volumes into one flat list.
    volume_elements = volume.findall(".//volume")
    volume_name = None
    detail = None
    if volume_elements:
        first_volume = volume_elements[0]
        volume_name = (first_volume.findtext("volName") or "").strip() or None
        for node in first_volume.iter("node"):
            hostname = (node.findtext("hostname") or "").strip()
            # Self-heal daemons are ordinary <node> entries; counting them as
            # bricks would double the reported brick count.
            if hostname == "Self-heal Daemon":
                continue
            members.append(
                ClusterMember(
                    name=hostname,
                    role="brick",
                    healthy=(node.findtext("status") or "0").strip() == "1",
                    detail=(node.findtext("path") or "").strip() or None,
                )
            )
        extra_volumes = len(volume_elements) - 1
        if extra_volumes:
            # Make the narrowing visible rather than hiding it: today's
            # cluster only has one volume, but if that ever changes this
            # must not read as a complete picture.
            detail = f"{volume_name} (+{extra_volumes} more volumes)"

    return ClusterService(
        kind="glusterfs",
        name=volume_name,
        reachable=True,
        leader=None,  # GlusterFS has no leader
        quorum_ok=total_peers > 0 and (connected + 1) * 2 > total_peers + 1,
        detail=detail,
        members=members,
    )


def probe_glusterfs() -> ClusterService:
    """GlusterFS runs on the host, so this uses sudo -n rather than the Docker API."""
    try:
        peer_xml = _gluster(["peer", "status"])
        volume_xml = _gluster(["volume", "status"])
    except _GlusterUnavailable:
        # No sudo, no gluster installed, no passwordless rule: not applicable.
        return ClusterService(kind="glusterfs", applicable=False)
    except Exception as exc:
        # The tool ran and something is actually wrong (daemon down, quorum
        # lost, volume not started, a hang) — this must not read as "n/a".
        return ClusterService(kind="glusterfs", error=str(exc))
    try:
        return parse_gluster(peer_xml, volume_xml)
    except Exception as exc:
        return ClusterService(kind="glusterfs", error=str(exc))


RUSTFS_PATTERNS = ("rustfs_rustfs",)
RUSTFS_FALLBACK_ENDPOINT = "https://localhost:9000"


def rustfs_endpoints(container) -> list[str]:
    """Endpoints to probe, derived from RUSTFS_VOLUMES in the container env.

    Read from the container rather than from configuration so the check stays
    correct across the move from ``shared`` (a local path) to ``distributed``
    (a list of URLs) without any change here.
    """
    environment = ((getattr(container, "attrs", {}) or {}).get("Config") or {}).get("Env") or []
    raw = ""
    for entry in environment:
        key, _, value = str(entry).partition("=")
        if key == "RUSTFS_VOLUMES":
            raw = value
            break
    endpoints = []
    for token in raw.split():
        if "://" not in token:
            continue  # a plain path: one local instance
        scheme, _, rest = token.partition("://")
        host_port = rest.split("/", 1)[0]
        endpoints.append(f"{scheme}://{host_port}")
    return endpoints or [RUSTFS_FALLBACK_ENDPOINT]


def probe_rustfs(client) -> ClusterService:
    """GET /health per endpoint — the only unauthenticated status RustFS offers."""
    try:
        container = find_container(client, RUSTFS_PATTERNS)
    except Exception as exc:
        return ClusterService(kind="rustfs", error=str(exc))
    if container is None:
        return ClusterService(kind="rustfs", applicable=False)
    members: list[ClusterMember] = []
    for endpoint in rustfs_endpoints(container):
        try:
            # curl from inside the container: the rustfs overlay network
            # publishes no host ports.
            status = exec_text(
                container,
                [
                    "curl", "-ks", "-o", "/dev/null", "-m", "2",
                    "-w", "%{http_code}", f"{endpoint}/health",
                ],
            ).strip()
            healthy = status == "200"
            detail = f"HTTP {status}"
        except Exception as exc:
            healthy = False
            detail = str(exc)[:60]
        members.append(
            ClusterMember(name=endpoint, role="peer", healthy=healthy, detail=detail)
        )
    live = sum(1 for member in members if member.healthy)
    return ClusterService(
        kind="rustfs",
        name="rustfs",
        reachable=live > 0,
        leader=None,  # RustFS has no leader we can observe
        # /health is a liveness check only; erasure-coding and heal state
        # require the admin API (which answers 403). Majority quorum.
        quorum_ok=live * 2 > len(members),
        detail=f"{live}/{len(members)} live",
        members=members,
    )


_PROBES = {
    "postgres": probe_postgres,
    "mongodb": probe_mongodb,
    "kafka": probe_kafka,
    "glusterfs": lambda _client: probe_glusterfs(),
    "rustfs": probe_rustfs,
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
