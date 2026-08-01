# Service Verdict Column & Flowing Cluster Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every DOCKER INFOS row a `Working` verdict (icon plus running/desired count), and lay the CLUSTER HEALTH blocks out in flowing columns instead of one tall list.

**Architecture:** A pure verdict function turns a row's services — plus, when the health section ran, that service's cluster verdict — into one cell. The two sections are joined by `kind_for_service`, which reuses the container-name patterns `clusters.py` already owns, so the identifier lives in one place. Nothing about measurement changes; this is presentation.

**Tech Stack:** Python 3.11+, `rich` (`Table.grid`, `Columns`), `pytest`.

## Global Constraints

- **The panel never claims something it did not measure.** A gap in coverage renders neither as a clean bill of health nor as a definite failure. This is the rule the whole health section was built around and it governs every choice below.
- **Icon vocabulary** (single source after Task 1): `✅` measured healthy · `⚠️` degraded · `💀` measured broken · `·` not observable · `…` out of budget · `✗` check failed.
- **`desired == 0` is not a failure.** A service scaled to zero is a decision. It renders `·`, never `💀`.
- **Without a cluster verdict, a clustered service renders `·`, never a replica-derived `✅`.** "Five brokers are running" is not the claim the column makes.
- The cell always carries the count, even when the icon is `·` — the count is a fact either way.
- Imports point from `render` to `collectors`, never the reverse.
- Python 3.11+, line length 100, ruff `select = ["E", "F", "I"]`.
- Code, comments and identifiers in English; user-facing panel strings in English.
- Run tests with `.venv/bin/python -m pytest`; lint with `uvx ruff@0.16 check src tests`.
- Baseline: 234 tests pass on `main`. Any failure you did not cause is a finding, not background noise.

## File Structure

| File | Responsibility |
|---|---|
| `src/terminal_status_panel/render/icons.py` | **Create.** The six status glyphs, imported by both renderers. Replaces two divergent copies. |
| `src/terminal_status_panel/render/verdict.py` | **Create.** One pure function: services (+ optional cluster verdict) → the `Working` cell. No Rich layout, no I/O. |
| `src/terminal_status_panel/collectors/clusters.py` | **Modify.** Add `kind_for_service`, the join key between the two sections. |
| `src/terminal_status_panel/render/panels.py` | **Modify.** Import icons; thread a verdict callable through `_stack_columns` → `_stack_matrix`; `services_section` takes `health`. |
| `src/terminal_status_panel/render/layout.py` | **Modify.** Pass `data.health` into `docker_section`. |
| `src/terminal_status_panel/render/health.py` | **Modify.** Import icons; lay the cluster blocks out with `rich.columns.Columns`; collapse not-applicable services to one line. |
| `README.md` | **Modify.** Document the column and the two sources behind it. |

---

### Task 1: One source for the icon vocabulary

**Files:**
- Create: `src/terminal_status_panel/render/icons.py`
- Modify: `src/terminal_status_panel/render/panels.py:29-31`, `src/terminal_status_panel/render/health.py:30-35`
- Test: `tests/test_render_icons.py`

**Interfaces:**
- Produces: `OK`, `WARN`, `DEAD`, `UNKNOWN`, `TRUNCATED`, `FAILED` in `render/icons.py`. Tasks 3–5 import from here.

`panels.py` defines `_OK`/`_WARN`/`_DEAD`; `health.py` separately defines `OK`/`WARN`/`DEAD`/`UNKNOWN`/`TRUNCATED`/`FAILED`. Task 3 needs all six. A third copy is how the container-name patterns silently diverged once already, so consolidate first.

Keep the private aliases in `panels.py` (`_OK = OK`) so the rest of that module is untouched — this task changes where the glyphs come from, nothing else.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_icons.py
from terminal_status_panel.render import health, icons, panels


def test_every_status_glyph_is_defined_once():
    assert (icons.OK, icons.WARN, icons.DEAD) == ("✅", "⚠️", "💀")
    assert (icons.UNKNOWN, icons.TRUNCATED, icons.FAILED) == ("·", "…", "✗")


