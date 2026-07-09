# Terminal Status Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `lmu.terminal_status_panel` Python package whose `lmu-status-panel` CLI prints a colorful server status panel (system info, resource bars, Dockerized service health) for use in `update-motd.d`.

**Architecture:** Pure data collectors return dataclasses (`model.py`), independent of rendering. Rich-based renderers turn those dataclasses into a fixed-width, force-ANSI panel. A thin CLI orchestrates collection → rendering and always exits 0. Config comes from built-in defaults overridable by an optional TOML file.

**Tech Stack:** Python 3.11+, `rich` (rendering), `psutil` (system/resource data), `docker` (docker-py, service health), `distro` (Linux distribution info), stdlib `tomllib`/`argparse`. Tests with `pytest` + `pytest-cov`.

## Global Constraints

- **Python:** 3.11+ (uses `tomllib`, `X | None` syntax). Applies to all code.
- **License:** EUPL-1.2 (already set in `pyproject.toml`).
- **Dependencies:** Only `rich`, `docker`, `psutil`, `distro` at runtime — no new deps without justification. Everything else stdlib.
- **UI language:** All user-facing panel labels/headings in **English**.
- **Platform:** Runs on Debian/Ubuntu (Linux) in production; must import and run its test suite on macOS (dev). Linux-only calls (`psutil.getloadavg`, `distro`) are guarded/mocked.
- **Robustness:** No collector exception may propagate to the CLI top level; every collector degrades to a "not available" state. The CLI **always exits 0**.
- **Rendering:** Fixed width, default **80**; Rich `Console(force_terminal=True)`. Never rely on TTY auto-detection.
- **Default thresholds (percent unless noted):** memory warn 75 / crit 90; swap warn 1; filesystem warn 80 / crit 90; load warn 0.8× / crit 1.0× per-CPU multiplier.
- **Commits:** Conventional Commits. Every commit message ends with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` (omitted from the example one-liners below for brevity — add it to each real commit).

## File Structure

```text
src/lmu/terminal_status_panel/
├── __init__.py
├── model.py                    # Dataclasses (Task 1)
├── config.py                   # Config + Thresholds + TOML loader (Task 2)
├── collectors/
│   ├── __init__.py
│   ├── system.py               # collect_system() (Task 3)
│   ├── resources.py            # collect_resources() (Task 4)
│   └── docker.py               # collect_docker() (Task 5)
├── render/
│   ├── __init__.py
│   ├── bars.py                 # classify/filled_cells/render_bar (Task 6)
│   ├── panels.py               # system/resources/services panels (Task 7)
│   └── layout.py               # build_layout() (Task 8)
└── cli.py                      # main() entry point (Task 9)
contrib/50-lmu-status-panel     # example update-motd.d hook (Task 10)
tests/                          # one test module per source module
```

**Removed in Task 1:** `src/lmu/terminal_status_panel/data/` (os_data.py, mem_data.py, terminal_data.py), `src/lmu/terminal_status_panel/main.py`, `tests/test_mem_stats.py`, `tests/test_os_stats.py`.

---

### Task 1: Data model, package cleanup, and dev environment

**Files:**
- Create: `src/lmu/terminal_status_panel/model.py`
- Create: `src/lmu/terminal_status_panel/collectors/__init__.py` (empty)
- Create: `src/lmu/terminal_status_panel/render/__init__.py` (empty)
- Delete: `src/lmu/terminal_status_panel/data/os_data.py`, `.../data/mem_data.py`, `.../data/terminal_data.py`, `.../main.py`, `tests/test_mem_stats.py`, `tests/test_os_stats.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: dataclasses used by every later task —
  - `SystemInfo(hostname: str|None=None, os_name: str|None=None, os_version: str|None=None, kernel: str|None=None, uptime_seconds: float|None=None, user: str|None=None, ip_addresses: list[str]=[])`
  - `FilesystemUsage(mountpoint: str, total: int, used: int, percent: float)`
  - `ResourceUsage(mem_total: int|None=None, mem_used: int|None=None, mem_percent: float|None=None, swap_total: int|None=None, swap_used: int|None=None, swap_percent: float|None=None, filesystems: list[FilesystemUsage]=[], load_avg: tuple[float,float,float]|None=None, cpu_count: int|None=None)`
  - `ServiceStatus(name: str, running_replicas: int, desired_replicas: int|None, critical: bool=False)`
  - `SwarmInfo(reachable: bool=False, enabled: bool=False, node_role: str|None=None, node_count: int|None=None, services: list[ServiceStatus]=[])`
  - `PanelData(system: SystemInfo|None=None, resources: ResourceUsage|None=None, swarm: SwarmInfo|None=None)`

- [ ] **Step 1: Set up the dev environment**

Run (from repo root; skip venv creation if one already exists in the devcontainer):

```bash
python -m venv .venv 2>/dev/null; . .venv/bin/activate
pip install -e '.[test]'
```

Expected: install succeeds, `pytest` available. `.venv` is already git-ignored.

- [ ] **Step 2: Remove the stale skeleton**

```bash
git rm -r src/lmu/terminal_status_panel/data src/lmu/terminal_status_panel/main.py \
         tests/test_mem_stats.py tests/test_os_stats.py
```

Expected: files staged for deletion. (The `main.py` Hello-World and the `data/` exploratory modules are superseded by this plan.)

- [ ] **Step 3: Write the failing test**

Create `tests/test_model.py`:

