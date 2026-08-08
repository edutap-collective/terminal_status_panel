"""Header-style sections and sub-blocks for the status dashboard.

Top-level sections (SYSTEM OVERVIEW, UPDATES, SYSTEM STATUS, DOCKER INFOS) get a
rule header via :func:`section`; sub-blocks inside them use the lighter
:func:`_subhead`. Every builder returns a Rich renderable and tolerates
``None``/degraded input.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.measure import Measurement
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..collectors.clusters import kind_for_service
from ..config import Config, Thresholds
from ..model import (
    ClusterService,
    HealthInfo,
    PeerReachability,
    ProcessInfo,
    ProcessSnapshot,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
    SwarmNode,
    SystemInfo,
    UpdateInfo,
)
from .bars import STATUS_COLORS, classify, format_bytes, render_bar
from .icons import DEAD as _DEAD
from .icons import OK as _OK
from .icons import WARN as _WARN
from .logo import os_logo
from .verdict import service_verdict

# CPU-usage bar coloring (not user-configurable — purely cosmetic thresholds).
_CPU_WARNING = 70.0
_CPU_CRITICAL = 90.0

# Name of the synthetic stack collecting infrastructure admin UIs.
INFRA_UI_STACK = "infra-uis"

#: A laptop with several interfaces and IPv6 privacy addresses can present
#: thirty addresses. Past a handful the list stops informing and starts
#: displacing everything below it.
MAX_RENDERED_IPS = 8

# Ordinal instances of one service: a connector pinned per node must run as
# connector_1, _2, … because each instance needs its own secrets.
# Underscore only — with '-<digits>' a stack named PostgreSQL-18 whose service
# carries the same name would be mutilated to PostgreSQL-18_PostgreSQL.
# The price: two unrelated services whose names differ only in a trailing
# '_<digits>' — say infra_php_7 and infra_php_8 — collapse into one "php" row
# summing their replicas, and _group_desc shows only the first one's
# description, so the second service disappears along with its description.
# Accepted: no service in this environment is named that way, and the
# alternative — collapsing only when siblings actually exist — would rename
# the row as instances come and go.
_ORDINAL_SUFFIX = re.compile(r"_\d+$")


def _short_mount(path: str, width: int) -> str:
    """Shorten from the left, keeping the tail.

    A long mount path carries its distinguishing part at the end --
    ``.../Volumes/iOS_23C54``. Cutting the tail, which is what letting the table
    truncate would do, turns a dozen distinct rows into a dozen copies of
    ``/Libr…``.
    """
    if len(path) <= width:
        return path
    if width <= 1:
        return "…"
    return "…" + path[-(width - 1):]


def section(title: str, body: RenderableType) -> Group:
    """A left-aligned rule header followed by the section body."""
    header = Rule(f"[bold blue]{title}[/]", align="left", style="blue")
    return Group(header, body)


def _subhead(title: str) -> Text:
    return Text(title, style="bold cyan")


def _kv_table() -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="left", style="bold cyan")
    table.add_column(justify="left")
    return table


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# System overview + updates (top row)
# --------------------------------------------------------------------------- #

def system_overview(info: SystemInfo | None) -> Group:
    table = _kv_table()
    if info is None:
        table.add_row("Status", "not available")
        return section("SYSTEM OVERVIEW", table)

    # distro.name(pretty=True) usually already embeds the version/codename.
    os_line = info.os_name or "n/a (OS identity unavailable)"
    if info.os_version and info.os_version not in (info.os_name or ""):
        os_line = f"{os_line} {info.os_version}"
    table.add_row("OS", os_line)
    table.add_row("Kernel", info.kernel or "n/a")
    table.add_row("Hostname", info.hostname or "n/a")
    table.add_row("Uptime", _fmt_uptime(info.uptime_seconds))
    table.add_row("Date", _now())
    table.add_row("User", info.user or "n/a")
    addresses = info.ip_addresses
    if not addresses:
        ip_line = "n/a"
    else:
        ip_line = ", ".join(addresses[:MAX_RENDERED_IPS])
        hidden = len(addresses) - MAX_RENDERED_IPS
        if hidden > 0:
            ip_line = f"{ip_line} … (+{hidden} more)"
    table.add_row("IP", ip_line)

    logo = os_logo(info.os_name)
    if logo.plain:
        body = Table.grid(padding=(0, 3))
        body.add_column(vertical="middle")  # logo
        body.add_column()                   # label + value table
        body.add_row(logo, table)
    else:
        body = table
    return section("SYSTEM OVERVIEW", body)


def updates_panel(updates: UpdateInfo | None) -> Group:
    if updates is None or not updates.supported:
        body = Text("n/a (not a Debian/Ubuntu system)", style="dim")
        return section("UPDATES", body)

    security = updates.security or 0
    avail_color = "red" if security else ("yellow" if updates.available else "green")
    table = Table.grid(padding=(0, 1))
    table.add_column()
    table.add_row(Text.assemble(
        ("Available updates: ", "bold cyan"),
        (str(updates.available if updates.available is not None else "?"), avail_color),
    ))
    table.add_row(Text.assemble(
        ("  • Security updates: ", "cyan"),
        (str(security), "red" if security else "green"),
    ))
    table.add_row(Text.assemble(
        ("  • Standard updates: ", "cyan"),
        (str(updates.standard if updates.standard is not None else "?"), "yellow"),
    ))
    return section("UPDATES", table)


# --------------------------------------------------------------------------- #
# System status (load + memory/swap + filesystem)
# --------------------------------------------------------------------------- #

#: Fixed width of the RAM/SWAP bars in the MEMORY & SWAP block. The
#: filesystem bar reuses this as its upper bound (see ``_FilesystemBody``) so
#: the two blocks, which sit side by side, carry equal visual weight instead
#: of the filesystem bar sprawling to whatever space happens to be left over.
_MEMORY_BAR_WIDTH = 44


def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "", "")
        return
    bar = render_bar(percent, status, width=_MEMORY_BAR_WIDTH)
    pct = Text(f"{percent:5.1f}%", style=STATUS_COLORS.get(status, "white"))
    detail = f"{format_bytes(used)} / {format_bytes(total)}"
    table.add_row(label, bar, pct, detail)


def _load_text(load_avg, cpu_count, thresholds: Thresholds) -> Text:
    if not load_avg:
        return Text("n/a", style="dim")
    cpus = cpu_count or 1
    text = Text()
    for i, value in enumerate(load_avg):
        status = classify((value / cpus) * 100,
                          thresholds.load_warning * 100, thresholds.load_critical * 100)
        if i:
            text.append(" ")
        text.append(f"{value:.2f}", style=STATUS_COLORS.get(status, "white"))
    text.append("  (1/5/15 min)", style="dim")
    return text


def _load_body(res: ResourceUsage, cfg: Config) -> RenderableType:
    head = Table.grid(padding=(0, 1))
    head.add_column(style="bold cyan")
    head.add_column()
    head.add_column(justify="right")
    head.add_row("Load Average", _load_text(res.load_avg, res.cpu_count, cfg.thresholds), "")
    if res.cpu_percent is not None:
        status = classify(res.cpu_percent, _CPU_WARNING, _CPU_CRITICAL)
        head.add_row("CPU", render_bar(res.cpu_percent, status, width=44),
                     Text(f"{res.cpu_percent:5.1f}%", style=STATUS_COLORS.get(status, "white")))

    parts: list[RenderableType] = [head]
    if res.cpu_per_core:
        parts.append(Text("per core", style="dim"))
        cores = Table.grid(padding=(0, 1))
        cores.add_column(style="cyan")
        cores.add_column()
        cores.add_column(justify="right")
        for idx, pct in enumerate(res.cpu_per_core, start=1):
            status = classify(pct, _CPU_WARNING, _CPU_CRITICAL)
            cores.add_row(f"Core {idx}", render_bar(pct, status, width=36),
                          Text(f"{pct:5.1f}%", style=STATUS_COLORS.get(status, "white")))
        parts.append(cores)
    return Group(*parts)


def _memory_body(res: ResourceUsage, cfg: Config) -> RenderableType:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_column(justify="right")
    table.add_column()
    t = cfg.thresholds
    _bar_row(table, "RAM", res.mem_percent, res.mem_used, res.mem_total,
             classify(res.mem_percent or 0, t.memory_warning, t.memory_critical))
    _bar_row(table, "SWAP", res.swap_percent, res.swap_used, res.swap_total,
             classify(res.swap_percent or 0, t.swap_warning, 100.0))
    return table


#: A bar thinner than this reads as a stray colour smear, not as usage --
#: below it the row is better off with no bar at all.
_MIN_FS_BAR_WIDTH = 6

#: The padding cell ``Table.grid`` inserts between the last numeric column
#: and the bar column once it exists (matches the table's own ``padding``).
_FS_BAR_GAP = 2

#: Comfortably wider than any realistic filesystem row could ever need, so
#: measuring against it yields the columns' true desired width rather than
#: one clipped to whatever space happens to be available (see
#: ``_FilesystemBody`` -- ``Table.__rich_measure__`` clips to the width it is
#: asked about, so asking about a merely "big enough" width would silently
#: hide how much room the fixed columns actually want).
_UNBOUNDED_WIDTH = 10_000


def _filesystem_table(res: ResourceUsage, bar_width: int | None) -> Table:
    """Build the filesystem table, with a usage bar column iff *bar_width* is given."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True, max_width=14)
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_column(justify="right")
    if bar_width is not None:
        table.add_column(no_wrap=True, width=bar_width)

    def _row(*cells: RenderableType) -> None:
        if bar_width is not None:
            cells = (*cells, "")
        table.add_row(*cells)

    if not res.filesystems:
        _row("Status", "", "", "", Text("no filesystems", style="dim"))
        return table
    _row("Mounted on", "Size", "Used", "Avail", "Use%")
    for fs in res.filesystems:
        status = classify(fs.percent, 80.0, 90.0)
        avail = max(fs.total - fs.used, 0)
        cells: list[RenderableType] = [
            _short_mount(fs.mountpoint, 14), format_bytes(fs.total), format_bytes(fs.used),
            format_bytes(avail),
            Text(f"{fs.percent:.0f}%", style=STATUS_COLORS.get(status, "white")),
        ]
        if bar_width is not None:
            cells.append(render_bar(fs.percent, status, width=bar_width))
        table.add_row(*cells)
    return table


