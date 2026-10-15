"""The host state the collector writes and the panel reads.

One file, one schema. The collector runs with privileges the login user does
not have; this module is the whole contract between the two, so both sides
import it and neither re-spells a field name.

Numbers, states and timestamps only. Nothing here may carry log text, a path
or a client address: the file is world-readable on purpose.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1
STATE_PATH = "/run/terminal-status-panel/host.json"


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
    def from_dict(cls, data: dict) -> HostState:
        """Rebuild from the JSON form. Raises ValueError on a foreign schema."""
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError(f"unknown schema {data.get('schema')!r}")
        vrrp = data.get("vrrp")
        apache = data.get("apache")
        updates = data.get("updates")
        return cls(
            written_at=float(data["written_at"]),
            collector=str(data.get("collector", "")),
            vrrp=VrrpState(
                source=vrrp["source"],
                instances=[VrrpInstance(**entry) for entry in vrrp.get("instances", [])],
            )
            if vrrp
            else None,
            apache=ApacheState(**apache) if apache else None,
            updates=UpdateCounts(**updates) if updates else None,
            events={k: EventCount(**v) for k, v in (data.get("events") or {}).items()},
            events_window_s=int(data.get("events_window_s", 12 * 3600)),
            errors=dict(data.get("errors") or {}),
        )


#: Two collector periods and change. Older than this, a figure is a memory.
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
