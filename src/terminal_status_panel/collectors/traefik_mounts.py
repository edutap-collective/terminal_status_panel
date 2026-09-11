"""Where a path inside the Traefik container comes from, and whether it can be read here.

The Docker API describes Traefik's filesystem only through its mounts: Swarm
configs (readable through the API from any node), bind mounts (a host path,
readable only on the node the task runs on), and volumes (closed to the login
user). Anything else lives in image layers the panel cannot see.

Reading a bind mount reads a host file as the login user. The path comes from
the Docker spec, which anyone holding the Docker socket controls already, so
no privilege is added -- but the limits below keep a misconfigured spec from
making the panel read something large or something outside the mount.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from typing import Any, Protocol

from .traefik_static import FileProvider

MAX_FILE_BYTES = 1024 * 1024
MAX_PROVIDER_FILES = 64

#: The extensions Traefik's file provider reads, case-insensitively
#: (traefik/pkg/provider/file/file.go, v3.7.13).
_DYNAMIC_EXTENSIONS = {".toml": "toml", ".yaml": "yaml", ".yml": "yaml"}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Mount:
    """One mount of the Traefik container, as the panel needs it."""

    kind: str  # bind | volume | config | tmpfs | npipe | cluster
    target: str  # the path inside the container
    source: str  # host path, volume name, or config name


@dataclass
class Workload:
    """The Traefik container, from a Swarm service spec or a container inspect."""

    name: str
    args: list[str]
    env: dict[str, str]
    workdir: str | None
    mounts: list[Mount] = field(default_factory=list)
    #: The docker-py service, to ask where its tasks run. ``None`` for a plain
    #: container, which runs on this daemon by definition. ``Any`` because
    #: docker-py ships no types; every other collector takes its objects
    #: unannotated for the same reason.
    service: Any = None


def _env(raw: object) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in raw if isinstance(raw, list) else []:
        key, sep, value = str(entry).partition("=")
        if sep and key:
            env[key] = value
    return env


def _args(raw: object) -> list[str]:
    return [str(arg) for arg in raw] if isinstance(raw, list) else []


def workload_from_service(service: Any) -> Workload:
    """The Traefik workload as a Swarm service declares it."""
    spec = _mapping(_mapping(getattr(service, "attrs", None)).get("Spec"))
    container = _mapping(_mapping(spec.get("TaskTemplate")).get("ContainerSpec"))
    items: list[Mount] = []
    for raw in container.get("Mounts") or []:
        entry = _mapping(raw)
        target = entry.get("Target")
        if isinstance(target, str) and target:
            kind = str(entry.get("Type") or "").lower()
            source = str(entry.get("Source") or "")
            items.append(Mount(kind, target, source))
    for raw in container.get("Configs") or []:
        entry = _mapping(raw)
        name = entry.get("ConfigName")
        if not isinstance(name, str) or not name:
            continue
        file_name = _mapping(entry.get("File")).get("Name") or name
        # The daemon uses an absolute name as-is and joins a relative one onto
        # "/" (moby daemon/container/container.go, docker-v29.8.0).
        items.append(Mount("config", posixpath.join("/", str(file_name)), name))
    return Workload(
        name=getattr(service, "name", "") or "",
        args=_args(container.get("Args")),
        env=_env(container.get("Env")),
        workdir=container.get("Dir") or None,
        mounts=items,
        service=service,
    )


def workload_from_container(container: Any) -> Workload:
    """The Traefik workload as a plain container's inspect describes it."""
    attrs = _mapping(getattr(container, "attrs", None))
    config = _mapping(attrs.get("Config"))
    items: list[Mount] = []
    for raw in attrs.get("Mounts") or []:
        entry = _mapping(raw)
        target = entry.get("Destination")
        if not isinstance(target, str) or not target:
            continue
        kind = str(entry.get("Type") or "").lower()
        source = entry.get("Name") if kind == "volume" else entry.get("Source")
        items.append(Mount(kind, target, str(source or "")))
    return Workload(
        name=getattr(container, "name", "") or "",
        args=_args(attrs.get("Args")),
        env=_env(config.get("Env")),
        workdir=config.get("WorkingDir") or None,
        mounts=items,
    )


@dataclass(frozen=True)
class Located:
    """A container path and the mount it comes from."""

    path: str
    mount: Mount | None
    #: For a bind mount: the file's path on the host.
    host_path: str | None = None


def _covers(target: str, path: str) -> bool:
    return path == target or path.startswith(target.rstrip("/") + "/")


def locate(workload: Workload, path: str) -> Located:
    """The mount *path* comes from, by longest target -- as the kernel resolves it.

    A config is a single file and matches only its exact target. A bind or
    volume matches its target and everything below it.
    """
    path = posixpath.normpath(path)
    best: Mount | None = None
    best_target = ""
    for mount in workload.mounts:
        target = posixpath.normpath(mount.target)
        matches = path == target if mount.kind == "config" else _covers(target, path)
        if matches and (best is None or len(target) > len(best_target)):
            best, best_target = mount, target
    if best is None:
        return Located(path, None)
    host_path = None
    if best.kind == "bind":
        relative = path[len(best_target) :].lstrip("/")
        host_path = os.path.join(best.source, relative) if relative else best.source
    return Located(path, best, host_path)