def test_both_renderers_use_the_shared_glyphs():
    """A second copy is how the container-name patterns diverged once already."""
    assert (panels._OK, panels._WARN, panels._DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.OK, health.WARN, health.DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.UNKNOWN, health.TRUNCATED, health.FAILED) == (
        icons.UNKNOWN, icons.TRUNCATED, icons.FAILED,
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render_icons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.render.icons'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/render/icons.py
"""The panel's status vocabulary, in one place.

Every glyph means one thing and only one thing. ``…`` and ``✗`` are not
interchangeable: a check that ran out of budget says nothing about the service,
a check that failed says a great deal. ``·`` is reserved for what was not
observable at all.
"""

OK = "✅"        # measured healthy
WARN = "⚠️"      # degraded, but serving
DEAD = "💀"      # measured broken
UNKNOWN = "·"    # not observable
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"     # the check itself failed
```

In `panels.py`, replace the three literals with an import and aliases:

```python
from .icons import DEAD as _DEAD
from .icons import OK as _OK
from .icons import WARN as _WARN
```

In `health.py`, replace the six literals with:

```python
from .icons import DEAD, FAILED, OK, TRUNCATED, UNKNOWN, WARN
```

Keep `health.py`'s existing module docstring table describing what each glyph means.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 236 passed (234 + 2 new). Every existing render test must still pass unchanged — the glyphs are identical, only their origin moved.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/icons.py src/terminal_status_panel/render/panels.py \
        src/terminal_status_panel/render/health.py tests/test_render_icons.py
git commit -m "refactor: give the status glyphs a single home"
```

---

### Task 2: `kind_for_service` — the join key

**Files:**
- Modify: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py` (append)

**Interfaces:**
- Consumes: the existing pattern constants `POSTGRES_PATTERNS`, `MONGODB_PATTERNS`, `KAFKA_PATTERNS`, `RUSTFS_PATTERNS`.
- Produces: `kind_for_service(name: str) -> str | None` — the cluster kind a Docker service name belongs to, or `None`.

Matching on the patterns rather than on `ClusterService.name` is deliberate: those names come from different places per probe — the stack for PostgreSQL, the cluster id for Kafka, the volume for GlusterFS — and would not join reliably. The patterns are the one identifier both sections already share.

GlusterFS has no Docker service (it runs on the host), so it has no patterns and can never match. That is correct, not an omission.

- [ ] **Step 1: Write the failing test**

Service names are the real ones recorded from the production cluster.

```python
# tests/test_collectors_clusters.py  (append)

def test_kind_for_service_matches_the_real_service_names():
    assert clusters.kind_for_service("PostgreSQL-18_pg-lmzvd06-ccc-01") == "postgres"
    assert clusters.kind_for_service("mongodb_lmzvd06-internet-app-1") == "mongodb"
    assert clusters.kind_for_service("kafka_kafka-lmzvd06-ccc-01") == "kafka"
    assert clusters.kind_for_service("rustfs_rustfs-lmzvd06-ccn-01") == "rustfs"


def test_kind_for_service_does_not_match_the_admin_uis():
    """The narrow patterns exist so the UIs cannot be mistaken for members."""
    assert clusters.kind_for_service("kafbat-ui_kafbat-ui") is None
    assert clusters.kind_for_service("cloudbeaver_cloudbeaver") is None
    assert clusters.kind_for_service("s3manager_s3manager") is None


def test_kind_for_service_returns_none_for_ordinary_services():
    assert clusters.kind_for_service("edutap_production_backend") is None
    assert clusters.kind_for_service("traefik_traefik") is None


def test_kind_for_service_is_case_insensitive():
    assert clusters.kind_for_service("POSTGRESQL-18_PG-NODE") == "postgres"


def test_glusterfs_has_no_docker_service_and_never_matches():
    assert clusters.kind_for_service("glusterfs") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collectors_clusters.py -k kind_for_service -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'kind_for_service'`

- [ ] **Step 3: Write the implementation**

Add near the pattern constants in `clusters.py`:

```python
# The join key between DOCKER INFOS and CLUSTER HEALTH. Built from the same
# patterns the probes match containers with, so the identifier lives in exactly
# one place — a second copy is how the crash-loop detection broke once already.
# GlusterFS is absent on purpose: it runs on the host, not as a Docker service.
_KIND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("postgres", POSTGRES_PATTERNS),
    ("mongodb", MONGODB_PATTERNS),
    ("kafka", KAFKA_PATTERNS),
    ("rustfs", RUSTFS_PATTERNS),
)