```python
from lmu.terminal_status_panel.model import (
    FilesystemUsage,
    PanelData,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
    SystemInfo,
)


def test_dataclasses_have_graceful_defaults():
    # Every aggregate can be constructed empty (degraded state).
    assert SystemInfo().ip_addresses == []
    assert ResourceUsage().filesystems == []
    assert SwarmInfo().reachable is False
    assert SwarmInfo().services == []
    assert PanelData().system is None


def test_value_dataclasses_hold_fields():
    fs = FilesystemUsage(mountpoint="/", total=100, used=91, percent=91.0)
    assert (fs.mountpoint, fs.percent) == ("/", 91.0)
    svc = ServiceStatus(name="postgres", running_replicas=1, desired_replicas=1)
    assert svc.critical is False
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lmu.terminal_status_panel.model'`

- [ ] **Step 5: Implement `model.py`**

Create `src/lmu/terminal_status_panel/model.py`:

```python
"""Dataclasses shared by collectors and renderers.

All aggregate fields default to empty/None so a failed collector degrades
gracefully instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemInfo:
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    kernel: str | None = None
    uptime_seconds: float | None = None
    user: str | None = None
    ip_addresses: list[str] = field(default_factory=list)


@dataclass
class FilesystemUsage:
    mountpoint: str
    total: int
    used: int
    percent: float


@dataclass
class ResourceUsage:
    mem_total: int | None = None
    mem_used: int | None = None
    mem_percent: float | None = None
    swap_total: int | None = None
    swap_used: int | None = None
    swap_percent: float | None = None
    filesystems: list[FilesystemUsage] = field(default_factory=list)
    load_avg: tuple[float, float, float] | None = None
    cpu_count: int | None = None


@dataclass
class ServiceStatus:
    name: str
    running_replicas: int
    desired_replicas: int | None
    critical: bool = False


@dataclass
class SwarmInfo:
    reachable: bool = False
    enabled: bool = False
    node_role: str | None = None
    node_count: int | None = None
    services: list[ServiceStatus] = field(default_factory=list)


@dataclass
class PanelData:
    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
```

Also create empty `src/lmu/terminal_status_panel/collectors/__init__.py` and `src/lmu/terminal_status_panel/render/__init__.py`.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_model.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add data model and remove exploratory skeleton"
```

---

### Task 2: Configuration (defaults + optional TOML)

**Files:**
- Create: `src/lmu/terminal_status_panel/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Thresholds(memory_warning=75.0, memory_critical=90.0, swap_warning=1.0, filesystem_warning=80.0, filesystem_critical=90.0, load_warning=0.8, load_critical=1.0)` — all `float`.
  - `Config(width: int=80, docker_timeout: float=1.5, critical_services: list[str]=[], thresholds: Thresholds=Thresholds())`
  - `DEFAULT_CONFIG_PATH = "/etc/lmu-status-panel/config.toml"`
  - `load_config(path: str | os.PathLike | None = None) -> Config` — `None` → try `DEFAULT_CONFIG_PATH`; missing file → all defaults; explicit `path` that is missing → still return defaults (never raise).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from lmu.terminal_status_panel.config import Config, Thresholds, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.width == 80
    assert cfg.docker_timeout == 1.5
    assert cfg.critical_services == []
    assert cfg.thresholds.memory_critical == 90.0
    assert cfg.thresholds.load_warning == 0.8


def test_toml_overrides_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "width = 100",
                "[docker]",
                "timeout = 3.0",
                "[services]",
                'critical = ["postgres", "kafka"]',
                "[thresholds.memory]",
                "warning = 60",
                "critical = 85",
            ]
        )
    )
    cfg = load_config(path)
    assert cfg.width == 100
    assert cfg.docker_timeout == 3.0
    assert cfg.critical_services == ["postgres", "kafka"]
    assert cfg.thresholds.memory_warning == 60.0
    assert cfg.thresholds.memory_critical == 85.0
    # untouched thresholds keep defaults
    assert cfg.thresholds.filesystem_critical == 90.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lmu.terminal_status_panel.config'`

- [ ] **Step 3: Implement `config.py`**

Create `src/lmu/terminal_status_panel/config.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/config.py tests/test_config.py
git commit -m "feat: add config with defaults and optional TOML override"
```

---

### Task 3: System collector

**Files:**
- Create: `src/lmu/terminal_status_panel/collectors/system.py`
- Test: `tests/test_collectors_system.py`

**Interfaces:**
- Consumes: `SystemInfo` from `model`.
- Produces: `collect_system() -> SystemInfo`. Never raises; on total failure returns `SystemInfo()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collectors_system.py`:

```python
import socket
from types import SimpleNamespace

from lmu.terminal_status_panel.collectors import system
from lmu.terminal_status_panel.model import SystemInfo


def test_collect_system_populates_fields(monkeypatch):
    monkeypatch.setattr(system.platform, "node", lambda: "srv01")
    monkeypatch.setattr(system.platform, "release", lambda: "6.1.0-debian")
    monkeypatch.setattr(system.distro, "name", lambda pretty=False: "Debian GNU/Linux")
    monkeypatch.setattr(system.distro, "version", lambda: "12")
    monkeypatch.setattr(system.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(system.getpass, "getuser", lambda: "root")
    monkeypatch.setattr(
        system.psutil,
        "net_if_addrs",
        lambda: {
            "lo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            "eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
        },
    )

    info = system.collect_system()

    assert isinstance(info, SystemInfo)
    assert info.hostname == "srv01"
    assert info.kernel == "6.1.0-debian"
    assert info.os_name == "Debian GNU/Linux"
    assert info.os_version == "12"
    assert info.user == "root"
    assert "10.0.0.5" in info.ip_addresses
    assert "127.0.0.1" not in info.ip_addresses  # loopback filtered


def test_collect_system_degrades_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(system.psutil, "net_if_addrs", boom)
    # Should not raise; returns a SystemInfo (possibly partially empty).
    info = system.collect_system()
    assert isinstance(info, SystemInfo)
    assert info.ip_addresses == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collectors_system.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lmu.terminal_status_panel.collectors.system'`

