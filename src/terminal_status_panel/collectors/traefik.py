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
import ssl
from contextlib import contextmanager

import httpx2
import yaml

from ..model import TraefikInfo, TraefikRouter
from ._labels import SWARM_SERVICE_LABEL, compose_identity, container_labels
from .traefik_parse import (
    parse_api_rawdata,
    parse_dynamic_yaml,
    parse_entrypoints,
    parse_labels,
    parse_ping_entrypoint,
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


def _mounted_config_names(service) -> set[str]:
    """The Docker configs this service actually mounts.

    Swarm keeps every generation of a config — ``traefik_dynamic_yml_v1``
    through ``_v4`` may all exist — and only the ones named in the
    service spec are the ones Traefik reads. Selecting by name prefix instead
    parses the superseded generations too, which is how the panel came to show
    ``ping-router`` four times per entrypoint and to invent orphans out of
    entrypoints that were removed two revisions ago.
    """
    task_template = _mapping(_spec_of(service).get("TaskTemplate"))
    container = _mapping(task_template.get("ContainerSpec"))
    refs = container.get("Configs")
    if not isinstance(refs, list):
        return set()
    names = set()
    for ref in refs:
        name = _mapping(ref).get("ConfigName")
        if isinstance(name, str) and name:
            names.add(name)
    return names


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


def _combined_listing_error(service_error: str | None, container_error: str | None) -> str:
    """One line for the state where nothing at all could be read.

    Reached only when both the Swarm services and the container listing
    failed. Either one failing alone is tolerated and recorded on its own
    field -- a Swarm services listing failing by itself is what a Compose-only
    host with no swarm manager returns on every call, not a sign the daemon is
    unreachable.
    """
    return f"services: {service_error}; containers: {container_error}"


@contextmanager
def _socket_timeout(client, timeout: float):
    """Bound this collector's own Docker calls, then restore the client's own.

    docker-py has no per-call timeout: the client's socket timeout bounds every
    request. The client handed here is the shared one, carrying the health
    section's larger timeout, and both calls below run unbudgeted on the main
    thread — which is precisely what ``docker.timeout`` is documented to keep
    off the login path.

    **This mutates a client other code may hold.** It is safe only because
    ``collect_all`` calls ``_docker_client`` twice and hands the health section
    a *different* ``APIClient`` — so the health probes' daemon threads never
    see this attribute move under them. That is a property of the current
    structure, not a guarantee of this function: merging the two clients into
    one would turn this into a race, in which the health probes could run
    against the traefik timeout or the reverse. Anyone merging them must give
    this collector its own client, or drop the mutation and accept the shared
    timeout.
    """
    api = getattr(client, "api", None)
    # Nothing to bound when the client carries no socket timeout of its own,
    # which is also what keeps this working for a stand-in that is not a real
    # docker-py client.
    previous = getattr(api, "timeout", _MISSING) if api is not None else _MISSING
    restore = api is not None and previous is not _MISSING
    if api is not None and restore:
        try:
            api.timeout = timeout
        except Exception:
            restore = False
    try:
        yield
    finally:
        if api is not None and restore:
            try:
                api.timeout = previous
            except Exception:  # noqa: S110
                # Restoring a timeout on a client that is already unusable
                # changes nothing worth reporting.
                pass


def _absorb_swarm_services(client, info: TraefikInfo) -> set[str] | None:
    """List Swarm services, take their wiring, and report the mounted configs.

    Returns the set of config generations the Traefik service actually mounts,
    or ``None`` when that could not be determined -- either because the listing
    failed or because no service matched ``TRAEFIK_SERVICE_PATTERNS``. The
    caller treats both the same way, and deliberately: in neither case is there
    a way to tell a live config generation from a superseded one.
    """
    try:
        services = client.services.list()
    except Exception as exc:
        # A Swarm services listing failing on its own is not a dead daemon --
        # it is exactly what a Compose-only host with no swarm manager to ask
        # returns on every call. Degrade like the container pass does; only the
        # two failing together means genuinely nothing could be read.
        info.service_error = f"{type(exc).__name__}: {exc}"
        return None

    info.reachable = True
    mounted: set[str] | None = None
    for service in services:
        name = getattr(service, "name", "") or ""
        if any(pattern in name for pattern in TRAEFIK_SERVICE_PATTERNS):
            args = _args_of(service)
            info.entrypoints = parse_entrypoints(args)
            info.ping_entrypoint = parse_ping_entrypoint(args)
            mounted = _mounted_config_names(service)
        _absorb(info, parse_labels(_labels_of(service), origin=name))
    return mounted


def _list_containers(client, info: TraefikInfo) -> list:
    """List plain containers, or record why they could not be listed."""
    try:
        containers = client.containers.list()
    except Exception as exc:
        # Compose and plain containers are an addition, not a precondition: a
        # daemon that answered for services but not for containers still yields
        # real wiring, so degrade rather than discard what was already read.
        info.container_error = f"{type(exc).__name__}: {exc}"
        return []
    info.reachable = True
    return containers


def _absorb_containers(info: TraefikInfo, containers: list) -> None:
    """Take the wiring declared by containers that are not Swarm tasks."""
    for container in containers:
        labels = container_labels(container)
        if SWARM_SERVICE_LABEL in labels:
            continue
        # NOT sparse-safe, unlike `container_labels` right above it.
        # `containers.list()` inspects, so `.name` is a real string here.
        # Under `containers.list(sparse=True)` -- which `ContainerIndex` in
        # clusters.py uses -- docker-py's `Container.name` reads `Name`, which
        # the list API does not send, and the attribute is `None`: this would
        # then silently yield an empty origin *and* an empty target, with no
        # error and no failing test -- the sparse-shape test in
        # tests/test_collectors_traefik.py passes only because its fake sets
        # `.name`, which a real sparse object does not. Anyone switching this
        # collector to sparse must read the name through
        # `clusters.container_name`, which looks at the list response's
        # `Names` array as well.
        name = getattr(container, "name", "") or ""
        # origin (who declared this router) and docker_name (what
        # ServiceStatus.name calls it) differ for a Compose container: the
        # container is named "course-statistics-db", the service it matches
        # against is "db". compose_identity computes the same name
        # collectors/docker.py gives that container's ServiceStatus, the
        # project-label condition included, so the two stay in step without a
        # second, drifting copy of that rule here.
        _absorb(info, parse_labels(labels, origin=name, docker_name=compose_identity(labels, name)))


def _list_configs(client, info: TraefikInfo) -> list:
    """List Swarm configs, or record why the file provider could not be read."""
    try:
        return client.configs.list()
    except Exception as exc:
        # The file provider is optional, but a read failure is not the same as
        # "no dynamic config exists" — the caller must be able to tell them
        # apart, since api@internal and ping-router live only there.
        info.file_provider_error = f"{type(exc).__name__}: {exc}"
        return []


def _live_configs(info: TraefikInfo, configs: list, mounted: set[str] | None) -> list:
    """The config generations Traefik actually mounts, or none at all.

    Without the Traefik service there is no way to tell a live generation from
    a superseded one, and guessing by name would put routers on screen that
    Traefik has not read since two deploys ago. An unreadable file provider is
    the honest report: the warning names the reason, and the routers are
    visibly missing rather than wrong.
    """
    if mounted is None:
        if configs:
            _note_file_provider_error(
                info,
                "traefik service not found, so which config generations are"
                " mounted cannot be determined",
            )
        return []
    return [
        config
        for config in configs
        if DYNAMIC_CONFIG_PREFIX in (getattr(config, "name", "") or "")
        # A superseded generation. Not an error and not worth a warning --
        # Swarm keeping the old ones is normal.
        and (getattr(config, "name", "") or "") in mounted
    ]


def _absorb_config(info: TraefikInfo, config) -> None:
    """Take one dynamic config's wiring, or say why it yielded none."""
    name = getattr(config, "name", "") or ""
    text = _config_text(config)
    if text is None:
        # Same reasoning as the failed listing, one level down: a config nobody
        # could decode yields no router, which would read as "no such router"
        # instead of "not read".
        _note_file_provider_error(info, f"{name}: config data is not decodable")
        return
    if not text.strip():
        # An empty body decodes cleanly and parses cleanly to nothing, so
        # neither the guard above nor `_yaml_error` below sees it -- but a
        # dynamic config with no content is a read that came back empty, not a
        # file provider that declares no routers.
        _note_file_provider_error(info, f"{name}: config data is empty")
        return
    routers, middlewares, refs = parse_dynamic_yaml(text, origin=name)
    if not routers and not middlewares and not refs:
        error = _yaml_error(text)
        if error is not None:
            _note_file_provider_error(info, f"{name}: {error}")
    info.routers.extend(routers)
    info.middlewares.update(middlewares)
    # Labels win: a Swarm service that also carries a file-provider entry of
    # the same name is the one that can actually be measured.
    for ref_name, ref in refs.items():
        info.services.setdefault(ref_name, ref)


def _absorb(info: TraefikInfo, parsed) -> None:
    """Merge one parser's routers, middlewares and service references."""
    routers, middlewares, refs = parsed
    info.routers.extend(routers)
    info.middlewares.update(middlewares)
    info.services.update(refs)


def collect_traefik(client, timeout: float = 5.0) -> TraefikInfo:
    """The wiring as configured, within ``timeout`` per Docker call.

    Three sources, in the order their answers depend on each other: the Swarm
    services (which also say which config generations are live), the plain
    containers, and the file provider's configs. Each degrades on its own; only
    both listings failing together means nothing could be read at all.

    Never raises.
    """
    info = TraefikInfo()
    with _socket_timeout(client, timeout):
        mounted = _absorb_swarm_services(client, info)
        containers = _list_containers(client, info)
        if not info.reachable:
            # Both listings failed: there is no wiring left to show, unlike
            # either one failing alone.
            info.error = _combined_listing_error(info.service_error, info.container_error)
            return info
        _absorb_containers(info, containers)
        configs = _list_configs(client, info)

    for config in _live_configs(info, configs, mounted):
        _absorb_config(info, config)

    info.routers.sort(key=lambda r: (r.source != "swarm", r.name))
    return info


def mark_rejected(info: TraefikInfo, accepted: set[str]) -> None:
    """Flag routers Traefik never accepted. Only call after really asking it.

    ``accepted`` is what ``parse_api_rawdata`` returns: the routers Traefik
    did *not* report as rejected, which includes the ones it reported in a
    shape the parser could not read. Anything outside it is marked rejected,
    so a name may only be left out on a status that was positively read.
    """
    info.api_consulted = True
    for router in info.routers:
        router.rejected = router.name not in accepted


def _is_readable_rawdata(payload) -> bool:
    """Whether ``payload`` is a /api/rawdata answer this code can read.

    ``parse_api_rawdata`` never raises, so an unreadable payload comes back
    from it as an empty set — and an empty set is a *statement*: fed to
    ``mark_rejected`` it marks every router rejected. The difference between
    "Traefik holds no routers" and "we could not read the answer" has to be
    decided here, where the ``set[str] | None`` return type still has a way
    to say the second one.
    """
    return isinstance(payload, dict) and isinstance(payload.get("routers"), dict)


def _ssl_context(api) -> ssl.SSLContext:
    """The configured client certificate, as a context httpx2 can use.

    Neither httpx (since 0.28) nor httpx2 takes ``cert=`` at the top level, so
    the material is loaded into an ``SSLContext`` instead. ``load_cert_chain``
    takes the key separately or reads it from the certificate file when it is
    bundled there, which mirrors what the configuration allows.

    Building the context here also opts out of httpx2's default, which is
    ``truststore`` over the system trust store. That default is the better one
    for a plain request, but a client certificate has to be loaded into a
    context either way, and ``api.ca`` is the knob that says which roots to
    trust for this endpoint.
    """
    context = ssl.create_default_context(cafile=api.ca) if api.ca else ssl.create_default_context()
    context.load_cert_chain(api.cert, api.key)
    return context


def fetch_accepted(cfg, *, client: httpx2.Client | None = None) -> set[str] | None:
    """Ask Traefik what it accepted, or None when that could not be learned.

    ``None`` covers every way of not learning it: not configured, unreachable,
    an error response, and a 200 whose body could not be read. Only a payload
    in the expected shape produces a set — an empty one included, since
    "Traefik holds no routers" is a real answer.

    ``client`` is a private testing seam: pass an ``httpx2.Client`` built on a
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
            response = httpx2.get(api.url, verify=_ssl_context(api), timeout=5.0)
        response.raise_for_status()
        payload = response.json()
        if not _is_readable_rawdata(payload):
            return None
        return parse_api_rawdata(payload)
    except Exception:
        # Unreachable is not the same as "rejected everything": leave the
        # routers unconsulted rather than marking them all rejected.
        return None
