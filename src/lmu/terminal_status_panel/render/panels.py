"""Header-style sections and sub-blocks for the status dashboard.

Top-level sections (SYSTEM OVERVIEW, UPDATES, SYSTEM STATUS, DOCKER INFOS) get a
rule header via :func:`section`; sub-blocks inside them use the lighter
:func:`_subhead`. Every builder returns a Rich renderable and tolerates
``None``/degraded input.
"""

from __future__ import annotations

from datetime import datetime

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..config import Config, Thresholds
from ..model import ResourceUsage, ServiceStatus, SwarmInfo, SystemInfo, UpdateInfo
from .bars import STATUS_COLORS, classify, format_bytes, render_bar
from .logo import os_logo

# CPU-usage bar coloring (not user-configurable — purely cosmetic thresholds).
_CPU_WARNING = 70.0
_CPU_CRITICAL = 90.0

# Emoji status markers — readable regardless of color perception.
_OK = "✅"
_WARN = "⚠️"
_DEAD = "💀"
_MISSING = "❌"

# Services surfaced as Swarm key facts (and hidden from the stack columns).
_HIGHLIGHT_KEYS = ("registry", "traefik")


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

    logo = os_logo(info.os_name)
    body = Columns([logo, table], padding=(0, 3)) if logo.plain else table
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

def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "", "")
        return
    bar = render_bar(percent, status, width=22)
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
        head.add_row("CPU", render_bar(res.cpu_percent, status, width=22),
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
            cores.add_row(f"Core {idx}", render_bar(pct, status, width=18),
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


def _filesystem_body(res: ResourceUsage) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_column(justify="right")
    table.add_column()
    table.add_column(justify="right")
    if not res.filesystems:
        table.add_row("Status", "", "", "", Text("no filesystems", style="dim"), "")
        return table
    table.add_row("Mounted on", "Size", "Used", "Avail", "", "Use%")
    for fs in res.filesystems:
        status = classify(fs.percent, 80.0, 90.0)
        avail = max(fs.total - fs.used, 0)
        table.add_row(
            fs.mountpoint, format_bytes(fs.total), format_bytes(fs.used),
            format_bytes(avail), render_bar(fs.percent, status, width=12),
            Text(f"{fs.percent:.0f}%", style=STATUS_COLORS.get(status, "white")),
        )
    return table


def system_status(res: ResourceUsage | None, cfg: Config) -> Group:
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
    return section("SYSTEM STATUS", grid)


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
    if node.reachable:
        return Text(_OK)
    return Text(f"{_DEAD} {node.state or 'down'}", style="red")


def _node_inline(node) -> Text:
    return Text.assemble((node.name, "bold"), " ") + _node_health(node)


def _nodes_inline(nodes) -> Text:
    if not nodes:
        return Text("n/a", style="dim")
    line = Text()
    for i, node in enumerate(nodes):
        if i:
            line.append("   ")
        line.append_text(_node_inline(node))
    return line


def _task_states(svc) -> Text:
    """Per-node task states: ``node ✅`` running, ``node 💀 - <state>`` else,
    plus ``(❌ N unassigned)`` for orphaned tasks."""
    parts: list[Text] = []
    for task in svc.tasks:
        if task.running:
            parts.append(Text.assemble((task.node or "?", "bold"), " ", _OK))
        else:
            parts.append(Text.assemble(
                (task.node or "?", "bold"), " ", (f"{_DEAD} - {task.state}", "red")))
    if svc.unassigned:
        parts.append(Text(f"({_MISSING} {svc.unassigned} unassigned)", style="red"))
    if not parts:
        mark = _OK if (svc.desired_replicas is not None
                       and svc.running_replicas >= svc.desired_replicas) else _DEAD
        desired = svc.desired_replicas if svc.desired_replicas is not None else "-"
        return Text(f"{svc.running_replicas}/{desired} {mark}")
    line = Text()
    for i, part in enumerate(parts):
        if i:
            line.append(", ")
        line.append_text(part)
    return line


def _is_highlight(svc) -> bool:
    hay = f"{svc.name} {svc.stack or ''}".lower()
    return any(key in hay for key in _HIGHLIGHT_KEYS)


def _highlight_line(services, key: str) -> Text | None:
    matches = [s for s in services if key in f"{s.name} {s.stack or ''}".lower()]
    if not matches:
        return None
    merged = ServiceStatus(
        name=key,
        running_replicas=sum(s.running_replicas for s in matches),
        desired_replicas=sum(
            (s.desired_replicas if s.desired_replicas is not None else s.running_replicas)
            for s in matches) or None,
        tasks=[t for s in matches for t in s.tasks],
        unassigned=sum(s.unassigned for s in matches),
    )
    return _task_states(merged)


def _swarm_body(swarm: SwarmInfo) -> RenderableType:
    role = swarm.node_role or "?"
    n_nodes = swarm.node_count if swarm.node_count is not None else len(swarm.nodes)
    n_stacks = len({s.stack for s in swarm.services if s.stack})
    leader = next((n for n in swarm.nodes if n.leader), None)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Swarm", Text.assemble(
        ("active", "green"),
        (f"  ·  {role}  ·  {n_nodes} nodes  ·  "
         f"{len(swarm.services)} services  ·  {n_stacks} stacks", "default"),
    ))
    table.add_row("Master", _node_inline(leader) if leader else Text("n/a", style="dim"))
    for key in _HIGHLIGHT_KEYS:
        line = _highlight_line(swarm.services, key)
        if line is not None:
            table.add_row(key.capitalize(), line)
    table.add_row("Nodes", _nodes_inline(swarm.nodes))
    return table


