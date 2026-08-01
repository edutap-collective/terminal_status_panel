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
from .verdict import service_verdict, severity, verdict_icon

_INTERNAL_SUFFIX = "@internal"


def _subhead(title: str) -> Text:
    return Text(title, style="bold cyan")


def _service_state(router: TraefikRouter, info: TraefikInfo,
                   swarm: SwarmInfo | None) -> tuple[str, Text]:
    """This router's service, as a verdict glyph and as a rendered line.

    The glyph is empty when the line makes no claim at all — Traefik's own
    endpoints. The summary form needs the severity without the line, and
    deriving it a second time, or reading it back out of the rendered text,
    is how the two would come to disagree.
    """
    name = router.service or router.name
    ref = info.services.get(name)
    line = Text(f"     └─ → {name}")
    if ref and ref.scheme:
        line.append(f"  {ref.scheme}")
    if ref and ref.port:
        line.append(f" :{ref.port}")
    if name.endswith(_INTERNAL_SUFFIX):
        # Traefik's own endpoint: nothing was measured, so nothing is claimed.
        return "", line
    if swarm is None or not swarm.reachable or not swarm.enabled:
        # Nobody looked at Docker, or the look came back empty-handed: no
        # client (`swarm is None`), no answer from the daemon
        # (`reachable=False`), or no Swarm at all (`enabled=False`, where
        # `services` holds container names that can never match a Swarm
        # service). Claiming the service does not exist would be asserting what
        # was never measured — show the neutral dot, no count.
        line.append(f"  {icons.UNKNOWN}", style="dim")
        return icons.UNKNOWN, line
    docker_name = ref.docker_service if ref else None
    matching = [s for s in swarm.services if s.name == docker_name]
    if not matching:
        line.append(f"  {icons.FAILED} no such service", style="red")
        return icons.FAILED, line
    line.append("  ")
    # Same preference as DOCKER INFOS: ``_node_map`` swallows a failed node
    # listing, so an empty node list beside a non-zero count is reachable, and
    # counting only the list would give a global-mode service a second, softer
    # verdict here than the one the other section shows.
    node_count = swarm.node_count or len(swarm.nodes)
    line.append_text(service_verdict(matching, node_count=node_count))
    return verdict_icon(matching, node_count=node_count), line


