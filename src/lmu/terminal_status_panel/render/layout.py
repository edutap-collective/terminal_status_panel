"""Compose sections into the final full-width dashboard layout.

Top row: system overview (logo + identity) beside the updates panel.
Middle: a SYSTEM STATUS section grouping load, memory/swap and filesystems.
Bottom: a DOCKER INFOS section (swarm key facts over three stack columns).
Everything stretches to the console width.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..model import PanelData
from .panels import services_section, system_overview, system_status, updates_panel


def _footer() -> Table:
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")
    grid.add_row(
        Text("Tip: Run 'htop' for detailed process view", style="dim cyan"),
        Text(f"Last check: {datetime.now():%Y-%m-%d %H:%M:%S}", style="dim cyan"),
    )
    return grid


def build_layout(data: PanelData, cfg: Config) -> Group:
    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column(ratio=3)
    top.add_column(ratio=2)
    top.add_row(system_overview(data.system), updates_panel(data.updates))

    return Group(
        top,
        Text(""),
        system_status(data.resources, cfg),
        Text(""),
        services_section(data.swarm, cfg),
        Rule(style="dim blue"),
        _footer(),
    )
