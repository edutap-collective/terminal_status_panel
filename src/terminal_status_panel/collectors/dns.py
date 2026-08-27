"""DNS consistency checks.

Uses dnspython rather than ``socket.getaddrinfo`` on purpose: getaddrinfo
consults ``/etc/hosts``, and a divergence between ``/etc/hosts`` and DNS is
precisely the fault this collector exists to surface.

A divergence is reported as a *warning* (``ok=None``), never as a failure —
such overrides are sometimes deliberate here, and crying wolf would train
people to ignore the panel.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..model import DnsCheck


def read_hosts_file(path: str = "/etc/hosts") -> dict[str, set[str]]:
    """Map every name in *path* (lowercased) to the set of addresses it has."""
    mapping: dict[str, set[str]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return mapping
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        parts = entry.split()
        if len(parts) < 2:
            continue
        address, names = parts[0], parts[1:]
        for name in names:
            mapping.setdefault(name.lower(), set()).add(address)
    return mapping


def _addresses(resolver, name: str) -> list[str]:
    """A-record addresses for *name* as plain strings. Raises on failure."""
    return [str(record) for record in resolver.resolve(name, "A")]


def _default_resolver(timeout: float):
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout
    return resolver


def _own_fqdn_check(resolver, fqdn: str, own_addresses: list[str] | None) -> DnsCheck:
    """Whether the machine's own name resolves back to itself.

    An address with no PTR record is ordinary and simply contributes no name to
    the comparison; the check fails only when none of the names found is the
    one we started from.
    """
    if own_addresses is None:
        return DnsCheck(label="own FQDN", ok=False, detail="no A record")
    names: list[str] = []
    for address in own_addresses:
        try:
            names.extend(str(entry).rstrip(".") for entry in resolver.resolve_address(address))
        except Exception:  # noqa: S112
            continue
    consistent = fqdn.rstrip(".") in names
    return DnsCheck(
        label="own FQDN",
        ok=consistent,
        detail="A+PTR ok" if consistent else f"PTR: {', '.join(names) or 'missing'}",
    )


def _peers_check(peer_names: list[str], resolve: Callable[[str], list[str] | None]) -> DnsCheck:
    """One line for the whole peer list: how many answered, or which did not."""
    missing = [name for name in peer_names if resolve(name) is None]
    return DnsCheck(
        label="Peers",
        ok=not missing,
        detail=(
            f"{len(peer_names) - len(missing)}/{len(peer_names)}"
            if not missing
            else f"no answer: {', '.join(missing)}"
        ),
    )


def _expectation_checks(
    expectations: list[tuple[str, list[str]]], resolve: Callable[[str], list[str] | None]
) -> list[DnsCheck]:
    """One line per configured name. An empty expectation only asks it to resolve."""
    checks: list[DnsCheck] = []
    for name, expected in expectations:
        answer = resolve(name)
        if answer is None:
            checks.append(DnsCheck(label=name, ok=False, detail="no answer"))
        elif expected and set(answer) != set(expected):
            checks.append(DnsCheck(label=name, ok=False, detail=f"got {', '.join(answer)}"))
        else:
            checks.append(DnsCheck(label=name, ok=True, detail=", ".join(answer)))
    return checks


def _hosts_check(seen: dict[str, list[str]], hosts_path: str) -> DnsCheck:
    """``/etc/hosts`` against what DNS said, for every name already looked up.

    If nothing resolved at all -- an unreachable resolver, say -- there is
    nothing to compare, and reporting "matches" would be a false pass. That
    case and a real divergence are both ``None``: unknown, not healthy.
    """
    hosts = read_hosts_file(hosts_path)
    diverging = [
        name for name, answer in seen.items() if name in hosts and hosts[name] != set(answer)
    ]
    if not seen:
        return DnsCheck(label="/etc/hosts", ok=None, detail="no data")
    if diverging:
        return DnsCheck(label="/etc/hosts", ok=None, detail=f"diverges: {', '.join(diverging)}")
    return DnsCheck(label="/etc/hosts", ok=True, detail="matches")


def collect_dns(
    fqdn: str,
    peer_names: list[str],
    expectations: list[tuple[str, list[str]]],
    timeout: float,
    resolver=None,
    hosts_path: str = "/etc/hosts",
) -> list[DnsCheck]:
    """Resolver reachability, own name, peers, expectations, /etc/hosts. Never raises."""
    if resolver is None:
        try:
            resolver = _default_resolver(timeout)
        except Exception as exc:
            return [DnsCheck(label="Resolver", ok=False, detail=str(exc)[:60])]

    seen: dict[str, list[str]] = {}  # name -> DNS answer, for the /etc/hosts comparison

    def resolve(name: str) -> list[str] | None:
        try:
            answer = _addresses(resolver, name)
        except Exception:
            return None
        seen[name.lower()] = answer
        return answer

    # 1. Resolver reachability and latency, measured on the first lookup there
    #    is to make anyway.
    servers = ", ".join(getattr(resolver, "nameservers", []) or []) or "unknown"
    started = time.monotonic()
    own_addresses = resolve(fqdn)
    elapsed_ms = (time.monotonic() - started) * 1000
    checks: list[DnsCheck] = [
        DnsCheck(
            label=f"Resolver {servers}",
            ok=own_addresses is not None,
            detail=f"{elapsed_ms:.0f} ms" if own_addresses is not None else "no answer",
        ),
        # 2. Own FQDN forward and reverse.
        _own_fqdn_check(resolver, fqdn, own_addresses),
    ]
    # 3. Peer names, as one line rather than one per peer.
    if peer_names:
        checks.append(_peers_check(peer_names, resolve))
    # 4. Configured expectations.
    checks.extend(_expectation_checks(expectations, resolve))
    # 5. /etc/hosts, last because it compares against everything looked up above.
    checks.append(_hosts_check(seen, hosts_path))
    return checks
