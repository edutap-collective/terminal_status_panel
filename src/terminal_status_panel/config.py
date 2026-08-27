"""Configuration: built-in defaults overridable by an optional TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import platform_defaults

#: Docker service label read as the per-service description column.
#: Vendor-neutral, as befits a public package.
DEFAULT_DESCRIPTION_LABEL = "status.description"

#: Label that says which services belong in one row. Where it is absent the
#: name heuristic in the renderer decides, which is deliberately conservative:
#: it collapses a trailing `_<digits>` but not `-<digits>`, because a stack
#: named `PostgreSQL-18` would otherwise be mutilated. This label removes the
#: guessing wherever a deployment can simply state the answer.
DEFAULT_GROUP_LABEL = "status.group"

#: The key this panel read before the rename. Still honoured as a fallback by
#: the Docker collector, so installations that set it -- and never set
#: ``docker.description_label`` -- keep their descriptions without changing
#: anything.
LEGACY_DESCRIPTION_LABEL = "lmu.service.description"

DEFAULT_CONFIG_PATH = "/etc/terminal-status-panel/config.toml"


@dataclass
class Thresholds:
    """Where a measurement stops being ok and starts being worth showing."""

    memory_warning: float = 75.0
    memory_critical: float = 90.0
    swap_warning: float = field(default_factory=platform_defaults.swap_warning)
    filesystem_warning: float = 80.0
    filesystem_critical: float = 90.0
    load_warning: float = 0.8  # per-CPU multiplier
    load_critical: float = 1.0  # per-CPU multiplier


@dataclass
class DnsExpectation:
    """A name the operator expects to resolve, and to what."""

    name: str
    addresses: list[str] = field(default_factory=list)


@dataclass
class HealthConfig:
    """What the CLUSTER HEALTH section may probe, and how long it may take."""

    budget: float = 5.0
    timeouts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HEALTH_TIMEOUTS))
    enabled: list[str] = field(default_factory=lambda: list(DEFAULT_HEALTH_KINDS))
    dns_expect: list[DnsExpectation] = field(default_factory=list)


DEFAULT_INFRASTRUCTURE_STACKS = [
    "postgresql",
    "postgres",
    "kafka",
    "mongodb",
    "rustfs",
    "portainer",
    "traefik",
    "registry",
    "minio",
    "redis",
    "valkey",
    "mariadb",
    "mysql",
    "elasticsearch",
    "bugsink",
    # The controller that drives scheduled jobs by scaling their services up
    # (https://github.com/crazy-max/swarm-cronjob). Infrastructure rather than
    # an application: it carries no data and serves no user. Both separators
    # are listed because the keys are matched as substrings -- a Swarm stack
    # is usually "swarm-cronjob", a Compose project "swarm_cronjob".
    "swarm-cronjob",
    "swarm_cronjob",
]

# Admin web UIs for infrastructure services. Matched case-insensitively against
# the stack name *and* the service name; matches are grouped into the
# "infra-uis" pseudo stack and win over DEFAULT_INFRASTRUCTURE_STACKS.
DEFAULT_INFRA_UI_SERVICES = [
    "kafbat-ui",
    "kafka-ui",
    "kafdrop",
    "cloudbeaver",
    "pgadmin",
    "adminer",
    "mongo-express",
    "mongo-gui",
    "rustfs-console",
    "rustfs-ui",
    "s3-browser",
    "s3browser",
    "s3manager",
    "redisinsight",
    "redis-commander",
    "dozzle",
    "kibana",
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
    """Everything under ``[traefik]``.

    The API cross-check is dormant today: the dashboard router requires a
    client certificate signed by the web frontend's CA, and the Ansible role
    issues only app-server ones. ``links`` is independent of it.
    """

    url: str | None = None
    cert: str | None = None
    key: str | None = None
    ca: str | None = None
    #: Entrypoint name to the base URL its services are reached at. The panel
    #: cannot derive this: Traefik's routers match on path alone, so no
    #: hostname appears in the routing configuration at all.
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """Everything the panel reads from its config file, with defaults."""

    width: int = 80
    docker_timeout: float = 1.5
    #: Its own deadline for `/system/df`, and larger than `docker_timeout` on
    #: purpose. The reading was measured at 510 ms against a daemon holding 47
    #: images and 185 volumes, and it grows with the number of objects -- so on
    #: a busy node it can outlast the timeout every other Docker call runs on.
    #: It is spent on a separate client, so overrunning it costs one line
    #: rather than the whole DOCKER INFOS section.
    docker_df_timeout: float = 4.0
    critical_services: list[str] = field(default_factory=list)
    description_label: str = DEFAULT_DESCRIPTION_LABEL
    group_label: str = DEFAULT_GROUP_LABEL
    #: Whether the DOCKER INFOS rows carry the image they run. It is the one
    #: column that answers "which version is deployed here", and the one that
    #: costs the description its width on a narrow terminal -- hence a switch.
    show_image: bool = True
    infrastructure_stacks: list[str] = field(
        default_factory=lambda: list(DEFAULT_INFRASTRUCTURE_STACKS)
    )
    infra_ui_services: list[str] = field(default_factory=lambda: list(DEFAULT_INFRA_UI_SERVICES))
    ignore_mountpoints: list[str] = field(default_factory=platform_defaults.ignore_mountpoints)
    thresholds: Thresholds = field(default_factory=Thresholds)
    health: HealthConfig = field(default_factory=HealthConfig)
    traefik: TraefikApiConfig = field(default_factory=TraefikApiConfig)
    #: Seconds to sample process CPU over. Cost on a login path, hence a dial:
    #: 0.3 s over roughly 400 processes measures at about 0.32 s wall clock.
    #: Zero or less disables the CPU ranking entirely.
    process_sample: float = 0.3
    #: Rows per list in the process block. Zero turns the block off, and with
    #: it the sampling window -- the cost this switch exists to remove.
    top_processes: int = 5
    #: Refresh interval for --follow when the health section is not among the
    #: requested ones.
    follow_interval: float = 5.0
    #: Refresh interval for --follow when it is. The health checks run docker
    #: exec probes -- the Kafka one alone carries roughly 2.6 s of JVM startup
    #: -- so a five-second cadence would keep a JVM starting forever.
    follow_health_interval: float = 20.0
    #: Values in the config file that could not be used, with what was used
    #: instead. Empty for a valid file and for a missing one -- absent is not
    #: malformed. Shown by ``--debug``; the panel itself renders regardless.
    problems: list[ConfigProblem] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigProblem:
    """One config value that could not be used, and what was used instead.

    Collected rather than raised. A status panel that refuses to render
    because one threshold is misspelled is worse than one that renders with
    the default and says so -- and until this existed, a single bad value
    made ``load_config`` raise, ``main`` swallow it, and the login shell print
    nothing whatsoever.
    """

    #: Dotted path as it appears in the file: ``thresholds.memory.warning``.
    key: str
    #: What was found there, as ``repr`` -- quoted, so `"75"` and `75` differ.
    found: str
    #: The value used instead, as ``repr``.
    used: str
    #: Why it could not be used, in a few words.
    reason: str

    def __str__(self) -> str:
        """One line, as ``--debug`` prints it."""
        return f"{self.key}: {self.reason} (found {self.found}, using {self.used})"


#: Strings TOML would have accepted as a boolean if they had not been quoted.
#: `bool("false")` is `True`, so reading a quoted boolean the way Python does
#: silently inverts what the file says -- the one misreading worth special
#: handling rather than a fallback.
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})


class _Reader:
    """Reads typed values out of the parsed TOML, and never raises.

    Every accessor takes the value's dotted path, so a fallback can name
    itself without the call site repeating the name in a message. Values that
    are simply absent are not problems -- the defaults are the documented
    behaviour, and a config file is optional.
    """

    def __init__(self) -> None:
        self.problems: list[ConfigProblem] = []

    def _note(self, key: str, found: object, used: object, reason: str) -> None:
        self.problems.append(
            ConfigProblem(key=key, found=repr(found), used=repr(used), reason=reason)
        )

    def note_file(self, path: str, reason: str) -> None:
        """Record an unreadable or unparseable file as a problem in its own right."""
        self.problems.append(
            ConfigProblem(key=path, found="unreadable", used="built-in defaults", reason=reason)
        )

    def number(
        self,
        section: dict,
        key: str,
        default: float,
        *,
        minimum: float | None = None,
    ) -> float:
        """A float, falling back on anything that is not one.

        ``minimum`` rejects rather than clamps: a timeout of 0 is not a small
        timeout, it is a value nobody can have meant, and clamping it to the
        smallest legal one would hide the typo behind plausible behaviour.
        """
        name = key.rsplit(".", 1)[-1]
        if name not in section:
            return default
        raw = section[name]
        if isinstance(raw, bool):
            # `True` is an int in Python, and would silently become 1.0.
            self._note(key, raw, default, "expected a number, found a boolean")
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self._note(key, raw, default, "expected a number")
            return default
        if value != value or value in (float("inf"), float("-inf")):
            self._note(key, raw, default, "not a finite number")
            return default
        if minimum is not None and value < minimum:
            self._note(key, raw, default, f"must be at least {minimum}")
            return default
        return value

    def integer(
        self,
        section: dict,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        clamp_to: int | None = None,
    ) -> int:
        """A whole number.

        ``clamp_to`` is for the documented case: README says a negative
        ``top_processes`` means zero. That is the file being read as written,
        not a misreading, so it is applied silently. ``minimum`` is the other
        case -- a value outside the range falls back and is reported.
        """
        name = key.rsplit(".", 1)[-1]
        if name not in section:
            return default
        raw = section[name]
        if isinstance(raw, bool):
            self._note(key, raw, default, "expected a whole number, found a boolean")
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self._note(key, raw, default, "expected a whole number")
            return default
        if clamp_to is not None and value < clamp_to:
            return clamp_to
        if minimum is not None and value < minimum:
            self._note(key, raw, default, f"must be at least {minimum}")
            return default
        return value

    def flag(self, section: dict, key: str, default: bool) -> bool:
        """A boolean, forgiving the quoted spellings TOML would have accepted."""
        name = key.rsplit(".", 1)[-1]
        if name not in section:
            return default
        raw = section[name]
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            word = raw.strip().lower()
            if word in _TRUE_WORDS:
                return True
            if word in _FALSE_WORDS:
                return False
        self._note(key, raw, default, "expected true or false")
        return default

    def text(self, section: dict, key: str, default: str) -> str:
        """A string. A number here is a mistake, not something to stringify."""
        name = key.rsplit(".", 1)[-1]
        if name not in section:
            return default
        raw = section[name]
        if isinstance(raw, str):
            return raw
        self._note(key, raw, default, "expected a string")
        return default


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


def _health_config(data: dict, reader: _Reader) -> HealthConfig:
    """Parse the [health] block. A malformed value falls back to its default."""
    health = _section(data, "health")
    defaults = HealthConfig()

    budget = reader.number(health, "health.budget", defaults.budget, minimum=0.1)

    timeouts = dict(DEFAULT_HEALTH_TIMEOUTS)
    timeout_section = _section(data, "health", "timeout")
    for key in timeout_section:
        timeouts[key] = reader.number(
            timeout_section,
            f"health.timeout.{key}",
            DEFAULT_HEALTH_TIMEOUTS.get(key, defaults.budget),
            minimum=0.0,
        )

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

    return HealthConfig(budget=budget, timeouts=timeouts, enabled=kinds, dns_expect=expectations)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from *path*, or from the default location.

    Never raises on a missing or unreadable file — falls back to defaults.
    """
    target = os.fspath(path) if path is not None else DEFAULT_CONFIG_PATH
    reader = _Reader()
    try:
        with open(target, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        # Absent is the normal case: the config file is optional and the
        # defaults are the documented behaviour. Not a problem to report.
        return Config()
    except (PermissionError, tomllib.TOMLDecodeError, OSError) as exc:
        # Present but unusable is a different thing entirely, and the one a
        # reader needs told: the file they edited is having no effect.
        reader.note_file(target, f"{type(exc).__name__}: {exc}")
        return Config(problems=reader.problems)

    t = Thresholds()
    mem = _section(data, "thresholds", "memory")
    swap = _section(data, "thresholds", "swap")
    fs = _section(data, "thresholds", "filesystem")
    load = _section(data, "thresholds", "load")
    thresholds = Thresholds(
        memory_warning=reader.number(mem, "thresholds.memory.warning", t.memory_warning),
        memory_critical=reader.number(mem, "thresholds.memory.critical", t.memory_critical),
        swap_warning=reader.number(swap, "thresholds.swap.warning", t.swap_warning),
        filesystem_warning=reader.number(fs, "thresholds.filesystem.warning", t.filesystem_warning),
        filesystem_critical=reader.number(
            fs, "thresholds.filesystem.critical", t.filesystem_critical
        ),
        load_warning=reader.number(load, "thresholds.load.warning", t.load_warning),
        load_critical=reader.number(load, "thresholds.load.critical", t.load_critical),
    )
    docker = _section(data, "docker")
    services = _section(data, "services")
    infra = docker.get("infrastructure_stacks", services.get("infrastructure", None))
    infra_uis = docker.get("infra_ui_services", None)
    resources = _section(data, "resources")
    # Presence, not truthiness: an explicit [] means "hide nothing" and must not
    # be replaced by the platform defaults it was written to override.
    ignore = resources.get("ignore_mountpoints", None)
    process_sample = reader.number(resources, "resources.process_sample", 0.3)
    # clamp_to rather than minimum: README documents a negative value as
    # meaning 0, so that is the file read as written, not a misreading.
    top_processes = reader.integer(resources, "resources.top_processes", 5, clamp_to=0)
    traefik_section = _section(data, "traefik")
    links: dict[str, str] = {}
    for name, value in _section(data, "traefik", "links").items():
        # Dropped rather than rejected, like every other malformed value here:
        # this file must never fail a login. An entrypoint whose base is
        # unusable gets no links, which is the same state as not configuring
        # it -- and better than a link nobody can trust.
        if not isinstance(value, str):
            continue
        url = value.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlsplit(url)
        if not parsed.hostname:
            # A scheme with nothing to reach -- `http://`, `http://:8080` --
            # is not a base anything can be joined onto.
            continue
        if parsed.query or parsed.fragment:
            # `link_for` only ever appends a path, so a query or fragment on
            # the base would land inside the joined URL instead of where it
            # was written: `https://x.de?q=1` + `/a` -> `https://x.de?q=1/a`.
            continue
        links[str(name)] = url
    traefik = TraefikApiConfig(
        url=traefik_section.get("url") or None,
        cert=traefik_section.get("cert") or None,
        key=traefik_section.get("key") or None,
        ca=traefik_section.get("ca") or None,
        links=links,
    )
    health_config = _health_config(data, reader)
    follow_section = _section(data, "follow")
    follow_interval = reader.number(follow_section, "follow.interval", 5.0, minimum=0.1)
    follow_health_interval = reader.number(
        follow_section, "follow.health_interval", 20.0, minimum=0.1
    )
    return Config(
        # A width below 20 leaves no room for a single column of content, and
        # a timeout of 0 aborts every call before it starts: both are values
        # nobody can have meant, so they fall back and say so.
        width=reader.integer(data, "width", 80, minimum=20),
        docker_timeout=reader.number(docker, "docker.timeout", 1.5, minimum=0.1),
        docker_df_timeout=reader.number(docker, "docker.df_timeout", 4.0, minimum=0.1),
        critical_services=_list_setting(services.get("critical"), []),
        description_label=reader.text(
            docker, "docker.description_label", DEFAULT_DESCRIPTION_LABEL
        ),
        group_label=reader.text(docker, "docker.group_label", DEFAULT_GROUP_LABEL),
        show_image=reader.flag(docker, "docker.show_image", True),
        infrastructure_stacks=_list_setting(infra, DEFAULT_INFRASTRUCTURE_STACKS),
        infra_ui_services=_list_setting(infra_uis, DEFAULT_INFRA_UI_SERVICES),
        ignore_mountpoints=_list_setting(ignore, platform_defaults.ignore_mountpoints()),
        thresholds=thresholds,
        health=health_config,
        traefik=traefik,
        process_sample=process_sample,
        top_processes=top_processes,
        follow_interval=follow_interval,
        follow_health_interval=follow_health_interval,
        problems=reader.problems,
    )
