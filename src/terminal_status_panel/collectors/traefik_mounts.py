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
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .traefik_static import FileProvider

MAX_FILE_BYTES = 1024 * 1024
MAX_PROVIDER_FILES = 64
#: Directory entries a provider walk may look at before it stops: a bound on
#: the work a misconfigured spec can cause, far above any real layout.
MAX_SCANNED_ENTRIES = 10_000

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


def _entries(raw: object) -> list[dict]:
    """The mappings in *raw* when it is a list, nothing otherwise.

    The Docker API sends a list of mappings here. Any other shape is one it
    should not send, and is ignored rather than allowed to raise out of the
    collector.
    """
    return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []


def _text(raw: object) -> str | None:
    """*raw* when it is a non-empty string, ``None`` otherwise."""
    return raw if isinstance(raw, str) and raw else None


def _mount(kind: object, target: object, source: object) -> Mount | None:
    """One mount, or ``None`` when its target or its source is not a string.

    An absent source is an empty one -- a tmpfs mount has none.
    """
    if not isinstance(target, str) or not target:
        return None
    if source is not None and not isinstance(source, str):
        return None
    return Mount(str(kind or "").lower(), target, source or "")


def workload_from_service(service: Any) -> Workload:
    """The Traefik workload as a Swarm service declares it."""
    spec = _mapping(_mapping(getattr(service, "attrs", None)).get("Spec"))
    container = _mapping(_mapping(spec.get("TaskTemplate")).get("ContainerSpec"))
    items: list[Mount] = []
    for entry in _entries(container.get("Mounts")):
        mount = _mount(entry.get("Type"), entry.get("Target"), entry.get("Source"))
        if mount is not None:
            items.append(mount)
    for entry in _entries(container.get("Configs")):
        name = _text(entry.get("ConfigName"))
        if name is None:
            continue
        file_name = _text(_mapping(entry.get("File")).get("Name")) or name
        # The daemon uses an absolute name as-is and joins a relative one onto
        # "/" (moby daemon/container/container.go, docker-v29.8.0).
        items.append(Mount("config", posixpath.join("/", file_name), name))
    return Workload(
        name=getattr(service, "name", "") or "",
        args=_args(container.get("Args")),
        env=_env(container.get("Env")),
        workdir=_text(container.get("Dir")),
        mounts=items,
        service=service,
    )


def workload_from_container(container: Any) -> Workload:
    """The Traefik workload as a plain container's inspect describes it."""
    attrs = _mapping(getattr(container, "attrs", None))
    config = _mapping(attrs.get("Config"))
    items: list[Mount] = []
    for entry in _entries(attrs.get("Mounts")):
        kind = str(entry.get("Type") or "").lower()
        source = entry.get("Name") if kind == "volume" else entry.get("Source")
        mount = _mount(kind, entry.get("Destination"), source)
        if mount is not None:
            items.append(mount)
    return Workload(
        name=getattr(container, "name", "") or "",
        args=_args(attrs.get("Args")),
        env=_env(config.get("Env")),
        workdir=_text(config.get("WorkingDir")),
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


def _os_error_text(exc: OSError | ValueError) -> str:
    """A human-readable reason -- ``strerror`` for an ``OSError``, else the message.

    A path holding a NUL byte fails as ``ValueError`` before any syscall runs
    at all (``strerror`` does not apply); a missing file or a permission
    problem fails as ``OSError``, and ``strerror`` is the useful part of it.
    """
    return exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)


#: A defensive bound on a chain of symlink hops, well above any real Traefik
#: dynamic-config layout -- not a claim about what the OS allows.
_MAX_SYMLINK_HOPS = 40


class _MissingComponent(Unreadable):
    """A component above the final one does not exist, so neither does the path.

    It carries the message ``Unreadable`` always carried there, so a read
    reports it exactly as before; only ``presence`` tells it apart, because
    for Traefik's file search a missing directory means "not there".
    """


