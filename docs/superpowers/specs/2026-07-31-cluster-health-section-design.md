# CLUSTER HEALTH Section — Design

**Date:** 2026-07-31
**Status:** Approved (design) — pending spec review
**Package:** `terminal_status_panel`

## Purpose

The panel currently answers *what is deployed* (Docker services, replicas, node
placement). It does not answer *is the clustered infrastructure actually
working*. Three questions come up at every login and are today answered by
hand:

1. **Are the clustered infrastructure services healthy?** Who is the PostgreSQL
   primary, does Kafka have a controller leader, are all GlusterFS bricks
   online? A Swarm task in state `running` says nothing about this — a broker
   can be up and out of quorum at the same time.
2. **Are the nodes reachable?** The Swarm view can report `ready` while the
   WireGuard tunnel carrying the overlay networks is stale.
3. **Does DNS resolve correctly?** Specifically: do DNS and `/etc/hosts`
   disagree, and does the host's own name resolve consistently in both
   directions.

This design adds a third section, `health`, alongside the existing `server` and
`docker` sections. It renders at login through `status-full` and separately
through a new `status-health` entry point.

### Scope

In scope: the app servers (inventory groups `lrz`, `lrz_cc`, `vzd-app`,
`lmucard` in `ansible-app-server`). The webfe servers are explicitly out of
scope for this iteration — they run no Swarm and would need a different set of
checks (VRRP state, Apache balancer members, certificate expiry). They get
their own design once the pattern here has proven itself.

Also out of scope: the Traefik wiring viewer (entrypoint → router → middleware
→ service). It is a separate sub-project with its own spec, because its data
source, runtime and risk profile are all different.

## Architecture

### Why a separate section

The package already models sections as the unit of isolation: each section
collects only the data it needs, has its own entry point, and degrades
independently (`cli.collect_all` gates every collector on section membership).
The new checks are also the only ones that need a time budget. Putting them in
a section keeps that budget in one place instead of spreading concurrency
through the existing collectors.

The rejected alternative was weaving the new data into the existing panels (DB
roles into the DOCKER INFOS matrix, WireGuard into the node table). It is more
compact, but it gives the Docker section a second data source and blurs the
failure mode: a hung `docker exec` would then take down the existing service
matrix, which is the panel's most load-bearing view.

### New modules

| Module | Responsibility |
|---|---|
| `budget.py` | Run named callables concurrently under a total wall-clock budget; return whatever finished. The only place in the package that touches concurrency. |
| `collectors/clusters.py` | Probe the clustered infrastructure services. One function per service kind, identical signature. |
| `collectors/network.py` | Peer reachability: WireGuard handshake age, TCP fallback. |
| `collectors/dns.py` | Resolver reachability, own name forward/reverse, peer names, configured name list. |
| `render/health.py` | Render the three panels of the section. |

Existing modules change in three small, mechanical ways: `render/layout.py`
adds `"health"` to `SECTIONS` and dispatches the new panels, `cli.py` gains a
`health_main` entry point gated like the existing two, and `pyproject.toml`
registers the `status-health` console script.

Threads, not async: every check is subprocess or socket I/O, the rest of the
package is synchronous, and a login banner does not justify converting the
whole collector layer. `budget.py` uses `concurrent.futures.ThreadPoolExecutor`
with a total deadline.

### New dependency

**dnspython.** Required, not convenience: `socket.getaddrinfo` consults
`/etc/hosts`, and the discrepancy between `/etc/hosts` and DNS is precisely the
fault this section must surface. Detecting it means querying the resolvers
directly. The same library provides per-resolver latency measurement. It is
pure Python with no transitive dependencies.

## Data model

Added to `model.py`, all fields defaulting to empty/None so a failed collector
degrades instead of raising — consistent with the existing dataclasses.