def kind_for_service(name: str) -> str | None:
    """The cluster kind a Docker service name belongs to, or None."""
    lowered = (name or "").lower()
    for kind, patterns in _KIND_PATTERNS:
        if any(pattern.lower() in lowered for pattern in patterns):
            return kind
    return None
```

Place it after the last pattern constant is defined, so every referenced name exists.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS, including the five new tests.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: map a Docker service name to its cluster kind"
```

---

### Task 3: The verdict function

**Files:**
- Create: `src/terminal_status_panel/render/verdict.py`
- Test: `tests/test_render_verdict.py`

**Interfaces:**
- Consumes: `icons` (Task 1); `ServiceStatus` and `ClusterService` from `model.py`.
- Produces: `service_verdict(services, *, kind, cluster, node_count) -> Text`. Task 4 calls it once per row.

This is the whole decision, in one pure function with no Rich layout and no I/O, so its table of cases can be tested directly rather than through rendered output.

The three-way split:

- `kind is None` — an ordinary service. Replica logic.
- `kind` set, `cluster is None` — a clustered service with no verdict, because the health section did not run or the kind is not enabled. `·` plus the count. **Never a replica-derived `✅`.**
- `kind` set, `cluster` present — the cluster's own verdict decides the icon; the count still comes from Docker. `⚠️ 5/5` is the valuable case: every broker up, quorum degraded anyway.

Counts: sum `running_replicas` across the row's services. For the denominator sum `desired_replicas`, except when every service in the row is global mode (`desired_replicas is None`), where the denominator is `node_count` — a global service wants one task per node.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_verdict.py
import pytest

from terminal_status_panel.model import ClusterService, ServiceStatus
from terminal_status_panel.render import icons
from terminal_status_panel.render.verdict import service_verdict


def _svc(running, desired):
    return ServiceStatus(name="x", running_replicas=running, desired_replicas=desired)


def _cell(services, kind=None, cluster=None, node_count=5):
    return service_verdict(services, kind=kind, cluster=cluster, node_count=node_count).plain


@pytest.mark.parametrize(
    "running,desired,expected",
    [
        (3, 3, f"{icons.OK} 3/3"),      # fully staffed
        (2, 5, f"{icons.WARN} 2/5"),    # serving, degraded
        (0, 3, f"{icons.DEAD} 0/3"),    # wants replicas, has none
        (0, 0, f"{icons.UNKNOWN} 0/0"),  # scaled to zero on purpose
    ],
)
def test_replica_states(running, desired, expected):
    assert _cell([_svc(running, desired)]) == expected


def test_a_row_sums_its_per_node_replicas():
    """One row bundles the per-node replicas of one logical service."""
    assert _cell([_svc(1, 1), _svc(1, 1), _svc(0, 1)]) == f"{icons.WARN} 2/3"


def test_global_mode_counts_against_the_node_count():
    assert _cell([_svc(5, None)], node_count=5) == f"{icons.OK} 5/5"
    assert _cell([_svc(3, None)], node_count=5) == f"{icons.WARN} 3/5"
    assert _cell([_svc(0, None)], node_count=5) == f"{icons.DEAD} 0/5"


def test_running_above_desired_is_not_degraded():
    assert _cell([_svc(4, 3)]) == f"{icons.OK} 4/3"


def test_a_clustered_service_without_a_verdict_is_not_observable():
    """status-docker runs without the health section; five brokers running is
    not the claim this column makes."""
    assert _cell([_svc(5, 5)], kind="kafka") == f"{icons.UNKNOWN} 5/5"


def test_the_cluster_verdict_beats_the_replica_count():
    degraded = ClusterService(kind="kafka", quorum_ok=False)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=degraded) == f"{icons.DEAD} 5/5"


def test_a_healthy_quorum_shows_ok():
    healthy = ClusterService(kind="kafka", quorum_ok=True)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=healthy) == f"{icons.OK} 5/5"


def test_an_unreported_quorum_is_not_observable():
    unreported = ClusterService(kind="kafka", quorum_ok=None)
    assert _cell([_svc(5, 5)], kind="kafka", cluster=unreported) == f"{icons.UNKNOWN} 5/5"


