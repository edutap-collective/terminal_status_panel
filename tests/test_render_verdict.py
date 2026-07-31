import pytest

from terminal_status_panel.model import ClusterService, ServiceStatus
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


def test_global_mode_counts_against_the_node_count():
    assert _cell([_svc(5, None)], node_count=5) == f"{icons.OK} 5/5"
    assert _cell([_svc(3, None)], node_count=5) == f"{icons.WARN} 3/5"
    assert _cell([_svc(0, None)], node_count=5) == f"{icons.DEAD} 0/5"


def test_running_above_desired_is_not_degraded():
    assert _cell([_svc(4, 3)]) == f"{icons.OK} 4/3"


def test_a_clustered_service_without_a_verdict_is_not_observable():
    """status-docker runs without the health section; five brokers running is
    not the claim this column makes."""
    assert _cell([_svc(5, 5)], kind="kafka") == f"{icons.UNKNOWN} 5/5"


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
    observer, not about the service, which may run fine elsewhere."""
    elsewhere = ClusterService(kind="rustfs", applicable=False)
    assert _cell([_svc(4, 5)], kind="rustfs", cluster=elsewhere) == f"{icons.UNKNOWN} 4/5"


def test_an_empty_row_renders_nothing_rather_than_a_verdict():
    assert _cell([]) == ""
