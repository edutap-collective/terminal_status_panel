"""Read Traefik's wiring from the Docker API.

Everything the dashboard shows is derivable here: the entrypoints from the
Traefik service's own arguments, the routers and services from the labels of
every Swarm service, and the file-provider routers from the mounted Docker
configs. No client certificate, no change to the Traefik deployment.

What this cannot see is Traefik's runtime opinion — a rule that failed to
parse still appears here as configured. The optional API path answers that.
"""

from __future__ import annotations

import base64

from ..model import TraefikInfo, TraefikRouter
from .traefik_parse import (
    parse_api_rawdata,
    parse_dynamic_yaml,
    parse_entrypoints,
    parse_labels,
)

TRAEFIK_SERVICE_PATTERNS = ("traefik_traefik",)
DYNAMIC_CONFIG_PREFIX = "traefik_dynamic"


def unknown_entrypoints(router: TraefikRouter, known: set[str]) -> list[str]:
    """Entrypoints a router names that do not exist.

    A router with no entrypoint named is attached to all of them by Traefik,
    so it is never an orphan.
    """
    return [name for name in router.entrypoints if name not in known]


def _args_of(service) -> list[str]:
    spec = (getattr(service, "attrs", {}) or {}).get("Spec", {})
    container = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
    return list(container.get("Args") or [])


def _labels_of(service) -> dict:
    return dict(((getattr(service, "attrs", {}) or {}).get("Spec", {})).get("Labels") or {})


def _config_text(config) -> str:
    data = ((getattr(config, "attrs", {}) or {}).get("Spec", {})).get("Data") or ""
    try:
        return base64.b64decode(data).decode("utf-8", "replace")
    except Exception:
        return ""


def collect_traefik(client, timeout: float = 5.0) -> TraefikInfo:
    """The wiring as configured. Never raises."""
    info = TraefikInfo()
    try:
        services = client.services.list()
    except Exception as exc:
        return TraefikInfo(error=f"{type(exc).__name__}: {exc}")
    info.reachable = True

    for service in services:
        name = getattr(service, "name", "") or ""
        if any(pattern in name for pattern in TRAEFIK_SERVICE_PATTERNS):
            info.entrypoints = parse_entrypoints(_args_of(service))
        routers, middlewares, refs = parse_labels(_labels_of(service), origin=name)
        info.routers.extend(routers)
        info.middlewares.update(middlewares)
        info.services.update(refs)

    try:
        configs = client.configs.list()
    except Exception as exc:
        # The file provider is optional, but a read failure is not the same
        # as "no dynamic config exists" — the caller must be able to tell
        # them apart, since api@internal and ping-router live only there.
        info.file_provider_error = f"{type(exc).__name__}: {exc}"
        configs = []
    for config in configs:
        name = getattr(config, "name", "") or ""
        if DYNAMIC_CONFIG_PREFIX not in name:
            continue
        routers, middlewares = parse_dynamic_yaml(_config_text(config), origin=name)
        info.routers.extend(routers)
        info.middlewares.update(middlewares)

    info.routers.sort(key=lambda r: (r.source != "swarm", r.name))
    return info


def mark_rejected(info: TraefikInfo, accepted: set[str]) -> None:
    """Flag routers Traefik never accepted. Only call after really asking it."""
    info.api_consulted = True
    for router in info.routers:
        router.rejected = router.name not in accepted


def fetch_accepted(cfg) -> set[str] | None:
    """Ask Traefik what it accepted, or None when not configured or reachable."""
    api = getattr(cfg, "traefik", None)
    if not api or not api.url or not api.cert:
        return None
    try:
        import httpx

        response = httpx.get(
            api.url,
            cert=(api.cert, api.key) if api.key else api.cert,
            verify=api.ca or True,
            timeout=5.0,
        )
        response.raise_for_status()
        return parse_api_rawdata(response.json())
    except Exception:
        # Unreachable is not the same as "rejected everything": leave the
        # routers unconsulted rather than marking them all rejected.
        return None