def _lstat_or_defer(candidate: str, path: str, *, is_final: bool) -> os.stat_result | None:
    """``lstat`` *candidate*, or ``None`` to defer a missing *final* component.

    A missing final component is not reported here -- the ordinary open()
    failure that follows resolution reports "no such file" in its usual
    wording. A missing component above it is ``_MissingComponent``; any other
    failure, at any position, is reported immediately.
    """
    try:
        return os.lstat(candidate)
    except (OSError, ValueError) as exc:
        if isinstance(exc, FileNotFoundError):
            if is_final:
                return None
            raise _MissingComponent(f"{path}: {_os_error_text(exc)}") from exc
        raise Unreadable(f"{path}: {_os_error_text(exc)}") from exc


def _symlink_hop_components(candidate: str, path: str, hops: int) -> tuple[list[str], int]:
    """The components of *candidate*'s relative link target, and the new hop count.

    Refuses an absolute target -- resolved on the host, it may stay inside
    the bind source only by coincidence; inside the container, the same
    absolute target resolves under a different root and names a different
    file -- and a chain longer than ``_MAX_SYMLINK_HOPS``.
    """
    hops += 1
    if hops > _MAX_SYMLINK_HOPS:
        raise Unreadable(f"{path}: too many levels of symbolic links — not followed")
    target = os.readlink(candidate)
    if os.path.isabs(target):
        raise Unreadable(
            f"{candidate} is an absolute symlink — resolves differently "
            "inside the container, not followed"
        )
    return target.split(os.sep), hops


def _resolve_below_root(path: str, root: str) -> str:
    """The real host path *path* resolves to: *root* and its resolved components."""
    return os.path.join(root, *_resolved_below_root(path, root))


def _resolved_below_root(path: str, root: str) -> list[str]:
    """The physical components *path* resolves to below *root* -- as the kernel does.

    *root* is never ``lstat``'ed or read as a link: the daemon already
    resolved the bind source on the host, so the container sees exactly its
    target, whatever *root* is spelled as or itself points to. Every
    component below *root* is resolved one at a time, kernel-style:
    ``..`` undoes the last *resolved* (already-physical) component rather
    than the last written one, so it cannot be fooled by a symlinked
    component that was never actually descended into lexically; a relative
    symlink's own target is pushed back onto the queue, so its own ``..``
    and any further links are resolved the exact same way; a climb that
    would go above *root* is refused, even one that would immediately
    return back below it -- inside the container, that same relative link
    resolves against a different root and names a different file.

    Raises ``Unreadable`` for an absolute symlink (at any hop), a symlink
    loop, a climb above *root*, a component that could not even be
    inspected, or a link that changed while it was being resolved -- except
    a missing *final* component, which is left for the ordinary "no such
    file" failure that follows this call, in its usual wording. Nothing else
    escapes: every caller turns ``Unreadable`` into a reason, never a crash.
    """
    if not path:
        # Only a bind mount whose Source is empty gets here with no path at
        # all; relpath() would fail with a message that names nothing.
        raise Unreadable("a bind mount with an empty source — not read")
    try:
        return _resolve_components(path, root)
    except (OSError, ValueError) as exc:
        # readlink() after lstat() races a link being removed or replaced in
        # between; relpath() rejects what it cannot compare.
        raise Unreadable(f"{path}: {_os_error_text(exc)}") from exc


def _resolve_components(path: str, root: str) -> list[str]:
    """The body of ``_resolved_below_root``, free to raise ``OSError``/``ValueError``."""
    relative = os.path.relpath(path, root)
    if relative in (os.curdir, ""):
        return []
    pending = relative.split(os.sep)
    resolved: list[str] = []
    hops = 0
    while pending:
        component = pending.pop(0)
        if component in ("", os.curdir):
            continue
        if component == os.pardir:
            if not resolved:
                raise Unreadable(f"{path} leads outside {root} — not followed")
            resolved.pop()
            continue
        candidate = os.path.join(root, *resolved, component)
        info = _lstat_or_defer(candidate, path, is_final=not pending)
        if info is None:
            resolved.append(component)
            break
        if not stat.S_ISLNK(info.st_mode):
            resolved.append(component)
            continue
        target_components, hops = _symlink_hop_components(candidate, path, hops)
        pending[0:0] = target_components
    return resolved


