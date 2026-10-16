# About the TROUBLE block

```{versionchanged} 0.14
TROUBLE is its own section, no longer part of DOCKER INFOS, and it reports
**host events** next to the containers. It has its own command,
`status-trouble`. In `status-full` it sits between HEALTH and DOCKER INFOS; in
`status-docker` it sits directly above DOCKER INFOS.
```

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

## Host events

A host has trouble too, with or without Docker. When the collector runs (see
{doc}`About host health <host-health>`), its journal events join the same
table, one row per kind that happened in the window:

```
TROUBLE  (last 12 h)
    SERVICE                   NODE   FAILS   UP  CAUSE
💀  host: OOM kill              —      ↻ 2×    —  last 3h ago
⚠️  host: Apache slots full     —      ↻ 4×    —  last 41m ago
⚠️  host: VRRP state change     —      ↻ 6×    —  last 12m ago
```

| Row | Event | Marker |
|-----|-------|--------|
| `host: OOM kill` | the kernel killed a process | 💀 |
| `host: unit failed` | a systemd unit failed | 💀 |
| `host: Apache slots full` | Apache ran out of process slots (`AH03490`) | ⚠️ |
| `host: VRRP state change` | an instance entered MASTER, BACKUP or FAULT | ⚠️ |
| `host: Apache reload` | systemd reloaded Apache | ⚠️ |

The row shows the count and when the last one was, and **never any log text**:
the collector reduces an event to a count and a timestamp before it reaches the
state file. If the state file is older than 120 seconds, the cause carries
`(state 3 min old)`, because a count from a collector that has stopped is a
memory. If the collector's last journal read failed (a timeout, or no
permission to read the journal), it keeps the counts it had, and the cause
carries `(not refreshed)`: the state file is new, but the counts are not.
TROUBLE then also shows one yellow line, `⚠️ host events: journal not
refreshed (<code>)`, below the rows or, when the counts are zero and there is
no row, alone under the heading: a failed read must not look like a quiet
twelve hours. THIS HOST shows `journal: not refreshed (<code>)` as well. If the file is missing there
are no host rows; the HEALTH section is where the missing collector is
reported.

`[trouble] host_events` picks which kinds are reported (and which the collector
counts); an unknown name in it is dropped and reported by `--debug`, and a
repeated one counts once. When the list changes, the collector reads the whole
twelve-hour window again, so a kind enabled later still finds its recent
events. A host
without Docker shows only the host rows. A host with Docker whose daemon cannot
be reached shows the host rows and, in DOCKER INFOS, its usual warning.

## What qualifies (containers)

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

## Three absences, three meanings

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

## What it costs

Nothing in steady state. Services meeting their replica count and up longer
than the window are dismissed without the history call, so one sick service
among ten healthy ones produces exactly one extra API call rather than eleven.
In an incident the few rows that qualify each pay one call — and in exchange
the panel says what an SSH session would have said.

The block is capped at ten rows, and what is dropped is always named
(`… and 7 more`). A node reboot brings every service on it in at once, and a
silent cap would claim ten services are troubled where twenty are.
