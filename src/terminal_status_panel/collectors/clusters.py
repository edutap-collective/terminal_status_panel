"""Probe the clustered infrastructure services.

Every probe follows the same shape: find a locally running container of the
service, run one read-only status command inside it through the Docker API,
and parse the output into a ``ClusterService``.

The panel itself opens no database or broker connection and holds no
credentials — its only privilege is the Docker socket it already uses for the
DOCKER INFOS section.

A node that runs no member of a service is *not applicable*, not broken: no
A cluster without MongoDB, or one without Kafka, is the normal case.

The probes run concurrently, one budget task each, and share a single
``ContainerIndex`` so the Docker daemon is asked for its container list once
per run rather than once per probe.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from collections.abc import Callable
from typing import Any
from xml.etree import ElementTree

from ..config import DEFAULT_INFRA_UI_SERVICES
from ..model import ClusterMember, ClusterService

# Container name substrings, matched case-insensitively. Deliberately narrow:
# "_pg-" rather than "pg-", and "kafka_kafka-" rather than "kafka", so the
# admin UIs (kafbat-ui, kafka-ui, pgadmin) can never be mistaken for a member.
POSTGRES_PATTERNS = ("_pg-",)

_PG_NAME_PREFIX = re.compile(r"^pg\d*-")


class ContainerIndex:
    """One shared, lazily fetched view of the local Docker state.

    docker-py's ``ContainerCollection.list()`` defaults to ``sparse=False`` and
    then issues one ``GET /containers/{id}/json`` per running container. Asking
    once per probe cost roughly ``kinds × (1 + containers)`` round trips at every
    login — about 205 on a host with 40 containers and five kinds enabled. This
    fetches the list once, sparsely, and hands the same list to every probe;
    only a probe that needs the full attributes (RustFS, for ``Config.Env``)
    pays for an inspect, and only for the one container it matched.

    ``sparse=True`` also removes a false verdict: with the inspecting variant, a
    container that disappears between the listing and its inspect makes
    ``list()`` raise ``NotFound``, which ``locate_member`` would have rendered
    as "✗ PostgreSQL: 404 Client Error" — a red claim produced by a race.

    Thread-safe because the probes run concurrently: the first probe to need a
    piece of state pays for it under that state's lock and the others reuse the
    answer instead of starting four more requests. The instance is created
    inside ``collect_health``, so the fetch happens inside the time budget and
    never on the login path's main thread.
    """

    def __init__(self, client) -> None:
        """Wrap *client*, whose listings this cache serves to every probe."""
        self._client = client
        self._cache: dict[str, tuple[Any, Exception | None]] = {}
        self._locks: dict[str, threading.Lock] = {
            key: threading.Lock() for key in ("containers", "services", "info")
        }

    def _cached(self, key: str, fetch: Callable[[], Any]) -> Any:
        with self._locks[key]:
            if key not in self._cache:
                try:
                    self._cache[key] = (fetch(), None)
                except Exception as exc:  # remembered, so every probe sees it
                    self._cache[key] = (None, exc)
            value, error = self._cache[key]
        if error is not None:
            raise error
        return value

    def containers(self) -> list:
        """Every running container, without a per-container inspect."""
        return self._cached(
            "containers",
            # ignore_removed has no effect while sparse is True (there is no
            # inspect to race with); it is passed so the guarantee survives if
            # sparse ever has to be turned off.
            lambda: self._client.containers.list(sparse=True, ignore_removed=True),
        )

    def services(self) -> list:
        """The Swarm service list, fetched at most once per run."""
        return self._cached("services", lambda: self._client.services.list())

    def info(self) -> dict:
        """``docker info``, fetched at most once per run."""
        return self._cached("info", lambda: self._client.info() or {})


def container_names(container) -> list[str]:
    """Every name *container* is known by.

    A sparse list entry carries the list response's ``Names`` array, and
    docker-py's ``Container.name`` (which reads ``Name``) is then ``None`` — so
    both spellings have to be looked at.
    """
    names = []
    own_name = getattr(container, "name", None)
    if own_name:
        names.append(str(own_name).lstrip("/"))
    attributes = getattr(container, "attrs", None) or {}
    for raw in attributes.get("Names") or []:
        names.append(str(raw).lstrip("/"))
    return names


def container_name(container) -> str:
    """The container's primary name, or an empty string when it has none."""
    names = container_names(container)
    return names[0] if names else ""


