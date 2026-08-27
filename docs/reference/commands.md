# Commands and command-line options

The dashboard is split into four independently runnable sections, each with
its own entry point — plus the combined command:

| Command | Sections | Use |
|---------|----------|-----|
| `status-full` | server + docker + health + traefik | The full panel (default). |
| `status-server` | server only | System overview, updates, load/mem/fs. |
| `status-docker` | docker only | The Docker Swarm block. Collects no health, so a clustered service's **Working** cell falls back to Docker's own measurement — `⬜` only when Docker itself has nothing stronger to say (fully staffed or scaled to zero), still `💀`/`⚠️` for a row Docker measured dead or degraded — pair it with `status-health` to get the cluster verdicts. |
| `status-health` | health only | Clustered infrastructure services, WireGuard peers, DNS. |
| `status-traefik` | traefik only | Traefik's entrypoint → router → middleware → service wiring, **as configured** — the same block `status-full` shows, without the rest of the panel. |

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

Any of the five works in the profile.d snippet (see *Running it at login*) —
e.g. call `status-docker` or `status-health` on Docker Swarm nodes,
`status-server` on plain servers, and `status-traefik` wherever you want to
check what Traefik is actually wired to serve.

# Usage

```bash
status-full    [--sections server,docker,health,traefik] [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-server  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-docker  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-health  [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
status-traefik [--width N] [--no-color] [--config PATH] [-f|--follow] [--interval N] [--debug]
```

The command **always exits 0** so it can never break a login shell. If a
collector fails (no Docker socket, non-Debian host, …) that section degrades to
a placeholder instead of erroring.

An error the collectors do not anticipate is swallowed too, and that used to be
the end of it: an empty panel with no way to ask why. `--debug` lifts the
silence without changing the contract — it still exits 0, and it still renders
whatever it can. See {doc}`Diagnosing an empty panel </how-to/diagnose-an-empty-panel>`.

## Command-line options

| Option        | Default | Description |
|---------------|---------|-------------|
| `--sections`  | *(per command)* | Comma-separated sections to render: `server`, `docker`, `health`, `traefik`. On `status-full` the default is all four; the dedicated commands fix their own section. The wiring block renders identically however it is selected. |
| `--width N`   | *(auto)* | Force the render width to `N` columns. Overrides both auto-detection and the config `width`. |
| `--no-color`  | off     | Disable ANSI colours (plain text — useful for piping/debugging). Also suppresses the entrypoint/router hyperlinks in TRAEFIK WIRING (see {doc}`Traefik wiring </explanation/traefik-wiring>`), for a terminal that renders OSC-8 badly. |
| `--config PATH` | *(see below)* | Load configuration from `PATH` instead of the default location. A missing file is not an error (defaults are used). |
| `-f`, `--follow` | off | Keep the panel on screen and refresh it, on all five commands. See {doc}`Follow mode </explanation/follow-mode>` below. |
| `--interval N` | *(see below)* | Seconds between refreshes under `--follow`. Overrides both the config and the built-in default; values below 1 second are raised to 1 second. Ignored without `--follow`. |
| `--processes N` | *(see below)* | Rows per process list in the TOP CPU / TOP RAM row. Overrides `[resources] top_processes` (default `5`). `0` turns the whole row off — see {doc}`Top processes </explanation/top-processes>` below. A negative value counts as `0`. |
| `--debug`     | off     | Report config problems and any unexpected error on stderr. Still exits 0, still renders the panel; stdout stays the panel alone, so a pipe into an MOTD file is unaffected. `TERMINAL_STATUS_PANEL_DEBUG=1` does the same for a login shell whose profile snippet you would rather not edit. See {doc}`Diagnosing an empty panel </how-to/diagnose-an-empty-panel>`. |