class _FilesystemBody:
    """The filesystem table, with a usage bar sized to whatever is left over.

    An earlier task dropped the bar entirely to free up columns, having
    measured the layout only at width 80 -- at the maintainer's actual
    terminal (215 columns) that left roughly 90 columns unused. Simply
    giving the bar a ``ratio`` column would bring it back, but Rich's own
    column-shrinking falls back to reducing *every* column evenly once the
    table no longer fits (see ``Table._calculate_column_widths``'s
    last-resort ``ratio_reduce`` step) -- including columns marked
    ``no_wrap``, which is exactly the squeeze the numeric columns must never
    take. So the decision of whether the bar exists at all, and how wide it
    is, is made here at render time against the real available width
    (``options.max_width``, the width Rich has already allotted this cell)
    rather than left to that generic algorithm: when there is not enough
    left over for a bar that would actually read as one, the table falls
    back to exactly the same rendering as before the bar existed.

    The width is additionally capped at ``_MEMORY_BAR_WIDTH``: on a wide
    terminal, leftover space alone would let this bar run far past the
    RAM/SWAP bars in the block directly above it, so the two blocks stop
    reading as a matched pair. Below the cap the bar keeps flexing with the
    available width exactly as before.
    """

    def __init__(self, res: ResourceUsage) -> None:
        self._res = res

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        base = _filesystem_table(self._res, bar_width=None)
        natural_width = Measurement.get(
            console, options.update_width(_UNBOUNDED_WIDTH), base
        ).maximum
        bar_width = min(
            options.max_width - natural_width - _FS_BAR_GAP, _MEMORY_BAR_WIDTH
        )
        if bar_width >= _MIN_FS_BAR_WIDTH:
            yield _filesystem_table(self._res, bar_width=bar_width)
        else:
            yield base


