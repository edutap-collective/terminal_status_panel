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
from .links import link_for, router_link
from .packing import PackedColumns
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
    if ref and ref.source == "file":
        # Declared in the file provider, not in Swarm. Docker cannot see where
        # this one points, so the target is shown and no verdict is given —
        # matching it against Swarm service names would report a service that
        # was never supposed to be there as missing.
        for url in ref.upstreams:
            line.append(f"  {url}", style="dim")
        line.append(f"  {icons.UNKNOWN}", style="dim")
        return icons.UNKNOWN, line
    if swarm is None or not swarm.reachable:
        # Nobody looked at Docker, or the look came back empty-handed: no
        # client (`swarm is None`) or no answer from the daemon
        # (`reachable=False`). Claiming the service does not exist would be
        # asserting what was never measured — show the neutral dot, no count.
        line.append(f"  {icons.UNKNOWN}", style="dim")
        return icons.UNKNOWN, line
    docker_name = ref.docker_service if ref else None
    # Swarm services and containers both carry router labels, so a target may
    # legitimately be either. Matching only `services` would report every
    # Compose-hosted target as missing on a host that runs no Swarm services.
    targets = list(swarm.services) + list(swarm.containers)
    matching = [s for s in targets if s.name == docker_name]
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
                  swarm: SwarmInfo | None, *, fold: bool = True,
                  base: str | None = None) -> list[Text]:
    style = "dim" if router.source == "file" else ""
    head = Text("  └─ ", style=style)
    start = len(head.plain)
    head.append(router.name)
    # Unlike the entrypoint head below, a router with no derivable path, or
    # one whose Host() names a different host than *base*, gets no link at
    # all rather than one to the bare base: `link_for(base, None)` returns
    # the base by design, which is exactly right for a whole entrypoint whose
    # sub-path is merely unknown, but wrong for one router among several on
    # it -- linking it to the root would claim it serves the root, and
    # nothing measured that.
    rejected = bool(info.api_consulted and router.rejected)
    # A router Traefik measurably rejected asserts the route does not exist;
    # a link beside that claim would offer to take you there anyway.
    url = None if rejected else router_link(base, router.rule)
    if url:
        # Applied to the name's span alone, and now rather than later: the rule
        # and the rejection notice are appended below, and a link laid over the
        # whole line would swallow both.
        head.stylize(f"link {url}", start, len(head.plain))
    if router.rule:
        head.append(f"        {router.rule}", style="dim")
    if rejected:
        # Traefik was asked and said no: a measured failure, not a suspicion.
        # The accepted case stays silent — the tree already reads as
        # configured-and-accepted — and an unconsulted router (`rejected is
        # None`, or `api_consulted` False) implies nothing either way.
        head.append(f"  {icons.DEAD} rejected by Traefik", style="red")
    lines: list[Text] = [head]
    for name in router.middlewares:
        # Traefik appends `@provider` when a router references a middleware
        # from another provider; we parsed the bare name.
        bare = name.split("@", 1)[0]
        mw = info.middlewares.get(bare)
        if mw is None:
            # A reference to something that was never declared. Rendering it
            # like a resolved one makes a typo read as working wiring.
            lines.append(Text(f"     ├─ ⇢ {name}  {icons.FAILED} no such middleware",
                              style="red"))
            continue
        kind = f" ({mw.kind})" if mw.kind else ""
        lines.append(Text(f"     ├─ ⇢ {name}{kind}", style="dim"))
    glyph, service = _service_state(router, info, swarm)
    if fold and not glyph and not router.middlewares:
        # An empty glyph means the line makes no claim at all — Traefik's own
        # `@internal` endpoints, which nobody measured. A whole line for a name
        # and nothing else, repeated on every entrypoint the ping router hangs
        # on. It fits on the end of the line above.
        #
        # Only without middlewares: with one between the head and the service,
        # moving the target up would put the flow out of order. And only for
        # the empty glyph — a file-provider service returns UNKNOWN and carries
        # its configured upstreams, which is real content and stays put.
        #
        # Folding it in is not always the shorter path to the screen, though:
        # it widens the block by the width of the target name, and a wider
        # block can push a column over the terminal width and cost the section
        # a whole column back. `traefik_section` therefore builds this branch
        # both ways — folded and not — and hands both to `PackedColumns`,
        # which measures and draws whichever one is actually shorter once
        # packed. `fold=False` is what produces the unfolded alternative.
        head.append("  " + service.plain.strip().removeprefix("└─ "))
        return lines
    lines.append(service)
    return lines


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


