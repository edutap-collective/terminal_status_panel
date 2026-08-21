"""Header-style sections and sub-blocks for the status dashboard.

Top-level sections (SYSTEM OVERVIEW, UPDATES, SYSTEM STATUS, DOCKER INFOS) get a
rule header via :func:`section`; sub-blocks inside them use the lighter
:func:`_subhead`. Every builder returns a Rich renderable and tolerates
``None``/degraded input.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from datetime import datetime

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.measure import Measurement
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..collectors.clusters import kind_for_service
from ..config import Config, Thresholds
from ..model import (
    TROUBLE_WINDOW_SECONDS,
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
    return "…" + path[-(width - 1) :]


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
    """The SYSTEM OVERVIEW block: host, OS, kernel, uptime, addresses."""
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
        body.add_column()  # label + value table
        body.add_row(logo, table)
    else:
        body = table
    return section("SYSTEM OVERVIEW", body)


def updates_panel(updates: UpdateInfo | None) -> Group:
    """The UPDATES block, or a note that this platform cannot report them."""
    if updates is None or not updates.supported:
        body = Text("n/a (not a Debian/Ubuntu system)", style="dim")
        return section("UPDATES", body)

    security = updates.security or 0
    avail_color = "red" if security else ("yellow" if updates.available else "green")
    table = Table.grid(padding=(0, 1))
    table.add_column()
    table.add_row(
        Text.assemble(
            ("Available updates: ", "bold cyan"),
            (str(updates.available if updates.available is not None else "?"), avail_color),
        )
    )
    table.add_row(
        Text.assemble(
            ("  • Security updates: ", "cyan"),
            (str(security), "red" if security else "green"),
        )
    )
    table.add_row(
        Text.assemble(
            ("  • Standard updates: ", "cyan"),
            (str(updates.standard if updates.standard is not None else "?"), "yellow"),
        )
    )
    return section("UPDATES", table)


# --------------------------------------------------------------------------- #
# System status (load + memory/swap + filesystem)
# --------------------------------------------------------------------------- #

#: Fixed width of the RAM/SWAP bars in the MEMORY & SWAP block. The
#: filesystem bar reuses this as its upper bound (see ``_FilesystemBody``) so
#: the two blocks, which sit side by side, carry equal visual weight instead
#: of the filesystem bar sprawling to whatever space happens to be left over.
_MEMORY_BAR_WIDTH = 44


def _bar_row(
    table: Table,
    label: str,
    percent: float | None,
    used: int | None,
    total: int | None,
    status: str,
) -> None:
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
        status = classify(
            (value / cpus) * 100, thresholds.load_warning * 100, thresholds.load_critical * 100
        )
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
        head.add_row(
            "CPU",
            render_bar(res.cpu_percent, status, width=44),
            Text(f"{res.cpu_percent:5.1f}%", style=STATUS_COLORS.get(status, "white")),
        )

    parts: list[RenderableType] = [head]
    if res.cpu_per_core:
        parts.append(Text("per core", style="dim"))
        cores = Table.grid(padding=(0, 1))
        cores.add_column(style="cyan")
        cores.add_column()
        cores.add_column(justify="right")
        for idx, pct in enumerate(res.cpu_per_core, start=1):
            status = classify(pct, _CPU_WARNING, _CPU_CRITICAL)
            cores.add_row(
                f"Core {idx}",
                render_bar(pct, status, width=36),
                Text(f"{pct:5.1f}%", style=STATUS_COLORS.get(status, "white")),
            )
        parts.append(cores)
    return Group(*parts)


def _memory_body(res: ResourceUsage, cfg: Config) -> RenderableType:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_column(justify="right")
    table.add_column()
    t = cfg.thresholds
    _bar_row(
        table,
        "RAM",
        res.mem_percent,
        res.mem_used,
        res.mem_total,
        classify(res.mem_percent or 0, t.memory_warning, t.memory_critical),
    )
    _bar_row(
        table,
        "SWAP",
        res.swap_percent,
        res.swap_used,
        res.swap_total,
        classify(res.swap_percent or 0, t.swap_warning, 100.0),
    )
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
            _short_mount(fs.mountpoint, 14),
            format_bytes(fs.total),
            format_bytes(fs.used),
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

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        base = _filesystem_table(self._res, bar_width=None)
        natural_width = Measurement.get(
            console, options.update_width(_UNBOUNDED_WIDTH), base
        ).maximum
        bar_width = min(options.max_width - natural_width - _FS_BAR_GAP, _MEMORY_BAR_WIDTH)
        if bar_width >= _MIN_FS_BAR_WIDTH:
            yield _filesystem_table(self._res, bar_width=bar_width)
        else:
            yield base


def _filesystem_body(res: ResourceUsage) -> RenderableType:
    return _FilesystemBody(res)


#: Truncate a service name that will not fit rather than wrapping it. A wrapped
#: cell would break the column alignment the eye uses to scan the block, and a
#: readable row count -- configurable, five by default -- is the whole point
#: of this block.
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


def _process_table(rows: list[ProcessInfo], origins: dict[str, str] | None) -> Table:
    table = Table.grid(padding=(0, 2))
    for justify in ("right", "right", "right", "right", "left", "left"):
        table.add_column(justify=justify)
    table.add_row(
        *[
            Text(head, style="bold cyan")
            for head in ("%CPU", "%MEM", "MEM", "PID", "PROCESS", "SERVICE")
        ]
    )
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
        table.add_row(
            Text(cpu),
            Text(mem),
            Text(size),
            Text(str(row.pid)),
            Text(row.name),
            Text(service, style="dim"),
        )
    return table


#: Gap between the TOP CPU and TOP RAM tables when they sit side by side.
#: Used both to build the grid and to decide whether the pair fits -- the two
#: must agree, or the decision and the render could disagree about what
#: "fits" means. Matches the gap ``PackedColumns`` (``render/packing.py``)
#: uses for the same reason.
_PROCESS_TABLE_GAP = 4


class _ProcessRow:
    """TOP CPU / TOP RAM: side by side when both tables fit, stacked otherwise.

    Needs the console to answer "does it fit", so this is a renderable rather
    than a table builder -- the same shape ``PackedColumns`` uses for exactly
    this reason: only the console's width answers that question, and only at
    render time.

    Below roughly 90 columns two six-column tables no longer fit side by
    side. Hiding a column was rejected -- see the design doc's section 4 --
    because it would shorten a number, which this panel never does:
    ``_SERVICE_WIDTH`` truncates names for the opposite reason, a shortened
    name still identifies its service, where a shortened number is simply
    wrong. Stacking `TOP RAM` below `TOP CPU` instead costs height, not
    digits, so every column keeps its full value at any width.
    """

    def __init__(self, snapshot: ProcessSnapshot, origins: dict[str, str] | None) -> None:
        self._snapshot = snapshot
        self._origins = origins

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        snapshot, origins = self._snapshot, self._origins
        if not snapshot.top_cpu and not snapshot.top_memory:
            # Asked and came back empty-handed: no psutil, or no process table.
            # Omitting the row silently would hide that it was tried at all.
            yield Group(_subhead("TOP CPU"), Text("not available", style="dim"))
            return
        if snapshot.top_cpu:
            left = Group(
                _subhead(f"TOP CPU ({snapshot.sampled:g}s)"),
                _process_table(snapshot.top_cpu, origins),
            )
        else:
            # No window was sampled, so there is no ranking to show. Rows of
            # 0.0 would read as a measurement rather than as its absence.
            left = Group(_subhead("TOP CPU"), Text("CPU sampling is off", style="dim"))
        right = Group(_subhead("TOP RAM"), _process_table(snapshot.top_memory, origins))

        left_width = Measurement.get(console, options, left).maximum
        right_width = Measurement.get(console, options, right).maximum
        if left_width + right_width + _PROCESS_TABLE_GAP <= options.max_width:
            grid = Table.grid(
                padding=(0, _PROCESS_TABLE_GAP), collapse_padding=True, pad_edge=False
            )
            grid.add_column()
            grid.add_column()
            grid.add_row(left, right)
            yield grid
        else:
            yield Group(left, Text(""), right)


def _process_row(snapshot: ProcessSnapshot, origins: dict[str, str] | None) -> RenderableType:
    return _ProcessRow(snapshot, origins)


def system_status(
    res: ResourceUsage | None,
    cfg: Config,
    processes: ProcessSnapshot | None = None,
    origins: dict[str, str] | None = None,
) -> Group:
    """The RESOURCES block: load, memory, filesystems and the process lists.

    *origins* maps a container id to the service name DOCKER INFOS shows for
    it, so a process row and a service row name the same thing the same way.
    """
    if res is None:
        return section("SYSTEM STATUS", Text("not available", style="dim"))
    left = Group(_subhead("SYSTEM LOAD"), _load_body(res, cfg))
    right = Group(
        _subhead("MEMORY & SWAP"),
        _memory_body(res, cfg),
        Text(""),
        _subhead("FILESYSTEM USAGE"),
        _filesystem_body(res),
    )
    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column(ratio=3)
    grid.add_column(ratio=4)
    grid.add_row(left, right)
    if processes is None:
        return section("SYSTEM STATUS", grid)
    # A second row rather than a third column: SYSTEM LOAD and MEMORY & SWAP
    # already fill the width, and squeezing six-column tables in beside them
    # would truncate every service name.
    return section("SYSTEM STATUS", Group(grid, Text(""), _process_row(processes, origins)))


# Standalone wrappers (kept for direct use/tests).
def load_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    """The SYSTEM LOAD block."""
    if res is None:
        return section("SYSTEM LOAD", Text("not available", style="dim"))
    return section("SYSTEM LOAD", _load_body(res, cfg))


def memory_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    """The MEMORY & SWAP block."""
    if res is None:
        return section("MEMORY & SWAP", Text("not available", style="dim"))
    return section("MEMORY & SWAP", _memory_body(res, cfg))


def filesystem_panel(res: ResourceUsage | None) -> Group:
    """The FILESYSTEM USAGE block."""
    if res is None:
        return section("FILESYSTEM USAGE", Text("not available", style="dim"))
    return section("FILESYSTEM USAGE", _filesystem_body(res))


# --------------------------------------------------------------------------- #
# Docker infos (swarm key facts + stack columns)
# --------------------------------------------------------------------------- #


#: Above this, the filesystem holding Docker's data is under real pressure and
#: reclaimable bytes are worth acting on. Below it the same figures are just
#: housekeeping, and a warning that glows while a disk is a fifth full becomes
#: wallpaper -- unread on the day it finally means something.
_DISK_PRESSURE_PERCENT = 80.0


def _disk_style(disk, resources) -> str | None:
    """Yellow where Docker's data sits on a filesystem under pressure.

    The colour follows the *disk*, not the size of the reclaimable heap: 28 GB
    of recoverable images matter on a full volume and matter not at all on an
    empty one.

    The mount is found by longest match rather than by assuming "/", because a
    node that gives Docker its own volume is exactly the node where the two
    filesystems disagree. Without filesystem data no colour is chosen at all --
    an unmeasured disk is not evidence of comfort, but claiming pressure would
    be a finding nobody took.
    """
    if disk is None or resources is None or not disk.root_dir:
        return None
    best = None
    for fs in resources.filesystems:
        base = fs.mountpoint.rstrip("/")
        if disk.root_dir == fs.mountpoint or disk.root_dir.startswith(f"{base}/"):
            if best is None or len(fs.mountpoint) > len(best.mountpoint):
                best = fs
    if best is None or best.percent is None:
        return None
    return "yellow" if best.percent > _DISK_PRESSURE_PERCENT else None


def _disk_line(disk, resources=None) -> Text:
    """The one-line Docker footprint, or a stated absence.

    Absence renders rather than disappears. A missing line reads as "nothing to
    report", and that is the single reading here that would be untrue.
    """
    if disk is None:
        return Text("n/a (timeout)", style="dim")
    style = _disk_style(disk, resources)
    line = Text()
    if disk.node:
        line.append(disk.node, style="bold")
        line.append("  ·  ")
    line.append(f"{format_bytes(disk.used)} used")
    line.append(f"  ·  ↺ {format_bytes(disk.reclaimable)} reclaimable", style=style or "")
    line.append(
        f"  ·  images {format_bytes(disk.images)}"
        f"  ·  cache {format_bytes(disk.build_cache)}"
        f"  ·  volumes {format_bytes(disk.volumes)}",
        style="dim",
    )
    if disk.volumes_total:
        line.append(f"  ·  {disk.volumes_unused}/{disk.volumes_total} unused", style=style or "dim")
    return line


#: How many trouble rows the block ever renders. A node reboot brings every
#: service on it in at once, and this panel has a hard height limit -- twenty
#: rows here would push the stack tables, the point of the section, off the
#: screen. What is dropped is always named: a silent cap would claim ten
#: services are troubled where twenty are, and that is the reading a status
#: panel may least afford.
_TROUBLE_MAX_ROWS = 10

#: Marks a row of instances that are nailed to their nodes. Placed after the
#: health glyph so the row reads "healthy, and immovable" rather than
#: replacing one statement with the other.
_PINNED = "📌"


def _fmt_short_age(seconds: float | None) -> str:
    """An age that keeps its seconds while they still matter.

    Neither existing formatter fits. ``_fmt_age`` is coarse by design and
    renders 47 seconds as "0m", which reads as "standing still" -- the exact
    opposite of what a service that came up forty-seven seconds ago is doing.
    ``_fmt_uptime`` spells out days and hours for a machine that has been up
    for weeks. Here the interesting range is the small one: a service back up
    seconds ago is flapping, and that is the finding.
    """
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _trouble_table(entries) -> Table:
    """The rows themselves, worst first and capped."""
    ordered = sorted(entries, key=lambda e: (e.rank, e.name))
    shown = ordered[:_TROUBLE_MAX_ROWS]

    table = Table.grid(padding=(0, 2))
    table.add_column()  # symbol
    table.add_column()  # name
    table.add_column()  # node
    table.add_column(justify="right")  # fails
    table.add_column(justify="right")  # uptime
    table.add_column()  # cause
    table.add_row(
        Text(""),
        Text("SERVICE", style="dim"),
        Text("NODE", style="dim"),
        Text("FAILS", style="dim"),
        Text("UP", style="dim"),
        Text("CAUSE", style="dim"),
    )
    for entry in shown:
        dead = entry.severity == "dead"
        if entry.fails is None:
            # No counter applies: it never started, so it never fell. A "0"
            # here would be a measurement of something that did not happen.
            fails = Text("—", style="dim")
        else:
            prefix = "≥" if entry.fails_capped else ""
            fails = Text(f"↻ {prefix}{entry.fails}×")
        table.add_row(
            Text(_DEAD if dead else _WARN),
            Text(entry.name, style="bold"),
            Text(entry.node or "—", style="dim"),
            fails,
            Text(_fmt_short_age(entry.uptime_seconds), style="dim"),
            Text(entry.cause or "—", style="red" if dead else "yellow"),
        )
    if len(ordered) > len(shown):
        table.add_row(
            Text(""),
            Text(f"… and {len(ordered) - len(shown)} more", style="dim"),
            Text(""),
            Text(""),
            Text(""),
            Text(""),
        )
    return table


def _trouble_block(entries) -> RenderableType | None:
    """The TROUBLE block, or None when there is nothing to report.

    Nothing is the normal state, and it renders as nothing at all -- not as an
    empty heading. A block that is present every day stops being read on the
    day it has something to say.
    """
    if not entries:
        return None
    hours = TROUBLE_WINDOW_SECONDS // 3600
    return Group(_subhead(f"TROUBLE  (last {hours} h)"), _trouble_table(entries))


#: Marks a figure measured against a reservation rather than a limit. The two
#: must not look alike: exceeding a limit gets the service killed, exceeding a
#: reservation quietly makes the cluster's capacity arithmetic wrong.
_RESERVED = "⚑"


def _memory_cell(services) -> Text:
    """One row's memory on this node, against whatever reference exists.

    Three cases, and they are deliberately distinguishable at a glance:

    * a limit renders ``412.0 MB / 1.0 GB`` -- how close to being killed;
    * a reservation renders ``890.0 MB ⚑ 512.0 MB`` -- over what the cluster
      planned for, which kills nothing and breaks placement arithmetic;
    * neither renders ``6.0 GB no limit`` -- a finding in itself, because an
      unbounded service takes the node with it when it leaks.

    ``elsewhere`` where the service runs, but not here: `/containers/{id}/stats`
    reaches only the local daemon. Not a dash, which would claim the figure was
    unobtainable, and not a blank, which beside a filled cell reads as
    "consumes nothing".

    A service running *nowhere* gets a dash instead. "Elsewhere" would send a
    reader looking for it on another node, when in truth it is down -- and the
    Working cell two columns to the left already says so. The distinction is
    not hypothetical: a stopped Compose container has no local task either, and
    it is emphatically not somewhere else.
    """
    local = [s for s in services if s.local_tasks]
    if not local:
        running = any(s.running_replicas for s in services)
        return Text("elsewhere" if running else "—", style="dim")
    used = sum(s.memory_bytes or 0 for s in local)
    limit = sum(s.memory_limit or 0 for s in local)
    reservation = sum(s.memory_reservation or 0 for s in local)
    cell = Text(format_bytes(used))
    if limit:
        cell.append(f" / {format_bytes(limit)}", style="dim")
    elif reservation:
        cell.append(f" {_RESERVED} {format_bytes(reservation)}", style="dim")
    else:
        cell.append(" no limit", style="yellow")
    return cell


def _node_health(node) -> Text:
    """✅ ready and active · ⚠️ drained/paused or unreachable · 💀 down."""
    if not node.reachable:
        return Text(f"{_DEAD} {node.state or 'down'}", style="red")
    if not node.operational:
        return Text(f"{_WARN} {node.availability or 'unavailable'}", style="yellow")
    # Last, because it is the only one of the three that would otherwise render
    # green. The orchestrator can call a node `ready` while the other managers
    # cannot reach it, and that combination is a quorum risk wearing a healthy
    # tick. The two earlier branches already cover nodes that look broken.
    if node.reachable_by_managers is False:
        return Text(f"{_WARN} unreachable", style="yellow")
    return Text(_OK)


def _node_inline(node, mark_leader: bool = False, peers=None, common_version=None) -> Text:
    line = Text.assemble((node.name, "bold"), " ") + _node_health(node)
    note = _node_tunnel_note(node, peers)
    if note is not None:
        line.append_text(note)
    # Only a node that disagrees carries a version. Printing the common one
    # beside every node would repeat what the summary line above already says,
    # once per node, and bury the single entry worth finding.
    if common_version is not None and node.engine_version not in (None, common_version):
        line.append(f" {_WARN} {node.engine_version}", style="yellow")
    if mark_leader and node.leader:
        line.append(" (leader)", style="dim")
    return line


def _nodes_inline(nodes, mark_leader: bool = False, peers=None, common_version=None) -> Text:
    if not nodes:
        return Text("n/a", style="dim")
    line = Text()
    for i, node in enumerate(sorted(nodes, key=lambda n: n.name)):
        if i:
            line.append("   ")
        line.append_text(
            _node_inline(node, mark_leader=mark_leader, peers=peers, common_version=common_version)
        )
    return line


def _engine_versions(nodes) -> tuple[str | None, int]:
    """The version to state in the header, and how many distinct ones exist.

    The reported one is the most common; ties break on the lexicographically
    greatest, purely so the choice is deterministic rather than dependent on
    node ordering. Which of two equally frequent versions is called the norm
    changes nothing that matters — both are rendered either way, one in the
    header and the other beside the node that carries it.

    Nodes that reported no version are ignored rather than counted as a third
    kind. A version nobody stated is not a divergence; it is a silence.
    """
    stated = [n.engine_version for n in nodes if n.engine_version]
    if not stated:
        return None, 0
    counts: dict[str, int] = {}
    for version in stated:
        counts[version] = counts.get(version, 0) + 1
    reported = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    return reported, len(counts)


def _manager_quorum(nodes) -> Text | None:
    """A warning when one more manager loss would cost the swarm its quorum.

    Only ``False`` counts against a manager. ``None`` means the daemon said
    nothing about reachability, and treating silence as failure would raise an
    alarm about a cluster nobody measured.
    """
    managers = [n for n in nodes if n.role == "manager"]
    if not managers:
        return None
    unreachable = [n for n in managers if n.reachable_by_managers is False]
    if not unreachable:
        return None
    reachable = len(managers) - len(unreachable)
    quorum = len(managers) // 2 + 1
    if reachable > quorum:
        return None
    if reachable < quorum:
        return Text(
            f"{_WARN} {reachable}/{len(managers)} managers reachable — quorum lost",
            style="red",
        )
    return Text(
        f"{_WARN} {reachable}/{len(managers)} managers reachable — "
        "one more failure locks the swarm",
        style="yellow",
    )


def _short_node_names(nodes) -> list[tuple[str, str]]:
    """Return (full, short) node names in alphabetical order.

    A shared hostname prefix up to the last '-' is stripped, so
    'host01-node-a' becomes 'node-a'.
    """
    ordered = sorted(nodes, key=lambda n: n.name)
    names = [n.name for n in ordered]
    prefix = ""
    if len(names) > 1:
        common = os.path.commonprefix(names)
        cut = common.rfind("-")
        prefix = common[: cut + 1] if cut >= 0 else ""
    return [(n.name, n.name[len(prefix) :] or n.name) for n in ordered]


def _node_capacity(nodes) -> Text | None:
    """A ' (1 drain, 1 down)' note, or None when every node is operational.

    Unreachable nodes count as 'down' only — their availability is moot.
    """
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


def _peer_for_node(node_name: str, peers: list[PeerReachability] | None) -> PeerReachability | None:
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


def _node_tunnel_note(node: SwarmNode, peers: list[PeerReachability] | None) -> Text | None:
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


def _swarm_body(swarm: SwarmInfo, peers=None, resources=None) -> RenderableType:
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
    common_version, distinct = _engine_versions(swarm.nodes)
    if common_version is not None:
        summary.append(f"  ·  Docker {common_version}")
        if distinct > 1:
            summary.append(f" {_WARN} {distinct} versions", style="yellow")
    elif not swarm.nodes and swarm.local_engine_version:
        # A worker may not enumerate the swarm, so this is the only version
        # there is. It is marked as local because an unqualified version in
        # the swarm header reads as a statement about the swarm.
        summary.append(f"  ·  Docker {swarm.local_engine_version}")
        summary.append(" (local)", style="dim")
    table.add_row("Swarm", summary)
    table.add_row(
        "Nodes",
        _nodes_inline(
            swarm.nodes,
            mark_leader=True,
            peers=peers,
            common_version=common_version if distinct > 1 else None,
        ),
    )
    quorum = _manager_quorum(swarm.nodes)
    if quorum is not None:
        table.add_row("", quorum)
    table.add_row("Disk", _disk_line(swarm.disk, resources))
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


def _base_service_name(name: str, node_names: Sequence[str]) -> str:
    """Reduce a service name to the row it belongs in.

    A trailing '-<node hostname>' or '_<node hostname>' goes first, so
    per-node replicas collapse (kafka_kafka-node-a -> kafka_kafka), then a
    trailing '_<digits>' so ordinal instances collapse
    (mystack_connector_1 -> mystack_connector).
    """
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
            return base[len(prefix) :]
    return base


def _group_key(svc, node_names) -> str:
    """Which row a service belongs in.

    An explicit ``status.group`` wins, because a deployment that states the
    answer should not be second-guessed. It also fixes a fault the heuristic
    knowingly accepts: two *unrelated* services differing only in a trailing
    `_<digits>` -- `infra_php_7` and `infra_php_8` -- collapse into one row
    and the second one's description vanishes with it. Labelled, they stay
    apart.

    An empty label is not a group. Presence, not truthiness: a service saying
    ``status.group=""`` is saying "group me with no one", and falling through
    to the heuristic there would group it by name after all.
    """
    if svc.group:
        return svc.group
    return _base_service_name(svc.name, node_names)


def _base_groups(services, node_names) -> dict[str, list]:
    groups: dict[str, list] = {}
    for svc in services:
        groups.setdefault(_group_key(svc, node_names), []).append(svc)
    return groups


def _with_pin(cell: Text, services) -> Text:
    """Mark a collapsed row whose members cannot move.

    Only for a row that actually collapsed something. A lone `1/1` claims no
    mobility, so there is none to correct -- and a symbol on every constrained
    service would appear so often it would stop being read.

    Shown in health as much as in failure. A marker introduced only once
    everything is already red is a marker nobody has had the chance to learn.
    """
    if len(services) < 2 or not all(getattr(s, "pinned", False) for s in services):
        return cell
    return cell + Text(f" {_PINNED}")


def _group_desc(services) -> str:
    """The row's description, led by a job's schedule where there is one.

    A job's Working cell reports an outcome and an age -- "ok 12h" -- which
    raises exactly the question the schedule answers: when was it meant to run?
    Without it the reader cannot tell a job that is merely resting from one
    that has silently stopped being triggered.
    """
    desc = next((s.description for s in services if s.description), "")
    schedule = next((s.schedule for s in services if s.job and s.schedule), None)
    if not schedule:
        return desc
    return f"{schedule} · {desc}" if desc else schedule


def _split_image(reference: str) -> tuple[str, str]:
    """An image reference as (repository, tag), registry and namespace dropped.

    The colon is searched for in the last path segment only. A private registry
    carries its port in the reference -- ``registry.example.org:5000/app`` --
    and splitting the whole string on its last colon would read that port as a
    tag and the rest as the repository.
    """
    last = reference.rpartition("/")[2]
    repo, sep, tag = last.partition(":")
    return repo, tag if sep else ""


def _image_cell(services) -> Text:
    """The image the row's replicas run, marked when they do not all agree.

    One row can stand for several services -- the per-node replicas of a Swarm
    stack -- and a rolling update that stalled leaves them on different tags.
    The cell still has to name one image, so it names the one most running
    replicas carry; the marker says the row is not of one mind about it.

    Ranked by *running* replicas rather than by service count: a tag nothing
    runs any more is exactly what the marker is meant to point away from.
    """
    weights: dict[tuple[str, str], tuple[int, int]] = {}
    for svc in services:
        if not svc.image:
            continue
        key = _split_image(svc.image)
        replicas, seen = weights.get(key, (0, 0))
        weights[key] = (replicas + max(svc.running_replicas, 0), seen + 1)
    if not weights:
        return Text("")
    # Alphabetical as the last tie-break, so a row cannot change what it says
    # between two logins without anything having changed on the cluster.
    repo, tag = min(weights, key=lambda key: (-weights[key][0], -weights[key][1], key))
    if len(weights) == 1:
        return Text(f"{repo}:{tag}" if tag else repo, style="dim")
    if len({name for name, _ in weights}) == 1 and tag:
        # One image, several versions: the marker belongs where they differ.
        return Text(f"{repo}:{_WARN} {tag}", style="yellow")
    return Text(f"{_WARN} {repo}:{tag}" if tag else f"{_WARN} {repo}", style="yellow")


def _split_infra_uis(services, ui_keys, node_names) -> tuple[list, list]:
    """Split *services* into (admin UIs, everything else).

    A service matches when one of *ui_keys* occurs in its stack name or in its
    node-suffix-stripped service name, so a UI is found whether it runs as a
    standalone container, as its own stack, or inside a larger stack.
    """
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
    detached row stays attributable.
    """
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
    title,
    entries,
    nodes,
    verdict: Callable[[list[ServiceStatus]], Text],
    show_image: bool = True,
) -> RenderableType:
    short = _short_node_names(nodes)
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")  # stack / service name
    table.add_column(justify="left")  # Working
    table.add_column(justify="left")  # RAM on this node
    for _ in short:
        table.add_column(justify="center")  # per-node status
    table.add_column(style="dim")  # description
    if show_image:
        table.add_column()  # image

    header = [_subhead(title), Text("Working", style="cyan"), Text("RAM (this node)", style="cyan")]
    header += [Text(s, style="cyan") for _, s in short]
    header.append(Text("Description", style="cyan"))
    if show_image:
        header.append(Text("Image", style="cyan"))
    table.add_row(*header)

    #: Cells to the right of a row's first one -- what a header or placeholder
    #: row has to fill so the columns below it still line up.
    trailing = len(short) + (4 if show_image else 3)

    if not entries:
        table.add_row(Text("—", style="dim"), *[""] * trailing)

    def _row(label, services, desc):
        cells = [label, _with_pin(verdict(services), services), _memory_cell(services)]
        cells += [_node_cell(services, full) for full, _ in short]
        cells.append(Text(desc or ""))
        if show_image:
            cells.append(_image_cell(services))
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
            table.add_row(Text(stack_name, style="bold cyan"), *[""] * trailing)
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
            (_strip_stack_prefix(base, stack) or base, groups[base], _group_desc(groups[base]))
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


