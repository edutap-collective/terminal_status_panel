"""Compose panels into the final two-column-over-full-width layout."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table

from ..config import Config
from ..model import PanelData
from .panels import resources_panel, services_panel, system_panel


def build_layout(data: PanelData, cfg: Config) -> Group:
    top = Table.grid(expand=True)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_row(system_panel(data.system), resources_panel(data.resources, cfg))
    return Group(top, services_panel(data.swarm, cfg))
