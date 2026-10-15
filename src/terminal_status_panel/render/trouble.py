"""The TROUBLE section: what fell in the last twelve hours, containers and host.

Its own section since 0.14: a host without Docker has trouble too, and one
block for both keeps a node reboot -- containers and an OOM kill at once --
on one screen instead of two.
"""

from __future__ import annotations

import time

from rich.console import RenderableType
from rich.text import Text

from ..collectors.hoststate import host_trouble
from ..config import Config
from ..model import PanelData
from .panels import _trouble_block


def trouble_section(data: PanelData, cfg: Config) -> RenderableType:
    """The block, or an empty renderable when there is nothing to report."""
    entries = list(data.swarm.trouble) if data.swarm and data.swarm.reachable else []
    if data.host is not None:
        entries += host_trouble(data.host, time.time(), cfg.trouble_host_events)
    block = _trouble_block(entries)
    return block if block is not None else Text("")
