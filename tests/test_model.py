from terminal_status_panel.model import (
    FilesystemUsage,
    PanelData,
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