def _filesystem_body(res: ResourceUsage) -> RenderableType:
    return _FilesystemBody(res)


#: Truncate a service name that will not fit rather than wrapping it. A wrapped
#: cell would break the column alignment the eye uses to scan five rows, and
#: five rows is the whole point of this block.
_SERVICE_WIDTH = 22


def _service_name(origin: str | None, origins: dict[str, str] | None) -> str:
    """What to print in the SERVICE column.

    A systemd unit already names itself. A container names only its ID, and the
    ID becomes a service name only if the Docker section was collected and
    knows it. Where it does not -- `status-server` alone never opens the socket
    -- the ID stands. Resolving it any further would mean naming a service on
    the strength of nothing.
    """
    if not origin:
        return ""
    if not origin.startswith("container "):
        return origin
    short = origin.removeprefix("container ")
    for container_id, service in (origins or {}).items():
        if container_id.startswith(short):
            return service
    return short


def _process_table(rows: list[ProcessInfo],
                   origins: dict[str, str] | None) -> Table:
    table = Table.grid(padding=(0, 2))
    for justify in ("right", "right", "right", "right", "left", "left"):
        table.add_column(justify=justify)
    table.add_row(*[Text(head, style="bold cyan")
                    for head in ("%CPU", "%MEM", "MEM", "PID", "PROCESS", "SERVICE")])
    for row in rows:
        cpu = "—" if row.cpu_percent is None else f"{row.cpu_percent:.1f}"
        mem = "—" if row.memory_percent is None else f"{row.memory_percent:.1f}"
        # Not `format_bytes(row.memory_bytes)`: that returns "n/a" for None,
        # and this row already says absence with a dash. Two vocabularies for
        # one meaning in one line is one too many.
        size = "—" if row.memory_bytes is None else format_bytes(row.memory_bytes)
        service = _service_name(row.origin, origins)
        if len(service) > _SERVICE_WIDTH:
            service = service[: _SERVICE_WIDTH - 1] + "…"
        table.add_row(Text(cpu), Text(mem), Text(size), Text(str(row.pid)),
                      Text(row.name), Text(service, style="dim"))
    return table


