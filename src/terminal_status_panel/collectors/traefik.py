"""Read Traefik's wiring from the Docker API.

Everything the dashboard shows is derivable here: the entrypoints from
Traefik's static configuration -- a file, its flags or its environment,
whichever Traefik itself reads -- the routers and services from the labels of
every Swarm service and container, and the file-provider routers from the
files its file provider reads, Docker configs and bind-mounted host files
alike. No client certificate, no change to the Traefik deployment.

What this cannot see is Traefik's runtime opinion — a rule that failed to
parse still appears here as configured. The optional API path answers that.
"""

from __future__ import annotations

import base64
import posixpath
import ssl
from contextlib import contextmanager
from typing import Any

import httpx2

from ..config import DEFAULT_TRAEFIK_MATCH
from ..model import TraefikInfo, TraefikRouter
from ._labels import SWARM_SERVICE_LABEL, compose_identity, container_labels
from .traefik_mounts import (
    KnownPlacement,
    Located,
    Placement,
    Unreadable,
    Workload,
    format_of,
    locate,
    presence,
    provider_files,
    read_located,
    workload_from_container,
    workload_from_service,
)
from .traefik_parse import (
    is_templated,
    parse_api_rawdata,
    parse_dynamic,
    parse_error,
    parse_labels,
)
from .traefik_static import (
    FileProvider,
    StaticConfig,
    candidate_paths,
    config_file_arg,
    has_static_env,
    ignored_flags,
    parse_static_args,
    parse_static_document,
    parse_static_env,
)

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


def _labels_of(service) -> dict:
    return _mapping(_spec_of(service).get("Labels"))


def _config_text(config) -> str | None:
    """The decoded config body, or ``None`` when it could not be decoded."""
    data = _spec_of(config).get("Data") or ""
    try:
        return base64.b64decode(data).decode("utf-8", "replace")
    except Exception:
        return None


def _note_file_provider_error(info: TraefikInfo, message: str) -> None:
    """Record a file-provider failure: the field shows the first, and how many more.

    The field is one line in the panel, so it cannot hold them all; counting
    the rest keeps a later failure from vanishing without a trace. Every note
    is kept in ``file_provider_notes``.
    """
    info.file_provider_notes.append(message)
    first = info.file_provider_notes[0]
    more = len(info.file_provider_notes) - 1
    info.file_provider_error = first if more == 0 else f"{first} (+{more} more)"


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


def _matches(name: str, match: tuple[str, ...]) -> bool:
    """Whether *name* contains one of *match* -- case-insensitively, as documented."""
    folded = name.casefold()
    return any(pattern.casefold() in folded for pattern in match)


def _absorb_swarm_services(client, info: TraefikInfo, match: tuple[str, ...]) -> Any:
    """List Swarm services, take their wiring, and return the Traefik service.

    ``None`` when the listing failed or no service matched *match*. Last match
    wins, as it did before. The service is returned as docker-py handed it:
    turning it into a ``Workload`` is part of reading Traefik's own
    configuration, which the caller guards.
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
    traefik = None
    for service in services:
        name = getattr(service, "name", "") or ""
        if _matches(name, match):
            traefik = service
        _absorb(info, parse_labels(_labels_of(service), origin=name))
    return traefik


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


def _traefik_container(containers: list, match: tuple[str, ...]) -> Any:
    """A plain (non-Swarm) Traefik container, when no Swarm service matched."""
    for container in containers:
        if SWARM_SERVICE_LABEL in container_labels(container):
            continue
        if _matches(getattr(container, "name", "") or "", match):
            return container
    return None


class _SwarmPlacement:
    """Where the Traefik service's tasks run -- asked once, and only if needed.

    A bind mount is the only thing that needs it, so a deployment without one
    pays no extra Docker call.
    """

    def __init__(self, client, workload: Workload) -> None:
        self._client = client
        self._workload = workload
        self._answer: tuple[bool, str | None] | None = None

    def _ask(self) -> tuple[bool, str | None]:
        if self._answer is None:
            self._answer = _runs_here(self._client, self._workload)
        return self._answer

    @property
    def here(self) -> bool:
        """Whether a running Traefik task is on this node."""
        return self._ask()[0]

    @property
    def node(self) -> str | None:
        """The node a Traefik task runs on, when it is not this one."""
        return self._ask()[1]


def _node_name(client, node_id: str) -> str:
    """The node's hostname where a manager can say, its ID otherwise."""
    try:
        return client.nodes.get(node_id).attrs["Description"]["Hostname"]
    except Exception:
        return node_id


