# Node Availability & `infra-uis` Pseudo Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop rendering drained/paused Swarm nodes as healthy, and group the
administrative web UIs of infrastructure services into a single `infra-uis`
pseudo stack inside the *Infrastruktur* block.

**Architecture:** Three layers, each touched once per feature. The collector
(`collectors/docker.py`) reads one additional Swarm API field. The data model
(`model.py`) carries it and derives an `operational` property. The renderer
(`render/panels.py`) gains a third node-health branch, a capacity note in the
Swarm summary, and one grouping pass that pulls UI services out of their stacks.
Configuration (`config.py`) gains one substring list, mirroring the existing
`infrastructure_stacks` option.

**Tech Stack:** Python 3.11+, `rich` (rendering), `docker` SDK (Swarm API),
`pytest`, `ruff`, `uv` for the environment.

**Spec:** `docs/superpowers/specs/2026-07-27-node-availability-and-infra-uis-design.md`

## Global Constraints

- Target Python: `>=3.11`; ruff `target-version = "py311"`, `line-length = 100`,
  lint rules `["E", "F", "I"]`. Keep every new line ≤ 100 characters.
- Source code, comments, identifiers, docstrings, documentation and commit
  messages are **English**. (The repository is a public PyPI package; its
  existing docs and history are English.)
- Conventional Commits. Work happens on branch
  `feature/node-availability-and-infra-uis` — never commit to `main`.
- Never `git push` without asking the user first.
- The panel must never raise: collectors stay exception-safe, `load_config()`
  never raises, and the CLI always exits 0.
- Docker data comes from the Docker API only — no database or broker protocol.
- CI parity: `ruff check src tests` and `python -m pytest` must both pass.
- Emoji markers live as module constants in `render/panels.py`
  (`_OK = "✅"`, `_DEAD = "💀"`, new `_WARN = "⚠️"`).

---

### Task 1: `SwarmNode.availability` and `operational`

Adds the data-model foundation for change 1, plus the local dev environment
(the repository has no `.venv` yet).

**Files:**
- Modify: `src/terminal_status_panel/model.py:76-82`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SwarmNode(name, reachable=False, role=None, leader=False,
  state=None, availability=None)` with the read-only property
  `SwarmNode.operational -> bool`.

- [ ] **Step 1: Create the environment**

```bash
uv venv
uv pip install -U -e ".[test,dev]"
```

- [ ] **Step 2: Verify the existing suite is green before changing anything**

Run: `uv run python -m pytest`
Expected: PASS (all tests). If anything fails here, stop and report — it is a
pre-existing problem, not something this plan introduced.

- [ ] **Step 3: Write the failing test**

Add `SwarmNode` to the existing import block at the top of
`tests/test_model.py` (keep the names alphabetically sorted — ruff rule `I`),
then append:

```python
def test_swarm_node_operational_requires_ready_and_active():
    # No availability reported (older daemons) counts as active.
    assert SwarmNode("n1", reachable=True).operational is True
    assert SwarmNode("n1", reachable=True, availability="active").operational is True
    # Ready, but administratively withdrawn -> not usable.
    assert SwarmNode("n1", reachable=True, availability="drain").operational is False
    assert SwarmNode("n1", reachable=True, availability="pause").operational is False
    # Unreachable is never operational, whatever the availability says.
    assert SwarmNode("n1", reachable=False, availability="active").operational is False
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_model.py::test_swarm_node_operational_requires_ready_and_active -v`
Expected: FAIL with `TypeError: SwarmNode.__init__() got an unexpected keyword
argument 'availability'`.

- [ ] **Step 5: Write the implementation**

In `src/terminal_status_panel/model.py`, replace the `SwarmNode` dataclass:

```python
@dataclass
class SwarmNode:
    name: str
    reachable: bool = False
    role: str | None = None  # manager / worker
    leader: bool = False
    state: str | None = None  # raw node state (ready / down / ...)
    availability: str | None = None  # active / pause / drain (Spec.Availability)

    @property
    def operational(self) -> bool:
        """Ready *and* accepting tasks.

        A drained or paused node still reports ``ready`` — it talks to the
        managers but runs no tasks, so it must not be shown as healthy.
        ``None`` means the daemon did not report availability; treat that as
        active so behaviour is unchanged against older daemons.
        """
        return self.reachable and self.availability in (None, "active")
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_model.py -v`
Expected: PASS.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/terminal_status_panel/model.py tests/test_model.py
git commit -m "feat: carry Swarm node availability and derive operational state"
```

