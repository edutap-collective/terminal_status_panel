"""Pure parsers for Traefik's wiring.

No Docker, no network, no Rich — every function here turns recorded text into
model objects, which is what lets the tests drive them with the real fixtures
captured from a production cluster.
"""

from __future__ import annotations

import re

import yaml

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
    """Entrypoints from the Traefik service's command arguments, in the order
    the arguments declare them.

    Declaration order is the deployment's own grouping and reads better than
    the port number: the Ansible role lists the four entrypoints every cluster
    has — ``dashboard``, ``ping``, ``default``, ``https`` — before the per-vhost
    ones it appends for this cluster, so that grouping survives into the panel.
    Sorting by port would interleave them (``https`` at 443 first, ``dashboard``
    at 8082 last) and scatter what belongs together.
    """
    found: list[TraefikEntrypoint] = []
    seen: set[str] = set()
    for arg in args or []:
        match = _ENTRYPOINT_ADDRESS.match(arg.strip())
        if not match:
            continue
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        address = match.group("address")
        found.append(
            TraefikEntrypoint(name=name, address=address, port=_port_of(address))
        )
    return found


_PING_ENTRYPOINT = re.compile(r"^--ping\.entrypoint=(?P<name>.+)$", re.IGNORECASE)


def parse_ping_entrypoint(args: list[str]) -> str | None:
    """The entrypoint Traefik answers its own health check on, if configured.

    ``--ping=true --ping.entryPoint=ping`` makes Traefik serve ``/ping`` on
    that entrypoint itself, without a router. Rendering it as ``— no router``
    is true but reads as a finding, and this is the one entrypoint that is
    *supposed* to look empty.
    """
    for arg in args or []:
        match = _PING_ENTRYPOINT.match(arg.strip())
        if match:
            return match.group("name").strip() or None
    return None


# Same trap as the entrypoint arguments above: Traefik reads its label keys
# case-insensitively, so `…routers.image_api.entryPoints` is a valid spelling.
# Matched case-sensitively it parses to no entrypoints at all — which the
# renderer reads as "attached to every entrypoint", turning a router wired to a
# nonexistent port into one wired to all of them.
_ROUTER = re.compile(r"^traefik\.http\.routers\.(?P<name>[^.]+)\.(?P<key>.+)$", re.IGNORECASE)
_MIDDLEWARE = re.compile(
    r"^traefik\.http\.middlewares\.(?P<name>[^.]+)\.(?P<kind>[^.]+)\.(?P<key>.+)$",
    re.IGNORECASE,
)
_SERVICE = re.compile(
    r"^traefik\.http\.services\.(?P<name>[^.]+)\.(?P<key>.+)$", re.IGNORECASE
)


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
            # Case-folded for the same reason the pattern is: only the field
            # name is folded, never the router name — `ImageApi` and
            # `image_api` are two routers as far as Traefik is concerned.
            field = match.group("key").lower()
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
            field = match.group("key").lower()
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


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _upstreams(spec: object) -> list[str]:
    """The URLs a file-provider service load-balances over."""
    if not isinstance(spec, dict):
        return []
    balancer = spec.get("loadBalancer", spec.get("loadbalancer"))
    if not isinstance(balancer, dict):
        return []
    servers = balancer.get("servers")
    if not isinstance(servers, list):
        return []
    urls = []
    for server in servers:
        url = server.get("url") if isinstance(server, dict) else None
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def parse_dynamic_yaml(
    text: str, origin: str
) -> tuple[list[TraefikRouter], dict[str, TraefikMiddleware], dict[str, TraefikServiceRef]]:
    """Routers, middlewares and services from a file-provider config.

    The api and ping-router entries live only here. Without them the dashboard
    entrypoint looks empty and the /_traefik_ping_ path every webfe health
    check depends on is invisible.

    The services matter for the same reason in reverse: ``account-api`` points
    at ``account-api-placeholder``, which is declared here and not in Swarm at
    all. Read only from labels, it looks like a router pointing at nothing —
    the panel would report a missing service it had never looked for.
    """
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        return [], {}, {}
    if not isinstance(data, dict):
        return [], {}, {}
    http = data.get("http") or {}
    if not isinstance(http, dict):
        return [], {}, {}

    raw_routers = http.get("routers") or {}
    if not isinstance(raw_routers, dict):
        raw_routers = {}
    raw_middlewares = http.get("middlewares") or {}
    if not isinstance(raw_middlewares, dict):
        raw_middlewares = {}

    routers: list[TraefikRouter] = []
    for name, spec in sorted(raw_routers.items()):
        if not isinstance(spec, dict):
            continue
        # The file provider accepts either spelling of the key.
        entrypoints = spec.get("entrypoints", spec.get("entryPoints"))
        routers.append(
            TraefikRouter(
                name=str(name),
                entrypoints=_as_list(entrypoints),
                rule=spec.get("rule"),
                middlewares=_as_list(spec.get("middlewares")),
                service=spec.get("service"),
                tls=str(spec.get("tls", "")).lower() == "true",
                source="file",
                origin=origin,
            )
        )

    middlewares: dict[str, TraefikMiddleware] = {}
    for name, spec in sorted(raw_middlewares.items()):
        kind = next(iter(spec), None) if isinstance(spec, dict) else None
        middlewares[str(name)] = TraefikMiddleware(name=str(name), kind=kind)

    raw_services = http.get("services") or {}
    if not isinstance(raw_services, dict):
        raw_services = {}
    services: dict[str, TraefikServiceRef] = {}
    for name, spec in sorted(raw_services.items()):
        # No docker_service: this one is not backed by Swarm, and claiming a
        # name that will never match is how it came to read "no such service".
        services[str(name)] = TraefikServiceRef(
            name=str(name), source="file", upstreams=_upstreams(spec)
        )

    return routers, middlewares, services


def parse_api_rawdata(payload: dict) -> set[str]:
    """Router names Traefik did not report as rejected, from /api/rawdata.

    Names carry a provider suffix there (kafbat-ui@swarm); the labels do not,
    so it is stripped for comparison.

    A name is left out only when Traefik positively reported a status that is
    not ``enabled`` — the one case where the caller may say the router was
    rejected. A spec in a shape this parser cannot read (no ``status`` key, or
    not a mapping at all) says nothing: "Traefik reported no status" is not
    observable, and turning it into a rejection would put a measured-failure
    marker on a router nothing was measured about. Both odd shapes therefore
    stay in the set.
    """
    routers = (payload or {}).get("routers") if isinstance(payload, dict) else None
    if not isinstance(routers, dict):
        return set()
    accepted: set[str] = set()
    for name, spec in routers.items():
        if isinstance(spec, dict) and "status" in spec and spec["status"] != "enabled":
            continue
        accepted.add(str(name).split("@", 1)[0])
    return accepted