def find_containers(index: ContainerIndex, patterns: tuple[str, ...]) -> list:
    """Every locally running container whose name contains one of *patterns*."""
    matches = []
    for container in index.containers():
        names = [name.lower() for name in container_names(container)]
        if any(pattern.lower() in name for pattern in patterns for name in names):
            matches.append(container)
    return matches


def find_container(index: ContainerIndex, patterns: tuple[str, ...]):
    """First locally running container whose name contains one of *patterns*."""
    matches = find_containers(index, patterns)
    return matches[0] if matches else None


def _append_note(service: ClusterService, note: str) -> None:
    """Add a parenthetical note to *service*'s detail.

    Merged into a parenthetical that is already there rather than set beside
    it, so a RustFS line reads ``1/1 live (endpoint list unknown, +1 more
    container)`` and not as two adjacent brackets.
    """
    if not service.detail:
        service.detail = f"({note})"
    elif service.detail.endswith(")"):
        service.detail = f"{service.detail[:-1]}, {note})"
    else:
        service.detail = f"{service.detail} ({note})"


def _note_extra_containers(service: ClusterService, extras: int) -> None:
    """Make a narrowed view visible, the way ``parse_gluster`` does for volumes.

    A node hosting two clusters of the same kind — a pg16 → pg18 migration is
    the case ``_PG_NAME_PREFIX`` anticipates — would otherwise show one of them
    with nothing to say the other exists.
    """
    if extras <= 0:
        return
    _append_note(service, f"+{extras} more container{'s' if extras > 1 else ''}")


def exec_text(container, command: list[str]) -> str:
    """Run *command* inside *container* and return stdout as text."""
    exit_code, output = container.exec_run(command)
    text = (output or b"").decode("utf-8", "replace")
    if exit_code != 0:
        raise RuntimeError(text.strip()[:200] or f"exit code {exit_code}")
    return text


def _pinned_to_hostname(service, hostname: str) -> bool:
    """True when *service*'s placement constraints pin it to *hostname*.

    Parses ``node.hostname == <name>`` constraints tolerantly (whitespace
    around ``==`` is optional); any other constraint key is ignored, since we
    only need to answer "does this run here", not evaluate the full
    constraint language.
    """
    placement = ((service.attrs or {}).get("Spec") or {}).get("TaskTemplate", {}).get(
        "Placement"
    ) or {}
    for constraint in placement.get("Constraints") or []:
        key, sep, value = str(constraint).partition("==")
        if not sep or key.strip() != "node.hostname":
            continue
        if value.strip() == hostname:
            return True
    return False


def _wanted_on_this_node(index: ContainerIndex, patterns: tuple[str, ...]) -> bool:
    """True when Swarm's service *spec* wants this service running on this node.

    Called only when no local container was found — which on a typical app
    server is the *common* case for all five kinds, not a rare one, so the
    ``docker info`` and service-list answers it needs are fetched once per run
    by the shared index rather than once per probe.

    This reads ``Spec.Mode``/``Spec.TaskTemplate.Placement`` rather
    than the live task list: during a crash loop, the failed task has already
    flipped to ``desired: shutdown`` and its replacement may not exist yet, so
    a task-list snapshot is a coin flip exactly when it matters. The spec is
    the stable, timing-independent statement of what Swarm wants.

    A replicated service with no placement constraint could legitimately be
    running on a different node, so this deliberately returns ``False`` for
    it rather than over-claiming "wanted here" — a crash loop of an unpinned
    service is therefore not flagged by this check (DOCKER INFOS, which reads
    the Swarm service list directly, still shows it correctly).
    """
    hostname = (index.info() or {}).get("Name")
    if not hostname:
        return False
    for service in index.services():
        name = (getattr(service, "name", "") or "").lower()
        if not any(pattern.lower() in name for pattern in patterns):
            continue
        mode = ((service.attrs or {}).get("Spec") or {}).get("Mode") or {}
        if "Global" in mode:
            return True
        replicas = ((mode.get("Replicated") or {}).get("Replicas")) or 0
        if replicas >= 1 and _pinned_to_hostname(service, hostname):
            return True
    return False


