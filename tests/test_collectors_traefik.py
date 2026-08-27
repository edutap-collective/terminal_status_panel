import inspect
import ssl
from datetime import UTC

import httpx2
import pytest

from terminal_status_panel.collectors import traefik as collector
from terminal_status_panel.collectors._labels import (
    COMPOSE_PROJECT_LABEL,
    COMPOSE_SERVICE_LABEL,
)
from terminal_status_panel.config import Config, TraefikApiConfig
from terminal_status_panel.model import TraefikRouter


class _FakeService:
    def __init__(self, name, labels=None, args=None, configs=None):
        self.name = name
        spec = {"Labels": labels or {}}
        container = {}
        if args is not None:
            container["Args"] = args
        if configs is not None:
            # What the real Traefik service carries: one entry per mounted
            # config generation, which is how the collector tells the live ones
            # from the superseded ones Swarm keeps around.
            container["Configs"] = [{"ConfigName": name} for name in configs]
        if container:
            spec["TaskTemplate"] = {"ContainerSpec": container}
        self.attrs = {"Spec": spec}


class _FakeConfig:
    def __init__(self, name, data):
        import base64

        self.name = name
        self.attrs = {"Spec": {"Data": base64.b64encode(data.encode()).decode()}}


class _FakeContainer:
    """Labels in the inspect shape, which is what `containers.list()` returns."""

    def __init__(self, name, labels=None):
        self.name = name
        self.attrs = {"Config": {"Labels": dict(labels or {})}}


class _SparseFakeContainer:
    """Labels in the `sparse=True` shape: top level, no Config key."""

    def __init__(self, name, labels=None):
        self.name = name
        self.attrs = {"Labels": dict(labels or {})}


class _FakeClient:
    def __init__(self, services=None, configs=None, containers=None):
        self._services = services or []
        self._configs = configs or []
        self._containers = containers or []

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

    @property
    def containers(self):
        return self._Coll(self._containers)


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
    client = _FakeClient(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
        ]
    )
    info = collector.collect_traefik(client)
    assert info.reachable is True
    assert [ep.name for ep in info.entrypoints] == ["portalmgmt"]


