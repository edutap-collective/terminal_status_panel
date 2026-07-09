"""Header-style sections for system info, load, memory, filesystems,
updates, and services. Each builder returns a Rich renderable and tolerates
``None``/degraded input."""

from __future__ import annotations

from datetime import datetime

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config, Thresholds
from ..model import ResourceUsage, SwarmInfo, SystemInfo, UpdateInfo
from .bars import STATUS_COLORS, classify, format_bytes, render_bar
from .logo import os_logo

# CPU-usage bar coloring (not user-configurable — purely cosmetic thresholds).
_CPU_WARNING = 70.0
_CPU_CRITICAL = 90.0


def section(title: str, body: RenderableType) -> Group:
    """A left-aligned rule header followed by the section body."""
    header = Rule(f"[bold blue]{title}[/]", align="left", style="blue")
    return Group(header, body)


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


def system_overview(info: SystemInfo | None) -> Group:
    table = _kv_table()
    if info is None:
        table.add_row("Status", "not available")
        return section("SYSTEM OVERVIEW", table)

    # ``os_name`` from distro.name(pretty=True) usually already embeds the
    # version/codename, so only append the version when it is not already there.
    os_line = info.os_name or "n/a"
    if info.os_version and info.os_version not in (info.os_name or ""):
        os_line = f"{os_line} {info.os_version}"
    table.add_row("OS", os_line)
    table.add_row("Kernel", info.kernel or "n/a")
    table.add_row("Hostname", info.hostname or "n/a")
    table.add_row("Uptime", _fmt_uptime(info.uptime_seconds))
    table.add_row("Date", _now())
    table.add_row("User", info.user or "n/a")
    table.add_row("IP", ", ".join(info.ip_addresses) or "n/a")

    body = Columns([os_logo(info.os_name), table], padding=(0, 3))
    return section("SYSTEM OVERVIEW", body)


def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "", "")
        return
    bar = render_bar(percent, status, width=24)
    pct = Text(f"{percent:5.1f}%", style=STATUS_COLORS.get(status, "white"))
    detail = f"{format_bytes(used)} / {format_bytes(total)}"
    table.add_row(label, bar, pct, detail)


def memory_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")   # label
    table.add_column()                    # bar
    table.add_column(justify="right")     # percent
    table.add_column()                    # detail
    t = cfg.thresholds

    if res is None:
        table.add_row("Status", Text("not available", style="dim"), "", "")
        return section("MEMORY & SWAP", table)

    _bar_row(table, "RAM", res.mem_percent, res.mem_used, res.mem_total,
             classify(res.mem_percent or 0, t.memory_warning, t.memory_critical))
    _bar_row(table, "SWAP", res.swap_percent, res.swap_used, res.swap_total,
             classify(res.swap_percent or 0, t.swap_warning, 100.0))
    return section("MEMORY & SWAP", table)


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


def load_panel(res: ResourceUsage | None, cfg: Config) -> Group:
    if res is None:
        return section("SYSTEM LOAD", Text("not available", style="dim"))

    head = Table.grid(padding=(0, 1))
    head.add_column(style="bold cyan")
    head.add_column()
    head.add_column(justify="right")
    head.add_column()

    head.add_row("Load Average", _load_text(res.load_avg, res.cpu_count, cfg.thresholds),
                 "", "")
    if res.cpu_percent is not None:
        status = classify(res.cpu_percent, _CPU_WARNING, _CPU_CRITICAL)
        head.add_row("CPU", render_bar(res.cpu_percent, status, width=24),
                     Text(f"{res.cpu_percent:5.1f}%", style=STATUS_COLORS.get(status, "white")),
                     "")

    parts: list[RenderableType] = [head]
    if res.cpu_per_core:
        parts.append(Text("CPU Usage (per core)", style="bold blue"))
        cores = Table.grid(padding=(0, 1))
        cores.add_column(style="cyan")
        cores.add_column()
        cores.add_column(justify="right")
        for idx, pct in enumerate(res.cpu_per_core, start=1):
            status = classify(pct, _CPU_WARNING, _CPU_CRITICAL)
            cores.add_row(
                f"Core {idx}",
                render_bar(pct, status, width=22),
                Text(f"{pct:5.1f}%", style=STATUS_COLORS.get(status, "white")),
            )
        parts.append(cores)

    return section("SYSTEM LOAD", Group(*parts))


def filesystem_panel(res: ResourceUsage | None) -> Group:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")     # mount
    table.add_column(justify="right")       # size
    table.add_column(justify="right")       # used
    table.add_column(justify="right")       # avail
    table.add_column()                      # use% bar
    table.add_column(justify="right")       # use%

    if res is None or not res.filesystems:
        table.add_row("Status", "", "", "", Text("no filesystems", style="dim"), "")
        return section("FILESYSTEM USAGE", table)

    table.add_row("Mounted on", "Size", "Used", "Avail", "", "Use%")
    for fs in res.filesystems:
        status = classify(fs.percent, 80.0, 90.0)
        avail = max(fs.total - fs.used, 0)
        table.add_row(
            fs.mountpoint,
            format_bytes(fs.total),
            format_bytes(fs.used),
            format_bytes(avail),
            render_bar(fs.percent, status, width=12),
            Text(f"{fs.percent:.0f}%", style=STATUS_COLORS.get(status, "white")),
        )
    return section("FILESYSTEM USAGE", table)


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


def _swarm_summary(swarm: SwarmInfo) -> Table:
    table = _kv_table()
    role = swarm.node_role or "?"
    table.add_row("Swarm", Text(f"active ({role})", style="green"))
    table.add_row("Nodes", str(swarm.node_count if swarm.node_count is not None else "?"))
    table.add_row("Services", str(len(swarm.services)))
    return table


def _service_cell(svc) -> Text:
    desired = svc.desired_replicas
    healthy = desired is not None and svc.running_replicas >= desired
    color = "green" if healthy else "red"
    desired_str = desired if desired is not None else "-"
    name = Text(svc.name, style="bold" if svc.critical else "")
    return Text.assemble(
        ("● ", color), name, (f" {svc.running_replicas}/{desired_str}", color)
    )


def services_section(swarm: SwarmInfo | None, cfg: Config) -> Group:
    if swarm is None or not swarm.reachable:
        return section("DOCKER SWARM", Text("Docker not reachable", style="dim"))

    if not swarm.enabled:
        title = "DOCKER (containers)"
        left = _kv_table()
        left.add_row("Swarm", Text("inactive", style="yellow"))
        left.add_row("Services", str(len(swarm.services)))
    else:
        title = "DOCKER SWARM"
        left = _swarm_summary(swarm)

    if swarm.services:
        cells: list[RenderableType] = [_service_cell(s) for s in swarm.services]
        right: RenderableType = Columns(cells, padding=(0, 3), equal=True)
    else:
        right = Text("no services", style="dim")

    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column()
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return section(title, grid)