def locate_member(index: ContainerIndex, kind: str, patterns: tuple[str, ...]):
    """Find the local container for *kind*.

    Returns ``(container, None, extras)`` when one is running, otherwise
    ``(None, verdict, 0)`` where *verdict* already carries the right
    conclusion: an error when Swarm wants a task here but none runs,
    ``applicable=False`` when this node genuinely runs no member of the
    service. *extras* counts the further matching containers that this probe
    will not look at, so the caller can make that narrowing visible.
    """
    try:
        matches = find_containers(index, patterns)
    except Exception as exc:
        return None, ClusterService(kind=kind, error=str(exc)), 0
    if matches:
        return matches[0], None, len(matches) - 1
    try:
        wanted = _wanted_on_this_node(index, patterns)
    except Exception:
        wanted = False  # cannot ask Swarm: fall back to the weaker claim
    if wanted:
        return None, ClusterService(kind=kind, error="no running container"), 0
    return None, ClusterService(kind=kind, applicable=False), 0


def _node_from_member(name: str) -> str | None:
    """``pg18-node-b`` -> ``node-b``."""
    stripped = _PG_NAME_PREFIX.sub("", name)
    return stripped or None


def _lsn_position(lsn: str) -> int | None:
    """A PostgreSQL LSN as a byte offset, or None if it is not one.

    LSNs are written ``hi/lo`` in hexadecimal, where ``hi`` counts 4 GB
    segments. Anything that does not parse yields None rather than a guess --
    a wrong distance would be worse than no distance.
    """
    hi, _, lo = lsn.partition("/")
    try:
        return (int(hi, 16) << 32) | int(lo, 16)
    except ValueError:
        return None


def _lag_distance(member_lsn: str, primary_lsn: str) -> int | None:
    """How many bytes a member trails the primary by, or None if unknowable.

    None rather than a guess in two cases: an LSN that does not parse, and a
    member *ahead* of the primary. The latter is not hypothetical -- right
    after a promotion the figures can cross, and a negative distance would be
    nonsense to render.
    """
    here, there = _lsn_position(member_lsn), _lsn_position(primary_lsn)
    if here is None or there is None or there <= here:
        return None
    return there - here


def _split_timeline(tli_lsn: str) -> tuple[str | None, str]:
    """``5: 0/23942800`` -> ``("5", "0/23942800")``.

    Older ``pg_autoctl`` versions emit the LSN without the timeline prefix.
    Then there is no timeline to compare and none is claimed -- the check falls
    back to what it did before. This package runs wherever it is installed, and
    assuming the newest version is the mistake that produced the incident this
    was written for.
    """
    if ":" not in tli_lsn:
        return None, tli_lsn.strip()
    timeline, lsn = tli_lsn.split(":", 1)
    return timeline.strip() or None, lsn.strip()