---

### Task 2: Collector reads `Spec.Availability`

**Files:**
- Modify: `src/terminal_status_panel/collectors/docker.py:29-45`
- Test: `tests/test_collectors_docker.py:30-40` (extend `_FakeNode`), plus two
  new tests

**Interfaces:**
- Consumes: `SwarmNode(..., availability=...)` and `SwarmNode.operational` from
  Task 1.
- Produces: `collect_docker()` returns `SwarmInfo` whose `nodes` carry
  `availability` straight from the Swarm API (`None` when absent).

- [ ] **Step 1: Extend the test fake and write the failing tests**

In `tests/test_collectors_docker.py`, replace the whole `_FakeNode` class:

```python
class _FakeNode:
    def __init__(self, node_id, hostname, state="ready", role="worker", leader=False,
                 availability="active"):
        self.id = node_id
        self.attrs = {
            "ID": node_id,
            "Description": {"Hostname": hostname},
            "Status": {"State": state},
            "Spec": {"Role": role, "Availability": availability},
        }
        if leader:
            self.attrs["ManagerStatus"] = {"Leader": True}
```

Then append two tests at the end of the file:

```python
def test_drained_node_is_ready_but_not_operational(monkeypatch):
    client = _FakeClient("active", nodes=[
        _FakeNode("n1", "srv-01"),
        _FakeNode("n2", "srv-02", availability="drain"),
    ])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    active, drained = docker_collector.collect_docker().nodes

    assert active.availability == "active" and active.operational is True
    # Drained nodes still report 'ready' — that must not read as healthy.
    assert drained.reachable is True
    assert drained.state == "ready"
    assert drained.availability == "drain"
    assert drained.operational is False


def test_missing_availability_field_stays_operational(monkeypatch):
    node = _FakeNode("n1", "srv-01")
    del node.attrs["Spec"]["Availability"]
    client = _FakeClient("active", nodes=[node])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()

    assert result.nodes[0].availability is None
    assert result.nodes[0].operational is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_collectors_docker.py -v`
Expected: `test_drained_node_is_ready_but_not_operational` FAILs on
`assert active.availability == "active"` (it is `None`, because the collector
never reads the field). The other tests pass.

- [ ] **Step 3: Write the implementation**

In `src/terminal_status_panel/collectors/docker.py`, inside `_node_map()`,
replace the body of the `for node in raw_nodes:` loop from the
`manager = ...` line down to the closing `)` of `nodes.append(...)`:

```python
        manager = attrs.get("ManagerStatus") or {}
        state = attrs.get("Status", {}).get("State")
        spec = attrs.get("Spec", {})
        nodes.append(
            SwarmNode(
                name=name,
                reachable=state == "ready",
                role=spec.get("Role"),
                leader=bool(manager.get("Leader", False)),
                state=state,
                # active / pause / drain — a drained node is ready but idle.
                availability=spec.get("Availability"),
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_collectors_docker.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/terminal_status_panel/collectors/docker.py tests/test_collectors_docker.py
git commit -m "feat: read Spec.Availability for Swarm nodes"
```

---

### Task 3: Render drained nodes as a warning, not as healthy

Covers the visible half of change 1, including its README update.

**Files:**
- Modify: `src/terminal_status_panel/render/panels.py:28-31` (marker constants),
  `:256-259` (`_node_health`), `:293-307` (`_swarm_body`)
- Modify: `README.md:11-17` (feature description)
- Test: `tests/test_render_panels.py`

**Interfaces:**
- Consumes: `SwarmNode.operational`, `SwarmNode.availability` from Task 1.
- Produces: `_node_health(node) -> Text` with three outcomes, and a private
  helper `_node_capacity(nodes) -> Text | None` used by `_swarm_body()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render_panels.py`:

