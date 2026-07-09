from lmu.terminal_status_panel.model import (
    FilesystemUsage,
    PanelData,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
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
