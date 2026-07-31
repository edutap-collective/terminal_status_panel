"""Turn a DOCKER INFOS row into one ``Working`` cell.

The cell carries an icon and a count. They can come from different places: for
a clustered service the icon is the cluster's own verdict while the count stays
Docker's, so ``⚠️ 5/5`` reads "every broker is running and the quorum is
degraded anyway" — the case where a replica count on its own lies.

Pure: no Rich layout, no I/O, so the table of cases is testable directly.
"""

from __future__ import annotations

from rich.text import Text

from ..model import ClusterService, ServiceStatus
from . import icons

_STYLES = {
    icons.OK: "",
    icons.WARN: "yellow",
    icons.DEAD: "red",
    icons.FAILED: "red",
    icons.UNKNOWN: "dim",
}


def _counts(services: list[ServiceStatus], node_count: int) -> tuple[int, int]:
    """(running, desired) for a row, which bundles one service's per-node replicas."""
    running = sum(s.running_replicas for s in services)
    # A global-mode service reports no replica count: it wants one task per node.
    if all(s.desired_replicas is None for s in services):
        return running, node_count
    return running, sum(s.desired_replicas or 0 for s in services)


def _replica_icon(running: int, desired: int) -> str:
    if desired == 0:
        # Scaled to zero is a decision, not an outage. Rendering it as broken
        # would train people to ignore this column.
        return icons.UNKNOWN
    if running == 0:
        return icons.DEAD
    if running < desired:
        return icons.WARN
    return icons.OK


def _cluster_icon(cluster: ClusterService) -> str:
    if cluster.error:
        return icons.FAILED
    if not cluster.applicable:
        # The probe found no member on this node — a statement about the
        # observer, not about the service, which may run fine elsewhere.
        return icons.UNKNOWN
    if cluster.quorum_ok is None:
        return icons.UNKNOWN
    if not cluster.quorum_ok:
        return icons.DEAD
    # Quorum holds, but that alone can hide a minority of dead members — e.g.
    # RustFS genuinely reports quorum_ok=True at 3/5 live, since the quorum
    # rule is a majority. Only a member *measured* unhealthy counts here:
    # ``healthy is None`` means not observable (MongoDB reports membership but
    # not member state), and treating an unmeasured member as degraded would
    # be the same over-claim in the opposite direction.
    if any(member.healthy is False for member in cluster.members):
        return icons.WARN
    return icons.OK


def service_verdict(
    services: list[ServiceStatus],
    *,
    kind: str | None = None,
    cluster: ClusterService | None = None,
    node_count: int = 0,
) -> Text:
    """The ``Working`` cell for one row: an icon and a running/desired count."""
    if not services:
        return Text("")
    running, desired = _counts(services, node_count)
    if kind is None:
        icon = _replica_icon(running, desired)
    elif cluster is None:
        # A clustered service with no verdict: the health section did not run,
        # or this kind is not enabled. "Five brokers are running" is not the
        # claim this column makes, so it stays unobserved.
        icon = icons.UNKNOWN
    else:
        icon = _cluster_icon(cluster)
    return Text(f"{icon} {running}/{desired}", style=_STYLES.get(icon, ""))
