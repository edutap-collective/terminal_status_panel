from terminal_status_panel.model import (
    ClusterMember,
    ClusterService,
    DnsCheck,
    FilesystemUsage,
    HealthInfo,
    PanelData,
    PeerReachability,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
    SwarmNode,
    SystemInfo,
)


def test_dataclasses_have_graceful_defaults():
    # Every aggregate can be constructed empty (degraded state).
    assert SystemInfo().ip_addresses == []
    assert ResourceUsage().filesystems == []
    assert SwarmInfo().reachable is False
    assert SwarmInfo().services == []
    assert PanelData().system is None


def test_value_dataclasses_hold_fields():
    fs = FilesystemUsage(mountpoint="/", total=100, used=91, percent=91.0)
    assert (fs.mountpoint, fs.percent) == ("/", 91.0)
    svc = ServiceStatus(name="postgres", running_replicas=1, desired_replicas=1)
    assert svc.critical is False


def test_swarm_node_operational_requires_ready_and_active():
    # No availability reported (older daemons) counts as active.
    assert SwarmNode("n1", reachable=True).operational is True
    assert SwarmNode("n1", reachable=True, availability="active").operational is True
    # Ready, but administratively withdrawn -> not usable.
    assert SwarmNode("n1", reachable=True, availability="drain").operational is False
    assert SwarmNode("n1", reachable=True, availability="pause").operational is False
    # Unreachable is never operational, whatever the availability says.
    assert SwarmNode("n1", reachable=False, availability="active").operational is False


def test_cluster_member_defaults_to_unobserved_health():
    member = ClusterMember(name="pg18-lmzvd06-ccn-02")
    assert member.healthy is None
    assert member.role is None
    assert member.warning is None


def test_cluster_service_defaults_are_degraded_not_healthy():
    service = ClusterService(kind="postgres")
    assert service.applicable is True
    assert service.reachable is False
    assert service.leader is None
    assert service.quorum_ok is None
    assert service.members == []
    assert service.error is None


def test_health_info_defaults_are_empty():
    health = HealthInfo()
    assert health.clusters == []
    assert health.peers == []
    assert health.dns == []
    assert health.truncated == []


def test_panel_data_carries_health():
    assert PanelData().health is None
    health = HealthInfo(peers=[PeerReachability(name="ccn-01", method="wireguard")])
    assert PanelData(health=health).health.peers[0].method == "wireguard"


def test_dns_check_none_means_warning():
    assert DnsCheck(label="/etc/hosts", ok=None, detail="1 Abweichung").ok is None


def test_health_info_defaults_to_unprobed_peers():
    assert HealthInfo().peers_probed is False
