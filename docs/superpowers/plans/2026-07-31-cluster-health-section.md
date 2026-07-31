# CLUSTER HEALTH Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third panel section, `health`, that reports the state of the clustered infrastructure services (PostgreSQL, MongoDB, Kafka, GlusterFS, RustFS), WireGuard peer reachability, and DNS consistency at login.

**Architecture:** Each check is a small collector that returns a dataclass and never raises. A single new module `budget.py` runs all of them concurrently on daemon threads under one wall-clock deadline, so a hung check degrades to "unknown" instead of delaying the login shell. The cluster probes run read-only status commands *inside* the service containers through the Docker socket the panel already uses — the panel itself opens no database or broker connection and holds no credentials.

**Tech Stack:** Python 3.11+, `rich` (rendering), `docker` SDK, `dnspython` (new), `pytest`. Threads via `threading`, deliberately not `asyncio`: every check is subprocess or socket I/O and the rest of the package is synchronous.

## Global Constraints

- **Never break the login shell.** `cli.main()` returns 0 unconditionally. Every collector catches all exceptions and returns its dataclass with `error` set.
- **Timeout ≠ failure.** A check that hit the budget renders `…`; a check that failed renders `✗`. Never conflate them.
- **Not applicable ≠ error.** A node that runs no member of a service (no Mongo on `lrz_cc`, no Kafka on `vzd-app`), or has no passwordless sudo, sets `applicable=False` and renders "n/a here". It must never show a red panel.
- **Total budget default 5.0 s.** Individual timeouts: postgres 1.5, mongodb 2.5, kafka 4.0, glusterfs 1.0, rustfs 2.0, wireguard 1.0, dns 2.5. All run concurrently, so wall clock is bounded by the total, not the sum.
- **Threads must be daemon threads.** A non-daemon `ThreadPoolExecutor` thread is joined at interpreter exit and would hang the login shell past the budget. This is the single most important implementation detail in this plan.
- **Fixtures are real recorded output**, captured from the production clusters on 2026-07-31, never invented examples. They are reproduced verbatim in the tasks below.
- Python 3.11+, line length 100, ruff `select = ["E", "F", "I"]`.
- Code, comments and identifiers in English (repo convention: `edutap-collective`).

## File Structure

| File | Responsibility |
|---|---|
| `src/terminal_status_panel/budget.py` | **Create.** Concurrent runner with a wall-clock deadline. The only module that touches concurrency. |
| `src/terminal_status_panel/model.py` | **Modify.** Five new dataclasses plus `PanelData.health`. |
| `src/terminal_status_panel/collectors/clusters.py` | **Create.** Container discovery, exec helper, one probe per service kind, `collect_clusters()`. |
| `src/terminal_status_panel/collectors/network.py` | **Create.** WireGuard handshake ages, TCP fallback. |
| `src/terminal_status_panel/collectors/dns.py` | **Create.** Resolver, forward/reverse, peers, expectations, `/etc/hosts` comparison. Owns `read_hosts_file()`. |
| `src/terminal_status_panel/config.py` | **Modify.** `HealthConfig`, `DnsExpectation`, `[health]` parsing. |
| `src/terminal_status_panel/render/health.py` | **Create.** The three panels of the section. |
| `src/terminal_status_panel/render/layout.py` | **Modify.** `SECTIONS` gains `"health"`, plus the builder entry. |
| `src/terminal_status_panel/cli.py` | **Modify.** `collect_all` gate, `health_main` entry point. |
| `pyproject.toml` | **Modify.** `dnspython` dependency, `status-health` console script. |
| `README.md` | **Modify.** New section, new command, `[health]` reference, corrected "Docker API only" claim. |

`network.py` imports `read_hosts_file` from `dns.py` — a deliberate one-directional dependency, because mapping a WireGuard tunnel IP to a node name is a name-resolution concern and must not cost a network round trip.

---

### Task 1: Concurrent runner with a wall-clock budget

**Files:**
- Create: `src/terminal_status_panel/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BudgetResult(results: dict[str, Any], truncated: list[str], failed: dict[str, str])` and `run_with_budget(tasks: dict[str, Callable[[], Any]], budget: float) -> BudgetResult`. Every later collector task is invoked through this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_budget.py
import time

from terminal_status_panel.budget import run_with_budget


def test_fast_tasks_all_complete():
    result = run_with_budget({"a": lambda: 1, "b": lambda: 2}, budget=2.0)
    assert result.results == {"a": 1, "b": 2}
    assert result.truncated == []
    assert result.failed == {}


def test_slow_task_is_reported_as_truncated_not_failed():
    def slow():
        time.sleep(5)
        return "too late"

    result = run_with_budget({"fast": lambda: "ok", "slow": slow}, budget=0.3)
    assert result.results == {"fast": "ok"}
    assert result.truncated == ["slow"]
    assert result.failed == {}