def _runs_here(client, workload: Workload) -> tuple[bool, str | None]:
    """Whether a running task of *workload* is on this node, and where it is if not.

    Anything that cannot be asked, or answers in a shape that cannot be read,
    counts as "not here": a bind mount is then reported as unreadable rather
    than read from a host that may not be the one Traefik runs on.
    """
    if workload.service is None:
        # A plain container runs on the daemon that listed it.
        return True, None
    try:
        node_id = ((client.info() or {}).get("Swarm") or {}).get("NodeID")
        tasks = workload.service.tasks(filters={"desired-state": "running"})
        nodes = [
            task.get("NodeID") for task in tasks if isinstance(task, dict) and task.get("NodeID")
        ]
    except Exception:
        return False, None
    if node_id and node_id in nodes:
        return True, None
    return False, (_node_name(client, nodes[0]) if nodes else None)


def _unchecked_names(paths: list[str], index: int, target: str, explicit: bool) -> str:
    """The file names an undecidable check leaves open, relative to the mount *target*.

    For ``--configFile``, its one name. For a default location, every
    extension Traefik would still try there: ``traefik.toml/.yaml/.yml``.
    """
    path = paths[index]
    name = posixpath.relpath(path, posixpath.normpath(target))
    if name == posixpath.curdir:
        name = posixpath.basename(path)
    if explicit:
        return name
    stem = posixpath.splitext(path)[0]
    extensions = dict.fromkeys(
        posixpath.splitext(later)[1]
        for later in paths[index:]
        if posixpath.splitext(later)[0] == stem
    )
    return posixpath.splitext(name)[0] + "/".join(extensions)


def _find_static_file(workload: Workload, placement: Placement) -> Located | str | None:
    """The file Traefik reads its static configuration from, searched as Traefik searches.

    Returns the located file; a sentence saying why the search cannot be
    decided from here; or ``None`` when there is no file, and the flags or
    the environment apply. A candidate that is not there is passed over, as
    Traefik passes over it -- a missing ``--configFile`` included. One that no
    mount provides counts as not there, except ``--configFile``: that one may
    live in the image, so it cannot be decided either way.
    """
    explicit = config_file_arg(workload.args)
    explicit_index = 0 if explicit else -1
    paths = candidate_paths(workload.args, workload.env, workload.workdir)
    if explicit and not posixpath.isabs(paths[0]):
        return (
            f"--configFile={explicit} is relative and the container's working directory"
            " is not declared — not read"
        )
    for index, path in enumerate(paths):
        located = locate(workload, path)
        mount = located.mount
        if mount is None:
            if index == explicit_index:
                return (
                    f"--configFile={explicit} is not mounted — it may be part of the image,"
                    " or absent (then Traefik falls back to its default locations and flags)"
                )
            continue
        found = presence(located, placement=placement)
        if found.state == "present":
            return located
        if found.state == "unknown":
            names = _unchecked_names(paths, index, mount.target, index == explicit_index)
            return f"{found.reason} — whether it holds {names} cannot be checked from here"
    return None


def _read_static_file(
    info: TraefikInfo,
    workload: Workload,
    located: Located,
    configs: dict[str, str | None],
    placement: Placement,
) -> StaticConfig:
    """The static file Traefik reads, or the reason it cannot be read here.

    Never the flags instead: Traefik reads the file, and the flags would show
    a configuration it ignores.
    """
    path = located.path
    try:
        text = read_located(located, configs=configs, placement=placement)
        static = parse_static_document(text, format_of(path) or "yaml")
    except Unreadable as exc:
        info.static_problem = f"entrypoints are configured in {path}, {exc}"
        return StaticConfig()
    except ValueError as exc:
        info.static_problem = f"{path}: {exc}"
        return StaticConfig()
    info.static_source = path
    ignored = ignored_flags(workload.args)
    if ignored:
        info.static_notes.append(
            f"static configuration from {path}; Traefik ignores {ignored} other"
            f" command-line flag{'' if ignored == 1 else 's'}"
        )
    if not static.entrypoints:
        info.static_problem = f"{path} declares no entrypoints"
    return static


def _read_static(
    info: TraefikInfo, workload: Workload, configs: dict[str, str | None], placement: Placement
) -> StaticConfig:
    """The static configuration, from the source Traefik itself would use.

    The first file found, else the flags, else the ``TRAEFIK_`` variables. A
    search that cannot be decided draws nothing: the flags would show a
    configuration Traefik may be ignoring.
    """
    found = _find_static_file(workload, placement)
    if isinstance(found, str):
        info.static_problem = found
        return StaticConfig()
    if found is not None:
        return _read_static_file(info, workload, found, configs, placement)
    if workload.args:
        info.static_source = "command-line flags"
        return parse_static_args(workload.args)
    if has_static_env(workload.env):
        info.static_source = "environment"
        return parse_static_env(workload.env)
    info.static_problem = "no static configuration found — no file, no flags, no TRAEFIK_ variables"
    return StaticConfig()