def parse_pg_state(output: str) -> ClusterService:
    """Parse the fixed-width table of ``pg_autoctl show state``."""
    members: list[ClusterMember] = []
    primary_lsn: str | None = None
    primary_timeline: str | None = None
    # Collected first, judged after: the primary can appear on any row, and a
    # member's verdict depends on it.
    raw: list[tuple[str, str | None, str, str, str]] = []

    for line in output.splitlines():
        # The separator row uses '+' rather than '|', so it drops out here.
        if "|" not in line:
            continue
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 7 or columns[0] == "Name":
            continue
        name, _node, _host_port, tli_lsn, _connection, reported, assigned = columns[:7]
        timeline, lsn = _split_timeline(tli_lsn)
        if reported == "primary":
            primary_lsn, primary_timeline = lsn, timeline
        raw.append((name, timeline, lsn, reported, assigned))

    for name, timeline, lsn, reported, assigned in raw:
        # A transition is ordinary and passes; a foreign timeline does not.
        # Both are shown together where both apply: one says the node is cut
        # off, the other that the monitor is already working on it, and
        # neither answers the other's question.
        transition = f"→ {assigned}" if reported != assigned else None
        diverged = (
            primary_timeline is not None and timeline is not None and timeline != primary_timeline
        )
        if diverged:
            note = f"TLI {timeline}≠{primary_timeline}"
            warning = f"{note} {transition}" if transition else note
        else:
            warning = transition
        members.append(
            ClusterMember(
                name=name,
                node=_node_from_member(name),
                role=reported,
                # A node on a foreign timeline is NOT healthy, whatever the
                # monitor reports about it. This one boolean is the whole
                # lever: quorum_ok is counted from it, and the renderer's
                # _combined_icon carries the verdict into the service row in
                # DOCKER INFOS -- where a reader looks first.
                #
                # It matters because such a node looks perfectly fine. Its WAL
                # receiver keeps the connection alive with keepalives, so
                # pg_auto_failover reports it as a healthy secondary, and a
                # production cluster once ran for hours that way with three of
                # five nodes replicating nothing. They were not behind. They
                # had stopped, and a standby that will never catch up
                # contributes nothing in a failover -- it is a copy of
                # yesterday.
                healthy=(not diverged) and reported in ("primary", "secondary"),
                # On a divergence the LSN is withheld deliberately: a byte
                # figure suggests catching up is a matter of time, which is
                # precisely the misreading this replaces.
                detail=None if diverged else lsn,
                warning=warning,
            )
        )

    if not members:
        # The command succeeded but nothing recognisable came back — a changed
        # output format, an empty table. Nothing was *measured*, so quorum stays
        # unreported: a 💀 here would be a definite verdict from missing data.
        return ClusterService(kind="postgres", reachable=False, quorum_ok=None)

    leader = next((m.name for m in members if m.role == "primary"), None)
    for member in members:
        if member.role == "secondary" and primary_lsn and member.detail != primary_lsn:
            if member.warning is None:
                member.warning = "lag"
                member.lag_bytes = _lag_distance(member.detail or "", primary_lsn)

    healthy_count = sum(1 for m in members if m.healthy)
    return ClusterService(
        kind="postgres",
        reachable=True,
        leader=leader,
        quorum_ok=healthy_count * 2 > len(members),
        members=members,
    )


def probe_postgres(index: ContainerIndex) -> ClusterService:
    """``pg_autoctl show state`` — works from any data node, not only the monitor."""
    container, verdict, extras = locate_member(index, "postgres", POSTGRES_PATTERNS)
    if verdict is not None:
        return verdict
    try:
        output = exec_text(container, ["pg_autoctl", "show", "state"])
    except Exception as exc:
        return ClusterService(kind="postgres", error=str(exc))
    service = parse_pg_state(output)
    stack = container_name(container).split("_", 1)[0]
    service.name = stack or None
    _note_extra_containers(service, extras)
    return service


#: Fallback deadline for one cluster kind, used where the caller names none.
#: The configured per-kind timeouts in `HealthConfig` are what normally apply.
DEFAULT_KIND_TIMEOUT = 2.0

MONGODB_PATTERNS = ("mongodb",)

#: Wall-clock a `docker exec mongosh` spends before the first line of the
#: script runs. Measured on a cluster node over five runs: 0.97 s to 1.50 s,
#: almost all of it the shell's own start-up. The fan-out deadline is the
#: check's timeout minus this, because a deadline measured from inside the
#: script cannot account for the time spent getting there -- and one set to the
#: full timeout would only be reached after the budget thread had already
#: abandoned the check, turning a partial answer into a truncated one.
MONGO_STARTUP_ALLOWANCE = 1.5

#: Deadline for one member's `hello`, in milliseconds. Measured on the node: a
#: healthy member answers in 171-279 ms, a refused connection costs 97 ms and
#: an unresolvable name 113 ms. 700 ms is the only case that has to be capped
#: -- a blackholed address, where the connection neither completes nor fails --
#: and it was measured to hold at 787 ms wall clock.
MONGO_MEMBER_TIMEOUT_MS = 700


def _mongo_fanout_budget(timeout: float) -> float:
    """How long the script may spend asking members, given the check's timeout.

    Never zero or negative: with a timeout too small for any fan-out at all,
    the local ``hello`` still has to run, and the loop then simply stops before
    its first member. That degrades to exactly the old behaviour rather than to
    an error.
    """
    return max(0.2, timeout - MONGO_STARTUP_ALLOWANCE)


