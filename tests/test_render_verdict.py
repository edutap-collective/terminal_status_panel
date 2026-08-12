import pytest

from terminal_status_panel.model import (
    ClusterMember,
    ClusterService,
    JobRun,
    ServiceStatus,
    ServiceTask,
)
from terminal_status_panel.render import icons
from terminal_status_panel.render.verdict import service_verdict


def _svc(running, desired):
    return ServiceStatus(name="x", running_replicas=running, desired_replicas=desired)


def _cell(services, kind=None, cluster=None, node_count=5):
    return service_verdict(services, kind=kind, cluster=cluster, node_count=node_count).plain


@pytest.mark.parametrize(
    "running,desired,expected",
    [
        (3, 3, f"{icons.OK} 3/3"),      # fully staffed
        (2, 5, f"{icons.WARN} 2/5"),    # serving, degraded
        (0, 3, f"{icons.DEAD} 0/3"),    # wants replicas, has none
        (0, 0, f"{icons.UNKNOWN} 0/0"),  # scaled to zero on purpose
    ],
)
def test_replica_states(running, desired, expected):
    assert _cell([_svc(running, desired)]) == expected


def test_a_row_sums_its_per_node_replicas():
    """One row bundles the per-node replicas of one logical service."""
    assert _cell([_svc(1, 1), _svc(1, 1), _svc(0, 1)]) == f"{icons.WARN} 2/3"


def _global(running, states, unassigned=0):
    """A global-mode service (no replica count) with per-node task states."""
    return ServiceStatus(
        name="traefik_traefik",
        running_replicas=running,
        desired_replicas=None,
        tasks=[ServiceTask(node=f"node{i}", state=s) for i, s in enumerate(states)],
        unassigned=unassigned,
    )


def test_global_mode_counts_the_tasks_swarm_scheduled():
    """Swarm removes a global service's task from a drained node, so counting
    against every node would render a healthy service permanently degraded —
    the panel asserting a degradation it never measured."""
    four_of_five_nodes = ["running"] * 4
    assert _cell([_global(4, four_of_five_nodes)], node_count=5) == f"{icons.OK} 4/4"


def test_a_global_service_missing_a_task_still_warns():
    assert (
        _cell([_global(3, ["running"] * 3 + ["failed"])], node_count=5)
        == f"{icons.WARN} 3/4"
    )


def test_a_global_task_pinned_to_a_dead_node_still_counts_as_wanted():
    """An unassigned task is one Swarm wants to place and cannot."""
    assert (
        _cell([_global(3, ["running"] * 3, unassigned=1)], node_count=5)
        == f"{icons.WARN} 3/4"
    )


def test_global_mode_falls_back_to_the_node_count_without_task_data():
    assert _cell([_svc(5, None)], node_count=5) == f"{icons.OK} 5/5"
    assert _cell([_svc(3, None)], node_count=5) == f"{icons.WARN} 3/5"
    assert _cell([_svc(0, None)], node_count=5) == f"{icons.DEAD} 0/5"


def test_running_above_desired_is_not_degraded():
    assert _cell([_svc(4, 3)]) == f"{icons.OK} 4/3"


def test_a_clustered_service_without_a_verdict_is_not_observable():
    """status-docker runs without the health section; five brokers running is
    not the claim this column makes."""
    assert _cell([_svc(5, 5)], kind="kafka") == f"{icons.UNKNOWN} 5/5"


def test_a_dead_clustered_row_still_shows_dead_without_a_verdict():
    """Withholding the cluster's claim is not a reason to withhold Docker's:
    'no task is running' was measured here, health section or not."""
    assert _cell([_svc(0, 3)], kind="kafka") == f"{icons.DEAD} 0/3"


def test_the_cluster_verdict_beats_the_replica_count():
    degraded = ClusterService(kind="kafka", quorum_ok=False)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=degraded) == f"{icons.DEAD} 5/5"


