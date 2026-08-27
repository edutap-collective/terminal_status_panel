# About the Docker panel

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

# The image column

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

# Engine versions and manager reachability

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

# Docker's own disk footprint

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

# Memory per service

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

## Three references, three statements

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

## The figure is not `memory_stats.usage`

That value includes the page cache. The number `docker stats` prints — and the
one a reader expects — subtracts `inactive_file` (cgroup v2) or
`total_inactive_file` (v1) first. The difference is not cosmetic: measured
against a live daemon, one container reported **79.0 MB raw against 32.4 MB
real**, an overstatement of 144%.

## Why there is no CPU column

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
