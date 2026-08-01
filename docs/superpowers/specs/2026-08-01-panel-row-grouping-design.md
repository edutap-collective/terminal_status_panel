# Per-Node Task Counts, Ordinal Instance Grouping, bugsink — Design

**Date:** 2026-08-01
**Status:** Approved (design) — pending spec review
**Package:** `terminal_status_panel`

## Purpose

Three refinements to the DOCKER INFOS block, all prompted by reading the
rendered panel on a production node.

1. **A node cell cannot show more than one.** `_node_cell` renders a single ✅
   whether one task runs on that node or five. Nothing in the matrix reveals
   that a service may hold several instances on one host.
2. **Ordinal instances render as separate rows.** `heidi_connector` must run as
   `heidi_connector_1`, `heidi_connector_2`, … because each pinned instance
   needs its own secrets. That is an implementation detail of secret
   management, and the panel currently spreads it across one row per instance.
3. **bugsink sits in the wrong block.** It is infrastructure and renders under
   *Service*.

None of this changes what is measured. All three are about what the matrix
lets you see.

## Change 1 — per-node task counts

`_node_cell` is binary today: all tasks running → ✅, otherwise 💀. It gains the
same icon-plus-count idiom the `Working` column already uses, but only where
there is something to say:

| Tasks on that node | Cell |
|---|---|
| none | blank — unchanged |
| 1, running | `✅` — unchanged |
| 1, not running | `💀` — unchanged |
| n > 1, all running | `✅n` |
| n > 1, some running | `⚠️k/n` |
| n > 1, none running | `💀0/n` |

The single-task cases stay byte-identical, so the common row is exactly as
quiet as it is today and the matrix only thickens where instances actually
pile up. The `⚠️` for a partially-staffed node is new at cell level and matches
the vocabulary: degraded, but serving.

## Change 2 — collapse ordinal instances

`_base_service_name` strips a known trailing node hostname so per-node replicas
collapse (`kafka_kafka-lmzvd06-ccc-01` → `kafka_kafka`). A second step runs
after it: a trailing `_<digits>` is removed as well. The two compose, should a
name ever carry both.

`edutap_production_heidi_connector_1` therefore becomes the row
`heidi_connector`, and with three pinned instances it renders `✅ 3/3` with
checkmarks on the three nodes — the same shape `kafka` and `pg` already have.

### Underscore only, never hyphen

The separator must be `_`. With `-<digits>` a stack named `PostgreSQL-18` whose
service carries the same name would produce the full name
`PostgreSQL-18_PostgreSQL-18` and be mutilated to `PostgreSQL-18_PostgreSQL`.
`_` is what Docker Swarm puts between a stack and its service, and it is the
form the real case has.

### Stripping is unconditional, and what that costs

The suffix is removed whether or not siblings exist, so the row keeps a stable
name no matter how many instances are deployed. The alternative — collapse only
when two or more siblings share a base — was considered and rejected: it would
rename the row as instances come and go.

The accepted cost, recorded here so nobody rediscovers it as a bug: if
`heidi_connector_2` is removed, its row disappears into the remaining one and
the panel reads `✅ 1/1`. Nothing indicates that a second instance was ever
expected. The panel reports what Swarm currently wants, and Swarm no longer
wants that instance.

This is a deliberate exception to the section's usual rule about not hiding
gaps, and it is bounded: the gap is invisible only across a *deployment*
change, not across a *failure*. A failing instance still has a desired replica
and renders `💀` or `⚠️` in both the `Working` column and its node cell.

### How the two changes meet

Worth noting because it is the case that exercises both at once: once ordinal
instances collapse into one row, that row's services can put more than one task
on the same node — `heidi_connector_1` and `heidi_connector_2` pinned to the
same host. `_node_cell` already aggregates across all of a row's services, so
Change 1's `✅2` is what makes that visible instead of a single ✅ hiding it.
Change 2 is therefore the main reason Change 1 will fire at all on this
cluster, and a test should cover the pair together, not only each alone.

## Change 3 — bugsink is infrastructure

Add `bugsink` to `DEFAULT_INFRASTRUCTURE_STACKS` in `config.py`.

The Ansible role does not render `infrastructure_stacks` into
`/etc/terminal-status-panel/config.toml` — it writes only `width`, the
`[docker]` keys and the thresholds — so the package default is what production
uses. The change reaches the app servers with the next package release and
needs no playbook run.

## Testing

**Change 1.** A table over the six cell states, including the two new
multi-task ones and the `n > 1, none running` case. The single-task cases are
asserted to be unchanged, since they are the common path.

**Change 2.** Stripping with and without a node suffix; several ordinal
instances collapsing into one row whose count sums their replicas; and
explicitly that `PostgreSQL-18_PostgreSQL-18` is **not** mutilated — that is
the case the underscore-only rule exists for and the one a future
"simplification" would break.

**Change 3.** `bugsink` renders in the Infrastruktur block and not under
*Service*.

## Out of scope

The measurement layer. No collector changes, no new configuration keys, no
change to the `Working` column's verdict rules. `_node_cell`'s new `⚠️` is a
rendering refinement of data the collector already provides.
