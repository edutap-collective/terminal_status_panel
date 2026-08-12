"""Compose sections into the final dashboard layout.

The panel is split into four independently selectable sections:

- ``server``  — SYSTEM OVERVIEW + UPDATES (top row) and the SYSTEM STATUS block.
- ``docker``  — the DOCKER INFOS block.
- ``health``  — the CLUSTER HEALTH block.
- ``traefik`` — the TRAEFIK WIRING block.

``build_layout`` renders whichever sections are requested (default: all of
``SECTIONS``), followed by a shared footer, and stretches to the console
width. ``SECTIONS`` lists every section the layout knows how to build, so
``--sections traefik`` works — it is not the same as the *default* set of
sections a bare ``status-full`` renders; see ``cli.DEFAULT_SECTIONS`` for that.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..model import PanelData
from .health import health_section
from .panels import services_section, system_overview, system_status, updates_panel
from .traefik import traefik_section

SECTIONS: tuple[str, ...] = ("server", "docker", "health", "traefik")


def _footer() -> Table:
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(
        Text("Tip: Run 'htop' for detailed process view", style="dim cyan"),
        Text(f"Last check: {datetime.now():%Y-%m-%d %H:%M:%S}", style="dim cyan"),
    )
    return grid


def server_section(data: PanelData, cfg: Config) -> RenderableType:
    """Top row (system overview + updates) over the system status block."""
    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column(ratio=3)
    top.add_column(ratio=2)
    top.add_row(system_overview(data.system), updates_panel(data.updates))
    # The origin map comes from the Docker section when it ran. Without it the
    # process rows show container ids, which is what `status-server` alone can
    # honestly say.
    origins = data.swarm.container_services if data.swarm else None
    return Group(top, Text(""), system_status(data.resources, cfg, data.processes, origins))


def docker_section(data: PanelData, cfg: Config) -> RenderableType:
    """The DOCKER INFOS block.

    The health data, when the section ran, supplies the cluster verdicts the
    replica counts cannot give.
    """
    return services_section(data.swarm, cfg, data.health)


def health_block(data: PanelData, cfg: Config) -> RenderableType:
    """The CLUSTER HEALTH block."""
    return health_section(data.health, cfg)


def traefik_block(data: PanelData, cfg: Config) -> RenderableType:
    """The TRAEFIK WIRING block."""
    return traefik_section(data.traefik, cfg, data.swarm)


_SECTION_BUILDERS = {
    "server": server_section,
    "docker": docker_section,
    "health": health_block,
    "traefik": traefik_block,
}


def build_layout(data: PanelData, cfg: Config, sections: tuple[str, ...] = SECTIONS) -> Group:
    """The whole panel, as the requested *sections* in the order given.

    A section whose builder is unknown is skipped rather than raising: the
    panel renders what it can.
    """
    parts: list[RenderableType] = []
    for name in sections:
        builder = _SECTION_BUILDERS.get(name)
        if builder is None:
            continue
        if parts:
            parts.append(Text(""))
        parts.append(builder(data, cfg))
    parts.append(Rule(style="dim blue"))
    parts.append(_footer())
    return Group(*parts)
