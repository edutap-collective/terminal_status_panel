# lmu.terminal_status_panel

A small Python package that renders a colorful server status panel on login
via `update-motd.d`. It shows system identity, load & per-core CPU usage,
memory/swap and filesystem bars, pending package updates, and the health of
Dockerized services. Docker Swarm services are grouped by stack, with per-task node placement and
per-node state markers (✅ running, 💀 failed, ❌ unassigned) plus an optional
description label — all read from the Docker API (no database or broker
protocol is ever spoken to).

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

## License

EUPL-1.2