- [ ] **Step 3: Implement `system.py`**

Create `src/lmu/terminal_status_panel/collectors/system.py`:

```python
"""Collect system identity info (OS, kernel, host, uptime, user, IPs)."""

from __future__ import annotations

import getpass
import platform
import socket

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


def collect_system() -> SystemInfo:
    """Return system identity info; never raises."""
    os_name = _safe(lambda: distro.name(pretty=True)) or _safe(platform.system)
    return SystemInfo(
        hostname=_safe(platform.node) or _safe(socket.gethostname),
        os_name=os_name or None,
        os_version=_safe(distro.version) or None,
        kernel=_safe(platform.release),
        uptime_seconds=_safe(lambda: _uptime_seconds()),
        user=_safe(getpass.getuser),
        ip_addresses=_safe(_collect_ips, []) or [],
    )


def _uptime_seconds() -> float:
    import time

    return time.time() - psutil.boot_time()
```

Note: `distro.name`/`distro.version` return `""` on non-Linux (macOS dev); `os_version` then becomes `None` and `os_name` falls back to `platform.system()`. `time` is imported inside `_uptime_seconds` to keep the module top clean, but a top-level `import time` is equally fine.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collectors_system.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/collectors/system.py tests/test_collectors_system.py
git commit -m "feat: add system info collector"
```

---

### Task 4: Resource collector

**Files:**
- Create: `src/lmu/terminal_status_panel/collectors/resources.py`
- Test: `tests/test_collectors_resources.py`

**Interfaces:**
- Consumes: `ResourceUsage`, `FilesystemUsage` from `model`.
- Produces:
  - `PSEUDO_FSTYPES: set[str]` — `{"tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "devfs", "autofs", "cgroup", "cgroup2", "mqueue", "debugfs", "tracefs"}`.
  - `collect_resources() -> ResourceUsage`. Never raises; on failure of any part, that part stays `None`/empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collectors_resources.py`:

```python
from types import SimpleNamespace

import pytest

from lmu.terminal_status_panel.collectors import resources
from lmu.terminal_status_panel.model import ResourceUsage


@pytest.fixture
def base_mocks(monkeypatch):
    monkeypatch.setattr(
        resources.psutil, "virtual_memory",
        lambda: SimpleNamespace(total=32_000_000_000, used=20_400_000_000, percent=64.0),
    )
    monkeypatch.setattr(
        resources.psutil, "swap_memory",
        lambda: SimpleNamespace(total=8_000_000_000, used=600_000_000, percent=8.0),
    )
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", lambda: (1.0, 0.7, 0.4))
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)


def test_memory_and_swap(base_mocks):
    res = resources.collect_resources()
    assert isinstance(res, ResourceUsage)
    assert res.mem_percent == 64.0
    assert res.swap_used == 600_000_000
    assert res.load_avg == (1.0, 0.7, 0.4)
    assert res.cpu_count == 4


def test_filesystem_filtering(base_mocks, monkeypatch):
    parts = [
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        SimpleNamespace(device="overlay", mountpoint="/var/lib/docker", fstype="overlay"),
    ]
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        resources.psutil, "disk_usage",
        lambda p: SimpleNamespace(total=230_000_000_000, used=210_000_000_000, percent=91.0),
    )
    res = resources.collect_resources()
    assert [fs.mountpoint for fs in res.filesystems] == ["/"]
    assert res.filesystems[0].percent == 91.0


def test_degrades_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(resources.psutil, "virtual_memory", boom)
    monkeypatch.setattr(resources.psutil, "swap_memory", boom)
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", boom)
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    res = resources.collect_resources()
    assert res.mem_percent is None
    assert res.filesystems == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collectors_resources.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `resources.py`**

Create `src/lmu/terminal_status_panel/collectors/resources.py`:

```python
"""Collect resource usage: memory, swap, filesystems, load average."""

from __future__ import annotations

import psutil

from ..model import FilesystemUsage, ResourceUsage

PSEUDO_FSTYPES: set[str] = {
    "tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "devfs",
    "autofs", "cgroup", "cgroup2", "mqueue", "debugfs", "tracefs",
}


def _collect_filesystems() -> list[FilesystemUsage]:
    result: list[FilesystemUsage] = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in PSEUDO_FSTYPES or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        result.append(
            FilesystemUsage(
                mountpoint=part.mountpoint,
                total=usage.total,
                used=usage.used,
                percent=usage.percent,
            )
        )
    return result


def _safe(func, default=None):
    try:
        return func()
    except Exception:
        return default


def collect_resources() -> ResourceUsage:
    """Return resource usage; never raises. Unavailable parts stay None/empty."""
    res = ResourceUsage()

    mem = _safe(psutil.virtual_memory)
    if mem is not None:
        res.mem_total, res.mem_used, res.mem_percent = mem.total, mem.used, mem.percent

    swap = _safe(psutil.swap_memory)
    if swap is not None:
        res.swap_total, res.swap_used, res.swap_percent = swap.total, swap.used, swap.percent

    res.filesystems = _safe(_collect_filesystems, []) or []
    res.cpu_count = _safe(psutil.cpu_count)

    load = _safe(lambda: psutil.getloadavg())
    if load is not None:
        res.load_avg = (load[0], load[1], load[2])

    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collectors_resources.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/collectors/resources.py tests/test_collectors_resources.py
git commit -m "feat: add resource usage collector"
```