def test_a_failed_probe_shows_the_failure_marker():
    broken = ClusterService(kind="rustfs", error="no running container")
    assert _cell([_svc(0, 1)], kind="rustfs", cluster=broken) == f"{icons.FAILED} 0/1"


def test_not_applicable_here_says_nothing_about_the_service():
    """The probe found no member on THIS node. That is a statement about the
    observer, not about the service, which may run fine elsewhere."""
    elsewhere = ClusterService(kind="rustfs", applicable=False)
    assert _cell([_svc(4, 5)], kind="rustfs", cluster=elsewhere) == f"{icons.UNKNOWN} 4/5"


def test_an_empty_row_renders_nothing_rather_than_a_verdict():
    assert _cell([]) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.render.verdict'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/render/verdict.py
"""Turn a DOCKER INFOS row into one ``Working`` cell.

The cell carries an icon and a count. They can come from different places: for
a clustered service the icon is the cluster's own verdict while the count stays
Docker's, so ``⚠️ 5/5`` reads "every broker is running and the quorum is
degraded anyway" — the case where a replica count on its own lies.

Pure: no Rich layout, no I/O, so the table of cases is testable directly.
"""

from __future__ import annotations

from rich.text import Text

from ..model import ClusterService, ServiceStatus
from . import icons

_STYLES = {
    icons.OK: "",
    icons.WARN: "yellow",
    icons.DEAD: "red",
    icons.FAILED: "red",
    icons.UNKNOWN: "dim",
}


def _counts(services: list[ServiceStatus], node_count: int) -> tuple[int, int]:
    """(running, desired) for a row, which bundles one service's per-node replicas."""
    running = sum(s.running_replicas for s in services)
    # A global-mode service reports no replica count: it wants one task per node.
    if all(s.desired_replicas is None for s in services):
        return running, node_count
    return running, sum(s.desired_replicas or 0 for s in services)


def _replica_icon(running: int, desired: int) -> str:
    if desired == 0:
        # Scaled to zero is a decision, not an outage. Rendering it as broken
        # would train people to ignore this column.
        return icons.UNKNOWN
    if running == 0:
        return icons.DEAD
    if running < desired:
        return icons.WARN
    return icons.OK


def _cluster_icon(cluster: ClusterService) -> str:
    if cluster.error:
        return icons.FAILED
    if not cluster.applicable:
        # The probe found no member on this node — a statement about the
        # observer, not about the service, which may run fine elsewhere.
        return icons.UNKNOWN
    if cluster.quorum_ok is None:
        return icons.UNKNOWN
    return icons.OK if cluster.quorum_ok else icons.DEAD


def service_verdict(
    services: list[ServiceStatus],
    *,
    kind: str | None = None,
    cluster: ClusterService | None = None,
    node_count: int = 0,
) -> Text:
    """The ``Working`` cell for one row: an icon and a running/desired count."""
    if not services:
        return Text("")
    running, desired = _counts(services, node_count)
    if kind is None:
        icon = _replica_icon(running, desired)
    elif cluster is None:
        # A clustered service with no verdict: the health section did not run,
        # or this kind is not enabled. "Five brokers are running" is not the
        # claim this column makes, so it stays unobserved.
        icon = icons.UNKNOWN
    else:
        icon = _cluster_icon(cluster)
    return Text(f"{icon} {running}/{desired}", style=_STYLES.get(icon, ""))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_render_verdict.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/verdict.py tests/test_render_verdict.py
