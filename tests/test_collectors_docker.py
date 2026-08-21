from datetime import UTC, datetime

import pytest

from terminal_status_panel.collectors import docker as docker_collector
from terminal_status_panel.collectors._labels import COMPOSE_SERVICE_LABEL
from terminal_status_panel.model import SwarmInfo
from terminal_status_panel.render import icons
from terminal_status_panel.render.verdict import service_verdict

#: A fixed "now" for job-age assertions, so the arithmetic is readable.
_AT_2130Z = datetime(2026, 8, 12, 21, 30, tzinfo=UTC).timestamp()


class _FakeService:
    """*tasks* is a list of (node_id | None, state) for desired-state=running."""

    def __init__(
        self,
        name,
        desired,
        tasks,
        stack=None,
        description=None,
        raw_labels=None,
        mode=None,
        history=None,
        image=None,
        task_timestamp=None,
        placement_error=None,
        constraints=None,
        container_labels=None,
    ):
        self.task_timestamp = task_timestamp
        self.placement_error = placement_error
        self.unfiltered_calls = 0
        self.name = name
        labels = {}
        if stack is not None:
            labels["com.docker.stack.namespace"] = stack
        if description is not None:
            labels[docker_collector.LEGACY_DESCRIPTION_LABEL] = description
        labels.update(raw_labels or {})
        self.attrs = {
            "Spec": {"Mode": mode or {"Replicated": {"Replicas": desired}}, "Labels": labels}
        }
        if image is not None or constraints is not None or container_labels is not None:
            template = self.attrs["Spec"].setdefault("TaskTemplate", {})
            if image is not None or container_labels is not None:
                spec = template.setdefault("ContainerSpec", {})
                if image is not None:
                    spec["Image"] = image
                if container_labels is not None:
                    spec["Labels"] = dict(container_labels)
            if constraints is not None:
                template["Placement"] = {"Constraints": list(constraints)}
        self._tasks = tasks
        #: (node_id, state, timestamp) for tasks Swarm has already shut down --
        #: what a finished job leaves behind. Only an unfiltered listing sees them.
        self._history = history or []

    def tasks(self, filters=None):
        result = []
        for node_id, state in self._tasks:
            status = {"State": state}
            if self.task_timestamp is not None:
                status["Timestamp"] = self.task_timestamp
            if node_id is None and self.placement_error is not None:
                status["Err"] = self.placement_error
            task = {"Status": status}
            if node_id is not None:
                task["NodeID"] = node_id
            result.append(task)
        if filters and filters.get("desired-state") == "running":
            return result
        self.unfiltered_calls += 1
        for entry in self._history:
            node_id, state, timestamp = entry[:3]
            status = {"State": state, "Timestamp": timestamp}
            if len(entry) > 3 and entry[3] is not None:
                status["ContainerStatus"] = {"ExitCode": entry[3]}
            if len(entry) > 4 and entry[4]:
                status["Err"] = entry[4]
            task = {"Status": status, "DesiredState": "shutdown"}
            if node_id is not None:
                task["NodeID"] = node_id
            result.append(task)
        return result


class _FakeNode:
    def __init__(
        self,
        node_id,
        hostname,
        state="ready",
        role="worker",
        leader=False,
        availability="active",
        engine_version=None,
        reachability=None,
    ):
        self.id = node_id
        self.attrs = {
            "ID": node_id,
            "Description": {"Hostname": hostname},
            "Status": {"State": state},
            "Spec": {"Role": role, "Availability": availability},
        }
        if engine_version is not None:
            self.attrs["Description"]["Engine"] = {"EngineVersion": engine_version}
        if leader or reachability is not None:
            manager = self.attrs.setdefault("ManagerStatus", {})
            if leader:
                manager["Leader"] = True
            if reachability is not None:
                manager["Reachability"] = reachability


class _FakeClient:
    def __init__(
        self,
        swarm_state,
        services=None,
        containers=None,
        nodes=None,
        df=None,
        retention=5,
    ):
        self._swarm_attrs = (
            None
            if retention is None
            else {"Spec": {"Orchestration": {"TaskHistoryRetentionLimit": retention}}}
        )
        self._info = {
            "Swarm": {"LocalNodeState": swarm_state, "ControlAvailable": True, "Nodes": 3},
            "Name": "srv-01",
            "DockerRootDir": "/var/lib/docker",
        }
        self._df = df
        self._services = services or []
        self._containers = containers or []
        self._nodes = nodes or []

    def info(self):
        return self._info

    @property
    def swarm(self):
        if self._swarm_attrs is None:
            raise Exception("swarm not readable")

        class _Swarm:
            attrs = self._swarm_attrs

        return _Swarm()

    def df(self):
        if self._df is None:
            raise Exception("df not available")
        return self._df

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def services(self):
        return self._Coll(self._services)

    @property
    def containers(self):
        return self._Coll(self._containers)

    @property
    def nodes(self):
        return self._Coll(self._nodes)


def test_unreachable_when_from_env_raises(monkeypatch):
    monkeypatch.setattr(
        docker_collector.docker,
        "from_env",
        lambda *a, **k: (_ for _ in ()).throw(Exception("no socket")),
    )
    result = docker_collector.collect_docker(timeout=0.1)
    assert isinstance(result, SwarmInfo)
    assert result.reachable is False


