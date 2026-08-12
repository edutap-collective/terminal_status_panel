import plistlib
import socket
from types import SimpleNamespace

from terminal_status_panel.collectors import system
from terminal_status_panel.collectors import system as system_collector
from terminal_status_panel.model import SystemInfo


def test_collect_system_populates_fields(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
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
    assert info.kernel == "Linux 6.1.0-debian"
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


def test_darwin_identity_comes_from_the_system_version_plist(monkeypatch, tmp_path):
    plist = tmp_path / "SystemVersion.plist"
    plist.write_bytes(plistlib.dumps({"ProductName": "macOS", "ProductVersion": "26.5.2"}))
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_collector, "MACOS_VERSION_PLIST", str(plist))

    assert system_collector._os_identity() == ("macOS", "26.5.2")


def test_darwin_without_a_readable_plist_reports_nothing(monkeypatch, tmp_path):
    """No fabricated fallback: an unidentifiable system says so."""
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_collector, "MACOS_VERSION_PLIST", str(tmp_path / "absent.plist"))

    assert system_collector._os_identity() == (None, None)


def test_linux_identity_comes_from_distro(monkeypatch):
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        system_collector.distro, "name", lambda pretty=False: "Debian GNU/Linux 12 (bookworm)"
    )
    monkeypatch.setattr(system_collector.distro, "version", lambda: "12")

    assert system_collector._os_identity() == ("Debian GNU/Linux 12 (bookworm)", "12")


def test_rhel_family_identity(monkeypatch):
    """Pins behaviour that already works, so a refactor cannot break it silently."""
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        system_collector.distro,
        "name",
        lambda pretty=False: "Red Hat Enterprise Linux 9.4 (Plow)",
    )
    monkeypatch.setattr(system_collector.distro, "version", lambda: "9.4")

    name, version = system_collector._os_identity()
    assert name == "Red Hat Enterprise Linux 9.4 (Plow)"
    assert version == "9.4"


def test_suse_family_identity(monkeypatch):
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system_collector.distro, "name", lambda pretty=False: "openSUSE Tumbleweed")
    monkeypatch.setattr(system_collector.distro, "version", lambda: "20260801")

    assert system_collector._os_identity() == ("openSUSE Tumbleweed", "20260801")


def test_freebsd_identity_comes_from_distro_too(monkeypatch):
    """FreeBSD ships an /etc/os-release; distro parses it like any other."""
    monkeypatch.setattr(system_collector.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(
        system_collector.distro, "name", lambda pretty=False: "FreeBSD 14.3-RELEASE"
    )
    monkeypatch.setattr(system_collector.distro, "version", lambda: "14.3")

    assert system_collector._os_identity() == ("FreeBSD 14.3-RELEASE", "14.3")


def test_unidentifiable_system_reports_nothing(monkeypatch):
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system_collector.distro, "name", lambda pretty=False: "")
    monkeypatch.setattr(system_collector.distro, "version", lambda: "")

    assert system_collector._os_identity() == (None, None)


def test_kernel_names_the_system_it_belongs_to(monkeypatch):
    monkeypatch.setattr(system_collector.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_collector.platform, "release", lambda: "25.5.0")

    assert system_collector._kernel() == "Darwin 25.5.0"


def test_link_local_addresses_are_dropped(monkeypatch):
    """fe80:: and 169.254/16 are unreachable from anywhere that matters."""
    import socket as socket_module

    class _Addr:
        def __init__(self, family, address):
            self.family = family
            self.address = address

    monkeypatch.setattr(
        system_collector.psutil,
        "net_if_addrs",
        lambda: {
            "en0": [
                _Addr(socket_module.AF_INET, "192.168.178.169"),
                _Addr(socket_module.AF_INET, "169.254.11.4"),
                _Addr(socket_module.AF_INET6, "fe80::1c57:1103%en0"),
                _Addr(socket_module.AF_INET6, "2a02:810d:d07:b100::42"),
            ],
            "lo0": [_Addr(socket_module.AF_INET, "127.0.0.1")],
        },
    )

    assert system_collector._collect_ips() == [
        "192.168.178.169",
        "2a02:810d:d07:b100::42",
    ]


def test_the_collector_does_not_cap_the_address_list(monkeypatch):
    """Capping here would destroy the total the renderer needs for "+N more"."""
    import socket as socket_module

    class _Addr:
        def __init__(self, family, address):
            self.family = family
            self.address = address

    monkeypatch.setattr(
        system_collector.psutil,
        "net_if_addrs",
        lambda: {"en0": [_Addr(socket_module.AF_INET, f"10.0.0.{n}") for n in range(1, 21)]},
    )

    assert len(system_collector._collect_ips()) == 20