def _process_row(snapshot: ProcessSnapshot,
                 origins: dict[str, str] | None) -> RenderableType:
    if not snapshot.top_cpu and not snapshot.top_memory:
        # Asked and came back empty-handed: no psutil, or no process table.
        # Omitting the row silently would hide that it was tried at all.
        return Group(_subhead("TOP CPU"), Text("not available", style="dim"))
    if snapshot.top_cpu:
        left = Group(_subhead(f"TOP CPU ({snapshot.sampled:g}s)"),
                     _process_table(snapshot.top_cpu, origins))
    else:
        # No window was sampled, so there is no ranking to show. Five rows of
        # 0.0 would read as a measurement rather than as its absence.
        left = Group(_subhead("TOP CPU"),
                     Text("CPU sampling is off", style="dim"))
    right = Group(_subhead("TOP RAM"), _process_table(snapshot.top_memory, origins))
    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def system_status(res: ResourceUsage | None, cfg: Config,
                  processes: ProcessSnapshot | None = None,
                  origins: dict[str, str] | None = None) -> Group:
    if res is None:
        return section("SYSTEM STATUS", Text("not available", style="dim"))
    left = Group(_subhead("SYSTEM LOAD"), _load_body(res, cfg))
    right = Group(
        _subhead("MEMORY & SWAP"), _memory_body(res, cfg),
        Text(""), _subhead("FILESYSTEM USAGE"), _filesystem_body(res),
    )
    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column(ratio=3)
    grid.add_column(ratio=4)
    grid.add_row(left, right)
    if processes is None:
        return section("SYSTEM STATUS", grid)
    # A second row rather than a third column: SYSTEM LOAD and MEMORY & SWAP
    # already fill the width, and squeezing five-column tables in beside them
    # would truncate every service name.
    return section("SYSTEM STATUS",
                   Group(grid, Text(""), _process_row(processes, origins)))