def _entrypoint_block(entrypoint, info: TraefikInfo,
                      swarm: SwarmInfo | None, *, fold: bool = True,
                      links: dict[str, str] | None = None) -> list[Text]:
    """This entrypoint's branch, as the flat list of lines it occupies.

    Lines rather than a ``Group`` because the packer has to know how tall and
    how wide this branch is before anything is drawn, and a ``Group`` only
    answers that by being rendered.
    """
    head = Text(entrypoint.name, style="bold cyan")
    base = (links or {}).get(entrypoint.name)
    root = link_for(base, None)
    if root:
        # Scoped to the name alone, as with the router heads below: the
        # address is a cluster-internal port, and a link swallowing it would
        # put that port inside a link meant for a public URL.
        head.stylize(f"link {root}", 0, len(head.plain))
    head.append(f"  {entrypoint.address}", style="bold cyan")
    attached = _attached(entrypoint, info)
    if not attached:
        if entrypoint.name == info.ping_entrypoint:
            # `--ping.entryPoint=…`: Traefik answers /ping here itself. The one
            # entrypoint that is supposed to carry no router.
            head.append("   — Traefik's own health check", style="dim")
        else:
            # A published port nothing serves is a finding, not an absence.
            head.append("   — no router", style="dim")
        return [head]
    worst = max(
        (_service_state(r, info, swarm)[0] for r in attached), key=severity, default=""
    )
    if worst:
        # The column head carries the worst verdict below it, so a wall of
        # branches still says at a glance which one to read first.
        head.append(f"   {worst}")
    lines = [head]
    for router in attached:
        lines.extend(_router_lines(router, info, swarm, fold=fold, base=base))
    return lines


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
        if router.origin:
            # The label lives on a Docker service (or in a config); naming the
            # router alone tells you what is broken but not where to fix it.
            head.append(f"   [{router.origin}]", style="dim")
        parts.append(head)
        if router.rule:
            parts.append(Text(f"     {router.rule}", style="dim"))
        parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def traefik_section(info: TraefikInfo | None, cfg: Config,
                    swarm: SwarmInfo | None = None) -> RenderableType:
    """The TRAEFIK WIRING block.

    The entrypoint branches are packed into height-balanced columns: stacked
    vertically they run to some seventy lines while two thirds of the terminal
    stay empty, and filled row by row — what ``rich.Columns`` does — a short
    branch beside a tall one leaves the difference blank. The orphan block
    stays full width below them: its lines are the longest in the section, and
    it holds the findings.
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
    if data.container_error:
        # Same shape as the file-provider line above, one label source over:
        # a container listing that failed drops every router declared by a
        # plain or Compose container, and the section would otherwise render
        # as though the wiring were complete. A notice only — the verdicts
        # below come from `swarm`, which this failure did not touch.
        parts.append(Text(
            f"{icons.WARN} container labels unreadable: {data.container_error}"
            " — routers declared by plain or Compose containers are missing",
            style="dim",
        ))
        parts.append(Text(""))
    if data.service_error and swarm is not None and swarm.enabled:
        # A Swarm services listing failing is the *expected*, permanent
        # outcome on a Compose-only host — Swarm inactive, no manager to ask.
        # Rendering that every time would be a warning nobody reads twice.
        # `swarm.enabled` says whether the local node believes Swarm is
        # active, so it is what tells that case apart from the one worth
        # showing: a Swarm manager or worker that genuinely could not be
        # queried. Silence when `swarm` is missing or unreachable too — an
        # accusation this section cannot back up with a real measurement.
        parts.append(Text(
            f"{icons.WARN} Swarm service labels unreadable: {data.service_error}"
            " — routers declared by Swarm services are missing",
            style="dim",
        ))
        parts.append(Text(""))
    if data.entrypoints:
        # Declaration order, which the collector preserves: the four
        # entrypoints every cluster has come before this cluster's own. The
        # packer may put them in any column, but never out of order within one.
        #
        # Two candidate renderings, folded and not: folding a router's
        # `@internal` target onto its own line saves a row per branch but
        # widens the block, and a wider block can force the packer to a
        # column fewer — paying several lines to save one. `PackedColumns`
        # packs both at print time and draws whichever is actually shorter.
        folded = [_entrypoint_block(ep, data, swarm, links=cfg.traefik.links)
                  for ep in data.entrypoints]
        unfolded = [
            _entrypoint_block(ep, data, swarm, fold=False, links=cfg.traefik.links)
            for ep in data.entrypoints
        ]
        parts.append(PackedColumns(folded, alternative=unfolded))
        parts.append(Text(""))
    orphans = _orphan_block(data, swarm)
    if orphans is not None:
        parts.append(orphans)
    return section("TRAEFIK WIRING", Group(*parts))
