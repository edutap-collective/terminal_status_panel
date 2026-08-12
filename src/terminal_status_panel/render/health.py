"""Render the CLUSTER HEALTH section.

Icon vocabulary, extending the panel's existing scheme:

  ✅ measured healthy      ⚠️ warning        💀 measured broken
  ·  not observable        … out of budget   n/a not applicable
  ✗  check failed

The neutral dot matters: MongoDB reports its set members but not their state,
and a panel that renders an unmeasured ✅ is worse than one that says nothing.
The dot is reserved for exactly that member-level case — a service whose own
quorum was never reported gets a plain "quorum not reported" note instead, so
"not observable" and "not measured at all" never look the same.

Layout follows the rest of the dashboard (see ``render/panels.py``): a
``Rule`` header for the top-level section, plain bold-cyan sub-headers for
the blocks inside it, no bordered panels.
"""

from __future__ import annotations

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from ..collectors.network import mixed_endpoint_families
from ..config import Config
from ..model import ClusterService, HealthInfo, PeerReachability
from .bars import format_bytes
from .icons import DEAD, FAILED, OK, TRUNCATED, UNKNOWN, WARN
from .panels import section

_KIND_TITLES = {
    "postgres": "PostgreSQL",
    "mongodb": "MongoDB",
    "kafka": "Kafka (KRaft)",
    "glusterfs": "GlusterFS",
    "rustfs": "RustFS",
}


def _subhead(title: str) -> Text:
    return Text(title, style="bold cyan")