```python
def _mixed_availability_swarm() -> SwarmInfo:
    """One active, one drained, one down node — no services."""
    return SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[
            SwarmNode("srv-01", reachable=True, role="manager", leader=True,
                      state="ready", availability="active"),
            SwarmNode("srv-02", reachable=True, role="worker",
                      state="ready", availability="drain"),
            SwarmNode("srv-03", reachable=False, role="worker",
                      state="down", availability="active"),
        ],
    )


def test_drained_node_is_not_rendered_as_healthy():
    out = _text(panels.services_section(_mixed_availability_swarm(), Config()), width=170)
    nodes_line = next(line for line in out.splitlines() if "srv-02" in line)
    assert "⚠" in nodes_line and "drain" in nodes_line
    assert "srv-02 ✅" not in nodes_line
    # The healthy and the dead node keep their existing markers.
    assert "srv-01 ✅" in nodes_line
    assert "💀" in nodes_line and "down" in nodes_line


def test_swarm_summary_counts_unavailable_nodes():
    out = _text(panels.services_section(_mixed_availability_swarm(), Config()), width=170)
    assert "3 nodes (1 drain, 1 down)" in out


def test_swarm_summary_omits_capacity_note_when_all_nodes_are_active():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=2,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active"),
               SwarmNode("srv-02", reachable=True, state="ready", availability="active")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "2 nodes  ·" in out
    assert "drain" not in out and "down" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_render_panels.py -v -k "drained or summary"`
Expected: `test_drained_node_is_not_rendered_as_healthy` FAILs (the line reads
`srv-02 ✅`), `test_swarm_summary_counts_unavailable_nodes` FAILs (no
parenthetical), the third test passes already.

- [ ] **Step 3: Add the warning marker constant**

In `src/terminal_status_panel/render/panels.py`, extend the marker block:

```python
# Emoji status markers — readable regardless of color perception.
_OK = "✅"
_WARN = "⚠️"
_DEAD = "💀"
```

- [ ] **Step 4: Implement the three-state node health**

Replace `_node_health()`:

```python
def _node_health(node) -> Text:
    """✅ ready and active · ⚠️ ready but drained/paused · 💀 unreachable."""
    if not node.reachable:
        return Text(f"{_DEAD} {node.state or 'down'}", style="red")
    if not node.operational:
        return Text(f"{_WARN} {node.availability}", style="yellow")
    return Text(_OK)
```

- [ ] **Step 5: Implement the capacity note**

Insert this helper directly above `_swarm_body()`:

```python
def _node_capacity(nodes) -> Text | None:
    """A ' (1 drain, 1 down)' note, or None when every node is operational.

    Unreachable nodes count as 'down' only — their availability is moot."""
    withdrawn: dict[str, int] = {}
    down = 0
    for node in nodes:
        if not node.reachable:
            down += 1
        elif not node.operational:
            key = node.availability or "unavailable"
            withdrawn[key] = withdrawn.get(key, 0) + 1
    if not withdrawn and not down:
        return None

    parts = [(f"{count} {name}", "yellow") for name, count in sorted(withdrawn.items())]
    if down:
        parts.append((f"{down} down", "red"))
    note = Text(" (")
    for index, (label, style) in enumerate(parts):
        if index:
            note.append(", ")
        note.append(label, style=style)
    note.append(")")
    return note
```

- [ ] **Step 6: Use it in the summary line**

In `_swarm_body()`, replace the `table.add_row("Swarm", Text.assemble(...))`
call (and only that call) with:

```python
    summary = Text()
    summary.append("active", style="green")
    summary.append(f"  ·  {role}  ·  {n_nodes} nodes")
    capacity = _node_capacity(swarm.nodes)
    if capacity is not None:
        summary.append_text(capacity)
    summary.append(f"  ·  {len(swarm.services)} services  ·  {n_stacks} stacks")
    table.add_row("Swarm", summary)
```

- [ ] **Step 7: Run the whole suite**

Run: `uv run python -m pytest`
Expected: PASS. `test_services_section_merges_per_node_replicas` still passes —
it only asserts on presence, and its down node now additionally produces
`(1 down)` in the summary.

- [ ] **Step 8: Eyeball the rendering**

Run:

```bash
uv run python -c "
from rich.console import Console
from terminal_status_panel.config import Config
from terminal_status_panel.model import SwarmInfo, SwarmNode
from terminal_status_panel.render import panels
swarm = SwarmInfo(reachable=True, enabled=True, node_role='manager', node_count=3,
                  nodes=[SwarmNode('srv-01', reachable=True, role='manager', leader=True,
                                   state='ready', availability='active'),
                         SwarmNode('srv-02', reachable=True, state='ready',
                                   availability='drain'),
                         SwarmNode('srv-03', reachable=False, state='down')])
Console(width=170).print(panels.services_section(swarm, Config()))
"
```

