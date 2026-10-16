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
from .icons import WARN
from .panels import _trouble_block


def _journal_warning(data: PanelData, cfg: Config) -> list[Text]:
    """One line when the collector's last journal read failed, else none.

    A failed read keeps old counts -- or zeros, which build no row at all --
    so without this line TROUBLE would look quiet exactly when it cannot know.
    """
    state = data.host.state if data.host else None
    error = (state.errors or {}).get("events") if state and cfg.trouble_host_events else None
    if not error:
        return []
    return [Text(f"{WARN}host events: journal not refreshed ({error})", style="yellow")]


def trouble_section(data: PanelData, cfg: Config) -> RenderableType:
    """The block, or an empty renderable when there is nothing to report."""
    entries = list(data.swarm.trouble) if data.swarm and data.swarm.reachable else []
    if data.host is not None:
        entries += host_trouble(data.host, time.time(), cfg.trouble_host_events)
    block = _trouble_block(entries, _journal_warning(data, cfg))
    return block if block is not None else Text("")