def _tri_state(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return OK if value else DEAD


def _service_lines(service: ClusterService) -> Text:
    title = _KIND_TITLES.get(service.kind, service.kind)
    if service.name:
        title = f"{title} {service.name}"

    if not service.applicable:
        return Text(f"{title}: n/a here", style="dim")
    if service.error:
        return Text(f"{FAILED} {title}: {service.error}", style="red")

    text = Text()
    if service.quorum_ok is None:
        # Reserve the neutral dot for member-level "not observable". A service
        # whose own quorum was never reported gets no icon at all — an unmeasured
        # "·" here would look like the same claim member health makes, when in
        # fact nothing about quorum was even attempted.
        header = title
        if service.detail:
            header += f" — {service.detail}"
        text.append(header, style="bold")
        text.append("  quorum not reported", style="dim")
        text.append("\n")
    else:
        icon = OK if service.quorum_ok else DEAD
        header = f"{icon} {title}"
        if service.detail:
            header += f" — {service.detail}"
        text.append(header + "\n", style="bold")
    if service.leader:
        text.append(f"   leader   {service.leader}\n")
    for member in service.members:
        icon = _tri_state(member.healthy)
        label = member.node or member.name
        line = f"   {icon} {label}"
        if member.role:
            line += f"  {member.role}"
        if member.warning:
            line += f"  {WARN} {member.warning}"
        text.append(line + "\n")
    return text


# Task names in ``HealthInfo.truncated`` that are not cluster kinds.
_NON_CLUSTER_TASKS = ("peers", "dns")


def _truncated_kinds(health: HealthInfo) -> list[str]:
    """The cluster kinds that ran out of time, in the order they were reported."""
    return [name for name in health.truncated if name not in _NON_CLUSTER_TASKS]


def _clusters_body(health: HealthInfo) -> RenderableType:
    if not health.clusters_probed:
        # Never ran: no Docker client, or every kind disabled. Rendering this as
        # "nothing found" would report a gap in coverage as a clean bill of health.
        return Text(f"{UNKNOWN} not checked (no Docker client)", style="dim")
    truncated = _truncated_kinds(health)
    if not health.clusters and not truncated:
        return Text("no clustered services found", style="dim")

    applicable = [s for s in health.clusters if s.applicable]
    not_applicable = [s for s in health.clusters if not s.applicable]

    blocks: list[RenderableType] = [_service_lines(service) for service in applicable]
    # Each kind is its own budget task, so one kind running out of time says
    # nothing about the kinds beside it — and they keep their own blocks.
    blocks.extend(
        Text(f"{TRUNCATED} {_KIND_TITLES.get(kind, kind)}: time budget exceeded", style="dim")
        for kind in truncated
    )

    # Columns flows the blocks into as many columns as the width allows and
    # falls back to one on a narrow terminal. The gap matches the PEERS/DNS
    # grid below and the DOCKER INFOS tables, so the section is spaced like the
    # rest of the dashboard.
    parts: list[RenderableType] = [Columns(blocks, padding=(0, 4), expand=False)]

    if not_applicable:
        # A one-line "n/a" does not deserve a column of its own.
        names = ", ".join(_KIND_TITLES.get(s.kind, s.kind) for s in not_applicable)
        parts.append(Text(f"n/a here: {names}", style="dim"))

    return Group(*parts)


def _peer_method_label(health: HealthInfo) -> str:
    """ "wg"/"tcp" when every peer agrees on a method, "mixed" when they don't,
    "wg" as the default when there is nothing to report."""
    if not health.peers:
        return "wg"
    methods = {peer.method for peer in health.peers}
    if len(methods) > 1:
        return "mixed"
    return "wg" if methods.pop() == "wireguard" else "tcp"


def _peer_traffic(peer: PeerReachability) -> Text:
    """``↑2.0 KB ↓1.0 KB``, and the word that names the fault when it is one.

    The TCP fallback has no counters; it must render blank rather than claim a
    zero it never measured.
    """
    if peer.rx_bytes is None or peer.tx_bytes is None:
        return Text("")
    traffic = Text(f"↑{format_bytes(peer.tx_bytes)} ↓{format_bytes(peer.rx_bytes)}", style="dim")
    if peer.one_way:
        # Sent but never received: the packets leave and nothing answers.
        # A dead peer looks different — there both counters stand still.
        traffic.append("  one-way", style="red")
    return traffic


def _peers_body(health: HealthInfo) -> RenderableType:
    if "peers" in health.truncated:
        return Text(f"{TRUNCATED} time budget exceeded", style="dim")
    if not health.peers_probed:
        # Never ran: no peer names and no WireGuard answer. Rendering this as
        # "no peers" would report a gap in coverage as a clean bill of health.
        return Text(f"{UNKNOWN} not checked (no peer list available)", style="dim")
    if not health.peers:
        return Text("no peers detected", style="dim")
    table = Table.grid(padding=(0, 2))
    table.add_column()
    table.add_column()
    table.add_column()
    for peer in health.peers:
        table.add_row(
            Text(f"{OK if peer.ok else WARN} {peer.name}"),
            Text(peer.detail or "", style="dim"),
            _peer_traffic(peer),
        )
    families = mixed_endpoint_families(health.peers)
    if families is None:
        return table
    # Not an error on its own — but it is the precondition under which a
    # tunnel that depends on conntrack stops working, so it earns a line
    # before it costs anyone an afternoon.
    spread = ", ".join(f"{count}× {family}" for family, count in sorted(families.items()))
    return Group(
        table,
        Text(f"{UNKNOWN} mixed endpoint families: {spread}", style="yellow"),
    )


def _dns_body(health: HealthInfo) -> RenderableType:
    if "dns" in health.truncated:
        return Text(f"{TRUNCATED} time budget exceeded", style="dim")
    if not health.dns_probed:
        # Never ran. "no DNS checks" is a statement about configuration and
        # must not stand in for a state it cannot distinguish from that.
        return Text(f"{UNKNOWN} not checked (DNS check did not run)", style="dim")
    if not health.dns:
        return Text("no DNS checks", style="dim")
    table = Table.grid(padding=(0, 2))
    table.add_column()
    table.add_column()
    for check in health.dns:
        icon = WARN if check.ok is None else (OK if check.ok else DEAD)
        table.add_row(Text(f"{icon} {check.label}"), Text(check.detail, style="dim"))
    return table


def _bottom_row(health: HealthInfo) -> Table:
    peers_block = Group(_subhead(f"PEERS ({_peer_method_label(health)})"), _peers_body(health))
    dns_block = Group(_subhead("DNS"), _dns_body(health))
    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(peers_block, dns_block)
    return grid


def health_section(health: HealthInfo | None, cfg: Config) -> RenderableType:
    """The CLUSTER HEALTH block: infrastructure services, peer reachability, DNS."""
    data = health or HealthInfo()
    clusters_block = Group(_subhead("CLUSTERS"), _clusters_body(data))
    body = Group(clusters_block, Text(""), _bottom_row(data))
    return section("CLUSTER HEALTH", body)
