"""Render Traefik's wiring: entrypoint → router → middleware → service.

One branch per entrypoint, ordered by port, then a block for routers whose
entrypoint does not exist. That block is not symmetry: a tree keyed by
entrypoint has no branch for such a router, so without it the panel would drop
it silently — and the cluster has one today.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from ..collectors.traefik import unknown_entrypoints
from ..config import Config
from ..model import SwarmInfo, TraefikInfo, TraefikRouter
from . import icons
from .panels import section
from .verdict import service_verdict

_INTERNAL_SUFFIX = "@internal"


def _subhead(title: str) -> Text:
    return Text(title, style="bold cyan")


def _service_line(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Text:
    name = router.service or router.name
    ref = info.services.get(name)
    line = Text(f"     └─ → {name}")
    if ref and ref.scheme:
        line.append(f"  {ref.scheme}")
    if ref and ref.port:
        line.append(f" :{ref.port}")
    if name.endswith(_INTERNAL_SUFFIX):
        # Traefik's own endpoint: nothing was measured, so nothing is claimed.
        return line
    if swarm is None:
        # Nobody looked at Docker. Claiming the service does not exist would be
        # asserting what was never measured — show the neutral dot, no count.
        line.append(f"  {icons.UNKNOWN}", style="dim")
        return line
    docker_name = ref.docker_service if ref else None
    matching = [s for s in swarm.services if s.name == docker_name]
    if not matching:
        line.append(f"  {icons.FAILED} no such service", style="red")
        return line
    line.append("  ")
    line.append_text(service_verdict(matching, node_count=len(swarm.nodes)))
    return line


def _router_lines(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Group:
    style = "dim" if router.source == "file" else ""
    head = Text(f"  └─ {router.name}", style=style)
    if router.rule:
        head.append(f"        {router.rule}", style="dim")
    parts: list[RenderableType] = [head]
    for name in router.middlewares:
        mw = info.middlewares.get(name)
        kind = f" ({mw.kind})" if mw and mw.kind else ""
        parts.append(Text(f"     ├─ ⇢ {name}{kind}", style="dim"))
    parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def _entrypoint_block(entrypoint, info: TraefikInfo, swarm: SwarmInfo | None) -> Group:
    head = Text(f"{entrypoint.name}  {entrypoint.address}", style="bold cyan")
    # A router naming no entrypoint is attached to all of them by Traefik
    # (see unknown_entrypoints) — without this it would render in none.
    attached = [
        r for r in info.routers if not r.entrypoints or entrypoint.name in r.entrypoints
    ]
    if not attached:
        # A published port nothing serves is a finding, not an absence.
        head.append("   — no router", style="dim")
        return Group(head)
    # Internal routers last: ping-router hangs on six of nine entrypoints.
    attached.sort(key=lambda r: (r.source != "swarm", r.name))
    return Group(head, *[_router_lines(r, info, swarm) for r in attached])


def _orphan_block(info: TraefikInfo, swarm: SwarmInfo | None) -> Group | None:
    known = {ep.name for ep in info.entrypoints}
    orphans = [(r, unknown_entrypoints(r, known)) for r in info.routers]
    orphans = [(r, missing) for r, missing in orphans if missing]
    if not orphans:
        return None
    parts: list[RenderableType] = [_subhead("ORPHANED ROUTERS")]
    for router, missing in orphans:
        named = ", ".join(f"`{name}`" for name in missing)
        parts.append(
            Text(f"  {icons.FAILED} {router.name}        entrypoint {named} does not exist",
                 style="red")
        )
        if router.rule:
            parts.append(Text(f"     {router.rule}", style="dim"))
        parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def traefik_section(info: TraefikInfo | None, cfg: Config,
                    swarm: SwarmInfo | None = None) -> RenderableType:
    """The TRAEFIK WIRING block."""
    data = info or TraefikInfo()
    if data.error:
        return section("TRAEFIK WIRING",
                       Text(f"{icons.FAILED} {data.error}", style="red"))
    if not data.reachable:
        return section("TRAEFIK WIRING", Text("not checked", style="dim"))
    if not data.entrypoints:
        return section("TRAEFIK WIRING", Text("no entrypoints found", style="dim"))

    parts: list[RenderableType] = []
    if data.file_provider_error:
        # api@internal and ping-router live only in the file provider. Without
        # this line their absence from the tree below reads as a finding
        # ("— no router") instead of the gap it actually is.
        parts.append(Text(
            f"{icons.WARN} file provider unreadable: {data.file_provider_error}"
            " — routers defined there are missing",
            style="dim",
        ))
        parts.append(Text(""))
    ordered = sorted(data.entrypoints, key=lambda ep: (ep.port is None, ep.port))
    for entrypoint in ordered:
        parts.append(_entrypoint_block(entrypoint, data, swarm))
        parts.append(Text(""))
    orphans = _orphan_block(data, swarm)
    if orphans is not None:
        parts.append(orphans)
    return section("TRAEFIK WIRING", Group(*parts))