def test_budget_bounds_wall_clock_not_the_sum():
    def slow():
        time.sleep(5)

    started = time.monotonic()
    run_with_budget({"a": slow, "b": slow, "c": slow}, budget=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"budget overrun: {elapsed:.2f}s"


def test_raising_task_is_reported_as_failed_not_truncated():
    def boom():
        raise ValueError("kaputt")

    result = run_with_budget({"boom": boom}, budget=1.0)
    assert result.results == {}
    assert result.truncated == []
    assert "boom" in result.failed
    assert "kaputt" in result.failed["boom"]


def test_worker_threads_are_daemon_so_they_never_block_interpreter_exit():
    import threading

    seen = {}

    def record():
        seen["daemon"] = threading.current_thread().daemon
        return None

    run_with_budget({"record": record}, budget=1.0)
    assert seen["daemon"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.budget'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/budget.py
"""Run named callables concurrently under a single wall-clock budget.

The only module in the package that touches concurrency. It exists so a hung
check degrades to "unknown" instead of delaying the login shell.

Deliberately hand-rolled daemon threads rather than ``ThreadPoolExecutor``:
the executor's workers are non-daemon and get joined by an ``atexit`` hook, so
a check that outlives the budget would still hold up interpreter exit — which
is exactly the failure this budget is meant to prevent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BudgetResult:
    """Outcome of one budgeted run.

    ``truncated`` and ``failed`` are kept apart on purpose: a blown budget says
    nothing about the state of the checked service, while a raised exception
    does. Conflating them would be the worst property of a status panel.
    """

    results: dict[str, Any] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def run_with_budget(tasks: dict[str, Callable[[], Any]], budget: float) -> BudgetResult:
    """Run every callable in *tasks* concurrently, waiting at most *budget* seconds."""
    results: dict[str, Any] = {}
    failed: dict[str, str] = {}
    lock = threading.Lock()

    def runner(name: str, func: Callable[[], Any]) -> None:
        try:
            value = func()
        except Exception as exc:  # a collector should catch its own, but never trust that
            with lock:
                failed[name] = f"{type(exc).__name__}: {exc}"
            return
        with lock:
            results[name] = value

    threads: list[tuple[str, threading.Thread]] = []
    for name, func in tasks.items():
        thread = threading.Thread(
            target=runner, args=(name, func), daemon=True, name=f"check-{name}"
        )
        thread.start()
        threads.append((name, thread))

    deadline = time.monotonic() + budget
    for _, thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    with lock:
        truncated = [
            name for name, _ in threads if name not in results and name not in failed
        ]
        return BudgetResult(results=dict(results), truncated=truncated, failed=dict(failed))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_budget.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/budget.py tests/test_budget.py
git add src/terminal_status_panel/budget.py tests/test_budget.py
git commit -m "feat: add concurrent runner with a wall-clock budget"
```

---

### Task 2: Data model for the health section

**Files:**
- Modify: `src/terminal_status_panel/model.py` (append after `SwarmInfo`, before `PanelData`; extend `PanelData`)
- Test: `tests/test_model.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `ClusterMember`, `ClusterService`, `PeerReachability`, `DnsCheck`, `HealthInfo`, and `PanelData.health: HealthInfo | None`. Every collector and the renderer depend on these exact field names.

Two deliberate refinements over the design sketch, both to keep honesty in the type system rather than in prose:

- `ClusterMember.healthy` is `bool | None`. `None` means **not observable** — MongoDB's `db.hello()` lists the set members but reports no per-member state. The renderer maps `None` to a neutral `·`, so the panel never claims health it did not measure.
- `ClusterMember.warning` carries a short string (`"lag"`, `"→ primary"`) instead of a pile of booleans, and `ClusterService.detail` carries the service-level note (Kafka's follower lag, the Gluster volume name).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model.py  (append)
from terminal_status_panel.model import (
    ClusterMember,
    ClusterService,
    DnsCheck,
    HealthInfo,
    PanelData,
    PeerReachability,
)


def test_cluster_member_defaults_to_unobserved_health():
    member = ClusterMember(name="pg18-lmzvd06-ccn-02")
    assert member.healthy is None
    assert member.role is None
    assert member.warning is None


def test_cluster_service_defaults_are_degraded_not_healthy():
    service = ClusterService(kind="postgres")
    assert service.applicable is True
    assert service.reachable is False
    assert service.leader is None
    assert service.quorum_ok is None
    assert service.members == []
    assert service.error is None


def test_health_info_defaults_are_empty():
    health = HealthInfo()
    assert health.clusters == []
    assert health.peers == []
    assert health.dns == []
    assert health.truncated == []


def test_panel_data_carries_health():
    assert PanelData().health is None
    health = HealthInfo(peers=[PeerReachability(name="ccn-01", method="wireguard")])
    assert PanelData(health=health).health.peers[0].method == "wireguard"


def test_dns_check_none_means_warning():
    assert DnsCheck(label="/etc/hosts", ok=None, detail="1 divergence").ok is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'ClusterMember'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/model.py  (insert after SwarmInfo)


@dataclass
class ClusterMember:
    """One member of a clustered infrastructure service.

    ``healthy`` is tri-state on purpose: ``None`` means *not observable*. The
    MongoDB probe, for instance, learns the set members but not their state,
    and the panel must not render an unmeasured ✅.
    """

    name: str
    node: str | None = None  # derived Swarm hostname, when derivable
    role: str | None = None  # primary / secondary / leader / voter / observer / peer
    healthy: bool | None = None
    detail: str | None = None  # kind-specific: LSN, brick path, endpoint
    warning: str | None = None  # short note: "lag", "→ primary"


@dataclass
class ClusterService:
    """State of one clustered infrastructure service as seen from this node."""

    kind: str  # postgres | mongodb | kafka | glusterfs | rustfs
    name: str | None = None  # PostgreSQL-18, lrz_app, cluster id, volume name
    applicable: bool = True  # False when this node runs no member
    reachable: bool = False
    leader: str | None = None  # primary / controller leader; None when leaderless
    quorum_ok: bool | None = None
    detail: str | None = None  # service-level note, e.g. Kafka follower lag
    members: list[ClusterMember] = field(default_factory=list)
    error: str | None = None


@dataclass
class PeerReachability:
    name: str
    method: str  # wireguard | tcp
    ok: bool = False
    detail: str | None = None  # handshake age or probed port


@dataclass
class DnsCheck:
    label: str
    ok: bool | None = None  # None = warning (inconsistent, not broken)
    detail: str = ""


@dataclass
class HealthInfo:
    clusters: list[ClusterService] = field(default_factory=list)
    peers: list[PeerReachability] = field(default_factory=list)
    dns: list[DnsCheck] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    # An empty ``clusters`` list means "nothing found" only when this is True.
    # It is False when the check never ran at all -- no Docker client, or every
    # kind disabled -- which must not render as "all clear".
    clusters_probed: bool = False
```

And extend `PanelData`:

```python
@dataclass
class PanelData:
    system: SystemInfo | None = None
    resources: ResourceUsage | None = None
    swarm: SwarmInfo | None = None
    updates: UpdateInfo | None = None
    health: HealthInfo | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_model.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/model.py tests/test_model.py
git add src/terminal_status_panel/model.py tests/test_model.py
git commit -m "feat: add health section data model"
```

---

### Task 3: Cluster collector skeleton and the PostgreSQL probe

**Files:**
- Create: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py`

**Interfaces:**
- Consumes: `ClusterMember`, `ClusterService` (Task 2).
- Produces:
  - `find_container(client, patterns: tuple[str, ...])` → the first locally running container whose name contains one of *patterns*, else `None`.
  - `exec_text(container, command: list[str]) -> str` → stdout as text; raises `RuntimeError` on a non-zero exit.
  - `parse_pg_state(output: str) -> ClusterService`
  - `probe_postgres(client) -> ClusterService`
  - `collect_clusters(client, kinds: list[str]) -> list[ClusterService]` — dispatches to the probes; Tasks 4–7 each register one more kind.

Container name patterns are module constants, not configuration: they must match the stack naming the Ansible roles produce, and a wrong value would silently disable a check. `_pg-` (not `pg-`) and `kafka_kafka-` (not `kafka`) are chosen so `kafbat-ui` and `kafka-ui` cannot match.

- [ ] **Step 1: Write the failing tests**

The fixture is the verbatim output recorded on `lmzvd06-ccc-01`.

```python
# tests/test_collectors_clusters.py
from terminal_status_panel.collectors import clusters

PG_STATE = """\
               Name |  Node |                Host:Port |       TLI: LSN |   Connection |      Reported State |      Assigned State
--------------------+-------+--------------------------+----------------+--------------+---------------------+--------------------
pg18-lmzvd06-ccn-02 |     1 | pg18-lmzvd06-ccn-02:5432 |   1: 0/75243B8 |   read-write |             primary |             primary
pg18-lmzvd06-ccn-03 |     2 | pg18-lmzvd06-ccn-03:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccn-04 |     3 | pg18-lmzvd06-ccn-04:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccc-01 |     4 | pg18-lmzvd06-ccc-01:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
pg18-lmzvd06-ccn-01 |     5 | pg18-lmzvd06-ccn-01:5432 |   1: 0/75243B8 |    read-only |           secondary |           secondary
"""


class _FakeContainer:
    def __init__(self, name, exec_result=(0, b""), env=None):
        self.name = name
        self._exec_result = exec_result
        self.attrs = {"Config": {"Env": env or []}}
        self.commands = []

    def exec_run(self, command, **kwargs):
        self.commands.append(command)
        return self._exec_result


class _FakeClient:
    def __init__(self, containers):
        self._containers = containers

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def containers(self):
        return self._Coll(self._containers)


def test_find_container_matches_substring_case_insensitively():
    target = _FakeContainer("PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc")
    client = _FakeClient([_FakeContainer("traefik_traefik.1.x"), target])
    assert clusters.find_container(client, ("_pg-",)) is target


def test_find_container_returns_none_when_nothing_matches():
    client = _FakeClient([_FakeContainer("traefik_traefik.1.x")])
    assert clusters.find_container(client, ("_pg-",)) is None


def test_kafka_pattern_does_not_match_the_kafbat_ui():
    client = _FakeClient([_FakeContainer("kafbat-ui_kafbat-ui.1.x")])
    assert clusters.find_container(client, ("kafka_kafka-",)) is None


def test_exec_text_raises_on_nonzero_exit():
    container = _FakeContainer("x", exec_result=(1, b"boom"))
    try:
        clusters.exec_text(container, ["true"])
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_parse_pg_state_finds_primary_and_all_members():
    service = clusters.parse_pg_state(PG_STATE)
    assert service.kind == "postgres"
    assert service.reachable is True
    assert service.leader == "pg18-lmzvd06-ccn-02"
    assert len(service.members) == 5
    assert service.quorum_ok is True


def test_parse_pg_state_derives_node_names_and_lsn():
    service = clusters.parse_pg_state(PG_STATE)
    primary = service.members[0]
    assert primary.node == "lmzvd06-ccn-02"
    assert primary.role == "primary"
    assert primary.healthy is True
    assert primary.detail == "0/75243B8"
    assert primary.warning is None


def test_parse_pg_state_marks_a_secondary_whose_lsn_lags():
    lagging = PG_STATE.replace(
        "pg18-lmzvd06-ccn-03:5432 |   1: 0/75243B8", "pg18-lmzvd06-ccn-03:5432 |   1: 0/7000000"
    )
    service = clusters.parse_pg_state(lagging)
    behind = [m for m in service.members if m.name == "pg18-lmzvd06-ccn-03"][0]
    assert behind.warning == "lag"


def test_parse_pg_state_marks_a_member_in_transition():
    moving = PG_STATE.replace(
        "|           secondary |           secondary\npg18-lmzvd06-ccn-04",
        "|           secondary |             primary\npg18-lmzvd06-ccn-04",
    )
    service = clusters.parse_pg_state(moving)
    moving_member = [m for m in service.members if m.name == "pg18-lmzvd06-ccn-03"][0]
    assert moving_member.warning == "→ primary"


def test_parse_pg_state_reports_no_quorum_when_most_members_are_down():
    broken = "\n".join(PG_STATE.splitlines()[:4]) + "\n" + "\n".join(
        line.replace("secondary |           secondary", "draining  |           draining ")
        for line in PG_STATE.splitlines()[4:]
    )
    service = clusters.parse_pg_state(broken)
    assert service.quorum_ok is False


def test_probe_postgres_is_not_applicable_without_a_local_container():
    service = clusters.probe_postgres(_FakeClient([]))
    assert service.applicable is False
    assert service.error is None


def test_probe_postgres_runs_pg_autoctl_and_parses_it():
    container = _FakeContainer(
        "PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc", exec_result=(0, PG_STATE.encode())
    )
    service = clusters.probe_postgres(_FakeClient([container]))
    assert container.commands == [["pg_autoctl", "show", "state"]]
    assert service.leader == "pg18-lmzvd06-ccn-02"


def test_probe_postgres_reports_an_exec_failure_as_error():
    container = _FakeContainer(
        "PostgreSQL-18_pg-lmzvd06-ccc-01.1.abc", exec_result=(1, b"connection refused")
    )
    service = clusters.probe_postgres(_FakeClient([container]))
    assert service.applicable is True
    assert service.reachable is False
    assert "connection refused" in service.error


def test_collect_clusters_only_probes_the_requested_kinds():
    assert clusters.collect_clusters(_FakeClient([]), kinds=[]) == []
    result = clusters.collect_clusters(_FakeClient([]), kinds=["postgres"])
    assert [s.kind for s in result] == ["postgres"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.clusters'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/collectors/clusters.py
"""Probe the clustered infrastructure services.

Every probe follows the same shape: find a locally running container of the
service, run one read-only status command inside it through the Docker API,
and parse the output into a ``ClusterService``.

The panel itself opens no database or broker connection and holds no
credentials — its only privilege is the Docker socket it already uses for the
DOCKER INFOS section.

A node that runs no member of a service is *not applicable*, not broken: no
MongoDB on lrz_cc and no Kafka on vzd-app are the normal case.
"""

from __future__ import annotations

import re

from ..model import ClusterMember, ClusterService

# Container name substrings, matched case-insensitively. Deliberately narrow:
# "_pg-" rather than "pg-", and "kafka_kafka-" rather than "kafka", so the
# admin UIs (kafbat-ui, kafka-ui, pgadmin) can never be mistaken for a member.
POSTGRES_PATTERNS = ("_pg-",)

_PG_NAME_PREFIX = re.compile(r"^pg\d*-")


def find_container(client, patterns: tuple[str, ...]):
    """First locally running container whose name contains one of *patterns*."""
    try:
        containers = client.containers.list()
    except Exception:
        return None
    for container in containers:
        name = (getattr(container, "name", "") or "").lower()
        if any(pattern.lower() in name for pattern in patterns):
            return container
    return None


def exec_text(container, command: list[str]) -> str:
    """Run *command* inside *container* and return stdout as text."""
    exit_code, output = container.exec_run(command)
    text = (output or b"").decode("utf-8", "replace")
    if exit_code != 0:
        raise RuntimeError(text.strip()[:200] or f"exit code {exit_code}")
    return text


def _node_from_member(name: str) -> str | None:
    """``pg18-lmzvd06-ccn-02`` -> ``lmzvd06-ccn-02``."""
    stripped = _PG_NAME_PREFIX.sub("", name)
    return stripped or None


def parse_pg_state(output: str) -> ClusterService:
    """Parse the fixed-width table of ``pg_autoctl show state``."""
    members: list[ClusterMember] = []
    primary_lsn: str | None = None

    for line in output.splitlines():
        # The separator row uses '+' rather than '|', so it drops out here.
        if "|" not in line:
            continue
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 7 or columns[0] == "Name":
            continue
        name, _node, _host_port, tli_lsn, _connection, reported, assigned = columns[:7]
        lsn = tli_lsn.split(":", 1)[1].strip() if ":" in tli_lsn else tli_lsn
        if reported == "primary":
            primary_lsn = lsn
        members.append(
            ClusterMember(
                name=name,
                node=_node_from_member(name),
                role=reported,
                healthy=reported in ("primary", "secondary"),
                detail=lsn,
                # Reported != assigned means the cluster is mid-transition:
                # neither healthy nor broken, so it is a warning.
                warning=f"→ {assigned}" if reported != assigned else None,
            )
        )

    leader = next((m.name for m in members if m.role == "primary"), None)
    for member in members:
        if member.role == "secondary" and primary_lsn and member.detail != primary_lsn:
            member.warning = member.warning or "lag"

    healthy_count = sum(1 for m in members if m.healthy)
    return ClusterService(
        kind="postgres",
        reachable=bool(members),
        leader=leader,
        quorum_ok=bool(members) and healthy_count * 2 > len(members),
        members=members,
    )


def probe_postgres(client) -> ClusterService:
    """``pg_autoctl show state`` — works from any data node, not only the monitor."""
    container = find_container(client, POSTGRES_PATTERNS)
    if container is None:
        return ClusterService(kind="postgres", applicable=False)
    try:
        output = exec_text(container, ["pg_autoctl", "show", "state"])
    except Exception as exc:
        return ClusterService(kind="postgres", error=str(exc))
    service = parse_pg_state(output)
    stack = (getattr(container, "name", "") or "").split("_", 1)[0]
    service.name = stack or None
    return service


_PROBES = {
    "postgres": probe_postgres,
}


def collect_clusters(client, kinds: list[str]) -> list[ClusterService]:
    """Probe each requested kind. Never raises."""
    services: list[ClusterService] = []
    for kind in kinds:
        probe = _PROBES.get(kind)
        if probe is None:
            continue
        try:
            services.append(probe(client))
        except Exception as exc:
            services.append(ClusterService(kind=kind, error=f"{type(exc).__name__}: {exc}"))
    return services
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: probe the PostgreSQL cluster via pg_autoctl show state"
```

---

### Task 4: MongoDB probe

**Files:**
- Modify: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py` (append)

**Interfaces:**
- Consumes: `find_container`, `exec_text`, `_PROBES` (Task 3).
- Produces: `parse_mongo_hello(output: str) -> ClusterService`, `probe_mongodb(client) -> ClusterService`, and a `"mongodb"` entry in `_PROBES`.

`db.hello()` needs no authentication — the Ansible role's own healthcheck already relies on an unauthenticated `ping`. It reports the member list but **not** each member's state, so every host other than the primary and `me` gets `healthy=None`, and `quorum_ok` means exactly *a primary exists*.

- [ ] **Step 1: Write the failing tests**

The fixture is the verbatim output recorded on `lmzvd06-internet-app-1`.

```python
# tests/test_collectors_clusters.py  (append)

MONGO_HELLO = (
    '{"set":"lrz_app","me":"mongodb-lmzvd06-internet-app-1:27017",'
    '"primary":"mongodb-lmzvd06-internet-app-2:27017","isPrimary":false,'
    '"hosts":["mongodb-lmzvd06-internet-app-1:27017","mongodb-lmzvd06-internet-app-2:27017",'
    '"mongodb-lmzvd06-internet-app-3:27017","mongodb-lmzvd06-internet-app-4:27017",'
    '"mongodb-lmzvd06-internet-app-5:27017"]}\n'
)


def test_parse_mongo_hello_reads_set_name_and_primary():
    service = clusters.parse_mongo_hello(MONGO_HELLO)
    assert service.kind == "mongodb"
    assert service.name == "lrz_app"
    assert service.reachable is True
    assert service.leader == "mongodb-lmzvd06-internet-app-2:27017"
    assert service.quorum_ok is True
    assert len(service.members) == 5


def test_parse_mongo_hello_only_claims_health_where_it_has_evidence():
    service = clusters.parse_mongo_hello(MONGO_HELLO)
    by_name = {member.name: member for member in service.members}
    # We just talked to this one, and the set agrees on the primary.
    assert by_name["mongodb-lmzvd06-internet-app-1:27017"].healthy is True
    assert by_name["mongodb-lmzvd06-internet-app-2:27017"].healthy is True
    assert by_name["mongodb-lmzvd06-internet-app-2:27017"].role == "primary"
    # db.hello() says nothing about the state of the others.
    assert by_name["mongodb-lmzvd06-internet-app-4:27017"].healthy is None
    assert by_name["mongodb-lmzvd06-internet-app-4:27017"].role == "member"


def test_parse_mongo_hello_without_a_primary_has_no_quorum():
    service = clusters.parse_mongo_hello(MONGO_HELLO.replace(
        '"primary":"mongodb-lmzvd06-internet-app-2:27017",', ""
    ))
    assert service.leader is None
    assert service.quorum_ok is False


def test_probe_mongodb_is_not_applicable_without_a_local_container():
    service = clusters.probe_mongodb(_FakeClient([]))
    assert service.applicable is False


def test_probe_mongodb_runs_mongosh_unauthenticated():
    container = _FakeContainer(
        "mongodb_lmzvd06-internet-app-1.1.abc", exec_result=(0, MONGO_HELLO.encode())
    )
    service = clusters.probe_mongodb(_FakeClient([container]))
    command = container.commands[0]
    assert command[0] == "mongosh"
    assert "--quiet" in command
    assert not any("-u" == part or "--username" in part for part in command)
    assert service.name == "lrz_app"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_clusters.py -k mongo -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_mongo_hello'`

- [ ] **Step 3: Write the implementation**

Append to `clusters.py` (and add `import json` at the top):

```python
MONGODB_PATTERNS = ("mongodb",)

# db.hello() is unauthenticated — the Ansible role's own healthcheck already
# relies on an unauthenticated ping. rs.status() would report per-member state
# but needs credentials, which are deliberately out of scope.
MONGO_EVAL = (
    "const h = db.hello(); "
    "JSON.stringify({set: h.setName, me: h.me, primary: h.primary, "
    "isPrimary: h.isWritablePrimary, hosts: h.hosts})"
)
MONGO_COMMAND = [
    "mongosh",
    "--tls",
    "--tlsAllowInvalidCertificates",
    "--quiet",
    "--eval",
    MONGO_EVAL,
]


def parse_mongo_hello(output: str) -> ClusterService:
    """Parse the JSON produced by ``db.hello()``."""
    line = next((raw for raw in reversed(output.splitlines()) if raw.strip()), "")
    data = json.loads(line)
    primary = data.get("primary")
    me = data.get("me")
    members = []
    for host in data.get("hosts") or []:
        if host == primary:
            role, healthy = "primary", True
        elif host == me:
            # We just executed a command against this member.
            role, healthy = "secondary", True
        else:
            # db.hello() lists membership, not state — claim nothing.
            role, healthy = "member", None
        members.append(ClusterMember(name=host, role=role, healthy=healthy))
    return ClusterService(
        kind="mongodb",
        name=data.get("set"),
        reachable=True,
        leader=primary,
        # For MongoDB this means exactly "a primary exists" and nothing more.
        quorum_ok=bool(primary),
        members=members,
    )


def probe_mongodb(client) -> ClusterService:
    """``db.hello()`` through mongosh — no credentials required."""
    container = find_container(client, MONGODB_PATTERNS)
    if container is None:
        return ClusterService(kind="mongodb", applicable=False)
    try:
        output = exec_text(container, MONGO_COMMAND)
        return parse_mongo_hello(output)
    except Exception as exc:
        return ClusterService(kind="mongodb", error=str(exc))
```

Register it: `_PROBES["mongodb"] = probe_mongodb` — i.e. extend the dict literal to

```python
_PROBES = {
    "postgres": probe_postgres,
    "mongodb": probe_mongodb,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: probe the MongoDB replica set via db.hello()"
```

---

### Task 5: Kafka probe

**Files:**
- Modify: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py` (append)

**Interfaces:**
- Consumes: `find_container`, `exec_text`, `_PROBES`.
- Produces: `parse_kafka_quorum(output: str) -> ClusterService`, `probe_kafka(client) -> ClusterService`, `"kafka"` in `_PROBES`.

Two facts that cost time to discover and must not be re-litigated: the Kafka tools are **not on `$PATH`** (the absolute `/opt/kafka/bin/…` path is required), and `/client.properties` is mounted by the `kafka` role explicitly for `docker exec` queries, using the broker certificate whose principal is a superuser.

`quorum_ok` is `leader is not None` and deliberately nothing more: the status output reports `MaxFollowerLag` but not *which* follower is behind, so "all voters healthy" cannot be derived without inventing a lag threshold.

- [ ] **Step 1: Write the failing tests**

The fixture is the verbatim output recorded on `lmzvd06-ccc-01`.

```python
# tests/test_collectors_clusters.py  (append)

KAFKA_QUORUM = """\
ClusterId:              Jucv8gBrQg-WOxKNTIAPVw
LeaderId:               1
LeaderEpoch:            2
HighWatermark:          173016
MaxFollowerLag:         0
MaxFollowerLagTimeMs:   296
CurrentVoters:          [{"id": 0, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccc-01:9093"]}, {"id": 1, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-01:9093"]}, {"id": 2, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-02:9093"]}, {"id": 3, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-03:9093"]}, {"id": 4, "endpoints": ["CONTROLLER://kafka-lmzvd06-ccn-04:9093"]}]
CurrentObservers:       []
"""


def test_parse_kafka_quorum_maps_the_leader_id_to_its_endpoint_host():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert service.kind == "kafka"
    assert service.reachable is True
    assert service.leader == "kafka-lmzvd06-ccn-01"
    assert service.quorum_ok is True
    assert service.name == "Jucv8gBrQg-WOxKNTIAPVw"


def test_parse_kafka_quorum_lists_voters_with_the_leader_marked():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert len(service.members) == 5
    by_name = {member.name: member for member in service.members}
    assert by_name["kafka-lmzvd06-ccn-01"].role == "leader"
    assert by_name["kafka-lmzvd06-ccc-01"].role == "voter"
    assert by_name["kafka-lmzvd06-ccc-01"].healthy is True


def test_parse_kafka_quorum_carries_follower_lag_as_service_detail():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM)
    assert "0" in service.detail
    assert "296" in service.detail


def test_parse_kafka_quorum_without_a_leader_has_no_quorum():
    service = clusters.parse_kafka_quorum(KAFKA_QUORUM.replace("LeaderId:               1", ""))
    assert service.leader is None
    assert service.quorum_ok is False


def test_parse_kafka_quorum_marks_observers():
    with_observer = KAFKA_QUORUM.replace(
        "CurrentObservers:       []",
        'CurrentObservers:       [{"id": 9, "endpoints": ["CONTROLLER://kafka-obs:9093"]}]',
    )
    service = clusters.parse_kafka_quorum(with_observer)
    by_name = {member.name: member for member in service.members}
    assert by_name["kafka-obs"].role == "observer"


def test_probe_kafka_uses_the_absolute_tool_path_and_the_mounted_client_properties():
    container = _FakeContainer(
        "kafka_kafka-lmzvd06-ccc-01.1.abc", exec_result=(0, KAFKA_QUORUM.encode())
    )
    service = clusters.probe_kafka(_FakeClient([container]))
    command = container.commands[0]
    assert command[0] == "/opt/kafka/bin/kafka-metadata-quorum.sh"
    assert "/client.properties" in command
    assert service.leader == "kafka-lmzvd06-ccn-01"


def test_probe_kafka_is_not_applicable_without_a_local_broker():
    assert clusters.probe_kafka(_FakeClient([])).applicable is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_clusters.py -k kafka -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_kafka_quorum'`

- [ ] **Step 3: Write the implementation**

Append to `clusters.py`:

```python
KAFKA_PATTERNS = ("kafka_kafka-",)

# The Kafka tools are NOT on $PATH in the image — the absolute path is required.
# /client.properties is mounted by the kafka Ansible role explicitly for
# "manuelle Abfragen per docker exec" and uses the broker certificate.
KAFKA_COMMAND = [
    "/opt/kafka/bin/kafka-metadata-quorum.sh",
    "--bootstrap-server",
    "localhost:9092",
    "--command-config",
    "/client.properties",
    "describe",
    "--status",
]


def _kafka_endpoint_host(entry: dict) -> str:
    """``CONTROLLER://kafka-lmzvd06-ccn-01:9093`` -> ``kafka-lmzvd06-ccn-01``."""
    endpoints = entry.get("endpoints") or []
    raw = endpoints[0] if endpoints else str(entry.get("id", "?"))
    without_scheme = raw.split("://", 1)[-1]
    return without_scheme.rsplit(":", 1)[0]