def _origin_block(title, infra, service, nodes, verdict, show_image=True) -> list[RenderableType]:
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
        _stack_matrix("Infrastructure", infra, nodes, verdict, show_image),
        Text(""),
        _stack_matrix("Service", service, nodes, verdict, show_image),
        Text(""),
    ]


def _stack_columns(
    swarm: SwarmInfo, cfg: Config, health: HealthInfo | None = None
) -> RenderableType:
    node_names = [n.name for n in swarm.nodes]
    by_kind: dict[str, ClusterService] = {
        service.kind: service for service in (health.clusters if health else [])
    }
    # Same preference as the SWARM summary line: ``_node_map`` swallows a failed
    # node listing, so an empty list next to a non-zero count is reachable and
    # would otherwise render a global row as "/0".
    node_count = swarm.node_count or len(swarm.nodes)

    def verdict(services):
        kind = next((k for k in (kind_for_service(s.name) for s in services) if k), None)
        return service_verdict(
            services,
            kind=kind,
            cluster=by_kind.get(kind) if kind else None,
            node_count=node_count,
        )

    swarm_infra, swarm_service, swarm_rest = _classify_origin(swarm.services, cfg, node_names)
    compose_infra, compose_service, compose_rest = _classify_origin(
        swarm.containers, cfg, node_names
    )

    parts: list[RenderableType] = []
    parts += _origin_block(
        "SWARM STACKS", swarm_infra, swarm_service, swarm.nodes, verdict, cfg.show_image
    )
    parts += _origin_block(
        "COMPOSE PROJECTS", compose_infra, compose_service, swarm.nodes, verdict, cfg.show_image
    )

    stackless = swarm_rest + compose_rest
    if stackless:
        parts += [
            _stack_matrix("Standalone containers", stackless, swarm.nodes, verdict, cfg.show_image),
        ]
    if not parts:
        return Text("no services or containers", style="dim")
    return Group(*parts)


