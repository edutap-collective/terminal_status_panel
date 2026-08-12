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

    checks: list[DnsCheck] = []
    seen: dict[str, list[str]] = {}  # name -> DNS answer, for the /etc/hosts comparison

    def resolve(name: str) -> list[str] | None:
        try:
            answer = _addresses(resolver, name)
        except Exception:
            return None
        seen[name.lower()] = answer
        return answer

    # 1. Resolver reachability and latency.
    servers = ", ".join(getattr(resolver, "nameservers", []) or []) or "unknown"
    started = time.monotonic()
    own_addresses = resolve(fqdn)
    elapsed_ms = (time.monotonic() - started) * 1000
    checks.append(
        DnsCheck(
            label=f"Resolver {servers}",
            ok=own_addresses is not None,
            detail=f"{elapsed_ms:.0f} ms" if own_addresses is not None else "no answer",
        )
    )

    # 2. Own FQDN forward and reverse.
    if own_addresses is None:
        checks.append(DnsCheck(label="own FQDN", ok=False, detail="no A record"))
    else:
        names: list[str] = []
        for address in own_addresses:
            try:
                names.extend(str(entry).rstrip(".") for entry in resolver.resolve_address(address))
            except Exception:  # noqa: S112
                # An address with no PTR record is ordinary; it simply
                # contributes no name to the comparison.
                continue
        consistent = fqdn.rstrip(".") in names
        checks.append(
            DnsCheck(
                label="own FQDN",
                ok=consistent,
                detail="A+PTR ok" if consistent else f"PTR: {', '.join(names) or 'missing'}",
            )
        )

    # 3. Peer names.
    if peer_names:
        missing = [name for name in peer_names if resolve(name) is None]
        checks.append(
            DnsCheck(
                label="Peers",
                ok=not missing,
                detail=(
                    f"{len(peer_names) - len(missing)}/{len(peer_names)}"
                    if not missing
                    else f"no answer: {', '.join(missing)}"
                ),
            )
        )

    # 4. Configured expectations.
    for name, expected in expectations:
        answer = resolve(name)
        if answer is None:
            checks.append(DnsCheck(label=name, ok=False, detail="no answer"))
        elif expected and set(answer) != set(expected):
            checks.append(DnsCheck(label=name, ok=False, detail=f"got {', '.join(answer)}"))
        else:
            checks.append(DnsCheck(label=name, ok=True, detail=", ".join(answer)))

    # 5. /etc/hosts against what DNS said, for every name already looked up.
    # If nothing resolved at all (e.g. the resolver is unreachable), there is
    # nothing to compare against /etc/hosts — reporting "matches" there would
    # be a false pass, so this is a warning rather than a silent True.
    hosts = read_hosts_file(hosts_path)
    diverging = [
        name for name, answer in seen.items() if name in hosts and hosts[name] != set(answer)
    ]
    if not seen:
        hosts_ok, hosts_detail = None, "no data"
    elif diverging:
        hosts_ok, hosts_detail = None, f"diverges: {', '.join(diverging)}"
    else:
        hosts_ok, hosts_detail = True, "matches"
    checks.append(DnsCheck(label="/etc/hosts", ok=hosts_ok, detail=hosts_detail))
    return checks
