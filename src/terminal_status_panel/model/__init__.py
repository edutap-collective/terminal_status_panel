"""Dataclasses shared by collectors and renderers.

All aggregate fields default to empty/None so a failed collector degrades
gracefully instead of raising.

Split by domain -- system, docker, health, traefik -- and re-exported here, so
`from ..model import ServiceStatus` keeps working and no caller has to know
which file a dataclass lives in. The model is this package's internal contract
surface; where a field is defined is a matter of navigation, not of interface.

There is still no public Python API (see the package docstring): this module
being importable is not a promise that it is stable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .docker import (
    TROUBLE_SEVERITIES,
    TROUBLE_WINDOW_SECONDS,
    DockerDiskUsage,
    JobRun,
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    SwarmNode,
    TroubleEntry,
)
from .health import ClusterMember, ClusterService, DnsCheck, HealthInfo, PeerReachability
from .system import (
    FilesystemUsage,
    ProcessInfo,
    ProcessSnapshot,
    ResourceUsage,
    SystemInfo,
    UpdateInfo,
)
from .traefik import (
    TraefikEntrypoint,
    TraefikInfo,
    TraefikMiddleware,
    TraefikRouter,
    TraefikServiceRef,
)


@dataclass
class PanelData:
    """Everything the collectors gathered, ready for the renderers.

    Defined here rather than in one of the four domain modules because it is
    the one type that belongs to all of them, and putting it in any single one
    would make that module import its three siblings.
    """

    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
    updates: UpdateInfo | None = None
    health: HealthInfo | None = None
    traefik: TraefikInfo | None = None
    processes: ProcessSnapshot | None = None


__all__ = [
    "TROUBLE_SEVERITIES",
    "TROUBLE_WINDOW_SECONDS",
    "ClusterMember",
    "ClusterService",
    "DnsCheck",
    "DockerDiskUsage",
    "FilesystemUsage",
    "HealthInfo",
    "JobRun",
    "PanelData",
    "PeerReachability",
    "ProcessInfo",
    "ProcessSnapshot",
    "ResourceUsage",
    "ServiceStatus",
    "ServiceTask",
    "SwarmInfo",
    "SwarmNode",
    "SystemInfo",
    "TraefikEntrypoint",
    "TraefikInfo",
    "TraefikMiddleware",
    "TraefikRouter",
    "TraefikServiceRef",
    "TroubleEntry",
    "UpdateInfo",
]