```python
@dataclass
class ClusterMember:
    name: str                    # pg18-lmzvd06-ccn-02, kafka-lmzvd06-ccn-01, …
    node: str | None             # derived Swarm hostname, when derivable
    role: str | None             # primary / secondary / leader / voter / peer / None
    healthy: bool = False
    detail: str | None = None    # LSN, brick port, handshake age — kind-specific

@dataclass
class ClusterService:
    kind: str                    # postgres | mongodb | kafka | glusterfs | rustfs
    name: str | None = None      # PostgreSQL-18, lrz_app, cluster id, volume name
    applicable: bool = True      # False when this node runs no member
    reachable: bool = False
    leader: str | None = None    # primary / controller leader; None for leaderless
    quorum_ok: bool | None = None
    members: list[ClusterMember] = field(default_factory=list)
    error: str | None = None

@dataclass
class PeerReachability:
    name: str
    method: str                  # wireguard | tcp
    ok: bool = False
    detail: str | None = None    # handshake age or probed port

@dataclass
class DnsCheck:
    label: str
    ok: bool | None = None       # None = warning (inconsistent, not broken)
    detail: str = ""

@dataclass
class HealthInfo:
    clusters: list[ClusterService] = field(default_factory=list)
    peers: list[PeerReachability] = field(default_factory=list)
    dns: list[DnsCheck] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)   # names of checks that hit the budget
```

`PanelData` gains `health: HealthInfo | None = None`.

`truncated` exists so the renderer can distinguish *unknown* from *broken*. See
"Failure semantics".

## Collection

### Clustered infrastructure services

Every probe follows the same shape: find a locally running container belonging
to the service, run one read-only command inside it through the Docker API, and
parse the output into `ClusterService`. Nothing is written, no credentials are
needed, and no database or broker protocol is spoken by the panel itself — the
existing "Docker API only" principle is preserved in the sense that matters:
the panel's only privilege is the Docker socket it already uses.

All commands below were verified against the production clusters on 2026-07-31.

**PostgreSQL** — `pg_autoctl show state`, measured 0.13 s on `lmzvd06-ccc-01`.

Works from *any* data node, not only the monitor; the monitor container does
not run on every node. Output is a fixed-width table:

```
               Name |  Node |                Host:Port |       TLI: LSN |   Connection |      Reported State |      Assigned State
--------------------+-------+--------------------------+----------------+--------------+---------------------+--------------------
pg18-lmzvd06-ccn-02 |     1 | pg18-lmzvd06-ccn-02:5432 |   1: 0/75243B8 |   read-write |             primary |             primary
pg18-lmzvd06-ccn-03 |     2 | pg18-lmzvd06-ccn-03:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
```

- `leader` = the row whose *Reported State* is `primary`.
- Replication lag = a secondary whose LSN differs from the primary's.
- *Reported State* ≠ *Assigned State* means the cluster is mid-transition;
  rendered as a warning, because it is neither healthy nor broken.
- `quorum_ok` = more than half the data nodes report `primary` or `secondary`.

**MongoDB** — `mongosh --tls --tlsAllowInvalidCertificates --quiet --eval
'<json>'` calling `db.hello()`, measured 0.95 s on `lmzvd06-internet-app-1`.

`db.hello()` needs no authentication — the role's own healthcheck already
relies on an unauthenticated `ping`. It returns `setName`, `me`, `primary`,
`isWritablePrimary` and `hosts`.

Stated limitation: `db.hello()` reports the member list but not each member's
state. `rs.status()` would, but requires credentials and is deliberately out of
scope. For MongoDB, `quorum_ok` therefore means exactly *a primary exists* —
and the renderer must not imply more than that.

**Kafka** — `/opt/kafka/bin/kafka-metadata-quorum.sh --bootstrap-server
localhost:9092 --command-config /client.properties describe --status`, measured
2.6 s on `lmzvd06-ccc-01`.

The tools are not on `$PATH`; the absolute path is required. `/client.properties`
is mounted by the `kafka` role explicitly for "manuelle Abfragen per docker
exec" and uses the broker certificate, whose principal is a superuser.

```
LeaderId: 1   LeaderEpoch: 2   HighWatermark: 173016
MaxFollowerLag: 0   MaxFollowerLagTimeMs: 296
CurrentVoters: [{"id": 0, …}, …]   CurrentObservers: []
```

- `leader` = `LeaderId` mapped to its voter endpoint hostname.
- `quorum_ok` = a leader exists. Deliberately nothing more: the status output
  reports `MaxFollowerLag` but not which follower is behind, so "all voters
  healthy" cannot be derived from it without inventing a lag threshold.
