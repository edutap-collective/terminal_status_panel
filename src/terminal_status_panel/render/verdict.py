"""Turn a DOCKER INFOS row into one ``Working`` cell.

The cell carries an icon and a count. They can come from different places: for
a clustered service the icon is the cluster's own verdict while the count stays
Docker's, so RustFS at ``3/5 live`` — a minority of members measured unhealthy
while the majority quorum still holds — renders ``⚠️ 5/5``: every container is
up as a Docker task, which is the case where a replica count on its own lies.

The reconciliation runs the other way too. See ``_combined_icon``: a replica
state measured ``💀`` or ``⚠️`` is a fact about *this* Docker service and is
never softened by a cluster-level ``✅`` or ``·``.

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
    icons.JOB: "",
}

# How much each icon claims, from least to most. "Not observable" ranks *above*
# OK on purpose: an unmeasured cluster must never render as a clean bill of
# health. ``✗`` tops the scale because it names a failed probe — as severe as
# 💀 and more specific about why.
_SEVERITY = {
    icons.OK: 0,
    # A job resting between successful runs claims exactly as much as ✅: it was
    # measured, and what was measured is fine.
    icons.JOB: 0,
    icons.UNKNOWN: 1,
    icons.WARN: 2,
    icons.DEAD: 3,
    icons.FAILED: 4,
}


def _desired(service: ServiceStatus, node_count: int) -> int:
    """How many tasks this service wants.

    A replicated service says so itself. A global-mode service reports no
    replica count — it wants one task per *eligible* node, and a drained node
    or a placement constraint makes it fewer than all of them: Swarm removes
    the task from a drained node, so counting against every node would render
    a healthy global service as permanently degraded. The tasks Docker
    actually scheduled are measurable, and they are the same denominator
    ``docker service ls`` shows, since the collector already filters tasks to
    desired-state ``running``. Only a row that carries no task data at all
    falls back to the node count.
    """
    if service.desired_replicas is not None:
        return service.desired_replicas
    scheduled = len(service.tasks) + service.unassigned
    return scheduled or node_count


def _counts(services: list[ServiceStatus], node_count: int) -> tuple[int, int]:
    """(running, desired) for a row, which bundles one service's per-node replicas."""
    running = sum(s.running_replicas for s in services)
    return running, sum(_desired(s, node_count) for s in services)


def _replica_icon(running: int, desired: int, starting: int = 0) -> str:
    if desired == 0:
        # Scaled to zero is a decision, not an outage. Rendering it as broken
        # would train people to ignore this column.
        return icons.UNKNOWN
    if running == 0:
        # Nothing runs -- but a task on its way up has not been measured
        # broken, and a deploy in progress must not read like an outage.
        return icons.WARN if starting else icons.DEAD
    if running < desired:
        return icons.WARN
    return icons.OK


def _fmt_age(seconds: float | None) -> str:
    """How long ago, in one unit. Coarse on purpose: the cell is three glyphs
    wide, and "ran 12 hours ago" answers the question that "12h 04m" does."""
    if seconds is None:
        return "?"
    total = int(seconds)
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _job_cell(services: list[ServiceStatus]) -> tuple[str, str] | None:
    """(icon, text) for a row of scheduled jobs, or None if this is not one.

    A job's resting state is zero running tasks, so the replica rule -- which
    reads ``0/1`` as an outage -- states the opposite of the truth here and is
    replaced rather than adjusted. What the row reports instead is the outcome
    of the last run, because that is the only thing about a sleeping job that
    can be measured at all.

    Only an all-job row qualifies. A row mixing a job with a long-running
    service still has something that ought to be up, and the replica count
    remains the honest answer for it.
    """
    if not all(service.job for service in services):
        return None
    running = sum(service.running_replicas for service in services)
    if running:
        # A run in progress is an ordinary service for as long as it lasts.
        desired = sum(_desired(service, 0) for service in services)
        return _replica_icon(running, desired), f"{running}/{desired}"
    runs = [service.last_run for service in services if service.last_run]
    if not runs:
        # Never ran, or Swarm has already pruned the history: either way this
        # is an absence of evidence, not evidence of health.
        return icons.UNKNOWN, "never"
    newest = min(runs, key=lambda run: (run.age_seconds is None, run.age_seconds or 0))
    age = _fmt_age(newest.age_seconds)
    if newest.failed:
        return icons.DEAD, f"{icons.FAILED} {age}"
    return icons.JOB, f"ok {age}"


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


def _combined_icon(replica: str, cluster: str) -> str:
    """Reconcile two independent measurements of the same row.

    The cluster verdict is the more specific statement and normally wins — it
    is what makes ``⚠️ 5/5`` and ``· 5/5`` possible at all. But ``💀``/``⚠️``
    from the replica count are measurements of *this* Docker service, and the
    join key is a substring match: a service that merely shares a cluster's
    stack can pick up its verdict. So a degraded replica state wins whenever
    it is strictly more severe than the cluster's, and a row with nothing to
    say (``✅`` fully staffed, ``·`` scaled to zero) never overrides anything:

        replica \\ cluster   ✅    ·     ⚠️    💀    ✗
        ✅ (n/n)             ✅    ·     ⚠️    💀    ✗
        · (0/0)              ✅    ·     ⚠️    💀    ✗
        ⚠️ (0/n or 1..n-1/n) ⚠️    ⚠️    ⚠️    💀    ✗
        💀 (0/n not starting)💀    💀    💀    💀    ✗
    """
    if replica in (icons.DEAD, icons.WARN) and _SEVERITY[replica] > _SEVERITY[cluster]:
        return replica
    return cluster


def verdict_icon(
    services: list[ServiceStatus],
    *,
    kind: str | None = None,
    cluster: ClusterService | None = None,
    node_count: int = 0,
) -> str:
    """The verdict glyph alone, for callers that summarise rather than tabulate.

    Exists so that a caller needing only the severity does not re-derive it or,
    worse, read it back out of the rendered cell: the two would drift, and this
    project has already been bitten once by the same rule living in two places.
    """
    if not services:
        return ""
    job = _job_cell(services)
    if job is not None:
        return job[0]
    running, desired = _counts(services, node_count)
    starting = sum(1 for s in services for t in s.tasks if t.starting)
    replica = _replica_icon(running, desired, starting)
    if kind is None:
        return replica
    if cluster is None:
        # A clustered service with no verdict: the health section did not run,
        # or this kind is not enabled. "Five brokers are running" is not the
        # claim this column makes, so the cluster side contributes nothing —
        # but _combined_icon still lets a replica state more severe than
        # "unobserved" through, which is what renders ``💀 0/3`` here.
        return _combined_icon(replica, icons.UNKNOWN)
    return _combined_icon(replica, _cluster_icon(cluster))


def severity(icon: str) -> int:
    """How much a glyph claims. Unknown glyphs rank lowest, claiming nothing."""
    return _SEVERITY.get(icon, -1)


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
    job = _job_cell(services)
    if job is not None:
        icon, text = job
        return Text(f"{icon} {text}", style=_STYLES.get(icon, ""))
    icon = verdict_icon(services, kind=kind, cluster=cluster, node_count=node_count)
    running, desired = _counts(services, node_count)
    return Text(f"{icon} {running}/{desired}", style=_STYLES.get(icon, ""))