def test_a_healthy_quorum_shows_ok():
    healthy = ClusterService(kind="kafka", quorum_ok=True)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=healthy) == f"{icons.OK} 5/5"


def test_an_unreported_quorum_is_not_observable():
    unreported = ClusterService(kind="kafka", quorum_ok=None)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=unreported) == f"{icons.UNKNOWN} 5/5"


def test_a_failed_probe_shows_the_failure_marker():
    broken = ClusterService(kind="rustfs", error="no running container")
    assert _cell([_svc(0, 1)], kind="rustfs", cluster=broken) == f"{icons.FAILED} 0/1"


def test_not_applicable_here_says_nothing_about_the_service():
    """The probe found no member on THIS node. That is a statement about the
    observer, not about the service, which may run fine elsewhere — so a row
    whose own replicas are all up stays unobserved rather than green."""
    elsewhere = ClusterService(kind="rustfs", applicable=False)
    assert _cell([_svc(5, 5)], kind="rustfs", cluster=elsewhere) == f"{icons.UNKNOWN} 5/5"


def test_an_empty_row_renders_nothing_rather_than_a_verdict():
    assert _cell([]) == ""


def test_a_measured_dead_row_beats_a_healthy_cluster_verdict():
    """The join key is a substring match, so a service that merely shares a
    cluster's stack can pick up its verdict. A row with zero running tasks was
    *measured* dead; rendering it green because some other service's quorum
    holds is the blank-row failure in a louder form."""
    healthy = ClusterService(kind="mongodb", quorum_ok=True)
    assert _cell([_svc(0, 1)], kind="mongodb", cluster=healthy) == f"{icons.DEAD} 0/1"


def test_a_partially_staffed_row_beats_a_healthy_cluster_verdict():
    healthy = ClusterService(kind="rustfs", quorum_ok=True)
    assert _cell([_svc(2, 5)], kind="rustfs", cluster=healthy) == f"{icons.WARN} 2/5"


def test_a_measured_dead_row_beats_an_unobservable_cluster():
    """'The probe found no member here' says nothing about the service — but
    'no task is running' is a measurement of this Docker service."""
    elsewhere = ClusterService(kind="rustfs", applicable=False)
    assert _cell([_svc(0, 3)], kind="rustfs", cluster=elsewhere) == f"{icons.DEAD} 0/3"


def test_a_dead_row_does_not_soften_a_failed_probe():
    """✗ is the more specific statement and no less severe, so it stands."""
    broken = ClusterService(kind="rustfs", error="no running container")
    assert _cell([_svc(0, 1)], kind="rustfs", cluster=broken) == f"{icons.FAILED} 0/1"


def test_a_degraded_row_does_not_soften_a_lost_quorum():
    lost = ClusterService(kind="kafka", quorum_ok=False)
    assert _cell([_svc(2, 5)], kind="kafka", cluster=lost) == f"{icons.DEAD} 2/5"


def test_a_dead_row_beats_a_warning_cluster():
    degraded = ClusterService(
        kind="rustfs", quorum_ok=True,
        members=[ClusterMember(name="node1", healthy=False)],
    )
    assert _cell([_svc(0, 5)], kind="rustfs", cluster=degraded) == f"{icons.DEAD} 0/5"


def test_a_row_scaled_to_zero_leaves_the_cluster_verdict_alone():
    """0/0 is a decision, not a measurement of trouble, so it overrides nothing."""
    healthy = ClusterService(kind="rustfs", quorum_ok=True)
    assert _cell([_svc(0, 0)], kind="rustfs", cluster=healthy) == f"{icons.OK} 0/0"


