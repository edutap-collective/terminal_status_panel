from lmu.terminal_status_panel.collectors import docker as docker_collector
from lmu.terminal_status_panel.model import SwarmInfo


class _FakeService:
    def __init__(self, name, desired, running):
        self.name = name
        self.attrs = {"Spec": {"Mode": {"Replicated": {"Replicas": desired}}}}
        self._running = running

    def tasks(self, filters=None):
        return [{"Status": {"State": "running"}} for _ in range(self._running)] + [
            {"Status": {"State": "failed"}}
        ]


class _FakeClient:
    def __init__(self, swarm_state, services=None, containers=None):
        self._info = {
            "Swarm": {"LocalNodeState": swarm_state, "ControlAvailable": True, "Nodes": 3}
        }
        self._services = services or []
        self._containers = containers or []

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
