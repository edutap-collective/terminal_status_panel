"""Collect system identity info (OS, kernel, host, uptime, user, IPs)."""

from __future__ import annotations

import getpass
import platform
import socket
import time

import distro
import psutil

from ..model import SystemInfo


def _collect_ips() -> list[str]:
    ips: list[str] = []
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            value = addr.address.split("%")[0]  # strip IPv6 zone id
            if value.startswith("127.") or value in ("::1",):
                continue
            if value and value not in ips:
                ips.append(value)
    return ips


def _safe(func, default=None):
    try:
        return func()
    except Exception:
        return default


def _uptime_seconds() -> float:
    return time.time() - psutil.boot_time()


def collect_system() -> SystemInfo:
    """Return system identity info; never raises."""
    os_name = _safe(lambda: distro.name(pretty=True)) or _safe(platform.system)
    return SystemInfo(
        hostname=_safe(platform.node) or _safe(socket.gethostname),
        os_name=os_name or None,
        os_version=_safe(distro.version) or None,
        kernel=_safe(platform.release),
        uptime_seconds=_safe(_uptime_seconds),
        user=_safe(getpass.getuser),
        ip_addresses=_safe(_collect_ips, []) or [],
    )
