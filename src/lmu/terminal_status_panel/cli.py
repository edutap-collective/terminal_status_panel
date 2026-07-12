"""Command-line entry points: collect data, render the panel, always exit 0.

Three console scripts share this module:

- ``lmu-status-panel``  — both sections (``server`` + ``docker``).
- ``lmu-server-status`` — only the system/server section.
- ``lmu-docker-status`` — only the Docker section.

Each section collects only the data it needs, so ``lmu-docker-status`` never
touches the system collectors and ``lmu-server-status`` never opens the Docker
socket.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from rich.console import Console

from .collectors.docker import collect_docker
from .collectors.resources import collect_resources
from .collectors.system import collect_system
from .collectors.updates import collect_updates
from .config import Config, load_config
from .model import PanelData
from .render.layout import SECTIONS, build_layout


def collect_all(cfg: Config, sections: tuple[str, ...] = SECTIONS) -> PanelData:
    """Collect only the data required by the requested sections."""
    server = "server" in sections
    docker = "docker" in sections
    return PanelData(
        system=collect_system() if server else None,
        resources=collect_resources() if server else None,
        updates=collect_updates(timeout=cfg.docker_timeout) if server else None,
        swarm=collect_docker(timeout=cfg.docker_timeout, critical=cfg.critical_services,
                             description_label=cfg.description_label) if docker else None,
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
                        help="comma-separated sections to render: server,docker")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, sections: tuple[str, ...] = SECTIONS,
         prog: str = "lmu-status-panel") -> int:
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
    """Entry point for ``lmu-server-status`` — system section only."""
    return main(argv, sections=("server",), prog="lmu-server-status")


def docker_main(argv: list[str] | None = None) -> int:
    """Entry point for ``lmu-docker-status`` — Docker section only."""
    return main(argv, sections=("docker",), prog="lmu-docker-status")


if __name__ == "__main__":
    raise SystemExit(main())
