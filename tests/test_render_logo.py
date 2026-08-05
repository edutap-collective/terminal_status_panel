"""Logo selection: platform first, distribution second, Tux last.

Tux is a true statement about any Linux distribution and a false one about
anything else, which is the line this module draws.
"""

from terminal_status_panel import platform_defaults
from terminal_status_panel.render import logo as logo_module


def _on(monkeypatch, system):
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: system)


def test_darwin_never_shows_the_penguin(monkeypatch):
    _on(monkeypatch, "Darwin")
    assert logo_module._logo_name("macOS") == "macos"


def test_freebsd_and_the_other_bsds(monkeypatch):
    _on(monkeypatch, "FreeBSD")
    assert logo_module._logo_name("FreeBSD 14.3-RELEASE") == "freebsd"
    _on(monkeypatch, "OpenBSD")
    assert logo_module._logo_name("OpenBSD 7.5") == "bsd"
    _on(monkeypatch, "NetBSD")
    assert logo_module._logo_name("NetBSD 10.0") == "bsd"


def test_the_platform_key_wins_over_the_reported_name(monkeypatch):
    """A Darwin host whose name mentions Debian is still a Mac."""
    _on(monkeypatch, "Darwin")
    assert logo_module._logo_name("Debian GNU/Linux 12") == "macos"


def test_linux_distributions_resolve_by_name(monkeypatch):
    _on(monkeypatch, "Linux")
    cases = {
        "Ubuntu 24.04.1 LTS": "ubuntu",
        "Debian GNU/Linux 12 (bookworm)": "debian",
        "Red Hat Enterprise Linux 9.4 (Plow)": "rhel",
        "Rocky Linux 9.4 (Blue Onyx)": "rocky",
        "AlmaLinux 9.4 (Seafoam Ocelot)": "alma",
        "Fedora Linux 40 (Workstation Edition)": "fedora",
        "CentOS Stream 9": "centos",
        "openSUSE Tumbleweed": "opensuse",
        "SUSE Linux Enterprise Server 15 SP6": "suse",
    }
    for name, expected in cases.items():
        assert logo_module._logo_name(name) == expected, name


def test_opensuse_is_matched_before_the_generic_suse_key(monkeypatch):
    """"opensuse" contains "suse"; order decides, so pin it."""
    _on(monkeypatch, "Linux")
    assert logo_module._logo_name("openSUSE Leap 15.6") == "opensuse"


def test_an_unknown_linux_falls_back_to_tux(monkeypatch):
    _on(monkeypatch, "Linux")
    assert logo_module._logo_name("Void Linux") == "linux"
    assert logo_module._logo_name(None) == "linux"


def test_a_missing_logo_file_renders_as_empty_text(monkeypatch):
    monkeypatch.setattr(logo_module, "_logo_name", lambda name: "does-not-exist")
    assert logo_module.os_logo("whatever").plain == ""


def test_the_macos_word_mark_is_bundled():
    path = logo_module._LOGO_DIR / "macos.ans"
    assert path.is_file(), "macos.ans must ship with the package"
    assert logo_module.os_logo_by_key("macos").plain.strip() != ""
