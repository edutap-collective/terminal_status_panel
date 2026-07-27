# terminal-status-panel

A small Python package that renders a colorful server status panel on login —
best run from a `profile.d` snippet so it uses the full terminal width (see
[Running it at login](#running-it-at-login)). The full-width dashboard is laid
out in three tiers:

- **SYSTEM OVERVIEW** (with a real, pre-rendered OS logo) beside **UPDATES**.
- **SYSTEM STATUS** — load & per-core CPU usage, memory/swap, and a filesystem
  usage table.
- **DOCKER INFOS** — Swarm key facts (summary + node health) above three
  stacked node matrices: *Infrastruktur*, *Service*, and standalone *Container*.
  Node health has three states: ✅ ready and active, ⚠️ ready but drained or
  paused (it accepts no tasks), 💀 unreachable; the summary line counts the
  non-operational ones, e.g. `5 nodes (1 drain, 1 down)`.
  Per-node replicas of the same service (e.g. `kafka_kafka-<node>` on every
  node) collapse into a single row; a stack with one logical service shows as
  one row named after the stack, a stack with several shows a header plus one
  sub-row per service (stack prefix and node name stripped). Columns are the
  nodes (alphabetical) with ✅ / 💀 placement, plus a description column.

All Docker data is read from the Docker API only — no database or broker
protocol is ever spoken to.

## Requirements

- Python 3.11+
- Linux (Debian/Ubuntu) in production; macOS supported for development.
- Optional: a running Docker daemon for the services panel.

## Installation

```bash
pip install terminal-status-panel     # from PyPI, once published
# or, from a checkout:
pip install .
```

This installs three panel commands (`status-full`, `status-server`,
`status-docker`) plus an `install-panel` helper to wire it into a login shell.

## Commands (sections)

The dashboard is split into two independently runnable sections, each with its
own entry point — plus the combined command:

| Command | Sections | Use |
|---------|----------|-----|
| `status-full` | server + docker | The full panel (default). |
| `status-server` | server only | System overview, updates, load/mem/fs. |
| `status-docker` | docker only | The Docker Swarm block. |

Each section only collects the data it needs: `status-docker` never touches
the system collectors, and `status-server` never opens the Docker socket —
so you can run just what a given host cares about. The combined command also
accepts `--sections server,docker` to pick explicitly.

Any of the three works in the profile.d snippet (see *Running it at login*) —
e.g. call `status-docker` on Docker Swarm nodes and `status-server` on
plain servers.

## Usage

```bash
status-full   [--sections server,docker] [--width N] [--no-color] [--config PATH]
status-server  [--width N] [--no-color] [--config PATH]
status-docker  [--width N] [--no-color] [--config PATH]
```

The command **always exits 0** so it can never break a login shell. If a
collector fails (no Docker socket, non-Debian host, …) that section degrades to
a placeholder instead of erroring.

### Command-line options

| Option        | Default | Description |
|---------------|---------|-------------|
| `--sections`  | *(all / per command)* | Comma-separated sections to render: `server`, `docker`. On `status-full` the default is both; the dedicated commands fix their own section. |
| `--width N`   | *(auto)* | Force the render width to `N` columns. Overrides both auto-detection and the config `width`. |
| `--no-color`  | off     | Disable ANSI colours (plain text — useful for piping/debugging). |
| `--config PATH` | *(see below)* | Load configuration from `PATH` instead of the default location. A missing file is not an error (defaults are used). |

Colours are always **forced on** (unless `--no-color`), because at MOTD
generation time there is no TTY to auto-detect a colour terminal.

### How the render width is chosen

The width is resolved in this order (first match wins):

1. **`--width N`** — an explicit flag always wins.
2. **The current terminal width** — used automatically when standard output is
   a real terminal (TTY), i.e. when you run the command interactively or from a
   shell-login hook. This is what gives you the *full screen width*.
3. **`width` from the config** (default **80**) — the fallback when there is no
   TTY, e.g. when `update-motd.d` pre-generates the cached MOTD.

> The panel is designed for wide terminals. Narrow widths still render but wrap.

## Configuration

Zero configuration is required. Settings are read from
`/etc/terminal-status-panel/config.toml` (override with `--config PATH`). A missing
or unreadable file falls back to the built-in defaults — it never raises.

### Configuration reference

| Key | Default | Meaning |
|-----|---------|---------|
| `width` | `80` | Fallback render width when no TTY is available (see width resolution above). |
| `docker.timeout` | `1.5` | Seconds to wait for the Docker socket before giving up (also bounds the `apt` update check). Keeps a hung/absent daemon from delaying login. |
| `docker.description_label` | `"lmu.service.description"` | Docker **service label** read as the per-service description column. |
| `docker.infrastructure_stacks` | `["postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql", "elasticsearch"]` | Case-insensitive substrings. A stack (or ungrouped service, e.g. `registry`) whose name matches goes into the **Infrastruktur** column; everything else goes into **Service**. |
| `services.critical` | `[]` | Service names flagged as critical (parsed and available on the data model; not visually emphasised in the current matrix view). |
| `thresholds.memory.warning` / `.critical` | `75` / `90` | RAM usage % thresholds (yellow / red). |
| `thresholds.swap.warning` | `1` | Swap usage % above which SWAP turns yellow. |
| `thresholds.filesystem.warning` / `.critical` | `80` / `90` | Filesystem usage % thresholds. |
| `thresholds.load.warning` / `.critical` | `0.8` / `1.0` | Load-average thresholds as a **per-CPU multiplier** (compared against `load1 / cpu_count`). |

### Full example

```toml
# Fallback width for non-TTY (MOTD) rendering. Interactive logins auto-detect
# the real terminal width regardless of this value.
width = 200

[docker]
timeout = 1.5
description_label = "lmu.service.description"
infrastructure_stacks = ["postgresql", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry"]

[services]
critical = ["postgres", "kafka"]

[thresholds.memory]
warning = 75
critical = 90

[thresholds.swap]
warning = 1

[thresholds.filesystem]
warning = 80
critical = 90

[thresholds.load]
warning = 0.8   # per-CPU multiplier
critical = 1.0
```

## Running it at login

**Recommended: run it from `profile.d` (the login shell), not from
`update-motd.d`.** This is the setup we use in production, and the reasoning is
explained below. Use *one* method only — running both prints the panel twice.

### Why profile.d and not update-motd.d?

`update-motd.d` looks like the natural home for a login banner, but it cannot
render at the viewer's terminal width:

- `pam_motd` runs the `update-motd.d` scripts **during PAM session setup, before
  the login shell starts**. At that moment the script has **no controlling TTY**
  and the terminal size is not available; `COLUMNS`/`LINES` are only set later,
  by the interactive shell.
- **SSH does not change this.** SSH knows the client's window size (from its
  `pty-req`), but it does not pass it to the MOTD scripts. So whether you connect
  by SSH or by a VM console, the result is the same.
- The output is also typically **cached** (`/run/motd.dynamic`) and shown to
  every subsequent login regardless of their window — a single fixed rendering.

The net effect: `update-motd.d` always renders at a **fixed** width (the config
`width`, default 80). For our environment that is exactly wrong:

- We reach every server **only over SSH** — never a VM/VMware console — so a
  real terminal with a known size is always present *at the shell*, just not at
  MOTD-generation time.
- We work from **MacBooks and 4K displays**, where an 80-column banner is either
  cramped or wastes most of the screen. We want the panel to fill whatever
  window the login happens in.

A `profile.d` snippet runs **inside the interactive login shell**, where stdout
*is* the SSH pty and its (SSH-negotiated) size is available. The tool then
auto-detects and uses the **full current terminal width** on every login — wide
on a 4K display, snug in a small MacBook window — with no fixed value to
maintain. That flexibility is why we chose it.

### Install with `install-panel`

The `install-panel` command writes the login snippet for you — system-wide or
per-user — and is idempotent (safe to re-run) and reversible.

```bash
# System-wide, all users (writes /etc/profile.d/zz-terminal-status-panel.sh):
sudo install-panel --scope global

# Per user, no root needed (managed block in ~/.profile or ~/.zprofile):
install-panel --scope user

# Pick which panel(s) to show — e.g. only Docker on a Swarm node:
sudo install-panel --scope global --panel docker
# …or both sections as separate commands:
install-panel --scope user --panel server --panel docker

# Preview without writing, then remove again:
install-panel --scope user --dry-run
install-panel --scope user --uninstall
```

Options:

| Option | Values | Default | Meaning |
|--------|--------|---------|---------|
| `--scope` | `global` \| `user` | `user` | `/etc/profile.d` (needs root) vs. your own login profile. |
| `--panel` | `full` \| `server` \| `docker` | `full` | Which command to run; repeatable. |
| `--shell` | `auto` \| `bash` \| `zsh` | `auto` | Target profile; `zsh` uses `zprofile` (zsh does not read `/etc/profile`). |
| `--uninstall` | — | — | Remove a previous install. |
| `--dry-run` | — | — | Show what would change, write nothing. |

Global vs. user gives you flexibility: roll it out for everyone via
`/etc/profile.d`, or let individual users opt in (or override the global one)
from their own profile. The snippet only runs for **login** shells (SSH logins,
`bash -l`) and only when interactive — it renders once at login; resizing the
window afterwards re-renders on the next login.

To avoid a duplicate static banner, make sure no `update-motd.d` hook is
installed and, if present, empty `/etc/motd` (and optionally set `PrintMotd no`
in `/etc/ssh/sshd_config`).

### Fallback: update-motd.d (fixed width, not recommended here)

If you must use the classic MOTD mechanism, drop a one-line hook
(`exec status-full`) into `/etc/update-motd.d/` and set a fixed wide `width` in
the config — accepting that it will **not** adapt to each login's window. For a
mix of 4K and laptop screens that is the wrong trade-off; prefer `install-panel`.

## Service descriptions (Docker Swarm)

Services are grouped by their `com.docker.stack.namespace` label (set
automatically for stack deployments). To show a human-readable description per
service, add a label to the service — by default the key
`lmu.service.description`:

```yaml
services:
  postgres:
    image: postgres:18
    deploy:
      labels:
        lmu.service.description: "PostgreSQL Datenbank, Version 18"
```

Change the read label key via `docker.description_label` in the config.

## OS logos

Logos are **pre-rendered** from real PNGs into half-block ANSI (`▀` with
fore/background colours) and bundled under
`src/terminal_status_panel/render/logos/*.ans`. They are plain ANSI, so they
render in MOTD and over SSH without any image protocol or runtime dependency.
The correct logo is chosen automatically from the detected distribution
(Debian / Ubuntu / generic Linux).

To regenerate them (dev only, needs Pillow — `pip install -e '.[dev]'`), drop
source PNGs into `assets/logos/` and run:

```bash
python tools/generate_logos.py
```

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,test]'
ruff check src tests
python -m pytest
```

CI (`.github/workflows/ci.yml`) runs ruff and the test suite on every push and
pull request across Python 3.11–3.13.

## Publishing to PyPI

Releases are automated. Pushing a version tag `vX.Y.Z` triggers
`.github/workflows/release.yml`, which builds the sdist + wheel and publishes to
PyPI via **Trusted Publishing (OIDC)** — no API token is stored in the repo.

Release steps:

```bash
# 1. bump the version in pyproject.toml (must match the tag)
# 2. commit, then tag and push
git commit -am "release: v0.1.0"
git tag v0.1.0
git push && git push --tags
```

One-time setup (before the first release):

1. On <https://pypi.org> → *Your projects* → *Publishing*, add a **pending
   trusted publisher** with:
   - **PyPI project name:** `terminal-status-panel`
   - **Owner:** `edutap-collective`
   - **Repository name:** `terminal_status_panel`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
2. In the GitHub repo → *Settings → Environments*, create an environment named
   `pypi` (optionally add required reviewers to gate publishes).

## License

Licensed under the [EUPL-1.2](LICENSE).