def _stack_emoji(running: int, desired: int, unassigned: int, degraded: bool) -> str:
    if running == 0:
        return _DEAD
    if running < desired or unassigned or degraded:
        return _WARN
    return _OK


def _aggregate(services) -> tuple[int, int, str]:
    running = sum(s.running_replicas for s in services)
    desired = sum(
        (s.desired_replicas if s.desired_replicas is not None else s.running_replicas)
        for s in services)
    unassigned = sum(s.unassigned for s in services)
    degraded = any(not t.running for s in services for t in s.tasks)
    return running, desired, _stack_emoji(running, desired, unassigned, degraded)


def _rollup_column(title: str, rows: list[tuple[str, list]]) -> RenderableType:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")      # name
    table.add_column()                  # emoji
    table.add_column(justify="right")   # count
    if not rows:
        table.add_row(Text("—", style="dim"), "", "")
    for name, services in sorted(rows, key=lambda r: r[0].lower()):
        running, desired, emoji = _aggregate(services)
        table.add_row(name, emoji, f"{running}/{desired}")
    return Group(_subhead(title), table)


def _stack_columns(swarm: SwarmInfo, cfg: Config) -> RenderableType:
    infra_keys = [k.lower() for k in cfg.infrastructure_stacks]

    stacks: dict[str, list] = {}
    containers: list = []
    for svc in swarm.services:
        if _is_highlight(svc):
            continue
        if svc.stack is None:
            containers.append(svc)
        else:
            stacks.setdefault(svc.stack, []).append(svc)

    infra, service = [], []
    for name, svcs in stacks.items():
        if any(k in name.lower() for k in infra_keys):
            infra.append((name, svcs))
        else:
            service.append((name, svcs))

    container_rows = [(c.name, [c]) for c in containers]

    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        _rollup_column("Infrastruktur", infra),
        _rollup_column("Service", service),
        _rollup_column("Container (ohne Stack)", container_rows),
    )
    return grid


def services_section(swarm: SwarmInfo | None, cfg: Config) -> Group:
    if swarm is None or not swarm.reachable:
        return section("DOCKER INFOS", Text("Docker not reachable", style="dim"))

    if not swarm.enabled:
        body = Group(
            _subhead("CONTAINER"),
            _stack_columns(swarm, cfg),
        )
        return section("DOCKER INFOS", body)

    body = Group(
        _subhead("SWARM"),
        _swarm_body(swarm),
        Text(""),
        _subhead("STACKS"),
        _stack_columns(swarm, cfg),
    )
    return section("DOCKER INFOS", body)