def test_collect_joins_labels_from_every_service():
    client = _FakeClient(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
            _FakeService(
                "kafbat-ui_kafbat-ui",
                labels={
                    "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt",
                    "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/x`)",
                    "traefik.http.services.kafbat-ui.loadbalancer.server.port": "8080",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["kafbat-ui"]
    assert info.services["kafbat-ui"].port == 8080


def test_router_labels_are_read_from_containers():
    client = _FakeClient(
        containers=[
            _FakeContainer(
                "portal-web-1",
                {
                    "traefik.http.routers.web.rule": "Host(`www.example.net`)",
                    "traefik.http.routers.web.entrypoints": "https",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    by_name = {r.name: r for r in info.routers}
    assert "web" in by_name
    assert by_name["web"].entrypoints == ["https"]
    assert by_name["web"].origin == "portal-web-1"


def test_a_compose_container_s_router_matches_the_compose_service_name():
    """The container's own name (`origin`, for display) and the Compose
    service name (`docker_service`, matched against ServiceStatus.name by the
    renderer) are two different strings -- collectors/docker.py names this
    container's ServiceStatus "db", not "course-statistics-db"."""
    client = _FakeClient(
        containers=[
            _FakeContainer(
                "course-statistics-db",
                {
                    "traefik.http.routers.db.rule": "Host(`stats.example.net`)",
                    "traefik.http.routers.db.entrypoints": "https",
                    "traefik.http.services.db.loadbalancer.server.port": "5432",
                    # Both labels, which is the only shape Compose produces: the
                    # service name identifies this container within its project.
                    COMPOSE_PROJECT_LABEL: "course-statistics",
                    COMPOSE_SERVICE_LABEL: "db",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    router = next(r for r in info.routers if r.name == "db")
    assert router.origin == "course-statistics-db"
    assert info.services["db"].docker_service == "db"


def test_a_container_without_compose_labels_yields_the_same_name_twice():
    """No Compose service label to prefer, so the container's own name is the
    only identity there is -- for both `origin` and `docker_service`."""
    client = _FakeClient(
        containers=[
            _FakeContainer(
                "standalone-proxy",
                {
                    "traefik.http.routers.web.rule": "Host(`www.example.net`)",
                    "traefik.http.routers.web.entrypoints": "https",
                    "traefik.http.services.web.loadbalancer.server.port": "80",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    router = next(r for r in info.routers if r.name == "web")
    assert router.origin == "standalone-proxy"
    assert info.services["web"].docker_service == "standalone-proxy"


def test_container_labels_are_read_in_the_sparse_shape_too():
    client = _FakeClient(
        containers=[
            _SparseFakeContainer(
                "portal-web-1",
                {
                    "traefik.http.routers.web.entrypoints": "https",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["web"]


def test_a_swarm_task_container_is_not_read_twice():
    """The task carries its service's labels; services.list() already had them."""
    labels = {"traefik.http.routers.api.entrypoints": "https"}
    client = _FakeClient(
        services=[_FakeService("api", labels=labels)],
        containers=[
            _FakeContainer("api.1.abcdef", {**labels, "com.docker.swarm.service.name": "api"})
        ],
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["api"]
    assert [r.origin for r in info.routers] == ["api"]


def test_a_failing_container_listing_keeps_the_service_results():
    """Containers raise, services succeed: unchanged from before the
    services-side fix -- `container_error` is set, service routers stand.

    A subclass, not a monkeypatch of `_FakeClient.containers` itself: see
    `test_a_failing_swarm_listing_still_returns_container_routers` for why
    overwriting the shared class's property outlives this one test."""

    class _NoContainers(_FakeClient):
        @property
        def containers(self):
            raise RuntimeError("no containers for you")

    client = _NoContainers(
        services=[
            _FakeService("api", labels={"traefik.http.routers.api.entrypoints": "https"}),
        ]
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["api"]
    assert info.reachable is True
    assert info.error is None
    assert "no containers for you" in info.container_error


def test_a_failing_swarm_listing_still_returns_container_routers():
    """`services.list()` raising `This node is not a swarm manager` is the
    normal answer on a Compose-only host -- the case this branch exists to
    serve. It must not blank out the routers a container declares.

    A subclass, not a monkeypatch of `_FakeClient.services` itself: that
    property is defined once on the shared class, and overwriting it on the
    class object (even with a `finally: del`) would drop the original
    definition for every test that runs afterward in this process, not just
    this one."""

    class _NoSwarmManager(_FakeClient):
        @property
        def services(self):
            raise RuntimeError("This node is not a swarm manager")

    client = _NoSwarmManager(
        containers=[
            _FakeContainer(
                "portal-web-1",
                {
                    "traefik.http.routers.web.rule": "Host(`www.example.net`)",
                    "traefik.http.routers.web.entrypoints": "https",
                },
            ),
        ]
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["web"]
    assert info.reachable is True
    assert info.error is None
    assert "swarm manager" in info.service_error


def test_both_listings_failing_yields_an_error_naming_both():
    """Only when neither listing can be read is there genuinely nothing to
    show -- the one case that still aborts like the old code always did."""

    class _BrokenClient(_FakeClient):
        @property
        def services(self):
            raise RuntimeError("services down")

        @property
        def containers(self):
            raise RuntimeError("containers down")

    info = collector.collect_traefik(_BrokenClient())
    assert info.reachable is False
    assert "services down" in info.error
    assert "containers down" in info.error


def test_collect_reads_the_file_provider_configs():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
        configs=[
            _FakeConfig(
                "traefik_dynamic_yml_v2",
                (
                    "http:\n  routers:\n    api:\n      entrypoints: dashboard\n"
                    "      rule: PathPrefix(`/traefik`)\n      service: api@internal\n"
                ),
            )
        ],
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

    client = _ConfigsBreak(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
            _FakeService(
                "kafbat-ui_kafbat-ui",
                labels={
                    "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt",
                    "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/x`)",
                },
            ),
        ]
    )
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
    client = _FakeClient(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
            _FakeService(
                "mystack_image_api",
                labels={
                    "traefik.http.routers.image_api.entryPoints": "websecure",
                    "traefik.http.routers.image_api.rule": "Host(`www.example.net`)",
                },
            ),
        ]
    )
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
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
        configs=[_Undecodable()],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert info.file_provider_error is not None
    assert "traefik_dynamic_yml_v2" in info.file_provider_error


def test_a_config_with_broken_yaml_is_reported_as_a_file_provider_error():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", "http:\n  routers: [unclosed\n")],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert info.file_provider_error is not None
    assert "traefik_dynamic_yml_v2" in info.file_provider_error


def test_an_empty_config_body_is_reported_as_a_file_provider_error():
    """It decodes cleanly and parses cleanly to nothing, so it slips past both
    the decode guard and the YAML guard — the last silent way for the file
    provider to contribute no routers."""
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", "")],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert info.file_provider_error is not None
    assert "traefik_dynamic_yml_v2" in info.file_provider_error


def test_a_config_that_parses_to_nothing_is_not_an_error():
    """Valid YAML without an http section is a real, empty answer."""
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
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
    # `.error` stays unset here -- the container listing this fake inherits
    # from `_FakeClient` still succeeds (trivially, with none), so this is a
    # partial failure, not the "nothing could be read at all" case. What this
    # test actually checks is the timeout restoration below; `service_error`
    # only confirms the failing call was genuinely exercised.
    assert "socket gone" in collector.collect_traefik(client, timeout=1.5).service_error
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

    info = TraefikInfo(
        routers=[
            TraefikRouter(name="kept"),
            TraefikRouter(name="dropped"),
        ]
    )
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
        return httpx2.Response(200, json={"routers": {}})

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(), client=client)

    assert result is None
    assert calls == []


def test_fetch_accepted_returns_none_not_empty_set_when_unreachable():
    def handler(request):
        raise httpx2.ConnectError("connection refused", request=request)

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
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
        return httpx2.Response(200, json=payload)

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

    assert result == {"kafbat-ui"}


def test_fetch_accepted_returns_none_when_the_payload_cannot_be_read():
    """A 200 whose body is not the expected shape is "we asked and could not
    read the answer" — the same not-observable state as an unreachable API.
    Returning an empty set instead would send `mark_rejected` through every
    router with nothing to match, marking all of them rejected."""
    shapes = [
        {"routers": ["kafbat-ui@swarm"]},  # a list where a mapping belongs
        {"routers": None},
        {},
        [],
        None,
    ]
    for payload in shapes:

        def handler(request, payload=payload):
            return httpx2.Response(200, json=payload)

        with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
            result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

        assert result is None, payload


def test_an_unreadable_payload_leaves_every_router_unconsulted():
    """The seam the defect lived at: neither the parser's tests nor the
    renderer's could see it, because each side was right on its own."""
    from terminal_status_panel.model import TraefikInfo

    def handler(request):
        return httpx2.Response(200, json={"routers": ["kafbat-ui@swarm"]})

    info = TraefikInfo(routers=[TraefikRouter(name="a"), TraefikRouter(name="b")])
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        accepted = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)
    if accepted is not None:  # what cli.collect_all does
        collector.mark_rejected(info, accepted)

    assert info.api_consulted is False
    assert all(router.rejected is None for router in info.routers)


def test_an_empty_router_list_is_a_readable_answer_and_rejects_everything():
    """The counter-case: Traefik answered, in the right shape, that it holds
    no routers. That is measured, and every configured router really was
    rejected."""
    from terminal_status_panel.model import TraefikInfo

    def handler(request):
        return httpx2.Response(200, json={"routers": {}})

    info = TraefikInfo(routers=[TraefikRouter(name="a")])
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        accepted = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)
    assert accepted == set()
    collector.mark_rejected(info, accepted)
    assert info.routers[0].rejected is True


def test_fetch_accepted_returns_none_on_a_server_error_like_unreachable():
    def handler(request):
        return httpx2.Response(500)

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        result = collector.fetch_accepted(Config(traefik=_API_CFG), client=client)

    assert result is None


def test_only_the_config_generations_the_service_mounts_are_read():
    """Swarm keeps every generation. Reading them all put `ping-router` four
    times on each entrypoint and invented orphans out of entrypoints removed
    two revisions ago — the panel showed wiring Traefik has not read in weeks."""
    body = (
        "http:\n  routers:\n    ping-router:\n      entrypoints: {eps}\n"
        "      rule: Path(`/_traefik_ping_`)\n      service: ping@internal\n"
    )
    client = _FakeClient(
        services=[
            _FakeService(
                "traefik_traefik",
                args=["--entryPoints.portalmgmt.address=:2020"],
                configs=["traefik_dynamic_yml_v4"],
            )
        ],
        configs=[
            _FakeConfig("traefik_dynamic_yml_v1", body.format(eps="db-ui, kafbat")),
            _FakeConfig("traefik_dynamic_yml_v4", body.format(eps="portalmgmt")),
        ],
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["ping-router"]
    assert info.routers[0].entrypoints == ["portalmgmt"]
    assert info.file_provider_error is None


def test_without_the_traefik_service_no_config_generation_is_guessed():
    """Which generation is live cannot be known, and showing all of them would
    put stale routers on screen as if they were current. The routers are
    visibly missing instead, with the reason named."""
    client = _FakeClient(
        services=[_FakeService("some_other_stack")],
        configs=[
            _FakeConfig(
                "traefik_dynamic_yml_v1",
                ("http:\n  routers:\n    api:\n      rule: PathPrefix(`/traefik`)\n"),
            )
        ],
    )
    info = collector.collect_traefik(client)
    assert info.routers == []
    assert "traefik service not found" in info.file_provider_error


def test_no_configs_at_all_is_not_reported_as_a_gap():
    """Nothing was hidden, so nothing is claimed: a daemon without dynamic
    configs must not read as a file provider that failed."""
    info = collector.collect_traefik(_FakeClient(services=[_FakeService("other")]))
    assert info.file_provider_error is None


# --------------------------------------------------------------------------- #
# The production request path
#
# Every test above passes the `client` seam, which bypasses the branch that
# builds the real request. That branch was therefore never executed by the
# suite -- and it was broken: httpx removed `cert` from its top-level API in
# 0.28, so the call raised TypeError and the mTLS cross-check failed silently.
# httpx2 has no such parameter either, so the same test still guards the same
# mistake -- now against the signature the package actually calls.
# --------------------------------------------------------------------------- #


def test_the_production_path_passes_arguments_httpx2_accepts(monkeypatch, tmp_path):
    """The regression test for a call httpx2 would reject.

    Binding against the real signature is the point: a plain mock accepts any
    keyword, so it would have recorded `cert=...` happily and proved nothing.
    """
    monkeypatch.setattr(collector, "_ssl_context", lambda api: ssl.create_default_context())
    signature = inspect.signature(httpx2.get)
    seen = {}

    def recording_get(url, **kwargs):
        signature.bind(url, **kwargs)  # TypeError if httpx2 would not accept these
        seen.update(kwargs)
        # `request=` is required: raise_for_status refuses to work without it.
        return httpx2.Response(200, json={"routers": {}}, request=httpx2.Request("GET", url))

    monkeypatch.setattr(collector.httpx2, "get", recording_get)
    cfg = Config(traefik=TraefikApiConfig(url="https://example.invalid/api", cert="/unused.pem"))

    result = collector.fetch_accepted(cfg)

    assert result == set()
    assert "cert" not in seen  # httpx2 has no such parameter either


def _self_signed(tmp_path, *, split_key: bool):
    """A throwaway certificate and key, generated rather than committed."""
    from datetime import datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "panel-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_file = tmp_path / "client.pem"
    if split_key:
        cert_file.write_bytes(cert_pem)
        key_file = tmp_path / "client.key"
        key_file.write_bytes(key_pem)
        return str(cert_file), str(key_file)
    cert_file.write_bytes(cert_pem + key_pem)  # bundled, as the config allows
    return str(cert_file), None


@pytest.mark.parametrize("split_key", [True, False], ids=["separate-key", "bundled-key"])
def test_the_client_certificate_is_actually_loadable(tmp_path, split_key):
    """Both shapes the configuration allows must produce a usable context.

    Asserting that the call *succeeds* is the whole test: `load_cert_chain`
    raises on a key that does not match its certificate, on a missing file and
    on a malformed one, so a context coming back at all is the evidence.
    """
    cert, key = _self_signed(tmp_path, split_key=split_key)
    api = TraefikApiConfig(url="https://example.invalid/api", cert=cert, key=key)

    context = collector._ssl_context(api)

    assert isinstance(context, ssl.SSLContext)


def test_without_a_configured_ca_the_system_trust_store_is_used(tmp_path):
    """`traefik.ca` unset means OpenSSL's default paths, not a vendored bundle.

    This is worth pinning because the README claimed the opposite for two
    releases: that the HTTP library would fall back to certifi's bundle and
    miss a corporate CA in `/etc/ssl/certs`. The library never gets to decide.
    `fetch_accepted` only runs when a client certificate is configured, so
    `_ssl_context` always runs too and always passes an explicit context --
    and `ssl.create_default_context()` loads from
    `ssl.get_default_verify_paths()`, which is the system store.

    Asserted as "the same store a default context gets" rather than as a
    certificate count, because the count is not portable: from a `cafile`
    OpenSSL loads every root eagerly, from a `capath` -- the hashed directory
    Debian, Ubuntu and FreeBSD ship -- it loads them by hash on demand, so a
    perfectly configured host reports zero until a handshake needs one. An
    earlier version of this test asserted the count and went red on every
    platform except macOS.
    """
    cert, key = _self_signed(tmp_path, split_key=True)
    api = TraefikApiConfig(url="https://example.invalid/api", cert=cert, key=key)

    context = collector._ssl_context(api)

    paths = ssl.get_default_verify_paths()
    assert paths.cafile or paths.capath, "no system trust store configured on this host"
    assert context.cert_store_stats() == ssl.create_default_context().cert_store_stats()


def test_a_configured_ca_replaces_the_system_roots(tmp_path):
    """`traefik.ca` is for an endpoint the system store does not know."""
    cert, key = _self_signed(tmp_path, split_key=True)
    api = TraefikApiConfig(url="https://example.invalid/api", cert=cert, key=key, ca=cert)

    context = collector._ssl_context(api)

    # Exactly the one certificate handed in, and not the system's set.
    #
    # Counted under `x509` rather than `x509_ca`: the fixture is a self-signed
    # leaf with no basicConstraints, so OpenSSL does not file it as a CA -- an
    # accurate trust store for a test, and not what a real `traefik.ca` holds.
    #
    # A `cafile` is always loaded eagerly, so this count is 1 everywhere. What
    # a default context reports is not portable (see the test above), so the
    # second assertion says only that the two differ -- which is the claim.
    assert context.cert_store_stats()["x509"] == 1
    assert context.cert_store_stats() != ssl.create_default_context().cert_store_stats()


def test_the_request_never_leaves_the_trust_decision_to_the_library(monkeypatch, tmp_path):
    """`verify=` is always an explicit context, so no library default applies.

    httpx2 verifies against the system store via `truststore` by default and
    httpx used certifi; neither default is ever reached from here. Pinning it
    keeps a future reader from re-deriving the trust path from the library's
    documentation instead of from this call.
    """
    cert, key = _self_signed(tmp_path, split_key=True)
    seen = {}

    def recording_get(url, **kwargs):
        seen.update(kwargs)
        return httpx2.Response(200, json={"routers": {}}, request=httpx2.Request("GET", url))

    monkeypatch.setattr(collector.httpx2, "get", recording_get)
    cfg = Config(traefik=TraefikApiConfig(url="https://example.invalid/api", cert=cert, key=key))

    collector.fetch_accepted(cfg)

    assert isinstance(seen["verify"], ssl.SSLContext)
