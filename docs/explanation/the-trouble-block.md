# About the TROUBLE block

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

## What qualifies

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
