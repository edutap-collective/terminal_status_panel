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
from contextlib import contextmanager

import httpx
import yaml

from ..model import TraefikInfo, TraefikRouter
from .traefik_parse import (
    parse_api_rawdata,
    parse_dynamic_yaml,
    parse_entrypoints,
    parse_labels,
)

TRAEFIK_SERVICE_PATTERNS = ("traefik_traefik",)
DYNAMIC_CONFIG_PREFIX = "traefik_dynamic"

_MISSING = object()


def unknown_entrypoints(router: TraefikRouter, known: set[str]) -> list[str]:
    """Entrypoints a router names that do not exist.

    A router with no entrypoint named is attached to all of them by Traefik,
    so it is never an orphan.
    """
    return [name for name in router.entrypoints if name not in known]


def _mapping(value) -> dict:
    """``value`` when it is a mapping, an empty one otherwise.

    ``attrs or {}`` only guards ``None``. A ``Spec`` that comes back as a list
    — the same shape that broke the Task 4 parser — would raise
    ``AttributeError`` out of this module, and ``main``'s blanket ``except``
    would then print no panel at all rather than a traceback.
    """
    return value if isinstance(value, dict) else {}


def _spec_of(obj) -> dict:
    return _mapping(_mapping(getattr(obj, "attrs", None)).get("Spec"))


def _args_of(service) -> list[str]:
    task_template = _mapping(_spec_of(service).get("TaskTemplate"))
    container = _mapping(task_template.get("ContainerSpec"))
    args = container.get("Args")
    return [str(arg) for arg in args] if isinstance(args, list) else []


def _labels_of(service) -> dict:
    return _mapping(_spec_of(service).get("Labels"))


def _config_text(config) -> str | None:
    """The decoded config body, or ``None`` when it could not be decoded."""
    data = _spec_of(config).get("Data") or ""
    try:
        return base64.b64decode(data).decode("utf-8", "replace")
    except Exception:
        return None


def _yaml_error(text: str) -> str | None:
    """Why this config parsed to nothing, when the reason is broken YAML.

    Only consulted for a config that yielded neither router nor middleware:
    valid YAML with no ``http`` section is a real, empty answer, and must not
    be reported as a read failure.
    """
    try:
        yaml.safe_load(text)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _note_file_provider_error(info: TraefikInfo, message: str) -> None:
    """Record the first read failure; the field holds one line, not a list."""
    if info.file_provider_error is None:
        info.file_provider_error = message


@contextmanager
def _socket_timeout(client, timeout: float):
    """Bound this collector's own Docker calls, then restore the client's own.

    docker-py has no per-call timeout: the client's socket timeout bounds every
    request. The client handed here is the shared one, carrying the health
    section's larger timeout, and both calls below run unbudgeted on the main
    thread — which is precisely what ``docker.timeout`` is documented to keep
    off the login path.
    """
    api = getattr(client, "api", None)
    previous = getattr(api, "timeout", _MISSING)
    if previous is not _MISSING:
        try:
            api.timeout = timeout
        except Exception:
            previous = _MISSING
    try:
        yield
    finally:
        if previous is not _MISSING:
            try:
                api.timeout = previous
            except Exception:
                pass


def collect_traefik(client, timeout: float = 5.0) -> TraefikInfo:
    """The wiring as configured, within ``timeout`` per Docker call.

    Never raises.
    """
    info = TraefikInfo()
    with _socket_timeout(client, timeout):
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
        text = _config_text(config)
        if text is None:
            # Same reasoning as the failed listing above, one level down: a
            # config nobody could decode yields no router, which would read
            # as "no such router" instead of "not read".
            _note_file_provider_error(info, f"{name}: config data is not decodable")
            continue
        routers, middlewares = parse_dynamic_yaml(text, origin=name)
        if not routers and not middlewares:
            error = _yaml_error(text)
            if error is not None:
                _note_file_provider_error(info, f"{name}: {error}")
        info.routers.extend(routers)
        info.middlewares.update(middlewares)

    info.routers.sort(key=lambda r: (r.source != "swarm", r.name))
    return info


def mark_rejected(info: TraefikInfo, accepted: set[str]) -> None:
    """Flag routers Traefik never accepted. Only call after really asking it."""
    info.api_consulted = True
    for router in info.routers:
        router.rejected = router.name not in accepted


def fetch_accepted(cfg, *, client: httpx.Client | None = None) -> set[str] | None:
    """Ask Traefik what it accepted, or None when not configured or reachable.

    ``client`` is a private testing seam: pass an ``httpx.Client`` built on a
    ``MockTransport`` to exercise this against a recorded response without a
    real socket. Production code never sets it — the default builds a plain
    request with the configured mTLS material.
    """
    api = getattr(cfg, "traefik", None)
    if not api or not api.url or not api.cert:
        return None
    try:
        if client is not None:
            response = client.get(api.url, timeout=5.0)
        else:
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