def test_swarm_active_counts_running_replicas(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01"), _FakeNode("n2", "srv-02"), _FakeNode("n3", "srv-03")],
        services=[
            _FakeService("postgres", desired=1, tasks=[("n1", "running")]),
            _FakeService(
                "kafka", desired=3, tasks=[("n1", "running"), ("n2", "running"), ("n3", "failed")]
            ),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(critical=["postgres"])
    assert result.reachable is True and result.enabled is True
    assert result.node_role == "manager"
    by_name = {s.name: s for s in result.services}
    assert by_name["postgres"].running_replicas == 1
    assert by_name["postgres"].critical is True
    assert by_name["kafka"].running_replicas == 2  # failed task not counted
    assert by_name["kafka"].desired_replicas == 3


def test_swarm_groups_stacks_nodes_states_and_descriptions(monkeypatch):
    nodes = [
        _FakeNode("n1", "srv-ccc-01", role="manager", leader=True),
        _FakeNode("n2", "srv-ccn-01"),
        _FakeNode("n3", "srv-ccn-02", state="down"),
    ]
    services = [
        _FakeService(
            "pg",
            desired=1,
            tasks=[("n1", "running")],
            stack="PostgreSQL-18",
            description="PostgreSQL database, version 18",
        ),
        _FakeService(
            "kafka", desired=2, tasks=[("n2", "running"), ("n3", "failed")], stack="kafka"
        ),
        _FakeService("registry", desired=1, tasks=[(None, "pending")]),  # unassigned
    ]
    client = _FakeClient("active", services=services, nodes=nodes)
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()

    assert [n.name for n in result.nodes] == ["srv-ccc-01", "srv-ccn-01", "srv-ccn-02"]
    assert result.nodes[0].leader is True and result.nodes[0].role == "manager"
    assert result.nodes[2].reachable is False and result.nodes[2].state == "down"

    pg = next(s for s in result.services if s.name == "pg")
    assert pg.stack == "PostgreSQL-18"
    assert pg.description == "PostgreSQL database, version 18"
    assert [(t.node, t.state) for t in pg.tasks] == [("srv-ccc-01", "running")]

    kafka = next(s for s in result.services if s.name == "kafka")
    assert {(t.node, t.state) for t in kafka.tasks} == {
        ("srv-ccn-01", "running"),
        ("srv-ccn-02", "failed"),
    }

    reg = next(s for s in result.services if s.name == "registry")
    assert reg.stack is None
    assert reg.tasks == [] and reg.unassigned == 1


def test_custom_description_label(monkeypatch):
    svc = _FakeService("app", desired=1, tasks=[("n1", "running")])
    svc.attrs["Spec"]["Labels"]["info"] = "my custom description"
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(description_label="info")
    assert result.services[0].description == "my custom description"


def test_swarm_inactive_falls_back_to_containers(monkeypatch):
    class _C:
        def __init__(self, name):
            self.name = name
            self.id = f"id-{name}"
            self.status = "running"
            self.attrs = {"State": {"Status": "running", "ExitCode": 0}, "Config": {"Labels": {}}}

    client = _FakeClient("inactive", containers=[_C("redis"), _C("nginx")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.reachable is True and result.enabled is False
    assert result.services == []
    assert {c.name for c in result.containers} == {"redis", "nginx"}
    assert all(c.running_replicas == 1 and c.desired_replicas == 1 for c in result.containers)


def test_drained_node_is_ready_but_not_operational(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[
            _FakeNode("n1", "srv-01"),
            _FakeNode("n2", "srv-02", availability="drain"),
        ],
    )
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


# --- description label: neutral default, legacy key still honoured ----------


def test_default_description_label_is_vendor_neutral():
    from terminal_status_panel.config import Config

    assert Config().description_label == "status.description"
    assert docker_collector.LEGACY_DESCRIPTION_LABEL == "lmu.service.description"


def test_legacy_label_is_still_read(monkeypatch):
    """Installations predating the rename set the old key and no config. They
    must keep their descriptions without changing anything."""
    svc = _FakeService(
        "pg",
        desired=1,
        tasks=[("n1", "running")],
        stack="s",
        raw_labels={"lmu.service.description": "from the old key"},
    )
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-a")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.services[0].description == "from the old key"


def test_configured_label_wins_over_the_legacy_one(monkeypatch):
    svc = _FakeService(
        "pg",
        desired=1,
        tasks=[("n1", "running")],
        stack="s",
        raw_labels={"status.description": "current", "lmu.service.description": "stale"},
    )
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-a")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.services[0].description == "current"


def test_an_empty_configured_label_is_still_the_answer(monkeypatch):
    """Setting the key to "" is a deliberate "no description here". Treating it
    as absent would resurrect the legacy text the service was migrated away
    from — the opposite of what the migration was for."""
    svc = _FakeService(
        "pg",
        desired=1,
        tasks=[("n1", "running")],
        stack="s",
        raw_labels={"status.description": "", "lmu.service.description": "stale"},
    )
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-a")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.services[0].description == ""


# --- plain and Compose container collection ----------------------------------


class _FakeContainer:
    """*state* is the raw Docker status; *health* the healthcheck verdict."""

    def __init__(
        self,
        name,
        labels=None,
        state="running",
        exit_code=0,
        health=None,
        container_id=None,
        image=None,
        restart_count=0,
        started_at=None,
        oom_killed=False,
        error="",
        memory=None,
        inactive_file=0,
        mem_limit=0,
        mem_reservation=0,
        stats_raises=False,
        cgroup_v1=False,
    ):
        self._memory = memory
        self._inactive_file = inactive_file
        self._stats_raises = stats_raises
        self._cgroup_v1 = cgroup_v1
        self.stats_kwargs = None
        self.name = name
        self.id = container_id or f"id-{name}"
        self.status = state
        container_state = {
            "Status": state,
            "ExitCode": exit_code,
            "OOMKilled": oom_killed,
            "Error": error,
        }
        if started_at is not None:
            container_state["StartedAt"] = started_at
        if health is not None:
            container_state["Health"] = {"Status": health}
        self.attrs = {
            "State": container_state,
            "Config": {"Labels": dict(labels or {})},
            "RestartCount": restart_count,
            "HostConfig": {"Memory": mem_limit, "MemoryReservation": mem_reservation},
        }
        if image is not None:
            self.attrs["Config"]["Image"] = image

    def stats(self, **kwargs):
        self.stats_kwargs = kwargs
        if self._stats_raises:
            raise Exception("stats unavailable")
        key = "total_inactive_file" if self._cgroup_v1 else "inactive_file"
        return {
            "memory_stats": {
                "usage": self._memory,
                "limit": 54 * 2**30,
                "stats": {key: self._inactive_file},
            }
        }


def _compose(project, service, **kwargs):
    return _FakeContainer(
        f"{project}-{service}-1",
        labels={
            docker_collector.COMPOSE_PROJECT_LABEL: project,
            COMPOSE_SERVICE_LABEL: service,
        },
        **kwargs,
    )


def test_compose_containers_are_grouped_by_project_and_service(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[
            _compose("portal", "web"),
            _compose("portal", "web"),
            _compose("portal", "db"),
            _FakeContainer("lone-otel"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    by_name = {c.name: c for c in result.containers}
    assert by_name["web"].stack == "portal"
    assert by_name["web"].running_replicas == 2
    assert by_name["web"].desired_replicas == 2
    assert by_name["db"].stack == "portal"
    assert by_name["lone-otel"].stack is None
    assert result.services == []


class _SparseFakeContainer:
    """Mimics ``containers.list(sparse=True)``: labels sit at the top level and
    there is no "Config" key at all -- unlike the inspecting ``containers.list()``
    this collector actually calls today, where ``_FakeContainer`` above puts
    them under ``Config.Labels``. Only what ``_container_labels`` cares about
    is varied here; ``State`` stays nested so the rest of the pipeline
    (``_raw_state`` and friends) behaves exactly as in the non-sparse tests.
    """

    def __init__(self, name, labels=None, state="running", exit_code=0):
        self.name = name
        self.id = f"id-{name}"
        self.status = state
        self.attrs = {
            "State": {"Status": state, "ExitCode": exit_code},
            "Labels": dict(labels or {}),
        }


def test_a_sparsely_shaped_container_is_still_grouped_by_compose_labels(monkeypatch):
    """``_container_labels`` must tolerate both response shapes, not just the
    one this collector happens to call today -- see its docstring."""
    sparse = _SparseFakeContainer(
        "portal-web-1",
        labels={
            docker_collector.COMPOSE_PROJECT_LABEL: "portal",
            COMPOSE_SERVICE_LABEL: "web",
        },
    )
    client = _FakeClient("inactive", containers=[sparse])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert len(result.containers) == 1
    web = result.containers[0]
    assert web.name == "web"
    assert web.stack == "portal"
    assert web.running_replicas == 1


def test_swarm_tasks_are_not_listed_twice(monkeypatch):
    """A Swarm task is also a container; services.list() already reported it."""
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01", leader=True, role="manager")],
        services=[_FakeService("kafka", desired=1, tasks=[("n1", "running")])],
        containers=[
            _FakeContainer(
                "kafka.1.abcdef", labels={docker_collector.SWARM_SERVICE_LABEL: "kafka"}
            ),
            _compose("portal", "web"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert [s.name for s in result.services] == ["kafka"]
    assert [c.name for c in result.containers] == ["web"]


def test_a_dead_compose_container_shows_as_a_shortfall(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[
            _compose("portal", "web"),
            _compose("portal", "web", state="exited", exit_code=137),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    web = result.containers[0]
    assert (web.running_replicas, web.desired_replicas) == (1, 2)


def test_a_completed_job_is_dropped_entirely(monkeypatch):
    """Exit code 0 means the job did its work; it is not a service that died."""
    client = _FakeClient(
        "inactive",
        containers=[
            _compose("portal", "migrate", state="exited", exit_code=0),
            _compose("portal", "web"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert [c.name for c in result.containers] == ["web"]


def test_a_failed_job_is_kept(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[_compose("portal", "migrate", state="exited", exit_code=1)],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    migrate = result.containers[0]
    assert (migrate.running_replicas, migrate.desired_replicas) == (0, 1)


def test_stopped_containers_without_compose_labels_are_ignored(monkeypatch):
    """Otherwise every `docker run` leftover haunts the panel forever."""
    client = _FakeClient(
        "inactive",
        containers=[
            _FakeContainer("leftover", state="exited", exit_code=2),
            _FakeContainer("running-one"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert [c.name for c in result.containers] == ["running-one"]


def test_an_unhealthy_container_does_not_count_as_running(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[_compose("portal", "web", health="unhealthy")],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    web = result.containers[0]
    assert web.running_replicas == 0
    assert web.desired_replicas == 1


def test_a_starting_healthcheck_is_not_reported_as_failure(monkeypatch):
    """`docker compose up` a service with a `start_period`, log in inside it.

    Asserted on the *rendered* verdict, not on ``running_replicas``: that is 0
    for a container on its way up and for a dead one alike, so an assertion on
    it passes just as happily while the panel prints `💀 0/1`. Which is exactly
    what it printed, for every host without Swarm, when the collector dropped
    the task list and with it the only record of the starting state.
    """
    client = _FakeClient(
        "inactive",
        containers=[_compose("portal", "web", health="starting")],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    web = result.containers[0]
    assert web.running_replicas == 0
    assert service_verdict([web]).plain == f"{icons.WARN} 0/1"
    assert icons.DEAD not in service_verdict([web]).plain


def test_containers_are_placed_on_the_local_node_when_swarm_is_active(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01", leader=True, role="manager")],
        containers=[_compose("portal", "web")],
    )
    client._info["Swarm"]["NodeID"] = "n1"
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    web = result.containers[0]
    assert [t.node for t in web.tasks] == ["srv-01"]
    assert web.tasks[0].running is True


def test_containers_without_swarm_carry_tasks_with_no_node(monkeypatch):
    """No Swarm means no node name -- not no task.

    The task is where the state lives, and the verdict needs it. There are no
    node columns to place it in either way, so ``node=None`` costs nothing.
    """
    client = _FakeClient("inactive", containers=[_compose("portal", "web")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    web = result.containers[0]
    assert [t.node for t in web.tasks] == [None]
    assert web.tasks[0].running is True


def test_containers_use_the_same_description_label_as_services(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[
            _FakeContainer(
                "web",
                labels={
                    docker_collector.COMPOSE_PROJECT_LABEL: "portal",
                    COMPOSE_SERVICE_LABEL: "web",
                    "status.description": "the public front end",
                },
            )
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert result.containers[0].description == "the public front end"


def test_containers_honour_the_legacy_description_label(monkeypatch):
    client = _FakeClient(
        "inactive",
        containers=[
            _FakeContainer(
                "web",
                labels={
                    docker_collector.COMPOSE_PROJECT_LABEL: "portal",
                    COMPOSE_SERVICE_LABEL: "web",
                    docker_collector.LEGACY_DESCRIPTION_LABEL: "from the old key",
                },
            )
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert result.containers[0].description == "from the old key"


def test_containers_can_be_marked_critical(monkeypatch):
    client = _FakeClient("inactive", containers=[_compose("portal", "web")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker(critical=["web"])

    assert result.containers[0].critical is True


def test_a_service_label_without_a_project_label_names_the_container_the_same_way(
    monkeypatch,
):
    """Both collectors must call this container by one name.

    Compose always sets the project label beside the service one, so this shape
    takes a hand-written `com.docker.compose.service`. The Docker collector
    only groups by service name inside a project; a container that names a
    service but no project is not part of a Compose project at all, so it keeps
    its own name -- and the Traefik collector, which matches router targets
    against exactly those names, has to agree. Reading the service label alone
    made the router render a red "no such service" for a container sitting
    right there in the same listing.
    """
    from terminal_status_panel.collectors import traefik as traefik_collector

    labels = {
        COMPOSE_SERVICE_LABEL: "db",
        "traefik.http.routers.db.rule": "Host(`stats.example.net`)",
        "traefik.http.services.db.loadbalancer.server.port": "5432",
    }
    container = _FakeContainer("legacy-box", labels=labels)

    client = _FakeClient("inactive", containers=[container])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    docker_name = docker_collector.collect_docker().containers[0].name

    traefik_info = traefik_collector.collect_traefik(client)
    target = traefik_info.services["db"].docker_service

    assert docker_name == "legacy-box"
    assert target == docker_name


# --- container id to service name map -----------------------------------------


def test_a_swarm_container_maps_its_id_to_its_service(monkeypatch):
    """The process rows need this map, and Docker already hands us the data.

    A Swarm container is skipped for the DOCKER INFOS listing -- it is already
    reported through services.list() -- but its id still has to resolve, or a
    process row can only ever show a bare hex string.
    """
    client = _FakeClient(
        "active",
        containers=[
            _FakeContainer(
                "app.1.xyz",
                container_id="aaaa111122223333",
                labels={docker_collector.SWARM_SERVICE_LABEL: "stack_app_backend"},
            ),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert result.container_services["aaaa111122223333"] == "stack_app_backend"


def test_a_compose_container_maps_to_project_and_service(monkeypatch):
    client = _FakeClient("inactive", containers=[_compose("portal", "web")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert result.container_services["id-portal-web-1"] == "portal_web"


def test_a_standalone_container_maps_to_its_own_name(monkeypatch):
    client = _FakeClient("inactive", containers=[_FakeContainer("lone-otel")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    result = docker_collector.collect_docker()

    assert result.container_services["id-lone-otel"] == "lone-otel"


def test_an_unreachable_daemon_maps_nothing(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no daemon")

    monkeypatch.setattr(docker_collector.docker, "from_env", boom)
    assert docker_collector.collect_docker().container_services == {}


# --------------------------------------------------------------------------- #
# Scheduled jobs
# --------------------------------------------------------------------------- #


def test_swarm_cronjob_labels_mark_a_service_as_a_job(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        services=[
            _FakeService(
                "stack_nightly",
                desired=1,
                tasks=[],
                raw_labels={"swarm.cronjob.enable": "true", "swarm.cronjob.schedule": "0 5 * * *"},
            )
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (service,) = docker_collector.collect_docker().services

    assert service.job is True
    assert service.schedule == "0 5 * * *"


@pytest.mark.parametrize(
    "mode",
    [{"ReplicatedJob": {"TotalCompletions": 1}}, {"GlobalJob": {}}],
    ids=["replicated-job", "global-job"],
)
def test_a_native_swarm_job_is_recognised_without_labels(monkeypatch, mode):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        services=[_FakeService("stack_migrate", desired=None, tasks=[], mode=mode)],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (service,) = docker_collector.collect_docker().services

    assert service.job is True
    assert service.schedule is None  # a native job carries no cron expression


def test_the_newest_task_is_the_last_run(monkeypatch):
    """The *newest* task, not "some failed task exists".

    A job that failed yesterday and succeeded this morning is healthy; ranking
    by severity instead of by time would keep yesterday on the panel forever.
    """
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01"), _FakeNode("n2", "srv-02")],
        services=[
            _FakeService(
                "stack_nightly",
                desired=1,
                tasks=[],
                raw_labels={"swarm.cronjob.enable": "true"},
                history=[
                    ("n2", "failed", "2026-08-11T20:00:00.000000000Z"),
                    ("n1", "complete", "2026-08-12T09:30:00.000000000Z"),
                ],
            )
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    monkeypatch.setattr(docker_collector, "_now", lambda: _AT_2130Z)

    (service,) = docker_collector.collect_docker().services

    assert service.last_run.state == "complete"
    assert service.last_run.node == "srv-01"
    assert service.last_run.age_seconds == 12 * 3600


def test_a_docker_task_timestamp_is_read_as_utc():
    """The shape Docker actually sends: RFC 3339, nanoseconds, trailing Z.

    Measured against a live Swarm task on 2026-08-12.
    """
    parsed = docker_collector._parse_timestamp("2026-08-12T07:28:29.81745826Z")

    assert parsed == datetime(2026, 8, 12, 7, 28, 29, 817458, tzinfo=UTC).timestamp()


def test_a_timestamp_without_a_zone_is_still_read_as_utc():
    """Docker reports UTC. Falling back to the local zone would shift the age
    by the machine's offset -- silently, and only for hosts not on UTC."""
    parsed = docker_collector._parse_timestamp("2026-08-12T07:28:29")

    assert parsed == datetime(2026, 8, 12, 7, 28, 29, tzinfo=UTC).timestamp()


@pytest.mark.parametrize("value", [None, "", "yesterday", 17, {}])
def test_an_unreadable_timestamp_is_no_timestamp(value):
    assert docker_collector._parse_timestamp(value) is None


# --- the image a service actually runs ---------------------------------------


def test_a_swarm_service_reports_the_image_of_its_task_template(monkeypatch):
    svc = _FakeService(
        "app",
        desired=1,
        tasks=[("n1", "running")],
        image="gitlab.example.org:5005/group/project/app:2026-08-14_1206",
    )
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (service,) = docker_collector.collect_docker().services

    assert service.image == "gitlab.example.org:5005/group/project/app:2026-08-14_1206"


def test_the_pinned_digest_is_dropped_from_the_image(monkeypatch):
    """Swarm rewrites every image reference to `tag@sha256:...` once the service
    is created. The digest is 71 characters that never differ between two
    services running the same tag, so it is dropped where the fact is read."""
    svc = _FakeService(
        "app",
        desired=1,
        tasks=[("n1", "running")],
        image="registry.example.org/app:v3.3@sha256:" + "a" * 64,
    )
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (service,) = docker_collector.collect_docker().services

    assert service.image == "registry.example.org/app:v3.3"


def test_a_service_without_a_task_template_has_no_image(monkeypatch):
    svc = _FakeService("app", desired=1, tasks=[("n1", "running")])
    client = _FakeClient("active", services=[svc], nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (service,) = docker_collector.collect_docker().services

    assert service.image is None


def test_a_container_reports_the_image_it_was_configured_with(monkeypatch):
    """`Config.Image` is the reference the container was started from -- the
    name a reader recognises. `attrs["Image"]` is the resolved image ID, a
    bare sha256 that identifies the same thing unreadably."""
    client = _FakeClient(
        "inactive",
        containers=[_FakeContainer("redis", image="redis:7.4-alpine")],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (container,) = docker_collector.collect_docker().containers

    assert container.image == "redis:7.4-alpine"


def test_a_container_without_a_configured_image_has_no_image(monkeypatch):
    client = _FakeClient("inactive", containers=[_FakeContainer("redis")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (container,) = docker_collector.collect_docker().containers

    assert container.image is None


def test_a_compose_group_reports_the_image_that_is_actually_serving(monkeypatch):
    """A changed tag leaves the old container behind, stopped but still listed
    -- that is what makes a Compose shortfall visible. Reading the group's
    image off the first member would then name the image that is no longer
    running, on the very row whose replicas say the new one is."""
    client = _FakeClient(
        "inactive",
        containers=[
            _compose("shop", "api", state="exited", exit_code=1, image="api:1.0", container_id="a"),
            _compose("shop", "api", image="api:2.0", container_id="b"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)

    (group,) = docker_collector.collect_docker().containers

    assert group.image == "api:2.0"


# --- engine version and manager reachability -------------------------------


def test_nodes_carry_their_engine_version(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[
            _FakeNode("n1", "srv-01", role="manager", engine_version="28.5.2"),
            _FakeNode("n2", "srv-02", engine_version="27.3.1"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    by_name = {n.name: n for n in docker_collector.collect_docker().nodes}
    assert by_name["srv-01"].engine_version == "28.5.2"
    assert by_name["srv-02"].engine_version == "27.3.1"


def test_a_node_without_an_engine_block_reports_no_version(monkeypatch):
    client = _FakeClient("active", nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    assert docker_collector.collect_docker().nodes[0].engine_version is None


def test_a_manager_carries_its_reachability(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[
            _FakeNode("n1", "srv-01", role="manager", leader=True, reachability="reachable"),
            _FakeNode("n2", "srv-02", role="manager", reachability="unreachable"),
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    by_name = {n.name: n for n in docker_collector.collect_docker().nodes}
    assert by_name["srv-01"].reachable_by_managers is True
    assert by_name["srv-02"].reachable_by_managers is False


def test_a_worker_has_no_reachability_rather_than_a_false_one(monkeypatch):
    """A worker has no ManagerStatus at all, and absent must not read as down."""
    client = _FakeClient("active", nodes=[_FakeNode("n1", "srv-01", role="worker")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    assert docker_collector.collect_docker().nodes[0].reachable_by_managers is None


def test_the_local_engine_version_is_kept_when_nodes_cannot_be_listed(monkeypatch):
    """On a worker `nodes.list()` is refused, and only the own version remains."""

    class _WorkerClient(_FakeClient):
        @property
        def nodes(self):
            raise Exception("This node is not a swarm manager")

    client = _WorkerClient("active")
    client._info["ServerVersion"] = "28.5.2"
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.nodes == []
    assert result.local_engine_version == "28.5.2"


# --- docker disk usage -----------------------------------------------------


def _df_payload():
    """A /system/df response shaped like the daemon's, with one unused volume."""
    return {
        "LayersSize": 20 * 2**30,
        "ImageUsage": {"TotalSize": 20 * 2**30, "Reclaimable": 13 * 2**30, "TotalCount": 47},
        "ContainerUsage": {"TotalSize": 2**28, "Reclaimable": 2**28, "TotalCount": 10},
        "VolumeUsage": {"TotalSize": 2 * 2**30, "Reclaimable": 2 * 2**30, "TotalCount": 5},
        "BuildCacheUsage": {"TotalSize": 2**30, "Reclaimable": 2**30, "TotalCount": 3},
        "Volumes": [
            {"Name": "a", "UsageData": {"RefCount": 1, "Size": 2**20}},
            {"Name": "b", "UsageData": {"RefCount": 0, "Size": 2**20}},
            {"Name": "c", "UsageData": {"RefCount": 0, "Size": 2**20}},
        ],
    }


def test_disk_usage_totals_and_unused_volumes(monkeypatch):
    client = _FakeClient("active", nodes=[_FakeNode("n1", "srv-01")], df=_df_payload())
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    disk = docker_collector.collect_docker().disk
    assert disk is not None
    assert disk.images == 20 * 2**30
    assert disk.reclaimable == (13 + 2 + 1) * 2**30 + 2**28
    assert disk.used == (20 + 2 + 1) * 2**30 + 2**28
    assert (disk.volumes_unused, disk.volumes_total) == (2, 3)
    assert disk.root_dir == "/var/lib/docker"


def test_a_failing_df_loses_only_itself(monkeypatch):
    """The blast radius is the point: everything else must survive."""
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        services=[_FakeService("postgres", desired=1, tasks=[("n1", "running")])],
        df=None,  # raises
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.disk is None
    assert result.reachable is True and result.enabled is True
    assert [s.name for s in result.services] == ["postgres"]
    assert [n.name for n in result.nodes] == ["srv-01"]


def test_disk_usage_is_collected_without_a_swarm(monkeypatch):
    client = _FakeClient("inactive", df=_df_payload())
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.enabled is False
    assert result.disk is not None
    assert result.disk.node == "srv-01"


def test_the_disk_client_gets_its_own_timeout(monkeypatch):
    """A df() that overruns must not be bounded by the panel-wide socket timeout."""
    seen = []

    def _from_env(*a, **k):
        seen.append(k.get("timeout"))
        return _FakeClient("active", nodes=[_FakeNode("n1", "srv-01")], df=_df_payload())

    monkeypatch.setattr(docker_collector.docker, "from_env", _from_env)
    docker_collector.collect_docker(timeout=1.5, df_timeout=4.0)
    assert 1.5 in seen and 4.0 in seen


# --- trouble: local containers ---------------------------------------------

_RECENT = "2026-08-22T09:00:00.000000000Z"
_OLD = "2026-08-20T09:00:00.000000000Z"
#: 2026-08-22 09:05 UTC -- five minutes after _RECENT, two days after _OLD.
_FIXED_NOW = 1787389500.0


def _trouble(monkeypatch, *containers):
    monkeypatch.setattr(docker_collector, "_now", lambda: _FIXED_NOW)
    client = _FakeClient("inactive", containers=list(containers))
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    return docker_collector.collect_docker().trouble


def test_an_old_restart_is_not_trouble(monkeypatch):
    """RestartCount is cumulative and never reset, so on its own it would pin
    a stumble from months ago to the panel for good."""
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "worker", restart_count=5, started_at=_OLD),
    )
    assert entries == []


def test_a_recent_restart_is_trouble(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "worker", restart_count=5, started_at=_RECENT),
    )
    assert [e.name for e in entries] == ["mystack_worker"]
    assert entries[0].fails == 5


def test_a_container_that_never_restarted_is_not_trouble(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "worker", restart_count=0, started_at=_RECENT),
    )
    assert entries == []


def test_an_oom_kill_is_named_as_one(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose(
            "mystack",
            "model",
            state="exited",
            exit_code=137,
            oom_killed=True,
            restart_count=3,
            started_at=_RECENT,
        ),
    )
    assert entries[0].cause == "OOMKilled · exit 137"
    assert entries[0].severity == "dead"
    assert entries[0].uptime_seconds is None


def test_a_running_container_cannot_say_why_it_fell(monkeypatch):
    """Measured against Docker 29.7.2: a container that failed and came back
    reports ExitCode 0 and OOMKilled false. The cause is gone, and the panel
    must say so rather than invent one."""
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "worker", state="running", restart_count=2, started_at=_RECENT),
    )
    assert entries[0].cause is None
    assert entries[0].severity == "recovered"
    assert entries[0].uptime_seconds == 300.0


def test_an_empty_error_string_does_not_become_an_empty_cause(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose(
            "mystack", "worker", state="exited", exit_code=1, error="", restart_count=1,
            started_at=_RECENT,
        ),
    )
    assert entries[0].cause == "exit 1"


def test_an_error_string_is_appended_when_docker_gives_one(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose(
            "mystack", "web", state="exited", exit_code=1,
            error="bind: address already in use", restart_count=1, started_at=_RECENT,
        ),
    )
    assert entries[0].cause == 'exit 1 · "bind: address already in use"'


def test_a_restarting_container_ranks_between_dead_and_recovered(monkeypatch):
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "flapper", state="restarting", exit_code=1, restart_count=4,
                 started_at=_RECENT),
    )
    assert entries[0].severity == "restarting"


def test_a_cleanly_finished_job_container_is_not_trouble(monkeypatch):
    """Exit 0 is work done, not a fall -- and it is skipped before it is even
    grouped, so a restart count on it must not resurrect it."""
    entries = _trouble(
        monkeypatch,
        _compose("mystack", "migrate", state="exited", exit_code=0, restart_count=2,
                 started_at=_RECENT),
    )
    assert entries == []


def test_a_swarm_container_is_left_to_the_task_history(monkeypatch):
    """Its restarts are new tasks, counted from the manager API, not here."""
    entries = _trouble(
        monkeypatch,
        _FakeContainer(
            "mystack_worker.1.abc",
            labels={"com.docker.swarm.service.name": "mystack_worker"},
            restart_count=3,
            started_at=_RECENT,
        ),
    )
    assert entries == []


def test_trouble_entries_carry_the_local_node(monkeypatch):
    monkeypatch.setattr(docker_collector, "_now", lambda: _FIXED_NOW)
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        containers=[_compose("mystack", "worker", restart_count=1, started_at=_RECENT)],
    )
    client._info["Swarm"]["NodeID"] = "n1"
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    entries = docker_collector.collect_docker().trouble
    assert [e.node for e in entries] == ["srv-01"]


# --- trouble: swarm task history -------------------------------------------


def _swarm_trouble(monkeypatch, *services, retention=5):
    monkeypatch.setattr(docker_collector, "_now", lambda: _FIXED_NOW)
    client = _FakeClient(
        "active", nodes=[_FakeNode("n1", "srv-01")], services=list(services), retention=retention
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    return docker_collector.collect_docker().trouble, client


def test_a_healthy_long_running_service_is_never_inspected(monkeypatch):
    """The pre-selection is the whole cost argument: in steady state the extra
    call must not happen at all."""
    svc = _FakeService("web", desired=1, tasks=[("n1", "running")], task_timestamp=_OLD)
    entries, client = _swarm_trouble(monkeypatch, svc)
    assert entries == []
    assert svc.unfiltered_calls == 0


def test_a_service_short_of_its_replicas_is_inspected(monkeypatch):
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert [e.name for e in entries] == ["web"]
    assert svc.unfiltered_calls == 1


def test_a_service_whose_task_is_young_is_inspected(monkeypatch):
    """Full replicas, but it came up minutes ago -- the case worth catching."""
    svc = _FakeService(
        "web",
        desired=1,
        tasks=[("n1", "running")],
        task_timestamp=_RECENT,
        history=[("n1", "failed", _RECENT, 137, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert [e.name for e in entries] == ["web"]
    assert entries[0].severity == "recovered"


def test_a_cronjob_is_never_inspected(monkeypatch):
    svc = _FakeService(
        "backup",
        desired=0,
        tasks=[],
        raw_labels={"swarm.cronjob.enable": "true", "swarm.cronjob.schedule": "*/15 * * * *"},
        history=[("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries == []


def test_a_swarm_job_mode_service_is_never_inspected(monkeypatch):
    svc = _FakeService(
        "seed",
        desired=1,
        tasks=[],
        mode={"ReplicatedJob": {"MaxConcurrent": 1}},
        history=[("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries == []


def test_a_rolling_update_is_not_a_crash(monkeypatch):
    """Three cleanly ended tasks are a deploy. Counting them would report every
    ordinary image bump as a crash."""
    svc = _FakeService(
        "web",
        desired=1,
        tasks=[("n1", "running")],
        task_timestamp=_RECENT,
        history=[
            ("n1", "shutdown", _RECENT),
            ("n1", "shutdown", _RECENT),
            ("n1", "complete", _RECENT),
        ],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries == []


def test_a_finished_task_keeps_its_exit_code_and_error(monkeypatch):
    """What a local container overwrites, the swarm preserves."""
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 137, "task: non-zero exit (137)")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries[0].fails == 1
    assert "137" in entries[0].cause


def test_only_recent_failures_count(monkeypatch):
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _OLD, 1, ""), ("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries[0].fails == 1


def test_a_count_at_the_retention_limit_is_marked_as_a_floor(monkeypatch):
    """Swarm keeps only so much history; a twelve-fold crash looks fivefold."""
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "") for _ in range(5)],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc, retention=5)
    assert entries[0].fails == 5
    assert entries[0].fails_capped is True


def test_a_count_below_the_limit_is_not_a_floor(monkeypatch):
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "") for _ in range(3)],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc, retention=5)
    assert entries[0].fails_capped is False


def test_an_unreadable_retention_limit_does_not_block_the_block(monkeypatch):
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "") for _ in range(5)],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc, retention=None)
    assert entries[0].fails == 5
    assert entries[0].fails_capped is False


def test_a_service_that_was_never_placed_reports_the_reason(monkeypatch):
    """No failures at all, but the sentence people open SSH for."""
    svc = _FakeService(
        "builder",
        desired=1,
        tasks=[(None, "pending")],
        placement_error="no suitable node (insufficient memory on 3 nodes)",
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert [e.name for e in entries] == ["builder"]
    assert entries[0].fails is None
    assert entries[0].uptime_seconds is None
    assert "no suitable node" in entries[0].cause
    assert entries[0].severity == "dead"


def test_swarm_trouble_carries_the_node_of_the_failed_task(monkeypatch):
    svc = _FakeService(
        "web",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, svc)
    assert entries[0].node == "srv-01"


def test_a_healthy_fleet_costs_no_extra_calls_at_all(monkeypatch):
    """The scale version of the cost argument: one sick service among ten
    healthy ones must produce exactly one history call, not eleven."""
    healthy = [
        _FakeService(f"web-{i}", desired=1, tasks=[("n1", "running")], task_timestamp=_OLD)
        for i in range(10)
    ]
    sick = _FakeService(
        "broken",
        desired=2,
        tasks=[("n1", "running")],
        task_timestamp=_OLD,
        history=[("n1", "failed", _RECENT, 1, "")],
    )
    entries, _ = _swarm_trouble(monkeypatch, *healthy, sick)
    assert [e.name for e in entries] == ["broken"]
    assert sum(s.unfiltered_calls for s in healthy) == 0
    assert sick.unfiltered_calls == 1


def test_the_retention_limit_is_read_once_not_per_service(monkeypatch):
    """It is a property of the swarm, and asking per service would multiply a
    constant by the size of the fleet."""
    monkeypatch.setattr(docker_collector, "_now", lambda: _FIXED_NOW)
    services = [
        _FakeService(f"web-{i}", desired=1, tasks=[("n1", "running")], task_timestamp=_OLD)
        for i in range(10)
    ]
    client = _FakeClient("active", nodes=[_FakeNode("n1", "srv-01")], services=services)
    reads = []
    original = type(client).swarm.fget

    def counting(self):
        reads.append(1)
        return original(self)

    monkeypatch.setattr(type(client), "swarm", property(counting))
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    docker_collector.collect_docker()
    assert len(reads) == 1


# --- grouping label and placement pinning ----------------------------------


def _services(monkeypatch, *services):
    client = _FakeClient("active", nodes=[_FakeNode("n1", "srv-01")], services=list(services))
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    return {s.name: s for s in docker_collector.collect_docker().services}


def test_the_group_label_is_read_from_service_labels(monkeypatch):
    by_name = _services(
        monkeypatch,
        _FakeService("web", desired=1, tasks=[("n1", "running")],
                     raw_labels={"status.group": "frontend"}),
    )
    assert by_name["web"].group == "frontend"


def test_the_group_label_is_read_from_the_container_spec(monkeypatch):
    by_name = _services(
        monkeypatch,
        _FakeService("web", desired=1, tasks=[("n1", "running")],
                     container_labels={"status.group": "frontend"}),
    )
    assert by_name["web"].group == "frontend"


def test_an_absent_group_label_is_none_not_empty(monkeypatch):
    """None means 'not stated'; '' means 'stated as nothing'. They differ."""
    by_name = _services(
        monkeypatch, _FakeService("web", desired=1, tasks=[("n1", "running")])
    )
    assert by_name["web"].group is None


def test_an_explicitly_empty_group_label_is_kept_as_empty(monkeypatch):
    by_name = _services(
        monkeypatch,
        _FakeService("web", desired=1, tasks=[("n1", "running")], raw_labels={"status.group": ""}),
    )
    assert by_name["web"].group == ""


def test_a_placement_constraint_marks_the_service_as_pinned(monkeypatch):
    by_name = _services(
        monkeypatch,
        _FakeService("web", desired=1, tasks=[("n1", "running")],
                     constraints=["node.hostname == srv-01"]),
    )
    assert by_name["web"].pinned is True


def test_a_service_without_constraints_is_not_pinned(monkeypatch):
    by_name = _services(
        monkeypatch, _FakeService("web", desired=1, tasks=[("n1", "running")])
    )
    assert by_name["web"].pinned is False


def test_an_empty_constraint_list_is_not_pinning(monkeypatch):
    by_name = _services(
        monkeypatch,
        _FakeService("web", desired=1, tasks=[("n1", "running")], constraints=[]),
    )
    assert by_name["web"].pinned is False


def test_the_group_label_key_is_configurable(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        services=[
            _FakeService("web", desired=1, tasks=[("n1", "running")],
                         raw_labels={"example.group": "frontend"})
        ],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(group_label="example.group")
    assert result.services[0].group == "frontend"


# --- memory per service, on this node --------------------------------------

_MB = 2**20


def _mem(monkeypatch, *containers, services=None):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01")],
        containers=list(containers),
        services=list(services or []),
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    return {s.name: s for s in list(result.services) + list(result.containers)}


def test_the_page_cache_is_subtracted(monkeypatch):
    """Raw `usage` overstated a real container by 144%. The number a reader
    expects -- and `docker stats` prints -- is usage minus inactive_file."""
    by_name = _mem(
        monkeypatch,
        _compose("mystack", "db", memory=79 * _MB, inactive_file=46 * _MB),
    )
    assert by_name["db"].memory_bytes == 33 * _MB


def test_cgroup_v1_is_handled_like_v2(monkeypatch):
    by_name = _mem(
        monkeypatch,
        _compose("mystack", "db", memory=79 * _MB, inactive_file=46 * _MB, cgroup_v1=True),
    )
    assert by_name["db"].memory_bytes == 33 * _MB


def test_stats_is_always_asked_for_one_shot(monkeypatch):
    """Without it the daemon blocks about a second per container (measured:
    1009.7 ms for one), and the login path is gone."""
    container = _compose("mystack", "db", memory=10 * _MB)
    _mem(monkeypatch, container)
    assert container.stats_kwargs == {"stream": False, "one_shot": True}


def test_no_limit_is_reported_as_such_not_as_the_host_ram(monkeypatch):
    """`memory_stats.limit` carries the host's RAM when nothing was set. The
    reliable test is HostConfig.Memory == 0."""
    by_name = _mem(monkeypatch, _compose("mystack", "db", memory=10 * _MB, mem_limit=0))
    assert by_name["db"].memory_limit is None


def test_a_set_limit_is_reported(monkeypatch):
    by_name = _mem(
        monkeypatch, _compose("mystack", "db", memory=10 * _MB, mem_limit=256 * _MB)
    )
    assert by_name["db"].memory_limit == 256 * _MB


def test_a_reservation_is_kept_apart_from_a_limit(monkeypatch):
    by_name = _mem(
        monkeypatch,
        _compose("mystack", "db", memory=10 * _MB, mem_limit=0, mem_reservation=128 * _MB),
    )
    svc = by_name["db"]
    assert svc.memory_limit is None
    assert svc.memory_reservation == 128 * _MB


def test_a_dead_container_is_not_measured(monkeypatch):
    """It holds no memory, and asking would spend a call to learn zero."""
    container = _compose("mystack", "db", state="exited", exit_code=1, memory=10 * _MB)
    by_name = _mem(monkeypatch, container)
    assert container.stats_kwargs is None
    assert by_name["db"].memory_bytes is None


def test_a_failing_stats_call_costs_only_its_own_figure(monkeypatch):
    by_name = _mem(monkeypatch, _compose("mystack", "db", stats_raises=True))
    svc = by_name["db"]
    assert svc.memory_bytes is None
    assert svc.running_replicas == 1  # the rest of the row survives


def test_several_local_tasks_sum_usage_and_reference(monkeypatch):
    """A summed usage against one instance's limit would be a false alarm."""
    swarm_label = {docker_collector.SWARM_SERVICE_LABEL: "mystack_worker"}
    containers = [
        _FakeContainer(
            f"mystack_worker.{i}.x",
            labels=swarm_label,
            memory=100 * _MB,
            mem_limit=256 * _MB,
        )
        for i in (1, 2)
    ]
    by_name = _mem(
        monkeypatch,
        *containers,
        services=[_FakeService("mystack_worker", desired=2, tasks=[("n1", "running")] * 2)],
    )
    svc = by_name["mystack_worker"]
    assert svc.memory_bytes == 200 * _MB
    assert svc.memory_limit == 512 * _MB
    assert svc.local_tasks == 2


def test_a_service_with_no_local_task_has_no_figure(monkeypatch):
    by_name = _mem(
        monkeypatch,
        services=[_FakeService("mystack_remote", desired=1, tasks=[("n2", "running")])],
    )
    svc = by_name["mystack_remote"]
    assert svc.memory_bytes is None
    assert svc.local_tasks == 0