def parse_kafka_quorum(output: str) -> ClusterService:
    """Parse ``kafka-metadata-quorum.sh describe --status`` (KRaft)."""
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    def _json_list(key: str) -> list[dict]:
        try:
            return json.loads(fields.get(key, "[]"))
        except (ValueError, TypeError):
            return []

    leader_id = fields.get("LeaderId")
    members: list[ClusterMember] = []
    leader: str | None = None
    for entry in _json_list("CurrentVoters"):
        host = _kafka_endpoint_host(entry)
        is_leader = leader_id is not None and str(entry.get("id")) == leader_id
        if is_leader:
            leader = host
        members.append(
            ClusterMember(name=host, role="leader" if is_leader else "voter", healthy=True)
        )
    for entry in _json_list("CurrentObservers"):
        members.append(
            ClusterMember(name=_kafka_endpoint_host(entry), role="observer", healthy=True)
        )

    lag = fields.get("MaxFollowerLag", "?")
    lag_ms = fields.get("MaxFollowerLagTimeMs", "?")
    return ClusterService(
        kind="kafka",
        name=fields.get("ClusterId"),
        reachable=bool(fields),
        leader=leader,
        # Only "a leader exists": the status output does not say which follower
        # is behind, so anything stronger would need an invented lag threshold.
        quorum_ok=leader is not None,
        detail=f"Lag {lag} / {lag_ms} ms",
        members=members,
    )


def probe_kafka(client) -> ClusterService:
    """KRaft controller quorum. Costs ~2.6 s — JVM startup, not optimisable."""
    container = find_container(client, KAFKA_PATTERNS)
    if container is None:
        return ClusterService(kind="kafka", applicable=False)
    try:
        return parse_kafka_quorum(exec_text(container, KAFKA_COMMAND))
    except Exception as exc:
        return ClusterService(kind="kafka", error=str(exc))
```

Extend the registry to include `"kafka": probe_kafka`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: probe the Kafka KRaft controller quorum"
```

---

### Task 6: GlusterFS probe

**Files:**
- Modify: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py` (append)

**Interfaces:**
- Consumes: `_PROBES`.
- Produces: `parse_gluster(peer_xml: str, volume_xml: str) -> ClusterService`, `probe_glusterfs() -> ClusterService`, `"glusterfs"` in `_PROBES`.

GlusterFS runs on the **host**, not in a container: this probe uses `subprocess` with `sudo -n`, not the Docker API. Two decisions worth stating:

- Use `--xml`. The plain-text output wraps long brick paths across two lines, and parsing that is fragile in exactly the way that produces a wrong panel rather than an obvious crash.
- In `gluster volume status --xml`, self-heal daemons appear as ordinary `<node>` entries with `<hostname>Self-heal Daemon</hostname>`. They must be excluded from the brick count or the panel reports twice as many bricks as exist.

Leaderless: `leader` stays `None`. Without passwordless sudo the block is *not applicable*, not an error.

- [ ] **Step 1: Write the failing tests**

Fixtures abridged from the real XML recorded on `lmzvd06-ccc-01` (structure verbatim).

```python
# tests/test_collectors_clusters.py  (append)

GLUSTER_PEERS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <peerStatus>
    <peer><uuid>e309</uuid><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <connected>1</connected><stateStr>Peer in Cluster</stateStr></peer>
    <peer><uuid>f72d</uuid><hostname>wg-lmzvd06-ccn-02.srv.mwn.de</hostname>
      <connected>0</connected><stateStr>Peer in Cluster</stateStr></peer>
  </peerStatus>
</cliOutput>
"""

GLUSTER_VOLUME = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <volStatus><volumes><volume>
    <volName>shared</volName>
    <nodeCount>4</nodeCount>
    <node><hostname>wg-lmzvd06-ccc-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>1</status></node>
    <node><hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>
      <path>/data/glusterfs/brick1/shared</path><status>0</status></node>
    <node><hostname>Self-heal Daemon</hostname><path>localhost</path><status>1</status></node>
    <node><hostname>Self-heal Daemon</hostname>
      <path>wg-lmzvd06-ccn-01.srv.mwn.de</path><status>1</status></node>
  </volume></volumes></volStatus>
</cliOutput>
"""


def test_parse_gluster_excludes_self_heal_daemons_from_the_brick_count():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    bricks = [member for member in service.members if member.role == "brick"]
    assert len(bricks) == 2, "self-heal daemons must not be counted as bricks"


def test_parse_gluster_reports_volume_name_and_is_leaderless():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    assert service.kind == "glusterfs"
    assert service.name == "shared"
    assert service.leader is None
    assert service.reachable is True


def test_parse_gluster_marks_a_disconnected_peer_and_an_offline_brick():
    service = clusters.parse_gluster(GLUSTER_PEERS, GLUSTER_VOLUME)
    by_key = {(member.role, member.name): member for member in service.members}
    assert by_key[("peer", "wg-lmzvd06-ccn-01.srv.mwn.de")].healthy is True
    assert by_key[("peer", "wg-lmzvd06-ccn-02.srv.mwn.de")].healthy is False
    assert by_key[("brick", "wg-lmzvd06-ccn-01.srv.mwn.de")].healthy is False


