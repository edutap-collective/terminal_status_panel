# terminal-status-panel

A small Python package that renders a colorful server status panel on login —
best run from a `profile.d` snippet so it uses the full terminal width (see
[Running it at login](#running-it-at-login)). The full-width dashboard is laid
out in five tiers:

- **SYSTEM OVERVIEW** (with a real, pre-rendered OS logo) beside **UPDATES**.
- **SYSTEM STATUS** — load & per-core CPU usage, memory/swap, a filesystem
  usage table, and a **TOP CPU** / **TOP RAM** row: the processes ranked by
  each, five by default and configurable via `[resources] top_processes` or
  `--processes`. See [Top processes](#top-processes) below for what that
  ranking measures, what it costs, and how to size or turn off the block.
- **DOCKER INFOS** — Swarm key facts (summary + node health) above the node
  matrices: an *Infrastructure* and a *Service* table per origin — under
  **SWARM STACKS** and **COMPOSE PROJECTS**, each block omitted entirely when
  that origin has nothing — and one shared *Standalone containers* table below
  them for everything that belongs to no stack or project.
  Node health has three states: ✅ ready and active, ⚠️ ready but drained or
  paused (it accepts no tasks), 💀 unreachable; the summary line counts the
  non-operational ones, e.g. `5 nodes (1 drain, 1 down)`.
  The summary also carries the engine version, and the node list carries it
  only where a node disagrees — see
  [Engine versions and manager reachability](#engine-versions-and-manager-reachability),
  which also covers the quorum warning for a manager the other managers
  cannot reach. A **Disk** line reports what Docker itself occupies on this
  node and how much of it can be reclaimed
  ([Docker's own disk footprint](#dockers-own-disk-footprint)). Where
  something fell or never started within the last twelve hours, a
  [TROUBLE block](#the-trouble-block) appears between the summary and the
  stacks and says why — and where nothing did, it does not exist at all.
  Each row also reports the memory its tasks hold on this node
  ([Memory per service](#memory-per-service)).
  Per-node replicas of the same service (e.g. `kafka_kafka-<node>` on every
  node) collapse into a single row; a stack with one logical service shows as
  one row named after the stack, a stack with several shows a header plus one
  sub-row per service (stack prefix and node name stripped). A trailing
  `_<digits>` is stripped the same way, so ordinal instances collapse too — a
  service deployed as `connector_1`, `_2`, `_3` (one pinned instance per
  node, each with its own secrets) renders as a single `connector` row.
  That stripping is unconditional, and it has an accepted cost: if one
  instance is later removed from the deployment — or scaled to zero — its
  row just folds into the remaining two, and the panel reads `✅ 2/2` with
  nothing to say a third instance was ever expected. The gap is invisible
  only across a *deployment* change, never a *failure* — a failing instance
  still has a desired replica and renders `💀` or `⚠️` in both the Working
  column and its node cell, so a lost instance is silent but a broken one is
  not. Each row also
  carries a **Working** cell right after the service name: an icon plus the
  row's running/desired task count, e.g. `✅ 3/3`, `⚠️ 2/5`, `💀 0/3`, `⬜ 0/0`.
  Five rules keep that cell from over-claiming:

  - a service scaled to zero replicas renders `⬜ 0/0`, not `💀` — that is a
    decision, not an outage;
  - for the four clustered services that actually run as Docker services
    (PostgreSQL, MongoDB, Kafka, RustFS — GlusterFS is queried on the host via
    `sudo -n`, not as a container, so it never gets a DOCKER INFOS row) the
    **icon** comes from that service's own CLUSTER HEALTH verdict while the
    **count** stays Docker's running/desired tally, so the two are allowed to
    disagree on purpose — except that a row measured `💀` or `⚠️` by its own
    replicas keeps that icon: no cluster-level `✅` or `⬜` can talk a row with
    nothing (or not everything) running into looking healthy;
  - under `status-docker`, which runs without the health section, a clustered
    service's cell falls back to `⬜` plus Docker's own count instead of a
    replica-derived `✅` — "five brokers are running" is not the claim this
    column makes — and the same fallback applies when the probe found no
    member of that service on this node, or the kind is not listed in
    `health.enabled`. Withholding the cluster's claim does not withhold
    Docker's, so such a row still renders `💀 0/3` when nothing runs and
    nothing is still starting;
  - a service where at least one task is still starting (not yet in running
    state) renders `⚠️` in the **Working** cell's icon in place of `💀`, since
    a deploy in progress is not the same as a measured outage — a single
    starting task renders the bare icon `⚠️`, and a row with no running tasks
    but some still starting renders `⚠️ 0/n`. This differs from the node cell,
    which only shows `⚠️ 0/N` when *all* tasks on that node are starting; a
    node cell with a mix of starting and failed tasks still shows `💀 0/N`.
  - a service that runs to completion — a **scheduled job** — reports the
    outcome of its last run instead of a replica count, because between runs
    it has none and `💀 0/1` would state the opposite of the truth. A job is
    recognised either by the [swarm-cronjob](https://github.com/crazy-max/swarm-cronjob)
    labels (`swarm.cronjob.enable=true`) or by Swarm's own job modes
    (`ReplicatedJob`/`GlobalJob`, Docker 20.10+). Its cell reads `⏰ ok 12h`
    when the last run completed, `💀 ✗ 20h` when it failed, `⬜ never` when
    there is no run to measure, and an ordinary `✅ 1/1` while a run is in
    progress. The outcome is taken from the *newest* task, not the most severe
    one: a job that failed yesterday and succeeded this morning is healthy.
    Where the job carries a cron expression, it leads the **Description**
    column (`0 5 * * * · nightly export`) — without it, "ok 12h" cannot be
    told apart from a job that quietly stopped being triggered. Note that the
    panel does **not** judge whether a job is overdue; that would require
    evaluating the cron expression against the clock.

    The controller that drives these jobs — swarm-cronjob itself — is
    classified as **Infrastructure**, not as a Service: it carries no data and
    serves no user, and filing it among the jobs it triggers is precisely
    where nobody looks when asking why nothing ran last night. Its absence is
    worth noticing, too: labelled jobs with no controller running are jobs
    nothing will ever start, and the panel shows each of them resting at
    `⏰ ok <age>` until the age itself gives it away.

  A global-mode service (Traefik here) reports no replica count, so the
  denominator is the number of tasks Swarm actually scheduled — the same one
  `docker service ls` shows. Counting against every node instead would render
  a healthy global service as degraded for as long as one node is drained or a
  placement constraint applies.

  RustFS is the worked example for the second rule: its own CLUSTER HEALTH
  block can read `3/5 live` (a minority of members measured unhealthy while
  the majority quorum still holds) even though every RustFS container is
  still up as a Docker task, so the row here reads `⚠️ 5/5` — a bare replica
  count with no cluster context would have shown a clean `✅ 5/5` and missed
  the degradation entirely. A member that was simply never measured
  (`healthy is None` — MongoDB reports its replica-set members but not their
  state) does not raise this warning; not measured is not a failure.

  Columns are otherwise the nodes (alphabetical), plus a description column.
  A node cell holding a single task keeps the bare glyph, ✅, ⚠️, or 💀 — the
  overwhelming majority of cells, since most rows place one task per node —
  and from two tasks up it carries the count right after the glyph, with no
  space in between: `✅N` when all run, `⚠️X/N` when some run, `⚠️0/N` when none
  run and *all* are in a starting state, `💀0/N` when none run and at least one
  has failed or entered another non-starting state. (Grouping can put more than one task on the same node — after
  ordinal stripping merges two originally separate services into one row,
  both can land on the same node, and that node's cell counts both.) See
  [Icon vocabulary](#icon-vocabulary) for what each glyph means — the Working
  column uses exactly the same vocabulary, no new glyphs, including `✗` when
  the underlying cluster probe itself failed (only `…`, the time-budget
  marker, never appears in this column).
- **CLUSTER HEALTH** — the clustered infrastructure services this node
  participates in (PostgreSQL, MongoDB, Kafka, GlusterFS, RustFS) with
  leader/member state, WireGuard peer handshake ages (TCP-probe fallback when
  passwordless sudo is unavailable), and DNS consistency checks. Every check
  runs concurrently under a shared time budget (default 5 s); a check that
  runs out renders `…`, deliberately distinct from `✗` for a check that
  actually failed. Each applicable service gets its own block (leader,
  members, warnings), and the blocks flow into as many columns as the
  terminal width allows — one column on a narrow terminal; services with no
  member on this node are not given a block each but summarised together on a
  single dim `n/a here: <names>` line. A service that *should* run here but
  has no running container (most likely a crash loop) still gets its own
  block, reading `✗ <service>: no running container` instead. See
  [Cluster health checks](#cluster-health-checks)
  below for the full icon vocabulary and what the section deliberately
  cannot see.
- **TRAEFIK WIRING** — Traefik's wiring **as configured**: one branch per
  entrypoint, each with its routers, their middlewares, and the service they
  point at. The branches are packed into as many height-balanced columns as
  the terminal allows, in the order the Traefik service's own arguments
  declare them — the four every cluster has (`dashboard`, `ping`, `default`,
  `https`) before this cluster's per-vhost ones. Routers whose entrypoint
  does not exist get their own block below, at full width, since a tree
  keyed by entrypoint would otherwise drop them silently. An entrypoint head
  and a router name become clickable when `[traefik.links]` configures where
  that entrypoint is actually reached. See
  [Traefik wiring](#traefik-wiring) below for what "as
  configured" means, for the clickable links, and for the optional (currently
  dormant) live cross-check against Traefik itself.

The panel itself opens no database or broker connection and holds no
credentials. Its only privilege is the Docker socket: the Docker section
reads the Swarm API, and the health section additionally executes
**read-only status commands inside the service containers**
(`pg_autoctl show state`, `db.hello()`, `kafka-metadata-quorum.sh`, a
`/health` curl). GlusterFS is queried on the host via `sudo -n` and is
skipped when that is unavailable.

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

This installs five panel commands (`status-full`, `status-server`,
`status-docker`, `status-health`, `status-traefik`) plus an `install-panel`
helper to wire it into a login shell.

## Commands (sections)

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
[Traefik wiring](#traefik-wiring)). The combined command also
accepts `--sections` with any comma-separated subset to pick explicitly, e.g.
`--sections docker,traefik` for the two Docker-facing blocks alone.

The wiring block is the same either way — one rendering, packed into
height-balanced columns — so nothing is visible in `status-traefik` that a
login does not also show.

Any of the five works in the profile.d snippet (see *Running it at login*) —
e.g. call `status-docker` or `status-health` on Docker Swarm nodes,
`status-server` on plain servers, and `status-traefik` wherever you want to
check what Traefik is actually wired to serve.

## Usage

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
whatever it can. See [Diagnosing an empty panel](#diagnosing-an-empty-panel).

### Command-line options

| Option        | Default | Description |
|---------------|---------|-------------|
| `--sections`  | *(per command)* | Comma-separated sections to render: `server`, `docker`, `health`, `traefik`. On `status-full` the default is all four; the dedicated commands fix their own section. The wiring block renders identically however it is selected. |
| `--width N`   | *(auto)* | Force the render width to `N` columns. Overrides both auto-detection and the config `width`. |
| `--no-color`  | off     | Disable ANSI colours (plain text — useful for piping/debugging). Also suppresses the entrypoint/router hyperlinks in TRAEFIK WIRING (see [Traefik wiring](#traefik-wiring)), for a terminal that renders OSC-8 badly. |
| `--config PATH` | *(see below)* | Load configuration from `PATH` instead of the default location. A missing file is not an error (defaults are used). |
| `-f`, `--follow` | off | Keep the panel on screen and refresh it, on all five commands. See [Follow mode](#follow-mode) below. |
| `--interval N` | *(see below)* | Seconds between refreshes under `--follow`. Overrides both the config and the built-in default; values below 1 second are raised to 1 second. Ignored without `--follow`. |
| `--processes N` | *(see below)* | Rows per process list in the TOP CPU / TOP RAM row. Overrides `[resources] top_processes` (default `5`). `0` turns the whole row off — see [Top processes](#top-processes) below. A negative value counts as `0`. |
| `--debug`     | off     | Report config problems and any unexpected error on stderr. Still exits 0, still renders the panel; stdout stays the panel alone, so a pipe into an MOTD file is unaffected. `TERMINAL_STATUS_PANEL_DEBUG=1` does the same for a login shell whose profile snippet you would rather not edit. See [Diagnosing an empty panel](#diagnosing-an-empty-panel). |

### Diagnosing an empty panel

A value in the config file that cannot be read does not stop the panel: the
built-in default is used instead, and the panel renders. What it does *not* do
is guess — `thresholds.memory.warning = "soon"` is not a threshold, and the
file is not silently treated as if it said something else.

`--debug` prints what was skipped and why:

```console
$ status-full --debug --config /etc/terminal-status-panel/config.toml
config: thresholds.memory.warning: expected a number (found 'soon', using 75.0)
config: docker.show_image: expected true or false (found 'maybe', using True)
```

With a clean file it says so, rather than printing nothing — silence would not
distinguish "no problems" from "the flag did nothing".

Two readings are worth knowing about because they are easy to write by
accident:

- **A quoted boolean is read as written.** `show_image = "false"` means false.
  Python's own `bool("false")` is `True`, so this used to mean the opposite of
  what it said. TOML has a real boolean and `show_image = false` remains the
  right way to write it; the quoted form is forgiven, not preferred.
- **A value nobody can have meant falls back rather than being honoured.** A
  `width` below 20 leaves no room for one column of content, and a `timeout` of
  0 aborts every call before it starts. These are reported, not clamped: a typo
  clamped to the nearest legal value disappears behind plausible behaviour.

If the panel is empty because something raised rather than because of the
config, `--debug` names the stage and the exception:

```console
$ status-full --debug
failed while collecting the data: PermissionError: [Errno 13] /var/run/docker.sock
```

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

### Follow mode

`-f` / `--follow` keeps the panel on screen and redraws it on an interval,
on all five commands, in place of a single one-shot render.

**The default interval depends on which sections are collected, not on which
command you ran.** If `health` is among the requested sections, the default
is 20 s (config key `[follow] health_interval`); otherwise it is 5 s (`[follow]
interval`). That is a rule about sections rather than a per-command table, so
it is also correct for a combination like `--sections docker,health` on
`status-full`. `--interval N` on the command line overrides both.

The health section earns the longer default: it runs `docker exec` probes
inside the cluster's containers, and the Kafka probe alone carries roughly
2.6 s of JVM startup (see [Cluster health checks](#cluster-health-checks)).
Measured on a five-node reference cluster at width 200, median of three runs:
`status-health` takes 3.43 s per pass against `status-server`'s 0.49 s — a
5 s cadence would start a new JVM on the cluster every five seconds, forever.

**Ctrl-C stops it.** There is no `q` key or other in-panel control; reading a
single keypress would need raw mode, and Ctrl-C already does the job.

**The panel is cropped to the screen**, with a status line at the bottom
naming what does not fit — `↓ 82 more lines · every 20s · Ctrl-C to stop` —
and the `↓` clause absent once everything fits. On the same reference
cluster, `status-full` renders 131 lines at width 200, taller than a normal
terminal, so it will be cropped there; each of the four section commands
(22 to 51 lines) fits on an ordinary screen.

**Without a TTY, `--follow` renders one frame and returns**, the same as a
plain run — piping the output to a file or generating a cached MOTD still
works, rather than looping forever inside a pipe.

## Configuration

Zero configuration is required. Settings are read from
`/etc/terminal-status-panel/config.toml` (override with `--config PATH`). A missing
or unreadable file falls back to the built-in defaults — it never raises.

### Configuration reference

| Key | Default | Meaning |
|-----|---------|---------|
| `width` | `80` | Fallback render width when no TTY is available (see width resolution above). |
| `docker.timeout` | `1.5` | Seconds to wait for the Docker socket before giving up (also bounds the `apt` update check). Keeps a hung/absent daemon from delaying login. |
| `docker.description_label` | `"status.description"` | Docker **service label** read as the per-service description column. The key `lmu.service.description` is still read as a fallback. |
| `docker.group_label` | `"status.group"` | Docker **service label** naming the row a service belongs in. Services of one stack sharing a value render as one row, whatever their names. Where it is absent the name heuristic decides instead — see [Grouping services into one row](#grouping-services-into-one-row). Read for presence, not truthiness: a service setting it to `""` groups with no one. |
| `docker.df_timeout` | `4.0` | Seconds to wait for `/system/df`, the Docker disk reading — deliberately larger than `docker.timeout` and spent on a **separate client**. The call was measured at 510 ms against a daemon holding 47 images and 185 volumes, and it grows with the object count. Overrunning it costs the one line, never the whole DOCKER INFOS section. See [Docker\'s own disk footprint](#dockers-own-disk-footprint). |
| `docker.show_image` | `true` | Whether the DOCKER INFOS rows carry an **Image** column right of the description (see [The image column](#the-image-column)). It is the column that answers "which version is deployed here", and the one that costs the description its width on a narrow terminal — `false` removes it. |
| `resources.ignore_mountpoints` | platform-dependent | Mountpoint prefixes hidden from the filesystem table. Defaults to `["/System/Volumes/", "/Library/Developer/CoreSimulator/"]` on macOS and to `[]` elsewhere. An explicitly empty list hides nothing rather than falling back to the default. |
| `resources.process_sample` | `0.3` | Seconds to sample process CPU usage over for the TOP CPU row (see [Top processes](#top-processes)). `0` or less disables the CPU ranking; TOP RAM is unaffected. |
| `resources.top_processes` | `5` | Rows per process table in the TOP CPU / TOP RAM row (see [Top processes](#top-processes)). `--processes N` on the command line wins over this. A value that cannot be read as a whole number falls back to `5`; a negative value means `0`. `0` removes the whole row, and with it the `process_sample` sampling wait — a different switch from `process_sample`, which only removes the CPU ranking and leaves TOP RAM in place. |
| `docker.infrastructure_stacks` | `["postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql", "elasticsearch", "bugsink", "swarm-cronjob", "swarm_cronjob"]` | Case-insensitive substrings. A **stack** (or Compose project) whose name matches goes into that origin's **Infrastructure** table; every other stack goes into **Service**. An entry with no stack at all is never classified this way — it has no project to be filed under, so `docker run -d redis` lands in **Standalone containers** like any other stackless entry, however infrastructural its name. |
| `docker.infra_ui_services` | `["kafbat-ui", "kafka-ui", "kafdrop", "cloudbeaver", "pgadmin", "adminer", "mongo-express", "mongo-gui", "rustfs-console", "rustfs-ui", "s3-browser", "s3browser", "redisinsight", "redis-commander", "portainer", "dozzle", "kibana"]` | Case-insensitive substrings matched against the stack name **and** the service name. Matching services leave their own stack and are collected as sub-rows of the pseudo stack **`infra-uis`**, shown first in the **Infrastructure** block. On a name matching both lists, this one wins. A sidecar pulled in only because its *stack* name matched (e.g. `portainer_agent`) is labelled `stack/service` so it stays attributable once detached. |
| `services.critical` | `[]` | Service names flagged as critical (parsed and available on the data model; not visually emphasised in the current matrix view). |
| `thresholds.memory.warning` / `.critical` | `75` / `90` | RAM usage % thresholds (yellow / red). |
| `thresholds.swap.warning` | platform-dependent | Swap usage % above which SWAP turns yellow. Defaults to `80` on macOS, which allocates swap continuously by design, and to `1` elsewhere. An explicit value overrides both. |
| `thresholds.filesystem.warning` / `.critical` | `80` / `90` | Filesystem usage % thresholds. |
| `thresholds.load.warning` / `.critical` | `0.8` / `1.0` | Load-average thresholds as a **per-CPU multiplier** (compared against `load1 / cpu_count`). |
| `health.budget` | `5.0` | Total wall-clock budget in seconds for all health checks. Every check runs concurrently as its own task, so this bounds the login delay — it is not the sum of the individual timeouts. |
| `health.timeout.*` | postgres `1.5`, mongodb `2.5`, kafka `4.0`, glusterfs `1.0`, rustfs `2.0`, wireguard `1.0`, dns `2.5` | Deadline for one check. Each cluster kind, the peer check and the DNS check are separate tasks; a task that overruns its value is reported as `… <name>: time budget exceeded` while every other check keeps its result. Values above `health.budget` have no effect — the budget always wins. See [How the timeouts are enforced](#how-the-timeouts-are-enforced). |
| `health.enabled` | all five kinds | Which cluster kinds to probe: `postgres`, `mongodb`, `kafka`, `glusterfs`, `rustfs`. |
| `health.dns.expect` | `[]` | Array of `{name, addresses}`. `addresses` is optional; without it the name only has to resolve at all. |
| `follow.interval` | `5.0` | Refresh interval in seconds for `--follow` when the `health` section is **not** among those requested (see [Follow mode](#follow-mode)). |
| `follow.health_interval` | `20.0` | Refresh interval in seconds for `--follow` when the `health` section **is** among those requested. |
| `traefik.url` | *(unset)* | URL of Traefik's `/api/rawdata` endpoint for the optional live cross-check. Leave unset — see [Traefik wiring](#traefik-wiring) for why it cannot work on today's app servers. |
| `traefik.cert` / `traefik.key` | *(unset)* | Client certificate/key for that endpoint (mTLS). Both `url` and `cert` must be set for the cross-check to run at all. |
| `traefik.ca` | *(unset)* | CA bundle to verify the endpoint's server certificate. Unset, the **system trust store** applies — `ssl.create_default_context()` loads OpenSSL's default paths, so a corporate CA installed in `/etc/ssl/certs` *is* picked up, and `SSL_CERT_FILE`/`SSL_CERT_DIR` override them as usual. Set this only for a CA the system does not know; doing so replaces the system roots rather than adding to them. The HTTP library's own default never applies here — the cross-check requires `traefik.cert`, so the request always carries an explicitly built `SSLContext`. |
| `traefik.links` | `{}` | Table mapping an entrypoint **name** to the `http://` or `https://` base URL it is actually reached at, e.g. `login_example_de = "https://login.example.de"`. Independent of `traefik.url`/`cert`/`key`/`ca` above — it needs no connection to Traefik at all. See [Traefik wiring](#traefik-wiring) for why this has to be configured rather than derived. A value that is not a string, or does not start with `http://`/`https://`, is dropped; that entrypoint then simply has no links, the same as leaving it out. |

### Full example

```toml
# Fallback width for non-TTY (MOTD) rendering. Interactive logins auto-detect
# the real terminal width regardless of this value.
width = 200

[docker]
timeout = 1.5
description_label = "status.description"
show_image = true
infrastructure_stacks = ["postgresql", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry"]
infra_ui_services = ["kafbat-ui", "cloudbeaver", "mongo-express", "rustfs-console"]

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

[health]
budget = 5.0
enabled = ["postgres", "mongodb", "kafka", "glusterfs", "rustfs"]

[health.timeout]
postgres = 1.5
mongodb = 2.5
kafka = 4.0
glusterfs = 1.0
rustfs = 2.0
wireguard = 1.0
dns = 2.5

[[health.dns.expect]]
name = "login.example.net"
addresses = ["10.9.9.9"]

[traefik.links]
login_example_de = "https://login.example.de"
portal_dept_uni_example_de = "https://portal.dept.uni-example.de"

[resources]
process_sample = 0.3
top_processes = 5

[follow]
interval = 5.0          # sections without health
health_interval = 20.0  # sections including health
```

## Top processes

The SYSTEM STATUS block ends with two tables side by side, **TOP CPU** and
**TOP RAM**, each with `%CPU`, `%MEM`, `MEM`, `PID`, `PROCESS` and `SERVICE`
columns — five rows per table by default.

**`MEM` is the resident set size, the same figure `%MEM` is a percentage
of.** psutil's `memory_percent()` is computed from `rss`, and `MEM` shows
that same `rss` value, formatted with the helper the MEMORY & SWAP block
above uses (`format_bytes`), so a size reads the same everywhere in the
panel — `2.0 GB` here is the same `2.0 GB` it would be up there. A process
the collector could not read carries a dash, `—`, in both columns rather
than mixing that with `format_bytes`'s own `n/a` for one row.

**Row count: `[resources] top_processes`, or `--processes N` on the command
line.** The default is `5`; the flag wins whenever both are given. A config
value that cannot be read as a whole number falls back to `5`, the same as
an unset one; a negative value — from either source — means `0`, not the
default.

**`0` turns the block off, and with it the CPU-sampling wait.** With
`--processes 0` (or `top_processes = 0`), the TOP CPU / TOP RAM row does not
render at all, and the collector skips the sampling window described below
entirely rather than measuring it and discarding the result — that window is
real cost on a login path, and removing it is the reason this knob exists.
Measured on a development machine, `--processes 3` took about 0.69 s wall
clock; `--processes 0` took about 0.29 s.

**`top_processes` and `process_sample` are different switches, and they do
different things at zero.** A row count of `0` removes the whole block,
sampling wait included. A sample of `0` (or less) leaves the block in place
and turns off only the CPU ranking: **TOP CPU** then reads
`CPU sampling is off` and **TOP RAM** renders alone. Setting the wrong one
either keeps paying the sampling cost while meaning to stop it, or drops
**TOP RAM** along with **TOP CPU** while meaning to keep it.

**`%CPU` is sampled over a window, not the lifetime average `ps` reports.**
`ps -eo %cpu` divides total CPU time by elapsed time since the process
started, so a container that has been running for weeks barely moves
whatever it is doing right now. This panel primes every process, waits, and
reads instead, so the figure is the share of CPU actually used during that
window — and the `TOP CPU` heading names the window it used, e.g.
`TOP CPU (0.3s)`.

That window is the `[resources] process_sample` config key, `0.3` seconds by
default, and it is real cost on a login path: sampling roughly 400 processes
measured at 0.32 s wall clock on a five-node reference cluster. Set it to `0`
or less and the CPU ranking turns off entirely — the row then reads
`CPU sampling is off` in place of a table, rather than rows of `0.0` that
would read as a measurement rather than its absence — and **TOP RAM** alone
remains.

**`SERVICE` is read from `/proc/<pid>/cgroup`.** A process running under a
systemd unit shows that unit's name verbatim. A process running inside a
container shows the container's short ID, and that ID resolves to a service
name only when the Docker section was also collected and can map it — which
is why `status-server` on its own shows IDs rather than names: it never
opens the Docker socket, deliberately, so it has nothing to resolve the ID
against.

**On a narrow terminal, the two tables stack instead of squeezing.** The
panel measures each table's natural width and lays `TOP CPU` beside
`TOP RAM`, with a gap between them, only when both fit the terminal as they
are — verified at width 200 and at 120, where both render in full side by
side, with only `SERVICE`'s own 22-character cap ever cutting a long unit or
service name (`containerd-shim`, unremarkable on a Docker host, reads in
full at either width). Once the pair no longer fits — verified at width 80,
which is `Config.width`'s default and what `resolve_width` falls back to
whenever stdout is not a TTY, the MOTD-generation path this README already
names — `TOP RAM` moves below `TOP CPU` instead of beside it, each keeping
the full terminal width and its own heading. Rich is never asked to shrink a
pair that does not fit; a pair that does not fit is stacked instead. That is
why the numeric columns — `%CPU`, `%MEM`, `MEM`, `PID` — keep their values
undamaged at every width, which is the guarantee worth making: a shortened
name is still shorter, but a shortened number would simply be wrong.

The panel excludes its own process from both rankings — the same reason
`ps` habitually ranks itself first: it is the one process guaranteed to be
running while the measurement happens.

## Platform behaviour

The panel is written for Linux servers but also runs correctly on a
developer's Mac, on FreeBSD, and on the RHEL and SUSE families. This section
is where those differences are collected.

- **Identity.** macOS reads `ProductName` and `ProductVersion` from
  `/System/Library/CoreServices/SystemVersion.plist`, the file both macOS
  itself and `platform.mac_ver()` read; every other system uses
  [`distro`](https://pypi.org/project/distro/), which covers Debian, Ubuntu,
  the RHEL family, the SUSE family and FreeBSD. There is deliberately no
  fallback chain — a system that cannot be identified reports `n/a (OS
  identity unavailable)` rather than inventing a coarser answer. The kernel
  row always names its system as well as its release, e.g. `Darwin 25.5.0` or
  `Linux 6.1.0-18-amd64`, because the release number alone is ambiguous
  between platforms.
- **Filesystems on macOS.** `/` and `/System/Volumes/Data` are two mounts of
  one APFS container; the panel reports the data volume's totals under `/`
  and drops the duplicate data-volume row. Without this merge, `/` reads as a
  reassuring 26 % used on a machine that is in fact 98 % full. The
  `resources.ignore_mountpoints` config key (see above) then hides the
  remaining system volumes and simulator runtimes that would otherwise
  outnumber the real filesystems roughly seven to one.
- **Swap on macOS.** The swap warning threshold defaults to 80 % there,
  rather than the 1 % used everywhere else, because macOS allocates swap
  continuously as a matter of design — the Linux default would warn on every
  healthy Mac.
- **Logos.** The logo is chosen by platform first, distribution second: a Mac
  is a Mac whatever string `distro` produces, so platform identity always
  wins when it applies. Where no platform claims the system, the panel falls
  back to matching the distribution name, and finally to Tux — a true
  statement about the kernel for any Linux distribution without its own
  bundled mark — the RHEL and SUSE families are exactly that case, since their
  marks could not be licensed for redistribution. Systems Tux would
  *misdescribe* never borrow it: macOS gets its product name rendered as block
  lettering rather than an Apple emblem, since no Apple artwork is
  redistributed, OpenBSD and NetBSD get the BSD daemon, and FreeBSD shows no
  logo at all, its own wordmark being illegible at this size. Every bundled
  mark's provenance and licence are recorded in
  [`assets/logos/SOURCES.md`](assets/logos/SOURCES.md).
- **Containers.** Plain `docker run` containers and Docker Compose projects
  now appear alongside Swarm services, not just services from an active
  Swarm. They are grouped by their Compose project into their own `COMPOSE
  PROJECTS` block (mirroring the `SWARM STACKS` block above it); a container
  with no Compose project lands under `Standalone containers`, as does a Swarm
  service created outside any stack — both are standalone, and neither is
  given a project heading it does not belong to. A container
  that exited cleanly (exit code `0`) is treated as finished work and is
  omitted, in both blocks. Beyond a clean exit, the two kinds are **not**
  treated alike: a **Compose** container that exits with a non-zero code
  stays visible and shows as a shortfall against its group, the same way a
  stopped Swarm task would; a **standalone** container has no group to fall
  short against, so it is shown only while it is `running` or `restarting`
  — once it exits, at any exit code, it disappears rather than lingering.
  Without that difference, every one-off `docker run` left behind on a
  development machine would accumulate in the panel forever.

## Cluster health checks

`status-health` (and the health block inside `status-full`) probes the
clustered infrastructure services this node participates in, plus WireGuard
peer reachability and DNS consistency. Every service probe runs a read-only
status command inside a locally running container via the Docker API —
GlusterFS is the one exception, queried on the host via `sudo -n` — so none of
this needs database or broker credentials:

| Service | Command |
|---------|---------|
| PostgreSQL | `pg_autoctl show state` |
| MongoDB | `db.hello()` via `mongosh` (unauthenticated) |
| Kafka (KRaft) | `kafka-metadata-quorum.sh describe --status` |
| GlusterFS | `gluster peer status --xml` / `gluster volume status --xml` via `sudo -n` |
| RustFS | `curl` against `/health` on each configured endpoint |

WireGuard peer reachability is read from `sudo -n wg show all dump`
(handshake age per peer); when that is unavailable it falls back to a plain
TCP probe of the Swarm port (2377) per peer, and the PEERS sub-header shows
which method was used (`wg`, `tcp`, or `mixed` when peers disagree).

The five kinds share **one** listing of the local containers, fetched once per
run without inspecting each container. Asking per probe cost roughly
`kinds × (1 + containers)` Docker round trips at every login — around 205 on a
host with 40 containers — and the per-container inspect could raise a `404`
for a container that stopped mid-listing, which the panel would have shown as
a failed check. Only RustFS needs a container's full attributes (for
`RUSTFS_VOLUMES`), and it inspects only the one container it matched.

Measured on a production manager of a five-node Swarm: the whole section
takes **3.25 s** end to end, within the 5 s default budget, with individual
probe costs of PostgreSQL `pg_autoctl show state` 0.13 s, Kafka
`kafka-metadata-quorum.sh` 2.6 s (JVM startup, not optimisable), GlusterFS
`gluster … --xml` 0.10 s, and RustFS `/health` ~0.2 s. That cluster runs no
MongoDB, so its figure — `db.hello()` 0.95 s — was measured separately on
another cluster.

### How the timeouts are enforced

Every check is its own task: each enabled cluster kind, the peer check and
the DNS check. They all start together and are waited for concurrently, each
until the earlier of its own `health.timeout.<name>` and the shared
`health.budget`. A check that misses its deadline is reported as
`… <name>: time budget exceeded` **for that check alone** — the kinds beside
it keep their results, so a hung RustFS can no longer hide a PostgreSQL
quorum loss that was measured two seconds earlier.

The deadline is what the panel *waits* for; whether the check itself stops
work at that moment depends on what it runs:

- **GlusterFS** and **RustFS** pass their value down to the child process
  they spawn (`subprocess` timeout, `curl -m`), so the work really stops.
  RustFS divides its value between its endpoints, so five endpoints behind a
  blackhole cost the RustFS timeout once, not five times. (Below ~0.1 s per
  endpoint — more than 20 endpoints at the default — the share stops shrinking
  and the task deadline takes over instead.)
- **PostgreSQL, MongoDB and Kafka** run through `docker exec`, and docker-py
  bounds an exec by the *client's socket timeout* rather than per call. Their
  timeout is therefore enforced as the task deadline: the panel stops waiting
  and reports the check as out of budget, while the exec itself finishes in a
  daemon thread whose result is discarded. So that the socket timeout never
  expires *first* — which is what made the default `docker.timeout = 1.5`
  unable to accommodate the ~2.6 s Kafka probe at all — the health section's
  Docker client is built with a socket timeout no smaller than the largest
  enabled `health.timeout.*`. `docker.timeout` continues to bound the DOCKER
  INFOS section exactly as before.

An abandoned check keeps running in a daemon thread, and one that is inside a
child process (`sudo -n wg`, `sudo -n gluster`) when the panel exits would
leave that child orphaned. Such a thread is therefore given a short, bounded
grace period (0.25 s) at exit to finish or to kill its own child — bounded
because the delay would otherwise be paid by the login shell, and only ever
paid when a check has already been abandoned.

The own-hostname lookup the DNS check needs (`socket.getfqdn()`, a forward
plus a reverse lookup through NSS) happens inside the DNS task for the same
reason: with a broken resolver it blocks for tens of seconds, and that is the
very fault this section reports on. The Swarm node list the peer and DNS
checks need is fetched inside the budget too, once, by whichever of the two
gets there first — and only when the Docker section did not already collect
it for free.

Exactly one thing leaves the process before the budget starts: constructing
the Docker client, which negotiates the API version with the daemon. That
request is bounded by `docker.timeout` (1.5 s by default), the knob documented
for it; the larger health timeout is applied to the client afterwards, so it
bounds only the requests made inside the budget.

### A stopped standby is not a slow one

PostgreSQL members carry one of two notices when they are not level with the
primary, and the difference between them is the difference between "wait" and
"act":

```
✅ node-b  secondary  ⚠️ lag 952.0 B
✅ node-c  secondary  ⚠️ lag 101.1 MB
💀 node-d  secondary  ⚠️ TLI 4≠5 → report_lsn
```

**`lag <size>`** means the member trails the primary by that many bytes. It
stays ✅ and counts toward quorum, because a lagging standby catches up on its
own. There is deliberately **no threshold**: we cannot know at what point a
byte count hurts on a given cluster, and 952 B beside 101 MB distinguishes
itself without one. Where the distance cannot be computed — an unreadable LSN,
or a member briefly *ahead* of the primary after a promotion — the size is
omitted rather than guessed.

**`TLI 4≠5`** means the member is on a different timeline than the primary.
That is not a delay, it is a stop: after a failover the promoted node begins a
new timeline, and a standby that did not follow will **never** catch up
without intervention. Such a member is 💀, does **not** count toward quorum,
and therefore turns the whole cluster's verdict red — which in turn reddens the
service row in DOCKER INFOS, even while every container is running.

The `→ assigned` marker stands beside it where a transition is in progress.
Both are needed: one says the node is cut off, the other that the orchestrator
is already working on it.

**Why this is emphatic.** A production cluster once ran for hours with three of
five nodes replicating nothing. Their WAL receivers kept the connections alive
with keepalives, so pg_auto_failover reported them as healthy secondaries, and
the panel — which compared only LSNs and discarded the timeline — reported
`lag`. The service row read a green `5/5`, because the containers were indeed
running. The nodes had stopped 3936 bytes past a fork point and PostgreSQL was
refusing to start them.

Nothing was measured too little. The mildest available interpretation was
chosen, and it hid the severest state behind the most ordinary word.

Two cases stay silent, per the rule below: with no member reporting `primary`
there is no reference timeline and none is claimed, and output from an older
`pg_autoctl` without the `TLI:` field parses as it always did.

### Icon vocabulary

The section's core idea: it never claims to know something it did not
measure.

| Icon | Meaning |
|------|---------|
| ✅ | measured healthy |
| ⚠️ | warning (e.g. a lagging PostgreSQL replica, a diverging DNS entry) |
| 💀 | measured broken |
| ⏰ | a scheduled job, resting between successful runs |
| `⬜` | not observable / not attempted — see below for where it appears |
| `…` | the check ran out of the shared time budget |
| `✗` | the check itself failed (a command errored, a connection was refused) |
| `n/a here` | not applicable — this node runs no member of the service |

`…` and `✗` mean different things and must not be conflated: a budget
timeout says nothing about the service's health, only that the panel gave up
waiting for it; a failed check (`✗`) is a statement about the service, or
about the tool used to ask it.

Every icon in the table above occupies **two terminal cells**, and that is a
requirement rather than a coincidence: a column mixing a one-cell glyph with a
two-cell one steps left and right down the block. Until 0.10 the not-observable
marker was a middle dot (`·`), one cell against `✅`'s two, and every cluster
member list was ragged because of it. The same character still appears in the
panel as a *separator* — `active · manager · 5 nodes`, and the follow-mode
status line — which is a different use and unrelated to this vocabulary.

The empty square (`⬜`) exists because **MongoDB reports its replica-set
members but not their state**: `db.hello()` tells the panel who belongs to
the set, but only the primary and the member it just ran the command against
have known state — every other member is genuinely unmeasured, and rendering
an unearned ✅ for it would be worse than saying nothing. Every other check in
this section (PostgreSQL's `pg_autoctl` rows, Kafka's quorum voters,
GlusterFS peers/bricks) reports ✅ or 💀 for each member it lists, never `⬜`,
because those commands do report per-member state. A *service* whose own
quorum was never established shows no icon at all next to its name, just a
dim "quorum not reported" note — so "not observable" (the dot) and "never
asked" never look the same. That note appears whenever the panel has no basis
for a quorum verdict:

- the probe errored before parsing anything;
- the command succeeded but its output was not recognisable (an empty
  `pg_autoctl` table, a Kafka status in an unexpected format after an
  upgrade, a GlusterFS answer with neither peers nor volumes) — an
  unrecognised answer is *not* a measurement, and rendering 💀 for one would
  raise a red alarm for a perfectly healthy cluster;
- `gluster peer status` reports zero other peers, i.e. a single-node volume,
  which peer status alone cannot establish a quorum for;
- RustFS's `RUSTFS_VOLUMES` could not be read, so the endpoint list is a
  guess — the line then also reads `(endpoint list unknown)` next to the
  live count, because "1/1 live" against a guessed endpoint says nothing
  about a five-node cluster.

All three blocks can additionally read `not checked (…)` instead of a false
clean result: the CLUSTERS block shows `not checked (no Docker client)` when
there is no Docker socket to probe from (or every cluster kind is disabled in
config), the PEERS block shows `not checked (no peer list available)` when
the check produced no result *and* there were no peer names to check in
the first place — i.e. neither the `wg`/TCP probe answered nor a Swarm node
list gave it anything to ask about — and the DNS block shows `not checked
(DNS check did not run)` when the section has no collected data at all (the
DNS check itself always runs, so within a real run this line cannot appear;
it guards the case where the whole section is missing). All exist for
the same reason — an empty list from a check that never ran would otherwise
look identical to "checked, found nothing," which is a false clean bill of
health. Those lines are also prefixed with the same empty square (`⬜`) as the
MongoDB member case above: a block that never ran carries exactly that claim
— not observed, nothing more said.

Where a probe deliberately narrows what it looks at, it says so in the same
place: `(+N more volumes)` when a GlusterFS host serves more than the one
volume reported, and `(+N more containers)` when a node runs more than one
container of the same kind (a `pg16` → `pg18` migration, say) and only the
first was probed.

### A service with no running container

`n/a here` (this node legitimately runs no member of the service — no MongoDB
on a node that only hosts PostgreSQL, say) is not the same as a service that
*should* be running here but is not. When the Swarm service spec pins a
service to this node's hostname (or the service runs in global mode) with at
least one desired replica, but no matching container is currently running —
most likely a crash loop — the panel reports `✗ <service>: no running
container` (the service title followed by the error) instead of `n/a here`.

This check is deliberately narrow: an **unpinned** replicated service (no
`node.hostname` placement constraint) is never flagged this way, even while
it is crash-looping, because it could legitimately be scheduled on a
different node. The DOCKER INFOS block, which reads the live Swarm service
list rather than the placement spec, still shows such a service correctly —
this blind spot is specific to the health section's per-node view.

### What the section cannot see

- **MongoDB quorum** means only "a primary exists". `db.hello()` reports
  membership and who the primary is, but not per-member replication state;
  that would need `rs.status()`, which needs credentials this panel
  deliberately does not hold.
- **RustFS heal and erasure-coding state** are invisible: `/health` is a
  liveness check only, and the admin API that reports heal/EC state answers
  403 without credentials the panel does not have.
- **An unpinned crash-looping service is not flagged** (see above) — DOCKER
  INFOS still shows it, just not the health section.
- **GlusterFS needs passwordless `sudo -n gluster …`** on the host; without it
  (no sudo rule, no `gluster` binary), the block reports `n/a here` rather
  than an error — the tool being unreachable says nothing about the volume's
  actual health.

## Traefik wiring

`status-traefik` (and the `traefik` section inside `status-full`) reads
Traefik's entrypoint → router → middleware → service wiring straight from the
Docker API: the entrypoints from the Traefik service's own command arguments,
the routers/middlewares/services from every Swarm service's `traefik.http.*`
labels **and** from every plain or Compose container's — a Traefik router can
be declared on a service, a `docker compose` container, or a bare
`docker run` container, and the panel reads all three the same way — and the
file-provider routers from the mounted Docker configs (`traefik_dynamic*`) —
the `api` and `ping-router` entries live only there. A container that is
itself a Swarm task is skipped: its labels are its service's own, already
read once from the services list, and reading them again from the container
list would double-count every Swarm-hosted router. No credentials beyond the
Docker socket are needed, and no change to the Traefik deployment.

**Only the config generations the Traefik service actually mounts are read.**
Swarm keeps every generation of a config — `traefik_dynamic_yml_v1` through
`_v4` may all still exist — but only the ones named in the service spec are
the ones Traefik has loaded. Selecting by name instead showed `ping-router`
four times on every entrypoint and turned entrypoints that were removed two
revisions ago into orphaned-router findings. Where the Traefik service cannot
be found at all, no generation is guessed: the file-provider routers are
reported as missing, with the reason named, rather than shown as if they were
current.

### Labels from containers, not only Swarm services

A `traefik.http.*` label works the same wherever it is declared: on a Swarm
service, on a `docker compose` container, or on a bare `docker run`
container. The panel reads all three.

A router coming from a container carries two different names, and the panel
keeps them apart on purpose. Its **origin** is the container's own name,
exactly as `docker ps` shows it, so a human can trace the router back to
what declared it — but the tree only ever shows it for a router that lands
in **ORPHANED ROUTERS**, in brackets after the finding, `[course-statistics-db]`
for example. A correctly-wired router — the common case — appears in the
normal per-entrypoint tree with no origin shown at all; `_router_lines`,
which renders that tree, never reads `router.origin`. Brackets are therefore
a property of the orphan listing, not a mark of where a router came from in
general, and their absence next to a healthy router does not mean the origin
was not read. Its **target**, the name matched against Docker to produce the
`✅ 1/1`-style verdict shown on every router regardless of where it landed,
is a different string for a Compose container: Compose sets
`com.docker.compose.service` to the *service* name (`db`), not the
container's own name (`course-statistics-db`), and `db` is also what
`collectors/docker.py` calls that container everywhere else in the panel.
Matching a router's target against the container's own name instead would
render a false `✗ no such service` for a target that is running and
correctly wired — origin and target answer "who declared this?" and "what
does it point at?", and only one of those two questions is "the container's
own name". `compose_identity()` in `collectors/_labels.py` computes the
target name once, and both the Docker collector and the Traefik collector
call it, so the two cannot silently drift apart. A container with no Compose
label — a bare `docker run` — has no second name: origin and target are the
same string.

**A known ambiguity, not fixed here.** A container's target name is unique
only *within* the Compose project (or stack) that started it — the panel has
no notion of a project-qualified identity. Two unrelated Compose projects
that each define a service called `db` both produce a target named `db`, and
a router pointing at `db` matches both: the verdict sums their replica
counts into one number, observed live as `✅ 2/2` where a single,
correctly-wired container should have read `✅ 1/1`. An inflated count for a
common service name (`db`, `web`, `api`, …) is this ambiguity showing up, not
the panel double-counting a healthy service or Traefik being misconfigured.
It predates this work and lives in `collectors/docker.py`'s choice to key a
container's identity by service name alone, not by (project, service) —
fixing it would mean deciding how a router should express *which* project's
`db` it means, which is a design question for another day.

**A second, different collision: one Traefik service name, two declarations.**
The ambiguity above is about two *containers* sharing a target name, and it
shows up as an inflated replica count. This one is about two *label sources*
declaring the same Traefik service name, and it shows up as a verdict for the
wrong thing entirely. `collect_traefik` reads Swarm services first and
containers second, and the container pass ends with `info.services.update()`,
so where both declare `traefik.http.services.web.*` the container's
declaration replaces the Swarm one:

```
Swarm service portal_web        declares routers.web + services.web
Compose container dev-web-1     declares routers.web + services.web
→ services["web"].docker_service becomes "web" (was "portal_web")
```

The router declared by `portal_web` now has its verdict computed from the
Compose container. If `portal_web` is scaled 0/3 and dead while the container
runs, that router renders a green `✅ 1/1` — a healthy verdict for a dead
service, measured on something unrelated to it. **Where two label sources
declare the same Traefik service name, the panel shows one verdict and does
not tell you there was a conflict.** The underlying situation is a genuine
name conflict between two label sources, and what the panel *should* say
about it — report both targets, flag the collision, prefer the Swarm
declaration — is a design question rather than a patch, so it is documented
here instead of fixed. Until it is answered, a router whose verdict looks
implausible is worth checking for a second declaration of its service name.

**A paused standalone container reads as missing.** A paused container is
still listed by `containers.list()`, so its labels are read and its router
appears in the tree — but `collectors/docker.py` counts a container with no
Compose project only while it is running or restarting, so it never becomes a
`ServiceStatus` for the verdict to match against. The router's target then
renders a red `✗ no such service` for a container the panel itself just read
labels off. Un-pausing it restores both halves; a *Compose* container is
unaffected, since it stays in its group and shows the shortfall instead.

The panel renders one branch per entrypoint (sorted by port), each listing
its routers (dimmed when they come from the file provider), their
middlewares, and the Docker service or container each one points at —
cross-checked against the same Swarm service **and container** data the
DOCKER INFOS section uses, through the same `service_verdict`, so one target
never gets two verdicts. A target that matches neither a service nor a
container, on a daemon that actually answered, still reads `✗ no such
service`, in red. That
data is collected whenever the `traefik` section runs, including for a bare
`status-traefik`; the DOCKER INFOS block itself is *not* rendered as a side
effect. When the Docker daemon gives no answer at all — no client, or an
unreachable or non-Swarm daemon — the service line shows a neutral `⬜`
rather than claiming the service is missing, since nothing was measured. A
router naming no
entrypoint is attached to every entrypoint by Traefik itself, so it appears
under all of them; an entrypoint with no attached router reads `— no
router`, which is a finding (a published port nothing serves), not an
absence.

### Layout and order

The entrypoint branches are packed into as many columns as the terminal
allows, balanced by height rather than filled row by row: `rich.Columns`,
which CLUSTER HEALTH still uses, fills a grid row by row, so a row is as tall
as its tallest cell and a three-line branch beside a twenty-line one leaves
seventeen blank lines behind it. The packer used here fills column by column
instead, putting the tallest branches each in a column of their own and
stacking the short ones together, so the whole layout is only as tall as its
fullest column. There is no fixed "three columns at 190, one at 60" to name,
because the column count now falls out of which branches land in which
column on the actual terminal width, not out of a uniform column width the
way `Columns` computed it. The orphaned-router block stays full width below
the columns: its lines are the longest in the section, and it is what you
read first. Each entrypoint's head line carries the worst verdict among its
routers, so a wall of branches still says at a glance which one to open.

Entrypoints appear **in the order the Traefik service's arguments declare
them**, not by port. The Ansible role lists the four every cluster has —
`dashboard`, `ping`, `default`, `https` — before appending this cluster's
per-vhost ones, and that grouping is more useful than the numeric order, which
would put `https` (443) first and `dashboard` (8082) last and scatter the four.

### Folding endpoints that claim nothing

A router pointing at one of Traefik's own `@internal` endpoints — the `ping`
router that answers `/ping` is the everyday example — has nothing to report
on its service line: nothing about `@internal` was ever measured, so there is
no verdict to show, only the target's name. Rather than spend a whole row on
that name, it is folded onto the router's own line instead:

```
  └─ ping-router        Path(`/_traefik_ping_`)  → ping@internal
```

Nothing is hidden and no verdict is dropped, because there was no verdict to
drop in the first place; a router carrying a middleware, or pointing at a
real service, keeps its service line on its own row exactly as before. The
fold does cost the branch some width, though, and a wider branch can push a
column over the terminal's width and cost the whole section a column back —
paying several lines on screen to save one. So the panel builds both the
folded and the unfolded form of every branch, packs each independently, and
draws whichever one actually packs to fewer lines. On a shape of six
entrypoints that share nothing but one `ping` router, folding unconditionally
would cost a column at a terminal width of 120 (14 lines instead of 11);
packing both and choosing avoids that.

### Entrypoints that are supposed to look empty

`--ping.entryPoint=ping` makes Traefik answer `/ping` on that entrypoint
itself, with no router involved. It is read from the same arguments, and that
entrypoint reads `— Traefik's own health check` instead of `— no router`, so
the one port that is *meant* to carry nothing does not read as a finding.
Every other empty entrypoint still does — an internal `https :443` with
nothing routed to it genuinely has nothing behind it.

### Services the file provider declares

A router can point at a service defined in the dynamic YAML rather than in
Swarm — `account-api` → `account-api-placeholder` →
`http://user-account.internal` is the live example. Those services are read
along with the routers, and the upstream URL is shown in place of a Docker
verdict, with a `⬜`: nothing about that target was measured. Matching them
against Swarm service names instead reported `✗ no such service` for something
that was never supposed to be a Swarm service.

### Clickable entrypoints and routers

When `[traefik.links]` names a base URL for an entrypoint, that entrypoint's
head becomes a clickable link, and so does the name of every router on it
whose rule names exactly one path. Nothing else in the block is ever
clickable — the service line names a container and a port inside the
cluster, an address no browser reaches, linked or not.

```toml
[traefik.links]
login_example_de = "https://login.example.de"
portal_dept_uni_example_de = "https://portal.dept.uni-example.de"
```

One entry per **entrypoint name**, not per router: every router hanging off
an entrypoint shares that entrypoint's base, only the path differs. The key
is the entrypoint's own name, exactly as Traefik reports it; the value must
start with `http://` or `https://` — anything else (a bare hostname, a
non-string, a typo'd scheme) is silently dropped, and that entrypoint is
left with no links at all rather than a broken one.

**The base cannot be derived — it has to be configured.** Traefik's routers
match on path alone: the reference cluster has no `Host()` rule at all, so
no hostname appears anywhere in the routing configuration this panel reads.
And the entrypoint's own name is not a hostname with underscores standing in
for dots, even though it can look like one: in a name such as
`portal_dept_uni_example_de`, one underscore is a dot and the next is a
hyphen, and nothing in the name itself says which is which —
`portal.dept.uni-example.de` is only recoverable by checking DNS, not by
substitution. A link that goes somewhere plausible but wrong is worse than
no link, because the reader cannot tell which until they click it. That is
why an entrypoint absent from `[traefik.links]` gets no links, rather than a
guessed one.

A router whose rule names more than one path — an alternation such as
``PathPrefix(`/a`) || PathPrefix(`/b`)`` — or negates a path —
``!Path(`/health`)``, which names the one path the router does *not* serve —
keeps no link of its own; there is no single sub-path left to join onto the
base. Its entrypoint's head stays clickable regardless: the host is known
even where the sub-path is not.

`--no-color` suppresses these hyperlinks along with every other colour, the
escape hatch for a terminal that renders OSC-8 hyperlinks badly.

### What "as configured" means, and its limit

Everything above is read from *configuration* — labels and YAML — never from
Traefik's own runtime state. **A router with a typo'd rule, or naming an
entrypoint that does not exist, still appears here exactly as declared**,
because nothing in this reading path asks Traefik whether it actually
accepted it. The real case on this cluster: the `image_api` router's label
names the entrypoint `websecure` (Traefik's own common naming convention for
a TLS entrypoint), but that cluster's nine entrypoints are named `dashboard`,
`ping`, `default`, `https`, `login_example_net`, `portalmgmt`,
`www_example_net`, `db-ui` and `kafbat` — no `websecure` among them, so the
router is wired to a port that plainly doesn't exist. Since a
tree keyed by entrypoint has no branch to put such a router under, it would
otherwise vanish from the panel silently. Instead it gets its own
**ORPHANED ROUTERS** block, listing the router, the entrypoint name(s) it
refers to that do not exist, its rule, and the service it would have pointed
at.

When the Docker configs backing the file provider could not be listed at
all, a `file provider unreadable: …` warning appears above the tree — a
partial-read failure, distinct from the routers simply being empty. Because
`api` and `ping-router` live only in the file provider, this warning is the
signal that their absence below is a read failure, not a finding.

### What is still not read when Traefik itself runs as a container

Reading labels from containers (above) means a router can now be *declared*
anywhere. Two other things this collector reads, it still reads only from
the Traefik **Swarm service**, and a host where Traefik runs as a container
rather than a Swarm service loses both — though not in the same way, as the
two paragraphs below show: one announces the gap loudly, the other can stay
completely silent about it.

**Entrypoints.** `--entryPoints.*` is read from the Traefik service's own
`TaskTemplate.ContainerSpec.Args`, a piece of the Docker API that only a
Swarm *service* carries. A container-hosted Traefik has no such spec, so
`info.entrypoints` comes back empty — and the section says so plainly: a
yellow `⚠️ no entrypoints found — the tree cannot be drawn, the routers
below could not be placed` banner opens the section whenever this happens,
so the *cause* is never quiet. What the banner does not spell out is its
second-order effect: with no entrypoint list, every router — however it was
declared — falls into the **ORPHANED ROUTERS** block instead of the tree,
where it reads, in yellow, ``⚠️ … entrypoint `https` — no entrypoint could
be read``, never the red ``✗ … entrypoint `https` does not exist`` a router
with a genuinely missing entrypoint gets: with no entrypoint list to check
against, the code cannot tell "not on this one" from "nothing was read" and
declines to accuse. So while the section is loud about not having read the
entrypoints, it says nothing further about whether any router points at one
that does not exist — that specific check simply does not run, for any
router, on such a host. **A container-hosted Traefik with no red orphan
findings has not been shown clean; the check that would have found a
problem never ran.**

**The file provider.** This collector reaches `traefik.yml` only through a
Docker Config mounted into a Swarm service; it never reads a container's
filesystem or its bind mounts. A container-hosted Traefik's `traefik.yml`,
mounted the ordinary Compose way, is invisible to the Docker API entirely —
there is no path to it. Concretely: if no `traefik_dynamic*` Docker config
exists at all, which is the normal state for a deployment that never created
one, the section prints **no warning whatsoever**; `api@internal` and the
ping router are simply absent from the tree, exactly as they would be for a
Swarm deployment that genuinely declares no file provider. The two cases are
not distinguishable from the panel's output. Only when a *stale*
`traefik_dynamic*` config survives from an earlier, Swarm-based deployment
of the same Traefik does the `file provider unreadable: traefik service not
found, so which config generations are mounted cannot be determined` warning
above fire — because in that case the collector can see a config, just not
one it can still tie to a live service.

Two further, narrower gaps, one on each side of the container-label read: a
`containers.list()` call that fails is recorded on `TraefikInfo.container_error`,
and a `services.list()` call that fails is recorded on the symmetric
`TraefikInfo.service_error` — both distinct from `error`, which is reserved
for the case where *neither* listing could be read and there is genuinely
nothing to show. Either one failing alone degrades rather than aborts: the
labels the other listing did read still stand, and the panel renders a dim
notice above the tree naming which side failed — `container labels
unreadable: …` or `Swarm service labels unreadable: …` — so a Docker
permission or connectivity problem never degrades silently to "labels from
the other source only."

The service-listing notice has one deliberate exception: it stays silent
when Swarm is not active on the node running the panel. A `services.list()`
call failing with "this node is not a swarm manager" is not a Docker problem
at all on a Compose-only host — it is the expected, permanent answer on
every single run there, and a warning that fires every time trains the
reader to stop reading it. The section only shows the notice when Swarm
reports itself active and the services listing still failed — a Swarm
manager or worker that genuinely could not be queried, which is worth a
line precisely because it is not supposed to happen.

### The optional live cross-check

The `[traefik]` config section (`url`, `cert`, `key`, `ca` — see
[Configuration reference](#configuration-reference)) is meant to close the
"as configured" gap: given Traefik's `/api/rawdata` endpoint and a client
certificate, the collector asks Traefik what it actually accepted and
records the answer per router.

**It is dormant on every app server today, and should stay unset.** Reaching
that endpoint needs a client certificate signed by the **webfe CA**, and the
Ansible role that provisions app servers currently issues only
**app-server TinyCA** certificates — for Traefik→service mTLS, a different
trust chain than the one the dashboard's own listener expects. Configuring
`traefik.url` without a certificate the dashboard accepts does not error: an
unreachable or rejected connection is treated the same as "not configured"
(see `fetch_accepted` in `collectors/traefik.py`) and the check is silently
skipped, so no test will surface the mistake. Leave the section unset until
the app servers have a certificate from the right CA.

When the cross-check does run, the tree shows its answer: a router Traefik
reported as *not* enabled is marked `💀 rejected by Traefik` on its own line
— the configuration is there, Traefik declined it. The accepted case adds
nothing: the tree already reads as configured-and-accepted, and a second
checkmark on every line would only be noise.

Nothing is marked unless Traefik was actually asked and actually answered
about that router. With `[traefik]` unset, unreachable, or answering in a
shape the parser cannot read (a router whose entry carries no `status` at
all), no marker appears — "we did not ask" and "Traefik said no" must never
look alike.

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

# Pick which panel(s) to show — e.g. Docker + cluster health on a Swarm node:
sudo install-panel --scope global --panel docker --panel health
# …or any other combination as separate commands:
install-panel --scope user --panel server --panel docker

# Preview without writing, then remove again:
install-panel --scope user --dry-run
install-panel --scope user --uninstall
```

A Swarm node is the natural case for `--panel docker --panel health`
together: the Docker Swarm block and the clustered-services health block
answer different questions (what's scheduled vs. what's actually healthy)
and both only make sense where the Docker socket is available. Installed
alone, `--panel docker` collects no health at all, so the **Working** cell of
every clustered service falls back to Docker's own measurement — `⬜` only for
a row Docker itself measured clean (fully staffed or scaled to zero), still
`💀`/`⚠️` when Docker measured it dead or degraded — honest, but the column
only earns its cluster icons with `--panel health` beside it.

Options:

| Option | Values | Default | Meaning |
|--------|--------|---------|---------|
| `--scope` | `global` \| `user` | `user` | `/etc/profile.d` (needs root) vs. your own login profile. |
| `--panel` | `full` \| `server` \| `docker` \| `health` \| `traefik` | `full` | Which command to run; repeatable. |
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
`status.description`:

```yaml
services:
  postgres:
    image: postgres:18
    deploy:
      labels:
        status.description: "PostgreSQL database, version 18"
```

Change the read label key via `docker.description_label` in the config.

Before this key was renamed it was `lmu.service.description`. That key is still
read whenever the configured one is absent from a service, so an existing
deployment keeps its descriptions without any change. Where a service carries
both, the configured key wins — otherwise a service could be pinned to an older
text it had been migrated away from. The rule is presence, not content: a
service that sets the configured key to an empty string is saying "no
description here", and the legacy key is not consulted.

## The image column

Right of **Description**, each row names the image its replicas run — the
repository and the tag, as `kafka:4.0.0`. Nothing has to be labelled for this:
the reference comes from the service's task template
(`Spec.TaskTemplate.ContainerSpec.Image`) or, for a plain or Compose container,
from the `Config.Image` it was started with.

Three things are dropped on the way to the cell, and each for a reason:

- **The digest.** Swarm rewrites every reference to `tag@sha256:…` when the
  service is created. Those 71 characters are the same for every replica of the
  same tag and separate nothing a reader is looking for.
- **The registry and the namespace.** On a cluster that builds its own images
  they repeat on every single row — `registry.example.org:5005/group/project/`
  ahead of each name — and what differs is exactly what is left. The colon is
  searched for in the **last path segment** only, so a registry port is not
  read as a tag.
- **Nothing else.** A reference without a tag is shown as the bare repository
  rather than as `:latest`. Docker would resolve it that way, but the panel
  reports what the service says, and the service did not say it.

Where one row stands for several services — the per-node replicas of a stack —
they can disagree, and a stalled rolling update is exactly when they do. The
cell then names the image the **most running replicas** carry and marks it:
`kafka:⚠️ 4.0.0` when only the tag differs, `⚠️ traefik:v3.3` when the
repositories do. Ranked by running replicas rather than by service count,
because a tag nothing runs any more is what the marker points away from — and
never by "newest", because the panel cannot order tags. `v3.10` sorts below
`v3.3` as a string, and claiming an order that does not exist is worse than
naming a majority that does.

A Compose group is read the same way at collection time: where a changed tag
left the old container behind, stopped but still listed, the row reports the
image of the container that is actually serving — the one its own replica count
is about.

The column costs width, which on a narrow terminal is the width the description
needs. Turn it off with `show_image = false` in the `[docker]` section.

## Engine versions and manager reachability

The summary line carries the Docker engine version, and the node list carries
it only where a node disagrees:

```
Swarm  active · manager · 5 nodes · 60 services · 18 stacks · Docker 28.5.2
Nodes  swarm01-mgr-01 ✅  swarm01-mgr-02 ✅  swarm01-mgr-03 ✅ (leader)  swarm01-wrk-01 ✅
```

```
Swarm  active · manager · 5 nodes · 60 services · 18 stacks · Docker 28.5.2 ⚠️ 2 versions
Nodes  swarm01-mgr-01 ✅  swarm01-mgr-02 ✅  swarm01-wrk-01 ⚠️ 27.3.1  swarm01-mgr-03 ✅ (leader)
```

Uniform is the normal case, so uniform is one number. Five identical strings
beside five nodes would repeat what the summary already says and bury the one
entry worth finding. **Any** divergence warns, patch level included: nodes that
are configuration-managed are meant to be identical, and a differing patch is
not "harmless", it is "this node missed an update".

Both facts come from the node listing the collector already makes, so neither
costs an API call.

On a **worker** the version renders as `Docker 28.5.2 (local)`. A worker may
not enumerate the swarm, so this is its own version and nothing more — and an
unqualified version in the swarm header would read as a statement about the
swarm.

Reachability appears only when it is missing:

```
Nodes  swarm01-mgr-01 ✅  swarm01-mgr-02 ✅  swarm01-mgr-03 ⚠️ unreachable (leader)
       ⚠️ 2/3 managers reachable — one more failure locks the swarm
```

This is a different question from the node state beside it. The orchestrator
can call a node `ready` while the other managers cannot reach it, and the panel
used to render that as a green tick — a quorum risk wearing a healthy face. The
warning line counts **managers**, not nodes, and turns red with `quorum lost`
once the majority is already gone. A node that reports nothing about
reachability is not counted against: silence is not failure.

## Docker's own disk footprint

The filesystem bars report a full disk; they never report why. On a node that
runs containers the cause is usually Docker itself, and one line under the
swarm summary says so:

```
Disk   swarm01-mgr-01 · 43.7 GB used · ↺ 28.3 GB reclaimable · images 13.5 GB · cache 12.4 GB · volumes 2.4 GB · 178/185 unused
```

The node name is not decoration. `/system/df` knows only the daemon it is
asked, so on a manager these figures describe that manager and nothing else;
without the name the number reads as a statement about the cluster.

`reclaimable` leads because it is the figure that leads to an action. "Docker
occupies 43.7 GB" invites none; "28.3 GB of it can be had back" does. The
unused-volume count is the part that matters most on a node running databases:
a volume nothing references still holds its bytes, and no other part of the
panel would ever mention it.

**Colour follows the disk, not the heap.** The line turns yellow only when the
filesystem holding `DockerRootDir` is itself above 80% full — 28 GB of
recoverable images matter on a full volume and not at all on an empty one. The
mount is found by longest match rather than by assuming `/`, because a node
that gives Docker its own volume is exactly where the two disagree. Without
filesystem data (running `status-docker` alone) the figures render uncoloured
rather than claiming a pressure nobody measured.

The reading is taken on a **separate client with its own timeout**
(`docker.df_timeout`). The Docker collector degrades to "not reachable" on any
exception, so a slow reading taken on the shared client would erase the entire
DOCKER INFOS section — swarm, stacks, nodes and containers — to report a disk
figure. A failure renders `Disk swarm01-mgr-01 · n/a (timeout)`, never a
vanished line: a line that disappears reads as "nothing to report".

## The TROUBLE block

A service row saying `0/1` has at least three possible causes — it keeps
crashing, it was killed for memory, or the orchestrator never placed it — and
the row itself shows the same number for all of them. The TROUBLE block is
where that number is explained:

```
TROUBLE  (last 12 h)
    SERVICE              NODE             FAILS   UP  CAUSE
💀  mystack_builder      —                    —    —  no suitable node (insufficient memory on 3 nodes)
💀  mystack_model        swarm01-wrk-01    ↻ 3×    —  OOMKilled · exit 137
💀  mystack_search_2     swarm01-wrk-02   ↻ ≥5×    —  exit 1 · "Could not load conf for core"
⚠️  mystack_thumbnailer  swarm01-wrk-01    ↻ 2×    —  exit 137
⚠️  registry             swarm01-mgr-01    ↻ 2×  47s  —
```

**It does not exist when there is nothing to report.** That is the normal
state, and a heading present at every login is a heading nobody reads on the
day it fills up.

### What qualifies

A restart count above zero **and** a start inside the last twelve hours. The
second condition carries the design: `RestartCount` is cumulative over a
container's whole life and is not reset by a manual start, so on its own it
would pin a stumble from three months ago to the panel for ever. The counter
says "it has fallen"; the window says "recently". Twelve hours spans a night,
so what broke at 03:00 is still there at the login that follows.

Only failures count — Swarm tasks in `failed`, `rejected` or `orphaned`. A
rolling update ends its old tasks cleanly, and counting those would report
every ordinary image bump as a crash.

**Jobs never appear.** A `swarm-cronjob` service, or one in a Swarm job mode,
is meant to start, finish and vanish; a quarter-hourly job would otherwise
report dozens of "failures" for doing exactly its work. Their own row already
carries the outcome beside the schedule that makes it readable.

### Three absences, three meanings

- **`—` under FAILS** — no counter applies. The service never started, so it
  never fell, and a `0` would measure something that did not happen.
- **`—` under CAUSE** — Docker overwrote it. A container that failed and came
  back reports `ExitCode 0` and `OOMKilled false`; the reason is simply gone.
  Swarm answers better, because each attempt survives as its own task with its
  own exit code — so a Swarm row often carries a cause where a local container
  row cannot, and that difference is information rather than inconsistency.
- **`↻ ≥5×`** — Swarm keeps only `TaskHistoryRetentionLimit` historic tasks per
  slot (five by default), so a service that fell twelve times looks like five.
  The limit is read once per collection, and a count that reaches it is marked
  as the floor it is. Understating a twelve-fold crash as fivefold would soften
  precisely the worst case.

### What it costs

Nothing in steady state. Services meeting their replica count and up longer
than the window are dismissed without the history call, so one sick service
among ten healthy ones produces exactly one extra API call rather than eleven.
In an incident the few rows that qualify each pay one call — and in exchange
the panel says what an SSH session would have said.

The block is capped at ten rows, and what is dropped is always named
(`… and 7 more`). A node reboot brings every service on it in at once, and a
silent cap would claim ten services are troubled where twenty are.

## Grouping services into one row

Per-node replicas and ordinal instances collapse into a single row, as
described in the section overview. Two things refine that.

**`status.group` states the answer** where the name cannot. Services of one
stack sharing a value render as one row, whatever their names — and it fixes a
fault the heuristic knowingly accepts: two *unrelated* services differing only
in a trailing `_<digits>`, say `infra_php_7` and `infra_php_8`, collapse today
and the second one's description disappears with them. The label is read for
presence, not truthiness: a service setting it to `""` is saying "group me with
no one", and falling through to the heuristic there would group it by name
after all.

The heuristic stays exactly as it is where no label is set — including its
underscore-only ordinal rule. `-<digits>` is deliberately *not* stripped,
because a stack named `PostgreSQL-18` would be mutilated into
`PostgreSQL-18_PostgreSQL`.

**The pin marks what cannot move.**

```
  backend   ✅ 3/3      ✅  ✅  ✅
  connector ✅ 3/3 📌   ✅  ✅  ✅
  search    ✅ 2/2 📌       ✅  ✅
```

`3/3` on a replicated service means "the orchestrator places three, and it will
move them". The same figure over instances each nailed to a node by
`node.hostname` means "three that cannot go anywhere". Rendered identically the
second borrows a resilience it does not have — and that is exactly the property
that decides what happens when a node dies.

It marks only rows that actually collapsed something: a lone `1/1` claims no
mobility, so there is none to correct. And it shows in health as much as in
failure, because a symbol introduced once everything is already red is a symbol
nobody has had the chance to learn.

## Memory per service

Right of **Working**, each row reports the memory its tasks hold **on this
node**:

```
Service    Working RAM (this node)       mgr-01 wrk-01 wrk-02
mystack
  cards    ✅ 1/1  890.0 MB ⚑ 512.0 MB     ✅
  frontend ✅ 3/3  412.0 MB / 1.0 GB       ✅     ✅     ✅
  model    ✅ 1/1  6.0 GB no limit         ✅
  search   ✅ 2/2  elsewhere                      ✅     ✅
  stopped  💀 0/1  —                       💀
```

The header says `this node` because that is the truth of it:
`/containers/{id}/stats` reaches only the local daemon, so on a manager
carrying sixty services most rows have none of it here.

### Three references, three statements

| Renders | Means |
|---------|-------|
| `412.0 MB / 1.0 GB` | against the cgroup limit — how close to being killed |
| `890.0 MB ⚑ 512.0 MB` | against the reservation — past what the cluster planned for |
| `6.0 GB no limit` | nothing bounds this service at all |

They must not look alike: 33% of a limit and 33% of a reservation are
different statements. Exceeding a limit gets the service killed; exceeding a
reservation kills nothing and quietly makes the cluster's placement arithmetic
wrong. `no limit` is a finding in its own right — an unbounded service takes
the node with it when it leaks.

The limit comes from the container's `HostConfig.Memory`, **not** from
`memory_stats.limit`, which is the host's total RAM when no limit was set. That
is a plausible-looking number, and using it would make every service on the
node look comfortable at a fraction of a percent.

Where a row holds several local tasks, usage **and** reference are summed over
the same tasks. Comparing a summed usage against one instance's limit would
manufacture an alarm out of arithmetic: two 100 MB tasks against a 256 MB limit
would read as 78% full when each sits at 39%.

`elsewhere` means the service runs, just not here. A service running *nowhere*
gets `—` instead: calling it "elsewhere" would send a reader hunting on another
node for something that is simply down, and the Working cell already says which
it is.

### The figure is not `memory_stats.usage`

That value includes the page cache. The number `docker stats` prints — and the
one a reader expects — subtracts `inactive_file` (cgroup v2) or
`total_inactive_file` (v1) first. The difference is not cosmetic: measured
against a live daemon, one container reported **79.0 MB raw against 32.4 MB
real**, an overstatement of 144%.

### Why there is no CPU column

The same call that makes this affordable makes CPU impossible. With
`one_shot=true` the reading returns in about 2 ms per container; without it the
daemon collects two samples first and blocks **about a second per container**
(measured: 1009.7 ms for one), which on a node with twenty containers would
cost a login twenty seconds. The price of `one_shot` is that `precpu_stats`
comes back zeroed, and a CPU share cannot be derived from a single sample.

That is no loss. Whether the machine has enough CPU is answered by the load
average and the per-core bars in SYSTEM STATUS, one section up, and by the TOP
CPU ranking beside them. Memory is the question nothing else on the panel
answers at the level of a service.

## OS logos

Logos are **pre-rendered** from real PNGs into half-block ANSI (`▀` with
fore/background colours) and bundled under
`src/terminal_status_panel/render/logos/*.ans`. They are plain ANSI, so they
render in MOTD and over SSH without any image protocol or runtime dependency.
The correct logo is chosen automatically — by platform first, then by
detected distribution, then Tux — as described under
[Platform behaviour](#platform-behaviour).

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

Releases are automated by `.github/workflows/release.yml`, which publishes via
**Trusted Publishing (OIDC)** — no API token is stored in the repository. It
builds once, with [build provenance attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations),
and publishes that same artefact to one of two indexes:

| Trigger | Goes to | Environment |
|---|---|---|
| every push to `main` | [test.pypi.org](https://test.pypi.org) | `release-test-pypi` |
| publishing a GitHub Release | [pypi.org](https://pypi.org) | `pypi` |

Test PyPI on every commit is deliberate: it exercises the publishing path
continuously, so release day is not the first time it runs. Those uploads use
`skip-existing`, because the version only changes at a release: between two
releases every push offers a file the index already has, and it would
otherwise refuse each one with `400 File already exists`.

The workflow calls the repository's CI rather than restating its checks — a
release gate that duplicates them drifts from them, and it drifts silently. A
tag is additionally checked against the version in `pyproject.toml`, so a
mistyped tag or a forgotten bump fails before upload rather than on PyPI,
where the wrong number cannot be taken back.

Release steps:

```bash
# 1. bump the version in pyproject.toml (must match the tag)
# 2. commit, tag and push
git commit -am "release: v0.6.0"
git tag v0.6.0
git push && git push --tags
# 3. publish a GitHub Release for that tag -- this is what uploads to PyPI
```

One-time setup, per index:

1. On the index (<https://pypi.org> or <https://test.pypi.org>) → *Your
   projects* → *Publishing*, add a **pending trusted publisher**:
   - **Project name:** `terminal-status-panel`
   - **Owner:** `edutap-collective`
   - **Repository name:** `terminal_status_panel`
   - **Workflow name:** `release.yml` — exactly as the file is named. A
     publisher whose workflow name does not match matches nothing at all, and
     the upload is rejected as `invalid-publisher`. Both indexes must name the
     same file, since there is only one: pypi.org has named `release.yml`
     since v0.4.0 and is the one that must not be disturbed, so Test PyPI
     follows it. This is a deliberate departure from the sibling packages,
     which call the file `release.yaml`.
   - **Environment name:** `pypi` for PyPI, `release-test-pypi` for Test PyPI
2. In the GitHub repository → *Settings → Environments*, create the matching
   environment. Required reviewers on `pypi` gate every upload behind an
   approval; `release-test-pypi` is better left ungated, since it fires on
   every push to `main`.

   `pypi` already exists and already carries a working publisher — it is what
   released v0.5.0. The environment names here follow that rather than the
   other way round: renaming it would mean deleting and re-registering the
   publisher on pypi.org, which is real risk on a live index in exchange for
   nothing but a tidier name.

## License

Licensed under the [EUPL-1.2](LICENSE).
