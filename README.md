# terminal-status-panel

A small Python package that renders a colorful server status panel on login —
best run from a `profile.d` snippet so it uses the full terminal width (see
[Running it at login](#running-it-at-login)). The full-width dashboard is laid
out in four tiers:

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
  sub-row per service (stack prefix and node name stripped). A trailing
  `_<digits>` is stripped the same way, so ordinal instances collapse too — a
  service deployed as `heidi_connector_1`, `_2`, `_3` (one pinned instance per
  node, each with its own secrets) renders as a single `heidi_connector` row.
  That stripping is unconditional, and it has an accepted cost: if one
  instance is later removed from the deployment — or scaled to zero — its
  row just folds into the remaining two, and the panel reads `✅ 2/2` with
  nothing to say a third instance was ever expected. The gap is invisible
  only across a *deployment* change, never a *failure* — a failing instance
  still has a desired replica and renders `💀` or `⚠️` in both the Working
  column and its node cell, so a lost instance is silent but a broken one is
  not. Each row also
  carries a **Working** cell right after the service name: an icon plus the
  row's running/desired task count, e.g. `✅ 3/3`, `⚠️ 2/5`, `💀 0/3`, `· 0/0`.
  Three rules keep that cell from over-claiming:

  - a service scaled to zero replicas renders `· 0/0`, not `💀` — that is a
    decision, not an outage;
  - for the four clustered services that actually run as Docker services
    (PostgreSQL, MongoDB, Kafka, RustFS — GlusterFS is queried on the host via
    `sudo -n`, not as a container, so it never gets a DOCKER INFOS row) the
    **icon** comes from that service's own CLUSTER HEALTH verdict while the
    **count** stays Docker's running/desired tally, so the two are allowed to
    disagree on purpose — except that a row measured `💀` or `⚠️` by its own
    replicas keeps that icon: no cluster-level `✅` or `·` can talk a row with
    nothing (or not everything) running into looking healthy;
  - under `status-docker`, which runs without the health section, a clustered
    service's cell falls back to `·` plus Docker's own count instead of a
    replica-derived `✅` — "five brokers are running" is not the claim this
    column makes — and the same fallback applies when the probe found no
    member of that service on this node, or the kind is not listed in
    `health.enabled`. Withholding the cluster's claim does not withhold
    Docker's, so such a row still renders `💀 0/3` when nothing runs.

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
  A node cell holding a single task keeps the bare glyph, ✅ or 💀 — the
  overwhelming majority of cells, since most rows place one task per node —
  and from two tasks up it carries the count right after the glyph, with no
  space in between: `✅2` when all of them run, `⚠️1/2` when some do, `💀0/2`
  when none do. (Grouping can put more than one task on the same node — after
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

This installs four panel commands (`status-full`, `status-server`,
`status-docker`, `status-health`) plus an `install-panel` helper to wire it
into a login shell.

## Commands (sections)

The dashboard is split into three independently runnable sections, each with
its own entry point — plus the combined command:

| Command | Sections | Use |
|---------|----------|-----|
| `status-full` | server + docker + health | The full panel (default). |
| `status-server` | server only | System overview, updates, load/mem/fs. |
| `status-docker` | docker only | The Docker Swarm block. Collects no health, so a clustered service's **Working** cell falls back to Docker's own measurement — `·` only when Docker itself has nothing stronger to say (fully staffed or scaled to zero), still `💀`/`⚠️` for a row Docker measured dead or degraded — pair it with `status-health` to get the cluster verdicts. |
| `status-health` | health only | Clustered infrastructure services, WireGuard peers, DNS. |

Each section only collects the data it needs: `status-docker` never touches
the system collectors, `status-server` never opens the Docker socket, and
`status-health` never touches the system collectors either (though it does
open the Docker socket, to `exec` into service containers) — so you can run
just what a given host cares about. The combined command also accepts
`--sections server,docker,health` to pick explicitly.

Any of the four works in the profile.d snippet (see *Running it at login*) —
e.g. call `status-docker` or `status-health` on Docker Swarm nodes and
`status-server` on plain servers.

## Usage

```bash
status-full   [--sections server,docker,health] [--width N] [--no-color] [--config PATH]
status-server  [--width N] [--no-color] [--config PATH]
status-docker  [--width N] [--no-color] [--config PATH]
status-health  [--width N] [--no-color] [--config PATH]
```

The command **always exits 0** so it can never break a login shell. If a
collector fails (no Docker socket, non-Debian host, …) that section degrades to
a placeholder instead of erroring.

### Command-line options

| Option        | Default | Description |
|---------------|---------|-------------|
| `--sections`  | *(all / per command)* | Comma-separated sections to render: `server`, `docker`, `health`. On `status-full` the default is all three; the dedicated commands fix their own section. |
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
| `docker.infrastructure_stacks` | `["postgresql", "postgres", "kafka", "mongodb", "rustfs", "portainer", "traefik", "registry", "minio", "redis", "valkey", "mariadb", "mysql", "elasticsearch", "bugsink"]` | Case-insensitive substrings. A stack (or ungrouped service, e.g. `registry`) whose name matches goes into the **Infrastruktur** column; everything else goes into **Service**. |
| `docker.infra_ui_services` | `["kafbat-ui", "kafka-ui", "kafdrop", "cloudbeaver", "pgadmin", "adminer", "mongo-express", "mongo-gui", "rustfs-console", "rustfs-ui", "s3-browser", "s3browser", "redisinsight", "redis-commander", "portainer", "dozzle", "kibana"]` | Case-insensitive substrings matched against the stack name **and** the service name. Matching services leave their own stack and are collected as sub-rows of the pseudo stack **`infra-uis`**, shown first in the **Infrastruktur** block. On a name matching both lists, this one wins. A sidecar pulled in only because its *stack* name matched (e.g. `portainer_agent`) is labelled `stack/service` so it stays attributable once detached. |
| `services.critical` | `[]` | Service names flagged as critical (parsed and available on the data model; not visually emphasised in the current matrix view). |
| `thresholds.memory.warning` / `.critical` | `75` / `90` | RAM usage % thresholds (yellow / red). |
| `thresholds.swap.warning` | `1` | Swap usage % above which SWAP turns yellow. |
| `thresholds.filesystem.warning` / `.critical` | `80` / `90` | Filesystem usage % thresholds. |
| `thresholds.load.warning` / `.critical` | `0.8` / `1.0` | Load-average thresholds as a **per-CPU multiplier** (compared against `load1 / cpu_count`). |
| `health.budget` | `5.0` | Total wall-clock budget in seconds for all health checks. Every check runs concurrently as its own task, so this bounds the login delay — it is not the sum of the individual timeouts. |
| `health.timeout.*` | postgres `1.5`, mongodb `2.5`, kafka `4.0`, glusterfs `1.0`, rustfs `2.0`, wireguard `1.0`, dns `2.5` | Deadline for one check. Each cluster kind, the peer check and the DNS check are separate tasks; a task that overruns its value is reported as `… <name>: time budget exceeded` while every other check keeps its result. Values above `health.budget` have no effect — the budget always wins. See [How the timeouts are enforced](#how-the-timeouts-are-enforced). |
| `health.enabled` | all five kinds | Which cluster kinds to probe: `postgres`, `mongodb`, `kafka`, `glusterfs`, `rustfs`. |
| `health.dns.expect` | `[]` | Array of `{name, addresses}`. `addresses` is optional; without it the name only has to resolve at all. |

### Full example

```toml
# Fallback width for non-TTY (MOTD) rendering. Interactive logins auto-detect
# the real terminal width regardless of this value.
width = 200

[docker]
timeout = 1.5
description_label = "lmu.service.description"
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
name = "login.lmu.de"
addresses = ["10.9.9.9"]
```

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

Measured on production nodes on 2026-07-31: on `lmzvd06-ccc-01` (5-node
Swarm), the whole section takes **3.25 s** end to end, within the 5 s default
budget, with individual probe costs of PostgreSQL `pg_autoctl show state`
0.13 s, Kafka `kafka-metadata-quorum.sh` 2.6 s (JVM startup, not
optimisable), GlusterFS `gluster … --xml` 0.10 s, and RustFS `/health`
~0.2 s. The `lrz_cc` cluster that node belongs to runs no MongoDB, so that
figure — `db.hello()` 0.95 s — was measured separately, on
`lmzvd06-internet-app-1`.

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

### Icon vocabulary

The section's core idea: it never claims to know something it did not
measure.

| Icon | Meaning |
|------|---------|
| ✅ | measured healthy |
| ⚠️ | warning (e.g. a lagging PostgreSQL replica, a diverging DNS entry) |
| 💀 | measured broken |
| `·` | not observable / not attempted — see below for where it appears |
| `…` | the check ran out of the shared time budget |
| `✗` | the check itself failed (a command errored, a connection was refused) |
| `n/a here` | not applicable — this node runs no member of the service |

`…` and `✗` mean different things and must not be conflated: a budget
timeout says nothing about the service's health, only that the panel gave up
waiting for it; a failed check (`✗`) is a statement about the service, or
about the tool used to ask it.

The neutral dot (`·`) exists because **MongoDB reports its replica-set
members but not their state**: `db.hello()` tells the panel who belongs to
the set, but only the primary and the member it just ran the command against
have known state — every other member is genuinely unmeasured, and rendering
an unearned ✅ for it would be worse than saying nothing. Every other check in
this section (PostgreSQL's `pg_autoctl` rows, Kafka's quorum voters,
GlusterFS peers/bricks) reports ✅ or 💀 for each member it lists, never `·`,
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
health. Those lines are also prefixed with the same neutral dot (`·`) as the
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
every clustered service falls back to Docker's own measurement — `·` only for
a row Docker itself measured clean (fully staffed or scaled to zero), still
`💀`/`⚠️` when Docker measured it dead or degraded — honest, but the column
only earns its cluster icons with `--panel health` beside it.

Options:

| Option | Values | Default | Meaning |
|--------|--------|---------|---------|
| `--scope` | `global` \| `user` | `user` | `/etc/profile.d` (needs root) vs. your own login profile. |
| `--panel` | `full` \| `server` \| `docker` \| `health` | `full` | Which command to run; repeatable. |
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
