import pytest

from terminal_status_panel import install


def test_unit_is_hardened_with_exactly_one_privileged_step():
    units = install.render_collector_units("/opt/tsp/bin")
    service = units["terminal-status-panel-collect.service"]
    for key in (
        "DynamicUser=yes",
        "User=status-collector",
        "SupplementaryGroups=systemd-journal",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "RuntimeDirectory=terminal-status-panel",
        "StateDirectory=terminal-status-panel",
    ):
        assert key in service
    assert service.count("ExecStartPre=") == 1
    assert "ExecStartPre=-+/usr/lib/terminal-status-panel/keepalived-dump" in service
    assert "ExecStart=/opt/tsp/bin/status-collect" in service
    assert "+" not in "".join(
        line for line in service.splitlines() if line.startswith("ExecStopPost=")
    )


def test_service_removes_the_dump_copy_after_every_run():
    # Also when ExecStart never started: the copy holds the VRRP password.
    service = install.render_collector_units("/x")["terminal-status-panel-collect.service"]
    assert "ExecStopPost=-/bin/rm -f %t/terminal-status-panel/keepalived.json" in service


def test_service_keeps_the_runtime_directory_between_runs():
    # The collector reuses the previous host.json for the hourly update check.
    service = install.render_collector_units("/x")["terminal-status-panel-collect.service"]
    assert "RuntimeDirectoryPreserve=yes" in service


def test_timer_period():
    timer = install.render_collector_units("/x")["terminal-status-panel-collect.timer"]
    assert "OnUnitActiveSec=30s" in timer
    assert "OnBootSec=30s" in timer


def test_units_render_from_installed_package_resources():
    units = install.render_collector_units("/opt/tsp/bin/")
    assert set(units) == {
        "terminal-status-panel-collect.service",
        "terminal-status-panel-collect.timer",
    }
    assert "{bin_dir}" not in units["terminal-status-panel-collect.service"]
    assert "ExecStart=/opt/tsp/bin/status-collect" in units["terminal-status-panel-collect.service"]


@pytest.mark.parametrize(
    "bin_dir", ["/a b", "/a\nExecStartPre=+/x", "/a%h", "/opt/bin\n", "/a$b", "rel/path", "/a\\b"]
)
def test_unsafe_bin_dir_is_rejected(bin_dir):
    with pytest.raises(ValueError):
        install.render_collector_units(bin_dir)
