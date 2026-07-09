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


def test_services_section_merges_per_node_replicas():
    N1, N2, N3 = "lmzvd06-ccc-01", "lmzvd06-ccn-01", "lmzvd06-ccn-02"
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode(N1, reachable=True, role="manager", leader=True),
               SwarmNode(N2, reachable=True, role="worker"),
               SwarmNode(N3, reachable=False, role="worker", state="down")],
        services=[
            # One kafka service per node — must collapse to a single "kafka" row.
            ServiceStatus(f"kafka_kafka-{N1}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus(f"kafka_kafka-{N2}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N2, "running")]),
            ServiceStatus(f"kafka_kafka-{N3}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N3, "failed")]),
            # PostgreSQL: per-node pg replicas + a distinct monitor service.
            ServiceStatus(f"PostgreSQL-18_pg-{N1}", 1, 1, stack="PostgreSQL-18",
                          description="PG", tasks=[ServiceTask(N1, "running")]),
            ServiceStatus(f"PostgreSQL-18_pg-{N2}", 1, 1, stack="PostgreSQL-18",
                          description="PG", tasks=[ServiceTask(N2, "running")]),
            ServiceStatus("PostgreSQL-18_pg-monitor", 1, 1, stack="PostgreSQL-18",
                          description="PG monitor", tasks=[ServiceTask(N3, "running")]),
            # traefik: two distinct services.
            ServiceStatus("traefik_sockproxy", 1, 1, stack="traefik",
                          description="socket proxy", tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("traefik_traefik", 3, 3, stack="traefik", description="ingress",
                          tasks=[ServiceTask(N1, "running"), ServiceTask(N2, "running"),
                                 ServiceTask(N3, "running")]),
            ServiceStatus("eduTAP_web", 1, 1, stack="eduTAP", description="eduTAP frontend",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("registry", 1, 1, description="Docker registry",
                          tasks=[ServiceTask(N2, "running")]),
            ServiceStatus("watchtower", 1, 1, description="Auto-update",
                          tasks=[ServiceTask(N3, "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "DOCKER INFOS" in out
    assert "Infrastruktur" in out and "Service" in out and "Container (ohne Stack)" in out
    assert "Description" in out
    assert "ccc-01" in out  # short node header

    # kafka collapses to ONE row; per-node service names are gone.
    assert "kafka" in out
    assert f"kafka_kafka-{N1}" not in out
    # PostgreSQL: merged 'pg' row + distinct 'pg-monitor', stack prefix stripped.
    assert "PostgreSQL-18" in out
    assert "pg-monitor" in out
    assert f"PostgreSQL-18_pg-{N1}" not in out
    # traefik sub-rows without stack prefix.
    assert "sockproxy" in out
    assert "traefik_sockproxy" not in out
    # registry -> Infrastruktur, watchtower -> Container, eduTAP -> Service.
    assert "registry" in out and "Docker registry" in out
    assert "watchtower" in out
    assert "eduTAP" in out
    # Status emojis present (running ✅, failed 💀 for kafka on down node).
    assert "✅" in out and "💀" in out


def test_services_section_unreachable():
    out = _text(panels.services_section(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()
