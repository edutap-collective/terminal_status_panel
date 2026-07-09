"""Compose sections into the final full-width dashboard layout.

Two independent columns on top (system identity + memory + filesystems on the
left; load + updates on the right), a full-width Docker/Swarm section below,
and a footer. Everything stretches to the console width.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..model import PanelData
from .panels import (
    filesystem_panel,
    load_panel,
    memory_panel,
    services_section,
    system_overview,
    updates_panel,
)


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
    left = Group(
        system_overview(data.system),
        Text(""),
        memory_panel(data.resources, cfg),
        Text(""),
        filesystem_panel(data.resources),
    )
    right = Group(
        load_panel(data.resources, cfg),
        Text(""),
        updates_panel(data.updates),
    )

    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_row(left, right)

    return Group(
        top,
        Text(""),
        services_section(data.swarm, cfg),
        Rule(style="dim blue"),
        _footer(),
    )
