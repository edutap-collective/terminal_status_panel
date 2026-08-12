from datetime import datetime, timezone

import pytest

from terminal_status_panel.collectors import docker as docker_collector
from terminal_status_panel.collectors._labels import COMPOSE_SERVICE_LABEL
from terminal_status_panel.model import SwarmInfo
from terminal_status_panel.render import icons
from terminal_status_panel.render.verdict import service_verdict

#: A fixed "now" for job-age assertions, so the arithmetic is readable.
_AT_2130Z = datetime(2026, 8, 12, 21, 30, tzinfo=timezone.utc).timestamp()


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
    ):
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
        self._tasks = tasks
        #: (node_id, state, timestamp) for tasks Swarm has already shut down --
        #: what a finished job leaves behind. Only an unfiltered listing sees them.
        self._history = history or []

    def tasks(self, filters=None):
        result = []
        for node_id, state in self._tasks:
            task = {"Status": {"State": state}}
            if node_id is not None:
                task["NodeID"] = node_id
            result.append(task)
        if filters and filters.get("desired-state") == "running":
            return result
        for node_id, state, timestamp in self._history:
            task = {"Status": {"State": state, "Timestamp": timestamp}, "DesiredState": "shutdown"}
            if node_id is not None:
                task["NodeID"] = node_id
            result.append(task)
        return result


class _FakeNode:
    def __init__(
        self, node_id, hostname, state="ready", role="worker", leader=False, availability="active"
    ):
        self.id = node_id
        self.attrs = {
            "ID": node_id,
            "Description": {"Hostname": hostname},
            "Status": {"State": state},
            "Spec": {"Role": role, "Availability": availability},
        }
        if leader:
            self.attrs["ManagerStatus"] = {"Leader": True}


class _FakeClient:
    def __init__(self, swarm_state, services=None, containers=None, nodes=None):
        self._info = {
            "Swarm": {"LocalNodeState": swarm_state, "ControlAvailable": True, "Nodes": 3}
        }
        self._services = services or []
        self._containers = containers or []
        self._nodes = nodes or []

    def info(self):
        return self._info

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
        self, name, labels=None, state="running", exit_code=0, health=None, container_id=None
    ):
        self.name = name
        self.id = container_id or f"id-{name}"
        self.status = state
        container_state = {"Status": state, "ExitCode": exit_code}
        if health is not None:
            container_state["Health"] = {"Status": health}
        self.attrs = {
            "State": container_state,
            "Config": {"Labels": dict(labels or {})},
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

    assert parsed == datetime(2026, 8, 12, 7, 28, 29, 817458, tzinfo=timezone.utc).timestamp()


def test_a_timestamp_without_a_zone_is_still_read_as_utc():
    """Docker reports UTC. Falling back to the local zone would shift the age
    by the machine's offset -- silently, and only for hosts not on UTC."""
    parsed = docker_collector._parse_timestamp("2026-08-12T07:28:29")

    assert parsed == datetime(2026, 8, 12, 7, 28, 29, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("value", [None, "", "yesterday", 17, {}])
def test_an_unreadable_timestamp_is_no_timestamp(value):
    assert docker_collector._parse_timestamp(value) is None
