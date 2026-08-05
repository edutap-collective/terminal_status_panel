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
    TraefikEntrypoint,
    TraefikInfo,
    TraefikMiddleware,
    TraefikRouter,
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
    member = ClusterMember(name="pg18-swarm01-wrk-02")
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


def test_traefik_entrypoint_carries_name_and_address():
    ep = TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020)
    assert (ep.name, ep.address, ep.port) == ("portalmgmt", ":2020", 2020)


def test_a_router_defaults_to_unconsulted_rather_than_accepted():
    """rejected is None until the Traefik API was actually asked."""
    router = TraefikRouter(name="kafbat-ui")
    assert router.rejected is None
    assert router.source == "swarm"
    assert router.entrypoints == []
    assert router.middlewares == []


def test_a_middleware_keeps_its_kind_and_detail():
    mw = TraefikMiddleware(name="image_api_stripprefix", kind="stripprefix",
                           detail="prefixes=/wallet/image-api")
    assert mw.kind == "stripprefix"


def test_traefik_info_defaults_are_empty_and_unconsulted():
    info = TraefikInfo()
    assert info.reachable is False
    assert info.entrypoints == []
    assert info.routers == []
    assert info.middlewares == {}
    assert info.services == {}
    assert info.api_consulted is False
    assert info.error is None
    assert info.file_provider_error is None


def test_panel_data_carries_traefik():
    assert PanelData().traefik is None
    info = TraefikInfo(reachable=True)
    assert PanelData(traefik=info).traefik.reachable is True


def test_the_states_docker_passes_through_before_running_are_starting():
    """Not running yet is not the same as measured broken."""
    from terminal_status_panel.model import ServiceTask

    for state in ("new", "allocated", "pending", "assigned", "accepted",
                  "preparing", "ready", "starting"):
        task = ServiceTask(node="srv-01", state=state)
        assert task.starting is True, state
        assert task.running is False, state


def test_running_and_failed_are_not_starting():
    from terminal_status_panel.model import ServiceTask

    assert ServiceTask(node="srv-01", state="running").starting is False
    assert ServiceTask(node="srv-01", state="failed").starting is False
    assert ServiceTask(node="srv-01", state="shutdown").starting is False


# --- one_way must not fire on missing data ----------------------------------

def test_one_way_needs_both_counters():
    """A parse failure leaves a counter at None. Reporting "one-way" from that
    would turn missing data into a diagnosis."""
    from terminal_status_panel.model import PeerReachability

    partial = PeerReachability(name="p", method="wireguard", rx_bytes=None, tx_bytes=100)
    assert partial.one_way is False

    partial = PeerReachability(name="p", method="wireguard", rx_bytes=0, tx_bytes=None)
    assert partial.one_way is False

    real = PeerReachability(name="p", method="wireguard", rx_bytes=0, tx_bytes=100)
    assert real.one_way is True
