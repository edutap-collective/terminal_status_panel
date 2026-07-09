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


def _text(renderable, width=100) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
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


def test_services_section_swarm_facts_and_three_columns():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode("srv-01", reachable=True, role="manager", leader=True),
               SwarmNode("srv-02", reachable=False, role="worker", state="down")],
        services=[
            # Multi-service infra stack — must list each service individually.
            ServiceStatus("PostgreSQL-18_a", 1, 1, stack="PostgreSQL-18",
                          description="PG primary", tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("PostgreSQL-18_b", 1, 1, stack="PostgreSQL-18",
                          description="PG replica", tasks=[ServiceTask("srv-02", "running")]),
            ServiceStatus("kafka_broker", 1, 2, stack="kafka", description="Message broker",
                          tasks=[ServiceTask("srv-01", "running"),
                                 ServiceTask("srv-02", "failed")]),
            ServiceStatus("eduTAP_web", 1, 1, stack="eduTAP", description="eduTAP frontend",
                          tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("watchtower", 1, 1, description="Auto-update",
                          tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("registry", 1, 1, description="Docker registry",
                          tasks=[ServiceTask("srv-02", "running")]),
            ServiceStatus("traefik_sockproxy", 1, 1, stack="traefik",
                          description="socket proxy", tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("traefik_traefik", 2, 2, stack="traefik", description="ingress",
                          tasks=[ServiceTask("srv-01", "running"),
                                 ServiceTask("srv-02", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "DOCKER INFOS" in out
    # Swarm key facts split into two blocks, incl. pulled-in registry & traefik.
    assert "SWARM" in out
    assert "Swarm" in out
    assert "Nodes" in out
    assert "srv-01" in out and "down" in out   # node line, srv-02 down
    assert "Registry" in out
    assert "Traefik" in out
    # Multi-service traefik lists both services with descriptions in the facts.
    assert "traefik_sockproxy" in out and "traefik_traefik" in out
    assert "socket proxy" in out and "ingress" in out
    # Three stack matrices with a description column.
    assert "Infrastruktur" in out
    assert "Service" in out
    assert "Container (ohne Stack)" in out
    assert "Description" in out
    # Multi-service infra stack lists each service.
    assert "PostgreSQL-18_a" in out and "PostgreSQL-18_b" in out
    assert "PG primary" in out
    # Single-service stacks stay one row; descriptions shown.
    assert "kafka" in out and "Message broker" in out
    assert "eduTAP" in out
    assert "watchtower" in out
    # Per-node status emojis present (running ✅, failed 💀).
    assert "✅" in out and "💀" in out


def test_services_section_unreachable():
    out = _text(panels.services_section(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()
