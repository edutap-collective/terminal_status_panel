"""Configuration: built-in defaults overridable by an optional TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = "/etc/lmu-status-panel/config.toml"


@dataclass
class Thresholds:
    memory_warning: float = 75.0
    memory_critical: float = 90.0
    swap_warning: float = 1.0
    filesystem_warning: float = 80.0
    filesystem_critical: float = 90.0
    load_warning: float = 0.8  # per-CPU multiplier
    load_critical: float = 1.0  # per-CPU multiplier


@dataclass
class Config:
    width: int = 80
    docker_timeout: float = 1.5
    critical_services: list[str] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)


def _section(data: dict, *keys: str) -> dict:
    node = data
    for key in keys:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from *path* (or the default location). Never raises on a
    missing or unreadable file — falls back to defaults."""
    target = os.fspath(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        with open(target, "rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, PermissionError, tomllib.TOMLDecodeError, OSError):
        return Config()

    t = Thresholds()
    mem = _section(data, "thresholds", "memory")
    swap = _section(data, "thresholds", "swap")
    fs = _section(data, "thresholds", "filesystem")
    load = _section(data, "thresholds", "load")
    thresholds = Thresholds(
        memory_warning=float(mem.get("warning", t.memory_warning)),
        memory_critical=float(mem.get("critical", t.memory_critical)),
        swap_warning=float(swap.get("warning", t.swap_warning)),
        filesystem_warning=float(fs.get("warning", t.filesystem_warning)),
        filesystem_critical=float(fs.get("critical", t.filesystem_critical)),
        load_warning=float(load.get("warning", t.load_warning)),
        load_critical=float(load.get("critical", t.load_critical)),
    )
    return Config(
        width=int(data.get("width", 80)),
        docker_timeout=float(_section(data, "docker").get("timeout", 1.5)),
        critical_services=list(_section(data, "services").get("critical", [])),
        thresholds=thresholds,
    )
