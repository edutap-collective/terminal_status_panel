# Terminal Status Panel — Design

**Date:** 2026-07-09
**Status:** Approved (architecture) — pending spec review
**Package:** `lmu.terminal_status_panel`

## Purpose

A small Python package that renders a colorful server status panel on login,
driven by `update-motd.d` on Debian/Ubuntu servers. It shows system identity,
resource usage (with bar charts), and the health of Dockerized services
(including Docker Swarm). Colors highlight problems (memory pressure, active
swap, high load, full filesystems).

Development happens on macOS; the tool must run on Debian/Ubuntu (Linux) in
production. macOS support is limited to what allows local development and
testing — the MOTD integration itself is Linux-only.

## Key Decisions (from brainstorming)

1. **MOTD rendering:** Force ANSI output at a fixed width. The CLI renders with
   Rich using `force_terminal=True` and a fixed width (default 80). The
   `update-motd.d` script simply invokes the CLI; the emitted ANSI escape codes
   are cached in `/run/motd.dynamic` and displayed by the login terminal. No TTY
   is available at generation time, so we never rely on TTY auto-detection.
2. **Service checks: Docker only.** All monitored services run in Docker, so we
   query service health exclusively through the Docker SDK (`docker` /
   docker-py) via the Docker socket. No systemd, no `psycopg`, no Kafka client.
3. **Docker detail: Swarm + curated.** Show Swarm node role and node count, plus
   per-service running/desired replicas (Docker knows the desired count from the
   service spec). A curated config can mark services as critical and give them
   display names.
4. **Config: sensible defaults, optional TOML.** Works with zero configuration.
   An optional `/etc/lmu-status-panel/config.toml` overrides thresholds, width,
   and the curated critical-service list.
5. **Layout: two columns on top.** System info (left) and resources (right) side
   by side; the services panel spans the full width below.

## Architecture

Data collection is separated from rendering. Collectors are pure functions that
return dataclasses and perform no Rich work, so they can be tested without a
terminal. Renderers turn dataclasses into Rich renderables and perform no I/O.

```text
src/lmu/terminal_status_panel/
├── cli.py              # Entry point: parse args, build Console, orchestrate
├── config.py           # Defaults + optional TOML loader
├── model.py            # Dataclasses (see Data Model)
├── collectors/
│   ├── __init__.py
│   ├── system.py       # OS, kernel, host, uptime, user, IPs
│   ├── resources.py    # RAM, SWAP, filesystems, load average
│   └── docker.py       # Swarm info + curated services
└── render/
    ├── __init__.py
    ├── bars.py         # usage bar: filled/empty blocks, percent, threshold color
    ├── panels.py       # system panel, resources panel, services panel
    └── layout.py       # two-column top + full-width services below
```

The existing exploratory modules (`data/os_data.py`, `data/mem_data.py`,
`data/terminal_data.py`, `main.py`) are replaced by this structure. The stale
test `tests/test_mem_stats.py` (asserts `get_mem_data() == "20GB"`) is removed
and replaced with tests against the new collectors.

## Data Model (`model.py`)

Dataclasses, all fields optional/nullable so a failed collector degrades
gracefully rather than crashing:

- `SystemInfo`: `hostname`, `os_name`, `os_version`, `kernel`, `uptime_seconds`,
  `user`, `ip_addresses: list[str]`.
- `ResourceUsage`: `total`, `used`, `percent` for memory and swap; a
  `list[FilesystemUsage]`; and `load_avg: tuple[float, float, float]` with
  `cpu_count`.
  - `FilesystemUsage`: `mountpoint`, `total`, `used`, `percent`.
- `ServiceStatus`: `name`, `running_replicas`, `desired_replicas`, `critical`.
- `SwarmInfo`: `enabled`, `node_role`, `node_count`, `services: list[ServiceStatus]`,
  `reachable` (False when the Docker socket is unavailable).
- `PanelData`: aggregates the above; each section may be `None` when its
  collector failed.

## Collectors

Each collector is wrapped so any exception yields a "not available" state
instead of propagating. The CLI orchestrator catches per-section, never aborts.

### `system.py`

- OS name/version: the `distro` package (`distro.name(pretty=True)`,
  `distro.version()`) for accurate Linux distribution name/version, since
  `platform.linux_distribution()` was removed in Python 3.8. Falls back to
  `platform.system()` on non-Linux (macOS dev).
- Kernel: `platform.release()`.
- Hostname: `platform.node()` / `socket.gethostname()`.
- Uptime: from `psutil.boot_time()`.
- User: `getpass.getuser()`.
- IPs: `psutil.net_if_addrs()`, filtered to non-loopback IPv4 (and IPv6 if
  present); Docker bridge addresses included (they appeared in the mockup).

