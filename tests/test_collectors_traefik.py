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


def test_a_mis_cased_entrypoints_label_still_reaches_the_orphan_block():
    """The branch's headline finding depends on this: image_api names the
    entrypoint `websecure`, which does not exist. Parsed case-sensitively the
    label vanishes, the empty list reads as "attached to all entrypoints", and
    the finding turns into "wired to all nine ports"."""
    client = _FakeClient(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
        _FakeService("edutap_production_image_api", labels={
            "traefik.http.routers.image_api.entryPoints": "websecure",
            "traefik.http.routers.image_api.rule": "Host(`www.portal.uni-muenchen.de`)",
        }),
    ])
    info = collector.collect_traefik(client)
    router = next(r for r in info.routers if r.name == "image_api")
    assert router.entrypoints == ["websecure"]
    assert collector.unknown_entrypoints(router, {"portalmgmt"}) == ["websecure"]


def test_a_service_whose_attrs_are_not_a_mapping_does_not_raise():
    """`collect_traefik` is specified never to raise, and `main` swallows what
    it does raise — so an odd shape here prints a blank panel, not a
    traceback."""
    class _Odd:
        name = "weird"
        attrs = ["not", "a", "mapping"]

    info = collector.collect_traefik(_FakeClient(services=[_Odd()]))
    assert info.error is None
    assert info.reachable is True
    assert info.routers == []


def test_a_spec_that_is_not_a_mapping_does_not_raise():
    class _Odd:
        name = "traefik_traefik"
        attrs = {"Spec": ["TaskTemplate"]}

    info = collector.collect_traefik(_FakeClient(services=[_Odd()]))
    assert info.error is None
    assert info.entrypoints == []


def test_an_undecodable_config_is_reported_as_a_file_provider_error():
    """A config that cannot be decoded yields no routers — indistinguishable
    from "no dynamic config exists" unless it is reported, and the dashboard
    entrypoint would read `— no router` instead of showing the gap."""
    class _Undecodable:
        name = "traefik_dynamic_yml_v2"
        attrs = {"Spec": {"Data": "not base64 %%%"}}

    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_Undecodable()],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert info.file_provider_error is not None
    assert "traefik_dynamic_yml_v2" in info.file_provider_error


def test_a_config_with_broken_yaml_is_reported_as_a_file_provider_error():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", "http:\n  routers: [unclosed\n")],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert info.file_provider_error is not None
    assert "traefik_dynamic_yml_v2" in info.file_provider_error


def test_a_config_that_parses_to_nothing_is_not_an_error():
    """Valid YAML without an http section is a real, empty answer."""
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", "tls:\n  options: {}\n")],
    )
    assert collector.collect_traefik(client).file_provider_error is None


def test_the_collector_bounds_its_own_docker_calls_with_its_timeout():
    """docker-py has no per-call timeout, and the client handed to this
    collector carries the health section's larger socket timeout. Two
    unbudgeted main-thread calls against a hung daemon is what `docker.timeout`
    is documented to prevent."""
    seen = []

    class _Api:
        timeout = 4.0

    class _Recording(_FakeClient):
        def __init__(self):
            super().__init__(services=[], configs=[])
            self.api = _Api()

        @property
        def services(self):
            seen.append(self.api.timeout)
            return self._Coll([])

        @property
        def configs(self):
            seen.append(self.api.timeout)
            return self._Coll([])

    client = _Recording()
    collector.collect_traefik(client, timeout=1.5)
    assert seen == [1.5, 1.5]
    assert client.api.timeout == 4.0


def test_the_socket_timeout_is_restored_even_when_the_call_fails():
    class _Api:
        timeout = 4.0

    class _Broken(_FakeClient):
        def __init__(self):
            super().__init__()
            self.api = _Api()

        @property
        def services(self):
            raise RuntimeError("socket gone")

    client = _Broken()
    assert "socket gone" in collector.collect_traefik(client, timeout=1.5).error
    assert client.api.timeout == 4.0


def test_a_client_without_an_api_attribute_still_collects():
    """The fakes in these tests have no `api`; neither has anything else that
    quacks like a Docker client without one."""
    info = collector.collect_traefik(_FakeClient(services=[]), timeout=1.5)
    assert info.reachable is True


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
