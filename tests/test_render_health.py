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
    output = _render(
        HealthInfo(clusters_probed=True, peers_probed=True, dns_probed=True)
    )
    assert "no clustered services found" in output
    assert "not checked" not in output


def test_unprobed_dns_is_not_rendered_as_no_dns_checks():
    output = _render(HealthInfo(clusters_probed=True, peers_probed=True))
    assert "not checked" in output
    assert "no DNS checks" not in output


def test_probed_but_empty_dns_says_so():
    output = _render(
        HealthInfo(clusters_probed=True, peers_probed=True, dns_probed=True)
    )
    assert "no DNS checks" in output


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
    health = HealthInfo(clusters_probed=True, truncated=["postgres"])
    output = _render(health)
    assert "…" in output
    assert "✗" not in output


def test_a_truncated_kind_is_named_and_does_not_hide_the_others():
    """One budget task per kind: a hung RustFS must not take the PostgreSQL
    result that finished long ago down with it."""
    health = HealthInfo(
        clusters_probed=True,
        clusters=[ClusterService(kind="postgres", name="pg18", quorum_ok=False)],
        truncated=["rustfs"],
    )
    output = _render(health)
    assert "pg18" in output
    assert "💀" in output  # the PostgreSQL quorum loss is still reported
    assert "RustFS: time budget exceeded" in output
    assert "no clustered services found" not in output


def test_peer_panel_shows_method_and_handshake_age():
    health = HealthInfo(peers_probed=True, peers=[
        PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31"),
        PeerReachability(name="ccn-02", method="wireguard", ok=False, detail="6:02"),
    ])
    output = _render(health)
    assert "wg" in output.lower()
    assert "0:31" in output
    assert "6:02" in output


def test_tcp_fallback_is_labelled_as_such():
    health = HealthInfo(peers_probed=True, peers=[
        PeerReachability(name="ccn-01", method="tcp", ok=True, detail="tcp/2377")
    ])
    assert "tcp" in _render(health).lower()


def test_dns_warning_renders_as_warning_not_failure():
    health = HealthInfo(
        dns_probed=True,
        dns=[DnsCheck(label="/etc/hosts", ok=None, detail="diverges: a")],
    )
    output = _render(health)
    assert "⚠" in output
    assert "💀" not in output


def test_narrow_width_still_renders():
    health = HealthInfo(
        clusters_probed=True, peers_probed=True, dns_probed=True,
        clusters=[ClusterService(kind="postgres", name="PostgreSQL-18", reachable=True)],
        peers=[PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31")],
        dns=[DnsCheck(label="Resolver", ok=True, detail="3 ms")],
    )
    assert "CLUSTER HEALTH" in _render(health, width=60)


# -- Fix 1: the peers title reflects every peer's method, not just the first --

def test_mixed_peer_methods_render_as_mixed():
    health = HealthInfo(peers_probed=True, peers=[
        PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31"),
        PeerReachability(name="ccn-02", method="tcp", ok=True, detail="tcp/2377"),
    ])
    assert "mixed" in _render(health)


def test_all_wireguard_peers_still_render_wg():
    health = HealthInfo(peers_probed=True, peers=[
        PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31"),
        PeerReachability(name="ccn-02", method="wireguard", ok=True, detail="1:02"),
    ])
    output = _render(health)
    assert "wg" in output.lower()
    assert "mixed" not in output


def test_all_tcp_peers_still_render_tcp():
    health = HealthInfo(peers_probed=True, peers=[
        PeerReachability(name="ccn-01", method="tcp", ok=True, detail="tcp/2377"),
        PeerReachability(name="ccn-02", method="tcp", ok=True, detail="tcp/2377"),
    ])
    output = _render(health)
    assert "tcp" in output.lower()
    assert "mixed" not in output


# -- Fix 2: an unreported service-level quorum is distinct from "not observable" --

def test_unreported_quorum_says_so_and_has_no_neutral_dot_in_the_header():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(kind="rustfs", name="rustfs-vol", reachable=True, quorum_ok=None,
                       detail="2/2 nodes online"),
    ])
    output = _render(health)
    assert "quorum not reported" in output
    header_line = next(line for line in output.splitlines() if "rustfs-vol" in line)
    assert "·" not in header_line


def test_quorum_true_and_false_are_unchanged():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(kind="postgres", name="ok-cluster", reachable=True, quorum_ok=True),
        ClusterService(kind="postgres", name="bad-cluster", reachable=True, quorum_ok=False),
    ])
    output = _render(health)
    assert "✅" in output
    assert "💀" in output
    assert "quorum not reported" not in output


def test_unprobed_peers_are_not_rendered_as_no_peers():
    output = _render(HealthInfo(peers_probed=False))
    assert "not checked" in output
    assert "no peers detected" not in output


def test_probed_but_empty_peers_say_no_peers():
    output = _render(HealthInfo(peers_probed=True))
    assert "no peers detected" in output


# -- Task 5: cluster blocks flow into columns --


def _cluster(kind, name, members=2):
    return ClusterService(
        kind=kind, name=name, reachable=True, quorum_ok=True,
        members=[ClusterMember(name=f"{kind}-{i}", role="peer", healthy=True)
                 for i in range(members)],
    )


def test_wide_terminals_put_cluster_blocks_side_by_side():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        _cluster("kafka", "cluster-id"),
        _cluster("glusterfs", "shared"),
    ])
    out = _render(health, width=150)
    side_by_side = [ln for ln in out.splitlines()
                    if "PostgreSQL" in ln and "Kafka" in ln]
    assert side_by_side, "at 150 columns two clusters should share a line"


def test_narrow_terminals_stack_the_blocks():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        _cluster("kafka", "cluster-id"),
    ])
    out = _render(health, width=60)
    assert not [ln for ln in out.splitlines()
                if "PostgreSQL" in ln and "Kafka" in ln]


def test_not_applicable_services_collapse_to_one_line():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        ClusterService(kind="mongodb", applicable=False),
        ClusterService(kind="rustfs", applicable=False),
    ])
    out = _render(health, width=150)
    assert "n/a here:" in out
    assert "MongoDB" in out
    assert "RustFS" in out
    # One shared line, not one block each.
    assert len([ln for ln in out.splitlines() if "n/a here:" in ln]) == 1


def test_an_all_not_applicable_panel_is_just_the_summary_line():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(kind="mongodb", applicable=False),
    ])
    out = _render(health, width=150)
    assert "n/a here: MongoDB" in out


def test_a_failed_service_keeps_its_own_block():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        ClusterService(kind="rustfs", error="no running container"),
    ])
    out = _render(health, width=150)
    assert "no running container" in out
    assert "n/a here" not in out