git commit -m "feat: decide the Working verdict for a service row"
```

---

### Task 4: Put the column in the matrix

**Files:**
- Modify: `src/terminal_status_panel/render/panels.py` (`_stack_matrix`, `_stack_columns`, `services_section`)
- Modify: `src/terminal_status_panel/render/layout.py:50-52`
- Test: `tests/test_render_panels.py` (append)

**Interfaces:**
- Consumes: `service_verdict` (Task 3), `kind_for_service` (Task 2), `HealthInfo` from `model.py`.
- Produces: `services_section(swarm, cfg, health=None)`; `layout.docker_section` passes `data.health`.

`_stack_matrix` currently builds a `Table.grid` with a name column, one column per node, and a description column. Add the verdict column **immediately after the name**: the row's verdict first, then the detail of where it runs.

Header label: `Working`.

Pass the verdict in as a callable so `_stack_matrix` stays a layout function that knows nothing about clusters or health:

```python
def _stack_matrix(title, entries, nodes, verdict) -> RenderableType:
```

where `verdict` is `Callable[[list[ServiceStatus]], Text]`. `_stack_columns` builds that closure once, from `health` and the node count.

Stack header rows keep an empty verdict cell — their sub-rows sit directly beneath with their own verdicts, and an aggregate would repeat what is on screen a line later. Note the existing header and placeholder rows pad with `[""] * (len(short) + 1)`; every such count grows by one.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_panels.py  (append)
from terminal_status_panel.model import ClusterService, HealthInfo
from terminal_status_panel.render import icons


def test_the_matrix_has_a_working_column():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("app_web", 3, 3, stack="app",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "Working" in out
    assert f"{icons.OK} 3/3" in out


def test_a_service_wanting_replicas_and_having_none_is_marked_dead():
    """Nine such rows render blank today — the outage is invisible."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("edutap_admin_backend", 0, 3, stack="edutap")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.DEAD} 0/3" in out


def test_a_service_scaled_to_zero_is_not_marked_dead():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("app_paused", 0, 0, stack="app")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.UNKNOWN} 0/0" in out
    assert f"{icons.DEAD} 0/0" not in out


def test_the_kafka_row_follows_the_cluster_verdict_not_the_replicas():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka-srv-01", 5, 5, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    health = HealthInfo(clusters_probed=True,
                        clusters=[ClusterService(kind="kafka", quorum_ok=False)])
    out = _text(panels.services_section(swarm, Config(), health), width=170)
    assert f"{icons.DEAD} 5/5" in out


def test_without_health_a_clustered_service_is_not_observable():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka-srv-01", 5, 5, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.UNKNOWN} 5/5" in out
    assert f"{icons.OK} 5/5" not in out
```

```python
# tests/test_render_layout.py  (append)
def test_docker_section_receives_the_health_data(monkeypatch):
    """The Kafka verdict lives in the health section; the matrix must see it."""
    seen = {}

    def fake(swarm, cfg, health=None):
        seen["health"] = health
        return Text("")

    monkeypatch.setattr(layout, "services_section", fake)
    health = HealthInfo(clusters_probed=True)
    layout.docker_section(PanelData(swarm=SwarmInfo(reachable=True), health=health), Config())
    assert seen["health"] is health
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_panels.py tests/test_render_layout.py -v`
Expected: FAIL — `TypeError: services_section() takes 2 positional arguments but 3 were given`, and `assert "Working" in out`.

- [ ] **Step 3: Write the implementation**

In `panels.py`, import what the closure needs:

```python
from ..collectors.clusters import kind_for_service
from ..model import ClusterService, HealthInfo
from .verdict import service_verdict
```

Give `_stack_matrix` the extra column:

```python
def _stack_matrix(title, entries, nodes, verdict) -> RenderableType:
    short = _short_node_names(nodes)
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")          # stack / service name
    table.add_column(justify="left")        # Working
    for _ in short:
        table.add_column(justify="center")  # per-node status
    table.add_column(style="dim")           # description

    header = [_subhead(title), Text("Working", style="cyan")]
    header += [Text(s, style="cyan") for _, s in short]
    header.append(Text("Description", style="cyan"))
    table.add_row(*header)

    if not entries:
        table.add_row(Text("—", style="dim"), *[""] * (len(short) + 2))

    def _row(label, services, desc):
        cells = [label, verdict(services)]
        cells += [_node_cell(services, full) for full, _ in short]
        cells.append(Text(desc or ""))
        table.add_row(*cells)
```

The stack-header row inside the same function grows by one empty cell:

```python
            table.add_row(Text(stack_name, style="bold cyan"), *[""] * (len(short) + 2))
```

In `_stack_columns`, build the closure and hand it to every `_stack_matrix` call:

```python
def _stack_columns(swarm: SwarmInfo, cfg: Config,
                   health: HealthInfo | None = None) -> RenderableType:
    ...
    by_kind: dict[str, ClusterService] = {
        service.kind: service for service in (health.clusters if health else [])
    }
    node_count = len(swarm.nodes)

    def verdict(services):
        kind = next(
            (k for k in (kind_for_service(s.name) for s in services) if k), None
        )
        return service_verdict(
            services, kind=kind, cluster=by_kind.get(kind) if kind else None,
            node_count=node_count,
        )
```

