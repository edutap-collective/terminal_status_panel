# Commands and command-line options

The dashboard is split into five independently selectable sections, each with
its own entry point (the TROUBLE section shares one with the Docker block) —
plus the combined command and the collector:

| Command | Sections | Use |
|---------|----------|-----|
| `status-full` | server + health + trouble + docker + traefik | The full panel (default). On a host without a Docker endpoint (`DOCKER_HOST` unset and no `/var/run/docker.sock`) the `docker` and `traefik` sections are left out; `sections` in the configuration overrides the choice. |
| `status-server` | server only | System overview, updates, load/mem/fs, and the PSI row. |
| `status-docker` | trouble + docker | The TROUBLE block above the Docker Swarm block. TROUBLE is the same section as everywhere else, so it includes the host events from the collector too. Collects no health, so a clustered service's **Working** cell falls back to Docker's own measurement — `⬜` only when Docker itself has nothing stronger to say (fully staffed or scaled to zero), still `💀`/`⚠️` for a row Docker measured dead or degraded — pair it with `status-health` to get the cluster verdicts. |
| `status-health` | health only | THIS HOST (systemd, VRRP, Apache), the DNS checks, clustered infrastructure services and WireGuard peers. Runs without Docker. |
| `status-trouble` | trouble only | What fell over in the last twelve hours: containers (when Docker answers) and host events from the collector. Prints nothing when there is nothing to report. |
| `status-traefik` | traefik only | Traefik's entrypoint → router → middleware → service wiring, **as configured** — the same block `status-full` shows, without the rest of the panel. |
| `status-collect` | — | Not a panel. One collection of the privileged host data (VRRP, Apache rate, Debian updates, journal events) written to the state file `/run/terminal-status-panel/host.json`, then exit. Run by a systemd timer; see {doc}`/how-to/install-the-collector`. Exits 0, or 1 with one line on standard error when it cannot write the file. Takes `--config PATH`. |
| `install-panel` | — | Installs the login snippet; with `--collector` the collector's service and timer instead (see below). |

Each section only collects the data it needs: `status-docker` never touches
the system collectors, `status-server` never opens the Docker socket, and
`status-health` never touches the system collectors either (though it does
open the Docker socket, to `exec` into service containers) — so you can run
just what a given host cares about. `status-traefik` also opens the Docker
socket, to list Swarm services and configs and to read the service states its
tree renders verdicts from (the DOCKER INFOS block itself stays unrendered),
but runs no `exec` and reaches no network beyond Docker unless the optional
`[traefik]` cross-check is configured (see
{doc}`Traefik wiring </explanation/traefik-wiring>`). The combined command also
accepts `--sections` with any comma-separated subset to pick explicitly, e.g.
`--sections docker,traefik` for the two Docker-facing blocks alone.

The wiring block is the same either way — one rendering, packed into
height-balanced columns — so nothing is visible in `status-traefik` that a
login does not also show.

Any of the panel commands works in the profile.d snippet (see *Running it at login*) —
e.g. call `status-docker` or `status-health` on Docker Swarm nodes,
`status-server` on plain servers, and `status-traefik` wherever you want to
check what Traefik is actually wired to serve.

# Usage

```bash
status-full    [--sections server,health,trouble,docker,traefik] [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-server  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-docker  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-health  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-trouble [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-traefik [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-collect [--config PATH]
install-panel  [--scope global|user] [--panel NAME] [--shell auto|bash|zsh] [--uninstall] [--dry-run]
install-panel  --collector [--uninstall]
```

The panel commands **always exit 0** so it can never break a login shell. If a
collector fails (no Docker socket, non-Debian host, …) that section degrades to
a placeholder instead of erroring.

An error the collectors do not anticipate is swallowed too, and that used to be
the end of it: an empty panel with no way to ask why. `--debug` lifts the
silence without changing the contract — it still exits 0, and it still renders
whatever it can. See {doc}`Diagnosing an empty panel </how-to/diagnose-an-empty-panel>`.

## Command-line options

| Option        | Default | Description |
|---------------|---------|-------------|
| `--sections`  | *(per command)* | Comma-separated sections to render, in the order given: `server`, `health`, `trouble`, `docker`, `traefik`. On `status-full` the default is all five (fewer without a Docker endpoint, or as set by `sections` in the configuration); the dedicated commands default to their own sections, and `--sections` overrides that too. The wiring block renders identically however it is selected. An unknown name is ignored; if no name is valid, the default applies. |
| `--width N`   | *(auto)* | Force the render width to `N` columns. Overrides both auto-detection and the config `width`. |
| `--no-color`  | off     | Disable ANSI colours (plain text — useful for piping/debugging). Also suppresses the entrypoint/router hyperlinks in TRAEFIK WIRING (see {doc}`Traefik wiring </explanation/traefik-wiring>`), for a terminal that renders OSC-8 badly. |
| `--config PATH` | *(see below)* | Load configuration from `PATH` instead of the default location. A missing file is not an error (defaults are used). |
| `-f`, `--follow` | off | Keep the panel on screen and refresh it, on every panel command. See {doc}`Follow mode </explanation/follow-mode>` below. |
| `--interval N` | *(see below)* | Seconds between refreshes under `--follow`. Overrides both the config and the built-in default; values below 1 second are raised to 1 second. Ignored without `--follow`. |
| `--processes N` | *(see below)* | Rows per process list in the TOP CPU / TOP RAM row. Overrides `[resources] top_processes` (default `5`). `0` turns the whole row off — see {doc}`Top processes </explanation/top-processes>` below. A negative value counts as `0`. |
| `--debug`     | off     | Report config problems and any unexpected error on stderr. Still exits 0, still renders the panel; stdout stays the panel alone, so a pipe into an MOTD file is unaffected. `TERMINAL_STATUS_PANEL_DEBUG=1` does the same for a login shell whose profile snippet you would rather not edit. See {doc}`Diagnosing an empty panel </how-to/diagnose-an-empty-panel>`. |

## `install-panel --collector`

```bash
sudo install-panel --collector            # install or update
sudo install-panel --collector --uninstall
```

Installs (or removes) the collector's `terminal-status-panel-collect.service`
and `.timer` in `/etc/systemd/system` and the keepalived helper in
`/usr/lib/terminal-status-panel`. It needs root and ignores the login-snippet
options. Installing prints `written` when something changed (systemd is then
reloaded and the timer enabled) and `unchanged` when the installed files
already match. Removing prints `removed` or `nothing to remove`; it clears the
runtime directory `/run/terminal-status-panel` but leaves the systemd state
directory `/var/lib/terminal-status-panel`.

The unit runs the `status-collect` that sits next to the interpreter running
`install-panel`. An interpreter below `/home`, `/root` or `/run/user` is
refused with exit status 1, because the service's `ProtectHome=yes` would hide
it; the message recommends a virtualenv under `/opt/terminal-status-panel`
installed with pip. Without root, the command exits 1 and asks for `sudo`. See
{doc}`/how-to/install-the-collector`.
