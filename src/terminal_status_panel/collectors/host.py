"""Host kinds: the THIS HOST lines of HEALTH.

``systemd`` asks systemctl directly -- it needs no privilege. ``vrrp`` and
``apache`` read what the collector wrote: keepalived runs as root, and a
request rate needs a window the login path cannot wait for.
"""

from __future__ import annotations

import subprocess

from ..config import ApacheProbe, VrrpProbe
from ..model.host import HostCheck, HostStateRead

HOST_KINDS = ("vrrp", "apache", "systemd")

_ORDER = ("ok", "warn", "crit")

_MISSING = {
    "missing": "host state unavailable (collector not running?)",
    "unreadable": "host state unreadable",
    "unknown schema": "host state unavailable (unknown schema)",
}


def _number(value: object) -> float | None:
    """*value* as a number, or None: the state file's field types are not checked."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _state_problem(kind: str, read: HostStateRead) -> HostCheck | None:
    if read.problem:
        return HostCheck(
            kind=kind, status="error", summary=_MISSING.get(read.problem, read.problem)
        )
    return None


def probe_systemd(timeout: float, ignore: tuple[str, ...]) -> HostCheck:
    """Failed units, by name. ``systemctl`` missing is *not applicable*."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["systemctl", "list-units", "--failed", "--plain", "--no-legend", "--no-pager"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return HostCheck(kind="systemd", status="na", summary="n/a here")
    names = [line.split()[0] for line in proc.stdout.splitlines() if line.strip()]
    failed = [name for name in names if name not in ignore]
    if not failed:
        return HostCheck(kind="systemd", status="ok", summary="0 failed")
    return HostCheck(kind="systemd", status="crit", summary=f"{len(failed)} failed", details=failed)


def _lead_status(lead: int, probe: VrrpProbe) -> str:
    if lead >= probe.lead_critical:
        return "crit"
    if lead >= -probe.lead_warning:
        return "warn"
    return "ok"


def check_vrrp(read: HostStateRead, probe: VrrpProbe) -> HostCheck:
    """One clause per instance: who holds it here, priorities, lead where known."""
    problem = _state_problem("vrrp", read)
    if problem:
        return problem
    vrrp = read.state.vrrp if read.state else None
    if vrrp is None or not vrrp.instances:
        return HostCheck(kind="vrrp", status="na", summary="n/a here")
    worst = "ok"
    clauses = []
    for inst in vrrp.instances:
        status = "ok"
        if inst.state in ("FAULT", "STOP", "DELETED"):
            status = "crit"
        clause = f"{inst.name} {inst.state}"
        effective = _number(inst.effective_priority)
        base = _number(inst.base_priority)
        if effective is not None and base is not None:
            clause += f" · {effective:.0f}/{base:.0f}"
        master = _number(inst.master_priority)
        if inst.state == "BACKUP" and effective is not None and master is not None:
            lead = int(effective - master)
            clause += f" · lead {lead:+d}".replace("-", "−")
            status = max(status, _lead_status(lead, probe), key=_ORDER.index)
        clauses.append(clause)
        worst = max(worst, status, key=_ORDER.index)
    if vrrp.source == "ip-addr":
        clauses.append("(addresses only)")
    return HostCheck(kind="vrrp", status=worst, summary="   ".join(clauses))


def _band(value: float | None, warning: float | None, critical: float | None) -> str:
    if value is None or warning is None or critical is None:
        return "ok"
    if value >= critical:
        return "crit"
    if value >= warning:
        return "warn"
    return "ok"


def check_apache(read: HostStateRead, probe: ApacheProbe) -> HostCheck:
    """Requests, slots, workers -- as one line; colour from the configured bands."""
    if probe.url is None:
        return HostCheck(kind="apache", status="na", summary="n/a here")
    problem = _state_problem("apache", read)
    if problem:
        return problem
    apache = read.state.apache if read.state else None
    if apache is None:
        error = (read.state.errors or {}).get("apache") if read.state else None
        return HostCheck(
            kind="apache", status="error", summary=f"no data ({error or 'not collected'})"
        )
    requests = _number(apache.requests_per_s)
    slots_share = _number(apache.slots_busy)
    workers_share = _number(apache.workers_busy)
    slots = None if slots_share is None else slots_share * 100
    workers = None if workers_share is None else workers_share * 100
    rate = "—" if requests is None else f"{requests:.0f}"
    parts = [f"{rate} req/s"]
    if slots is not None:
        parts.append(f"slots {slots:.0f} %")
    if workers is not None:
        parts.append(f"workers {workers:.0f} %")
    if apache.slots_finishing:
        parts.append(f"{apache.slots_finishing} finishing")
    status = max(
        _band(requests, probe.requests_warning, probe.requests_critical),
        _band(slots, probe.slots_warning, probe.slots_critical),
        key=_ORDER.index,
    )
    return HostCheck(kind="apache", status=status, summary=" · ".join(parts))
