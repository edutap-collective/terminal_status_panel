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
from dataclasses import dataclass
from functools import partial

from ..budget import run_with_budget
from ..config import Config
from ..model import ClusterService, DnsCheck, HealthInfo, PeerReachability
from .clusters import DEFAULT_KIND_TIMEOUT, ContainerIndex, probe_cluster
from .dns import collect_dns
from .network import collect_peers

# Cluster tasks are registered under a prefix so a configured kind can never
# collide with the "peers" or "dns" task names.
_CLUSTER_TASK_PREFIX = "cluster:"


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


def _build_health_tasks(
    cfg: Config,
    peer_names: list[str],
    client,
    resolve_fqdn: Callable[[], str] | None,
    resolve_peer_names: Callable[[], list[str]] | None,
) -> _HealthTasks:
    """Everything the budget will run, with the deadline each one gets."""
    health_cfg = cfg.health
    tasks: dict[str, Callable[[], object]] = {}
    timeouts: dict[str, float] = {}

    kinds = list(dict.fromkeys(health_cfg.enabled))
    clusters_probed = client is not None and bool(kinds)
    if clusters_probed:
        index = ContainerIndex(client)
        for kind in kinds:
            timeout = health_cfg.timeouts.get(kind, DEFAULT_KIND_TIMEOUT)
            key = _CLUSTER_TASK_PREFIX + kind
            tasks[key] = partial(probe_cluster, index, kind, timeout)
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
        kinds=kinds,
        clusters_probed=clusters_probed,
        resolved_peers=resolved_peers,
    )


def _clusters_from(outcome, kinds: list[str], clusters_probed: bool) -> list[ClusterService]:
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


def collect_health(
    cfg: Config,
    peer_names: list[str],
    client=None,
    resolve_fqdn: Callable[[], str] | None = None,
    resolve_peer_names: Callable[[], list[str]] | None = None,
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
    plan = _build_health_tasks(cfg, peer_names, client, resolve_fqdn, resolve_peer_names)
    outcome = run_with_budget(plan.tasks, budget=cfg.health.budget, timeouts=plan.timeouts)

    peers, dns = _peers_and_dns_from(outcome)
    return HealthInfo(
        clusters=_clusters_from(outcome, plan.kinds, plan.clusters_probed),
        peers=peers,
        dns=dns,
        truncated=[name.removeprefix(_CLUSTER_TASK_PREFIX) for name in outcome.truncated],
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
