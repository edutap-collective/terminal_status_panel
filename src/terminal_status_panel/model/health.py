"""Clustered services, peers and DNS.

Every aggregate here distinguishes three states rather than two: measured
healthy, measured broken, and not measured at all. `None` is the third one
and is never a synonym for `False` -- see the field comments.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClusterMember:
    """One member of a clustered infrastructure service.

    ``healthy`` is tri-state on purpose: ``None`` means *not observable*. The
    MongoDB probe, for instance, learns the set members but not their state,
    and the panel must not render an unmeasured ✅.
    """

    name: str
    node: str | None = None  # derived Swarm hostname, when derivable
    role: str | None = None  # primary / secondary / leader / voter / observer / peer
    healthy: bool | None = None
    detail: str | None = None  # kind-specific: LSN, brick path, endpoint
    warning: str | None = None  # short note: "lag", "→ primary"
    #: How far a lagging member is behind, in bytes. ``None`` where there is no
    #: lag or the distance could not be computed.
    #:
    #: A number, not a formatted string: the collector measures, the renderer
    #: presents -- the same split ``ProcessInfo.memory_bytes`` already follows.
    #: Formatting here would make a collector import from ``render``, which no
    #: collector in this package does.
    #:
    #: Deliberately no threshold anywhere. Any LSN difference used to warn, and
    #: on an active cluster the primary's LSN moves constantly -- so the notice
    #: was almost always present and was accordingly ignored. A boundary would
    #: only move that problem; we do not know at what point a byte count hurts
    #: on a given cluster. The number lets the reader judge.
    lag_bytes: int | None = None


@dataclass
class ClusterService:
    """State of one clustered infrastructure service as seen from this node."""

    kind: str  # postgres | mongodb | kafka | glusterfs | rustfs
    name: str | None = None  # stack name, cluster id, volume name
    applicable: bool = True  # False when this node runs no member
    reachable: bool = False
    leader: str | None = None  # primary / controller leader; None when leaderless
    quorum_ok: bool | None = None
    detail: str | None = None  # service-level note, e.g. Kafka follower lag
    members: list[ClusterMember] = field(default_factory=list)
    error: str | None = None


@dataclass
class PeerReachability:
    """Whether one configured peer answered, and how it was asked."""

    name: str
    method: str  # wireguard | tcp
    ok: bool = False
    detail: str | None = None  # handshake age or probed port
    rx_bytes: int | None = None  # None for the tcp fallback, which cannot know
    tx_bytes: int | None = None
    endpoint: str | None = None  # as WireGuard resolved it, host:port
    family: str | None = None  # IPv4 | IPv6, derived from endpoint

    @property
    def one_way(self) -> bool:
        """We send and nothing comes back.

        The signature of a filtered or mis-keyed tunnel, and a different fault
        from a peer that is simply gone — there both counters stand still. The
        distinction decides where to look: a packet filter and a key mismatch
        both produce this, a dead host does not.

        Both counters must be present. A missing one means the transport could
        not be read (the TCP fallback, or a malformed dump), and answering from
        that would turn absent data into a diagnosis.
        """
        if self.rx_bytes is None or self.tx_bytes is None:
            return False
        return self.tx_bytes > 0 and self.rx_bytes == 0


@dataclass
class DnsCheck:
    """One name-resolution check and its verdict."""

    label: str
    ok: bool | None = None  # None = warning (inconsistent, not broken)
    detail: str = ""


@dataclass
class HealthInfo:
    """The CLUSTER HEALTH section: clustered services, peers and DNS checks."""

    clusters: list[ClusterService] = field(default_factory=list)
    peers: list[PeerReachability] = field(default_factory=list)
    dns: list[DnsCheck] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    # False when the clusters check never ran at all (no Docker client, or no
    # kinds enabled). An empty ``clusters`` list means "nothing found" only
    # when this is True; otherwise it means "not attempted".
    clusters_probed: bool = False
    # False when there was nothing to probe: no peer names and no WireGuard
    # answer. An empty ``peers`` list means "no peers" only when this is True.
    peers_probed: bool = False
    # False when no collector produced this HealthInfo at all — the renderer's
    # own fallback for a missing section, or a hand-built instance. Unlike the
    # two flags above it cannot distinguish anything within a real run:
    # ``collect_health`` always registers the DNS check, so a collected
    # HealthInfo always has this True. It exists so that an empty ``dns`` list
    # on a default instance cannot read as "checked, nothing configured".
    dns_probed: bool = False