def _anchored_provider(
    info: TraefikInfo, workload: Workload, provider: FileProvider
) -> FileProvider | None:
    """*provider* with relative paths resolved as Traefik resolves them, or ``None``.

    Traefik opens a relative path against its own working directory. That is
    known only where the workload declares one: an image's ``WORKDIR`` is not
    visible from the Docker API, and guessing it would read a directory
    Traefik may never look at. ``None`` -- with the reason noted -- is the
    honest answer then, and nothing is read from the file provider at all.
    """
    path = provider.path or ""
    if posixpath.isabs(path):
        return provider
    workdir = workload.workdir
    if not workdir:
        _note_file_provider_error(
            info,
            f"{path} is relative and the container's working directory is not declared — not read",
        )
        return None

    def anchor(value: str | None) -> str | None:
        if value is None or posixpath.isabs(value):
            return value
        return posixpath.join(workdir, value)

    return FileProvider(directory=anchor(provider.directory), filename=anchor(provider.filename))


def _list_configs(client, info: TraefikInfo) -> list:
    """List Swarm configs, or record why the file provider could not be read."""
    try:
        return client.configs.list()
    except Exception as exc:
        # The file provider is optional, but a read failure is not the same as
        # "no dynamic config exists" — the caller must be able to tell them
        # apart, since api@internal and ping-router live only there.
        _note_file_provider_error(info, f"{type(exc).__name__}: {exc}")
        return []


def _decoded_configs(configs: list) -> dict[str, str | None]:
    """Every config's decoded body by name, ``None`` for one that could not be decoded."""
    return {getattr(config, "name", "") or "": _config_text(config) for config in configs}


def _live_configs(info: TraefikInfo, configs: list, mounted: set[str] | None) -> list:
    """The config generations Traefik actually mounts, or none at all.

    The rule for a Traefik whose static configuration names no file-provider
    path. Swarm keeps every generation of a config --
    ``traefik_dynamic_yml_v1`` through ``_v4`` may all exist -- and only the
    ones named in the service spec (*mounted*) are the ones Traefik reads.
    Selecting by name prefix alone parses the superseded generations too,
    which is how the panel came to show ``ping-router`` four times per
    entrypoint and to invent orphans out of entrypoints that were removed two
    revisions ago.

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


def _absorb_text(info: TraefikInfo, text: str, origin: str, fmt: str) -> None:
    """Take one dynamic file's wiring, or say why it yielded none."""
    if not text.strip():
        # An empty body decodes cleanly and parses cleanly to nothing, so
        # neither the decode guard nor `parse_error` below sees it -- but a
        # dynamic config with no content is a read that came back empty, not a
        # file provider that declares no routers.
        _note_file_provider_error(info, f"{origin}: config data is empty")
        return
    routers, middlewares, refs = parse_dynamic(text, origin=origin, fmt=fmt)
    if not routers and not middlewares and not refs:
        error = parse_error(text, fmt)
        if error is not None:
            _note_file_provider_error(info, f"{origin}: {error}")
    info.routers.extend(routers)
    info.middlewares.update(middlewares)
    # Labels win: a Swarm service that also carries a file-provider entry of
    # the same name is the one that can actually be measured.
    for ref_name, ref in refs.items():
        info.services.setdefault(ref_name, ref)


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
    if is_templated(text):
        # Traefik runs every dynamic file through Go's text/template; the panel
        # cannot, here as on the provider-path branch.
        _note_file_provider_error(info, f"{name}: templated — not evaluated")
        return
    _absorb_text(info, text, name, "yaml")


def _absorb_located(
    info: TraefikInfo, located: Located, configs: dict[str, str | None], placement: Placement
) -> None:
    """Take the wiring of one file the provider path selects, config or host file."""
    mount = located.mount
    is_config = mount is not None and mount.kind == "config"
    # A config keeps its name as the origin, as it always has; a host file is
    # named by its host path, which is where somebody would go to edit it.
    if mount is not None and is_config:
        origin = mount.source
    else:
        origin = located.host_path or located.path
    try:
        text = read_located(located, configs=configs, placement=placement)
    except Unreadable as exc:
        # A config's reason already names the config, in the words 0.12.2
        # used; any other mount is named by its path in the container.
        _note_file_provider_error(info, str(exc) if is_config else f"{located.path}: {exc}")
        return
    if is_templated(text):
        _note_file_provider_error(info, f"{origin}: templated — not evaluated")
        return
    _absorb_text(info, text, origin, format_of(located.path) or "yaml")


def _absorb_generations(info: TraefikInfo, configs: list, mounted: set[str] | None) -> None:
    """Take the wiring of the live config generations (see ``_live_configs``)."""
    for config in _live_configs(info, configs, mounted):
        _absorb_config(info, config)