# Standalone wrappers (kept for direct use/tests).
def load_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    if res is None:
        return section("SYSTEM LOAD", Text("not available", style="dim"))
    return section("SYSTEM LOAD", _load_body(res, cfg))


def memory_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    if res is None:
        return section("MEMORY & SWAP", Text("not available", style="dim"))
    return section("MEMORY & SWAP", _memory_body(res, cfg))


def filesystem_panel(res: ResourceUsage | None) -> Group:
    if res is None:
        return section("FILESYSTEM USAGE", Text("not available", style="dim"))
    return section("FILESYSTEM USAGE", _filesystem_body(res))


# --------------------------------------------------------------------------- #
# Docker infos (swarm key facts + stack columns)
# --------------------------------------------------------------------------- #

def _node_health(node) -> Text:
    """✅ ready and active · ⚠️ ready but drained/paused · 💀 unreachable."""
    if not node.reachable:
        return Text(f"{_DEAD} {node.state or 'down'}", style="red")
    if not node.operational:
        return Text(f"{_WARN} {node.availability or 'unavailable'}", style="yellow")
    return Text(_OK)


def _node_inline(node, mark_leader: bool = False, peers=None) -> Text:
    line = Text.assemble((node.name, "bold"), " ") + _node_health(node)
    note = _node_tunnel_note(node, peers)
    if note is not None:
        line.append_text(note)
    if mark_leader and node.leader:
        line.append(" (leader)", style="dim")
    return line


def _nodes_inline(nodes, mark_leader: bool = False, peers=None) -> Text:
    if not nodes:
        return Text("n/a", style="dim")
    line = Text()
    for i, node in enumerate(sorted(nodes, key=lambda n: n.name)):
        if i:
            line.append("   ")
        line.append_text(_node_inline(node, mark_leader=mark_leader, peers=peers))
    return line


def _short_node_names(nodes) -> list[tuple[str, str]]:
    """Return (full, short) node names in alphabetical order, stripping a shared
    hostname prefix up to the last '-' (e.g. 'host01-node-a' -> 'node-a')."""
    ordered = sorted(nodes, key=lambda n: n.name)
    names = [n.name for n in ordered]
    prefix = ""
    if len(names) > 1:
        common = os.path.commonprefix(names)
        cut = common.rfind("-")
        prefix = common[: cut + 1] if cut >= 0 else ""
    return [(n.name, n.name[len(prefix):] or n.name) for n in ordered]


def _node_capacity(nodes) -> Text | None:
    """A ' (1 drain, 1 down)' note, or None when every node is operational.

    Unreachable nodes count as 'down' only — their availability is moot."""
    withdrawn: dict[str, int] = {}
    down = 0
    for node in nodes:
        if not node.reachable:
            down += 1
        elif not node.operational:
            key = node.availability or "unavailable"
            withdrawn[key] = withdrawn.get(key, 0) + 1
    if not withdrawn and not down:
        return None

    parts = [(f"{count} {name}", "yellow") for name, count in sorted(withdrawn.items())]
    if down:
        parts.append((f"{down} down", "red"))
    note = Text(" (")
    for index, (label, style) in enumerate(parts):
        if index:
            note.append(", ")
        note.append(label, style=style)
    note.append(")")
    return note


def _peer_for_node(node_name: str,
                   peers: list[PeerReachability] | None) -> PeerReachability | None:
    """Match a Swarm node to its WireGuard peer by name.

    The two collectors name the same machine differently: Swarm reports the
    bare hostname (``node-c``), the tunnel is named from the hosts file
    (``wg-node-c.example.net``). Substring matching bridges that without
    a mapping table that would need maintaining. No match means no claim.

    Only WireGuard peers qualify. The TCP fallback's ``ok`` means "port 2377
    accepted a connection" and says nothing about a tunnel — reading it as one
    would produce a confident "(wg: ok)" from a probe that never looked.
    """
    for peer in peers or ():
        if peer.method != "wireguard":
            continue
        if node_name and node_name in peer.name:
            return peer
    return None