Thread `health` through `services_section`:

```python
def services_section(swarm: SwarmInfo | None, cfg: Config,
                     health: HealthInfo | None = None) -> Group:
```

and pass it to both `_stack_columns(swarm, cfg, health)` call sites in that function.

In `layout.py`:

```python
def docker_section(data: PanelData, cfg: Config) -> RenderableType:
    """The DOCKER INFOS block. The health data, when the section ran, supplies
    the cluster verdicts the replica counts cannot give."""
    return services_section(data.swarm, cfg, data.health)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Existing `test_render_panels.py` tests that assert on column layout may need their expected width adjusted — if one fails, read it before changing it: a genuine layout regression and a stale expectation look alike, and only one of them may be edited.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/panels.py src/terminal_status_panel/render/layout.py \
        tests/test_render_panels.py tests/test_render_layout.py
git commit -m "feat: show a Working verdict for every service row"
```

---

### Task 5: Flowing cluster blocks

**Files:**
- Modify: `src/terminal_status_panel/render/health.py` (`_clusters_body`)
- Test: `tests/test_render_health.py` (append)

**Interfaces:**
- Consumes: `icons` (Task 1), the existing `_service_lines` and `_KIND_TITLES`.
- Produces: no new public names.

Today `_clusters_body` returns a `Group` of blocks stacked vertically — roughly 55 lines tall on a 5-node cluster while two thirds of the width stay empty. Arrange the blocks with `rich.columns.Columns`, which flows them into as many columns as the width allows and degrades to one on a narrow terminal.

Services that are **not applicable** collapse into one dim line below the blocks: `n/a here: MongoDB`. A one-line "n/a" does not deserve a column of its own — that would spend exactly the space this change reclaims.

Services that **failed** or **ran out of budget** stay full blocks in the flow: they carry a message, and a message belongs where the eye is.

The branch order at the top of the function — truncated, then `clusters_probed`, then empty — does not change.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_health.py  (append)

def _cluster(kind, name, members=2):
    return ClusterService(
        kind=kind, name=name, reachable=True, quorum_ok=True,
        members=[ClusterMember(name=f"{kind}-{i}", role="peer", healthy=True)
                 for i in range(members)],
    )


def test_wide_terminals_put_cluster_blocks_side_by_side():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        _cluster("kafka", "cluster-id"),
        _cluster("glusterfs", "shared"),
    ])
    out = _render(health, width=150)
    side_by_side = [ln for ln in out.splitlines()
                    if "PostgreSQL" in ln and "Kafka" in ln]
    assert side_by_side, "at 150 columns two clusters should share a line"


def test_narrow_terminals_stack_the_blocks():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        _cluster("kafka", "cluster-id"),
    ])
    out = _render(health, width=60)
    assert not [ln for ln in out.splitlines()
                if "PostgreSQL" in ln and "Kafka" in ln]


def test_not_applicable_services_collapse_to_one_line():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        ClusterService(kind="mongodb", applicable=False),
        ClusterService(kind="rustfs", applicable=False),
    ])
    out = _render(health, width=150)
    assert "n/a here:" in out
    assert "MongoDB" in out
    assert "RustFS" in out
    # One shared line, not one block each.
    assert len([ln for ln in out.splitlines() if "n/a here:" in ln]) == 1


def test_an_all_not_applicable_panel_is_just_the_summary_line():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(kind="mongodb", applicable=False),
    ])
    out = _render(health, width=150)
    assert "n/a here: MongoDB" in out