def mongo_command(timeout: float) -> list[str]:
    """The mongosh invocation, with the fan-out deadline compiled in.

    ``db.hello()`` is answered before authentication, which is what makes any
    of this possible without credentials -- the Ansible role's own healthcheck
    already relies on an unauthenticated ping. It reports the set's membership
    but state only for the primary and for the node answering, so the script
    then asks each member the same question directly.

    ``replSetGetStatus`` would answer all of it in one round trip, including
    replication lag. It is not an option here: measured against the real set it
    replies ``Command replSetGetStatus requires authentication``.

    The loop checks the clock before each member rather than after, so the
    deadline bounds the work still to be started. Members left unasked simply
    do not appear in ``members`` and are rendered unmeasured -- the state the
    whole set was in before this existed, so a slow cluster degrades to the old
    display instead of to an empty one.

    Read-only throughout: ``hello`` and nothing else.
    """
    budget_ms = int(_mongo_fanout_budget(timeout) * 1000)
    script = (
        "const h = db.hello();"
        "const hosts = (h.hosts || []).concat(h.passives || []).concat(h.arbiters || []);"
        f"const deadline = Date.now() + {budget_ms};"
        "const members = [];"
        "for (const host of hosts) {"
        "  if (Date.now() >= deadline) break;"
        "  try {"
        '    const uri = "mongodb://" + host + "/?tls=true&tlsAllowInvalidCertificates=true"'
        f'      + "&serverSelectionTimeoutMS={MONGO_MEMBER_TIMEOUT_MS}"'
        f'      + "&connectTimeoutMS={MONGO_MEMBER_TIMEOUT_MS}"'
        f'      + "&socketTimeoutMS={MONGO_MEMBER_TIMEOUT_MS}";'
        '    const hh = new Mongo(uri).getDB("admin").hello();'
        '    const state = hh.arbiterOnly === true ? "arbiter"'
        '      : hh.isWritablePrimary === true ? "primary"'
        '      : hh.secondary === true ? "secondary" : "other";'
        "    members.push({host: host, state: state});"
        "  } catch (e) {"
        '    members.push({host: host, state: "unreachable"});'
        "  }"
        "}"
        "JSON.stringify({set: h.setName, me: h.me, primary: h.primary,"
        " isPrimary: h.isWritablePrimary, hosts: hosts, members: members})"
    )
    return ["mongosh", "--tls", "--tlsAllowInvalidCertificates", "--quiet", "--eval", script]


def parse_mongo_hello(output: str) -> ClusterService:
    """Parse the JSON produced by ``db.hello()``."""
    line = next((raw for raw in reversed(output.splitlines()) if raw.strip()), "")
    data = json.loads(line)
    primary = data.get("primary")
    me = data.get("me")
    # `hello` reports arbiters and passive (zero-priority) members in their own
    # fields rather than in `hosts`, so reading `hosts` alone shows a set that
    # has them one row short, with nothing saying a vote is missing. The
    # command already asks all three; the parser has to accept all three back.
    hosts = list(data.get("hosts") or [])
    for extra_field in ("passives", "arbiters"):
        for host in data.get(extra_field) or []:
            if host not in hosts:
                hosts.append(host)
    if not hosts and not primary and not data.get("set"):
        # Valid JSON, but nothing we recognise as a replica set: no membership,
        # no primary, no set name. Nothing measured, so nothing claimed.
        return ClusterService(kind="mongodb", reachable=False, quorum_ok=None)
    # What the fan-out reached, keyed by host. Absent for a member the deadline
    # cut off, and for the whole answer when the script produced no fan-out at
    # all -- both fall through to the membership-only reading below.
    measured = {}
    raw_members = data.get("members")
    if isinstance(raw_members, list):
        for entry in raw_members:
            if isinstance(entry, dict) and entry.get("host"):
                measured[str(entry["host"])] = str(entry.get("state") or "other")

    members = []
    for host in hosts:
        state = measured.get(host)
        if state == "unreachable":
            # Asked and could not be reached. That is a measurement, and a
            # different thing from never having asked.
            role, healthy = "unreachable", False
        elif state in ("primary", "secondary", "arbiter"):
            role, healthy = state, True
        elif state == "other":
            # Reached, answered, and in none of the states worth a name --
            # starting up, rolling back, recovering. Alive, not serving.
            role, healthy = "other", None
        elif host == primary:
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


