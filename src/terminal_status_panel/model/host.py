"""The host state the collector writes and the panel reads.

One file, one schema. The collector runs with privileges the login user does
not have; this module is the whole contract between the two, so both sides
import it and neither re-spells a field name.

Numbers, states and timestamps only. Nothing here may carry log text, a path
or a client address: the file is world-readable on purpose.
"""

from __future__ import annotations

import functools
import math
from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any, TypeVar, get_args, get_type_hints

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

_Block = TypeVar("_Block", bound="DataclassInstance")

SCHEMA_VERSION = 1
STATE_PATH = "/run/terminal-status-panel/host.json"


class UnknownSchemaError(ValueError):
    """The file was written under a schema this code does not read."""


def _is(value: Any, kind: object) -> bool:
    """Whether *value* is a JSON value of the scalar type *kind*.

    bool is never a number here, although Python counts it as an int; an int
    is a float, since JSON writes ``30.0`` and ``30`` alike.
    """
    if kind is type(None):
        return value is None
    if isinstance(value, bool):
        return False
    if kind is int:
        return isinstance(value, int)
    if kind is float:
        return isinstance(value, int | float)
    if kind is str:
        return isinstance(value, str)
    return False  # a type this check does not know is refused, not waved through


def _check(name: str, value: Any, hint: object) -> None:
    """Raise ValueError unless *value* fits *hint* (a scalar or ``X | None``)."""
    if not any(_is(value, kind) for kind in get_args(hint) or (hint,)):
        raise ValueError(f"{name}: unexpected {type(value).__name__}")


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _entry(cls: type[_Block], value: Any) -> _Block:
    """Build one block entry from *value*, ignoring keys the dataclass lacks.

    Unknown keys are dropped rather than refused, so an older panel can read a
    newer collector's file. Anything that is not a mapping, and any known field
    of the wrong type, is a ValueError: the readers compare and format these
    values, and a string where a count belongs would raise there instead.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} expects an object, got {type(value).__name__}")
    hints = _hints(cls)
    known = {f.name for f in fields(cls)}
    kept = {key: val for key, val in value.items() if key in known}
    for key, val in kept.items():
        _check(f"{cls.__name__}.{key}", val, hints[key])
    return cls(**kept)


@dataclass
class VrrpInstance:
    """One VRRP instance as this host's keepalived reports it."""

    name: str
    state: str  # MASTER | BACKUP | FAULT | INIT | STOP | DELETED | UNKNOWN
    base_priority: int | None = None
    effective_priority: int | None = None
    #: The priority the current master advertises. Meaningful on a BACKUP only:
    #: a MASTER never hears the backups, so it cannot know how far ahead it is.
    master_priority: int | None = None

    @property
    def holds(self) -> bool:
        """Whether this host holds the instance's addresses."""
        return self.state == "MASTER"

    @property
    def lead(self) -> int | None:
        """How far this BACKUP is from taking over (negative: behind), or None."""
        if self.state != "BACKUP":
            return None
        if self.effective_priority is None or self.master_priority is None:
            return None
        return self.effective_priority - self.master_priority


@dataclass
class VrrpState:
    """Every VRRP instance on this host, and where the answer came from."""

    source: str  # keepalived-json | keepalived-data | ip-addr
    instances: list[VrrpInstance] = field(default_factory=list)


@dataclass
class ApacheState:
    """Apache httpd as mod_status reports it, over the collector's window."""

    window_s: float
    requests_per_s: float | None = None
    slots_busy: float | None = None  # share of scoreboard slots not open
    workers_busy: float | None = None  # share of thread slots serving a request
    slots_finishing: int | None = None  # slots held by a gracefully finishing process


@dataclass
class UpdateCounts:
    """Pending package updates, as a simulated upgrade counts them."""

    checked_at: float
    available: int
    security: int
    source: str  # apt-simulate


@dataclass
class EventCount:
    """How often one kind of host event happened in the window, and when last."""

    count: int
    last: float | None = None


@dataclass
class HostState:
    """The whole state file."""

    written_at: float
    collector: str
    vrrp: VrrpState | None = None
    apache: ApacheState | None = None
    updates: UpdateCounts | None = None
    events: dict[str, EventCount] = field(default_factory=dict)
    events_window_s: int = 12 * 3600
    errors: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """The JSON-ready form, schema version included."""
        data = asdict(self)
        data["schema"] = SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Any) -> HostState:
        """Rebuild from the JSON form.

        Raises UnknownSchemaError for a foreign schema and ValueError for any
        other malformed content, so the reader can tell the two apart.
        """
        if not isinstance(data, dict):
            raise ValueError("state file must hold a JSON object")
        if data.get("schema") != SCHEMA_VERSION:
            raise UnknownSchemaError(f"unknown schema {data.get('schema')!r}")
        written_at = data["written_at"]
        _check("written_at", written_at, float)
        if not math.isfinite(written_at):
            raise ValueError("written_at must be finite")
        collector = data.get("collector", "")
        _check("collector", collector, str)
        events_window_s = data.get("events_window_s", 12 * 3600)
        _check("events_window_s", events_window_s, int)
        vrrp = data.get("vrrp")
        apache = data.get("apache")
        updates = data.get("updates")
        events = data.get("events") or {}
        if not isinstance(events, dict):
            raise ValueError("events must be an object")
        errors = data.get("errors") or {}
        if not isinstance(errors, dict):
            raise ValueError("errors must be an object")
        for name, code in errors.items():
            _check(f"errors.{name}", code, str | None)
        return cls(
            written_at=float(written_at),
            collector=collector,
            vrrp=_vrrp(vrrp) if vrrp is not None else None,
            apache=_entry(ApacheState, apache) if apache is not None else None,
            updates=_entry(UpdateCounts, updates) if updates is not None else None,
            events={name: _entry(EventCount, value) for name, value in events.items()},
            events_window_s=events_window_s,
            errors=dict(errors),
        )


def _vrrp(value: Any) -> VrrpState:
    """The VRRP block: a source and a list of instance objects."""
    if not isinstance(value, dict):
        raise ValueError("vrrp must be an object")
    instances = value.get("instances", [])
    if not isinstance(instances, list):
        raise ValueError("vrrp.instances must be a list")
    source = value["source"]
    _check("vrrp.source", source, str)
    return VrrpState(
        source=source,
        instances=[_entry(VrrpInstance, entry) for entry in instances],
    )


#: Four collector periods of 30 s. Older than this, a figure is a memory.
STALE_AFTER_SECONDS = 120

#: The journal event kinds the collector counts, in render order.
HOST_EVENT_KINDS = ("oom_kill", "vrrp_state", "apache_slots_full", "unit_failed", "apache_reload")


@dataclass
class HostStateRead:
    """The state, its age, or why there is none."""

    state: HostState | None = None
    age_s: float | None = None
    problem: str | None = None  # missing | unreadable | unknown schema

    @property
    def stale(self) -> bool:
        """Whether the figures are too old to be read as current."""
        return self.age_s is not None and self.age_s > STALE_AFTER_SECONDS


@dataclass
class HostCheck:
    """One line of the THIS HOST block."""

    kind: str  # vrrp | apache | systemd
    status: str  # ok | warn | crit | na | error
    summary: str
    details: list[str] = field(default_factory=list)