def test_a_failed_service_keeps_its_own_block():
    health = HealthInfo(clusters_probed=True, clusters=[
        _cluster("postgres", "PostgreSQL-18"),
        ClusterService(kind="rustfs", error="no running container"),
    ])
    out = _render(health, width=150)
    assert "no running container" in out
    assert "n/a here" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_health.py -k "side_by_side or collapse or summary_line or own_block or stack_the_blocks" -v`
Expected: FAIL — no line carries two cluster names, and `n/a here:` does not appear.

- [ ] **Step 3: Write the implementation**

Add the import at the top of `health.py`:

```python
from rich.columns import Columns
```

Rewrite the body-building half of `_clusters_body`, leaving its opening branches untouched:

```python
    applicable = [s for s in health.clusters if s.applicable]
    not_applicable = [s for s in health.clusters if not s.applicable]

    blocks: list[RenderableType] = [_service_lines(service) for service in applicable]
    # Each kind is its own budget task, so one kind running out of time says
    # nothing about the kinds beside it — and they keep their own blocks.
    blocks.extend(
        Text(f"{TRUNCATED} {_KIND_TITLES.get(kind, kind)}: time budget exceeded", style="dim")
        for kind in truncated
    )

    # Columns flows the blocks into as many columns as the width allows and
    # falls back to one on a narrow terminal.
    parts: list[RenderableType] = [Columns(blocks, padding=(0, 4), expand=False)]

    if not_applicable:
        # A one-line "n/a" does not deserve a column of its own.
        names = ", ".join(_KIND_TITLES.get(s.kind, s.kind) for s in not_applicable)
        parts.append(Text(f"n/a here: {names}", style="dim"))

    return Group(*parts)
```

Note `_service_lines` already renders a not-applicable service as `<Title>: n/a here`; that branch stays for any caller that passes one directly, but `_clusters_body` no longer reaches it.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Existing health render tests that asserted on the old vertical layout may need updating — read each before changing it, and change only expectations about *arrangement*, never about which icon or message appears.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/health.py tests/test_render_health.py
git commit -m "feat: flow the cluster blocks into columns"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Document the `Working` column**

In the DOCKER INFOS description, add the column: it sits after the service name and carries an icon and a running/desired task count, `✅ 3/3` · `⚠️ 2/5` · `💀 0/3` · `· 0/0`.

State the three rules plainly:

- `desired == 0` renders `·`, not `💀` — a service scaled to zero is a decision, not an outage.
- For the four clustered services that run as Docker services (GlusterFS is queried on the host, so it never gets a DOCKER INFOS row) the **icon** comes from the cluster's own verdict while the **count** stays Docker's. RustFS at `3/5 live` therefore renders `⚠️ 5/5` — a minority of members measured unhealthy while the majority quorum still holds and every container is up — which is the case a replica count alone gets wrong. A row measured `💀`/`⚠️` by its own replicas keeps that icon regardless: a cluster-level `✅` or `·` never talks it back up.
- Under `status-docker`, which runs without the health section, a clustered service shows `·` plus its count rather than a replica-derived `✅`. "Five brokers are running" is not the claim the column makes. The same applies when the probe found no member on this node, or when the kind is not in `health.enabled`.

- [ ] **Step 2: Note the cluster layout**

In the CLUSTER HEALTH section, mention that the cluster blocks flow into as many columns as the terminal width allows, and that services with no member on this node are summarised on one `n/a here:` line rather than each taking a block.

- [ ] **Step 3: Check the icon table covers the column**

The README's existing icon vocabulary table already defines all six glyphs. Verify the `Working` column's use of `·` and `✗` is consistent with what that table says, and cross-reference rather than restating it.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the Working column and the cluster layout"
```

---

## Self-Review

**Spec coverage.** Column placement, the icon-plus-count cell, the cluster-verdict precedence table including `applicable is False`, the replica table including `desired == 0` and global mode, the honest `·` gap under `status-docker`, the `kind_for_service` join, empty stack-header cells → Tasks 2–4. Flowing columns and the collapsed `n/a here:` line → Task 5. README → Task 6. The icon consolidation in Task 1 is not in the spec: it exists because Task 3 needs all six glyphs and a third copy of them is the failure mode the spec's own join-key rationale warns about.

**Type consistency.** `service_verdict(services, *, kind, cluster, node_count) -> Text` is defined in Task 3 and called with exactly those keywords in Task 4. `kind_for_service(name) -> str | None` is defined in Task 2 and its result feeds `kind=` in Task 4. `services_section(swarm, cfg, health=None)` is defined in Task 4 and called positionally by `layout.docker_section` in the same task. The icon names from Task 1 are used unqualified in Tasks 3–5.

**One risk worth naming.** Task 4 adds a column to a table several existing tests render and assert on by position or width. The plan tells the implementer to read a failing test before editing it, because a genuine layout regression and a stale expectation look identical from the failure message — and only one of them may be edited. The same warning applies to Task 5's rearrangement of the health blocks.
