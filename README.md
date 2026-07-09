# lmu.terminal_status_panel

A small Python package that renders a colorful server status panel on login
via `update-motd.d`. The full-width dashboard is laid out in three tiers:

- **SYSTEM OVERVIEW** (with a real, pre-rendered OS logo) beside **UPDATES**.
- **SYSTEM STATUS** — load & per-core CPU usage, memory/swap, and a filesystem
  usage table.
- **DOCKER INFOS** — Swarm key facts (summary + node health) above three
  stacked node matrices: *Infrastruktur*, *Service*, and standalone *Container*.
  Stacks and containers are sorted alphabetically; each matrix has a row per
  stack (or per service when a stack has several), a column per node
  (alphabetical) showing ✅ / 💀 placement, and a description column.

All Docker data is read from the Docker API only — no database or broker
protocol is ever spoken to.

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
# Service label read as the human-readable description shown per service.
description_label = "lmu.service.description"
# Stacks whose name matches one of these (case-insensitive substring) go into
# "Infrastruktur"; other stacks go into "Service". Ungrouped services matching
# a key (e.g. registry) are treated as infrastructure too.
infrastructure_stacks = ["postgresql", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry"]

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
`src/lmu/terminal_status_panel/render/logos/*.ans`. They are plain ANSI, so they
render in MOTD and over SSH without any image protocol or runtime dependency.
The correct logo is chosen automatically from the detected distribution
(Debian / Ubuntu / generic Linux).

To regenerate them (dev only, needs Pillow — `pip install -e '.[dev]'`), drop
source PNGs into `assets/logos/` and run:

```bash
python tools/generate_logos.py
```

## License

EUPL-1.2
