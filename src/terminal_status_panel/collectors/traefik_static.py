"""Traefik's static configuration, from whichever source Traefik itself reads.

Pure: text and lists in, a ``StaticConfig`` out. Which source applies -- a
file, the command line, or the environment -- is decided by the collector,
which knows what is mounted; this module only parses and lists candidates.

Traefik reads exactly one source: the first file found, else the flags, else
the environment (https://doc.traefik.io/traefik/getting-started/configuration-overview/;
the search order is in traefik/pkg/cli/loader_file.go and
paerser/cli/file_finder.go, read against Traefik v3.7.13).
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from dataclasses import dataclass, field

import yaml

from ..model import TraefikEntrypoint
from .traefik_parse import parse_entrypoints, parse_ping_entrypoint, port_of

#: The two spellings Traefik's file loader honours. ``--ConfigFile`` selects
#: nothing there, so it selects nothing here.
CONFIG_FILE_FLAGS = ("--configFile", "--configfile")

#: Where Traefik looks when no file is named, in its order, each tried with
#: every extension before the next base path.
_BASE_PATHS = (
    "/etc/traefik/traefik",
    "$XDG_CONFIG_HOME/traefik",
    "$HOME/.config/traefik",
    "./traefik",
)
_EXTENSIONS = ("toml", "yaml", "yml")

_VARIABLE = re.compile(r"\$(?:\{(?P<braced>\w+)\}|(?P<bare>\w+))")
_FILE_PROVIDER_FLAG = re.compile(
    r"^--providers\.file\.(?P<key>directory|filename)=(?P<value>.+)$", re.IGNORECASE
)
_ENV_ENTRYPOINT = re.compile(r"^traefik_entrypoints_(?P<name>[^_]+)_address$")


@dataclass(frozen=True)
class FileProvider:
    """Where Traefik's file provider reads its dynamic configuration."""

    directory: str | None = None
    filename: str | None = None

    @property
    def path(self) -> str | None:
        """The one path Traefik uses: ``directory`` wins when both are set."""
        return self.directory or self.filename


@dataclass
class StaticConfig:
    """The parts of Traefik's static configuration the panel draws."""

    entrypoints: list[TraefikEntrypoint] = field(default_factory=list)
    ping_entrypoint: str | None = None
    file_provider: FileProvider | None = None


def config_file_arg(args: list[str]) -> str | None:
    """The path given by ``--configFile``, if any."""
    for arg in args or []:
        flag, sep, value = arg.strip().partition("=")
        if sep and flag in CONFIG_FILE_FLAGS and value.strip():
            return value.strip()
    return None


def ignored_flags(args: list[str]) -> int:
    """How many flags Traefik ignores because a file was found."""
    return sum(
        1
        for arg in args or []
        if arg.startswith("--") and arg.partition("=")[0] not in CONFIG_FILE_FLAGS
    )


def _expand(text: str, env: dict[str, str]) -> str:
    """``os.ExpandEnv`` over *env*: an unset variable becomes ``""``."""
    return _VARIABLE.sub(lambda m: env.get(m.group("braced") or m.group("bare"), ""), text)


def candidate_paths(args: list[str], env: dict[str, str], workdir: str | None) -> list[str]:
    """Every path Traefik would try for its static file, in its order.

    The environment is the one the workload declares; a ``HOME`` the engine
    injects at run time is not visible from here. ``./`` is only resolved
    against a declared working directory -- an image's own ``WORKDIR`` is not
    visible either, and guessing one would invent a location. A relative
    ``--configFile`` is joined onto that working directory the same way, and
    left relative without one, for the caller to report.
    """
    paths: list[str] = []
    explicit = config_file_arg(args)
    if explicit:
        if workdir and not posixpath.isabs(explicit):
            # Traefik opens a relative path against its own working directory.
            explicit = posixpath.join(workdir, explicit)
        paths.append(explicit)
    for base in _BASE_PATHS:
        expanded = _expand(base, env)
        if expanded.startswith("./"):
            if not workdir:
                continue
            expanded = posixpath.join(workdir, expanded[2:])
        paths.extend(f"{expanded}.{extension}" for extension in _EXTENSIONS)
    return paths


def _get(node: object, key: str) -> object:
    """``node[key]``, matching the key case-insensitively as Traefik does."""
    if not isinstance(node, dict):
        return None
    for name, value in node.items():
        if str(name).lower() == key:
            return value
    return None


def _file_provider(directory: object, filename: object) -> FileProvider | None:
    directory = directory if isinstance(directory, str) and directory else None
    filename = filename if isinstance(filename, str) and filename else None
    if directory is None and filename is None:
        return None
    return FileProvider(directory=directory, filename=filename)


def parse_static_document(text: str, fmt: str) -> StaticConfig:
    """A static configuration file, YAML or TOML.

    Raises ``ValueError`` carrying the parser's own message when the text is
    not valid *fmt* -- the one failure the caller must report as such. A
    document of the wrong shape is not an error: it declares nothing.
    """
    try:
        data = tomllib.loads(text) if fmt == "toml" else yaml.safe_load(text)
    except (tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc

    entrypoints: list[TraefikEntrypoint] = []
    raw = _get(data, "entrypoints")
    for name, spec in raw.items() if isinstance(raw, dict) else []:
        address = _get(spec, "address")
        if isinstance(address, str) and address:
            entrypoints.append(
                TraefikEntrypoint(name=str(name), address=address, port=port_of(address))
            )

    ping = _get(_get(data, "ping"), "entrypoint")
    provider = _get(_get(data, "providers"), "file")
    return StaticConfig(
        entrypoints=entrypoints,
        ping_entrypoint=ping if isinstance(ping, str) and ping else None,
        file_provider=_file_provider(_get(provider, "directory"), _get(provider, "filename")),
    )


def parse_static_args(args: list[str]) -> StaticConfig:
    """The static configuration Traefik builds from its command-line flags."""
    provider: dict[str, str] = {}
    for arg in args or []:
        match = _FILE_PROVIDER_FLAG.match(arg.strip())
        if match:
            provider.setdefault(match.group("key").lower(), match.group("value").strip())
    return StaticConfig(
        entrypoints=parse_entrypoints(args),
        ping_entrypoint=parse_ping_entrypoint(args),
        file_provider=_file_provider(provider.get("directory"), provider.get("filename")),
    )


def has_static_env(env: dict[str, str]) -> bool:
    """Whether Traefik's environment loader would find anything at all."""
    return any(key.upper().startswith("TRAEFIK_") for key in env)


def parse_static_env(env: dict[str, str]) -> StaticConfig:
    """The static configuration from ``TRAEFIK_*`` variables.

    Traefik lower-cases the whole name and reads ``_`` as ``.``, so an
    entrypoint name comes back lower-case and cannot itself contain ``_``.
    """
    lowered = {key.lower(): value for key, value in env.items()}
    entrypoints = [
        TraefikEntrypoint(name=match.group("name"), address=value, port=port_of(value))
        for key, value in lowered.items()
        if value and (match := _ENV_ENTRYPOINT.match(key))
    ]
    ping = lowered.get("traefik_ping_entrypoint") or None
    return StaticConfig(
        entrypoints=entrypoints,
        ping_entrypoint=ping,
        file_provider=_file_provider(
            lowered.get("traefik_providers_file_directory"),
            lowered.get("traefik_providers_file_filename"),
        ),
    )