def test_parse_gluster_has_no_quorum_when_most_peers_are_disconnected():
    two_down = GLUSTER_PEERS.replace(
        "<hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>\n      <connected>1</connected>",
        "<hostname>wg-lmzvd06-ccn-01.srv.mwn.de</hostname>\n      <connected>0</connected>",
    )
    assert clusters.parse_gluster(two_down, GLUSTER_VOLUME).quorum_ok is False


def test_probe_glusterfs_is_not_applicable_without_passwordless_sudo(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("sudo: a password is required")

    monkeypatch.setattr(clusters.subprocess, "run", refuse)
    service = clusters.probe_glusterfs()
    assert service.applicable is False
    assert service.error is None


def test_probe_glusterfs_calls_sudo_n_with_xml(monkeypatch):
    calls = []

    class _Completed:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        return _Completed(GLUSTER_PEERS if "peer" in command else GLUSTER_VOLUME)

    monkeypatch.setattr(clusters.subprocess, "run", fake_run)
    service = clusters.probe_glusterfs()
    assert calls[0][:3] == ["sudo", "-n", "gluster"]
    assert "--xml" in calls[0]
    assert service.name == "shared"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_clusters.py -k gluster -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_gluster'`

- [ ] **Step 3: Write the implementation**

Append to `clusters.py` (add `import subprocess` and `from xml.etree import ElementTree` at the top):

```python
GLUSTER_TIMEOUT = 1.0


def _gluster(arguments: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["sudo", "-n", "gluster", *arguments, "--xml"],
        capture_output=True,
        text=True,
        timeout=GLUSTER_TIMEOUT,
    )
    if completed.returncode != 0:
        raise OSError(completed.stdout.strip()[:200] or "gluster failed")
    return completed.stdout


def parse_gluster(peer_xml: str, volume_xml: str) -> ClusterService:
    """Parse ``gluster peer status --xml`` and ``gluster volume status --xml``."""
    members: list[ClusterMember] = []

    peers = ElementTree.fromstring(peer_xml)
    connected = 0
    total_peers = 0
    for peer in peers.iter("peer"):
        hostname = (peer.findtext("hostname") or "").strip()
        is_connected = (peer.findtext("connected") or "0").strip() == "1"
        total_peers += 1
        connected += int(is_connected)
        members.append(
            ClusterMember(
                name=hostname,
                role="peer",
                healthy=is_connected,
                detail=(peer.findtext("stateStr") or "").strip() or None,
            )
        )

    volume = ElementTree.fromstring(volume_xml)
    volume_name = None
    for node in volume.iter("volume"):
        volume_name = (node.findtext("volName") or "").strip() or None
        break
    for node in volume.iter("node"):
        hostname = (node.findtext("hostname") or "").strip()
        # Self-heal daemons are ordinary <node> entries; counting them as
        # bricks would double the reported brick count.
        if hostname == "Self-heal Daemon":
            continue
        members.append(
            ClusterMember(
                name=hostname,
                role="brick",
                healthy=(node.findtext("status") or "0").strip() == "1",
                detail=(node.findtext("path") or "").strip() or None,
            )
        )

    return ClusterService(
        kind="glusterfs",
        name=volume_name,
        reachable=True,
        leader=None,  # GlusterFS has no leader
        quorum_ok=total_peers > 0 and (connected + 1) * 2 > total_peers + 1,
        members=members,
    )


def probe_glusterfs() -> ClusterService:
    """GlusterFS runs on the host, so this uses sudo -n rather than the Docker API."""
    try:
        peer_xml = _gluster(["peer", "status"])
        volume_xml = _gluster(["volume", "status"])
    except Exception:
        # No sudo, no gluster installed, no cluster: not applicable, not broken.
        return ClusterService(kind="glusterfs", applicable=False)
    try:
        return parse_gluster(peer_xml, volume_xml)
    except Exception as exc:
        return ClusterService(kind="glusterfs", error=str(exc))
```

`collect_clusters` passes a client to every probe, but `probe_glusterfs` takes none. Adapt the registry entry with a lambda that drops it:

```python
_PROBES = {
    "postgres": probe_postgres,
    "mongodb": probe_mongodb,
    "kafka": probe_kafka,
    "glusterfs": lambda _client: probe_glusterfs(),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS (31 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: probe GlusterFS peers and bricks via the XML CLI output"
```

---

### Task 7: RustFS probe

**Files:**
- Modify: `src/terminal_status_panel/collectors/clusters.py`
- Test: `tests/test_collectors_clusters.py` (append)

**Interfaces:**
- Consumes: `find_container`, `exec_text`, `_PROBES`.
- Produces: `rustfs_endpoints(container) -> list[str]`, `probe_rustfs(client) -> ClusterService`, `"rustfs"` in `_PROBES`.

The endpoint list comes from `RUSTFS_VOLUMES` in the container environment, deliberately **not** from configuration. RustFS runs today in `shared` mode (`RUSTFS_VOLUMES=/data`, verified) and is being extended to all nodes; deriving the endpoints from the container makes the check correct in both modes and survives the rollout without a code or config change. A value that is a path means a single local instance; values that look like URLs are the distributed endpoints.

Requests run from *inside* the RustFS container, because the `rustfs` overlay network publishes no host ports. `/health` answers 200 unauthenticated; `/minio/health/cluster` and the admin API answer 403, so heal and erasure-coding state are out of reach — an accepted limitation.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collectors_clusters.py  (append)


def test_rustfs_endpoints_treats_a_plain_path_as_a_single_local_instance():
    container = _FakeContainer("rustfs_rustfs.1.abc", env=["RUSTFS_VOLUMES=/data"])
    assert clusters.rustfs_endpoints(container) == ["https://localhost:9000"]


def test_rustfs_endpoints_reads_distributed_urls():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc",
        env=["RUSTFS_VOLUMES=https://rustfs-a:9000/data https://rustfs-b:9000/data"],
    )
    assert clusters.rustfs_endpoints(container) == [
        "https://rustfs-a:9000",
        "https://rustfs-b:9000",
    ]


def test_rustfs_endpoints_falls_back_to_localhost_without_the_variable():
    container = _FakeContainer("rustfs_rustfs.1.abc", env=[])
    assert clusters.rustfs_endpoints(container) == ["https://localhost:9000"]


def test_probe_rustfs_is_not_applicable_without_a_local_container():
    assert clusters.probe_rustfs(_FakeClient([])).applicable is False


def test_probe_rustfs_marks_a_200_endpoint_healthy():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc", exec_result=(0, b"200"), env=["RUSTFS_VOLUMES=/data"]
    )
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.kind == "rustfs"
    assert service.reachable is True
    assert service.leader is None
    assert len(service.members) == 1
    assert service.members[0].healthy is True
    assert service.members[0].role == "peer"
    assert "/health" in " ".join(container.commands[0])


def test_probe_rustfs_marks_a_non_200_endpoint_unhealthy():
    container = _FakeContainer(
        "rustfs_rustfs.1.abc", exec_result=(0, b"403"), env=["RUSTFS_VOLUMES=/data"]
    )
    service = clusters.probe_rustfs(_FakeClient([container]))
    assert service.members[0].healthy is False
    assert service.quorum_ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_clusters.py -k rustfs -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'rustfs_endpoints'`

- [ ] **Step 3: Write the implementation**

Append to `clusters.py`:

```python
RUSTFS_PATTERNS = ("rustfs_rustfs",)
RUSTFS_FALLBACK_ENDPOINT = "https://localhost:9000"


def rustfs_endpoints(container) -> list[str]:
    """Endpoints to probe, derived from RUSTFS_VOLUMES in the container env.

    Read from the container rather than from configuration so the check stays
    correct across the move from ``shared`` (a local path) to ``distributed``
    (a list of URLs) without any change here.
    """
    environment = ((getattr(container, "attrs", {}) or {}).get("Config") or {}).get("Env") or []
    raw = ""
    for entry in environment:
        key, _, value = str(entry).partition("=")
        if key == "RUSTFS_VOLUMES":
            raw = value
            break
    endpoints = []
    for token in raw.split():
        if "://" not in token:
            continue  # a plain path: one local instance
        scheme, _, rest = token.partition("://")
        host_port = rest.split("/", 1)[0]
        endpoints.append(f"{scheme}://{host_port}")
    return endpoints or [RUSTFS_FALLBACK_ENDPOINT]


def probe_rustfs(client) -> ClusterService:
    """GET /health per endpoint — the only unauthenticated status RustFS offers."""
    container = find_container(client, RUSTFS_PATTERNS)
    if container is None:
        return ClusterService(kind="rustfs", applicable=False)
    members: list[ClusterMember] = []
    for endpoint in rustfs_endpoints(container):
        try:
            # curl from inside the container: the rustfs overlay network
            # publishes no host ports.
            status = exec_text(
                container,
                [
                    "curl", "-ks", "-o", "/dev/null", "-m", "2",
                    "-w", "%{http_code}", f"{endpoint}/health",
                ],
            ).strip()
            healthy = status == "200"
            detail = f"HTTP {status}"
        except Exception as exc:
            healthy = False
            detail = str(exc)[:60]
        members.append(
            ClusterMember(name=endpoint, role="peer", healthy=healthy, detail=detail)
        )
    live = sum(1 for member in members if member.healthy)
    return ClusterService(
        kind="rustfs",
        name="rustfs",
        # members come from the endpoint list, not from probe success, so
        # reachability has to be derived from how many actually answered.
        reachable=live > 0,
        leader=None,  # RustFS has no leader we can observe
        # /health is a liveness check only; erasure-coding and heal state are
        # not observable without the admin API. A majority of instances is the
        # strongest claim the data supports, matching postgres and glusterfs.
        quorum_ok=live * 2 > len(members),
        detail=f"{live}/{len(members)} live",
        members=members,
    )
