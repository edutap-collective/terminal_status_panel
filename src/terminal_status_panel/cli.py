"""Command-line entry points: collect data, render the panel, always exit 0.

Five console scripts share this module:

- ``status-full``    — the default sections (``server`` + ``docker`` + ``health``).
- ``status-server``  — only the system/server section.
- ``status-docker``  — only the Docker section.
- ``status-health``  — only the cluster health section.
- ``status-traefik`` — only the Traefik wiring section.

Each section collects only the data it needs, so ``status-docker`` never
touches the system collectors and ``status-server`` never opens the Docker
socket.

``status-traefik`` is deliberately not part of ``status-full``'s default —
see ``DEFAULT_SECTIONS`` below.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from rich.console import Console

from .collectors.docker import collect_docker
from .collectors.health import collect_health
from .collectors.resources import collect_resources
from .collectors.system import collect_system
from .collectors.traefik import collect_traefik, fetch_accepted, mark_rejected
from .collectors.updates import collect_updates
from .config import Config, load_config
from .model import PanelData
from .render.layout import SECTIONS, build_layout

# The sections a bare `status-full` renders. Deliberately narrower than
# `SECTIONS` (which lists every section the layout knows how to build, so
# `--sections traefik` works): nine entrypoints and their routers would bury
# the login banner, so `traefik` is opt-in via `--sections` or `status-traefik`
# rather than part of the default full panel.
DEFAULT_SECTIONS: tuple[str, ...] = ("server", "docker", "health")


def _health_socket_timeout(cfg: Config) -> float:
    """Socket timeout for the health section's Docker client.

    docker-py has no per-call timeout: the client's socket timeout bounds every
    ``exec``. With the package default ``docker.timeout = 1.5`` the Kafka probe
    (~2.6 s of JVM startup) could therefore never succeed, whatever
    ``health.timeout.kafka`` said. The health client uses the largest enabled
    per-kind timeout instead when that is larger, so the per-kind budget
    deadline — the knob documented for this purpose — is what actually decides.
    ``docker.timeout`` keeps bounding the DOCKER INFOS section unchanged.
    """
    per_kind = [cfg.health.timeouts.get(kind, 0.0) for kind in cfg.health.enabled]
    return max([cfg.docker_timeout, *per_kind])


def _docker_client(cfg: Config):
    """A Docker client for the health probes, or None when unavailable.

    Constructed with ``docker.timeout``, not with the larger health timeout:
    ``docker.from_env()`` negotiates the API version with the daemon on
    construction, and that request happens here — on the main thread, before
    the budget. ``docker.timeout`` is the knob documented for exactly that
    ("keeps a hung/absent daemon from delaying login").

    The larger health timeout is applied afterwards, to the same client, so it
    bounds only the probe requests that run inside the budget. Set once, before
    any check thread starts, so the concurrent probes never see it change.
    """
    try:
        import docker

        client = docker.from_env(timeout=cfg.docker_timeout)
        client.api.timeout = _health_socket_timeout(cfg)
        return client
    except Exception:
        return None


def _peer_names(swarm) -> list[str]:
    return [node.name for node in getattr(swarm, "nodes", []) or []]


def _swarm_node_names(client) -> list[str]:
    """Swarm node hostnames, straight from the Docker API.

    Handed to ``collect_health`` as a callable rather than called here: it
    talks to the Docker daemon over the health client, whose socket timeout is
    the largest per-kind health timeout, and unbudgeted main-thread work of
    that size on a login path is the very thing the budget exists to prevent.
    """
    names = [
        (node.attrs.get("Description", {}) or {}).get("Hostname", "")
        for node in client.nodes.list()
    ]
    return [name for name in names if name]


def collect_all(cfg: Config, sections: tuple[str, ...] = SECTIONS) -> PanelData:
    """Collect only the data required by the requested sections."""
    server = "server" in sections
    docker_section = "docker" in sections
    health = "health" in sections
    traefik = "traefik" in sections

    swarm = (
        collect_docker(
            timeout=cfg.docker_timeout,
            critical=cfg.critical_services,
            description_label=cfg.description_label,
        )
        if docker_section
        else None
    )

    health_info = None
    if health:
        client = _docker_client(cfg)
        health_info = collect_health(
            cfg,
            # Free: the docker section already collected these. Empty when that
            # section was not selected, which is when the callable below is
            # used — inside the budget, never here.
            peer_names=_peer_names(swarm),
            client=client,
            resolve_peer_names=(
                None if client is None else lambda: _swarm_node_names(client)
            ),
        )

    traefik_info = None
    if traefik:
        client = _docker_client(cfg)
        traefik_info = collect_traefik(client, timeout=cfg.docker_timeout)
        # The API cross-check is optional (see TraefikApiConfig) and, when
        # unreachable, `fetch_accepted` returns None rather than an empty set —
        # so an unreachable API leaves every router unconsulted, not rejected.
        accepted = fetch_accepted(cfg)
        if accepted is not None:
            mark_rejected(traefik_info, accepted)

    return PanelData(
        system=collect_system() if server else None,
        resources=collect_resources() if server else None,
        updates=collect_updates(timeout=cfg.docker_timeout) if server else None,
        swarm=swarm,
        health=health_info,
        traefik=traefik_info,
    )


def resolve_width(arg_width: int | None, cfg: Config) -> int:
    """Pick the render width: explicit flag wins; otherwise use the real
    terminal width when attached to one, else the configured fixed width
    (as used for MOTD generation, where no TTY is present)."""
    if arg_width is not None:
        return arg_width
    if sys.stdout.isatty():
        columns = shutil.get_terminal_size(fallback=(cfg.width, 24)).columns
        if columns > 0:
            return columns
    return cfg.width


def build_console(width: int, no_color: bool) -> Console:
    return Console(
        force_terminal=True,
        width=width,
        color_system=None if no_color else "truecolor",
    )


def _resolve_sections(arg: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not arg:
        return default
    names = tuple(n.strip() for n in arg.split(",") if n.strip() in SECTIONS)
    return names or default


def _parse_args(argv: list[str] | None, prog: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--width", type=int, default=None, help="fixed render width")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--config", default=None, help="path to a TOML config file")
    parser.add_argument("--sections", default=None,
                        help="comma-separated sections to render: "
                             "server,docker,health,traefik")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, sections: tuple[str, ...] = DEFAULT_SECTIONS,
         prog: str = "status-full") -> int:
    """Render the status panel. Always returns 0 — never fails a login."""
    try:
        args = _parse_args(argv, prog)
        cfg = load_config(args.config)
        selected = _resolve_sections(args.sections, sections)
        width = resolve_width(args.width, cfg)
        console = build_console(width, args.no_color)
        data = collect_all(cfg, selected)
        console.print(build_layout(data, cfg, selected))
    except Exception:
        # A status panel must never break the login shell.
        pass
    return 0


def server_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-server`` — system section only."""
    return main(argv, sections=("server",), prog="status-server")


def docker_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-docker`` — Docker section only."""
    return main(argv, sections=("docker",), prog="status-docker")


def health_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-health`` — cluster health section only."""
    return main(argv, sections=("health",), prog="status-health")


def traefik_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-traefik`` — the wiring viewer only."""
    return main(argv, sections=("traefik",), prog="status-traefik")


if __name__ == "__main__":
    raise SystemExit(main())