- Members are the entries of `CurrentVoters` (role `voter`) and
  `CurrentObservers` (role `observer`); `MaxFollowerLag` is the service-level
  detail.

The 2.6 s are JVM startup and cannot be optimised away. Kafka gets a 4.0 s
individual timeout so a hung broker cannot consume the whole budget and starve
the cheap checks.

**GlusterFS** — `sudo -n gluster peer status` and `sudo -n gluster volume
status`, measured 0.10 s on `lmzvd06-ccc-01`.

Leaderless: `leader` stays `None`. Members are peers with their connection
state plus the per-brick online flag from the volume status. Gluster names its
peers `wg-<node>`, i.e. it runs across the same WireGuard tunnels the NETZ
panel checks — a stale tunnel therefore shows up consistently in both panels.

Requires `sudo -n`, the same mechanism approved for `wg show`. Without sudo the
block is omitted silently (`applicable=False`), not reported as an error.

**RustFS** — `GET /health` against every endpoint in `RUSTFS_VOLUMES`,
measured ~0.2 s.

`/health` answers 200 unauthenticated; `/minio/health/cluster` and
`/rustfs/admin/v3/info` answer 403. Cluster internals (erasure coding, heal
state) are therefore not available without signed admin requests — an accepted
limitation. Leaderless: one `ClusterMember` per endpoint, role `peer`, healthy
when `/health` answered 200.

The endpoint list is read from `RUSTFS_VOLUMES` in the container environment
via a Docker API inspect, deliberately **not** from configuration. RustFS runs
today in `shared` mode (one replica on GlusterFS) and is being extended to all
nodes; `rustfs_mode: distributed` exists in the role but is not implemented.
Deriving the endpoints from the container makes the check correct in both modes
and survives the rollout without a code or config change. The requests run from
inside the local RustFS container, because the `rustfs` overlay network
publishes no host ports.

**Not applicable is not an error.** No Mongo on `lrz_cc`, no Kafka on
`vzd-app`, no local member of a given service — all render as "n/a hier". A
node that legitimately does not run a service must never show a red panel.

### Peer reachability

Primary source `sudo -n wg show all dump`: one tab-separated line per peer,
including `latest-handshake` as a Unix timestamp.

- younger than 3 minutes → ok (WireGuard rekeys every 2 minutes under traffic)
- older → warning
- never → critical

Peers map to node names through their endpoint; when no name can be derived the
IP is shown.

If `sudo -n` fails, the collector falls back to parallel TCP connects against
the Swarm peers (already known from the Docker section) on port 2377. The
method is rendered explicitly as `wg` or `tcp` so the reader knows which claim
is being made. The fallback exists so that a host without sudo produces a
weaker answer rather than a silent gap.

### DNS

Four checks, all through dnspython so `/etc/hosts` cannot mask a DNS fault:

1. **Resolvers** from `/etc/resolv.conf`: does each answer, and how fast.
2. **Own FQDN**: forward (A/AAAA) and reverse (PTR) consistent with each other.
3. **Peer names** from the Swarm node list: do they all resolve.
4. **Configured names** with optional expected addresses.

For every name checked, the DNS answer is compared against `/etc/hosts`. A
divergence is a **warning, not an error** — such overrides are sometimes
deliberate in this environment, and flagging them as failures would train
people to ignore the panel.

## Failure semantics

- Every collector catches all exceptions and returns its dataclass with `error`
  set. No single check can take down the section.
- `main()` continues to return 0 unconditionally. The login shell must never
  break.
- **Timeout and failure are rendered differently** (`…` versus `✗`). A blown
  budget says nothing about the state of the service, and conflating the two
  would be the worst possible property of a status panel. This is what
  `HealthInfo.truncated` is for.
- Missing sudo, missing local container, missing WireGuard: not errors, but
  "not applicable".

## Rendering

A section titled CLUSTER HEALTH, laid out in two rows because five services
plus network plus DNS would be too cramped side by side. Status icons follow
the existing scheme (✅ / ⚠️ / 💀).