def _node_tunnel_note(node: SwarmNode,
                      peers: list[PeerReachability] | None) -> Text | None:
    """Why a node is down: the tunnel, or Docker above it.

    Only rendered for a node that is actually down — on a healthy line it would
    be noise, and the line is already wide. A down node with a healthy tunnel
    points at Docker; one without a handshake points at the network. That
    distinction is the whole reason the two sections are held against each
    other here rather than left for the reader to correlate by eye.
    """
    if node.reachable:
        return None
    peer = _peer_for_node(node.name, peers)
    if peer is None:
        return None
    if peer.ok:
        return Text(" (wg: ok)", style="dim")
    # ``ok`` is False for a handshake that never happened *and* for one that
    # merely aged past the staleness threshold. Calling the second "no
    # handshake" would misname it, so the age speaks for itself where there
    # is one; the parser writes "never" when there is not.
    reason = "no handshake" if peer.detail == "never" else f"last handshake {peer.detail}"
    if peer.one_way:
        reason += ", one-way"
    return Text(f" (wg: {reason})", style="red")


def _swarm_body(swarm: SwarmInfo, peers=None) -> RenderableType:
    role = swarm.node_role or "?"
    n_nodes = swarm.node_count if swarm.node_count is not None else len(swarm.nodes)
    n_stacks = len({s.stack for s in swarm.services if s.stack})

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    summary = Text()
    summary.append("active", style="green")
    summary.append(f"  ·  {role}  ·  {n_nodes} nodes")
    capacity = _node_capacity(swarm.nodes)
    if capacity is not None:
        summary.append_text(capacity)
    summary.append(f"  ·  {len(swarm.services)} services  ·  {n_stacks} stacks")
    if swarm.containers:
        summary.append(f"  ·  {len(swarm.containers)} containers")
    table.add_row("Swarm", summary)
    table.add_row("Nodes", _nodes_inline(swarm.nodes, mark_leader=True, peers=peers))
    return table


def _node_cell(services, node_full: str) -> Text:
    """Aggregate status of a row's tasks on one node.

    A single task keeps the bare glyph, so the common cell stays exactly as
    quiet as it was. From two tasks up the count is shown: one ✅ cannot say
    whether a node holds one instance or five.
    """
    tasks = [t for s in services for t in s.tasks if t.node == node_full]
    if not tasks:
        return Text(" ")
    running = sum(1 for t in tasks if t.running)
    if len(tasks) == 1:
        if running:
            return Text(_OK)
        # Not running yet is not the same as measured broken.
        if tasks[0].starting:
            return Text(_WARN, style="yellow")
        return Text(_DEAD, style="red")
    if running == len(tasks):
        return Text(f"{_OK}{len(tasks)}")
    if running == 0:
        if all(t.starting for t in tasks):
            return Text(f"{_WARN}0/{len(tasks)}", style="yellow")
        return Text(f"{_DEAD}0/{len(tasks)}", style="red")
    return Text(f"{_WARN}{running}/{len(tasks)}", style="yellow")


def _base_service_name(name: str, node_names) -> str:
    """Strip a trailing '-<node hostname>' / '_<node hostname>' so per-node
    replicas collapse (kafka_kafka-node-a -> kafka_kafka), then a
    trailing '_<digits>' so ordinal instances collapse
    (mystack_connector_1 -> mystack_connector)."""
    for nn in sorted(node_names, key=len, reverse=True):
        if nn and name.endswith(nn) and len(name) > len(nn) + 1:
            base = name[: -len(nn)].rstrip("-_")
            if base:
                name = base
                break
    return _ORDINAL_SUFFIX.sub("", name) or name


def _strip_stack_prefix(base: str, stack: str) -> str:
    for sep in ("_", "-"):
        prefix = f"{stack}{sep}"
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix):]
    return base


def _base_groups(services, node_names) -> dict[str, list]:
    groups: dict[str, list] = {}
    for svc in services:
        groups.setdefault(_base_service_name(svc.name, node_names), []).append(svc)
    return groups


def _group_desc(services) -> str:
    return next((s.description for s in services if s.description), "")


