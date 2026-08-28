"""Compose sections into the final dashboard layout.

The panel is split into four independently selectable sections:

- ``server``  — SYSTEM OVERVIEW + UPDATES (top row) and the SYSTEM STATUS block.
- ``docker``  — the DOCKER INFOS block.
- ``health``  — the CLUSTER HEALTH block.
- ``traefik`` — the TRAEFIK WIRING block.

``build_layout`` renders whichever sections are requested (default: all of
``SECTIONS``), followed by a shared footer, and stretches to the console
width. ``SECTIONS`` lists every section the layout knows how to build, and
``cli.DEFAULT_SECTIONS`` is defined as exactly this tuple — a bare
``status-full`` renders all four. A section is rendered the same way however
many others accompany it: a builder receives the panel data and the config,
never the list of its neighbours.
"""

from __future__ import annotations

import importlib.metadata
from datetime import datetime

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..model import PanelData
from .health import health_section
from .panels import (
    managed_panel,
    services_section,
    system_overview,
    system_status,
    updates_panel,
)
from .traefik import traefik_section

SECTIONS: tuple[str, ...] = ("server", "docker", "health", "traefik")


def panel_version() -> str:
    """The installed version, or ``dev`` when the package is not installed.

    Read from the distribution metadata rather than from a constant in the
    source: a second copy is a second thing to forget at release time. Running
    from a checkout without installing is a real case -- `python -m
    terminal_status_panel.cli` during development -- and `dev` is the honest
    answer there. Inventing a number would put a version on screen that
    matches no release.
    """
    try:
        return importlib.metadata.version("terminal-status-panel")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _footer() -> Table:
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(
        Text("Tip: Run 'htop' for detailed process view", style="dim cyan"),
        Text(
            f"v{panel_version()} · Last check: {datetime.now():%Y-%m-%d %H:%M:%S}",
            style="dim cyan",
        ),
    )
    return grid


def server_section(data: PanelData, cfg: Config) -> RenderableType:
    """Top row (system overview + updates) over the system status block."""
    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column(ratio=3)
    top.add_column(ratio=2)
    # The right column carries UPDATES and, where a tool is configured, the
    # MANAGED block beneath it. Stacked rather than given a row of its own:
    # it belongs to the same "what is this machine" reading as the two blocks
    # above it, and a full-width banner would push the panel down by four
    # lines on every login for a fact that does not change.
    updates = updates_panel(data.updates)
    managed = managed_panel(cfg.managed)
    right = updates if managed is None else Group(updates, Text(""), managed)
    top.add_row(system_overview(data.system), right)
    # The origin map comes from the Docker section when it ran. Without it the
    # process rows show container ids, which is what `status-server` alone can
    # honestly say.
    origins = data.swarm.container_services if data.swarm else None
    return Group(top, Text(""), system_status(data.resources, cfg, data.processes, origins))


def docker_section(data: PanelData, cfg: Config) -> RenderableType:
    """The DOCKER INFOS block.

    The health data, when the section ran, supplies the cluster verdicts the
    replica counts cannot give. The resource data, likewise when it ran, is
    what lets the disk line tell real pressure from mere housekeeping -- the
    same arrangement as the origin map in `server_section`, and it degrades
    the same way: without it the figures render uncoloured.
    """
    return services_section(data.swarm, cfg, data.health, data.resources)


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
