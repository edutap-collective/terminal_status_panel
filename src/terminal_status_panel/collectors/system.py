"""Collect system identity info (OS, kernel, host, uptime, user, IPs)."""

from __future__ import annotations

import getpass
import platform
import plistlib
import socket
import time

import distro
import psutil

from ..model import SystemInfo

#: macOS records its product name and version here. CPython's
#: ``platform.mac_ver()`` reads the very same file, so it is not a fallback --
#: it shares the failure mode exactly and only adds a hardcoded product name.
#: Reading the plist directly gets ``ProductName`` authentically instead.
MACOS_VERSION_PLIST = "/System/Library/CoreServices/SystemVersion.plist"


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


def _darwin_identity() -> tuple[str | None, str | None]:
    with open(MACOS_VERSION_PLIST, "rb") as handle:
        data = plistlib.load(handle)
    return data.get("ProductName"), data.get("ProductVersion")


def _os_identity() -> tuple[str | None, str | None]:
    """Return (name, version), or (None, None) when the system is unidentifiable.

    Two branches, no fallback chain. The panel runs at login on a booted system,
    so the files these branches read are present; a fallback would be an
    untested branch guarding a state that does not occur. Where the information
    really is missing, saying so beats inventing a coarser answer that looks
    complete.

    macOS is the only special case -- it has no ``/etc/os-release`` at all.
    Every Linux distribution and FreeBSD ship one, which ``distro`` parses.
    """
    if platform.system() == "Darwin":
        return _safe(_darwin_identity, (None, None))
    name = _safe(lambda: distro.name(pretty=True)) or None
    version = _safe(distro.version) or None
    return name, version


def _kernel() -> str:
    """``Darwin 25.5.0`` rather than a bare ``25.5.0``.

    The release number alone is ambiguous: on macOS it is the Darwin version,
    which is not the product version shown one row above.
    """
    return f"{platform.system()} {platform.release()}".strip()


def collect_system() -> SystemInfo:
    """Return system identity info; never raises."""
    os_name, os_version = _os_identity()
    return SystemInfo(
        hostname=_safe(platform.node) or _safe(socket.gethostname),
        os_name=os_name,
        os_version=os_version,
        kernel=_safe(_kernel),
        uptime_seconds=_safe(_uptime_seconds),
        user=_safe(getpass.getuser),
        ip_addresses=_safe(_collect_ips, []) or [],
    )
