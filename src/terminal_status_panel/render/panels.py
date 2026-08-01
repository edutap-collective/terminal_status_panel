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

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..collectors.clusters import kind_for_service
from ..config import Config, Thresholds
from ..model import (
    ClusterService,
    HealthInfo,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
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

# Ordinal instances of one service: heidi_connector must run as
# heidi_connector_1, _2, … because each pinned instance needs its own secrets.
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

def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "", "")
        return
    bar = render_bar(percent, status, width=44)
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
            format_bytes(avail), render_bar(fs.percent, status, width=24),
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
    """✅ ready and active · ⚠️ ready but drained/paused · 💀 unreachable."""
    if not node.reachable:
        return Text(f"{_DEAD} {node.state or 'down'}", style="red")
    if not node.operational:
        return Text(f"{_WARN} {node.availability or 'unavailable'}", style="yellow")
    return Text(_OK)


def _node_inline(node, mark_leader: bool = False) -> Text:
    line = Text.assemble((node.name, "bold"), " ") + _node_health(node)
    if mark_leader and node.leader:
        line.append(" (leader)", style="dim")
    return line


def _nodes_inline(nodes, mark_leader: bool = False) -> Text:
    if not nodes:
        return Text("n/a", style="dim")
    line = Text()
    for i, node in enumerate(sorted(nodes, key=lambda n: n.name)):
        if i:
            line.append("   ")
        line.append_text(_node_inline(node, mark_leader=mark_leader))
    return line


def _short_node_names(nodes) -> list[tuple[str, str]]:
    """Return (full, short) node names in alphabetical order, stripping a shared
    hostname prefix up to the last '-' (e.g. 'lmzvd06-ccc-01' -> 'ccc-01')."""
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


def _swarm_body(swarm: SwarmInfo) -> RenderableType:
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
    table.add_row("Swarm", summary)
    table.add_row("Nodes", _nodes_inline(swarm.nodes, mark_leader=True))
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
        return Text(_OK) if running else Text(_DEAD, style="red")
    if running == len(tasks):
        return Text(f"{_OK}{len(tasks)}")
    if running == 0:
        return Text(f"{_DEAD}0/{len(tasks)}", style="red")
    return Text(f"{_WARN}{running}/{len(tasks)}", style="yellow")


def _base_service_name(name: str, node_names) -> str:
    """Strip a trailing '-<node hostname>' / '_<node hostname>' so per-node
    replicas collapse (kafka_kafka-lmzvd06-ccc-01 -> kafka_kafka), then a
    trailing '_<digits>' so ordinal instances collapse
    (edutap_heidi_connector_1 -> edutap_heidi_connector)."""
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


def _stack_columns(swarm: SwarmInfo, cfg: Config,
                   health: HealthInfo | None = None) -> RenderableType:
    infra_keys = [k.lower() for k in cfg.infrastructure_stacks]
    ui_keys = [k.lower() for k in cfg.infra_ui_services]
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

    def is_infra(name: str) -> bool:
        return any(k in name.lower() for k in infra_keys)

    def subrows_for(stack: str, services) -> list[tuple[str, list, str]]:
        groups = _base_groups(services, node_names)
        return [
            (_strip_stack_prefix(base, stack) or base, groups[base], _group_desc(groups[base]))
            for base in sorted(groups, key=str.lower)
        ]

    # Admin UIs leave their origin stack and form one pseudo stack.
    ui_services, remaining = _split_infra_uis(swarm.services, ui_keys, node_names)

    stacks: dict[str, list] = {}
    ungrouped: list = []
    for svc in remaining:
        if svc.stack is None:
            ungrouped.append(svc)
        else:
            stacks.setdefault(svc.stack, []).append(svc)

    infra, service = [], []
    if ui_services:
        infra.append((INFRA_UI_STACK, _ui_subrows(ui_services, node_names, ui_keys)))
    for name, svcs in stacks.items():
        entry = (name, subrows_for(name, svcs))
        (infra if is_infra(name) else service).append(entry)

    # Ungrouped services: merge per-node replicas, classify each by base name.
    container_rows = []
    for base, svcs in _base_groups(ungrouped, node_names).items():
        entry = (base, [(base, svcs, _group_desc(svcs))])
        (infra if is_infra(base) else container_rows).append(entry)

    # Per-service rows plus a description column make each table wide, so the
    # three categories stack vertically (each full width) instead of side by side.
    return Group(
        _stack_matrix("Infrastruktur", infra, swarm.nodes, verdict),
        Text(""),
        _stack_matrix("Service", service, swarm.nodes, verdict),
        Text(""),
        _stack_matrix("Container (ohne Stack)", container_rows, swarm.nodes, verdict),
    )


def services_section(swarm: SwarmInfo | None, cfg: Config,
                     health: HealthInfo | None = None) -> Group:
    if swarm is None or not swarm.reachable:
        return section("DOCKER INFOS", Text("Docker not reachable", style="dim"))

    if not swarm.enabled:
        body = Group(_subhead("CONTAINER"), _stack_columns(swarm, cfg, health))
        return section("DOCKER INFOS", body)

    body = Group(
        _subhead("SWARM"),
        _swarm_body(swarm),
        Text(""),
        _subhead("STACKS"),
        _stack_columns(swarm, cfg, health),
    )
    return section("DOCKER INFOS", body)