def probe_mongodb(index: ContainerIndex, timeout: float = DEFAULT_KIND_TIMEOUT) -> ClusterService:
    """``db.hello()`` through mongosh, for the set and for each member."""
    container, verdict, extras = locate_member(index, "mongodb", MONGODB_PATTERNS)
    if verdict is not None:
        return verdict
    try:
        service = parse_mongo_hello(exec_text(container, mongo_command(timeout)))
    except Exception as exc:
        return ClusterService(kind="mongodb", error=str(exc))
    _note_extra_containers(service, extras)
    return service


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
    """``CONTROLLER://kafka-node-a:9093`` -> ``kafka-node-a``."""
    endpoints = entry.get("endpoints") or []
    raw = endpoints[0] if endpoints else str(entry.get("id", "?"))
    without_scheme = raw.split("://", 1)[-1]
    return without_scheme.rsplit(":", 1)[0]


# At least one of these must appear for the output to be the status report we
# think it is. Any "Key: value" line would otherwise pass for one — including
# an error banner, or whatever a future Kafka version prints instead.
_KAFKA_STATUS_KEYS = frozenset(
    {"ClusterId", "LeaderId", "LeaderEpoch", "HighWatermark", "CurrentVoters"}
)


def parse_kafka_quorum(output: str) -> ClusterService:
    """Parse ``kafka-metadata-quorum.sh describe --status`` (KRaft)."""
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    if not fields.keys() & _KAFKA_STATUS_KEYS:
        # Unrecognised output: an upgrade that changes the format must not
        # raise a red quorum alarm for a healthy cluster at every login.
        return ClusterService(kind="kafka", reachable=False, quorum_ok=None)

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
        reachable=True,
        leader=leader,
        # Only "a leader exists": the status output does not say which follower
        # is behind, so anything stronger would need an invented lag threshold.
        quorum_ok=leader is not None,
        detail=f"Lag {lag} / {lag_ms} ms",
        members=members,
    )


def probe_kafka(index: ContainerIndex) -> ClusterService:
    """KRaft controller quorum. Costs ~2.6 s — JVM startup, not optimisable."""
    container, verdict, extras = locate_member(index, "kafka", KAFKA_PATTERNS)
    if verdict is not None:
        return verdict
    try:
        service = parse_kafka_quorum(exec_text(container, KAFKA_COMMAND))
    except Exception as exc:
        return ClusterService(kind="kafka", error=str(exc))
    _note_extra_containers(service, extras)
    return service


# Fallback for a caller that names no timeout; the configured value
# (health.timeout.glusterfs) is what production actually uses.
GLUSTER_TIMEOUT = 1.0


class _GlusterUnavailable(Exception):
    """The tool or the privilege is absent — not applicable, not an error."""


def _gluster(arguments: list[str], timeout: float) -> str:
    """Run ``sudo -n gluster ... --xml`` and return stdout.

    Raises ``_GlusterUnavailable`` when there is no sudo/gluster on this host,
    or when sudo itself refused (no password configured). Any other failure
    — a broken daemon, lost quorum, a hang — propagates as a normal
    exception so the caller reports it as a real error.
    """
    try:
        # S607: resolved through PATH deliberately. gluster lives in
        # /usr/sbin on Debian and /usr/local/sbin on FreeBSD, and the panel
        # runs on both.
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["sudo", "-n", "gluster", *arguments, "--xml"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=timeout,
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

    # S314: not untrusted input -- this is the host's own `gluster --xml`
    # output, produced by the command above. Nothing remote reaches it.
    peers = ElementTree.fromstring(peer_xml)  # noqa: S314
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

    volume = ElementTree.fromstring(volume_xml)  # noqa: S314 - see above
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
        # Nothing parsed at all (no peers, no volumes) means we did not reach a
        # working glusterd, whatever the exit code said.
        reachable=total_peers > 0 or bool(volume_elements),
        leader=None,  # GlusterFS has no leader
        # `peer status` lists the *other* peers, so zero of them means either an
        # unparsed answer or a single-node volume. Neither supports a quorum
        # claim, so the panel reports none rather than inventing a 💀.
        quorum_ok=((connected + 1) * 2 > total_peers + 1 if total_peers > 0 else None),
        detail=detail,
        members=members,
    )


