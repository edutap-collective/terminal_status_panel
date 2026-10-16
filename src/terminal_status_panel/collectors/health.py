# src/terminal_status_panel/collectors/health.py
"""Run the health checks concurrently and assemble HealthInfo.

The one place where the budget meets the collectors. A check that ran out of
budget lands in ``HealthInfo.truncated``; a check that raised lands in its own
dataclass with ``error`` set. Keeping those apart is the whole point.

Each cluster kind is its own budget task, with its own timeout: a hung RustFS
must not take a PostgreSQL result that finished two seconds ago down with it.
The kinds share one ``ContainerIndex`` so they ask the Docker daemon for its
container list once between them, and it is built here — inside the budget —
rather than on the login path's main thread.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from ..budget import run_with_budget
from ..config import Config
from ..model import ClusterService, DnsCheck, HealthInfo, PeerReachability
from ..model.host import HostCheck, HostStateRead
from .clusters import DEFAULT_KIND_TIMEOUT, ContainerIndex, ProbeSettings, probe_cluster
from .dns import collect_dns
from .host import HOST_KINDS, check_apache, check_vrrp, probe_systemd
from .network import collect_peers

# Cluster tasks are registered under a prefix so a configured kind can never
# collide with the "peers" or "dns" task names.
_CLUSTER_TASK_PREFIX = "cluster:"
_HOST_TASK_PREFIX = "host:"

#: Cluster kinds whose probe runs on this host and needs no Docker client.
HOST_RUN_CLUSTER_KINDS = ("glusterfs",)


@dataclass
class _HealthTasks:
    """The budgeted work, and what is needed afterwards to read its result."""

    tasks: dict[str, Callable[[], object]]
    timeouts: dict[str, float]
    #: Deduplicated, in configured order. A kind repeated in the config must
    #: not become two tasks under one name, and then one result reported twice.
    kinds: list[str]
    clusters_probed: bool
    #: Filled by the check threads, read after they finish: a peer list that
    #: had to be resolved counts as having probed the peers even when the
    #: check itself ran out of budget.
    resolved_peers: list[list[str]]
    #: Docker-bound kinds that could not run for lack of a client on a host that
    #: has Docker: reported as errors, not silently dropped.
    unchecked: list[str] = field(default_factory=list)


def _build_health_tasks(
    cfg: Config,
    peer_names: list[str],
    client,
    resolve_fqdn: Callable[[], str] | None,
    resolve_peer_names: Callable[[], list[str]] | None,
    docker_present: bool = True,
) -> _HealthTasks:
    """Everything the budget will run, with the deadline each one gets."""
    health_cfg = cfg.health
    tasks: dict[str, Callable[[], object]] = {}
    timeouts: dict[str, float] = {}

    kinds = list(dict.fromkeys(health_cfg.enabled))
    cluster_kinds = [k for k in kinds if k not in HOST_KINDS]
    # Docker-bound kinds need a client; host-run ones (glusterfs) do not.
    runnable = [k for k in cluster_kinds if client is not None or k in HOST_RUN_CLUSTER_KINDS]
    unchecked = [k for k in cluster_kinds if k not in runnable] if docker_present else []
    clusters_probed = bool(runnable) or bool(unchecked)
    index = ContainerIndex(client) if client is not None else None
    settings = ProbeSettings.from_config(cfg)
    for kind in runnable:
        timeout = health_cfg.timeouts.get(kind, DEFAULT_KIND_TIMEOUT)
        key = _CLUSTER_TASK_PREFIX + kind
        tasks[key] = partial(probe_cluster, index, kind, timeout, settings=settings)
        timeouts[key] = timeout
    if "systemd" in kinds:
        timeout = health_cfg.timeouts.get("systemd", 1.0)
        key = _HOST_TASK_PREFIX + "systemd"
        tasks[key] = partial(probe_systemd, timeout, health_cfg.systemd.ignore)
        timeouts[key] = timeout

    # Resolved at most once, by whichever of the two tasks needs it first.
    resolved_peers: list[list[str]] = []
    resolved_peers_lock = threading.Lock()

    def known_peer_names() -> list[str]:
        """The peer names, fetched at most once and only inside the budget."""
        if peer_names:
            return list(peer_names)
        if resolve_peer_names is None:
            return []
        with resolved_peers_lock:
            if not resolved_peers:
                try:
                    resolved_peers.append(list(resolve_peer_names() or []))
                except Exception:
                    # No node list is a gap in what we can ask about, not a
                    # statement about the peers — the checks say so themselves.
                    resolved_peers.append([])
        return list(resolved_peers[0])

    peers_timeout = health_cfg.timeouts.get("wireguard", 1.0)

    def peers_task() -> list[PeerReachability]:
        return collect_peers(known_peer_names(), timeout=peers_timeout)

    tasks["peers"] = peers_task
    timeouts["peers"] = peers_timeout

    dns_timeout = health_cfg.timeouts.get("dns", 2.5)

    def dns_task() -> list[DnsCheck]:
        fqdn = (resolve_fqdn or socket.getfqdn)()
        return collect_dns(
            fqdn=fqdn,
            peer_names=known_peer_names(),
            expectations=[(e.name, list(e.addresses)) for e in health_cfg.dns_expect],
            timeout=dns_timeout,
        )

    tasks["dns"] = dns_task
    timeouts["dns"] = dns_timeout

    return _HealthTasks(
        tasks=tasks,
        timeouts=timeouts,
        kinds=runnable,
        clusters_probed=clusters_probed,
        resolved_peers=resolved_peers,
        unchecked=unchecked,
    )


def _clusters_from(
    outcome, kinds: list[str], clusters_probed: bool, unchecked: list[str]
) -> list[ClusterService]:
    """One entry per kind that produced an answer, in configured order.

    A kind that ran out of time contributes no entry -- it is named in
    ``truncated`` instead, and the renderer says so next to the kinds that did
    finish. A raised exception is a statement about the check and becomes an
    error line; a blown budget is not and does not.
    """
    clusters: list[ClusterService] = []
    for kind in kinds if clusters_probed else []:
        key = _CLUSTER_TASK_PREFIX + kind
        if key in outcome.results:
            clusters.append(outcome.results[key])
        elif key in outcome.failed:
            clusters.append(ClusterService(kind=kind, error=outcome.failed[key]))
    clusters.extend(
        ClusterService(kind=kind, error="not checked (no Docker client)") for kind in unchecked
    )
    return clusters


def _peers_and_dns_from(outcome) -> tuple[list[PeerReachability], list[DnsCheck]]:
    """The two non-cluster checks, with a failure folded in as its own row."""
    peers: list[PeerReachability] = list(outcome.results.get("peers") or [])
    dns: list[DnsCheck] = list(outcome.results.get("dns") or [])
    if "peers" in outcome.failed:
        peers.append(
            PeerReachability(name="?", method="tcp", ok=False, detail=outcome.failed["peers"])
        )
    if "dns" in outcome.failed:
        dns.append(DnsCheck(label="DNS", ok=False, detail=outcome.failed["dns"]))
    return peers, dns


def _journal_error(read: HostStateRead, cfg: Config) -> str | None:
    """The journal read's error code, where host events are enabled and it failed."""
    if not cfg.trouble_host_events or read.state is None:
        return None
    return (read.state.errors or {}).get("events")