```

Extend the registry with `"rustfs": probe_rustfs`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_clusters.py -v`
Expected: PASS (37 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git add src/terminal_status_panel/collectors/clusters.py tests/test_collectors_clusters.py
git commit -m "feat: probe RustFS endpoints derived from RUSTFS_VOLUMES"
```

---

### Task 8: DNS collector

**Files:**
- Create: `src/terminal_status_panel/collectors/dns.py`
- Modify: `pyproject.toml` (add `dnspython` to `dependencies`)
- Test: `tests/test_collectors_dns.py`

**Interfaces:**
- Consumes: `DnsCheck` (Task 2).
- Produces:
  - `read_hosts_file(path: str = "/etc/hosts") -> dict[str, set[str]]` — name (lowercased) → set of IPs. **Task 9 imports this.**
  - `collect_dns(fqdn, peer_names, expectations, timeout, resolver=None, hosts_path="/etc/hosts") -> list[DnsCheck]`

`expectations` is a list of `(name, addresses)` tuples; an empty `addresses` means "must resolve at all".

Why dnspython rather than `socket.getaddrinfo`: `getaddrinfo` consults `/etc/hosts`, and the divergence between `/etc/hosts` and DNS is precisely the fault this check exists to surface. The tests inject a stub resolver, so no test touches the network.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collectors_dns.py
from terminal_status_panel.collectors import dns as dns_collector


class _StubResolver:
    """Minimal stand-in for dns.resolver.Resolver."""

    def __init__(self, answers=None, reverse=None, nameservers=("127.0.0.53",)):
        self._answers = answers or {}
        self._reverse = reverse or {}
        self.nameservers = list(nameservers)
        self.lifetime = 1.0

    def resolve(self, name, rdtype="A", **kwargs):
        key = (str(name).rstrip("."), rdtype)
        if key not in self._answers:
            raise LookupError(f"no answer for {key}")
        return self._answers[key]

    def resolve_address(self, address, **kwargs):
        if address not in self._reverse:
            raise LookupError(f"no PTR for {address}")
        return self._reverse[address]


def _hosts(tmp_path, content):
    path = tmp_path / "hosts"
    path.write_text(content)
    return str(path)


def test_read_hosts_file_maps_every_name_to_its_addresses(tmp_path):
    path = _hosts(tmp_path, "127.0.0.1 localhost\n10.0.0.1 node1.example node1  # comment\n")
    mapping = dns_collector.read_hosts_file(path)
    assert mapping["localhost"] == {"127.0.0.1"}
    assert mapping["node1.example"] == {"10.0.0.1"}
    assert mapping["node1"] == {"10.0.0.1"}


def test_read_hosts_file_ignores_comments_and_blank_lines(tmp_path):
    path = _hosts(tmp_path, "\n# only a comment\n   \n")
    assert dns_collector.read_hosts_file(path) == {}


def test_read_hosts_file_survives_a_missing_file():
    assert dns_collector.read_hosts_file("/nonexistent/hosts") == {}


def test_resolver_check_reports_latency(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=[], expectations=[], timeout=1.0,
        resolver=resolver, hosts_path=_hosts(tmp_path, ""),
    )
    resolver_check = [c for c in checks if c.label.startswith("Resolver")][0]
    assert resolver_check.ok is True
    assert "ms" in resolver_check.detail


def test_forward_and_reverse_are_consistent(tmp_path):
    resolver = _StubResolver(
        answers={("node1.example", "A"): ["10.0.0.1"]},
        reverse={"10.0.0.1": ["node1.example."]},
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=[], expectations=[], timeout=1.0,
        resolver=resolver, hosts_path=_hosts(tmp_path, ""),
    )
    own = [c for c in checks if c.label == "own FQDN"][0]
    assert own.ok is True


def test_reverse_pointing_elsewhere_is_a_failure(tmp_path):
    resolver = _StubResolver(
        answers={("node1.example", "A"): ["10.0.0.1"]},
        reverse={"10.0.0.1": ["somebodyelse.example."]},
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=[], expectations=[], timeout=1.0,
        resolver=resolver, hosts_path=_hosts(tmp_path, ""),
    )
    own = [c for c in checks if c.label == "own FQDN"][0]
    assert own.ok is False


def test_all_peers_resolving_is_one_summary_check(tmp_path):
    resolver = _StubResolver(
        answers={
            ("node1.example", "A"): ["10.0.0.1"],
            ("node2.example", "A"): ["10.0.0.2"],
        }
    )
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=["node1.example", "node2.example"],
        expectations=[], timeout=1.0, resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    peers = [c for c in checks if c.label == "Peers"][0]
    assert peers.ok is True
    assert "2/2" in peers.detail


def test_a_peer_that_does_not_resolve_fails_the_summary(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=["node1.example", "ghost.example"],
        expectations=[], timeout=1.0, resolver=resolver,
        hosts_path=_hosts(tmp_path, ""),
    )
    peers = [c for c in checks if c.label == "Peers"][0]
    assert peers.ok is False
    assert "ghost.example" in peers.detail


def test_expectation_with_matching_address_passes(tmp_path):
    resolver = _StubResolver(answers={("login.lmu.de", "A"): ["10.9.9.9"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=[],
        expectations=[("login.lmu.de", ["10.9.9.9"])], timeout=1.0,
        resolver=resolver, hosts_path=_hosts(tmp_path, ""),
    )
    check = [c for c in checks if c.label == "login.lmu.de"][0]
    assert check.ok is True


def test_expectation_with_wrong_address_fails(tmp_path):
    resolver = _StubResolver(answers={("login.lmu.de", "A"): ["10.9.9.9"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=[],
        expectations=[("login.lmu.de", ["10.1.1.1"])], timeout=1.0,
        resolver=resolver, hosts_path=_hosts(tmp_path, ""),
    )
    check = [c for c in checks if c.label == "login.lmu.de"][0]
    assert check.ok is False
    assert "10.9.9.9" in check.detail


def test_hosts_file_diverging_from_dns_is_a_warning_not_a_failure(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=["node1.example"], expectations=[],
        timeout=1.0, resolver=resolver,
        hosts_path=_hosts(tmp_path, "10.0.0.99 node1.example\n"),
    )
    hosts_check = [c for c in checks if c.label == "/etc/hosts"][0]
    assert hosts_check.ok is None, "divergence is deliberate often enough to be a warning"
    assert "node1.example" in hosts_check.detail


def test_hosts_file_agreeing_with_dns_passes(tmp_path):
    resolver = _StubResolver(answers={("node1.example", "A"): ["10.0.0.1"]})
    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=["node1.example"], expectations=[],
        timeout=1.0, resolver=resolver,
        hosts_path=_hosts(tmp_path, "10.0.0.1 node1.example\n"),
    )
    hosts_check = [c for c in checks if c.label == "/etc/hosts"][0]
    assert hosts_check.ok is True


def test_collect_dns_never_raises_when_the_resolver_explodes(tmp_path):
    class _Broken:
        nameservers = ["127.0.0.53"]
        lifetime = 1.0

        def resolve(self, *a, **k):
            raise RuntimeError("resolver on fire")

        def resolve_address(self, *a, **k):
            raise RuntimeError("resolver on fire")

    checks = dns_collector.collect_dns(
        fqdn="node1.example", peer_names=["node1.example"], expectations=[],
        timeout=1.0, resolver=_Broken(), hosts_path=_hosts(tmp_path, ""),
    )
    assert all(check.ok is not True for check in checks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_dns.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.dns'`

- [ ] **Step 3: Write the implementation**

First add the dependency in `pyproject.toml`:

```toml
dependencies = [
    "rich",
    "docker",
    "psutil",
    "distro",
    "dnspython",  # query resolvers directly: getaddrinfo would hide /etc/hosts divergence
]
```

Then:

```python
# src/terminal_status_panel/collectors/dns.py
"""DNS consistency checks.

Uses dnspython rather than ``socket.getaddrinfo`` on purpose: getaddrinfo
consults ``/etc/hosts``, and a divergence between ``/etc/hosts`` and DNS is
precisely the fault this collector exists to surface.

A divergence is reported as a *warning* (``ok=None``), never as a failure —
such overrides are sometimes deliberate here, and crying wolf would train
people to ignore the panel.
"""

from __future__ import annotations

import time

from ..model import DnsCheck


def read_hosts_file(path: str = "/etc/hosts") -> dict[str, set[str]]:
    """Map every name in *path* (lowercased) to the set of addresses it has."""
    mapping: dict[str, set[str]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return mapping
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        parts = entry.split()
        if len(parts) < 2:
            continue
        address, names = parts[0], parts[1:]
        for name in names:
            mapping.setdefault(name.lower(), set()).add(address)
    return mapping


def _addresses(resolver, name: str) -> list[str]:
    """A-record addresses for *name* as plain strings. Raises on failure."""
    return [str(record) for record in resolver.resolve(name, "A")]


def _default_resolver(timeout: float):
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout
    return resolver


def collect_dns(
    fqdn: str,
    peer_names: list[str],
    expectations: list[tuple[str, list[str]]],
    timeout: float,
    resolver=None,
    hosts_path: str = "/etc/hosts",
) -> list[DnsCheck]:
    """Resolver reachability, own name, peers, expectations, /etc/hosts. Never raises."""
    if resolver is None:
        try:
            resolver = _default_resolver(timeout)
        except Exception as exc:
            return [DnsCheck(label="Resolver", ok=False, detail=str(exc)[:60])]

    checks: list[DnsCheck] = []
    seen: dict[str, list[str]] = {}  # name -> DNS answer, for the /etc/hosts comparison

    def resolve(name: str) -> list[str] | None:
        try:
            answer = _addresses(resolver, name)
        except Exception:
            return None
        seen[name.lower()] = answer
        return answer

    # 1. Resolver reachability and latency.
    servers = ", ".join(getattr(resolver, "nameservers", []) or []) or "unknown"
    started = time.monotonic()
    own_addresses = resolve(fqdn)
    elapsed_ms = (time.monotonic() - started) * 1000
    checks.append(
        DnsCheck(
            label=f"Resolver {servers}",
            ok=own_addresses is not None,
            detail=f"{elapsed_ms:.0f} ms" if own_addresses is not None else "no answer",
        )
    )

    # 2. Own FQDN forward and reverse.
    if own_addresses is None:
        checks.append(DnsCheck(label="own FQDN", ok=False, detail="no A record"))
    else:
        names: list[str] = []
        for address in own_addresses:
            try:
                names.extend(str(entry).rstrip(".") for entry in resolver.resolve_address(address))
            except Exception:
                continue
        consistent = fqdn.rstrip(".") in names
        checks.append(
            DnsCheck(
                label="own FQDN",
                ok=consistent,
                detail="A+PTR ok" if consistent else f"PTR: {', '.join(names) or 'missing'}",
            )
        )

    # 3. Peer names.
    if peer_names:
        missing = [name for name in peer_names if resolve(name) is None]
        checks.append(
            DnsCheck(
                label="Peers",
                ok=not missing,
                detail=(
                    f"{len(peer_names) - len(missing)}/{len(peer_names)}"
                    if not missing
                    else f"no answer: {', '.join(missing)}"
                ),
            )
        )

    # 4. Configured expectations.
    for name, expected in expectations:
        answer = resolve(name)
        if answer is None:
            checks.append(DnsCheck(label=name, ok=False, detail="no answer"))
        elif expected and set(answer) != set(expected):
            checks.append(DnsCheck(label=name, ok=False, detail=f"got {', '.join(answer)}"))
        else:
            checks.append(DnsCheck(label=name, ok=True, detail=", ".join(answer)))

    # 5. /etc/hosts against what DNS said, for every name already looked up.
    hosts = read_hosts_file(hosts_path)
    diverging = [
        name
        for name, answer in seen.items()
        if name in hosts and hosts[name] != set(answer)
    ]
    if not seen:
        # Nothing resolved, so there is nothing to compare against. Reporting
        # "matches" here would claim agreement with data we never obtained.
        checks.append(DnsCheck(label="/etc/hosts", ok=None, detail="no data"))
    else:
        checks.append(
            DnsCheck(
                label="/etc/hosts",
                ok=None if diverging else True,
                detail=f"diverges: {', '.join(diverging)}" if diverging else "matches",
            )
        )
    return checks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_dns.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/dns.py tests/test_collectors_dns.py
git add src/terminal_status_panel/collectors/dns.py tests/test_collectors_dns.py pyproject.toml
git commit -m "feat: add DNS consistency checks via dnspython"
```

---

### Task 9: Peer reachability collector

**Files:**
- Create: `src/terminal_status_panel/collectors/network.py`
- Test: `tests/test_collectors_network.py`

**Interfaces:**
- Consumes: `PeerReachability` (Task 2), `read_hosts_file` (Task 8).
- Produces: `parse_wg_dump(dump: str, now: float, hosts: dict[str, set[str]]) -> list[PeerReachability]`, `collect_peers(peer_names: list[str], timeout: float) -> list[PeerReachability]`.

`wg show all dump` emits one line per interface **and** one per peer. The interface line has 5 fields, peer lines have 9 (`interface, public-key, preshared-key, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive`). Field count is the discriminator; using line position would break with more than one interface.

Names come from `/etc/hosts`, not from a reverse lookup: it is free, deterministic, and cannot stall the login.

Thresholds: WireGuard rekeys every 2 minutes under traffic, so a handshake younger than 3 minutes is healthy.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collectors_network.py
from terminal_status_panel.collectors import network

# interface line: 5 fields. peer lines: 9 fields.
WG_DUMP = "\t".join(["wg0", "privkey", "pubkey", "51820", "off"]) + "\n" + "\n".join(
    "\t".join(row)
    for row in [
        ["wg0", "peerA", "(none)", "10.1.0.1:51820", "10.9.0.1/32", "1000", "1", "2", "25"],
        ["wg0", "peerB", "(none)", "10.1.0.2:51820", "10.9.0.2/32", "700", "1", "2", "25"],
        ["wg0", "peerC", "(none)", "(none)", "10.9.0.3/32", "0", "0", "0", "off"],
    ]
)

HOSTS = {"wg-node-a": {"10.9.0.1"}, "wg-node-b": {"10.9.0.2"}}


def test_parse_wg_dump_skips_the_interface_line():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert len(peers) == 3
    assert all(peer.method == "wireguard" for peer in peers)


def test_parse_wg_dump_names_peers_from_the_hosts_file():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[0].name == "wg-node-a"
    assert peers[1].name == "wg-node-b"


def test_parse_wg_dump_falls_back_to_the_tunnel_ip_when_unknown():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[2].name == "10.9.0.3"


def test_recent_handshake_is_healthy():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[0].ok is True
    assert peers[0].detail == "0:00"


def test_handshake_older_than_three_minutes_is_not_ok():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[1].ok is False  # 300s old
    assert peers[1].detail == "5:00"


def test_a_peer_that_never_handshook_is_not_ok():
    peers = network.parse_wg_dump(WG_DUMP, now=1000.0, hosts=HOSTS)
    assert peers[2].ok is False
    assert peers[2].detail == "never"


def test_collect_peers_falls_back_to_tcp_when_sudo_is_refused(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("sudo: a password is required")

    monkeypatch.setattr(network.subprocess, "run", refuse)

    opened = []

    def fake_connection(address, timeout=None):
        opened.append(address)
        if address[0] == "down.example":
            raise OSError("refused")

        class _Socket:
            def close(self):
                pass

        return _Socket()

    monkeypatch.setattr(network.socket, "create_connection", fake_connection)

    peers = network.collect_peers(["up.example", "down.example"], timeout=1.0)
    assert [peer.method for peer in peers] == ["tcp", "tcp"]
    assert peers[0].ok is True
    assert peers[1].ok is False
    assert opened == [("up.example", 2377), ("down.example", 2377)]


def test_collect_peers_prefers_wireguard_when_sudo_works(monkeypatch):
    class _Completed:
        returncode = 0
        stdout = WG_DUMP

    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(network, "read_hosts_file", lambda *a, **k: HOSTS)
    peers = network.collect_peers(["ignored.example"], timeout=1.0)
    assert [peer.method for peer in peers] == ["wireguard"] * 3


def test_collect_peers_never_raises(monkeypatch):
    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(network.socket, "create_connection", lambda *a, **k: 1 / 0)
    assert network.collect_peers(["x.example"], timeout=1.0)[0].ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_network.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.network'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/collectors/network.py
"""Peer reachability at the layer the overlay networks actually run on.

The Swarm view can report a node ``ready`` while the WireGuard tunnel carrying
its overlay traffic is stale, so ``wg show all dump`` is the primary source.
Without passwordless sudo it degrades to a TCP probe rather than to a silent
gap — the method is carried in the result so the reader knows which claim is
being made.
"""

from __future__ import annotations

import socket
import subprocess
import time

from ..model import PeerReachability
from .dns import read_hosts_file

SWARM_PORT = 2377
# WireGuard rekeys every ~2 minutes under traffic; 3 minutes is stale.
HANDSHAKE_STALE_SECONDS = 180
WG_PEER_FIELDS = 9


def _format_age(seconds: float) -> str:
    minutes, remainder = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{remainder:02d}"


def parse_wg_dump(dump: str, now: float, hosts: dict[str, set[str]]) -> list[PeerReachability]:
    """Parse ``wg show all dump``.

    Peer lines carry 9 tab-separated fields, the per-interface line only 5;
    the field count is the discriminator, because there may be several
    interfaces and position alone would not be reliable.
    """
    address_to_name: dict[str, str] = {}
    for name, addresses in hosts.items():
        for address in addresses:
            address_to_name.setdefault(address, name)

    peers: list[PeerReachability] = []
    for line in dump.splitlines():
        fields = line.split("\t")
        if len(fields) != WG_PEER_FIELDS:
            continue
        allowed_ips, handshake = fields[4], fields[5]
        tunnel_ip = allowed_ips.split("/", 1)[0].split(",")[0].strip()
        try:
            last = float(handshake)
        except ValueError:
            last = 0.0
        if last <= 0:
            ok, detail = False, "never"
        else:
            age = now - last
            ok, detail = age < HANDSHAKE_STALE_SECONDS, _format_age(age)
        peers.append(
            PeerReachability(
                name=address_to_name.get(tunnel_ip, tunnel_ip),
                method="wireguard",
                ok=ok,
                detail=detail,
            )
        )
    return peers


def _wg_dump(timeout: float) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["sudo", "-n", "wg", "show", "all", "dump"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise OSError(completed.stdout.strip()[:120] or "wg failed")
    return completed.stdout


def _tcp_probe(peer_names: list[str], timeout: float) -> list[PeerReachability]:
    """Fallback when sudo is unavailable: does the Swarm port accept a connection."""
    per_peer = max(0.1, timeout / max(1, len(peer_names)))
    peers: list[PeerReachability] = []
    for name in peer_names:
        try:
            connection = socket.create_connection((name, SWARM_PORT), timeout=per_peer)
            connection.close()
            ok, detail = True, f"tcp/{SWARM_PORT}"
        except Exception:
            ok, detail = False, f"tcp/{SWARM_PORT} closed"
        peers.append(PeerReachability(name=name, method="tcp", ok=ok, detail=detail))
    return peers


def collect_peers(peer_names: list[str], timeout: float) -> list[PeerReachability]:
    """WireGuard handshake ages, or a TCP probe when sudo is unavailable. Never raises."""
    try:
        dump = _wg_dump(timeout)
        peers = parse_wg_dump(dump, now=time.time(), hosts=read_hosts_file())
        if peers:
            return peers
    except Exception:
        pass  # no sudo, no wg, no peers: fall through to the weaker answer
    try:
        return _tcp_probe(peer_names, timeout)
    except Exception:
        return [
            PeerReachability(name=name, method="tcp", ok=False, detail="probe failed")
            for name in peer_names
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_network.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/network.py tests/test_collectors_network.py
git add src/terminal_status_panel/collectors/network.py tests/test_collectors_network.py
git commit -m "feat: check peer reachability via WireGuard with a TCP fallback"
```

---

### Task 10: Health configuration

**Files:**
- Modify: `src/terminal_status_panel/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `DnsExpectation(name, addresses)`, `HealthConfig(budget, timeouts, enabled, dns_expect)`, and `Config.health: HealthConfig`. Task 11 reads `cfg.health`, Task 12 passes it to the collectors.

Every key has a default that produces sensible behaviour, so a cluster that configures nothing still gets the full section — consistent with how `load_config` already treats a missing file.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py  (append)
from terminal_status_panel.config import DEFAULT_HEALTH_KINDS, load_config


def _write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return str(path)


def test_health_defaults_without_a_config_file():
    health = load_config("/nonexistent/config.toml").health
    assert health.budget == 5.0
    assert health.enabled == list(DEFAULT_HEALTH_KINDS)
    assert health.timeouts["kafka"] == 4.0
    assert health.timeouts["postgres"] == 1.5
    assert health.dns_expect == []


def test_health_budget_and_timeouts_are_overridable(tmp_path):
    path = _write(tmp_path, """
[health]
budget = 8.5

[health.timeout]
kafka = 6.0
""")
    health = load_config(path).health
    assert health.budget == 8.5
    assert health.timeouts["kafka"] == 6.0
    # untouched keys keep their defaults
    assert health.timeouts["postgres"] == 1.5


def test_enabled_kinds_can_be_narrowed(tmp_path):
    path = _write(tmp_path, """
[health]
enabled = ["postgres", "glusterfs"]
""")
    assert load_config(path).health.enabled == ["postgres", "glusterfs"]


def test_dns_expectations_are_parsed(tmp_path):
    path = _write(tmp_path, """
[[health.dns.expect]]
name = "login.lmu.de"
addresses = ["10.9.9.9"]

[[health.dns.expect]]
name = "www.portal.uni-muenchen.de"
""")
    expectations = load_config(path).health.dns_expect
    assert [e.name for e in expectations] == ["login.lmu.de", "www.portal.uni-muenchen.de"]
    assert expectations[0].addresses == ["10.9.9.9"]
    assert expectations[1].addresses == []


def test_a_broken_health_block_falls_back_to_defaults_instead_of_raising(tmp_path):
    path = _write(tmp_path, """
[health]
budget = "not a number"
""")
    assert load_config(path).health.budget == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_HEALTH_KINDS'`

- [ ] **Step 3: Write the implementation**

Add to `config.py`:

```python
DEFAULT_HEALTH_KINDS = ("postgres", "mongodb", "kafka", "glusterfs", "rustfs")

# Individual timeouts, all below the total budget so one slow check cannot
# starve the others. Kafka is the expensive one: ~2.6s of JVM startup.
DEFAULT_HEALTH_TIMEOUTS = {
    "postgres": 1.5,
    "mongodb": 2.5,
    "kafka": 4.0,
    "glusterfs": 1.0,
    "rustfs": 2.0,
    "wireguard": 1.0,
    "dns": 2.5,
}


@dataclass
class DnsExpectation:
    name: str
    addresses: list[str] = field(default_factory=list)


@dataclass
class HealthConfig:
    budget: float = 5.0
    timeouts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_HEALTH_TIMEOUTS)
    )
    enabled: list[str] = field(default_factory=lambda: list(DEFAULT_HEALTH_KINDS))
    dns_expect: list[DnsExpectation] = field(default_factory=list)
```

Extend `Config` with `health: HealthConfig = field(default_factory=HealthConfig)`.

Add a parser helper and call it from `load_config` (before building the `Config`):

```python
def _health_config(data: dict) -> HealthConfig:
    """Parse the [health] block. A malformed value falls back to its default."""
    health = _section(data, "health")
    defaults = HealthConfig()

    try:
        budget = float(health.get("budget", defaults.budget))
    except (TypeError, ValueError):
        budget = defaults.budget

    timeouts = dict(DEFAULT_HEALTH_TIMEOUTS)
    for key, value in _section(data, "health", "timeout").items():
        try:
            timeouts[key] = float(value)
        except (TypeError, ValueError):
            continue

    enabled = health.get("enabled")
    kinds = list(enabled) if isinstance(enabled, list) else list(DEFAULT_HEALTH_KINDS)

    expectations = []
    raw_expectations = _section(data, "health", "dns").get("expect", [])
    if isinstance(raw_expectations, list):
        for entry in raw_expectations:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            addresses = entry.get("addresses", [])
            expectations.append(
                DnsExpectation(
                    name=str(entry["name"]),
                    addresses=list(addresses) if isinstance(addresses, list) else [],
                )
            )

    return HealthConfig(
        budget=budget, timeouts=timeouts, enabled=kinds, dns_expect=expectations
    )
```

In `load_config`, add `health=_health_config(data)` to the returned `Config(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/config.py tests/test_config.py
git add src/terminal_status_panel/config.py tests/test_config.py
git commit -m "feat: add the [health] configuration block"
```

---

### Task 11: Health collector orchestration

**Files:**
- Create: `src/terminal_status_panel/collectors/health.py`
- Test: `tests/test_collectors_health.py`

**Interfaces:**
- Consumes: `run_with_budget` (Task 1), `collect_clusters` (Tasks 3–7), `collect_peers` (Task 9), `collect_dns` (Task 8), `HealthConfig` (Task 10).
- Produces: `collect_health(cfg, fqdn, peer_names, client=None) -> HealthInfo`. Task 13 calls this from `cli.collect_all`.

This is where the two guarantees from the Global Constraints become real: a check that hit the budget lands in `HealthInfo.truncated`, a check that raised lands in the corresponding dataclass with `error` set, and the two never mix.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collectors_health.py
from terminal_status_panel.collectors import health as health_collector
from terminal_status_panel.config import Config, HealthConfig
from terminal_status_panel.model import ClusterService, DnsCheck, PeerReachability


def _config(**kwargs):
    cfg = Config()
    cfg.health = HealthConfig(**kwargs)
    return cfg


def test_collect_health_gathers_all_three_groups(monkeypatch):
    monkeypatch.setattr(
        health_collector, "collect_clusters",
        lambda client, kinds: [ClusterService(kind="postgres", reachable=True)],
    )
    monkeypatch.setattr(
        health_collector, "collect_peers",
        lambda names, timeout: [PeerReachability(name="ccn-01", method="wireguard", ok=True)],
    )
    monkeypatch.setattr(
        health_collector, "collect_dns",
        lambda **kwargs: [DnsCheck(label="Resolver", ok=True, detail="3 ms")],
    )
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=["ccn-01"], client=object()
    )
    assert [service.kind for service in health.clusters] == ["postgres"]
    assert health.peers[0].name == "ccn-01"
    assert health.dns[0].label == "Resolver"
    assert health.truncated == []


def test_a_check_that_exceeds_the_budget_is_truncated_not_failed(monkeypatch):
    import time

    def slow(*args, **kwargs):
        time.sleep(5)
        return []

    monkeypatch.setattr(health_collector, "collect_clusters", slow)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(budget=0.3), fqdn="node.example", peer_names=[], client=object()
    )
    assert "clusters" in health.truncated
    assert health.clusters == []


def test_a_raising_check_becomes_an_error_entry_not_a_truncation(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(health_collector, "collect_clusters", boom)
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(health_collector, "collect_dns", lambda **kwargs: [])
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=[], client=object()
    )
    assert health.truncated == []
    assert len(health.clusters) == 1
    assert "kaputt" in health.clusters[0].error


def test_no_docker_client_still_yields_network_and_dns(monkeypatch):
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])
    monkeypatch.setattr(
        health_collector, "collect_dns", lambda **kwargs: [DnsCheck(label="Resolver", ok=True)]
    )
    health = health_collector.collect_health(
        _config(), fqdn="node.example", peer_names=[], client=None
    )
    assert health.clusters == []
    assert health.dns[0].label == "Resolver"