---

### Task 5: Docker/Swarm collector

**Files:**
- Create: `src/lmu/terminal_status_panel/collectors/docker.py`
- Test: `tests/test_collectors_docker.py`

**Interfaces:**
- Consumes: `SwarmInfo`, `ServiceStatus` from `model`.
- Produces: `collect_docker(timeout: float = 1.5, critical: list[str] | None = None) -> SwarmInfo`. Any failure (no socket, API error, timeout) → `SwarmInfo(reachable=False)`. When Swarm is active: `reachable=True, enabled=True`, `node_role`/`node_count` set, one `ServiceStatus` per Swarm service. When Docker is up but Swarm inactive: `reachable=True, enabled=False`, one `ServiceStatus(running=1, desired=1)` per running container.

**Docker SDK shape assumed (read defensively):**
- `client.info()` → dict; `info["Swarm"]` → `{"LocalNodeState": "active"|"inactive", "ControlAvailable": bool, "Nodes": int}`.
- `client.services.list()` → services; `service.name` → str; `service.attrs["Spec"]["Mode"]["Replicated"]["Replicas"]` → desired int (absent for global services → desired `None`); `service.tasks(filters={"desired-state": "running"})` → list of task dicts with `task["Status"]["State"]`.
- `client.containers.list()` → containers with `.name`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collectors_docker.py`:

```python
from lmu.terminal_status_panel.collectors import docker as docker_collector
from lmu.terminal_status_panel.model import SwarmInfo


class _FakeService:
    def __init__(self, name, desired, running):
        self.name = name
        self.attrs = {"Spec": {"Mode": {"Replicated": {"Replicas": desired}}}}
        self._running = running

    def tasks(self, filters=None):
        return [{"Status": {"State": "running"}} for _ in range(self._running)] + [
            {"Status": {"State": "failed"}}
        ]


class _FakeClient:
    def __init__(self, swarm_state, services=None, containers=None):
        self._info = {
            "Swarm": {"LocalNodeState": swarm_state, "ControlAvailable": True, "Nodes": 3}
        }
        self._services = services or []
        self._containers = containers or []

    def info(self):
        return self._info

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def services(self):
        return self._Coll(self._services)

    @property
    def containers(self):
        return self._Coll(self._containers)


def test_unreachable_when_from_env_raises(monkeypatch):
    monkeypatch.setattr(
        docker_collector.docker, "from_env",
        lambda *a, **k: (_ for _ in ()).throw(Exception("no socket")),
    )
    result = docker_collector.collect_docker(timeout=0.1)
    assert isinstance(result, SwarmInfo)
    assert result.reachable is False