def collect_health(
    cfg: Config,
    peer_names: list[str],
    client=None,
    resolve_fqdn: Callable[[], str] | None = None,
    resolve_peer_names: Callable[[], list[str]] | None = None,
    host: HostStateRead | None = None,
    docker_present: bool = True,
) -> HealthInfo:
    """Collect cluster, peer and DNS health under the configured budget.

    Two things the checks need are deliberately *not* taken as finished values
    from the caller, because obtaining them can block:

    - the own hostname, resolved by *resolve_fqdn* (``socket.getfqdn`` by
      default): a forward and a reverse lookup through NSS, tens of seconds
      with a broken resolver — precisely the fault this section diagnoses;
    - the peer list, resolved by *resolve_peer_names* when *peer_names* is
      empty: a Swarm node list from the Docker daemon.

    Both are called inside the budget, on a check thread. *peer_names* stays a
    plain list because the caller often already has it for free (the Docker
    section collected it), and using it then costs nothing.
    """
    plan = _build_health_tasks(
        cfg, peer_names, client, resolve_fqdn, resolve_peer_names, docker_present
    )
    outcome = run_with_budget(plan.tasks, budget=cfg.health.budget, timeouts=plan.timeouts)

    peers, dns = _peers_and_dns_from(outcome)
    read = host or HostStateRead(problem="missing")
    host_checks: list[HostCheck] = []
    for kind in dict.fromkeys(cfg.health.enabled):
        key = _HOST_TASK_PREFIX + kind
        if kind == "vrrp":
            host_checks.append(check_vrrp(read, cfg.health.vrrp))
        elif kind == "apache":
            host_checks.append(check_apache(read, cfg.health.apache))
        elif kind == "systemd" and key in outcome.results:
            host_checks.append(outcome.results[key])
        elif kind == "systemd" and key in outcome.failed:
            host_checks.append(HostCheck("systemd", "error", outcome.failed[key]))
    return HealthInfo(
        clusters=_clusters_from(outcome, plan.kinds, plan.clusters_probed, plan.unchecked),
        peers=peers,
        dns=dns,
        host=host_checks,
        docker_present=docker_present,
        host_state_age=read.age_s,
        host_state_stale=read.stale,
        journal_error=_journal_error(read, cfg),
        truncated=[
            name.removeprefix(_CLUSTER_TASK_PREFIX).removeprefix(_HOST_TASK_PREFIX)
            for name in outcome.truncated
        ],
        clusters_probed=plan.clusters_probed,
        # We had either an answer or something to ask about -- a peer name to
        # check. A truncated peers check still had peer names, so it counts as
        # probed too.
        peers_probed=bool(peers)
        or bool(peer_names)
        or bool(plan.resolved_peers and plan.resolved_peers[0]),
        # Unconditionally True: the DNS check is always registered (resolver
        # reachability and the own name are always worth asking about). The
        # flag distinguishes a collected HealthInfo from one nobody collected,
        # not one run from another — see the field comment on HealthInfo.
        dns_probed=True,
    )