def _open_below_root(root: str, parts: list[str]) -> int:
    """A descriptor for *root*/*parts*, opened one component at a time, never through a link.

    *parts* were resolved already. Each is opened relative to the directory
    opened just before it, with ``O_NOFOLLOW``, so a component that became a
    symlink since it was resolved fails to open instead of being followed:
    what was checked is what is read. *root* itself is opened by name and may
    be a link -- the daemon resolved it when it created the mount. The last
    component is opened ``O_NONBLOCK`` so a FIFO cannot block the login
    waiting for a writer that never comes. Every descriptor but the returned
    one is closed on every path.
    """
    if not parts:
        return os.open(root, os.O_RDONLY | os.O_NONBLOCK)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            below = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = below
        return os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)


def _read_host_file(path: str, root: str) -> str:
    """The text at *path*, refusing anything that is not a plain, contained file.

    Opened without following a link at any component (see
    ``_open_below_root``); ``fstat`` then refuses anything that is not a
    regular file (a FIFO, a socket, a device) before a byte is read from it.
    """
    parts = _resolved_below_root(path, root)
    try:
        descriptor = _open_below_root(root, parts)
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path}: {_os_error_text(exc)}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Unreadable(f"{path} is not a regular file — not read")
        if info.st_size > MAX_FILE_BYTES:
            raise Unreadable(f"{path} is larger than 1 MiB — not read")
        data = os.read(descriptor, MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise Unreadable(f"{path}: {_os_error_text(exc)}") from exc
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE_BYTES:
        raise Unreadable(f"{path} is larger than 1 MiB — not read")
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


@dataclass(frozen=True)
class Presence:
    """Whether a located file is there, as far as this node can tell."""

    #: ``"unknown"`` when the mount in the way cannot be checked from here.
    state: Literal["present", "absent", "unknown"]
    #: For ``"unknown"``: the mount in the way, and why it cannot be checked.
    reason: str | None = None


def presence(located: Located, *, placement: Placement) -> Presence:
    """Whether *located* exists inside the container -- measured where that can be done.

    A config, and a bind mount aimed exactly at the path, are there by
    definition; whether they can be *read* is ``read_located``'s question. A
    path under a bind-mounted directory is there only if the file is: that is
    measured on this node, through the resolver a read uses, so the symlink
    rules are the same. Anything else -- a bind directory on another node, a
    volume, a tmpfs, a check that fails for any reason but "does not exist",
    or no mount at all -- cannot be checked from here.
    """
    mount = located.mount
    if mount is None:
        return Presence("unknown", f"{located.path} is not mounted")
    target = posixpath.normpath(mount.target)
    if mount.kind == "config" or (mount.kind == "bind" and located.path == target):
        return Presence("present")
    if mount.kind == "volume":
        return Presence("unknown", f"{target} is on volume {mount.source}")
    if mount.kind != "bind":
        return Presence("unknown", f"{target} is on a {mount.kind or 'unknown'} mount")
    if not placement.here:
        return Presence(
            "unknown", f"{target} is a bind mount of {mount.source}, {_not_here_reason(placement)}"
        )
    return _bind_presence(located.host_path or mount.source, mount, target)


def _bind_presence(host_path: str, mount: Mount, target: str) -> Presence:
    """Whether *host_path*, below the bind *mount*, exists on this node.

    Only "does not exist" -- at the file or at a directory above it -- makes
    it absent, as only that makes Traefik move on to its next candidate.
    """
    try:
        os.stat(_resolve_below_root(host_path, mount.source))
    except (_MissingComponent, FileNotFoundError):
        return Presence("absent")
    except Unreadable as exc:
        reason = str(exc)
    except (OSError, ValueError) as exc:
        reason = f"{host_path}: {_os_error_text(exc)}"
    else:
        return Presence("present")
    return Presence(
        "unknown", f"{target} is a bind mount of {mount.source}, and checking it failed: {reason}"
    )


def format_of(path: str) -> str | None:
    """``"yaml"`` or ``"toml"`` by extension, ``None`` for anything else."""
    return _DYNAMIC_EXTENSIONS.get(posixpath.splitext(path)[1].lower())


@dataclass
class ProviderListing:
    """The files Traefik's file provider reads, and what could not be listed."""

    files: list[Located] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _bind_mount_note(start: str, host_source: str, reason: str) -> str:
    """A note naming both the container path and the host path behind it."""
    return f"{start}: a bind mount of {host_source} — {reason}"


def _not_here_reason(placement: Placement) -> str:
    where = f" (Traefik runs on {placement.node})" if placement.node else ""
    return f"not readable on this node{where}"


@dataclass
class _Scan:
    """How far the provider walk has got -- shared by every mount it lists."""

    #: Matching files known so far; a walk stops once there are more than
    #: ``MAX_PROVIDER_FILES`` of them, since the rest would not be read.
    files: int = 0
    #: Directory entries looked at so far, against ``MAX_SCANNED_ENTRIES``.
    entries: int = 0
    #: Set once the entry budget is spent; no further walk starts after that.
    exhausted: bool = False


class _ScanBudgetSpent(Exception):
    """The walk has looked at ``MAX_SCANNED_ENTRIES`` entries; it stopped in *directory*."""

    def __init__(self, directory: str) -> None:
        super().__init__(directory)
        self.directory = directory


def _entries_of(directory: str, scan: _Scan) -> tuple[list[str], list[str]]:
    """The subdirectory and file names in *directory*, each sorted, counted against the budget.

    Classified as ``os.walk`` classifies them: a symlink to a directory is a
    directory (and is not descended into), anything else is a file. Counting
    while the entries arrive means even one enormous directory costs no more
    than the budget.
    """
    dirs: list[str] = []
    files: list[str] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            scan.entries += 1
            if scan.entries > MAX_SCANNED_ENTRIES:
                raise _ScanBudgetSpent(directory)
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            (dirs if is_dir else files).append(entry.name)
    return sorted(dirs), sorted(files)


def _walk_tree(top: str, scan: _Scan) -> Iterator[tuple[str, list[str]]]:
    """``os.walk(top, followlinks=False)``, top-down in sorted order, within the scan budget.

    Yields each directory with its file names. A directory that cannot be
    listed raises its ``OSError``, as ``os.walk`` with a re-raising
    ``onerror`` would; a stack rather than recursion keeps a deep tree from
    hitting the interpreter's recursion limit.
    """
    pending = [top]
    while pending:
        current = pending.pop()
        dirs, files = _entries_of(current, scan)
        yield current, files
        below = [os.path.join(current, name) for name in dirs]
        pending.extend(path for path in reversed(below) if not os.path.islink(path))


def _is_regular_file(path: str) -> bool:
    """Whether *path* names a plain file -- never a FIFO, socket or device."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


def _walked_file(
    workload: Workload,
    mount: Mount,
    start: str,
    current: str,
    name: str,
    listing: ProviderListing,
    host_root: str,
) -> Located | None:
    """The walked entry *name* in *current*, when the provider reads it from *mount*.

    A file shadowed by a more specific mount is left to that mount's own
    entry. A non-regular dirent (a FIFO, a socket) is reported in a note
    rather than listed.
    """
    if format_of(name) is None:
        return None
    relative_dir = os.path.relpath(current, host_root)
    relative = name if relative_dir == os.curdir else f"{relative_dir}/{name}"
    container_path = posixpath.join(start, relative)
    # Resolve the same way read_located would -- against the bind source, not
    # *host_root*: inside the container a link may climb out of the provider
    # directory and still land in the mount, and a link between the source
    # and *host_root* counts too. A walked entry and a directly-read one then
    # agree: an absolute symlink, a loop, an escaping "..", or a link that
    # changed mid-walk is a note for this entry here rather than an
    # ``Unreadable`` there.
    try:
        resolved_file = _resolve_below_root(os.path.join(current, name), mount.source)
    except Unreadable as exc:
        listing.notes.append(f"{container_path}: {exc}")
        return None
    if not _is_regular_file(resolved_file):
        listing.notes.append(f"{container_path}: not a regular file — not listed")
        return None
    located = locate(workload, container_path)
    return located if located.mount == mount else None


def _walk(
    workload: Workload,
    mount: Mount,
    host_root: str,
    start: str,
    listing: ProviderListing,
    scan: _Scan,
) -> list[Located]:
    """Files under *start* that come from the bind *mount*, recursively.

    *host_root* is *mount*'s own host-side subtree for *start* -- the caller
    has already resolved which mount owns *start* and that the task runs
    here. Symlinked directories are not descended into -- Traefik's
    ``os.ReadDir`` does not either. The walk stops once more than
    ``MAX_PROVIDER_FILES`` matching files are known, and after
    ``MAX_SCANNED_ENTRIES`` directory entries with a note naming where.
    """
    found: list[Located] = []
    if scan.exhausted or scan.files > MAX_PROVIDER_FILES:
        return found
    try:
        for current, files in _walk_tree(host_root, scan):
            for name in files:
                located = _walked_file(workload, mount, start, current, name, listing, host_root)
                if located is None:
                    continue
                found.append(located)
                scan.files += 1
                if scan.files > MAX_PROVIDER_FILES:
                    return found
    except _ScanBudgetSpent as spent:
        scan.exhausted = True
        relative_dir = os.path.relpath(spent.directory, host_root)
        where = start if relative_dir == os.curdir else posixpath.join(start, relative_dir)
        listing.notes.append(
            f"{where}: more than {MAX_SCANNED_ENTRIES} directory entries — the rest not scanned"
        )
    except (OSError, ValueError) as exc:
        # An OSError from the walk names the exact entry that failed (e.g. an
        # unreadable subdirectory); fall back to the walk's own root when the
        # exception carries nothing more specific.
        failing_host_path = getattr(exc, "filename", None) or host_root
        listing.notes.append(
            _bind_mount_note(start, failing_host_path, f"{_os_error_text(exc)}, not listed")
        )
        return []
    return found


def _nested_bind_files(
    workload: Workload,
    mount: Mount,
    target: str,
    placement: Placement,
    listing: ProviderListing,
    scan: _Scan,
) -> list[Located]:
    """Files from a bind mount nested inside the provider directory.

    *target* has no dynamic extension here (a recognised extension is
    handled by the caller as a single file), so it is either a directory or
    something Traefik would not read either way.
    """
    if not placement.here:
        listing.notes.append(_bind_mount_note(target, mount.source, _not_here_reason(placement)))
        return []
    try:
        info = os.stat(mount.source)
    except (OSError, ValueError) as exc:
        listing.notes.append(
            _bind_mount_note(target, mount.source, f"{_os_error_text(exc)}, not listed")
        )
        return []
    if not stat.S_ISDIR(info.st_mode):
        return []
    return _walk(workload, mount, mount.source, target, listing, scan)


def _covering_bind_files(
    workload: Workload,
    mount: Mount,
    directory: str,
    placement: Placement,
    listing: ProviderListing,
    scan: _Scan,
) -> list[Located]:
    """Files from a bind mount whose target covers the whole provider directory.

    A more specific mount may already own *directory* itself -- that mount
    lists its own subtree in its own turn, and this one lists nothing.
    """
    base = locate(workload, directory)
    if base.mount != mount:
        return []
    host_root = base.host_path or mount.source
    if not placement.here:
        listing.notes.append(_bind_mount_note(directory, host_root, _not_here_reason(placement)))
        return []
    return _walk(workload, mount, host_root, directory, listing, scan)


def _empty_directory_note(workload: Workload, directory: str) -> str | None:
    """Why nothing was found under *directory* at all, when that has an explanation."""
    owner = locate(workload, directory).mount
    if owner is None:
        # A mount nested below *directory* (rather than covering it) already
        # explains an empty result in its own right -- it may just be an
        # empty, perfectly readable directory. "Not mounted" is only true
        # when nothing lies at or under *directory* at all.
        under_directory = any(
            _covers(directory, posixpath.normpath(mount.target)) for mount in workload.mounts
        )
        return None if under_directory else f"{directory} is not mounted — nothing to read"
    if owner.kind == "volume":
        return f"{directory} is on volume {owner.source} — not readable"
    if owner.kind == "tmpfs":
        return f"{directory} is on a tmpfs mount — not readable"
    return None


def _unreadable_mount_note(mount: Mount, target: str) -> str:
    """A mount the panel cannot look into, in ``read_located``'s words."""
    if mount.kind == "volume":
        return f"{target} is on volume {mount.source} — not readable"
    return f"{target} is on a {mount.kind or 'unknown'} mount — not readable"


def _files_from_mount(
    workload: Workload,
    mount: Mount,
    directory: str,
    placement: Placement,
    listing: ProviderListing,
    scan: _Scan,
) -> list[Located]:
    """What this one mount contributes to *directory*'s listing, if anything."""
    target = posixpath.normpath(mount.target)
    inside = _covers(directory, target) and target != directory
    if mount.kind == "config":
        return [locate(workload, target)] if inside and format_of(target) else []
    if mount.kind != "bind":
        if inside:
            # Traefik reads the dynamic files in there; the panel cannot, and
            # leaving the subtree out would pass a partial listing off as whole.
            listing.notes.append(_unreadable_mount_note(mount, target))
        return []
    if inside:
        if format_of(target):
            return [locate(workload, target)]
        return _nested_bind_files(workload, mount, target, placement, listing, scan)
    if _covers(target, directory):
        return _covering_bind_files(workload, mount, directory, placement, listing, scan)
    return []


def _capped(ordered: list[Located], directory: str, listing: ProviderListing) -> list[Located]:
    """*ordered*, truncated to ``MAX_PROVIDER_FILES`` with a note about the rest."""
    if len(ordered) <= MAX_PROVIDER_FILES:
        return ordered
    listing.notes.append(
        f"more than {MAX_PROVIDER_FILES} files under {directory} — the rest not read"
    )
    return ordered[:MAX_PROVIDER_FILES]


def provider_files(
    workload: Workload, provider: FileProvider, *, placement: Placement
) -> ProviderListing:
    """Every file Traefik's file provider reads, in path order, at most 64.

    The walk behind it is bounded too: it stops once more files are known
    than are read, and after ``MAX_SCANNED_ENTRIES`` directory entries.
    """
    listing = ProviderListing()
    if provider.directory is None:
        if provider.filename:
            listing.files.append(locate(workload, provider.filename))
        return listing

    directory = posixpath.normpath(provider.directory)
    found: dict[str, Located] = {}
    scan = _Scan()
    for mount in workload.mounts:
        scan.files = len(found)
        for located in _files_from_mount(workload, mount, directory, placement, listing, scan):
            found.setdefault(located.path, located)

    listing.files = _capped([found[path] for path in sorted(found)], directory, listing)

    if not listing.files and not listing.notes:
        note = _empty_directory_note(workload, directory)
        if note:
            listing.notes.append(note)

    return listing
