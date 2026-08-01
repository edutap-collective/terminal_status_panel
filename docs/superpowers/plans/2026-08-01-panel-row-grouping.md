# Per-Node Task Counts, Ordinal Grouping, bugsink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show how many tasks a service holds on each node, collapse ordinal instances of one service into a single row, and move bugsink into the Infrastruktur block.

**Architecture:** Two small changes inside `render/panels.py` — one to the per-node cell, one to the row-grouping key — plus one entry in a config default list. Nothing about measurement changes; all three make data the collector already provides legible.

**Tech Stack:** Python 3.11+, `rich` (`Table.grid`, `Text`), `pytest`.

## Global Constraints

- **The panel never claims something it did not measure.** A gap in coverage renders neither as a clean bill of health nor as a definite failure.
- **Icon vocabulary** from `render/icons.py`, aliased in `panels.py` as `_OK`, `_WARN`, `_DEAD`: `✅` measured healthy · `⚠️` degraded but serving · `💀` measured broken. Do not define glyphs locally.
- **The single-task cell stays byte-identical.** It is the common path and must not get noisier.
- **Ordinal stripping uses `_` only, never `-`.** With `-<digits>` a stack named `PostgreSQL-18` whose service carries the same name (`PostgreSQL-18_PostgreSQL-18`) would be mutilated to `PostgreSQL-18_PostgreSQL`. `_` is what Swarm puts between a stack and its service.
- Python 3.11+, line length 100, ruff `select = ["E", "F", "I"]`.
- Code, comments and identifiers in English; user-facing panel strings in English.
- Run tests with `.venv/bin/python -m pytest`; lint with `uvx ruff@0.16 check src tests`. Do **not** run `ruff format` — it reflows 26 files including deliberately aligned comment columns, and adopting it is a separate decision.
- Baseline: **286 tests pass** on `main`, suite fully green. Any failure you did not cause is a finding, not background noise.

## File Structure

| File | Responsibility |
|---|---|
| `src/terminal_status_panel/render/panels.py` | **Modify.** `_node_cell` gains per-node counts; `_base_service_name` gains the ordinal strip. Both are small, local functions. |
| `src/terminal_status_panel/config.py` | **Modify.** One entry in `DEFAULT_INFRASTRUCTURE_STACKS`. |
| `tests/test_render_panels.py` | **Modify.** Tests for all three. |
| `README.md` | **Modify.** The cell semantics, the ordinal grouping and the updated default list. |

---

### Task 1: Per-node task counts

**Files:**
- Modify: `src/terminal_status_panel/render/panels.py` (`_node_cell`)
- Test: `tests/test_render_panels.py` (append)

**Interfaces:**
- Consumes: `_OK`, `_WARN`, `_DEAD` (already imported from `render/icons.py` at the top of `panels.py`), `ServiceTask` from `model.py`.
- Produces: no new public names. `_node_cell(services, node_full) -> Text` keeps its signature.

`_node_cell` is binary today: all tasks running → `✅`, otherwise `💀`. It cannot distinguish one instance from five on the same host.

The single-task cases must render exactly as before — that is the overwhelming majority of cells, and making them noisier would cost more than the new information is worth.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_panels.py  (append)

def _tasks(*states):
    """One ServiceStatus holding tasks on node 'srv-01' with the given states."""
    return [ServiceStatus("svc", sum(s == "running" for s in states), len(states),
                          tasks=[ServiceTask("srv-01", s) for s in states])]


def test_a_single_running_task_still_renders_the_bare_glyph():
    assert panels._node_cell(_tasks("running"), "srv-01").plain == panels._OK


def test_a_single_failed_task_still_renders_the_bare_glyph():
    assert panels._node_cell(_tasks("failed"), "srv-01").plain == panels._DEAD


def test_an_empty_node_cell_stays_blank():
    assert panels._node_cell(_tasks("running"), "other-node").plain == " "


