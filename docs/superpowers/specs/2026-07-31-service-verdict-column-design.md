# Service Verdict Column & Flowing Cluster Blocks — Design

**Date:** 2026-07-31
**Status:** Approved (design) — pending spec review
**Package:** `terminal_status_panel`

## Purpose

Two changes, both prompted by looking at the rendered panel on a production
node rather than at the code.

1. **DOCKER INFOS shows where a service runs, never whether it works.** On
   `lmzvd06-ccc-01` the `edutap_production` stack renders nine rows with no
   checkmark at all. Those services are not switched off — they are at `0/3`,
   `0/2`, `0/1`: they want replicas and get none. Today that outage is
   indistinguishable from a service that is deliberately scaled to zero, and
   both look like an unremarkable blank row.

2. **CLUSTER HEALTH wastes the terminal.** The clusters render as one vertical
   list roughly 55 lines tall while two thirds of the width stay empty.

Neither change touches how anything is measured. Both are about making what was
already measured legible.

## Change 1 — a `Working` column

A new column in `_stack_matrix`, placed immediately after the service name and
before the per-node columns: first the verdict for the row, then the detail of
where it runs.

Each cell carries **an icon and a count** — `✅ 3/3`, `⚠️ 2/5`, `💀 0/3`,
`· 0/0`. The icon is the verdict, the count is running/desired Docker tasks.
Keeping both matters most where they disagree: `⚠️ 5/5` on Kafka reads "all
five brokers are running and the quorum is degraded anyway", which is exactly
the case where a replica count on its own lies.

### Where the verdict comes from

Two sources, in this order.

**1. The cluster probes, when the health section ran.** For the five clustered
services the verdict is `ClusterService.quorum_ok` — the same judgement the
CLUSTER HEALTH block shows, so the two sections cannot contradict each other:

| `ClusterService` state | Icon |
|---|---|
| `quorum_ok is True` | ✅ |
| `quorum_ok is False` | 💀 |
| `quorum_ok is None` | `·` |
| `error` set | ✗ |
| `applicable is False` | `·` |

`applicable is False` deserves the neutral dot rather than a fallback to the
replica count. It means the probe found no member *on this node* — a statement
about the observer, not about the service. The Swarm service may well be
running fine elsewhere, and a ✅ derived from replicas would be the same
over-claim as in the `status-docker` case below. The same applies when a kind
is absent from `health.enabled`: no verdict was produced, so none is shown.

**2. Otherwise the replica counts**, summed across the services a row bundles
(a row collapses the per-node replicas of one logical service):

| State | Icon |
|---|---|
| running == desired, desired > 0 | ✅ |
| 0 < running < desired | ⚠️ |
| running == 0, desired > 0 | 💀 |
| desired == 0 | `·` — deliberately scaled down, not a failure |
| global mode | as above, with the node count as the denominator |

`desired == 0` earns its own state on purpose. A service scaled to zero is a
decision, not an outage, and rendering it as 💀 would train people to ignore
the column.

### The honest gap

`status-docker` runs without the health section, so no quorum verdict exists.
A row that *is* one of the five clustered services then renders `·` plus its
count — **not** a replica-derived ✅. "Five brokers are running" is not the
claim this column makes, and substituting it would be precisely the
over-claiming this section was built to avoid. The count is still shown,
because the count is a fact either way.

### Joining the two sections

`clusters.py` gains `kind_for_service(name: str) -> str | None`, reusing the
container-name patterns it already owns (`_pg-`, `kafka_kafka-`, `mongodb`,
`rustfs_rustfs`). `services_section` takes `health: HealthInfo | None` as an
optional argument, which `layout.docker_section` passes through from
`PanelData`.

Matching on the patterns rather than on `ClusterService.name` is deliberate:
those names come from different places per probe — the stack for PostgreSQL,
the cluster id for Kafka, the volume for GlusterFS — and would not join
reliably. The patterns are the one identifier both sides already share.

The import points from `render` to `collectors` and never the other way, so no
cycle appears. It is the only such import; the alternative — duplicating the
patterns in the renderer — would put the same string in two files, which is how
the crash-loop detection was silently broken once already.

### Stack header rows

Rows that head a multi-service stack keep an empty cell. Their sub-rows sit
directly beneath and carry their own verdicts; an aggregate would only repeat
what is already on screen a line later.

## Change 2 — flowing cluster blocks

`_clusters_body` builds one renderable per applicable cluster and arranges them
with `rich.columns.Columns`, which flows them into as many columns as the width
allows — three at 150 columns, two at 100, one at 60. No fixed column count, so
nothing is wasted on a wide terminal and nothing breaks on a narrow one.

Services that are **not applicable** collapse into a single dim line below the
blocks: `n/a here: MongoDB`. Giving a one-line "n/a" its own column would spend
exactly the space this change reclaims.

Services that **failed** or **ran out of budget** stay full blocks in the flow.
They carry a message, and a message belongs where the eye is.

The existing branch order in `_clusters_body` — truncated, then
`clusters_probed`, then empty — is unchanged. Only the arrangement of the
per-service blocks changes.

## Testing

**Change 1.** A table-driven test over the five replica states, including
`desired == 0` and global mode. Separate tests for the health verdict taking
precedence over the replica count, for the `·`-without-health case, and for
`⚠️ 5/5` — the disagreement case, which is the whole reason the count stays.
A test that `kind_for_service` matches the real service names recorded from the
cluster (`PostgreSQL-18_pg-lmzvd06-ccc-01`, `kafka_kafka-lmzvd06-ccc-01`,
`rustfs_rustfs-lmzvd06-ccn-01`) and does **not** match `kafbat-ui_kafbat-ui`.

**Change 2.** Rendering at width 150 puts at least two cluster blocks on one
line; at width 60 exactly one per line. The `n/a here:` line appears instead of
a block for a non-applicable service, and lists several such services on one
line. An all-not-applicable panel renders the summary line and no blocks.

## Documentation

The README's icon vocabulary section gains the `Working` column: what the icon
means, what the count means, that they come from different sources for the
clustered services, and why `·` appears there under `status-docker`.

## Out of scope

The panel's measurement logic. No probe changes, no new configuration keys, no
change to the health section's own semantics. This is presentation only —
apart from the join helper, which exists to keep one identifier in one place.
