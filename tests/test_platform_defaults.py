"""The platform seam: a handful of values that differ per operating system."""

from terminal_status_panel import platform_defaults


def test_darwin_hides_system_volumes_and_simulator_runtimes(monkeypatch):
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    assert platform_defaults.ignore_mountpoints() == [
        "/System/Volumes/",
        "/Library/Developer/CoreSimulator/",
    ]


def test_other_platforms_hide_no_mountpoints(monkeypatch):
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Linux")
    assert platform_defaults.ignore_mountpoints() == []


def test_callers_cannot_mutate_the_shared_default(monkeypatch):
    """A caller that appends to the returned list must not poison the next call."""
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    first = platform_defaults.ignore_mountpoints()
    first.append("/tmp/")
    assert "/tmp/" not in platform_defaults.ignore_mountpoints()


def test_darwin_tolerates_permanent_swap_usage(monkeypatch):
    """macOS swaps continuously by design; warning at 1% would always fire."""
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    assert platform_defaults.swap_warning() == 80.0


def test_other_platforms_keep_the_strict_swap_threshold(monkeypatch):
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Linux")
    assert platform_defaults.swap_warning() == 1.0


def test_logo_keys_per_platform(monkeypatch):
    for system, expected in [
        ("Darwin", "macos"),
        ("FreeBSD", "freebsd"),
        ("OpenBSD", "bsd"),
        ("NetBSD", "bsd"),
    ]:
        monkeypatch.setattr(platform_defaults.platform, "system", lambda s=system: s)
        assert platform_defaults.logo_key() == expected


def test_linux_has_no_platform_logo_key(monkeypatch):
    """Linux defers to distribution matching, which the logo module owns."""
    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Linux")
    assert platform_defaults.logo_key() is None