class Placement(Protocol):
    """Whether the Traefik task runs on this node, and where it runs otherwise."""

    @property
    def here(self) -> bool:
        """Whether the Traefik task runs on this node."""
        ...

    @property
    def node(self) -> str | None:
        """The node the task runs on, when known and not this one."""
        ...


@dataclass(frozen=True)
class KnownPlacement:
    """A placement that is already known -- tests, and plain containers."""

    here: bool
    node: str | None = None


class Unreadable(Exception):
    """Why a located file could not be read here. The message is rendered as is."""


def _read_host_file(path: str, root: str) -> str:
    real, real_root = os.path.realpath(path), os.path.realpath(root)
    if real != real_root and not real.startswith(real_root.rstrip(os.sep) + os.sep):
        raise Unreadable(f"{path} leads outside {root} — not followed")
    try:
        if os.path.getsize(real) > MAX_FILE_BYTES:
            raise Unreadable(f"{path} is larger than 1 MiB — not read")
        with open(real, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise Unreadable(f"{path}: {exc.strerror or exc}") from exc
    return data.decode("utf-8", "replace")


def read_located(located: Located, *, configs: dict[str, str | None], placement: Placement) -> str:
    """The file's text, or ``Unreadable`` saying why not.

    *configs* maps a config name to its decoded body, ``None`` for one that
    could not be decoded. *placement* is only consulted for a bind mount.
    """
    mount = located.mount
    if mount is None:
        raise Unreadable(f"{located.path} is not mounted")
    if mount.kind == "config":
        if mount.source not in configs:
            raise Unreadable(f"{mount.source}: config not found")
        text = configs[mount.source]
        if text is None:
            raise Unreadable(f"{mount.source}: config data is not decodable")
        return text
    if mount.kind == "bind":
        if not placement.here:
            where = f" (Traefik runs on {placement.node})" if placement.node else ""
            raise Unreadable(
                f"a bind mount of {located.host_path} — not readable on this node{where}"
            )
        return _read_host_file(located.host_path or mount.source, mount.source)
    if mount.kind == "volume":
        raise Unreadable(f"on volume {mount.source} — not readable")
    raise Unreadable(f"on a {mount.kind or 'unknown'} mount — not readable")


def format_of(path: str) -> str | None:
    """``"yaml"`` or ``"toml"`` by extension, ``None`` for anything else."""
    return _DYNAMIC_EXTENSIONS.get(posixpath.splitext(path)[1].lower())


@dataclass
class ProviderListing:
    """The files Traefik's file provider reads, and what could not be listed."""

    files: list[Located] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _not_readable_note(start: str, host_root: str, placement: Placement) -> str:
    where = f" (Traefik runs on {placement.node})" if placement.node else ""
    return f"{start}: a bind mount of {host_root} — not readable on this node{where}"


def _walk(
    workload: Workload,
    mount: Mount,
    directory: str,
    placement: Placement,
    listing: ProviderListing,
) -> list[Located]:
    """Files under *directory* that come from the bind *mount*, recursively.

    Symlinked directories are not descended into -- Traefik's ``os.ReadDir``
    does not either. A file shadowed by a more specific mount is left to that
    mount's own entry.
    """
    base = locate(workload, directory)
    host_root = base.host_path if base.mount == mount else mount.source
    start = directory if base.mount == mount else posixpath.normpath(mount.target)
    if not placement.here:
        listing.notes.append(_not_readable_note(start, host_root or "", placement))
        return []
    found: list[Located] = []
    for current, dirs, files in os.walk(host_root or "", followlinks=False):
        dirs.sort()
        relative_dir = os.path.relpath(current, host_root)
        for name in sorted(files):
            if format_of(name) is None:
                continue
            relative = name if relative_dir == "." else f"{relative_dir}/{name}"
            located = locate(workload, posixpath.join(start, relative))
            if located.mount == mount:
                found.append(located)
    return found


def provider_files(
    workload: Workload, provider: FileProvider, *, placement: Placement
) -> ProviderListing:
    """Every file Traefik's file provider reads, in path order, at most 64."""
    listing = ProviderListing()
    if provider.directory is None:
        if provider.filename:
            listing.files.append(locate(workload, provider.filename))
        return listing

    directory = posixpath.normpath(provider.directory)
    found: dict[str, Located] = {}
    for mount in workload.mounts:
        target = posixpath.normpath(mount.target)
        inside = _covers(directory, target) and target != directory
        if mount.kind == "config" and inside and format_of(target):
            found.setdefault(target, locate(workload, target))
        elif mount.kind == "bind" and (inside or _covers(target, directory)):
            if inside and format_of(target):
                found.setdefault(target, locate(workload, target))
            elif not inside or os.path.isdir(mount.source):
                for located in _walk(workload, mount, directory, placement, listing):
                    found.setdefault(located.path, located)
    ordered = [found[path] for path in sorted(found)]
    if len(ordered) > MAX_PROVIDER_FILES:
        listing.notes.append(
            f"more than {MAX_PROVIDER_FILES} files under {directory} — the rest not read"
        )
        ordered = ordered[:MAX_PROVIDER_FILES]
    listing.files = ordered
    return listing
