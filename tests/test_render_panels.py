from rich.console import Console

from lmu.terminal_status_panel.config import Config
from lmu.terminal_status_panel.model import (
    FilesystemUsage,
    ResourceUsage,
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    SwarmNode,
    SystemInfo,
    UpdateInfo,
)
from lmu.terminal_status_panel.render import panels


def _text(renderable) -> str:
    console = Console(width=100, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_system_overview_shows_fields():
    info = SystemInfo(hostname="srv01", os_name="Debian", os_version="12",
                      kernel="6.1.0", uptime_seconds=90000, user="root",
                      ip_addresses=["10.0.0.5"])
    out = _text(panels.system_overview(info))
    assert "SYSTEM OVERVIEW" in out
    assert "srv01" in out
    assert "Debian" in out
    assert "10.0.0.5" in out


def test_system_overview_handles_none():
    out = _text(panels.system_overview(None))
    assert "not available" in out


def test_memory_panel_renders_bars():
    res = ResourceUsage(
        mem_total=32_000_000_000, mem_used=20_400_000_000, mem_percent=64.0,
        swap_total=8_000_000_000, swap_used=600_000_000, swap_percent=8.0,
    )
    out = _text(panels.memory_panel(res, Config()))
    assert "RAM" in out
    assert "SWAP" in out
    assert "64" in out   # percent shown
    assert "█" in out    # bar drawn


def test_load_panel_shows_per_core():
    res = ResourceUsage(
        load_avg=(1.0, 0.7, 0.4), cpu_count=4,
        cpu_percent=12.3, cpu_per_core=[8.1, 11.4, 6.2, 9.3],
    )
    out = _text(panels.load_panel(res, Config()))
    assert "SYSTEM LOAD" in out
    assert "Load Average" in out
    assert "per core" in out
    assert "Core 1" in out
    assert "Core 4" in out
    assert "12.3%" in out


def test_filesystem_panel_is_a_table():
    res = ResourceUsage(filesystems=[
        FilesystemUsage("/", 230_000_000_000, 210_000_000_000, 91.0),
        FilesystemUsage("/data", 500_000_000_000, 120_000_000_000, 24.0),
    ])
    out = _text(panels.filesystem_panel(res))
    assert "FILESYSTEM USAGE" in out
    assert "Size" in out
    assert "Use%" in out
    assert "/data" in out
    assert "91%" in out


def test_updates_panel_lists_counts():
    out = _text(panels.updates_panel(UpdateInfo(supported=True, available=12,
                                                security=5, standard=7)))
    assert "Available updates" in out
    assert "Security updates" in out
    assert "12" in out
    assert "5" in out


def test_updates_panel_unsupported():
    out = _text(panels.updates_panel(UpdateInfo(supported=False)))
    assert "n/a" in out.lower()


def test_services_section_groups_by_stack_with_emoji_states():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode("srv-01", reachable=True, role="manager", leader=True),
               SwarmNode("srv-02", reachable=False, role="worker", state="down")],
        services=[
            ServiceStatus("pg_db", 1, 1, stack="PostgreSQL-18",
                          description="PostgreSQL Datenbank, Version 18",
                          tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("kafka_broker", 1, 2, critical=True, stack="kafka",
                          tasks=[ServiceTask("srv-01", "running"),
                                 ServiceTask("srv-02", "failed")],
                          unassigned=1),
            ServiceStatus("watchtower", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("registry", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()))
    assert "DOCKER SWARM" in out
    # Compact key-fact header, grouped.
    assert "Swarm" in out
    assert "Master" in out
    assert "srv-01" in out                   # master / node listed
    assert "Registry" in out                 # registry surfaced as a key fact
    assert "Nodes" in out
    # Emoji states.
    assert "✅" in out and "💀" in out
    assert "down" in out                     # node/task state text
    assert "unassigned" in out               # orphaned task marker
    # Stacks section.
    assert "Stacks" in out
    assert "PostgreSQL-18:" in out           # stack header (sorted)
    assert "PostgreSQL Datenbank" in out     # description
    assert "kafka:" in out
    assert "Ohne Stack:" in out              # watchtower remains ungrouped
    assert "watchtower" in out


def test_services_section_unreachable():
    out = _text(panels.services_section(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()
