"""Configuration: built-in defaults overridable by an optional TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

from . import platform_defaults

#: Docker service label read as the per-service description column.
#: Vendor-neutral, as befits a public package.
DEFAULT_DESCRIPTION_LABEL = "status.description"

#: The key this panel read before the rename. Still honoured as a fallback by
#: the Docker collector, so installations that set it -- and never set
#: ``docker.description_label`` -- keep their descriptions without changing
#: anything.
LEGACY_DESCRIPTION_LABEL = "lmu.service.description"

DEFAULT_CONFIG_PATH = "/etc/terminal-status-panel/config.toml"


@dataclass
class Thresholds:
    memory_warning: float = 75.0
    memory_critical: float = 90.0
    swap_warning: float = field(default_factory=platform_defaults.swap_warning)
    filesystem_warning: float = 80.0
    filesystem_critical: float = 90.0
    load_warning: float = 0.8  # per-CPU multiplier
    load_critical: float = 1.0  # per-CPU multiplier


@dataclass
class DnsExpectation:
    name: str
    addresses: list[str] = field(default_factory=list)


@dataclass
class HealthConfig:
    budget: float = 5.0
    timeouts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_HEALTH_TIMEOUTS)
    )
    enabled: list[str] = field(default_factory=lambda: list(DEFAULT_HEALTH_KINDS))
    dns_expect: list[DnsExpectation] = field(default_factory=list)


DEFAULT_INFRASTRUCTURE_STACKS = [
    "postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer",
    "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql",
    "elasticsearch", "bugsink",
]

# Admin web UIs for infrastructure services. Matched case-insensitively against
# the stack name *and* the service name; matches are grouped into the
# "infra-uis" pseudo stack and win over DEFAULT_INFRASTRUCTURE_STACKS.
DEFAULT_INFRA_UI_SERVICES = [
    "kafbat-ui", "kafka-ui", "kafdrop",
    "cloudbeaver", "pgadmin", "adminer",
    "mongo-express", "mongo-gui",
    "rustfs-console", "rustfs-ui",
    "s3-browser", "s3browser", "s3manager",
    "redisinsight", "redis-commander",
    "dozzle", "kibana",
]

DEFAULT_HEALTH_KINDS = ("postgres", "mongodb", "kafka", "glusterfs", "rustfs")

# One timeout per check. Every check — each cluster kind, the WireGuard peers,
# DNS — runs as its own task under the shared budget, and its timeout is the
# deadline for that task: a check that overruns is reported as out of budget
# while the others keep their results. The checks run concurrently, so these
# values are deadlines, not a sum, and none of them may exceed the budget.
#
# glusterfs and rustfs additionally enforce their value on the child process
# they spawn (subprocess timeout, curl -m). The three docker-exec probes
# cannot: docker-py bounds an exec by the client's socket timeout, not per
# call — so the health client is built with a socket timeout no smaller than
# the largest enabled value here (see cli._health_socket_timeout), leaving the
# task deadline as the bound that decides. Kafka is the expensive one: ~2.6 s
# of JVM startup, which is why its value is the largest.
DEFAULT_HEALTH_TIMEOUTS = {
    "postgres": 1.5,
    "mongodb": 2.5,
    "kafka": 4.0,
    "glusterfs": 1.0,
    "rustfs": 2.0,
    "wireguard": 1.0,
    "dns": 2.5,
}


@dataclass
class TraefikApiConfig:
    """The optional runtime cross-check. Off unless a URL and cert are given.

    Dormant today: the dashboard router requires a client certificate signed by
    the webfe CA, and the Ansible role issues only app-server TinyCA ones.
    """

    url: str | None = None
    cert: str | None = None
    key: str | None = None
    ca: str | None = None


@dataclass
class Config:
    width: int = 80
    docker_timeout: float = 1.5
    critical_services: list[str] = field(default_factory=list)
    description_label: str = DEFAULT_DESCRIPTION_LABEL
    infrastructure_stacks: list[str] = field(
        default_factory=lambda: list(DEFAULT_INFRASTRUCTURE_STACKS)
    )
    infra_ui_services: list[str] = field(
        default_factory=lambda: list(DEFAULT_INFRA_UI_SERVICES)
    )
    ignore_mountpoints: list[str] = field(
        default_factory=platform_defaults.ignore_mountpoints
    )
    thresholds: Thresholds = field(default_factory=Thresholds)
    health: HealthConfig = field(default_factory=HealthConfig)
    traefik: TraefikApiConfig = field(default_factory=TraefikApiConfig)


def _section(data: dict, *keys: str) -> dict:
    node = data
    for key in keys:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _list_setting(value: object, default: list) -> list:
    """Coerce a list-shaped config *value* to a list, forgiving one typo.

    ``ignore_mountpoints = "/System/Volumes/"`` is valid TOML for a key that
    is meant to hold a list, and it is a plausible mistake -- there is only
    one value, so why wrap it in brackets? ``list(...)`` on that string does
    not raise; it splits it into its characters, and the lone "/" element
    then prefix-matches everything, blanking a whole config-driven list with
    no error. A bare string is special-cased into a one-element list, rather
    than rejected, because what the author meant is unambiguous. Anything
    else that is not a list -- a number, a table, a missing key (``None``) --
    is not that typo, so it falls back to *default* instead of guessing.

    Always returns a fresh list: neither *value* nor *default* is handed back
    by reference, so the caller's copy can never be mutated through this one.
    """
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    return list(default)


def _health_config(data: dict) -> HealthConfig:
    """Parse the [health] block. A malformed value falls back to its default."""
    health = _section(data, "health")
    defaults = HealthConfig()

    try:
        budget = float(health.get("budget", defaults.budget))
    except (TypeError, ValueError):
        budget = defaults.budget

    timeouts = dict(DEFAULT_HEALTH_TIMEOUTS)
    for key, value in _section(data, "health", "timeout").items():
        try:
            timeouts[key] = float(value)
        except (TypeError, ValueError):
            continue

    enabled = health.get("enabled")
    kinds = list(enabled) if isinstance(enabled, list) else list(DEFAULT_HEALTH_KINDS)

    expectations = []
    raw_expectations = _section(data, "health", "dns").get("expect", [])
    if isinstance(raw_expectations, list):
        for entry in raw_expectations:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            addresses = entry.get("addresses", [])
            expectations.append(
                DnsExpectation(
                    name=str(entry["name"]),
                    addresses=list(addresses) if isinstance(addresses, list) else [],
                )
            )

    return HealthConfig(
        budget=budget, timeouts=timeouts, enabled=kinds, dns_expect=expectations
    )


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
    docker = _section(data, "docker")
    services = _section(data, "services")
    infra = docker.get("infrastructure_stacks", services.get("infrastructure", None))
    infra_uis = docker.get("infra_ui_services", None)
    resources = _section(data, "resources")
    # Presence, not truthiness: an explicit [] means "hide nothing" and must not
    # be replaced by the platform defaults it was written to override.
    ignore = resources.get("ignore_mountpoints", None)
    traefik_section = _section(data, "traefik")
    traefik = TraefikApiConfig(
        url=traefik_section.get("url") or None,
        cert=traefik_section.get("cert") or None,
        key=traefik_section.get("key") or None,
        ca=traefik_section.get("ca") or None,
    )
    return Config(
        width=int(data.get("width", 80)),
        docker_timeout=float(docker.get("timeout", 1.5)),
        critical_services=_list_setting(services.get("critical"), []),
        description_label=str(docker.get("description_label",
                                        DEFAULT_DESCRIPTION_LABEL)),
        infrastructure_stacks=_list_setting(infra, DEFAULT_INFRASTRUCTURE_STACKS),
        infra_ui_services=_list_setting(infra_uis, DEFAULT_INFRA_UI_SERVICES),
        ignore_mountpoints=_list_setting(ignore, platform_defaults.ignore_mountpoints()),
        thresholds=thresholds,
        health=_health_config(data),
        traefik=traefik,
    )
