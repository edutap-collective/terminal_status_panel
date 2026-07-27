# Node Availability & `infra-uis` Pseudo Stack — Design

**Date:** 2026-07-27
**Status:** Approved (design) — pending spec review
**Package:** `terminal_status_panel`

## Purpose

Two independent corrections to the DOCKER INFOS section of the status panel:

1. **Node availability.** A Swarm node that is `ready` but has been drained (or
   paused) is currently rendered as ✅ — it looks fully functional although it
   accepts no tasks. Drained capacity must be visibly distinct from healthy
   capacity.
2. **`infra-uis` pseudo stack.** Administrative web UIs for infrastructure
   services (`kafbat-ui`, `cloudbeaver`, `mongo-express`, a RustFS S3 browser,
   …) currently scatter across the *Service* and *Container* matrices, because
   their names do not match any `infrastructure_stacks` keyword. They belong
   together, in the *Infrastruktur* block, under one grouping row.

Both changes stay inside the existing architecture: the collector reads one
more Swarm API field, the renderer gains one more status branch and one more
grouping step, and the configuration gains one list.

## Change 1 — Node availability

### Background

The Docker Swarm node API exposes two orthogonal facts:

| Field | Values | Meaning |
|-------|--------|---------|
| `Status.State` | `ready`, `down`, `disconnected`, `unknown` | Does the node talk to the managers? |
| `Spec.Availability` | `active`, `pause`, `drain` | Is the node allowed to run tasks? |

`collectors/docker.py` reads only the former (`reachable = state == "ready"`),
so drain and pause are invisible.

### Data model

`SwarmNode` gains one field and one derived property:

```python
@dataclass
class SwarmNode:
    name: str
    reachable: bool = False
    role: str | None = None
    leader: bool = False
    state: str | None = None            # raw node state (ready / down / ...)
    availability: str | None = None     # active / pause / drain (Spec.Availability)

    @property
    def operational(self) -> bool:
        """Ready *and* accepting tasks."""
        return self.reachable and self.availability in (None, "active")
```

`reachable` keeps its current meaning ("the node reports `ready`"). The new
"is actually usable" question is answered by `operational`. Keeping the two
apart preserves the existing semantics and lets the renderer distinguish
*crashed* from *administratively withdrawn*.

`availability` defaults to `None`, which `operational` treats as active — a
Docker daemon that does not report the field behaves exactly as today.

### Collector

`_node_map()` additionally reads `attrs["Spec"]["Availability"]` and passes it
through. No other collector change; the function stays exception-safe.

### Rendering

`_node_health()` gains a third branch, with a new marker constant
`_WARN = "⚠️"`:

| Node state | Rendering |
|------------|-----------|
| `reachable`, availability `active` or `None` | `✅` |
| `reachable`, availability `drain` / `pause` | `⚠️ drain` / `⚠️ pause`, yellow |
| not `reachable` | `💀 down`, red (unchanged) |

The Swarm summary line (`_swarm_body`) reports non-operational nodes:

```
Swarm  active  ·  manager  ·  5 nodes (1 drain, 1 down)  ·  42 services  ·  7 stacks
```

The parenthetical appears only when at least one node is non-operational.
Drain/pause counts render yellow, down counts red. Nodes are counted by their
`availability` value for the withdrawn ones and as `down` for unreachable ones;
an unreachable node counts only once, as `down`, regardless of its
availability.

The service matrix needs no change: a drained node has no tasks, so
`_node_cell()` already renders an empty cell rather than a misleading ✅.

## Change 2 — `infra-uis` pseudo stack

### Configuration

A new option `docker.infra_ui_services` — a list of case-insensitive
substrings, exactly like `docker.infrastructure_stacks`:

```python
DEFAULT_INFRA_UI_SERVICES = [
    "kafbat-ui", "kafka-ui", "kafdrop",
    "cloudbeaver", "pgadmin", "adminer",
    "mongo-express", "mongo-gui",
    "rustfs-console", "rustfs-ui", "s3-browser", "s3browser",
    "redisinsight", "redis-commander",
    "portainer", "dozzle", "kibana",
]
```

`portainer` stays in `DEFAULT_INFRASTRUCTURE_STACKS` *and* appears in the new
list. On a name matching both lists, `infra_ui_services` wins — Portainer *is*
a UI, so it renders as an `infra-uis` sub-row. This precedence rule holds for
every overlap, not just Portainer. Users who dislike the grouping override the
list in their TOML.

### Grouping

In `_stack_columns()`, before the Infrastruktur/Service split, every service is
tested against `infra_ui_services`. The match runs against **both** the stack
name and the service base name (after the per-node suffix has been stripped),
so all three real-world shapes are captured:

- a standalone container named `mongo-express`,
- a stack of its own named `cloudbeaver`,
- a service `kafbat-ui` living inside a larger stack such as `kafka`.

Matched services are removed from their origin group and collected as sub-rows
of a synthetic stack entry named `infra-uis`, which is placed **first** in the
*Infrastruktur* block rather than sorted alphabetically. Sub-row labels use the
same base-name logic as elsewhere (node suffix and stack prefix stripped), so
`kafka_kafbat-ui-<node>` shows as `kafbat-ui`.

If nothing matches, no `infra-uis` row is emitted at all.

The entry always renders in the "stack header plus one sub-row per service"
form of `_stack_matrix()`, even when only a single UI matches — collapsing it
to one row labelled `infra-uis` would hide *which* UI is running. Real stacks
keep their existing single-service collapse.

Example:

```
Infrastruktur    ccc-01  ccn-01  ccn-02   Description
infra-uis
  cloudbeaver      ✅
  kafbat-ui                ✅
  mongo-express    ✅
  rustfs-console           ✅
kafka              ✅      ✅      ✅
PostgreSQL-18      ✅
```

## Error handling

Unchanged in character: the collector remains exception-safe and returns
`SwarmInfo(reachable=False)` on any failure; a missing `Spec.Availability` key
yields `None`, which the renderer treats as active. Config loading keeps its
"never raise, fall back to defaults" contract; an absent
`docker.infra_ui_services` key yields the default list.

## Testing

Test-first, one behaviour per test.

`tests/test_collectors_docker.py`
: `_FakeNode` gains an `availability` argument. New test: a node with
  `state="ready", availability="drain"` yields `reachable is True`,
  `availability == "drain"`, `operational is False`.

`tests/test_model.py`
: `operational` truth table — active/None → True, drain/pause → False,
  unreachable → False regardless of availability.

`tests/test_render_panels.py`
: (a) a drained node renders `⚠️` and `drain` and **not** `✅` on its line;
  (b) the summary line contains `(1 drain, 1 down)`;
  (c) UI services appear under an `infra-uis` row inside the *Infrastruktur*
  block and no longer in *Service* / *Container (ohne Stack)*;
  (d) with no matching services, `infra-uis` does not appear.

`tests/test_config.py`
: `infra_ui_services` is read from TOML and falls back to the default list.

## Documentation

`README.md`: extend the DOCKER INFOS feature description (three node states,
`infra-uis` grouping), add `docker.infra_ui_services` to the configuration
table, and include it in the full example.

## Out of scope

- Reacting to availability anywhere outside the DOCKER INFOS section.
- Changing how `critical_services` are (not) visualised.
- Any hierarchy beyond the single `infra-uis` grouping level.