Expected: the Nodes line shows `srv-01 ✅ (leader)   srv-02 ⚠️ drain   srv-03 💀 down`
and the Swarm line shows `3 nodes (1 drain, 1 down)`. Emoji column alignment
may differ by one cell between terminals — that is cosmetic, not a failure.

- [ ] **Step 9: Update the README feature description**

In `README.md`, replace the `- **DOCKER INFOS** …` bullet (lines 11-17) with:

```markdown
- **DOCKER INFOS** — Swarm key facts (summary + node health) above three
  stacked node matrices: *Infrastruktur*, *Service*, and standalone *Container*.
  Node health has three states: ✅ ready and active, ⚠️ ready but drained or
  paused (it accepts no tasks), 💀 unreachable; the summary line counts the
  non-operational ones, e.g. `5 nodes (1 drain, 1 down)`.
  Per-node replicas of the same service (e.g. `kafka_kafka-<node>` on every
  node) collapse into a single row; a stack with one logical service shows as
  one row named after the stack, a stack with several shows a header plus one
  sub-row per service (stack prefix and node name stripped). Columns are the
  nodes (alphabetical) with ✅ / 💀 placement, plus a description column.
```

- [ ] **Step 10: Lint and commit**

```bash
uv run ruff check src tests
git add src/terminal_status_panel/render/panels.py tests/test_render_panels.py README.md
git commit -m "feat: render drained and paused Swarm nodes as a warning, not healthy"
```

---

### Task 4: `docker.infra_ui_services` configuration