def test_several_running_tasks_show_their_count():
    assert panels._node_cell(_tasks("running", "running"), "srv-01").plain == f"{panels._OK}2"
    assert (
        panels._node_cell(_tasks("running", "running", "running"), "srv-01").plain
        == f"{panels._OK}3"
    )


def test_a_partially_staffed_node_is_degraded_not_broken():
    cell = panels._node_cell(_tasks("running", "failed"), "srv-01")
    assert cell.plain == f"{panels._WARN}1/2"


def test_a_node_where_nothing_runs_is_broken_with_its_count():
    cell = panels._node_cell(_tasks("failed", "failed"), "srv-01")
    assert cell.plain == f"{panels._DEAD}0/2"


def test_tasks_of_several_services_in_one_row_are_counted_together():
    """A row bundles services — after ordinal grouping, two instances of one
    service can share a node."""
    services = [
        ServiceStatus("svc_1", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
        ServiceStatus("svc_2", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
    ]
    assert panels._node_cell(services, "srv-01").plain == f"{panels._OK}2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_panels.py -k "node_cell or task" -v`
Expected: FAIL on the multi-task tests — the current code returns a bare `✅`/`💀` regardless of count. The three single-task tests should already pass; that is the point of writing them first.

- [ ] **Step 3: Write the implementation**

Replace `_node_cell` in `panels.py`:

```python
def _node_cell(services, node_full: str) -> Text:
    """Aggregate status of a row's tasks on one node.

    A single task keeps the bare glyph, so the common cell stays exactly as
    quiet as it was. From two tasks up the count is shown: one ✅ cannot say
    whether a node holds one instance or five.
    """
    tasks = [t for s in services for t in s.tasks if t.node == node_full]
    if not tasks:
        return Text(" ")
    running = sum(1 for t in tasks if t.running)
    if len(tasks) == 1:
        return Text(_OK) if running else Text(_DEAD, style="red")
    if running == len(tasks):
        return Text(f"{_OK}{len(tasks)}")
    if running == 0:
        return Text(f"{_DEAD}0/{len(tasks)}", style="red")
    return Text(f"{_WARN}{running}/{len(tasks)}", style="yellow")
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Existing matrix tests assert on `✅`/`💀` substrings and single-task fixtures, so they should be unaffected — but if one fails, read it before touching it: a genuine regression and a stale expectation look identical from the failure message, and only one of them may be edited.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/panels.py tests/test_render_panels.py
git commit -m "feat: show how many tasks a service holds on each node"
```

---

### Task 2: Collapse ordinal instances into one row

**Files:**
- Modify: `src/terminal_status_panel/render/panels.py` (`_base_service_name`, plus an `import re` if absent)
- Test: `tests/test_render_panels.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: no new public names. `_base_service_name(name, node_names) -> str` keeps its signature.

`heidi_connector` must run as `heidi_connector_1`, `heidi_connector_2`, … because each pinned instance needs its own secrets. That is an implementation detail of secret management, and the panel currently gives each instance its own row.

`_base_service_name` already strips a known trailing node hostname so per-node replicas collapse. A second step runs after it and removes a trailing `_<digits>`. The two compose, should a name ever carry both.

**The separator is `_` and never `-`.** `PostgreSQL-18_PostgreSQL-18` — a stack whose single service carries the stack's name — would otherwise be mutilated to `PostgreSQL-18_PostgreSQL`. There is a test for exactly that, and it is the one a future "simplification" would break.

Note the current implementation `return`s as soon as a node suffix matches. It must now fall through to the ordinal step instead.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_panels.py  (append)

NODES = ["lmzvd06-ccc-01", "lmzvd06-ccn-01"]


def test_an_ordinal_suffix_is_stripped():
    assert (
        panels._base_service_name("edutap_production_heidi_connector_1", NODES)
        == "edutap_production_heidi_connector"
    )


def test_ordinal_stripping_survives_more_than_one_digit():
    assert panels._base_service_name("stack_worker_12", NODES) == "stack_worker"


def test_a_node_suffix_is_still_stripped():
    assert panels._base_service_name("kafka_kafka-lmzvd06-ccc-01", NODES) == "kafka_kafka"


def test_a_hyphen_before_digits_is_left_alone():
    """PostgreSQL-18_PostgreSQL-18 must not become PostgreSQL-18_PostgreSQL —
    '_' is what Swarm puts between a stack and its service, '-' is not."""
    assert (
        panels._base_service_name("PostgreSQL-18_PostgreSQL-18", NODES)
        == "PostgreSQL-18_PostgreSQL-18"
    )


def test_a_name_without_a_suffix_is_untouched():
    assert panels._base_service_name("traefik_sockproxy", NODES) == "traefik_sockproxy"


def test_a_name_that_is_only_an_ordinal_is_left_alone():
    assert panels._base_service_name("_1", NODES) == "_1"


def test_ordinal_instances_collapse_into_one_row():
    """Three pinned instances render as one row summing their replicas."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode(n, reachable=True, state="ready", availability="active")
               for n in ("srv-01", "srv-02", "srv-03")],
        services=[
            ServiceStatus(f"edutap_heidi_connector_{i}", 1, 1, stack="edutap",
                          tasks=[ServiceTask(f"srv-0{i}", "running")])
            for i in (1, 2, 3)
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "heidi_connector" in out
    assert "heidi_connector_1" not in out
    assert f"{icons.OK} 3/3" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_panels.py -k "ordinal or suffix or hyphen" -v`
Expected: FAIL — `_base_service_name` returns the name unchanged for `…_1`, so the first test reports the ordinal still attached.

- [ ] **Step 3: Write the implementation**

Add near the other module-level constants in `panels.py` (and `import re` at the top if it is not already there — check before adding):

```python
# Ordinal instances of one service: heidi_connector must run as
# heidi_connector_1, _2, … because each pinned instance needs its own secrets.
# Underscore only — with '-<digits>' a stack named PostgreSQL-18 whose service
# carries the same name would be mutilated to PostgreSQL-18_PostgreSQL.
_ORDINAL_SUFFIX = re.compile(r"_\d+$")
```

Replace `_base_service_name`:

```python
def _base_service_name(name: str, node_names) -> str:
    """Strip a trailing '-<node hostname>' / '_<node hostname>' so per-node
    replicas collapse (kafka_kafka-lmzvd06-ccc-01 -> kafka_kafka), then a
    trailing '_<digits>' so ordinal instances collapse
    (edutap_heidi_connector_1 -> edutap_heidi_connector)."""
    for nn in sorted(node_names, key=len, reverse=True):
        if nn and name.endswith(nn) and len(name) > len(nn) + 1:
            base = name[: -len(nn)].rstrip("-_")
            if base:
                name = base
                break
    return _ORDINAL_SUFFIX.sub("", name) or name
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. As in Task 1, read any pre-existing failure before editing it.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/panels.py tests/test_render_panels.py
git commit -m "feat: collapse ordinal instances of a service into one row"
```

---

### Task 3: bugsink is infrastructure

**Files:**
- Modify: `src/terminal_status_panel/config.py` (`DEFAULT_INFRASTRUCTURE_STACKS`)
- Test: `tests/test_render_panels.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new — one entry in an existing list.

bugsink is an error tracker and belongs with the other infrastructure. It currently renders under *Service*.

The Ansible role does not write `infrastructure_stacks` into `/etc/terminal-status-panel/config.toml` — it renders only `width`, the `[docker]` keys and the thresholds — so the package default is what production uses and this reaches the app servers with the next release, without a playbook run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_panels.py  (append)

def test_bugsink_is_infrastructure():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("bugsink_bugsink", 1, 1, stack="bugsink",
                          description="Bugsink (Fehler-Tracker)",
                          tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("app_web", 1, 1, stack="app",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastruktur"))
    service_at = _line_index(out, lambda ln: ln.strip().startswith("Service"))
    bugsink_at = _line_index(out, lambda ln: ln.strip().startswith("bugsink"))
    assert infra_at < bugsink_at < service_at
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render_panels.py -k bugsink -v`
Expected: FAIL — bugsink renders below the *Service* header, so `bugsink_at < service_at` is false.

- [ ] **Step 3: Write the implementation**

In `config.py`, add `"bugsink"` to `DEFAULT_INFRASTRUCTURE_STACKS`. Keep the list's existing wrapped-literal formatting; do not reflow the other entries.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `tests/test_config.py` asserts on defaults — if a test there names the list's contents, it is a stale expectation and may be updated; read it first.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/config.py tests/test_render_panels.py
git commit -m "feat: treat bugsink as infrastructure"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none.

Read the implementation before writing — `render/panels.py`'s `_node_cell` and `_base_service_name`, and `config.py`'s default list — and document what the code does.

- [ ] **Step 1: Document the per-node cell**

The README's DOCKER INFOS description (around lines 15-20) explains the matrix and the per-node placement. Extend it: a cell holding a single task keeps the bare glyph, and from two tasks up it carries the count — `✅2` when all run, `⚠️1/2` when some do, `💀0/2` when none do. Say why the single-task case stays bare: it is the overwhelming majority of cells.

- [ ] **Step 2: Document the ordinal grouping**

The same passage already says per-node replicas collapse into one row with the node name stripped. Add that a trailing `_<digits>` collapses too, so a service deployed as `heidi_connector_1`, `_2`, `_3` — one pinned instance per node, each with its own secrets — renders as a single `heidi_connector` row.

State the accepted cost plainly, because it is a real limitation and belongs where someone will find it: if an instance is removed from the deployment, its row folds into the remaining one and the panel reads `✅ 1/1` with nothing indicating a second was ever expected. Note the bound that makes this acceptable — the gap is invisible only across a *deployment* change, never across a *failure*: a failing instance still has a desired replica and renders `💀` or `⚠️` in both the `Working` column and its node cell.

- [ ] **Step 3: Update the default list**

`README.md:179` documents `docker.infrastructure_stacks` with its full default value. Add `bugsink` so the documented default matches `config.py`. Check whether the example block further down (around line 201) also lists the stacks; that one is an illustrative subset, not the default, so leave it unless it claims to be complete.

- [ ] **Step 4: Verify every claim**

Re-read what you wrote against the three source changes. Does every example string match what the code renders? Check `_node_cell`'s exact output format — glyph immediately followed by the count, no space — against your text.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document per-node task counts, ordinal grouping and bugsink"
```

---

## Self-Review

**Spec coverage.** Change 1 (per-node counts, six cell states) → Task 1. Change 2 (ordinal grouping, underscore-only rule, the `PostgreSQL-18_PostgreSQL-18` guard) → Task 2. Change 3 (bugsink) → Task 3. The spec's note that the two changes meet — a collapsed row can put several tasks on one node — is covered by Task 1's `test_tasks_of_several_services_in_one_row_are_counted_together`. Documentation, including the accepted cost of unconditional stripping → Task 4.

**Type consistency.** `_node_cell(services, node_full) -> Text` and `_base_service_name(name, node_names) -> str` both keep their existing signatures; no caller changes. `_ORDINAL_SUFFIX` is defined in Task 2 and used only there.

**One risk worth naming.** Both Task 1 and Task 2 change functions that existing matrix tests exercise indirectly through `services_section`. A genuine regression and a stale expectation look identical from the failure message, so both tasks tell the implementer to read a failing test before editing it. Task 2 carries the sharper edge: the ordinal strip changes row *identity*, so a pre-existing fixture whose service name happens to end in `_<digits>` would silently merge with a sibling.