### `resources.py`

- Memory: `psutil.virtual_memory()`.
- Swap: `psutil.swap_memory()`.
- Filesystems: `psutil.disk_partitions()` + `psutil.disk_usage()`. Filter out
  pseudo/virtual filesystems (tmpfs, devtmpfs, overlay, squashfs, proc, sysfs)
  so only real mounts show.
- Load average: `psutil.getloadavg()`, normalized against `psutil.cpu_count()`.

### `docker.py`

- Connect via docker-py `from_env()` with a **short timeout** (default ~1.5 s) so
  a missing/hung socket cannot block login. On any failure, return
  `SwarmInfo(reachable=False)`.
- Swarm: read node role and node count from the Docker/Swarm API.
- Services: list Swarm services; for each, extract running vs. desired replicas.
  Mark services listed in config as `critical`. If Swarm is inactive, fall back
  to listing running containers as un-replicated services.

## Rendering

### `bars.py`

- Render a horizontal bar of fixed cell width using block glyphs
  (`█` filled, `░` empty), followed by percent and a `used/total` label.
- Color by threshold (see Config): green (ok) / yellow (warning) /
  red (critical). Swap: any usage above a small floor is at least yellow.

### `panels.py`

- **System panel:** key/value lines (Host, OS, Kernel, Uptime, User, IPs).
- **Resources panel:** one bar row per metric (RAM, SWAP, each filesystem);
  load average shown as text, colored by threshold.
- **Services panel:** title shows Swarm role + node count; body shows each
  service as `● name running/desired`, dot colored by health (green when
  running == desired, red when short, critical services emphasized). Shows
  "Docker not reachable" when `reachable` is False.

### `layout.py`

- Two columns on top (system left, resources right) via `rich.columns` or a
  borderless `rich.table`, services panel full width below.
- Total width fixed (default 80, configurable).

## CLI (`cli.py`)

- Entry point registered as `[project.scripts]` → `lmu-status-panel`.
- Flags:
  - `--width N` (default 80) — override fixed render width.
  - `--no-color` — plain output (for piping/debugging).
  - `--config PATH` — override config location.
- Behavior: build a Rich `Console(force_terminal=True, width=..., color_system=...)`,
  run collectors, render layout, print. Always exits 0 (a status panel must not
  fail a login).

## Configuration (`config.py`)

- Load order: built-in defaults → optional TOML file (`/etc/lmu-status-panel/config.toml`
  or `--config`). Uses stdlib `tomllib` (Python 3.11+).
- Configurable keys:
  - `width` (int).
  - Thresholds: `memory.warning` / `memory.critical`,
    `swap.warning`, `filesystem.warning` / `filesystem.critical`,
    `load.warning` / `load.critical` (load thresholds are per-CPU multipliers).
  - `services.critical`: list of service names to emphasize.
  - `docker.timeout` (seconds).
- **Default thresholds:** memory warn 75% / crit 90%; swap warn > 1%;
  filesystem warn 80% / crit 90%; load warn 0.8×CPU / crit 1.0×CPU.

## Deployment (`update-motd.d`)

- Ship an example MOTD hook script (e.g. `50-lmu-status-panel`) that calls
  `lmu-status-panel`. Installation into `/etc/update-motd.d/` is the operator's
  responsibility (documented in the README); the package itself does not modify
  system directories.

## Error Handling & Robustness

- No collector exception may reach the top level; each section degrades to a
  "not available" placeholder.
- Docker access is time-boxed.
- The CLI always exits 0.
- Missing optional data (e.g. no swap configured) renders as an explicit
  "n/a" / omitted row, not an error.

## Testing

- `pytest` with `pytest-cov` (already configured).
- Collectors tested by mocking `psutil` / docker-py return values — verify
  correct dataclass construction, threshold classification, and filesystem
  filtering.
- Renderers tested by asserting on Rich output captured via a `Console` with
  `record=True` (check bar proportions, colors/markup, presence of warning
  markers).
- Robustness tests: each collector, when its underlying call raises, returns the
  "not available" state instead of propagating.
- Follow TDD: write the failing test for each unit before implementing it.

## Out of Scope (YAGNI)

- systemd / non-Docker service checks.
- Native database/broker protocol health checks.
- Historical metrics, alerting, or persistence.
- Automatic installation into `/etc/update-motd.d/`.
- Non-Swarm orchestrators (Kubernetes, Compose beyond plain container fallback).

## Resolved Details

- **UI label language:** English (portable, conventional for OS tooling).
- **IPv6 display:** include non-loopback IPv6 addresses when present, alongside
  non-loopback IPv4.