def test_swarm_active_lists_services(monkeypatch):
    client = _FakeClient(
        "active",
        services=[_FakeService("postgres", desired=1, running=1),
                  _FakeService("kafka", desired=3, running=2)],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(critical=["postgres"])
    assert result.reachable is True
    assert result.enabled is True
    assert result.node_role == "manager"
    assert result.node_count == 3
    by_name = {s.name: s for s in result.services}
    assert by_name["postgres"].running_replicas == 1
    assert by_name["postgres"].desired_replicas == 1
    assert by_name["postgres"].critical is True
    assert by_name["kafka"].running_replicas == 2
    assert by_name["kafka"].desired_replicas == 3
    assert by_name["kafka"].critical is False


def test_swarm_inactive_falls_back_to_containers(monkeypatch):
    class _C:
        def __init__(self, name):
            self.name = name

    client = _FakeClient("inactive", containers=[_C("redis"), _C("nginx")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.reachable is True
    assert result.enabled is False
    assert {s.name for s in result.services} == {"redis", "nginx"}
    assert all(s.running_replicas == 1 and s.desired_replicas == 1 for s in result.services)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collectors_docker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `docker.py`**

Create `src/lmu/terminal_status_panel/collectors/docker.py`:

```python
"""Collect Docker Swarm + curated service health via the Docker SDK.

Time-boxed and exception-safe: any failure yields SwarmInfo(reachable=False)
so a missing or hung Docker socket can never block login.
"""

from __future__ import annotations

import docker

from ..model import ServiceStatus, SwarmInfo


def _running_count(service) -> int:
    try:
        tasks = service.tasks(filters={"desired-state": "running"})
    except Exception:
        return 0
    return sum(1 for t in tasks if t.get("Status", {}).get("State") == "running")


def _desired_count(service) -> int | None:
    try:
        return service.attrs["Spec"]["Mode"]["Replicated"]["Replicas"]
    except (KeyError, TypeError):
        return None  # e.g. global-mode services


def _swarm_services(client, critical: set[str]) -> list[ServiceStatus]:
    services = []
    for svc in client.services.list():
        services.append(
            ServiceStatus(
                name=svc.name,
                running_replicas=_running_count(svc),
                desired_replicas=_desired_count(svc),
                critical=svc.name in critical,
            )
        )
    return services


def _container_services(client, critical: set[str]) -> list[ServiceStatus]:
    services = []
    for cont in client.containers.list():
        services.append(
            ServiceStatus(
                name=cont.name,
                running_replicas=1,
                desired_replicas=1,
                critical=cont.name in critical,
            )
        )
    return services


def collect_docker(timeout: float = 1.5, critical: list[str] | None = None) -> SwarmInfo:
    """Return Swarm/service health; never raises."""
    critical_set = set(critical or [])
    try:
        client = docker.from_env(timeout=timeout)
        info = client.info()
        swarm = info.get("Swarm", {}) if isinstance(info, dict) else {}
        active = swarm.get("LocalNodeState") == "active"

        if active:
            role = "manager" if swarm.get("ControlAvailable") else "worker"
            return SwarmInfo(
                reachable=True,
                enabled=True,
                node_role=role,
                node_count=swarm.get("Nodes"),
                services=_swarm_services(client, critical_set),
            )
        return SwarmInfo(
            reachable=True,
            enabled=False,
            services=_container_services(client, critical_set),
        )
    except Exception:
        return SwarmInfo(reachable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collectors_docker.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/collectors/docker.py tests/test_collectors_docker.py
git commit -m "feat: add docker/swarm service collector"
```

---

### Task 6: Bars and threshold coloring

**Files:**
- Create: `src/lmu/terminal_status_panel/render/bars.py`
- Test: `tests/test_render_bars.py`

**Interfaces:**
- Consumes: nothing (pure rendering helpers).
- Produces:
  - `STATUS_COLORS: dict[str, str]` = `{"ok": "green", "warning": "yellow", "critical": "red"}`.
  - `classify(value: float, warning: float, critical: float) -> str` → `"ok"|"warning"|"critical"` (>= critical → critical; >= warning → warning; else ok).
  - `filled_cells(percent: float, width: int) -> int` = `round(percent/100*width)`, clamped to `[0, width]`.
  - `render_bar(percent: float, status: str, width: int = 18) -> rich.text.Text` — a `Text` of `█`×filled + `░`×empty, styled with `STATUS_COLORS[status]`.
  - `format_bytes(n: int | None) -> str` — human-readable (e.g. `20.4 GB`); `None` → `"n/a"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_bars.py`:

```python
from rich.text import Text

from lmu.terminal_status_panel.render import bars


def test_classify_thresholds():
    assert bars.classify(50, 75, 90) == "ok"
    assert bars.classify(80, 75, 90) == "warning"
    assert bars.classify(95, 75, 90) == "critical"
    assert bars.classify(90, 75, 90) == "critical"  # boundary is inclusive


def test_filled_cells_proportional_and_clamped():
    assert bars.filled_cells(50, 20) == 10
    assert bars.filled_cells(0, 20) == 0
    assert bars.filled_cells(100, 20) == 20
    assert bars.filled_cells(150, 20) == 20  # clamped


def test_render_bar_is_styled_text():
    bar = bars.render_bar(50.0, "critical", width=10)
    assert isinstance(bar, Text)
    assert bar.plain.count("█") == 5
    assert bar.plain.count("░") == 5
    assert bar.style == "red"


def test_format_bytes():
    assert bars.format_bytes(None) == "n/a"
    assert bars.format_bytes(0) == "0.0 B"
    assert bars.format_bytes(20_400_000_000).endswith("GB")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_bars.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `bars.py`**

Create `src/lmu/terminal_status_panel/render/bars.py`:

```python
"""Bar-chart rendering helpers with threshold-based coloring."""

from __future__ import annotations

from rich.text import Text

STATUS_COLORS: dict[str, str] = {
    "ok": "green",
    "warning": "yellow",
    "critical": "red",
}

_FILLED = "█"
_EMPTY = "░"


def classify(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def filled_cells(percent: float, width: int) -> int:
    cells = round(percent / 100 * width)
    return max(0, min(width, cells))


def render_bar(percent: float, status: str, width: int = 18) -> Text:
    filled = filled_cells(percent, width)
    body = _FILLED * filled + _EMPTY * (width - filled)
    return Text(body, style=STATUS_COLORS.get(status, "white"))


def format_bytes(n: int | None) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_bars.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/render/bars.py tests/test_render_bars.py
git commit -m "feat: add bar rendering and threshold coloring"
```

---

### Task 7: Panels

**Files:**
- Create: `src/lmu/terminal_status_panel/render/panels.py`
- Test: `tests/test_render_panels.py`

**Interfaces:**
- Consumes: `SystemInfo`, `ResourceUsage`, `SwarmInfo` from `model`; `Config`, `Thresholds` from `config`; `classify`, `render_bar`, `format_bytes`, `STATUS_COLORS` from `render.bars`.
- Produces (each returns a `rich.panel.Panel`; each handles `None`/degraded input):
  - `system_panel(info: SystemInfo | None) -> Panel`
  - `resources_panel(res: ResourceUsage | None, cfg: Config) -> Panel`
  - `services_panel(swarm: SwarmInfo | None, cfg: Config) -> Panel`
- Helper: `_load_status(load_avg, cpu_count, thresholds) -> tuple[str, str]` returning `(text, status)` — used by resources_panel; load thresholds are per-CPU multipliers, so compare `load1 / cpu_count`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_panels.py`:

```python
from rich.console import Console
from rich.panel import Panel

from lmu.terminal_status_panel.config import Config
from lmu.terminal_status_panel.model import (
    FilesystemUsage,
    ResourceUsage,
    ServiceStatus,
    SwarmInfo,
    SystemInfo,
)
from lmu.terminal_status_panel.render import panels


def _text(renderable) -> str:
    console = Console(width=80, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_system_panel_shows_fields():
    info = SystemInfo(hostname="srv01", os_name="Debian", os_version="12",
                      kernel="6.1.0", uptime_seconds=90000, user="root",
                      ip_addresses=["10.0.0.5"])
    out = _text(panels.system_panel(info))
    assert "srv01" in out
    assert "Debian" in out
    assert "10.0.0.5" in out


def test_system_panel_handles_none():
    assert isinstance(panels.system_panel(None), Panel)


def test_resources_panel_renders_bars_and_load():
    res = ResourceUsage(
        mem_total=32_000_000_000, mem_used=20_400_000_000, mem_percent=64.0,
        swap_total=8_000_000_000, swap_used=600_000_000, swap_percent=8.0,
        filesystems=[FilesystemUsage("/", 230_000_000_000, 210_000_000_000, 91.0)],
        load_avg=(1.0, 0.7, 0.4), cpu_count=4,
    )
    out = _text(panels.resources_panel(res, Config()))
    assert "RAM" in out
    assert "SWAP" in out
    assert "64" in out  # percent shown
    assert "█" in out   # bar drawn
    assert "/" in out   # filesystem mountpoint


def test_services_panel_lists_services():
    swarm = SwarmInfo(reachable=True, enabled=True, node_role="manager", node_count=3,
                      services=[ServiceStatus("postgres", 1, 1),
                                ServiceStatus("kafka", 2, 3, critical=True)])
    out = _text(panels.services_panel(swarm, Config()))
    assert "manager" in out
    assert "postgres" in out
    assert "1/1" in out
    assert "kafka" in out
    assert "2/3" in out


def test_services_panel_unreachable():
    out = _text(panels.services_panel(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_panels.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `panels.py`**

Create `src/lmu/terminal_status_panel/render/panels.py`:

```python
"""Rich panels for system info, resources, and services."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import Config, Thresholds
from ..model import ResourceUsage, SwarmInfo, SystemInfo
from .bars import STATUS_COLORS, classify, format_bytes, render_bar


def _kv_table() -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="left")
    return table


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def system_panel(info: SystemInfo | None) -> Panel:
    table = _kv_table()
    if info is None:
        table.add_row("Status", "not available")
    else:
        os_line = " ".join(p for p in (info.os_name, info.os_version) if p) or "n/a"
        table.add_row("Host", info.hostname or "n/a")
        table.add_row("OS", os_line)
        table.add_row("Kernel", info.kernel or "n/a")
        table.add_row("Uptime", _fmt_uptime(info.uptime_seconds))
        table.add_row("User", info.user or "n/a")
        table.add_row("IPs", ", ".join(info.ip_addresses) or "n/a")
    return Panel(table, title="System", title_align="left")


def _bar_row(table: Table, label: str, percent: float | None,
             used: int | None, total: int | None, status: str) -> None:
    if percent is None:
        table.add_row(label, Text("n/a", style="dim"), "")
        return
    bar = render_bar(percent, status)
    pct = Text(f"{percent:4.0f}%", style=STATUS_COLORS.get(status, "white"))
    detail = f"{format_bytes(used)}/{format_bytes(total)}"
    table.add_row(label, bar, pct, detail)


def _load_status(load_avg, cpu_count, thresholds: Thresholds) -> tuple[str, str]:
    if not load_avg:
        return "n/a", "ok"
    one = load_avg[0]
    cpus = cpu_count or 1
    normalized = (one / cpus) * 100
    status = classify(normalized, thresholds.load_warning * 100,
                      thresholds.load_critical * 100)
    text = f"{load_avg[0]:.2f} {load_avg[1]:.2f} {load_avg[2]:.2f}"
    return text, status


def resources_panel(res: ResourceUsage | None, cfg: Config) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")   # label
    table.add_column()               # bar
    table.add_column(justify="right")  # percent
    table.add_column()               # detail
    t = cfg.thresholds

    if res is None:
        table.add_row("Status", Text("not available", style="dim"), "", "")
        return Panel(table, title="Resources", title_align="left")

    _bar_row(table, "RAM", res.mem_percent, res.mem_used, res.mem_total,
             classify(res.mem_percent or 0, t.memory_warning, t.memory_critical))
    _bar_row(table, "SWAP", res.swap_percent, res.swap_used, res.swap_total,
             classify(res.swap_percent or 0, t.swap_warning, 100.0))
    for fs in res.filesystems:
        _bar_row(table, fs.mountpoint, fs.percent, fs.used, fs.total,
                 classify(fs.percent, t.filesystem_warning, t.filesystem_critical))

    load_text, load_status = _load_status(res.load_avg, res.cpu_count, t)
    table.add_row("Load", Text(load_text, style=STATUS_COLORS.get(load_status, "white")), "", "")
    return Panel(table, title="Resources", title_align="left")


def services_panel(swarm: SwarmInfo | None, cfg: Config) -> Panel:
    if swarm is None or not swarm.reachable:
        body = Text("Docker not reachable", style="dim")
        return Panel(body, title="Services", title_align="left")

    if swarm.enabled:
        title = f"Services (Swarm: {swarm.node_role or '?'}, {swarm.node_count or '?'} nodes)"
    else:
        title = "Services (containers)"

    table = Table.grid(padding=(0, 2))
    table.add_column()
    if not swarm.services:
        table.add_row(Text("no services", style="dim"))
    for svc in swarm.services:
        desired = svc.desired_replicas
        healthy = desired is not None and svc.running_replicas >= desired
        color = "green" if healthy else "red"
        desired_str = desired if desired is not None else "-"
        name = Text(svc.name, style="bold" if svc.critical else "")
        line = Text.assemble(
            (f"● ", color), name, (f" {svc.running_replicas}/{desired_str}", color)
        )
        table.add_row(line)
    return Panel(table, title=title, title_align="left")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_panels.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/render/panels.py tests/test_render_panels.py
git commit -m "feat: add system, resources, and services panels"
```

---

### Task 8: Layout

**Files:**
- Create: `src/lmu/terminal_status_panel/render/layout.py`
- Test: `tests/test_render_layout.py`

**Interfaces:**
- Consumes: `PanelData` from `model`; `Config` from `config`; `system_panel`, `resources_panel`, `services_panel` from `render.panels`.
- Produces: `build_layout(data: PanelData, cfg: Config) -> rich.console.Group` — a renderable with system+resources side by side on top (via `rich.columns.Columns` or a borderless two-column `Table`) and the services panel full width below.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_layout.py`:

```python
from rich.console import Console

from lmu.terminal_status_panel.config import Config
from lmu.terminal_status_panel.model import (
    PanelData,
    ResourceUsage,
    SwarmInfo,
    SystemInfo,
)
from lmu.terminal_status_panel.render import layout


def test_build_layout_contains_all_sections():
    data = PanelData(
        system=SystemInfo(hostname="srv01", ip_addresses=["10.0.0.5"]),
        resources=ResourceUsage(mem_percent=64.0, mem_used=1, mem_total=2),
        swarm=SwarmInfo(reachable=False),
    )
    console = Console(width=80, force_terminal=True, color_system="truecolor", record=True)
    console.print(layout.build_layout(data, Config()))
    out = console.export_text()
    assert "System" in out
    assert "Resources" in out
    assert "Services" in out
    assert "srv01" in out


def test_build_layout_survives_all_none():
    console = Console(width=80, force_terminal=True, record=True)
    console.print(layout.build_layout(PanelData(), Config()))
    out = console.export_text()
    assert "System" in out
    assert "Services" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_layout.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `layout.py`**

Create `src/lmu/terminal_status_panel/render/layout.py`:

```python
"""Compose panels into the final two-column-over-full-width layout."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table

from ..config import Config
from ..model import PanelData
from .panels import resources_panel, services_panel, system_panel


def build_layout(data: PanelData, cfg: Config) -> Group:
    top = Table.grid(expand=True)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_row(system_panel(data.system), resources_panel(data.resources, cfg))
    return Group(top, services_panel(data.swarm, cfg))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_render_layout.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lmu/terminal_status_panel/render/layout.py tests/test_render_layout.py
git commit -m "feat: add two-column panel layout"
```

---

### Task 9: CLI and entry point

**Files:**
- Create: `src/lmu/terminal_status_panel/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_config` from `config`; `collect_system`, `collect_resources`, `collect_docker` from collectors; `build_layout` from `render.layout`; `PanelData` from `model`.
- Produces:
  - `collect_all(cfg: Config) -> PanelData` — runs all three collectors (each already exception-safe).
  - `build_console(width: int, no_color: bool) -> rich.console.Console` — `force_terminal=True`, fixed `width`, `color_system=None` when `no_color`.
  - `main(argv: list[str] | None = None) -> int` — parse args (`--width`, `--no-color`, `--config`), load config (CLI `--width` overrides config width), collect, render, print. **Always returns 0**, even on unexpected errors.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
from lmu.terminal_status_panel import cli


def test_main_exits_zero_and_prints(capsys):
    rc = cli.main(["--width", "80"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "System" in out
    assert "Services" in out


def test_main_never_raises_even_if_collection_breaks(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    # Break one collector entirely; main must still exit 0.
    monkeypatch.setattr(cli, "collect_resources", boom)
    rc = cli.main([])
    assert rc == 0


def test_width_flag_overrides(capsys):
    rc = cli.main(["--width", "40"])
    assert rc == 0
    # Narrow width still produces output without crashing.
    assert capsys.readouterr().out.strip() != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError`/`AttributeError` (no `cli` module / no `main`)

- [ ] **Step 3: Implement `cli.py`**

Create `src/lmu/terminal_status_panel/cli.py`:

```python
"""Command-line entry point: collect data, render the panel, always exit 0."""

from __future__ import annotations

import argparse

from rich.console import Console

from .collectors.docker import collect_docker
from .collectors.resources import collect_resources
from .collectors.system import collect_system
from .config import Config, load_config
from .model import PanelData
from .render.layout import build_layout


def collect_all(cfg: Config) -> PanelData:
    return PanelData(
        system=collect_system(),
        resources=collect_resources(),
        swarm=collect_docker(timeout=cfg.docker_timeout, critical=cfg.critical_services),
    )


def build_console(width: int, no_color: bool) -> Console:
    return Console(
        force_terminal=True,
        width=width,
        color_system=None if no_color else "truecolor",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lmu-status-panel")
    parser.add_argument("--width", type=int, default=None, help="fixed render width")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--config", default=None, help="path to a TOML config file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render the status panel. Always returns 0 — never fails a login."""
    try:
        args = _parse_args(argv)
        cfg = load_config(args.config)
        width = args.width if args.width is not None else cfg.width
        console = build_console(width, args.no_color)
        data = collect_all(cfg)
        console.print(build_layout(data, cfg))
    except Exception:
        # A status panel must never break the login shell.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Register the console-script entry point**

In `pyproject.toml`, add after the `[project.urls]` block (or anywhere at top level):

```toml
[project.scripts]
lmu-status-panel = "lmu.terminal_status_panel.cli:main"
```

Then reinstall so the script is on PATH:

```bash
pip install -e '.[test]'
```

- [ ] **Step 6: Verify the CLI runs end-to-end**

Run: `lmu-status-panel --width 80`
Expected: a rendered panel prints; `echo $?` prints `0`. (On macOS dev, the Services panel will show "Docker not reachable" unless Docker is running — that is correct degraded behavior.)

- [ ] **Step 7: Commit**

```bash
git add src/lmu/terminal_status_panel/cli.py tests/test_cli.py pyproject.toml
git commit -m "feat: add CLI entry point and console script"
```

---

### Task 10: Deployment example and README

**Files:**
- Create: `contrib/50-lmu-status-panel`
- Modify: `README.md`

**Interfaces:**
- Consumes: the `lmu-status-panel` console script from Task 9.
- Produces: an example `update-motd.d` hook and user-facing install/config docs. No code.

- [ ] **Step 1: Create the example MOTD hook**

Create `contrib/50-lmu-status-panel`:

```sh
#!/bin/sh
# Example update-motd.d hook for lmu.terminal_status_panel.
# Install by copying to /etc/update-motd.d/ and making it executable:
#   sudo cp contrib/50-lmu-status-panel /etc/update-motd.d/
#   sudo chmod +x /etc/update-motd.d/50-lmu-status-panel
# Adjust the path below to the interpreter/venv where the package is installed.
exec lmu-status-panel
```

- [ ] **Step 2: Document install and configuration in `README.md`**

Replace `README.md` with:

```markdown
# lmu.terminal_status_panel

A small Python package that renders a colorful server status panel on login
via `update-motd.d`. It shows system identity, resource usage (with bar
charts), and the health of Dockerized services, including Docker Swarm.

## Requirements

- Python 3.11+
- Linux (Debian/Ubuntu) in production; macOS supported for development.
- Optional: a running Docker daemon for the services panel.

## Installation

```bash
pip install .
```

This installs the `lmu-status-panel` command.

## Usage

```bash
lmu-status-panel [--width N] [--no-color] [--config PATH]
```

The command always exits 0 so it can never break a login shell.

## update-motd.d integration

Copy the example hook and make it executable:

```bash
sudo cp contrib/50-lmu-status-panel /etc/update-motd.d/
sudo chmod +x /etc/update-motd.d/50-lmu-status-panel
```

The hook runs at login; its output is cached in `/run/motd.dynamic`. ANSI
colors are forced on, so terminals display them even though no TTY is present
at generation time.

## Configuration

Zero configuration is required. To customize, create
`/etc/lmu-status-panel/config.toml`:

```toml
width = 80

[docker]
timeout = 1.5

[services]
critical = ["postgres", "kafka"]

[thresholds.memory]
warning = 75
critical = 90

[thresholds.filesystem]
warning = 80
critical = 90

[thresholds.load]
warning = 0.8   # per-CPU multiplier
critical = 1.0
```

## License

EUPL-1.2
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest`
Expected: all tests pass; coverage report prints.

- [ ] **Step 4: Commit**

```bash
git add contrib/50-lmu-status-panel README.md
git commit -m "docs: add update-motd.d hook example and README"
```

---

## Self-Review

**Spec coverage:**

- MOTD force-ANSI + fixed width → Task 9 (`build_console`), Task 10 (hook doc). ✓
- Docker-only service checks → Task 5. ✓
- Swarm role/nodes + curated critical services + running/desired replicas → Task 5 + Task 7 (`services_panel`). ✓
- Container fallback when Swarm inactive → Task 5. ✓
- Config defaults + optional TOML → Task 2. ✓
- Two-column-over-full-width layout → Task 8. ✓
- System info (OS via `distro`, kernel, host, uptime, user, IPv4+IPv6 non-loopback) → Task 3. ✓
- RAM/SWAP/filesystem bars + percent, pseudo-fs filtering, load average → Task 4 + Task 6 + Task 7. ✓
- Threshold coloring (green/yellow/red) incl. swap-any-use and per-CPU load → Task 6 + Task 7. ✓
- Robustness: collectors never raise, CLI always exits 0 → Tasks 3–5 (degrade tests), Task 9 (exit-0 test). ✓
- English UI labels → Tasks 7–10. ✓
- `update-motd.d` example hook + README, no auto-install → Task 10. ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete code. ✓

**Type consistency:** `SystemInfo`/`ResourceUsage`/`ServiceStatus`/`SwarmInfo`/`PanelData` fields defined in Task 1 are used consistently in Tasks 3–9. `classify`/`filled_cells`/`render_bar`/`format_bytes` signatures from Task 6 match their use in Task 7. `collect_system`/`collect_resources`/`collect_docker` signatures from Tasks 3–5 match `collect_all` in Task 9. `build_layout(data, cfg)` from Task 8 matches its call in Task 9. ✓

**Notes for the implementer:**
- `Text.assemble` in `services_panel` takes `(text, style)` tuples and bare `Text` objects — both forms are used intentionally.
- On macOS, `distro.name()`/`distro.version()` return empty strings; tests mock them, and `collect_system` falls back to `platform.system()`.
- The swap bar uses `100.0` as its "critical" bound so any usage above `swap_warning` shows yellow but never red purely from being non-zero; adjust if you want swap to escalate to red.