**Files:**
- Modify: `src/terminal_status_panel/config.py:23-39` (defaults + dataclass),
  `:73-84` (`load_config`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `terminal_status_panel.config.DEFAULT_INFRA_UI_SERVICES: list[str]`
  and `Config.infra_ui_services: list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_infra_ui_services_default(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert "kafbat-ui" in cfg.infra_ui_services
    assert "cloudbeaver" in cfg.infra_ui_services
    assert "mongo-express" in cfg.infra_ui_services


def test_infra_ui_services_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[docker]\ninfra_ui_services = ["cloudbeaver", "my-own-ui"]\n')
    cfg = load_config(path)
    assert cfg.infra_ui_services == ["cloudbeaver", "my-own-ui"]
    # An unrelated option keeps its default.
    assert cfg.docker_timeout == 1.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_config.py -v`
Expected: both new tests FAIL with
`AttributeError: 'Config' object has no attribute 'infra_ui_services'`.

- [ ] **Step 3: Add the default list**

In `src/terminal_status_panel/config.py`, below `DEFAULT_INFRASTRUCTURE_STACKS`:

```python
# Admin web UIs for infrastructure services. Matched case-insensitively against
# the stack name *and* the service name; matches are grouped into the
# "infra-uis" pseudo stack and win over DEFAULT_INFRASTRUCTURE_STACKS.
DEFAULT_INFRA_UI_SERVICES = [
    "kafbat-ui", "kafka-ui", "kafdrop",
    "cloudbeaver", "pgadmin", "adminer",
    "mongo-express", "mongo-gui",
    "rustfs-console", "rustfs-ui", "s3-browser", "s3browser",
    "redisinsight", "redis-commander",
    "portainer", "dozzle", "kibana",
]
```

- [ ] **Step 4: Add the config field**

In the `Config` dataclass, directly after `infrastructure_stacks`:

```python
    infra_ui_services: list[str] = field(
        default_factory=lambda: list(DEFAULT_INFRA_UI_SERVICES)
    )
```

- [ ] **Step 5: Read it in `load_config()`**

After the existing `infra = docker.get(...)` line, add:

```python
    infra_uis = docker.get("infra_ui_services", None)
```

and add this keyword argument to the returned `Config(...)`, right after
`infrastructure_stacks=...`:

```python
        infra_ui_services=list(infra_uis) if infra_uis is not None
        else list(DEFAULT_INFRA_UI_SERVICES),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests
git add src/terminal_status_panel/config.py tests/test_config.py
git commit -m "feat: add docker.infra_ui_services configuration option"
```

---

### Task 5: `infra-uis` pseudo stack in the Infrastruktur block

**Files:**
- Modify: `src/terminal_status_panel/render/panels.py:341-350` (new helpers),
  `:352-382` (`_stack_matrix` ordering and collapse rule),
  `:385-426` (`_stack_columns`)
- Modify: `README.md` (configuration table + full example)
- Test: `tests/test_render_panels.py`

**Interfaces:**
- Consumes: `Config.infra_ui_services` from Task 4; the existing helpers
  `_base_service_name(name, node_names)`, `_base_groups(services, node_names)`,
  `_strip_stack_prefix(base, stack)`, `_group_desc(services)`.
- Produces: module constant `INFRA_UI_STACK = "infra-uis"` and two private
  helpers, `_split_infra_uis(services, ui_keys, node_names) -> tuple[list, list]`
  and `_ui_subrows(ui_services, node_names) -> list[tuple[str, list, str]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render_panels.py`:

```python
def _line_index(out: str, predicate) -> int:
    lines = out.splitlines()
    return next(i for i, line in enumerate(lines) if predicate(line))


def test_infra_uis_are_grouped_into_a_pseudo_stack():
    N1, N2 = "srv-01", "srv-02"
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=2,
        nodes=[SwarmNode(N1, reachable=True, state="ready", availability="active"),
               SwarmNode(N2, reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("kafka_kafka", 1, 1, stack="kafka", description="Broker",
                          tasks=[ServiceTask(N1, "running")]),
            # A UI living inside a real stack must leave that stack.
            ServiceStatus("kafka_kafbat-ui", 1, 1, stack="kafka", description="Kafka UI",
                          tasks=[ServiceTask(N2, "running")]),
            # A UI deployed as its own stack.
            ServiceStatus("cloudbeaver_cloudbeaver", 1, 1, stack="cloudbeaver",
                          description="SQL UI", tasks=[ServiceTask(N1, "running")]),
            # A UI running as a standalone container.
            ServiceStatus("mongo-express", 1, 1, description="Mongo UI",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("eduTAP_web", 1, 1, stack="eduTAP", description="frontend",
                          tasks=[ServiceTask(N1, "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastruktur"))
    uis_at = _line_index(out, lambda ln: ln.strip().startswith("infra-uis"))
    kafka_at = _line_index(out, lambda ln: ln.strip().startswith("kafka"))
    service_at = _line_index(out, lambda ln: ln.strip().startswith("Service"))
    container_at = _line_index(out, lambda ln: ln.strip().startswith("Container (ohne"))

    # The pseudo stack heads the Infrastruktur block.
    assert infra_at < uis_at < kafka_at < service_at
    # All three UI shapes ended up inside it.
    for ui in ("kafbat-ui", "cloudbeaver", "mongo-express"):
        assert uis_at < _line_index(out, lambda ln, ui=ui: ui in ln) < kafka_at
    # Stack prefixes are stripped on the sub-rows.
    assert "kafka_kafbat-ui" not in out
    assert "cloudbeaver_cloudbeaver" not in out
    # Unrelated services keep their block.
    assert service_at < _line_index(out, lambda ln: "eduTAP" in ln) < container_at


def test_single_infra_ui_keeps_its_own_name():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("mongo-express", 1, 1, description="Mongo UI",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    # Not collapsed to a single row labelled 'infra-uis' — the UI stays named.
    assert "infra-uis" in out
    assert "mongo-express" in out


def test_infra_ui_services_win_over_infrastructure_stacks():
    """'portainer' is in both default lists — the UI list decides."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("portainer_portainer", 1, 1, stack="portainer",
                          description="Docker UI", tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("kafka_kafka", 1, 1, stack="kafka",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    uis_at = _line_index(out, lambda ln: ln.strip().startswith("infra-uis"))
    # Rendered as a sub-row of the pseudo stack, not as a top-level infra row.
    assert uis_at < _line_index(out, lambda ln: ln.strip().startswith("portainer"))
    assert _line_index(out, lambda ln: ln.strip().startswith("portainer")) < _line_index(
        out, lambda ln: ln.strip().startswith("kafka")
    )


def test_no_infra_uis_row_without_matching_services():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka", 1, 1, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "infra-uis" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_render_panels.py -v -k "infra or single_infra"`
Expected: `test_infra_uis_are_grouped_into_a_pseudo_stack`,
`test_single_infra_ui_keeps_its_own_name` and
`test_infra_ui_services_win_over_infrastructure_stacks` FAIL with
`StopIteration` (no `infra-uis` line exists);
`test_no_infra_uis_row_without_matching_services` passes already.

- [ ] **Step 3: Add the pseudo-stack constant and the two helpers**

In `src/terminal_status_panel/render/panels.py`, add the constant next to the
marker constants near the top of the file:

```python
# Name of the synthetic stack collecting infrastructure admin UIs.
INFRA_UI_STACK = "infra-uis"
```

and add the helpers directly after `_group_desc()`:

```python
def _split_infra_uis(services, ui_keys, node_names) -> tuple[list, list]:
    """Split *services* into (admin UIs, everything else).

    A service matches when one of *ui_keys* occurs in its stack name or in its
    node-suffix-stripped service name, so a UI is found whether it runs as a
    standalone container, as its own stack, or inside a larger stack."""
    uis, rest = [], []
    for svc in services:
        base = _base_service_name(svc.name, node_names)
        haystack = f"{svc.stack or ''} {base}".lower()
        (uis if any(key in haystack for key in ui_keys) else rest).append(svc)
    return uis, rest


def _ui_subrows(ui_services, node_names) -> list[tuple[str, list, str]]:
    """One sub-row per admin UI, labelled without stack prefix or node suffix."""
    rows = []
    for base, group in _base_groups(ui_services, node_names).items():
        stack = next((s.stack for s in group if s.stack), "")
        label = _strip_stack_prefix(base, stack) if stack else base
        rows.append((label or base, group, _group_desc(group)))
    rows.sort(key=lambda row: row[0].lower())
    return rows
```

- [ ] **Step 4: Teach `_stack_matrix` about the pseudo stack**

Two changes inside `_stack_matrix()`. First, the entry ordering — replace

```python
    for stack_name, subrows in sorted(entries, key=lambda e: e[0].lower()):
```

with

```python
    # The pseudo stack heads the block; real stacks stay alphabetical.
    for stack_name, subrows in sorted(
        entries, key=lambda e: (e[0] != INFRA_UI_STACK, e[0].lower())
    ):
```

Second, the single-service collapse — replace

```python
        if len(subrows) == 1:
```

with

```python
        # A lone UI must keep its own name; collapsing would hide which one runs.
        if len(subrows) == 1 and stack_name != INFRA_UI_STACK:
```

- [ ] **Step 5: Pull the UIs out in `_stack_columns`**

In `_stack_columns()`, add below the `infra_keys` line:

```python
    ui_keys = [k.lower() for k in cfg.infra_ui_services]
```

Replace the grouping block — from `stacks: dict[str, list] = {}` down to and
including the `for name, svcs in stacks.items():` loop — with:

```python
    # Admin UIs leave their origin stack and form one pseudo stack.
    ui_services, remaining = _split_infra_uis(swarm.services, ui_keys, node_names)

    stacks: dict[str, list] = {}
    ungrouped: list = []
    for svc in remaining:
        if svc.stack is None:
            ungrouped.append(svc)
        else:
            stacks.setdefault(svc.stack, []).append(svc)

    infra, service = [], []
    if ui_services:
        infra.append((INFRA_UI_STACK, _ui_subrows(ui_services, node_names)))
    for name, svcs in stacks.items():
        entry = (name, subrows_for(name, svcs))
        (infra if is_infra(name) else service).append(entry)
```

The remaining `container_rows` loop below stays exactly as it is — it already
iterates `ungrouped`, which no longer contains UI containers.

- [ ] **Step 6: Run the whole suite**

Run: `uv run python -m pytest`
Expected: PASS, including the pre-existing
`test_services_section_merges_per_node_replicas` (none of its service names
match the default UI keys).

- [ ] **Step 7: Eyeball the rendering**

Run:

```bash
uv run python -c "
from rich.console import Console
from terminal_status_panel.config import Config
from terminal_status_panel.model import ServiceStatus, ServiceTask, SwarmInfo, SwarmNode
from terminal_status_panel.render import panels
N1, N2 = 'srv-01', 'srv-02'
swarm = SwarmInfo(reachable=True, enabled=True, node_role='manager', node_count=2,
    nodes=[SwarmNode(N1, reachable=True, state='ready', availability='active'),
           SwarmNode(N2, reachable=True, state='ready', availability='active')],
    services=[
        ServiceStatus('kafka_kafka', 1, 1, stack='kafka', description='Broker',
                      tasks=[ServiceTask(N1, 'running')]),
        ServiceStatus('kafka_kafbat-ui', 1, 1, stack='kafka', description='Kafka UI',
                      tasks=[ServiceTask(N2, 'running')]),
        ServiceStatus('cloudbeaver_cloudbeaver', 1, 1, stack='cloudbeaver',
                      description='SQL UI', tasks=[ServiceTask(N1, 'running')]),
        ServiceStatus('mongo-express', 1, 1, description='Mongo UI',
                      tasks=[ServiceTask(N1, 'running')]),
    ])
Console(width=170).print(panels.services_section(swarm, Config()))
"
```

Expected: the Infrastruktur block starts with an `infra-uis` header followed by
indented `cloudbeaver`, `kafbat-ui` and `mongo-express` rows, then `kafka`.

- [ ] **Step 8: Document the option in the README**

In `README.md`, insert this row into the configuration table directly below the
`docker.infrastructure_stacks` row:

```markdown
| `docker.infra_ui_services` | `["kafbat-ui", "kafka-ui", "kafdrop", "cloudbeaver", "pgadmin", "adminer", "mongo-express", "mongo-gui", "rustfs-console", "rustfs-ui", "s3-browser", "s3browser", "redisinsight", "redis-commander", "portainer", "dozzle", "kibana"]` | Case-insensitive substrings matched against the stack name **and** the service name. Matching services leave their own stack and are collected as sub-rows of the pseudo stack **`infra-uis`**, shown first in the **Infrastruktur** block. On a name matching both lists, this one wins. |
```

and add the option to the `[docker]` block of the full example, below the
existing `infrastructure_stacks` line:

```toml
infra_ui_services = ["kafbat-ui", "cloudbeaver", "mongo-express", "rustfs-console"]
```

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check src tests
git add src/terminal_status_panel/render/panels.py tests/test_render_panels.py README.md
git commit -m "feat: group infrastructure admin UIs into the infra-uis pseudo stack"
```

---

### Task 6: Final verification

**Files:** none modified — verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: evidence that CI will pass.

- [ ] **Step 1: Run the full suite exactly as CI does**

Run: `uv run python -m pytest`
Expected: PASS, no errors, no warnings introduced by this branch.

- [ ] **Step 2: Run the linter exactly as CI does**

Run: `uv run ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 3: Smoke-test the CLI against the real environment**

Run: `uv run status-docker --width 170`
Expected: exits 0. On a host without a Docker socket it prints
`Docker not reachable` — that is a pass, not a failure.

- [ ] **Step 4: Review the branch diff**

Run: `git diff main...HEAD --stat` and `git log --oneline main..HEAD`
Expected: five feature/docs commits plus the design commit; only
`model.py`, `collectors/docker.py`, `render/panels.py`, `config.py`, the four
test files, `README.md` and the two `docs/superpowers/` files are touched.

- [ ] **Step 5: Report and stop**

Report changed files, test results, risks and open questions to the user.
**Do not push and do not open a merge request without being asked.**

---

## Risks and Notes

- **Emoji width.** `⚠️` (U+26A0 U+FE0F) may be measured as one cell by some
  terminals and two by others, so the Nodes line can shift by a column. Cosmetic
  only; `_OK`/`_DEAD` already have the same property.
- **Substring matching is blunt.** `infra_ui_services` uses the same
  case-insensitive substring approach as `infrastructure_stacks`; a stack
  literally named e.g. `portainer-backup` would also be pulled into `infra-uis`.
  Users override the list in their TOML if that bites.
- **Whole-stack capture.** A stack whose *name* matches a UI key (e.g. a stack
  named `cloudbeaver`) moves entirely into `infra-uis`, including any sidecar
  services it contains. This is intended for single-purpose UI stacks.
  **Amended during execution** (plan owner's ruling on a Task 5 review
  finding): such a sidecar keeps its origin as a `stack/service` label, so a
  detached row stays attributable. See the spec's *Grouping* section.
- **Behaviour change for existing deployments.** Hosts running Portainer will
  see it move from a top-level *Infrastruktur* row into the `infra-uis` group.
