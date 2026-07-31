# src/terminal_status_panel/collectors/network.py
"""Peer reachability at the layer the overlay networks actually run on.

The Swarm view can report a node ``ready`` while the WireGuard tunnel carrying
its overlay traffic is stale, so ``wg show all dump`` is the primary source.
Without passwordless sudo it degrades to a TCP probe rather than to a silent
gap — the method is carried in the result so the reader knows which claim is
being made.
"""

from __future__ import annotations

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
        allowed_ips, handshake = fields[4], fields[5]
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
                name=address_to_name.get(tunnel_ip, tunnel_ip),
                method="wireguard",
                ok=ok,
                detail=detail,
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