def probe_glusterfs(timeout: float = GLUSTER_TIMEOUT) -> ClusterService:
    """GlusterFS runs on the host, so this uses sudo -n rather than the Docker API.

    *timeout* is the budget for the whole probe and is split between the two
    calls it makes, so the configured ``health.timeout.glusterfs`` bounds the
    probe rather than one of its halves.
    """
    per_call = max(0.1, timeout / 2)
    try:
        peer_xml = _gluster(["peer", "status"], timeout=per_call)
        volume_xml = _gluster(["volume", "status"], timeout=per_call)
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

# The join key between DOCKER INFOS and CLUSTER HEALTH. Built from the same
# patterns the probes match containers with, so the identifier lives in exactly
# one place — a second copy is how the crash-loop detection broke once already.
# GlusterFS is absent on purpose: it runs on the host, not as a Docker service.
_KIND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("postgres", POSTGRES_PATTERNS),
    ("mongodb", MONGODB_PATTERNS),
    ("kafka", KAFKA_PATTERNS),
    ("rustfs", RUSTFS_PATTERNS),
)


# Names that must never resolve to a kind, however well they match. This is the
# join's own, narrower key: ``MONGODB_PATTERNS`` has to stay the bare word so
# ``probe_mongodb`` finds the local container, but as a join key it also matches
# every sidecar that merely shares the ``mongodb`` stack — an admin UI such as
# ``mongodb_mongo-express``. Handing such a service the replica set's verdict
# would state a health measurement about something that was never probed.
_NEVER_A_MEMBER: tuple[str, ...] = tuple(name.lower() for name in DEFAULT_INFRA_UI_SERVICES)


def kind_for_service(name: str) -> str | None:
    """The cluster kind a Docker service name belongs to, or None."""
    lowered = (name or "").lower()
    if any(ui in lowered for ui in _NEVER_A_MEMBER):
        return None
    for kind, patterns in _KIND_PATTERNS:
        if any(pattern.lower() in lowered for pattern in patterns):
            return kind
    return None


RUSTFS_FALLBACK_ENDPOINT = "https://localhost:9000"


def rustfs_endpoints(container) -> tuple[list[str], bool]:
    """Endpoints to probe, derived from RUSTFS_VOLUMES in the container env.

    Read from the container rather than from configuration so the check stays
    correct across the move from ``shared`` (a local path) to ``distributed``
    (a list of URLs) without any change here.

    Returns ``(endpoints, guessed)``. *guessed* is True when RUSTFS_VOLUMES was
    absent or unreadable and the localhost endpoint is therefore an assumption,
    not a reading — a five-node cluster must never render as "1/1 live" just
    because the variable was renamed. A plain path in RUSTFS_VOLUMES is not a
    guess: it says, readably, that this is a single local instance.
    """
    environment = ((getattr(container, "attrs", {}) or {}).get("Config") or {}).get("Env") or []
    raw = None
    for entry in environment:
        key, _, value = str(entry).partition("=")
        if key == "RUSTFS_VOLUMES":
            raw = value
            break
    if raw is None:
        return [RUSTFS_FALLBACK_ENDPOINT], True
    endpoints = []
    for token in raw.split():
        if "://" not in token:
            continue  # a plain path: one local instance
        scheme, _, rest = token.partition("://")
        host_port = rest.split("/", 1)[0]
        endpoints.append(f"{scheme}://{host_port}")
    return endpoints or [RUSTFS_FALLBACK_ENDPOINT], False


def _load_full_attributes(container) -> None:
    """Inspect *container* if the shared listing did not carry its attributes.

    The listing is sparse (see ``ContainerIndex``), so ``Config.Env`` is absent.
    RustFS is the only probe that needs it, and it needs it for exactly one
    container — so this is the one inspect call the run pays for. A failure is
    swallowed: the endpoint list then reads as guessed, which is the honest
    outcome for "we could not read RUSTFS_VOLUMES".
    """
    attributes = getattr(container, "attrs", None) or {}
    if "Config" in attributes:
        return
    reload_attributes = getattr(container, "reload", None)
    if reload_attributes is None:
        return
    try:
        reload_attributes()
    except Exception:  # noqa: S110
        # Best-effort refresh of an optional cache. Failing to reload leaves
        # the previous values in place, which is the same answer this probe
        # would have given a moment earlier.
        pass