def _disk_row(disk, resources) -> Table:
    """The disk line on its own grid, for the layout that has no SWARM block."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column()
    grid.add_row("Disk", _disk_line(disk, resources))
    return grid


def services_section(
    swarm: SwarmInfo | None,
    cfg: Config,
    health: HealthInfo | None = None,
    resources: ResourceUsage | None = None,
) -> Group:
    """The DOCKER INFOS block: Swarm stacks, containers and their verdicts.

    *health* is optional: without it the Working cells fall back to Docker's
    own replica measurement rather than claiming a cluster verdict nobody
    took.

    *resources* is optional in the same spirit, and used for one thing only:
    deciding whether Docker's disk footprint sits on a filesystem under
    pressure. Without it the figures still render, just uncoloured -- which is
    what `status-docker` on its own can honestly say.
    """
    if swarm is None or not swarm.reachable:
        return section("DOCKER INFOS", Text("Docker not reachable", style="dim"))

    if not swarm.enabled:
        # No SWARM block to hang it under, so the line leads the section. The
        # width pressure that argues for brevity comes from a cluster panel
        # with dozens of services; a host without a swarm has the room, and an
        # accumulated Docker is likelier there, not less.
        plain: list[RenderableType] = [_disk_row(swarm.disk, resources), Text("")]
        trouble = _trouble_block(swarm.trouble)
        if trouble is not None:
            plain += [trouble, Text("")]
        plain.append(_stack_columns(swarm, cfg, health))
        return section("DOCKER INFOS", Group(*plain))

    parts: list[RenderableType] = [
        _subhead("SWARM"),
        _swarm_body(swarm, health.peers if health else None, resources),
        Text(""),
    ]
    # Between the header and the stacks, and only when it has something to
    # say. Appended below sixty stack rows it would be read by nobody who was
    # not already scrolling; here it stands where the warning it explains was
    # raised, and in the normal case it displaces nothing because it does not
    # exist.
    trouble = _trouble_block(swarm.trouble)
    if trouble is not None:
        parts += [trouble, Text("")]
    parts.append(_stack_columns(swarm, cfg, health))
    return section("DOCKER INFOS", Group(*parts))
