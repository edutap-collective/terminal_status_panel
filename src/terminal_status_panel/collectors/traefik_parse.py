"""Pure parsers for Traefik's wiring.

No Docker, no network, no Rich — every function here turns recorded text into
model objects, which is what lets the tests drive them with the real fixtures
captured from a production cluster.
"""

from __future__ import annotations

import re

from ..model import TraefikEntrypoint

# The four default entrypoints are declared '--entrypoints.…' and the five
# vhost ones '--entryPoints.…', because different parts of the Ansible role
# build them. Case-sensitivity here silently drops five of nine.
_ENTRYPOINT_ADDRESS = re.compile(
    r"^--entrypoints\.(?P<name>[^.]+)\.address=(?P<address>.+)$", re.IGNORECASE
)


def _port_of(address: str) -> int | None:
    _, _, tail = address.rpartition(":")
    try:
        return int(tail)
    except ValueError:
        return None


def parse_entrypoints(args: list[str]) -> list[TraefikEntrypoint]:
    """Entrypoints from the Traefik service's command arguments, by port."""
    found: list[TraefikEntrypoint] = []
    for arg in args or []:
        match = _ENTRYPOINT_ADDRESS.match(arg.strip())
        if not match:
            continue
        address = match.group("address")
        found.append(
            TraefikEntrypoint(
                name=match.group("name"), address=address, port=_port_of(address)
            )
        )
    # A port-less entrypoint sorts last rather than crashing the comparison.
    found.sort(key=lambda ep: (ep.port is None, ep.port or 0, ep.name))
    return found