def test_a_minority_of_dead_members_warns_even_though_quorum_holds():
    """RustFS genuinely reports quorum_ok=True at 3/5 live: the quorum rule is
    a majority. Rendering that as plain OK is the "1 of 5 looks like 5 of 5"
    failure the three-level scheme exists to prevent."""
    degraded_majority = ClusterService(
        kind="rustfs",
        quorum_ok=True,
        members=[
            ClusterMember(name="node1", healthy=True),
            ClusterMember(name="node2", healthy=True),
            ClusterMember(name="node3", healthy=True),
            ClusterMember(name="node4", healthy=False),
            ClusterMember(name="node5", healthy=False),
        ],
    )
    assert (
        _cell([_svc(5, 5)], kind="rustfs", cluster=degraded_majority)
        == f"{icons.WARN} 5/5"
    )


def test_unobserved_members_do_not_warn():
    """MongoDB reports replica-set membership but not member state. An
    unmeasured member (healthy=None) must not be treated as degraded — that
    would be the same over-claim in the opposite direction."""
    unmeasured = ClusterService(
        kind="mongodb",
        quorum_ok=True,
        members=[
            ClusterMember(name="node1", healthy=None),
            ClusterMember(name="node2", healthy=None),
            ClusterMember(name="node3", healthy=True),
        ],
    )
    assert _cell([_svc(3, 3)], kind="mongodb", cluster=unmeasured) == f"{icons.OK} 3/3"


def test_all_members_healthy_shows_ok():
    """Guard against the warn branch firing too eagerly."""
    all_healthy = ClusterService(
        kind="rustfs",
        quorum_ok=True,
        members=[
            ClusterMember(name="node1", healthy=True),
            ClusterMember(name="node2", healthy=True),
        ],
    )
    assert _cell([_svc(2, 2)], kind="rustfs", cluster=all_healthy) == f"{icons.OK} 2/2"


def test_no_member_data_shows_ok():
    """Quorum holds and there is no member list at all: nothing to warn about."""
    no_members = ClusterService(kind="rustfs", quorum_ok=True, members=[])
    assert _cell([_svc(5, 5)], kind="rustfs", cluster=no_members) == f"{icons.OK} 5/5"


def test_a_service_whose_tasks_are_all_starting_is_degraded_not_dead():
    """0/3 with three containers coming up is a deploy in progress, not an
    outage. The count stays honest either way."""
    tasks = [ServiceTask(f"srv-0{i}", "preparing") for i in (1, 2, 3)]
    services = [ServiceStatus("s", 0, 3, tasks=tasks)]
    assert service_verdict(services).plain == "⚠️ 0/3"


def test_a_service_whose_tasks_failed_is_still_dead():
    tasks = [ServiceTask(f"srv-0{i}", "failed") for i in (1, 2, 3)]
    services = [ServiceStatus("s", 0, 3, tasks=tasks)]
    assert service_verdict(services).plain == "💀 0/3"


# --------------------------------------------------------------------------- #
# Scheduled jobs
# --------------------------------------------------------------------------- #


def _job(running=0, desired=1, last_run=None, schedule="0 5 * * *"):
    return ServiceStatus(
        name="nightly", running_replicas=running, desired_replicas=desired,
        job=True, schedule=schedule, last_run=last_run,
    )


def test_a_job_between_runs_is_not_dead():
    """The bug this feature exists for: 0/1 is a job's resting state."""
    run = JobRun(state="complete", age_seconds=12 * 3600, node="srv-01")

    assert _cell([_job(last_run=run)]) == f"{icons.JOB} ok 12h"


def test_a_job_whose_last_run_failed_is_dead():
    run = JobRun(state="failed", age_seconds=20 * 3600, node="srv-01")

    assert _cell([_job(last_run=run)]) == f"{icons.DEAD} ✗ 20h"


def test_a_job_that_never_ran_is_not_observable():
    assert _cell([_job(last_run=None)]) == f"{icons.UNKNOWN} never"


def test_a_job_with_a_task_running_reports_replicas():
    """While it runs, a job is an ordinary service and the count is the answer."""
    run = JobRun(state="running", age_seconds=5.0, node="srv-01")

    assert _cell([_job(running=1, desired=1, last_run=run)]) == f"{icons.OK} 1/1"
