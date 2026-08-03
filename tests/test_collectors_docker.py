from terminal_status_panel.collectors import docker as docker_collector
from terminal_status_panel.model import SwarmInfo


class _FakeService:
    """*tasks* is a list of (node_id | None, state) for desired-state=running."""

    def __init__(self, name, desired, tasks, stack=None, description=None):
        self.name = name
        labels = {}
        if stack is not None:
            labels["com.docker.stack.namespace"] = stack
        if description is not None:
            labels["lmu.service.description"] = description
        self.attrs = {
            "Spec": {"Mode": {"Replicated": {"Replicas": desired}}, "Labels": labels}
        }
        self._tasks = tasks

    def tasks(self, filters=None):
        result = []
        for node_id, state in self._tasks:
            task = {"Status": {"State": state}}
            if node_id is not None:
                task["NodeID"] = node_id
            result.append(task)
        return result


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
        docker_collector.docker, "from_env",
        lambda *a, **k: (_ for _ in ()).throw(Exception("no socket")),
    )
    result = docker_collector.collect_docker(timeout=0.1)
    assert isinstance(result, SwarmInfo)
    assert result.reachable is False


def test_swarm_active_counts_running_replicas(monkeypatch):
    client = _FakeClient(
        "active",
        nodes=[_FakeNode("n1", "srv-01"), _FakeNode("n2", "srv-02"),
               _FakeNode("n3", "srv-03")],
        services=[
            _FakeService("postgres", desired=1, tasks=[("n1", "running")]),
            _FakeService("kafka", desired=3,
                         tasks=[("n1", "running"), ("n2", "running"), ("n3", "failed")]),
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
        _FakeService("pg", desired=1, tasks=[("n1", "running")], stack="PostgreSQL-18",
                     description="PostgreSQL database, version 18"),
        _FakeService("kafka", desired=2, tasks=[("n2", "running"), ("n3", "failed")],
                     stack="kafka"),
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
        ("srv-ccn-01", "running"), ("srv-ccn-02", "failed")}

    reg = next(s for s in result.services if s.name == "registry")
    assert reg.stack is None
    assert reg.tasks == [] and reg.unassigned == 1


def test_custom_description_label(monkeypatch):
    svc = _FakeService("app", desired=1, tasks=[("n1", "running")])
    svc.attrs["Spec"]["Labels"]["info"] = "my custom description"
    client = _FakeClient("active", services=[svc],
                         nodes=[_FakeNode("n1", "srv-01")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(description_label="info")
    assert result.services[0].description == "my custom description"


def test_swarm_inactive_falls_back_to_containers(monkeypatch):
    class _C:
        def __init__(self, name):
            self.name = name

    client = _FakeClient("inactive", containers=[_C("redis"), _C("nginx")])
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()
    assert result.reachable is True and result.enabled is False
    assert {s.name for s in result.services} == {"redis", "nginx"}
    assert all(s.running_replicas == 1 and s.desired_replicas == 1 for s in result.services)


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