def _absorb_file_provider(
    info: TraefikInfo,
    workload: Workload,
    provider: FileProvider,
    configs: dict[str, str | None],
    placement: Placement,
) -> None:
    """Take the wiring of every file Traefik's file provider reads, by its path."""
    anchored = _anchored_provider(info, workload, provider)
    if anchored is None:
        return
    listing = provider_files(workload, anchored, placement=placement)
    for note in listing.notes:
        _note_file_provider_error(info, note)
    for located in listing.files:
        _absorb_located(info, located, configs, placement)


def _absorb_workload(client, info: TraefikInfo, workload: Workload, configs: list) -> None:
    """The static configuration and the file provider, both as Traefik reads them."""
    decoded = _decoded_configs(configs)
    placement: Placement = (
        _SwarmPlacement(client, workload)
        if workload.service is not None
        else KnownPlacement(here=True)
    )
    static = _read_static(info, workload, decoded, placement)
    info.entrypoints = static.entrypoints
    info.ping_entrypoint = static.ping_entrypoint

    provider = static.file_provider
    if provider is not None and provider.path:
        _absorb_file_provider(info, workload, provider, decoded, placement)
        return
    # No provider path: the config-generation rule of 0.12.2. A plain
    # container mounts no Swarm config, so nothing qualifies for it.
    mounted = (
        {mount.source for mount in workload.mounts if mount.kind == "config"}
        if workload.service is not None
        else set()
    )
    _absorb_generations(info, configs, mounted)


def _absorb_traefik(client, info: TraefikInfo, service: Any, container: Any, configs: list) -> None:
    """Read Traefik's own configuration; a failure nobody foresaw is a problem, not a crash.

    Everything below is written not to raise. This guard is for the shape
    nobody thought of: it keeps the promise that the collector never raises,
    and says what happened instead of drawing half a tree.
    """
    try:
        if service is not None:
            workload = workload_from_service(service)
        else:
            workload = workload_from_container(container)
        _absorb_workload(client, info, workload, configs)
    except Exception as exc:
        info.entrypoints = []
        info.ping_entrypoint = None
        info.static_problem = (
            f"Traefik's configuration could not be read: {type(exc).__name__}: {exc}"
        )


def _absorb(info: TraefikInfo, parsed) -> None:
    """Merge one parser's routers, middlewares and service references."""
    routers, middlewares, refs = parsed
    info.routers.extend(routers)
    info.middlewares.update(middlewares)
    info.services.update(refs)


def _swarm_active(client) -> bool:
    """Whether this daemon is part of an active Swarm -- ``False`` when that cannot be asked."""
    try:
        swarm = (client.info() or {}).get("Swarm") or {}
        return swarm.get("LocalNodeState") == "active"
    except Exception:
        return False


def collect_traefik(
    client, timeout: float = 5.0, match: tuple[str, ...] = DEFAULT_TRAEFIK_MATCH
) -> TraefikInfo:
    """The wiring as configured, within ``timeout`` per Docker call.

    Three sources, in the order their answers depend on each other: the Swarm
    services (which also say where Traefik runs and what it mounts), the plain
    containers, and the file provider's files. Each degrades on its own; only
    both listings failing together means nothing could be read at all.

    The Traefik workload is found by *match*: a Swarm service first, a plain
    container otherwise. Its static configuration is read from the source
    Traefik itself would use, and the file provider by the path that
    configuration names.

    An empty *match* states that there is no Traefik to read on this host:
    nothing is asked of Docker at all.

    Never raises.
    """
    info = TraefikInfo()
    if not match:
        return info
    with _socket_timeout(client, timeout):
        service = _absorb_swarm_services(client, info, match)
        containers = _list_containers(client, info)
        if not info.reachable:
            # Both listings failed: there is no wiring left to show, unlike
            # either one failing alone.
            info.error = _combined_listing_error(info.service_error, info.container_error)
            return info
        _absorb_containers(info, containers)
        container = _traefik_container(containers, match) if service is None else None
        # Swarm configs exist only on a Swarm daemon. When the services listing
        # failed, the daemon is asked once whether Swarm is active: a worker --
        # which can never list services -- or a manager whose listing failed
        # lists configs exactly as 0.12.2 did, and a failure there is reported
        # as it always was. Where Swarm is not active, or that cannot be asked,
        # configs are not asked for: the "not a swarm manager" answer would
        # blame the file provider for a Compose-only host.
        list_configs = info.service_error is None or _swarm_active(client)
        configs = _list_configs(client, info) if list_configs else []
        # The file reads happen inside the timeout too: where Traefik runs is
        # a Docker call, and host files are bounded by the mount resolver's
        # own size and count limits.
        if service is None and container is None:
            info.static_problem = (
                f"no Traefik service or container matches traefik.match ({', '.join(match)})"
            )
            _absorb_generations(info, configs, None)
        else:
            _absorb_traefik(client, info, service, container, configs)

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
