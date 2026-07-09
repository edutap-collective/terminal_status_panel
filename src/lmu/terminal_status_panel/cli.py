"""Command-line entry point: collect data, render the panel, always exit 0."""

from __future__ import annotations

import argparse

from rich.console import Console

from .collectors.docker import collect_docker
from .collectors.resources import collect_resources
from .collectors.system import collect_system
from .config import Config, load_config
from .model import PanelData
from .render.layout import build_layout


def collect_all(cfg: Config) -> PanelData:
    return PanelData(
        system=collect_system(),
        resources=collect_resources(),
        swarm=collect_docker(timeout=cfg.docker_timeout, critical=cfg.critical_services),
    )


def build_console(width: int, no_color: bool) -> Console:
    return Console(
        force_terminal=True,
        width=width,
        color_system=None if no_color else "truecolor",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lmu-status-panel")
    parser.add_argument("--width", type=int, default=None, help="fixed render width")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--config", default=None, help="path to a TOML config file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render the status panel. Always returns 0 — never fails a login."""
    try:
        args = _parse_args(argv)
        cfg = load_config(args.config)
        width = args.width if args.width is not None else cfg.width
        console = build_console(width, args.no_color)
        data = collect_all(cfg)
        console.print(build_layout(data, cfg))
    except Exception:
        # A status panel must never break the login shell.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
