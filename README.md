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
