from lmu.terminal_status_panel.collectors import docker as docker_collector
from lmu.terminal_status_panel.model import SwarmInfo


class _FakeService:
    def __init__(self, name, desired, running, stack=None, description=None,
                 node_ids=None):
        self.name = name
        labels = {}
        if stack is not None:
            labels["com.docker.stack.namespace"] = stack
        if description is not None:
            labels["description"] = description
        self.attrs = {
            "Spec": {"Mode": {"Replicated": {"Replicas": desired}}, "Labels": labels}
        }
        self._running = running
        self._node_ids = node_ids or []

    def tasks(self, filters=None):
        running = [
            {"Status": {"State": "running"}, "NodeID": self._node_ids[i]
             if i < len(self._node_ids) else ""}
            for i in range(self._running)
        ]
        return running + [{"Status": {"State": "failed"}}]


class _FakeNode:
    def __init__(self, node_id, hostname, state="ready", role="worker", leader=False):
        self.id = node_id
        self.attrs = {
            "ID": node_id,
            "Description": {"Hostname": hostname},
            "Status": {"State": state},
            "Spec": {"Role": role},
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


def test_swarm_active_lists_services(monkeypatch):
    client = _FakeClient(
        "active",
        services=[_FakeService("postgres", desired=1, running=1),
                  _FakeService("kafka", desired=3, running=2)],
    )
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker(critical=["postgres"])
    assert result.reachable is True
    assert result.enabled is True
    assert result.node_role == "manager"
    assert result.node_count == 3
    by_name = {s.name: s for s in result.services}
    assert by_name["postgres"].running_replicas == 1
    assert by_name["postgres"].desired_replicas == 1
    assert by_name["postgres"].critical is True
    assert by_name["kafka"].running_replicas == 2
    assert by_name["kafka"].desired_replicas == 3
    assert by_name["kafka"].critical is False


def test_swarm_groups_stacks_nodes_and_descriptions(monkeypatch):
    nodes = [
        _FakeNode("n1", "srv-ccc-01", role="manager", leader=True),
        _FakeNode("n2", "srv-ccn-01"),
        _FakeNode("n3", "srv-ccn-02", state="down"),
    ]
    services = [
        _FakeService("PostgreSQL-18_pg", desired=1, running=1, stack="PostgreSQL-18",
                     description="PostgreSQL Datenbank, Version 18", node_ids=["n1"]),
        _FakeService("registry", desired=1, running=1, node_ids=["n2"]),
    ]
    client = _FakeClient("active", services=services, nodes=nodes)
    monkeypatch.setattr(docker_collector.docker, "from_env", lambda *a, **k: client)
    result = docker_collector.collect_docker()

    assert [n.name for n in result.nodes] == ["srv-ccc-01", "srv-ccn-01", "srv-ccn-02"]
    assert result.nodes[0].leader is True
    assert result.nodes[0].role == "manager"
    assert result.nodes[2].reachable is False  # state "down"

    pg = next(s for s in result.services if s.name == "PostgreSQL-18_pg")
    assert pg.stack == "PostgreSQL-18"
    assert pg.description == "PostgreSQL Datenbank, Version 18"
    assert pg.nodes == ["srv-ccc-01"]
    reg = next(s for s in result.services if s.name == "registry")
    assert reg.stack is None  # ungrouped


def test_custom_description_label(monkeypatch):
    svc = _FakeService("app", desired=1, running=1)
    svc.attrs["Spec"]["Labels"]["info"] = "my custom description"
    client = _FakeClient("active", services=[svc], nodes=[])
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
    assert result.reachable is True
    assert result.enabled is False
    assert {s.name for s in result.services} == {"redis", "nginx"}
    assert all(s.running_replicas == 1 and s.desired_replicas == 1 for s in result.services)
