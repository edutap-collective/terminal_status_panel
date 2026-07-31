"""Compose sections into the final dashboard layout.

The panel is split into three independently selectable sections:

- ``server``  — SYSTEM OVERVIEW + UPDATES (top row) and the SYSTEM STATUS block.
- ``docker``  — the DOCKER INFOS block.
- ``health``  — the CLUSTER HEALTH block.

``build_layout`` renders whichever sections are requested (default: all),
followed by a shared footer, and stretches to the console width.
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

SECTIONS: tuple[str, ...] = ("server", "docker", "health")


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
    return Group(top, Text(""), system_status(data.resources, cfg))


def docker_section(data: PanelData, cfg: Config) -> RenderableType:
    """The DOCKER INFOS block. The health data, when the section ran, supplies
    the cluster verdicts the replica counts cannot give."""
    return services_section(data.swarm, cfg, data.health)


def health_block(data: PanelData, cfg: Config) -> RenderableType:
    """The CLUSTER HEALTH block."""
    return health_section(data.health, cfg)


_SECTION_BUILDERS = {
    "server": server_section,
    "docker": docker_section,
    "health": health_block,
}


def build_layout(data: PanelData, cfg: Config,
                 sections: tuple[str, ...] = SECTIONS) -> Group:
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