def test_collect_health_passes_the_configured_dns_expectations(monkeypatch):
    from terminal_status_panel.config import DnsExpectation

    captured = {}

    monkeypatch.setattr(health_collector, "collect_clusters", lambda client, kinds: [])
    monkeypatch.setattr(health_collector, "collect_peers", lambda names, timeout: [])

    def capture(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(health_collector, "collect_dns", capture)
    health_collector.collect_health(
        _config(dns_expect=[DnsExpectation(name="login.lmu.de", addresses=["10.9.9.9"])]),
        fqdn="node.example", peer_names=["ccn-01"], client=object(),
    )
    assert captured["expectations"] == [("login.lmu.de", ["10.9.9.9"])]
    assert captured["peer_names"] == ["ccn-01"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_collectors_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.health'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/collectors/health.py
"""Run the health checks concurrently and assemble HealthInfo.

The one place where the budget meets the collectors. A check that ran out of
budget lands in ``HealthInfo.truncated``; a check that raised lands in its own
dataclass with ``error`` set. Keeping those apart is the whole point.
"""

from __future__ import annotations

from ..budget import run_with_budget
from ..config import Config
from ..model import ClusterService, DnsCheck, HealthInfo, PeerReachability
from .clusters import collect_clusters
from .dns import collect_dns
from .network import collect_peers


def collect_health(
    cfg: Config, fqdn: str, peer_names: list[str], client=None
) -> HealthInfo:
    """Collect cluster, peer and DNS health under the configured budget."""
    health_cfg = cfg.health
    tasks = {}

    if client is not None and health_cfg.enabled:
        tasks["clusters"] = lambda: collect_clusters(client, list(health_cfg.enabled))

    tasks["peers"] = lambda: collect_peers(
        peer_names, timeout=health_cfg.timeouts.get("wireguard", 1.0)
    )
    tasks["dns"] = lambda: collect_dns(
        fqdn=fqdn,
        peer_names=peer_names,
        expectations=[(e.name, list(e.addresses)) for e in health_cfg.dns_expect],
        timeout=health_cfg.timeouts.get("dns", 2.5),
    )

    outcome = run_with_budget(tasks, budget=health_cfg.budget)

    clusters: list[ClusterService] = list(outcome.results.get("clusters") or [])
    peers: list[PeerReachability] = list(outcome.results.get("peers") or [])
    dns: list[DnsCheck] = list(outcome.results.get("dns") or [])

    # A raised exception is a statement about the check; a blown budget is not.
    for name, message in outcome.failed.items():
        if name == "clusters":
            clusters.append(ClusterService(kind="clusters", error=message))
        elif name == "peers":
            peers.append(PeerReachability(name="?", method="tcp", ok=False, detail=message))
        elif name == "dns":
            dns.append(DnsCheck(label="DNS", ok=False, detail=message))

    return HealthInfo(
        clusters=clusters, peers=peers, dns=dns, truncated=list(outcome.truncated)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_collectors_health.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/collectors/health.py tests/test_collectors_health.py
git add src/terminal_status_panel/collectors/health.py tests/test_collectors_health.py
git commit -m "feat: assemble the health section under one budget"
```

---

### Task 12: Render the CLUSTER HEALTH section

**Files:**
- Create: `src/terminal_status_panel/render/health.py`
- Test: `tests/test_render_health.py`

**Interfaces:**
- Consumes: `HealthInfo`, `ClusterService`, `ClusterMember`, `PeerReachability`, `DnsCheck` (Task 2), `Config` (Task 10).
- Produces: `health_section(health: HealthInfo | None, cfg: Config) -> RenderableType`. Task 13 wires it into the layout.

Icon vocabulary, extending the panel's existing ✅ / ⚠️ / 💀:

| Icon | Meaning |
|---|---|
| ✅ | measured healthy |
| ⚠️ | warning: lag, mid-transition, `/etc/hosts` divergence, stale handshake |
| 💀 | measured broken |
| `·` | not observable (`healthy is None`) — the panel must not claim what it did not measure |
| `…` | ran out of budget |
| `n/a` | not applicable on this node |

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_health.py
from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ClusterMember,
    ClusterService,
    DnsCheck,
    HealthInfo,
    PeerReachability,
)
from terminal_status_panel.render.health import health_section


def _render(health, width=120):
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(health_section(health, Config()))
    return capture.get()


def test_missing_health_renders_a_placeholder_not_a_crash():
    assert "CLUSTER HEALTH" in _render(None)


def test_unprobed_clusters_are_not_rendered_as_all_clear():
    output = _render(HealthInfo(clusters_probed=False))
    assert "not checked" in output
    assert "no clustered services found" not in output


def test_probed_but_empty_clusters_say_so():
    output = _render(HealthInfo(clusters_probed=True))
    assert "no clustered services found" in output
    assert "not checked" not in output


def test_healthy_cluster_shows_leader_and_members():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="postgres", name="PostgreSQL-18", reachable=True,
            leader="pg18-lmzvd06-ccn-02", quorum_ok=True,
            members=[
                ClusterMember(name="pg18-lmzvd06-ccn-02", node="lmzvd06-ccn-02",
                              role="primary", healthy=True),
                ClusterMember(name="pg18-lmzvd06-ccn-03", node="lmzvd06-ccn-03",
                              role="secondary", healthy=True),
            ],
        )
    ])
    output = _render(health)
    assert "PostgreSQL-18" in output
    assert "lmzvd06-ccn-02" in output
    assert "primary" in output
    assert "✅" in output


def test_not_applicable_service_renders_na_and_no_failure_icon():
    health = HealthInfo(clusters_probed=True, clusters=[ClusterService(kind="mongodb", applicable=False)])
    output = _render(health)
    assert "mongodb" in output.lower()
    assert "n/a" in output
    assert "💀" not in output


def test_unobservable_member_health_renders_a_neutral_dot():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="mongodb", name="lrz_app", reachable=True, quorum_ok=True,
            members=[ClusterMember(name="mongodb-4:27017", role="member", healthy=None)],
        )
    ])
    output = _render(health)
    assert "·" in output
    assert "✅ mongodb-4" not in output


def test_member_warning_is_visible():
    health = HealthInfo(clusters_probed=True, clusters=[
        ClusterService(
            kind="postgres", name="PostgreSQL-18", reachable=True,
            members=[ClusterMember(name="pg18-x", role="secondary", healthy=True,
                                   warning="lag")],
        )
    ])
    assert "lag" in _render(health)


def test_errored_service_shows_the_failure_marker_and_message():
    health = HealthInfo(clusters_probed=True, clusters=[ClusterService(kind="kafka", error="connection refused")])
    output = _render(health)
    assert "✗" in output
    assert "connection refused" in output


def test_truncated_check_renders_ellipsis_not_a_failure():
    health = HealthInfo(clusters_probed=True, truncated=["clusters"])
    output = _render(health)
    assert "…" in output
    assert "✗" not in output


def test_peer_panel_shows_method_and_handshake_age():
    health = HealthInfo(peers=[
        PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31"),
        PeerReachability(name="ccn-02", method="wireguard", ok=False, detail="6:02"),
    ])
    output = _render(health)
    assert "wg" in output.lower()
    assert "0:31" in output
    assert "6:02" in output


def test_tcp_fallback_is_labelled_as_such():
    health = HealthInfo(peers=[
        PeerReachability(name="ccn-01", method="tcp", ok=True, detail="tcp/2377")
    ])
    assert "tcp" in _render(health).lower()


def test_dns_warning_renders_as_warning_not_failure():
    health = HealthInfo(dns=[DnsCheck(label="/etc/hosts", ok=None, detail="diverges: a")])
    output = _render(health)
    assert "⚠" in output
    assert "💀" not in output


def test_narrow_width_still_renders():
    health = HealthInfo(
        clusters=[ClusterService(kind="postgres", name="PostgreSQL-18", reachable=True)],
        peers=[PeerReachability(name="ccn-01", method="wireguard", ok=True, detail="0:31")],
        dns=[DnsCheck(label="Resolver", ok=True, detail="3 ms")],
    )
    assert "CLUSTER HEALTH" in _render(health, width=60)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_render_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.render.health'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/render/health.py
"""Render the CLUSTER HEALTH section.

Icon vocabulary, extending the panel's existing scheme:

  ✅ measured healthy      ⚠️ warning        💀 measured broken
  ·  not observable        … out of budget   n/a not applicable

The neutral dot matters: MongoDB reports its set members but not their state,
and a panel that renders an unmeasured ✅ is worse than one that says nothing.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import Config
from ..model import ClusterService, DnsCheck, HealthInfo, PeerReachability

OK = "✅"
WARN = "⚠️"
DEAD = "💀"
UNKNOWN = "·"
TRUNCATED = "…"
FAILED = "✗"

_KIND_TITLES = {
    "postgres": "PostgreSQL",
    "mongodb": "MongoDB",
    "kafka": "Kafka (KRaft)",
    "glusterfs": "GlusterFS",
    "rustfs": "RustFS",
}


def _tri_state(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return OK if value else DEAD


def _service_lines(service: ClusterService) -> Text:
    title = _KIND_TITLES.get(service.kind, service.kind)
    if service.name:
        title = f"{title} {service.name}"

    if not service.applicable:
        return Text(f"{title}: n/a here", style="dim")
    if service.error:
        return Text(f"{FAILED} {title}: {service.error}", style="red")

    text = Text()
    header_icon = _tri_state(service.quorum_ok) if service.quorum_ok is not None else UNKNOWN
    header = f"{header_icon} {title}"
    if service.detail:
        header += f" — {service.detail}"
    text.append(header + "\n", style="bold")
    if service.leader:
        text.append(f"   leader   {service.leader}\n")
    for member in service.members:
        icon = _tri_state(member.healthy)
        label = member.node or member.name
        line = f"   {icon} {label}"
        if member.role:
            line += f"  {member.role}"
        if member.warning:
            line += f"  {WARN} {member.warning}"
        text.append(line + "\n")
    return text


def _clusters_panel(health: HealthInfo) -> Panel:
    if "clusters" in health.truncated:
        body: RenderableType = Text(f"{TRUNCATED} time budget exceeded", style="dim")
    elif not health.clusters_probed:
        # Never ran: no Docker client, or every kind disabled. Rendering this as
        # "nothing found" would report a gap in coverage as a clean bill of health.
        body = Text(f"{UNKNOWN} not checked (no Docker client)", style="dim")
    elif not health.clusters:
        body = Text("no clustered services found", style="dim")
    else:
        body = Group(*[_service_lines(service) for service in health.clusters])
    return Panel(body, title="INFRASTRUKTUR-DIENSTE", border_style="blue")


def _peers_panel(health: HealthInfo) -> Panel:
    if "peers" in health.truncated:
        body: RenderableType = Text(f"{TRUNCATED} time budget exceeded", style="dim")
    elif not health.peers:
        body = Text("no peers detected", style="dim")
    else:
        table = Table.grid(padding=(0, 2))
        table.add_column()
        table.add_column()
        for peer in health.peers:
            table.add_row(
                Text(f"{OK if peer.ok else WARN} {peer.name}"),
                Text(peer.detail or "", style="dim"),
            )
        body = table
    method = health.peers[0].method if health.peers else "wg"
    label = "wg" if method == "wireguard" else "tcp"
    return Panel(body, title=f"NETZ ({label})", border_style="blue")


def _dns_panel(health: HealthInfo) -> Panel:
    if "dns" in health.truncated:
        body: RenderableType = Text(f"{TRUNCATED} time budget exceeded", style="dim")
    elif not health.dns:
        body = Text("no DNS checks", style="dim")
    else:
        table = Table.grid(padding=(0, 2))
        table.add_column()
        table.add_column()
        for check in health.dns:
            icon = WARN if check.ok is None else (OK if check.ok else DEAD)
            table.add_row(Text(f"{icon} {check.label}"), Text(check.detail, style="dim"))
        body = table
    return Panel(body, title="DNS", border_style="blue")


def _bottom_row(health: HealthInfo) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(_peers_panel(health), _dns_panel(health))
    return grid


def health_section(health: HealthInfo | None, cfg: Config) -> RenderableType:
    """The CLUSTER HEALTH block: infrastructure services over network and DNS."""
    data = health or HealthInfo()
    return Group(
        Text("CLUSTER HEALTH", style="bold blue"),
        _clusters_panel(data),
        _bottom_row(data),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_render_health.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/terminal_status_panel/render/health.py tests/test_render_health.py
git add src/terminal_status_panel/render/health.py tests/test_render_health.py
git commit -m "feat: render the CLUSTER HEALTH section"
```

---

### Task 13: Wire the section into layout, CLI and packaging

**Files:**
- Modify: `src/terminal_status_panel/render/layout.py:25` (`SECTIONS`) and the builder registry
- Modify: `src/terminal_status_panel/cli.py` (`collect_all`, new `health_main`)
- Modify: `pyproject.toml` (`status-health` console script)
- Test: `tests/test_render_layout.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `health_section` (Task 12), `collect_health` (Task 11).
- Produces: `"health"` in `layout.SECTIONS`, `cli.health_main(argv=None) -> int`, console script `status-health`.

`collect_all` must gather the two inputs `collect_health` needs — the host FQDN and the peer names — without making the health section depend on the docker section being selected. It reads the FQDN from `socket.getfqdn()` and derives peer names from the Swarm node list when it has one, falling back to an empty list.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_layout.py  (append)
from terminal_status_panel.config import Config
from terminal_status_panel.model import HealthInfo, PanelData
from terminal_status_panel.render.layout import SECTIONS, build_layout


def test_health_is_a_known_section():
    assert "health" in SECTIONS


def test_build_layout_renders_only_the_requested_section():
    from rich.console import Console

    data = PanelData(health=HealthInfo())
    console = Console(width=100, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(build_layout(data, Config(), sections=("health",)))
    output = capture.get()
    assert "CLUSTER HEALTH" in output
    assert "DOCKER" not in output
```

```python
# tests/test_cli.py  (append)
import pytest

from terminal_status_panel import cli
from terminal_status_panel.config import Config


@pytest.fixture
def isolated_cli(monkeypatch):
    """Keep the CLI tests off the real Docker socket and the real system.

    ``collect_all`` builds a Docker client for the health section; without this
    the unit tests would talk to whatever daemon happens to run on the machine.
    """
    monkeypatch.setattr(cli, "_docker_client", lambda cfg: None)
    monkeypatch.setattr(cli, "collect_system", lambda: None)
    monkeypatch.setattr(cli, "collect_resources", lambda: None)
    monkeypatch.setattr(cli, "collect_updates", lambda timeout=None: None)
    return monkeypatch


def test_health_main_returns_zero(isolated_cli):
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: None)
    assert cli.health_main([]) == 0


def test_collect_all_skips_health_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []


def test_collect_all_calls_health_when_selected(isolated_cli):
    called = []

    def fake(cfg, fqdn, peer_names, client=None):
        called.append((fqdn, peer_names))
        return None

    isolated_cli.setattr(cli, "collect_health", fake)
    cli.collect_all(Config(), sections=("health",))
    assert len(called) == 1


def test_main_never_propagates_a_collector_explosion(isolated_cli):
    def boom(*a, **k):
        raise RuntimeError("kaputt")

    isolated_cli.setattr(cli, "collect_health", boom)
    assert cli.main(["--sections", "health"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_render_layout.py tests/test_cli.py -v`
Expected: FAIL — `AssertionError: 'health' not in SECTIONS`

- [ ] **Step 3: Write the implementation**

In `render/layout.py`, extend the import, the constant and the registry:

```python
from .health import health_section
from .panels import services_section, system_overview, system_status, updates_panel

SECTIONS: tuple[str, ...] = ("server", "docker", "health")
```

```python
def health_block(data: PanelData, cfg: Config) -> RenderableType:
    """The CLUSTER HEALTH block."""
    return health_section(data.health, cfg)


_SECTION_BUILDERS = {
    "server": server_section,
    "docker": docker_section,
    "health": health_block,
}
```

In `cli.py`, add the imports and extend `collect_all`:

```python
import socket

from .collectors.health import collect_health
```

```python
def _docker_client(cfg: Config):
    """A Docker client for the health probes, or None when unavailable."""
    try:
        import docker

        return docker.from_env(timeout=cfg.docker_timeout)
    except Exception:
        return None


def _peer_names(swarm) -> list[str]:
    return [node.name for node in getattr(swarm, "nodes", []) or []]


def collect_all(cfg: Config, sections: tuple[str, ...] = SECTIONS) -> PanelData:
    """Collect only the data required by the requested sections."""
    server = "server" in sections
    docker_section = "docker" in sections
    health = "health" in sections

    swarm = (
        collect_docker(
            timeout=cfg.docker_timeout,
            critical=cfg.critical_services,
            description_label=cfg.description_label,
        )
        if docker_section
        else None
    )

    health_info = None
    if health:
        client = _docker_client(cfg)
        peers = _peer_names(swarm)
        if not peers and client is not None:
            # The health section must not depend on the docker section being
            # selected, so fetch the node list on its own when needed.
            try:
                peers = [
                    (node.attrs.get("Description", {}) or {}).get("Hostname", "")
                    for node in client.nodes.list()
                ]
                peers = [name for name in peers if name]
            except Exception:
                peers = []
        health_info = collect_health(
            cfg, fqdn=socket.getfqdn(), peer_names=peers, client=client
        )

    return PanelData(
        system=collect_system() if server else None,
        resources=collect_resources() if server else None,
        updates=collect_updates(timeout=cfg.docker_timeout) if server else None,
        swarm=swarm,
        health=health_info,
    )
```

Add the entry point next to `docker_main`:

```python
def health_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-health`` — cluster health section only."""
    return main(argv, sections=("health",), prog="status-health")
```

In `pyproject.toml`:

```toml
[project.scripts]
status-full = "terminal_status_panel.cli:main"
status-server = "terminal_status_panel.cli:server_main"
status-docker = "terminal_status_panel.cli:docker_main"
status-health = "terminal_status_panel.cli:health_main"
install-panel = "terminal_status_panel.install:main"
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS — all tests, including the pre-existing ones

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/terminal_status_panel/render/layout.py src/terminal_status_panel/cli.py \
        pyproject.toml tests/test_render_layout.py tests/test_cli.py
git commit -m "feat: wire the health section into layout, CLI and packaging"
```

---

### Task 14: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything built so far. Produces no code.

The README currently claims: *"All Docker data is read from the Docker API only — no database or broker protocol is ever spoken to."* With this section that sentence is misleading and must be corrected rather than left standing — the accurate claim is narrower and worth stating precisely.

- [ ] **Step 1: Correct the "Docker API only" claim**

Replace that paragraph (currently `README.md:22-23`) with:

```markdown
The panel itself opens no database or broker connection and holds no
credentials. Its only privilege is the Docker socket: the Docker section reads
the Swarm API, and the health section additionally executes **read-only status
commands inside the service containers** (`pg_autoctl show state`,
`db.hello()`, `kafka-metadata-quorum.sh`). GlusterFS is queried on the host via
`sudo -n`, and is skipped when that is unavailable.
```

- [ ] **Step 2: Document the new section and command**

Add `health` to the section table and the commands table:

```markdown
| `status-health` | health only | Clustered infrastructure services, WireGuard peers, DNS. |
```

Extend the intro list with:

```markdown
- **CLUSTER HEALTH** — the clustered infrastructure services (PostgreSQL,
  MongoDB, Kafka, GlusterFS, RustFS) with leader and member state, WireGuard
  peer handshake ages, and DNS consistency checks. Every check runs under a
  shared time budget (default 5 s); a check that runs out renders `…`, which is
  deliberately distinct from `✗` for a check that failed. A service with no
  member on this node renders `n/a here` and is not an error.
```

- [ ] **Step 3: Document the configuration**

Add to the configuration reference table:

```markdown
| `health.budget` | `5.0` | Total wall-clock budget in seconds for all health checks. All checks run concurrently, so this bounds the login delay — it is not the sum of the individual timeouts. |
| `health.timeout.*` | postgres `1.5`, mongodb `2.5`, kafka `4.0`, glusterfs `1.0`, rustfs `2.0`, wireguard `1.0`, dns `2.5` | Individual timeouts, all below the budget. Kafka is the expensive one (~2.6 s of JVM startup). |
| `health.enabled` | all five kinds | Which cluster kinds to probe. |
| `health.dns.expect` | `[]` | Array of `{name, addresses}`. `addresses` is optional; without it the name only has to resolve at all. |
```

And a full example block:

```toml
[health]
budget = 5.0
enabled = ["postgres", "mongodb", "kafka", "glusterfs", "rustfs"]

[health.timeout]
postgres = 1.5
mongodb = 2.5
kafka = 4.0
glusterfs = 1.0
rustfs = 2.0
wireguard = 1.0
dns = 2.5

[[health.dns.expect]]
name = "login.lmu.de"
addresses = ["10.9.9.9"]
```

- [ ] **Step 4: Verify the README has no stale claims**

Run: `grep -n "no database or broker protocol" README.md`
Expected: no matches

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the health section and correct the Docker-API-only claim"
```

---

### Task 15: Ansible role

**Files (repo `ansible-app-server`, separate checkout and separate branch):**
- Modify: `roles/status_panel/defaults/main.yml`
- Modify: `roles/status_panel/templates/config.toml.j2`
- Modify: `roles/status_panel/meta/argument_specs.yml`
- Modify: `roles/status_panel/README.md`

**Interfaces:**
- Consumes: the `[health]` configuration contract from Task 10.
- Produces: no code the package depends on.

A cluster that sets no DNS expectation list must behave exactly as today. Comments stay in the existing German, ASCII-only style used by the role.

- [ ] **Step 1: Create a branch in the ansible repo**

```bash
cd ../ansible-app-server
git checkout -b feature/status-panel-health
```

- [ ] **Step 2: Add the defaults**

Append to `roles/status_panel/defaults/main.yml`:

```yaml
# --- CLUSTER-HEALTH-Sektion ---------------------------------------------
# Gesamtbudget in Sekunden fuer alle Health-Checks. Sie laufen nebenlaeufig,
# der Wert begrenzt also die Login-Verzoegerung, nicht die Summe der
# Einzeltimeouts.
status_panel_health_budget: 5.0

# Welche geclusterten Dienste geprueft werden. Ein Dienst ohne lokales
# Mitglied meldet "n/a", das ist kein Fehler.
status_panel_health_enabled:
  - postgres
  - mongodb
  - kafka
  - glusterfs
  - rustfs

# Einzeltimeouts, alle unterhalb des Gesamtbudgets. Kafka ist der teure
# Check (~2,6 s JVM-Start), deshalb der hohe Wert.
status_panel_health_timeouts:
  postgres: 1.5
  mongodb: 2.5
  kafka: 4.0
  glusterfs: 1.0
  rustfs: 2.0
  wireguard: 1.0
  dns: 2.5

# Optionale DNS-Erwartungen: Liste von {name, addresses}. addresses ist
# optional; ohne Angabe muss der Name nur ueberhaupt aufloesen.
# Default [] -> fuer alle bestehenden Cluster ein No-Op.
status_panel_health_dns_expect: []
```

Also bump the pinned version once the package is released:

```yaml
status_panel_version: "0.3.0"
```

- [ ] **Step 3: Extend the config template**

Append to `roles/status_panel/templates/config.toml.j2`:

```jinja
[health]
# Gesamtbudget fuer alle Health-Checks (nebenlaeufig).
budget = {{ status_panel_health_budget }}
enabled = {{ status_panel_health_enabled | to_json }}

[health.timeout]
{% for name, seconds in status_panel_health_timeouts.items() %}
{{ name }} = {{ seconds }}
{% endfor %}

{% for entry in status_panel_health_dns_expect %}
[[health.dns.expect]]
name = "{{ entry.name }}"
addresses = {{ (entry.addresses | default([])) | to_json }}
{% endfor %}
```

- [ ] **Step 4: Document the arguments**

Add to `roles/status_panel/meta/argument_specs.yml`, under the existing options:

```yaml
    status_panel_health_budget:
      type: float
      default: 5.0
      description: Gesamtbudget in Sekunden fuer alle Health-Checks.
    status_panel_health_enabled:
      type: list
      elements: str
      default: [postgres, mongodb, kafka, glusterfs, rustfs]
      description: Gepruefte geclusterte Dienste.
    status_panel_health_timeouts:
      type: dict
      description: Einzeltimeouts je Check, alle unterhalb des Gesamtbudgets.
    status_panel_health_dns_expect:
      type: list
      elements: dict
      default: []
      description: DNS-Erwartungen als Liste von {name, addresses}.
```

- [ ] **Step 5: Verify the template renders and commit**

```bash
ansible-lint roles/status_panel
git add roles/status_panel
git commit -m "feat(status_panel): Konfiguration fuer die CLUSTER-HEALTH-Sektion"
```

Then deploy to one node and confirm by hand:

```bash
ansible-playbook site.yml --limit lmzvd06-ccc-01.srv.mwn.de --tags status_panel
ssh loechel@lmzvd06-ccc-01.srv.mwn.de 'time status-health'
```

Expected: the section renders, and the elapsed time stays under the configured budget.

---

## Self-Review

**Spec coverage.** Every section of the design maps to a task: architecture and the budget module → Task 1; data model → Task 2; the five cluster probes → Tasks 3–7; DNS including dnspython → Task 8; peer reachability with the TCP fallback → Task 9; configuration → Task 10; the failure semantics (`truncated` vs `error`) → Task 11 and asserted again in Task 12; rendering → Task 12; section wiring and the `status-health` entry point → Task 13; the documentation correction the spec demands → Task 14; the Ansible role → Task 15.

**Type consistency.** `ClusterService`/`ClusterMember` field names are used identically in Tasks 3–7, 11 and 12. `run_with_budget` returns `BudgetResult(results, truncated, failed)` in Task 1 and is destructured with those exact names in Task 11. `collect_dns` is defined with keyword parameters in Task 8 and called with those same keywords in Task 11, which the Task 11 test asserts explicitly.

**Two deviations from the design sketch**, both deliberate and both explained at their task: `ClusterMember.healthy` is `bool | None` rather than `bool` (so MongoDB's unobservable members cannot render a false ✅), and `ClusterMember.warning` / `ClusterService.detail` carry short strings instead of a growing set of booleans.

**One risk worth naming.** `find_container` matches on container-name substrings that must track the stack naming the Ansible roles produce. A rename on the Ansible side would silently disable a probe — it would render `n/a here`, which looks like a legitimate state. Task 15's manual verification step (`status-health` on a real node) is what catches this, and it is the reason that step exists rather than being left to CI.
