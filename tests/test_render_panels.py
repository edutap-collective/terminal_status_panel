from rich.console import Console
from rich.panel import Panel

from lmu.terminal_status_panel.config import Config
from lmu.terminal_status_panel.model import (
    FilesystemUsage,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
    SystemInfo,
)
from lmu.terminal_status_panel.render import panels


def _text(renderable) -> str:
    console = Console(width=80, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_system_panel_shows_fields():
    info = SystemInfo(hostname="srv01", os_name="Debian", os_version="12",
                      kernel="6.1.0", uptime_seconds=90000, user="root",
                      ip_addresses=["10.0.0.5"])
    out = _text(panels.system_panel(info))
    assert "srv01" in out
    assert "Debian" in out
    assert "10.0.0.5" in out


def test_system_panel_handles_none():
    assert isinstance(panels.system_panel(None), Panel)


def test_resources_panel_renders_bars_and_load():
    res = ResourceUsage(
        mem_total=32_000_000_000, mem_used=20_400_000_000, mem_percent=64.0,
        swap_total=8_000_000_000, swap_used=600_000_000, swap_percent=8.0,
        filesystems=[FilesystemUsage("/", 230_000_000_000, 210_000_000_000, 91.0)],
        load_avg=(1.0, 0.7, 0.4), cpu_count=4,
    )
    out = _text(panels.resources_panel(res, Config()))
    assert "RAM" in out
    assert "SWAP" in out
    assert "64" in out  # percent shown
    assert "█" in out   # bar drawn
    assert "/" in out   # filesystem mountpoint


def test_services_panel_lists_services():
    swarm = SwarmInfo(reachable=True, enabled=True, node_role="manager", node_count=3,
                      services=[ServiceStatus("postgres", 1, 1),
                                ServiceStatus("kafka", 2, 3, critical=True)])
    out = _text(panels.services_panel(swarm, Config()))
    assert "manager" in out
    assert "postgres" in out
    assert "1/1" in out
    assert "kafka" in out
    assert "2/3" in out


def test_services_panel_unreachable():
    out = _text(panels.services_panel(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()
