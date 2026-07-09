"""Rich panels for system info, resources, and services."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import Config, Thresholds
from ..model import ResourceUsage, SwarmInfo, SystemInfo
from .bars import STATUS_COLORS, classify, format_bytes, render_bar


def _kv_table() -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="left", style="bold")
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
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def system_panel(info: SystemInfo | None) -> Panel:
    table = _kv_table()
    if info is None:
        table.add_row("Status", "not available")
    else:
        os_line = " ".join(p for p in (info.os_name, info.os_version) if p) or "n/a"
        table.add_row("Host", info.hostname or "n/a")
        table.add_row("OS", os_line)
        table.add_row("Kernel", info.kernel or "n/a")
        table.add_row("Uptime", _fmt_uptime(info.uptime_seconds))
        table.add_row("User", info.user or "n/a")
        table.add_row("IPs", ", ".join(info.ip_addresses) or "n/a")
    return Panel(table, title="System", title_align="left")


def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "", "")
        return
    bar = render_bar(percent, status)
    pct = Text(f"{percent:4.0f}%", style=STATUS_COLORS.get(status, "white"))
    detail = f"{format_bytes(used)}/{format_bytes(total)}"
    table.add_row(label, bar, pct, detail)


def _load_status(load_avg, cpu_count, thresholds: Thresholds) -> tuple[str, str]:
    if not load_avg:
        return "n/a", "ok"
    one = load_avg[0]
    cpus = cpu_count or 1
    normalized = (one / cpus) * 100
    status = classify(normalized, thresholds.load_warning * 100,
                      thresholds.load_critical * 100)
    text = f"{load_avg[0]:.2f} {load_avg[1]:.2f} {load_avg[2]:.2f}"
    return text, status


def resources_panel(res: ResourceUsage | None, cfg: Config) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")   # label
    table.add_column()               # bar
    table.add_column(justify="right")  # percent
    table.add_column()               # detail
    t = cfg.thresholds

    if res is None:
        table.add_row("Status", Text("not available", style="dim"), "", "")
        return Panel(table, title="Resources", title_align="left")

    _bar_row(table, "RAM", res.mem_percent, res.mem_used, res.mem_total,
             classify(res.mem_percent or 0, t.memory_warning, t.memory_critical))
    _bar_row(table, "SWAP", res.swap_percent, res.swap_used, res.swap_total,
             classify(res.swap_percent or 0, t.swap_warning, 100.0))
    for fs in res.filesystems:
        _bar_row(table, fs.mountpoint, fs.percent, fs.used, fs.total,
                 classify(fs.percent, t.filesystem_warning, t.filesystem_critical))

    load_text, load_status = _load_status(res.load_avg, res.cpu_count, t)
    table.add_row("Load", Text(load_text, style=STATUS_COLORS.get(load_status, "white")), "", "")
    return Panel(table, title="Resources", title_align="left")


def services_panel(swarm: SwarmInfo | None, cfg: Config) -> Panel:
    if swarm is None or not swarm.reachable:
        body = Text("Docker not reachable", style="dim")
        return Panel(body, title="Services", title_align="left")

    if swarm.enabled:
        title = f"Services (Swarm: {swarm.node_role or '?'}, {swarm.node_count or '?'} nodes)"
    else:
        title = "Services (containers)"

    table = Table.grid(padding=(0, 2))
    table.add_column()
    if not swarm.services:
        table.add_row(Text("no services", style="dim"))
    for svc in swarm.services:
        desired = svc.desired_replicas
        healthy = desired is not None and svc.running_replicas >= desired
        color = "green" if healthy else "red"
        desired_str = desired if desired is not None else "-"
        name = Text(svc.name, style="bold" if svc.critical else "")
        line = Text.assemble(
            ("● ", color), name, (f" {svc.running_replicas}/{desired_str}", color)
        )
        table.add_row(line)
    return Panel(table, title=title, title_align="left")
