# src/terminal_status_panel/collectors/network.py
"""Peer reachability at the layer the overlay networks actually run on.

The Swarm view can report a node ``ready`` while the WireGuard tunnel carrying
its overlay traffic is stale, so ``wg show all dump`` is the primary source.
Without passwordless sudo it degrades to a TCP probe rather than to a silent
gap — the method is carried in the result so the reader knows which claim is
being made.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import time

from ..model import PeerReachability
from .dns import read_hosts_file

SWARM_PORT = 2377
# WireGuard rekeys every ~2 minutes under traffic; 3 minutes is stale.
HANDSHAKE_STALE_SECONDS = 180
WG_PEER_FIELDS = 9


def _format_age(seconds: float) -> str:
    minutes, remainder = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{remainder:02d}"


def _endpoint_family(endpoint: str) -> tuple[str | None, str | None]:
    """Split ``host:port`` into (endpoint, "IPv4"|"IPv6").

    A configuration may name its endpoints by *hostname*, which WireGuard
    resolves once, when the interface comes up — so the family a peer ends up on
    depends on the resolver at that moment. Nodes started at different times can
    therefore disagree, and two families can never be each other's conntrack
    reply. That is worth showing, so the family is derived rather than discarded.

    ``(none)`` means WireGuard has not resolved the peer at all; claiming IPv4
    for that would invent a fact.
    """
    raw = endpoint.strip()
    if not raw or raw == "(none)":
        return None, None
    host = raw.rsplit("[", 1)[-1].split("]", 1)[0] if raw.startswith("[") else raw.rsplit(":", 1)[0]
    try:
        family = f"IPv{ipaddress.ip_address(host).version}"
    except ValueError:
        family = None  # a name, not an address: nothing to classify
    return raw, family


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def mixed_endpoint_families(peers: list[PeerReachability]) -> dict[str, int] | None:
    """``{"IPv4": 2, "IPv6": 2}`` when peers disagree on the family, else None.

    A disagreement is not itself a fault, but it is the precondition for a
    subtle one: where the host firewall has no explicit rule for the tunnel's
    transport port, the handshake only ever passes as a conntrack reply — and a
    reply cannot cross address families. Peers still unresolved hold no opinion.
    """
    counts: dict[str, int] = {}
    for peer in peers:
        if peer.family:
            counts[peer.family] = counts.get(peer.family, 0) + 1
    return counts if len(counts) > 1 else None


def _key_label(public_key: str) -> str:
    """Identify a peer that has no allowed-ips to name it by.

    A peer with an empty (or ``(none)``) allowed-ips column would otherwise
    render as a bare "✅ " with nothing to say which peer it is.
    """
    key = public_key.strip()
    return f"peer {key[:8]}" if key and key != "(none)" else "unknown peer"


def parse_wg_dump(dump: str, now: float, hosts: dict[str, set[str]]) -> list[PeerReachability]:
    """Parse ``wg show all dump``.

    Peer lines carry 9 tab-separated fields, the per-interface line only 5;
    the field count is the discriminator, because there may be several
    interfaces and position alone would not be reliable.
    """
    address_to_name: dict[str, str] = {}
    for name, addresses in hosts.items():
        for address in addresses:
            address_to_name.setdefault(address, name)

    peers: list[PeerReachability] = []
    for line in dump.splitlines():
        fields = line.split("\t")
        if len(fields) != WG_PEER_FIELDS:
            continue
        public_key, allowed_ips, handshake = fields[1], fields[4], fields[5]
        endpoint, family = _endpoint_family(fields[3])
        rx_bytes, tx_bytes = _to_int(fields[6]), _to_int(fields[7])
        if allowed_ips.strip() in ("", "(none)"):
            tunnel_ip = ""
        else:
            tunnel_ip = allowed_ips.split("/", 1)[0].split(",")[0].strip()
        try:
            last = float(handshake)
        except ValueError:
            last = 0.0
        if last <= 0:
            ok, detail = False, "never"
        else:
            age = now - last
            ok, detail = age < HANDSHAKE_STALE_SECONDS, _format_age(age)
        peers.append(
            PeerReachability(
                name=address_to_name.get(tunnel_ip) or tunnel_ip or _key_label(public_key),
                method="wireguard",
                ok=ok,
                detail=detail,
                rx_bytes=rx_bytes,
                tx_bytes=tx_bytes,
                endpoint=endpoint,
                family=family,
            )
        )
    return peers


def _wg_dump(timeout: float) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["sudo", "-n", "wg", "show", "all", "dump"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise OSError(completed.stdout.strip()[:120] or "wg failed")
    return completed.stdout


def _tcp_probe(peer_names: list[str], timeout: float) -> list[PeerReachability]:
    """Fallback when sudo is unavailable: does the Swarm port accept a connection."""
    per_peer = max(0.1, timeout / max(1, len(peer_names)))
    peers: list[PeerReachability] = []
    for name in peer_names:
        try:
            connection = socket.create_connection((name, SWARM_PORT), timeout=per_peer)
            connection.close()
            ok, detail = True, f"tcp/{SWARM_PORT}"
        except Exception:
            ok, detail = False, f"tcp/{SWARM_PORT} closed"
        peers.append(PeerReachability(name=name, method="tcp", ok=ok, detail=detail))
    return peers


def collect_peers(peer_names: list[str], timeout: float) -> list[PeerReachability]:
    """WireGuard handshake ages, or a TCP probe when sudo is unavailable. Never raises."""
    try:
        dump = _wg_dump(timeout)
        peers = parse_wg_dump(dump, now=time.time(), hosts=read_hosts_file())
        if peers:
            return peers
    except Exception:
        pass  # no sudo, no wg, no peers: fall through to the weaker answer
    try:
        return _tcp_probe(peer_names, timeout)
    except Exception:
        return [
            PeerReachability(name=name, method="tcp", ok=False, detail="probe failed")
            for name in peer_names
        ]