def _split_infra_uis(services, ui_keys, node_names) -> tuple[list, list]:
    """Split *services* into (admin UIs, everything else).

    A service matches when one of *ui_keys* occurs in its stack name or in its
    node-suffix-stripped service name, so a UI is found whether it runs as a
    standalone container, as its own stack, or inside a larger stack."""
    uis, rest = [], []
    for svc in services:
        base = _base_service_name(svc.name, node_names)
        haystack = f"{svc.stack or ''} {base}".lower()
        (uis if any(key in haystack for key in ui_keys) else rest).append(svc)
    return uis, rest


def _ui_subrows(ui_services, node_names, ui_keys) -> list[tuple[str, list, str]]:
    """One sub-row per admin UI, labelled without stack prefix or node suffix.

    A service that only came along because its *stack* name matched — a sidecar
    such as ``portainer_agent`` — keeps its origin as ``stack/service``, so a
    detached row stays attributable."""
    rows = []
    for base, group in _base_groups(ui_services, node_names).items():
        stack = next((s.stack for s in group if s.stack), "")
        label = (_strip_stack_prefix(base, stack) if stack else base) or base
        if stack and not any(key in label.lower() for key in ui_keys):
            label = f"{stack}/{label}"
        rows.append((label, group, _group_desc(group)))
    rows.sort(key=lambda row: row[0].lower())
    return rows


def _stack_matrix(
    title, entries, nodes, verdict: Callable[[list[ServiceStatus]], Text]
) -> RenderableType:
    short = _short_node_names(nodes)
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")          # stack / service name
    table.add_column(justify="left")        # Working
    for _ in short:
        table.add_column(justify="center")  # per-node status
    table.add_column(style="dim")           # description

    header = [_subhead(title), Text("Working", style="cyan")]
    header += [Text(s, style="cyan") for _, s in short]
    header.append(Text("Description", style="cyan"))
    table.add_row(*header)

    if not entries:
        table.add_row(Text("—", style="dim"), *[""] * (len(short) + 2))

    def _row(label, services, desc):
        cells = [label, verdict(services)]
        cells += [_node_cell(services, full) for full, _ in short]
        cells.append(Text(desc or ""))
        table.add_row(*cells)

    # The pseudo stack heads the block; real stacks stay alphabetical.
    for stack_name, subrows in sorted(
        entries, key=lambda e: (e[0] != INFRA_UI_STACK, e[0].lower())
    ):
        # A lone UI must keep its own name; collapsing would hide which one runs.
        if len(subrows) == 1 and stack_name != INFRA_UI_STACK:
            _, services, desc = subrows[0]
            _row(Text(stack_name), services, desc)
        else:
            # Several distinct services: stack header, then one row each. The
            # header's verdict cell stays empty — the sub-rows below carry
            # their own, and an aggregate here would only repeat that.
            table.add_row(Text(stack_name, style="bold cyan"), *[""] * (len(short) + 2))
            for label, services, desc in subrows:
                _row(Text(f"  {label}"), services, desc)
    return table


