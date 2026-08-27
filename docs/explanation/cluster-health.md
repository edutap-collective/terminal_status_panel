# About the cluster health checks

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
takes **3.25 s** end to end, within the default budget, with individual probe
costs of PostgreSQL `pg_autoctl show state` 0.13 s, Kafka
`kafka-metadata-quorum.sh` 2.6 s (JVM startup, not optimisable), GlusterFS
`gluster … --xml` 0.10 s, and RustFS `/health` ~0.2 s. That cluster runs no
MongoDB, so its figures were measured separately on another one, a five-member
replica set:

| MongoDB probe | Measured |
|---|---|
| `mongosh` start-up, before a line of script runs | 0.97–1.50 s |
| local `db.hello()` only (the pre-0.10 check) | 0.97–1.50 s total |
| **with the member fan-out, all five answering** | **1.95–2.01 s total** |
| one member, when it answers | 171–279 ms |
| one member, connection refused | 97 ms |
| one member, name does not resolve | 113 ms |
| one member, blackholed (the capped case) | 787 ms |
| worst case: five members, all blackholed | 4.65–4.93 s total |

The fan-out therefore costs about 0.8 s on a healthy set and stays **below
Kafka's 2.6 s**, so it does not lengthen the section at all — the checks run
concurrently and Kafka is still the one everything waits for. The 6 s deadline
exists for the worst case, and the 8 s budget exists because a per-kind
timeout above the budget has no effect.

## How the timeouts are enforced

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

## A stopped standby is not a slow one

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

## What the section cannot see

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
