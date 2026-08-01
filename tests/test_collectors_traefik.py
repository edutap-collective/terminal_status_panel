import httpx

from terminal_status_panel.collectors import traefik as collector
from terminal_status_panel.config import Config, TraefikApiConfig
from terminal_status_panel.model import TraefikRouter


class _FakeService:
    def __init__(self, name, labels=None, args=None):
        self.name = name
        spec = {"Labels": labels or {}}
        if args is not None:
            spec["TaskTemplate"] = {"ContainerSpec": {"Args": args}}
        self.attrs = {"Spec": spec}


class _FakeConfig:
    def __init__(self, name, data):
        import base64

        self.name = name
        self.attrs = {"Spec": {"Data": base64.b64encode(data.encode()).decode()}}


class _FakeClient:
    def __init__(self, services=None, configs=None):
        self._services = services or []
        self._configs = configs or []

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def services(self):
        return self._Coll(self._services)

    @property
    def configs(self):
        return self._Coll(self._configs)


def test_unknown_entrypoints_reports_only_the_missing_ones():
    router = TraefikRouter(name="r", entrypoints=["portalmgmt", "websecure"])
    assert collector.unknown_entrypoints(router, {"portalmgmt"}) == ["websecure"]


def test_a_router_with_only_known_entrypoints_has_no_orphans():
    router = TraefikRouter(name="r", entrypoints=["portalmgmt"])
    assert collector.unknown_entrypoints(router, {"portalmgmt"}) == []


def test_a_router_with_no_entrypoints_is_not_an_orphan():
    """No entrypoint named means Traefik attaches it to all of them."""
    assert collector.unknown_entrypoints(TraefikRouter(name="r"), {"a"}) == []


def test_collect_reads_entrypoints_from_the_traefik_service():
    client = _FakeClient(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
    ])
    info = collector.collect_traefik(client)
    assert info.reachable is True
    assert [ep.name for ep in info.entrypoints] == ["portalmgmt"]


def test_collect_joins_labels_from_every_service():
    client = _FakeClient(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
        _FakeService("kafbat-ui_kafbat-ui", labels={
            "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt",
            "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/x`)",
            "traefik.http.services.kafbat-ui.loadbalancer.server.port": "8080",
        }),
    ])
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["kafbat-ui"]
    assert info.services["kafbat-ui"].port == 8080


def test_collect_reads_the_file_provider_configs():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", (
            "http:\n  routers:\n    api:\n      entrypoints: dashboard\n"
            "      rule: PathPrefix(`/traefik`)\n      service: api@internal\n"
        ))],
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["api"]
    assert info.routers[0].source == "file"


def test_configs_that_are_not_traefik_dynamic_are_ignored():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("ca-certificates.crt_v1", "not yaml at all: [")],
    )
    assert collector.collect_traefik(client).routers == []


def test_a_docker_failure_is_reported_as_an_error_not_a_crash():
    class _Broken:
        @property
        def services(self):
            raise RuntimeError("socket gone")

    info = collector.collect_traefik(_Broken())
    assert info.reachable is False
    assert "socket gone" in info.error


def test_no_traefik_service_yields_no_entrypoints_but_does_not_fail():
    info = collector.collect_traefik(_FakeClient(services=[]))
    assert info.reachable is True
    assert info.entrypoints == []
    assert info.error is None


def test_a_broken_file_provider_is_reported_but_labels_still_stand():
    class _ConfigsBreak(_FakeClient):
        @property
        def configs(self):
            raise RuntimeError("config read failed")

    client = _ConfigsBreak(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
        _FakeService("kafbat-ui_kafbat-ui", labels={
            "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt",
            "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/x`)",
        }),
    ])
    info = collector.collect_traefik(client)
    assert info.reachable is True
    assert info.error is None
    assert "config read failed" in info.file_provider_error
    assert [r.name for r in info.routers] == ["kafbat-ui"]


def test_the_normal_path_leaves_file_provider_error_unset():
    client = _FakeClient(services=[_FakeService("traefik_traefik", args=[])])
    info = collector.collect_traefik(client)
    assert info.file_provider_error is None


def test_rejected_stays_none_when_the_api_was_not_consulted():
    info = collector.collect_traefik(_FakeClient(services=[]))
    assert info.api_consulted is False
    assert all(r.rejected is None for r in info.routers)


def test_mark_rejected_flags_routers_traefik_never_accepted():
    from terminal_status_panel.model import TraefikInfo

    info = TraefikInfo(routers=[
        TraefikRouter(name="kept"), TraefikRouter(name="dropped"),
    ])
    collector.mark_rejected(info, {"kept"})
    by_name = {r.name: r for r in info.routers}
    assert by_name["kept"].rejected is False
    assert by_name["dropped"].rejected is True
    assert info.api_consulted is True


_API_CFG = TraefikApiConfig(
    url="https://localhost:8082/traefik/api/rawdata", cert="/etc/ssl/panel.pem"
)


def test_fetch_accepted_returns_none_and_makes_no_request_when_not_configured():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"routers": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(), client=client)

    assert result is None
    assert calls == []


def test_fetch_accepted_returns_none_not_empty_set_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

    assert result is None


def test_fetch_accepted_parses_a_successful_response():
    payload = {
        "routers": {
            "kafbat-ui@swarm": {"entryPoints": ["portalmgmt"], "status": "enabled"},
            "broken@swarm": {"status": "disabled", "error": ["bad rule"]},
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

    assert result == {"kafbat-ui"}


def test_fetch_accepted_returns_none_on_a_server_error_like_unreachable():
    def handler(request):
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

    assert result is None
