from rich.console import Console

from lmu.terminal_status_panel.config import Config
from lmu.terminal_status_panel.model import (
    PanelData,
    ResourceUsage,
    SwarmInfo,
    SystemInfo,
)
from lmu.terminal_status_panel.render import layout


def test_build_layout_contains_all_sections():
    data = PanelData(
        system=SystemInfo(hostname="srv01", ip_addresses=["10.0.0.5"]),
        resources=ResourceUsage(mem_percent=64.0, mem_used=1, mem_total=2),
        swarm=SwarmInfo(reachable=False),
    )
    console = Console(width=80, force_terminal=True, color_system="truecolor", record=True)
    console.print(layout.build_layout(data, Config()))
    out = console.export_text()
    assert "System" in out
    assert "Resources" in out
    assert "Services" in out
    assert "srv01" in out


def test_build_layout_survives_all_none():
    console = Console(width=80, force_terminal=True, record=True)
    console.print(layout.build_layout(PanelData(), Config()))
    out = console.export_text()
    assert "System" in out
    assert "Services" in out