def _service_line(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Text:
    return _service_state(router, info, swarm)[1]


def _router_lines(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Group:
    style = "dim" if router.source == "file" else ""
    head = Text(f"  └─ {router.name}", style=style)
    if router.rule:
        head.append(f"        {router.rule}", style="dim")
    if info.api_consulted and router.rejected:
        # Traefik was asked and said no: a measured failure, not a suspicion.
        # The accepted case stays silent — the tree already reads as
        # configured-and-accepted — and an unconsulted router (`rejected is
        # None`, or `api_consulted` False) implies nothing either way.
        head.append(f"  {icons.DEAD} rejected by Traefik", style="red")
    parts: list[RenderableType] = [head]
    for name in router.middlewares:
        mw = info.middlewares.get(name)
        kind = f" ({mw.kind})" if mw and mw.kind else ""
        parts.append(Text(f"     ├─ ⇢ {name}{kind}", style="dim"))
    parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def _attached(entrypoint, info: TraefikInfo) -> list[TraefikRouter]:
    """The routers on this entrypoint, internal ones last.

    A router naming no entrypoint is attached to all of them by Traefik (see
    ``unknown_entrypoints``) — without that clause it would render in none.
    The sort keeps ``ping-router``, which hangs on six of nine entrypoints,
    from leading every branch.
    """
    attached = [
        r for r in info.routers if not r.entrypoints or entrypoint.name in r.entrypoints
    ]
    attached.sort(key=lambda r: (r.source != "swarm", r.name))
    return attached


def _entrypoint_block(entrypoint, info: TraefikInfo, swarm: SwarmInfo | None) -> Group:
    head = Text(f"{entrypoint.name}  {entrypoint.address}", style="bold cyan")
    attached = _attached(entrypoint, info)
    if not attached:
        # A published port nothing serves is a finding, not an absence.
        head.append("   — no router", style="dim")
        return Group(head)
    return Group(head, *[_router_lines(r, info, swarm) for r in attached])


def _compact_entrypoint(entrypoint, info: TraefikInfo,
                        swarm: SwarmInfo | None) -> Group:
    """One line per entrypoint, expanded only where something is wrong.

    The summary carries the worst verdict among the entrypoint's routers, so a
    healthy branch costs one line and a broken one still names which router
    broke. ``·`` is shown but never expanded: it means Docker was not measured,
    which is one condition for the whole panel rather than a finding about any
    single router — expanding on it would print every branch in full.
    """
    head = Text(f"  {entrypoint.name}  {entrypoint.address}", style="bold cyan")
    attached = _attached(entrypoint, info)
    if not attached:
        head.append("   — no router", style="dim")
        return Group(head)
    states = [(router, *_service_state(router, info, swarm)) for router in attached]
    head.append(f"   {len(attached)} router", style="dim")
    worst = max((icon for _, icon, _ in states), key=severity, default="")
    if worst:
        head.append(f"   {worst}")
    problems = [
        router for router, icon, _ in states if severity(icon) >= severity(icons.WARN)
    ]
    return Group(head, *[_router_lines(r, info, swarm) for r in problems])


def _orphan_block(info: TraefikInfo, swarm: SwarmInfo | None) -> Group | None:
    known = {ep.name for ep in info.entrypoints}
    orphans: list[tuple[TraefikRouter, list[str]]] = []
    for router in info.routers:
        missing = unknown_entrypoints(router, known)
        if missing:
            orphans.append((router, missing))
        elif not known and not router.entrypoints:
            # Traefik attaches this one to every entrypoint — and not one of
            # them could be read, so the tree above has no branch for it
            # either. Without this it would be the router that vanishes.
            orphans.append((router, []))
    if not orphans:
        return None
    parts: list[RenderableType] = [_subhead("ORPHANED ROUTERS")]
    for router, missing in orphans:
        named = ", ".join(f"`{name}`" for name in missing)
        if missing and known:
            # The entrypoints were read, and this name is not among them.
            head = Text(
                f"  {icons.FAILED} {router.name}        entrypoint {named} does not exist",
                style="red",
            )
        elif missing:
            # No entrypoint was read at all: the router cannot be placed, but
            # calling its entrypoint nonexistent would claim a measurement
            # that never happened.
            head = Text(
                f"  {icons.WARN} {router.name}        entrypoint {named}"
                " — no entrypoint could be read",
                style="yellow",
            )
        else:
            head = Text(
                f"  {icons.WARN} {router.name}        attached to every entrypoint"
                " — none could be read",
                style="yellow",
            )
        parts.append(head)
        if router.rule:
            parts.append(Text(f"     {router.rule}", style="dim"))
        parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def traefik_section(info: TraefikInfo | None, cfg: Config,
                    swarm: SwarmInfo | None = None,
                    compact: bool = False) -> RenderableType:
    """The TRAEFIK WIRING block.

    ``compact`` replaces the tree with one line per entrypoint. The full tree
    runs to some seventy lines on ``lrz_cc``, which is a debugging view, not a
    login banner — so the panel that greets a login summarises, and
    ``status-traefik`` still draws the whole thing. The orphan block is
    identical either way: it holds the findings, and a finding is never the
    part to shorten.
    """
    data = info or TraefikInfo()
    if data.error:
        return section("TRAEFIK WIRING",
                       Text(f"{icons.FAILED} {data.error}", style="red"))
    if not data.reachable:
        return section("TRAEFIK WIRING", Text("not checked", style="dim"))

    parts: list[RenderableType] = []
    if not data.entrypoints:
        # A coverage gap, not an empty configuration: the Traefik service may
        # carry a different name than TRAEFIK_SERVICE_PATTERNS matches, or
        # declare its entrypoints in static YAML rather than in Args. The tree
        # below cannot be drawn, but the routers are still known — they follow
        # in the orphan block, which in this state holds every one of them.
        parts.append(Text(
            f"{icons.WARN} no entrypoints found — the tree cannot be drawn,"
            " the routers below could not be placed",
            style="yellow",
        ))
        parts.append(Text(""))
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
        if compact:
            parts.append(_compact_entrypoint(entrypoint, data, swarm))
        else:
            parts.append(_entrypoint_block(entrypoint, data, swarm))
            parts.append(Text(""))
    if compact and ordered:
        parts.append(Text(""))
    orphans = _orphan_block(data, swarm)
    if orphans is not None:
        parts.append(orphans)
    return section("TRAEFIK WIRING", Group(*parts))