```
┌─ INFRASTRUKTUR-DIENSTE ────────────────────────────────────────────────────────┐
│ PostgreSQL-18  ✅ Quorum   │ Kafka (KRaft) ✅ Lag 0ms │ GlusterFS ✅ shared     │
│  primary   ccn-02          │  leader   ccn-01 (id 1)  │  5/5 Bricks online     │
│  secondary ccn-01 03 04    │  voters   5/5            │  5/5 Peers connected   │
│  secondary ccc-01          │  observer –              │ RustFS    ✅ 5/5 live  │
│ MongoDB        n/a hier    │                          │                        │
├─ NETZ (wg) ────────────────┴──────────┬─ DNS ──────────────────────────────────┤
│ ccc-01 ✅0:14  ccn-01 ✅0:31  …        │ Resolver ✅ 3ms   FQDN ✅ A+PTR        │
└───────────────────────────────────────┴────────────────────────────────────────┘
```

Narrow terminals stack the panels vertically, as the existing layout already
does.

## Configuration

A new `[health]` block. Every key has a default that reproduces sensible
behaviour, so a cluster that configures nothing still gets the section.

| Key | Default | Meaning |
|---|---|---|
| `health.budget` | `5.0` | Total wall-clock budget in seconds for all health checks. |
| `health.timeout.postgres` | `1.5` | Individual timeout. |
| `health.timeout.mongodb` | `2.5` | |
| `health.timeout.kafka` | `4.0` | Deliberately below the total budget. |
| `health.timeout.glusterfs` | `1.0` | |
| `health.timeout.rustfs` | `2.0` | |
| `health.timeout.wireguard` | `1.0` | |
| `health.timeout.dns` | `2.5` | |
| `health.enabled` | all kinds | Which cluster kinds to probe. |
| `health.dns.expect` | `[]` | List of `{name, addresses}`; `addresses` optional. |

All checks run concurrently, so the wall clock is bounded by `health.budget`,
not by the sum of the individual timeouts.

## Testing

Test-first, `pytest`, no real network and no real Docker access — matching the
existing `test_collectors_*.py`, which drive a fake Docker client.

The parser fixtures are the **real outputs recorded on the production clusters**
(`pg_autoctl show state`, `db.hello()` JSON, `kafka-metadata-quorum` status,
`gluster peer status` / `volume status`, `wg show dump` lines), not invented
examples. Parsing real fixed-width and JSON output is where this kind of
collector actually breaks.

Specific cases beyond the happy path:

- `budget.py`: a deliberately slow callable must yield a partial result and
  must not hang; the slow check must appear in `truncated`.
- Cluster mid-transition: *Reported State* ≠ *Assigned State* renders as a
  warning.
- LSN divergence marks the lagging secondary.
- `sudo -n` failure falls back to TCP and labels the method `tcp`.
- No local member of a kind yields `applicable=False`, not an error.
- DNS answer differing from `/etc/hosts` yields a warning (`ok=None`), not a
  failure.
- Render tests for panel structure and width, as in `test_render_panels.py`.

## Documentation

The README currently states: *"All Docker data is read from the Docker API only
— no database or broker protocol is ever spoken to."* That sentence becomes
misleading with this section and must be revised rather than left standing. The
accurate claim is narrower and worth stating precisely: the panel itself opens
no database or broker connection and holds no credentials; it executes
read-only status commands **inside** the service containers through the Docker
socket it already uses. The README also needs the new section, the
`status-health` command, and the `[health]` configuration reference.

## Ansible

`roles/status_panel` in `ansible-app-server` gains:

- the new defaults in `defaults/main.yml`, documented in the same commented
  style as the existing ones,
- the `[health]` blocks in `templates/config.toml.j2`,
- the corresponding entries in `meta/argument_specs.yml`,
- a pinned `status_panel_version` bump once the package is released.

A cluster that sets no DNS expectation list behaves exactly as today.

## Open points

- The `sudo -n` calls (`wg`, `gluster`) assume the operator accounts have
  passwordless sudo, which is the case on the app servers today. Hosts without
  it fall back or omit the block; no sudoers rule is added by this design.
- MongoDB quorum is only "a primary exists" (see above). Deepening it would
  require credentials and belongs to a later decision, not this iteration.
