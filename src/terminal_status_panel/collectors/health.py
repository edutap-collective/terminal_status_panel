# src/terminal_status_panel/collectors/health.py
"""Run the health checks concurrently and assemble HealthInfo.

The one place where the budget meets the collectors. A check that ran out of
budget lands in ``HealthInfo.truncated``; a check that raised lands in its own
dataclass with ``error`` set. Keeping those apart is the whole point.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

from ..budget import run_with_budget
from ..config import Config
from ..model import ClusterService, DnsCheck, HealthInfo, PeerReachability
from .clusters import collect_clusters
from .dns import collect_dns
from .network import collect_peers


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
    tasks = {}

    clusters_probed = client is not None and bool(health_cfg.enabled)
    if clusters_probed:
        tasks["clusters"] = lambda: collect_clusters(client, list(health_cfg.enabled))

    tasks["peers"] = lambda: collect_peers(
        peer_names, timeout=health_cfg.timeouts.get("wireguard", 1.0)
    )

    def dns_task() -> list[DnsCheck]:
        fqdn = (resolve_fqdn or socket.getfqdn)()
        return collect_dns(
            fqdn=fqdn,
            peer_names=peer_names,
            expectations=[(e.name, list(e.addresses)) for e in health_cfg.dns_expect],
            timeout=health_cfg.timeouts.get("dns", 2.5),
        )

    tasks["dns"] = dns_task

    outcome = run_with_budget(tasks, budget=health_cfg.budget)

    clusters: list[ClusterService] = list(outcome.results.get("clusters") or [])
    peers: list[PeerReachability] = list(outcome.results.get("peers") or [])
    dns: list[DnsCheck] = list(outcome.results.get("dns") or [])

    # A raised exception is a statement about the check; a blown budget is not.
    for name, message in outcome.failed.items():
        if name == "clusters":
            clusters.append(ClusterService(kind="clusters", error=message))
        elif name == "peers":
            peers.append(PeerReachability(name="?", method="tcp", ok=False, detail=message))
        elif name == "dns":
            dns.append(DnsCheck(label="DNS", ok=False, detail=message))

    # We had either an answer or something to ask about — a peer name to check.
    # A truncated peers check still had peer_names, so it counts as probed too.
    peers_probed = bool(peers) or bool(peer_names)

    return HealthInfo(
        clusters=clusters,
        peers=peers,
        dns=dns,
        truncated=list(outcome.truncated),
        clusters_probed=clusters_probed,
        peers_probed=peers_probed,
    )