def _classify_origin(services, cfg, node_names):
    """Sort one origin's services into (infrastructure, service, stackless).

    The infrastructure/UI classification is unchanged -- it simply runs per
    origin now, so a Compose `adminer` still joins the infra UIs and a Compose
    project named `postgres` still counts as infrastructure. It classifies
    *stacks*, though: an entry with no stack has no project to file under and
    goes to the shared remainder regardless (see below).
    """
    infra_keys = [k.lower() for k in cfg.infrastructure_stacks]
    ui_keys = [k.lower() for k in cfg.infra_ui_services]

    def is_infra(name: str) -> bool:
        return any(k in name.lower() for k in infra_keys)

    def subrows_for(stack: str, group) -> list[tuple[str, list, str]]:
        groups = _base_groups(group, node_names)
        return [
            (_strip_stack_prefix(base, stack) or base, groups[base],
             _group_desc(groups[base]))
            for base in sorted(groups, key=str.lower)
        ]

    # Stackless entries must bypass `_split_infra_uis` for the same reason
    # they bypass `is_infra` below: a bare `docker run -d adminer` on a host
    # with no Compose projects has no project to file under, UI-shaped or
    # not. Splitting them out first, before the UI keywords are even
    # consulted, keeps that true regardless of what the name matches.
    stackful = [svc for svc in services if svc.stack is not None]
    ungrouped = [svc for svc in services if svc.stack is None]

    ui_services, remaining = _split_infra_uis(stackful, ui_keys, node_names)

    stacks: dict[str, list] = {}
    for svc in remaining:
        stacks.setdefault(svc.stack, []).append(svc)

    infra, service, stackless = [], [], []
    if ui_services:
        infra.append((INFRA_UI_STACK, _ui_subrows(ui_services, node_names, ui_keys)))
    for name, svcs in stacks.items():
        entry = (name, subrows_for(name, svcs))
        (infra if is_infra(name) else service).append(entry)
    for base, svcs in _base_groups(ungrouped, node_names).items():
        # Stackless entries never enter this origin's tables, infrastructure-
        # shaped or not. `infra`/`service` live under a "SWARM STACKS" or
        # "COMPOSE PROJECTS" heading, and a `docker run -d redis` on a host
        # with no Compose project at all was filed under a project that does
        # not exist. Being infrastructure does not give an entry a project;
        # the shared remainder table says "Standalone containers", which is
        # true of a stackless Swarm service and a stackless container alike.
        stackless.append((base, [(base, svcs, _group_desc(svcs))]))

    return infra, service, stackless


def _origin_block(title, infra, service, nodes, verdict) -> list[RenderableType]:
    """The two tables of one origin, or nothing at all when it is empty.

    Omitting an empty block is what keeps the more explicit layout readable: a
    Mac with no Swarm services and a server with no Compose projects would each
    otherwise carry a full block of dashes. Inside a block that does exist the
    placeholder stays -- "running, but nothing here" is worth saying.
    """
    if not infra and not service:
        return []
    return [
        _subhead(title),
        _stack_matrix("Infrastructure", infra, nodes, verdict),
        Text(""),
        _stack_matrix("Service", service, nodes, verdict),
        Text(""),
    ]


def _stack_columns(swarm: SwarmInfo, cfg: Config,
                   health: HealthInfo | None = None) -> RenderableType:
    node_names = [n.name for n in swarm.nodes]
    by_kind: dict[str, ClusterService] = {
        service.kind: service for service in (health.clusters if health else [])
    }
    # Same preference as the SWARM summary line: ``_node_map`` swallows a failed
    # node listing, so an empty list next to a non-zero count is reachable and
    # would otherwise render a global row as "/0".
    node_count = swarm.node_count or len(swarm.nodes)

    def verdict(services):
        kind = next(
            (k for k in (kind_for_service(s.name) for s in services) if k), None
        )
        return service_verdict(
            services, kind=kind, cluster=by_kind.get(kind) if kind else None,
            node_count=node_count,
        )

    swarm_infra, swarm_service, swarm_rest = _classify_origin(
        swarm.services, cfg, node_names
    )
    compose_infra, compose_service, compose_rest = _classify_origin(
        swarm.containers, cfg, node_names
    )

    parts: list[RenderableType] = []
    parts += _origin_block("SWARM STACKS", swarm_infra, swarm_service,
                           swarm.nodes, verdict)
    parts += _origin_block("COMPOSE PROJECTS", compose_infra, compose_service,
                           swarm.nodes, verdict)

    stackless = swarm_rest + compose_rest
    if stackless:
        parts += [
            _stack_matrix("Standalone containers", stackless, swarm.nodes, verdict),
        ]
    if not parts:
        return Text("no services or containers", style="dim")
    return Group(*parts)


def services_section(swarm: SwarmInfo | None, cfg: Config,
                     health: HealthInfo | None = None) -> Group:
    if swarm is None or not swarm.reachable:
        return section("DOCKER INFOS", Text("Docker not reachable", style="dim"))

    if not swarm.enabled:
        return section("DOCKER INFOS", _stack_columns(swarm, cfg, health))

    body = Group(
        _subhead("SWARM"),
        _swarm_body(swarm, health.peers if health else None),
        Text(""),
        _stack_columns(swarm, cfg, health),
    )
    return section("DOCKER INFOS", body)
