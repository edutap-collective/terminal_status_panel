"""Traefik's wiring as configured: entrypoints, routers, middlewares, services.

Reconstructed from Docker labels and the file provider rather than read from
Traefik itself, so every field here describes what Traefik was *told*, not
necessarily what it accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TraefikEntrypoint:
    """A port Traefik listens on, as declared in its static configuration."""

    name: str
    address: str  # ":2020"
    port: int | None = None


@dataclass
class TraefikMiddleware:
    """One middleware attached to a router."""

    name: str
    kind: str | None = None  # stripprefix, headers, …
    detail: str | None = None  # the first configured key, for display


@dataclass
class TraefikServiceRef:
    """What a router points at — a Docker service, or one of Traefik's own."""

    name: str
    port: int | None = None
    scheme: str | None = None
    docker_service: str | None = None  # the Swarm service backing it, when known
    source: str = "swarm"  # swarm | file
    # Where a file-provider service sends traffic. Docker knows nothing about
    # these, so they are shown rather than measured.
    upstreams: list[str] = field(default_factory=list)


@dataclass
class TraefikRouter:
    """One Traefik router: what it matches, and where it forwards."""

    name: str
    entrypoints: list[str] = field(default_factory=list)
    rule: str | None = None
    middlewares: list[str] = field(default_factory=list)
    service: str | None = None
    tls: bool = False
    source: str = "swarm"  # swarm | file
    origin: str | None = None  # the Docker service, container, or config it was read from
    # None means the Traefik API was never asked. It must not render as
    # "accepted": not consulted is not the same as confirmed.
    rejected: bool | None = None


@dataclass
class TraefikInfo:
    """The Traefik wiring as configured: entrypoints, routers, services."""

    reachable: bool = False
    entrypoints: list[TraefikEntrypoint] = field(default_factory=list)
    routers: list[TraefikRouter] = field(default_factory=list)
    middlewares: dict[str, TraefikMiddleware] = field(default_factory=dict)
    services: dict[str, TraefikServiceRef] = field(default_factory=dict)
    api_consulted: bool = False
    # The entrypoint Traefik answers its own health check on. It carries no
    # router by design, which is the one case where "— no router" is not a
    # finding.
    ping_entrypoint: str | None = None
    # Set only when neither the Swarm services nor the container listing
    # could be read -- the one case with genuinely nothing to show. Either
    # one failing alone is a partial read, recorded below instead.
    error: str | None = None
    # A partial failure: the labels were read but the file provider was not.
    # Distinct from `error`, which means nothing could be read at all.
    file_provider_error: str | None = None
    # Labels were read from services but not from containers. Distinct from
    # `error`, which means nothing could be read at all.
    container_error: str | None = None
    # Labels were read from containers but not from Swarm services. Distinct
    # from `error`, which means nothing could be read at all. Expected and
    # permanent on a host with no Swarm manager to ask -- see how the
    # renderer decides whether this is worth a line.
    service_error: str | None = None
