import socket
from types import SimpleNamespace

from terminal_status_panel.collectors import system
from terminal_status_panel.model import SystemInfo


def test_collect_system_populates_fields(monkeypatch):
    monkeypatch.setattr(system.platform, "node", lambda: "srv01")
    monkeypatch.setattr(system.platform, "release", lambda: "6.1.0-debian")
    monkeypatch.setattr(system.distro, "name", lambda pretty=False: "Debian GNU/Linux")
    monkeypatch.setattr(system.distro, "version", lambda: "12")
    monkeypatch.setattr(system.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(system.getpass, "getuser", lambda: "root")
    monkeypatch.setattr(
        system.psutil,
        "net_if_addrs",
        lambda: {
            "lo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            "eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
        },
    )

    info = system.collect_system()

    assert isinstance(info, SystemInfo)
    assert info.hostname == "srv01"
    assert info.kernel == "6.1.0-debian"
    assert info.os_name == "Debian GNU/Linux"
    assert info.os_version == "12"
    assert info.user == "root"
    assert "10.0.0.5" in info.ip_addresses
    assert "127.0.0.1" not in info.ip_addresses  # loopback filtered


def test_collect_system_degrades_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(system.psutil, "net_if_addrs", boom)
    # Should not raise; returns a SystemInfo (possibly partially empty).
    info = system.collect_system()
    assert isinstance(info, SystemInfo)
    assert info.ip_addresses == []
