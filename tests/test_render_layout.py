from rich.console import Console
from rich.text import Text

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


def test_traefik_is_a_known_section():
    assert "traefik" in SECTIONS


def test_traefik_is_in_the_default_full_panel():
    """It renders there in summary form — the tree would bury the banner, but
    leaving the wiring out of the panel people actually see hides the findings
    it exists to surface."""
    from terminal_status_panel import cli

    assert "traefik" in cli.DEFAULT_SECTIONS


def test_docker_section_receives_the_health_data(monkeypatch):
    """The Kafka verdict lives in the health section; the matrix must see it."""
    seen = {}

    def fake(swarm, cfg, health=None):
        seen["health"] = health
        return Text("")

    monkeypatch.setattr(layout, "services_section", fake)
    health = HealthInfo(clusters_probed=True)
    layout.docker_section(PanelData(swarm=SwarmInfo(reachable=True), health=health), Config())
    assert seen["health"] is health


def _traefik_data():
    from terminal_status_panel.model import (
        ServiceStatus,
        ServiceTask,
        TraefikEntrypoint,
        TraefikInfo,
        TraefikRouter,
        TraefikServiceRef,
    )

    return PanelData(
        swarm=SwarmInfo(reachable=True, enabled=True, services=[
            ServiceStatus("kafbat-ui_kafbat-ui", 1, 1,
                          tasks=[ServiceTask("srv-01", "running")]),
        ]),
        traefik=TraefikInfo(
            reachable=True,
            entrypoints=[TraefikEntrypoint(name="portalmgmt", address=":2020",
                                           port=2020)],
            routers=[TraefikRouter(name="kafbat-ui", entrypoints=["portalmgmt"],
                                   rule="PathPrefix(`/portale/kafka-ui`)",
                                   service="kafbat-ui")],
            services={"kafbat-ui": TraefikServiceRef(
                name="kafbat-ui", port=8080, scheme="http",
                docker_service="kafbat-ui_kafbat-ui")},
        ),
    )


def _has_branch(out: str, router: str) -> bool:
    return any(line.strip().startswith(f"└─ {router}") for line in out.splitlines())


def test_the_wiring_alone_gets_the_full_tree():
    """Asked for on its own it is the whole point of the run."""
    console = Console(width=120, force_terminal=True, color_system=None, record=True)
    console.print(build_layout(_traefik_data(), Config(), ("traefik",)))
    assert _has_branch(console.export_text(), "kafbat-ui")


def test_the_wiring_beside_other_sections_is_summarised():
    """One block of a panel, not a debugging view: the tree would push the
    server header off the screen."""
    console = Console(width=120, force_terminal=True, color_system=None, record=True)
    console.print(build_layout(_traefik_data(), Config(), ("docker", "traefik")))
    out = console.export_text()
    assert "TRAEFIK WIRING" in out
    assert not _has_branch(out, "kafbat-ui")
    assert "1 router" in out
