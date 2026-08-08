from rich.console import Console
from rich.text import Text

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    HealthInfo,
    PanelData,
    ProcessInfo,
    ProcessSnapshot,
    ResourceUsage,
    SwarmInfo,
    SystemInfo,
    UpdateInfo,
)
from terminal_status_panel.render import layout
from terminal_status_panel.render.layout import SECTIONS, build_layout


def _render(renderable, width=100) -> str:
    """Render an already-built layout (or any Rich renderable) to plain text.

    Takes the renderable itself, not a ``PanelData``, so a caller that needs
    non-default sections can build the layout with ``build_layout(...,
    sections)`` and hand the result straight in.
    """
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_build_layout_contains_all_sections():
    data = PanelData(
        system=SystemInfo(hostname="srv01", ip_addresses=["10.0.0.5"]),
        resources=ResourceUsage(mem_percent=64.0, mem_used=1, mem_total=2,
                                cpu_percent=12.0, cpu_per_core=[10.0, 14.0]),
        swarm=SwarmInfo(reachable=False),
        updates=UpdateInfo(supported=True, available=3, security=1, standard=2),
    )
    out = _render(build_layout(data, Config()))
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
    out = _render(build_layout(PanelData(), Config()))
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


def _render_sections(sections):
    console = Console(width=120, force_terminal=True, color_system=None, record=True)
    console.print(build_layout(_traefik_data(), Config(), sections))
    return console.export_text()


def test_the_wiring_renders_the_same_whether_it_is_alone_or_not():
    """One rendering, flowed into columns — so the block a login shows is the
    block `status-traefik` shows, and nothing is only visible in one of them."""
    alone = _render_sections(("traefik",))
    beside = _render_sections(("docker", "traefik"))
    assert alone.count("\u2514\u2500 kafbat-ui") == 1
    assert beside.count("\u2514\u2500 kafbat-ui") == 1
    assert "TRAEFIK WIRING" in beside


def test_the_server_section_hands_the_origin_map_to_the_process_rows():
    data = PanelData(
        resources=ResourceUsage(),
        processes=ProcessSnapshot(
            top_memory=[ProcessInfo(pid=1, name="java", memory_percent=7.0,
                                    origin="container abcdef123456")],
            sampled=0.3),
        swarm=SwarmInfo(reachable=True, enabled=True,
                        container_services={"abcdef123456789": "stack_app_backend"}),
    )
    out = _render(build_layout(data, Config(), ("server",)), width=200)
    assert "stack_app_backend" in out