# Below this, curl has no room to connect at all, so a share smaller than this
# is not worth issuing. Reached only above ~20 endpoints at the default 2.0 s
# timeout; past that point the probe can overrun and its budget task deadline
# takes over, which is the honest bound rather than a silently ignored one.
MIN_ENDPOINT_TIMEOUT = 0.1


def probe_rustfs(index: ContainerIndex, timeout: float = 2.0) -> ClusterService:
    """GET /health per endpoint — the only unauthenticated status RustFS offers.

    The endpoints are probed one ``docker exec`` at a time, so *timeout* is
    divided between them: five endpoints behind a blackhole must cost the
    configured budget for RustFS once, not five times a per-endpoint constant.
    """
    container, verdict, extras = locate_member(index, "rustfs", RUSTFS_PATTERNS)
    if verdict is not None:
        return verdict
    _load_full_attributes(container)
    endpoints, guessed = rustfs_endpoints(container)
    per_endpoint = max(MIN_ENDPOINT_TIMEOUT, timeout / max(1, len(endpoints)))
    members: list[ClusterMember] = []
    for endpoint in endpoints:
        try:
            # curl from inside the container: the rustfs overlay network
            # publishes no host ports.
            status = exec_text(
                container,
                [
                    "curl",
                    "-ks",
                    "-o",
                    "/dev/null",
                    "-m",
                    f"{per_endpoint:.1f}",
                    "-w",
                    "%{http_code}",
                    f"{endpoint}/health",
                ],
            ).strip()
            healthy = status == "200"
            detail = f"HTTP {status}"
        except Exception as exc:
            healthy = False
            detail = str(exc)[:60]
        members.append(ClusterMember(name=endpoint, role="peer", healthy=healthy, detail=detail))
    live = sum(1 for member in members if member.healthy)
    detail = f"{live}/{len(members)} live"
    if guessed:
        detail += " (endpoint list unknown)"
    service = ClusterService(
        kind="rustfs",
        name="rustfs",
        reachable=live > 0,
        leader=None,  # RustFS has no leader we can observe
        # /health is a liveness check only; erasure-coding and heal state
        # require the admin API (which answers 403). Majority quorum — but a
        # guessed endpoint list supports no quorum claim at all, and neither
        # does an empty one.
        quorum_ok=None if guessed or not members else live * 2 > len(members),
        detail=detail,
        members=members,
    )
    _note_extra_containers(service, extras)
    return service


_PROBES: dict[str, Callable[[ContainerIndex, float], ClusterService]] = {
    # The three docker-exec probes cannot enforce a timeout themselves:
    # docker-py bounds an exec by the client's socket timeout, not per call.
    # Their configured timeout is enforced by the budget thread that runs them
    # (``budget.run_with_budget``), and the health client's socket timeout is
    # built so that it never expires first (``cli._health_socket_timeout``).
    "postgres": lambda index, _timeout: probe_postgres(index),
    # Unlike its two neighbours this one *does* use its timeout: not to bound
    # the exec, which docker-py cannot do per call, but to compute the deadline
    # the fan-out inside mongosh checks itself.
    "mongodb": probe_mongodb,
    "kafka": lambda index, _timeout: probe_kafka(index),
    # These two spawn their own child process / curl and enforce it directly.
    "glusterfs": lambda _index, timeout: probe_glusterfs(timeout),
    "rustfs": probe_rustfs,
}


def probe_cluster(
    index: ContainerIndex, kind: str, timeout: float = DEFAULT_KIND_TIMEOUT
) -> ClusterService:
    """Probe one kind under its own *timeout*. Never raises.

    One kind per call because each kind is registered as its own budget task:
    a hung RustFS must not take a finished PostgreSQL result down with it.
    """
    probe = _PROBES.get(kind)
    if probe is None:
        return ClusterService(kind=kind, error="unknown kind")
    try:
        return probe(index, timeout)
    except Exception as exc:
        return ClusterService(kind=kind, error=f"{type(exc).__name__}: {exc}")
