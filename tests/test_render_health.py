# tests/test_render_health.py
from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ClusterMember,
    ClusterService,
    DnsCheck,
    HealthInfo,
    PeerReachability,
)
from terminal_status_panel.render.health import health_section


def _render(health, width=120):
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(health_section(health, Config()))
    return capture.get()


def test_missing_health_renders_a_placeholder_not_a_crash():
    assert "CLUSTER HEALTH" in _render(None)


def test_unprobed_clusters_are_not_rendered_as_all_clear():
    output = _render(HealthInfo(clusters_probed=False))
    assert "not checked" in output
    assert "no clustered services found" not in output


def test_probed_but_empty_clusters_say_so():
    output = _render(HealthInfo(clusters_probed=True))
    assert "no clustered services found" in output
    assert "not checked" not in output


def test_healthy_cluster_shows_leader_and_members():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="postgres", name="PostgreSQL-18", reachable=True,
            leader="pg18-lmzvd06-ccn-02", quorum_ok=True,
            members=[
                ClusterMember(name="pg18-lmzvd06-ccn-02", node="lmzvd06-ccn-02",
                              role="primary", healthy=True),
                ClusterMember(name="pg18-lmzvd06-ccn-03", node="lmzvd06-ccn-03",
                              role="secondary", healthy=True),
            ],
        )
    ])
    output = _render(health)
    assert "PostgreSQL-18" in output
    assert "lmzvd06-ccn-02" in output
    assert "primary" in output
    assert "✅" in output


def test_not_applicable_service_renders_na_and_no_failure_icon():
    health = HealthInfo(
        clusters_probed=True, clusters=[ClusterService(kind="mongodb", applicable=False)]
    )
    output = _render(health)
    assert "mongodb" in output.lower()
    assert "n/a" in output
    assert "💀" not in output


def test_unobservable_member_health_renders_a_neutral_dot():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="mongodb", name="lrz_app", reachable=True, quorum_ok=True,
            members=[ClusterMember(name="mongodb-4:27017", role="member", healthy=None)],
        )
    ])
    output = _render(health)
    assert "·" in output
    assert "✅ mongodb-4" not in output


def test_member_warning_is_visible():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="postgres", name="PostgreSQL-18", reachable=True,
            members=[ClusterMember(name="pg18-x", role="secondary", healthy=True,
                                   warning="lag")],
        )
    ])
    assert "lag" in _render(health)


def test_errored_service_shows_the_failure_marker_and_message():
    health = HealthInfo(
        clusters_probed=True,
        clusters=[ClusterService(kind="kafka", error="connection refused")],
    )
    output = _render(health)
    assert "✗" in output
    assert "connection refused" in output


def test_truncated_check_renders_ellipsis_not_a_failure():
    health = HealthInfo(clusters_probed=True, truncated=["clusters"])
    output = _render(health)
    assert "…" in output
    assert "✗" not in output


def test_peer_panel_shows_method_and_handshake_age():
    health = HealthInfo(peers=[
        PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31"),
        PeerReachability(name="ccn-02", method="wireguard", ok=False, detail="6:02"),
    ])
    output = _render(health)
    assert "wg" in output.lower()
    assert "0:31" in output
    assert "6:02" in output


def test_tcp_fallback_is_labelled_as_such():
    health = HealthInfo(peers=[
        PeerReachability(name="ccn-01", method="tcp", ok=True, detail="tcp/2377")
    ])
    assert "tcp" in _render(health).lower()


def test_dns_warning_renders_as_warning_not_failure():
    health = HealthInfo(dns=[DnsCheck(label="/etc/hosts", ok=None, detail="diverges: a")])
    output = _render(health)
    assert "⚠" in output
    assert "💀" not in output


def test_narrow_width_still_renders():
    health = HealthInfo(
        clusters=[ClusterService(kind="postgres", name="PostgreSQL-18", reachable=True)],
        peers=[PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31")],
        dns=[DnsCheck(label="Resolver", ok=True, detail="3 ms")],
    )
    assert "CLUSTER HEALTH" in _render(health, width=60)
