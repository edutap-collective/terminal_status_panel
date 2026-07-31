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
from collections.abc import Callable
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


def collect_health(
    cfg: Config,
    peer_names: list[str],
    client=None,
    resolve_fqdn: Callable[[], str] | None = None,
) -> HealthInfo:
    """Collect cluster, peer and DNS health under the configured budget.

    The own hostname is resolved by *resolve_fqdn* (``socket.getfqdn`` by
    default) **inside** the budgeted DNS task, never by the caller: that call
    performs a forward and a reverse lookup through NSS and blocks for tens of
    seconds when the resolver is broken — precisely the fault this section
    exists to diagnose, and the login shell must not wait for it.
    """
    health_cfg = cfg.health
    tasks: dict[str, Callable[[], object]] = {}
    timeouts: dict[str, float] = {}

    # One task per kind, so a kind repeated in the config must not become two
    # tasks under one name (and then one result reported twice).
    kinds = list(dict.fromkeys(health_cfg.enabled))
    clusters_probed = client is not None and bool(kinds)
    if clusters_probed:
        index = ContainerIndex(client)
        for kind in kinds:
            timeout = health_cfg.timeouts.get(kind, DEFAULT_KIND_TIMEOUT)
            key = _CLUSTER_TASK_PREFIX + kind
            tasks[key] = partial(probe_cluster, index, kind, timeout)
            timeouts[key] = timeout

    peers_timeout = health_cfg.timeouts.get("wireguard", 1.0)
    tasks["peers"] = partial(collect_peers, peer_names, timeout=peers_timeout)
    timeouts["peers"] = peers_timeout

    dns_timeout = health_cfg.timeouts.get("dns", 2.5)

    def dns_task() -> list[DnsCheck]:
        fqdn = (resolve_fqdn or socket.getfqdn)()
        return collect_dns(
            fqdn=fqdn,
            peer_names=peer_names,
            expectations=[(e.name, list(e.addresses)) for e in health_cfg.dns_expect],
            timeout=dns_timeout,
        )

    tasks["dns"] = dns_task
    timeouts["dns"] = dns_timeout

    outcome = run_with_budget(tasks, budget=health_cfg.budget, timeouts=timeouts)

    # One entry per kind that produced an answer, in configured order. A kind
    # that ran out of time contributes no entry — it is named in ``truncated``
    # instead, and the renderer says so next to the kinds that did finish.
    clusters: list[ClusterService] = []
    for kind in kinds if clusters_probed else []:
        key = _CLUSTER_TASK_PREFIX + kind
        if key in outcome.results:
            clusters.append(outcome.results[key])
        elif key in outcome.failed:
            # A raised exception is a statement about the check; a blown budget
            # is not. Only the former becomes an error line.
            clusters.append(ClusterService(kind=kind, error=outcome.failed[key]))

    peers: list[PeerReachability] = list(outcome.results.get("peers") or [])
    dns: list[DnsCheck] = list(outcome.results.get("dns") or [])
    if "peers" in outcome.failed:
        peers.append(
            PeerReachability(
                name="?", method="tcp", ok=False, detail=outcome.failed["peers"]
            )
        )
    if "dns" in outcome.failed:
        dns.append(DnsCheck(label="DNS", ok=False, detail=outcome.failed["dns"]))

    truncated = [
        name.removeprefix(_CLUSTER_TASK_PREFIX) for name in outcome.truncated
    ]

    # We had either an answer or something to ask about — a peer name to check.
    # A truncated peers check still had peer_names, so it counts as probed too.
    peers_probed = bool(peers) or bool(peer_names)

    return HealthInfo(
        clusters=clusters,
        peers=peers,
        dns=dns,
        truncated=truncated,
        clusters_probed=clusters_probed,
        peers_probed=peers_probed,
        dns_probed="dns" in tasks,
    )
