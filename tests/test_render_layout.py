from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    HealthInfo,
    PanelData,
    ResourceUsage,
    SwarmInfo,
    SystemInfo,
    UpdateInfo,
)
from terminal_status_panel.render import layout
from terminal_status_panel.render.layout import SECTIONS, build_layout


def _render(data, width=100) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
    console.print(layout.build_layout(data, Config()))
    return console.export_text()


def test_build_layout_contains_all_sections():
    data = PanelData(
        system=SystemInfo(hostname="srv01", ip_addresses=["10.0.0.5"]),
        resources=ResourceUsage(mem_percent=64.0, mem_used=1, mem_total=2,
                                cpu_percent=12.0, cpu_per_core=[10.0, 14.0]),
        swarm=SwarmInfo(reachable=False),
        updates=UpdateInfo(supported=True, available=3, security=1, standard=2),
    )
    out = _render(data)
    assert "SYSTEM OVERVIEW" in out
    assert "UPDATES" in out
    assert "SYSTEM STATUS" in out
    assert "SYSTEM LOAD" in out
    assert "MEMORY & SWAP" in out
    assert "FILESYSTEM USAGE" in out
    assert "DOCKER INFOS" in out
    assert "srv01" in out
    assert "Last check:" in out


def test_build_layout_survives_all_none():
    out = _render(PanelData())
    assert "SYSTEM OVERVIEW" in out
    assert "DOCKER" in out


def test_health_is_a_known_section():
    assert "health" in SECTIONS


def test_build_layout_renders_only_the_requested_section():
    from rich.console import Console

    data = PanelData(health=HealthInfo())
    console = Console(width=100, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(build_layout(data, Config(), sections=("health",)))
    output = capture.get()
    assert "CLUSTER HEALTH" in output
    assert "DOCKER" not in output
