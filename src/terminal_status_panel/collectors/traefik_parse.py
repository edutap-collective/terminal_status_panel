"""Pure parsers for Traefik's wiring.

No Docker, no network, no Rich — every function here turns recorded text into
model objects, which is what lets the tests drive them with the real fixtures
captured from a production cluster.
"""

from __future__ import annotations

import re

from ..model import TraefikEntrypoint, TraefikMiddleware, TraefikRouter, TraefikServiceRef

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


_ROUTER = re.compile(r"^traefik\.http\.routers\.(?P<name>[^.]+)\.(?P<key>.+)$")
_MIDDLEWARE = re.compile(
    r"^traefik\.http\.middlewares\.(?P<name>[^.]+)\.(?P<kind>[^.]+)\.(?P<key>.+)$"
)
_SERVICE = re.compile(r"^traefik\.http\.services\.(?P<name>[^.]+)\.(?P<key>.+)$")


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_labels(
    labels: dict[str, str], origin: str
) -> tuple[list[TraefikRouter], dict[str, TraefikMiddleware], dict[str, TraefikServiceRef]]:
    """Routers, middlewares and services from one Docker service's labels."""
    routers: dict[str, TraefikRouter] = {}
    middlewares: dict[str, TraefikMiddleware] = {}
    services: dict[str, TraefikServiceRef] = {}

    for key, value in sorted((labels or {}).items()):
        match = _ROUTER.match(key)
        if match:
            router = routers.setdefault(
                match.group("name"),
                TraefikRouter(name=match.group("name"), origin=origin),
            )
            field = match.group("key")
            if field == "entrypoints":
                router.entrypoints = _csv(value)
            elif field == "rule":
                router.rule = value
            elif field == "middlewares":
                router.middlewares = _csv(value)
            elif field == "service":
                router.service = value
            elif field == "tls":
                router.tls = value.lower() == "true"
            continue

        match = _MIDDLEWARE.match(key)
        if match:
            name = match.group("name")
            existing = middlewares.get(name)
            if existing is None:
                middlewares[name] = TraefikMiddleware(
                    name=name,
                    kind=match.group("kind"),
                    detail=f"{match.group('key')}={value}",
                )
            continue

        match = _SERVICE.match(key)
        if match:
            name = match.group("name")
            service = services.setdefault(
                name, TraefikServiceRef(name=name, docker_service=origin)
            )
            field = match.group("key")
            if field.endswith("server.port"):
                try:
                    service.port = int(value)
                except ValueError:
                    service.port = None
            elif field.endswith("server.scheme"):
                service.scheme = value

    # Traefik defaults a router's service to the router's own name.
    for router in routers.values():
        if router.service is None:
            router.service = router.name

    return [routers[name] for name in sorted(routers)], middlewares, services
