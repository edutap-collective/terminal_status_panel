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


class _SpecService:
    """A Swarm service with a full ContainerSpec, and the tasks it runs."""

    def __init__(self, name, container_spec, labels=None, task_nodes=("node-1",)):
        self.name = name
        self.attrs = {
            "Spec": {"Labels": labels or {}, "TaskTemplate": {"ContainerSpec": container_spec}}
        }
        self._task_nodes = task_nodes

    def tasks(self, filters=None):
        return [{"NodeID": node} for node in self._task_nodes]


class _NodeClient(_FakeClient):
    """A client that knows which node it is asking from."""

    def __init__(self, *a, node_id="node-1", **k):
        super().__init__(*a, **k)
        self._node_id = node_id

    def info(self):
        return {"Swarm": {"NodeID": self._node_id}}


def test_unknown_entrypoints_reports_only_the_missing_ones():
    router = TraefikRouter(name="r", entrypoints=["adminpanel", "websecure"])
    assert collector.unknown_entrypoints(router, {"adminpanel"}) == ["websecure"]


def test_a_router_with_only_known_entrypoints_has_no_orphans():
    router = TraefikRouter(name="r", entrypoints=["adminpanel"])
    assert collector.unknown_entrypoints(router, {"adminpanel"}) == []


def test_a_router_with_no_entrypoints_is_not_an_orphan():
    """No entrypoint named means Traefik attaches it to all of them."""
    assert collector.unknown_entrypoints(TraefikRouter(name="r"), {"a"}) == []


def test_collect_reads_entrypoints_from_the_traefik_service():
    client = _FakeClient(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.adminpanel.address=:2020"]),
        ]
    )
    info = collector.collect_traefik(client)
    assert info.reachable is True
    assert [ep.name for ep in info.entrypoints] == ["adminpanel"]


def test_collect_joins_labels_from_every_service():
    client = _FakeClient(
        services=[
            _FakeService("traefik_traefik", args=["--entryPoints.adminpanel.address=:2020"]),
            _FakeService(
                "kafbat-ui_kafbat-ui",
                labels={
                    "traefik.http.routers.kafbat-ui.entrypoints": "adminpanel",
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
            _FakeService("traefik_traefik", args=["--entryPoints.adminpanel.address=:2020"]),
            _FakeService(
                "kafbat-ui_kafbat-ui",
                labels={
                    "traefik.http.routers.kafbat-ui.entrypoints": "adminpanel",
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
            _FakeService("traefik_traefik", args=["--entryPoints.adminpanel.address=:2020"]),
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
    assert collector.unknown_entrypoints(router, {"adminpanel"}) == ["websecure"]


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
            "kafbat-ui@swarm": {"entryPoints": ["adminpanel"], "status": "enabled"},
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
                args=["--entryPoints.adminpanel.address=:2020"],
                configs=["traefik_dynamic_yml_v4"],
            )
        ],
        configs=[
            _FakeConfig("traefik_dynamic_yml_v1", body.format(eps="db-ui, kafbat")),
            _FakeConfig("traefik_dynamic_yml_v4", body.format(eps="adminpanel")),
        ],
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["ping-router"]
    assert info.routers[0].entrypoints == ["adminpanel"]
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


# --------------------------------------------------------------------------- #
# Finding Traefik by `traefik.match`, and reading its configuration the way
# Traefik does
# --------------------------------------------------------------------------- #

STATIC_YAML = """\
entryPoints:
  http:
    address: ":80"
  https:
    address: ":443"
providers:
  file:
    directory: /etc/traefik/dynamic
"""

DYNAMIC_MIDDLEWARES = """\
http:
  middlewares:
    tools-auth:
      basicAuth:
        usersFile: /run/secrets/htpasswd
"""

PING_ROUTER = (
    "http:\n  routers:\n    ping-router:\n"
    "      rule: PathPrefix(`/ping`)\n      service: ping@internal\n"
)


def _file_based_traefik(tmp_path, task_nodes=("node-1",)):
    static_file = tmp_path / "traefik.yaml"
    static_file.write_text(STATIC_YAML)
    dynamic_file = tmp_path / "dynamic.yaml"
    dynamic_file.write_text(DYNAMIC_MIDDLEWARES)
    return _SpecService(
        "demo_traefik",
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(static_file),
                    "Target": "/etc/traefik/traefik.yaml",
                },
                {
                    "Type": "bind",
                    "Source": str(dynamic_file),
                    "Target": "/etc/traefik/dynamic/dynamic.yaml",
                },
            ],
        },
        task_nodes=task_nodes,
    )


def test_a_file_configured_traefik_yields_its_entrypoints(tmp_path):
    client = _NodeClient(services=[_file_based_traefik(tmp_path)])

    info = collector.collect_traefik(client, match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["http", "https"]
    assert info.static_problem is None
    assert info.static_source == "/etc/traefik/traefik.yaml"


def test_the_bind_mounted_dynamic_file_is_read_by_path(tmp_path):
    client = _NodeClient(services=[_file_based_traefik(tmp_path)])

    info = collector.collect_traefik(client, match=("demo_traefik",))

    assert "tools-auth" in info.middlewares
    assert info.file_provider_error is None


def test_a_bind_mount_on_another_node_is_reported_not_guessed(tmp_path):
    client = _NodeClient(services=[_file_based_traefik(tmp_path, task_nodes=("node-2",))])

    info = collector.collect_traefik(client, match=("demo_traefik",))

    assert info.entrypoints == []
    assert "not readable on this node" in info.static_problem
    assert "/etc/traefik/traefik.yaml" in info.static_problem


def test_an_unmounted_config_file_is_undecidable_and_no_flags_are_drawn():
    service = _SpecService(
        "demo_traefik",
        {"Args": ["--configFile=/etc/traefik/traefik.yaml", "--entrypoints.web.address=:80"]},
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert "is not mounted" in info.static_problem


def test_flags_beside_a_found_file_are_noted(tmp_path):
    service = _file_based_traefik(tmp_path)
    service.attrs["Spec"]["TaskTemplate"]["ContainerSpec"]["Args"].append("--log.level=DEBUG")

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.static_notes == [
        "static configuration from /etc/traefik/traefik.yaml;"
        " Traefik ignores 1 other command-line flag"
    ]


def test_without_a_file_the_flags_and_config_generations_work_as_before():
    """The CLI + Docker-config shape: the unit-level stand-in for the live diff."""
    service = _SpecService(
        "traefik_traefik",
        {
            "Args": ["--entrypoints.https.address=:443", "--providers.file.directory=/dynamic/"],
            "Configs": [
                {
                    "ConfigName": "traefik_dynamic_yml_v3",
                    "File": {"Name": "/dynamic/00-cluster.yml"},
                },
                {"ConfigName": "traefik_rootca_x_v1", "File": {"Name": "/certs/rootca_x.pem"}},
            ],
        },
    )
    client = _NodeClient(
        services=[service],
        configs=[
            _FakeConfig("traefik_dynamic_yml_v2", "http:\n  routers:\n    stale: {}\n"),
            _FakeConfig("traefik_dynamic_yml_v3", PING_ROUTER),
            _FakeConfig("traefik_rootca_x_v1", "-----BEGIN CERTIFICATE-----"),
        ],
    )

    info = collector.collect_traefik(client)

    assert [ep.name for ep in info.entrypoints] == ["https"]
    assert [r.name for r in info.routers] == ["ping-router"]
    assert info.routers[0].origin == "traefik_dynamic_yml_v3"
    assert info.static_source == "command-line flags"
    assert info.file_provider_error is None


def test_environment_configuration_is_used_when_there_is_neither_file_nor_flag():
    service = _SpecService("demo_traefik", {"Env": ["TRAEFIK_ENTRYPOINTS_WEB_ADDRESS=:80"]})

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["web"]


def test_no_matching_workload_names_the_configured_pattern():
    info = collector.collect_traefik(
        _NodeClient(services=[_FakeService("demo_other")]), match=("demo_traefik",)
    )

    assert info.static_problem == (
        "no Traefik service or container matches traefik.match (demo_traefik)"
    )


def test_a_compose_traefik_container_is_found_and_read(tmp_path):
    static_file = tmp_path / "traefik.yaml"
    static_file.write_text(STATIC_YAML)
    container = _FakeContainer("demo-traefik-1")
    container.attrs.update(
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(static_file),
                    "Destination": "/etc/traefik/traefik.yaml",
                }
            ],
        }
    )

    info = collector.collect_traefik(_FakeClient(containers=[container]), match=("demo-traefik",))

    assert [ep.name for ep in info.entrypoints] == ["http", "https"]


def test_a_templated_dynamic_file_is_noted_and_not_parsed(tmp_path):
    service = _file_based_traefik(tmp_path)
    (tmp_path / "dynamic.yaml").write_text(
        'http:\n  routers:\n    r:\n      rule: Host(`{{ env "H" }}`)\n'
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.routers == []
    assert "templated — not evaluated" in info.file_provider_error


def test_a_relative_provider_path_is_resolved_against_the_declared_working_directory():
    """Traefik resolves a relative path against its own working directory."""
    service = _SpecService(
        "demo_traefik",
        {
            "Args": ["--entrypoints.web.address=:80", "--providers.file.directory=dynamic"],
            "Dir": "/etc/traefik",
            "Configs": [
                {
                    "ConfigName": "demo_routes_v1",
                    "File": {"Name": "/etc/traefik/dynamic/routes.yml"},
                }
            ],
        },
    )
    client = _NodeClient(services=[service], configs=[_FakeConfig("demo_routes_v1", PING_ROUTER)])

    info = collector.collect_traefik(client, match=("demo_traefik",))

    assert [r.name for r in info.routers] == ["ping-router"]
    assert info.routers[0].origin == "demo_routes_v1"
    assert info.file_provider_error is None


def test_a_relative_provider_path_without_a_working_directory_reads_nothing():
    """The image's own WORKDIR is not visible from the spec, so the directory
    Traefik reads is unknown. Neither a guessed working directory nor the
    config-generation rule may stand in for it -- the prefixed, mounted config
    below is exactly what that rule would have drawn."""
    service = _SpecService(
        "traefik_traefik",
        {
            "Args": ["--entrypoints.web.address=:80", "--providers.file.directory=dynamic"],
            "Configs": [
                {"ConfigName": "traefik_dynamic_yml_v1", "File": {"Name": "/dynamic/routes.yml"}}
            ],
        },
    )
    client = _NodeClient(
        services=[service], configs=[_FakeConfig("traefik_dynamic_yml_v1", PING_ROUTER)]
    )

    info = collector.collect_traefik(client)

    assert info.routers == []
    assert info.file_provider_error == (
        "dynamic is relative and the container's working directory is not declared — not read"
    )


# --------------------------------------------------------------------------- #
# Looking for the static file the way Traefik does: a candidate under a
# bind-mounted directory is there only if the file is
# --------------------------------------------------------------------------- #


def _dir_based_traefik(tmp_path, args=(), task_nodes=("node-1",)):
    """Traefik with /etc/traefik bind-mounted from a host directory."""
    host_dir = tmp_path / "etc-traefik"
    host_dir.mkdir()
    service = _SpecService(
        "demo_traefik",
        {
            "Args": list(args),
            "Mounts": [{"Type": "bind", "Source": str(host_dir), "Target": "/etc/traefik"}],
        },
        task_nodes=task_nodes,
    )
    return service, host_dir


def test_a_bind_mounted_directory_is_searched_for_the_extension_it_holds(tmp_path):
    service, host_dir = _dir_based_traefik(tmp_path)
    (host_dir / "traefik.yml").write_text(STATIC_YAML)

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["http", "https"]
    assert info.static_source == "/etc/traefik/traefik.yml"
    assert info.static_problem is None


def test_a_bind_mounted_directory_without_a_static_file_leaves_the_flags_in_charge(tmp_path):
    service, _ = _dir_based_traefik(tmp_path, args=["--entrypoints.web.address=:80"])

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["web"]
    assert info.static_source == "command-line flags"
    assert info.static_problem is None


def test_a_bind_mounted_directory_on_another_node_makes_the_search_undecidable(tmp_path):
    service, host_dir = _dir_based_traefik(
        tmp_path, args=["--entrypoints.web.address=:80"], task_nodes=("node-2",)
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_source is None
    assert info.static_problem == (
        f"/etc/traefik is a bind mount of {host_dir}, not readable on this node"
        " (Traefik runs on node-2) — whether it holds traefik.toml/.yaml/.yml"
        " cannot be checked from here"
    )


def test_a_volume_over_the_default_location_makes_the_search_undecidable():
    service = _SpecService(
        "demo_traefik",
        {
            "Args": ["--entrypoints.web.address=:80"],
            "Mounts": [{"Type": "volume", "Source": "traefik_conf", "Target": "/etc/traefik"}],
        },
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_problem == (
        "/etc/traefik is on volume traefik_conf — whether it holds traefik.toml/.yaml/.yml"
        " cannot be checked from here"
    )


def test_a_missing_config_file_under_a_checkable_bind_falls_through_like_traefik(tmp_path):
    """Traefik skips a --configFile that does not exist, tries its default
    locations, and then takes its flags."""
    service, _ = _dir_based_traefik(
        tmp_path,
        args=["--configFile=/etc/traefik/custom.yaml", "--entrypoints.web.address=:80"],
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["web"]
    assert info.static_source == "command-line flags"
    assert info.static_problem is None


# --------------------------------------------------------------------------- #
# 0.12.2's texts in the unchanged shape
# --------------------------------------------------------------------------- #


def test_an_undecodable_config_in_the_flags_and_config_shape_keeps_the_old_text():
    class _Undecodable:
        name = "traefik_dynamic_yml_v3"
        attrs = {"Spec": {"Data": "not base64 %%%"}}

    service = _SpecService(
        "traefik_traefik",
        {
            "Args": ["--entrypoints.https.address=:443", "--providers.file.directory=/dynamic/"],
            "Configs": [
                {
                    "ConfigName": "traefik_dynamic_yml_v3",
                    "File": {"Name": "/dynamic/00-cluster.yml"},
                }
            ],
        },
    )

    info = collector.collect_traefik(_NodeClient(services=[service], configs=[_Undecodable()]))

    assert info.routers == []
    assert info.file_provider_error == "traefik_dynamic_yml_v3: config data is not decodable"


# --------------------------------------------------------------------------- #
# A relative --configFile
# --------------------------------------------------------------------------- #


def test_a_relative_config_file_is_resolved_against_the_declared_working_directory(tmp_path):
    static_file = tmp_path / "traefik.yaml"
    static_file.write_text(STATIC_YAML)
    service = _SpecService(
        "demo_traefik",
        {
            "Args": ["--configFile=traefik.yaml"],
            "Dir": "/etc/traefik",
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(static_file),
                    "Target": "/etc/traefik/traefik.yaml",
                }
            ],
        },
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["http", "https"]
    assert info.static_source == "/etc/traefik/traefik.yaml"


def test_a_relative_config_file_without_a_working_directory_is_not_guessed():
    service = _SpecService(
        "demo_traefik",
        {"Args": ["--configFile=traefik.yaml", "--entrypoints.web.address=:80"]},
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_problem == (
        "--configFile=traefik.yaml is relative and the container's working directory"
        " is not declared — not read"
    )


# --------------------------------------------------------------------------- #
# Never raises: shapes the Docker API should not send, but might
# --------------------------------------------------------------------------- #


def test_a_tasks_listing_of_the_wrong_shape_counts_as_not_here(tmp_path):
    service = _file_based_traefik(tmp_path)
    service.tasks = lambda filters=None: None

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert "not readable on this node" in info.static_problem


@pytest.mark.parametrize(
    "spec",
    [
        {"Mounts": 5},
        {"Configs": 5},
        {"Args": 5},
        {"Dir": 5},
        {"Mounts": [{"Type": "bind", "Source": 5, "Target": "/etc/traefik/traefik.yaml"}]},
        {"Mounts": [{"Type": "bind", "Source": "/srv/traefik", "Target": 5}]},
    ],
    ids=["mounts", "configs", "args", "dir", "source", "target"],
)
def test_a_traefik_spec_of_the_wrong_shape_is_ignored_not_raised(spec):
    service = _SpecService("demo_traefik", {"Env": ["TRAEFIK_ENTRYPOINTS_WEB_ADDRESS=:80"], **spec})

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.error is None
    assert [ep.name for ep in info.entrypoints] == ["web"]


def test_an_unexpected_failure_reading_traefik_s_configuration_is_a_problem(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(collector, "candidate_paths", boom)
    traefik = _SpecService("demo_traefik", {"Args": ["--entrypoints.web.address=:80"]})
    app = _FakeService("demo_app", labels={"traefik.http.routers.app.entrypoints": "web"})

    info = collector.collect_traefik(_NodeClient(services=[traefik, app]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_problem == "Traefik's configuration could not be read: RuntimeError: boom"
    # The labels were read before, and stay.
    assert [r.name for r in info.routers] == ["app"]


# --------------------------------------------------------------------------- #
# Paths the first round left untested
# --------------------------------------------------------------------------- #


def test_an_unparseable_static_file_names_the_parser_s_reason(tmp_path):
    from terminal_status_panel.collectors.traefik_static import parse_static_document

    broken = "entryPoints: [unclosed\n"
    service = _file_based_traefik(tmp_path)
    (tmp_path / "traefik.yaml").write_text(broken)
    with pytest.raises(ValueError) as parsed:
        parse_static_document(broken, "yaml")

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_problem == f"/etc/traefik/traefik.yaml: {parsed.value}"


def test_a_static_file_without_entrypoints_says_so_and_the_provider_is_still_read(tmp_path):
    service = _file_based_traefik(tmp_path)
    (tmp_path / "traefik.yaml").write_text(
        "providers:\n  file:\n    directory: /etc/traefik/dynamic\n"
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.entrypoints == []
    assert info.static_problem == "/etc/traefik/traefik.yaml declares no entrypoints"
    assert "tools-auth" in info.middlewares


def test_a_matching_service_wins_over_a_matching_container():
    service = _SpecService("demo_traefik", {"Args": ["--entrypoints.web.address=:80"]})
    container = _FakeContainer("demo_traefik-compose-1")
    container.attrs["Args"] = ["--entrypoints.other.address=:81"]

    info = collector.collect_traefik(
        _NodeClient(services=[service], containers=[container]), match=("demo_traefik",)
    )

    assert [ep.name for ep in info.entrypoints] == ["web"]


# --------------------------------------------------------------------------- #
# Copilot round 1 (#41)
# --------------------------------------------------------------------------- #


def test_traefik_match_ignores_case_for_a_service():
    service = _SpecService("Demo_Traefik", {"Args": ["--entrypoints.web.address=:80"]})

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert [ep.name for ep in info.entrypoints] == ["web"]


def test_traefik_match_ignores_case_for_a_container():
    container = _FakeContainer("Demo-Traefik-1")
    container.attrs["Args"] = ["--entrypoints.web.address=:80"]

    info = collector.collect_traefik(_FakeClient(containers=[container]), match=("DEMO-traefik",))

    assert [ep.name for ep in info.entrypoints] == ["web"]


def test_a_templated_config_generation_is_noted_and_not_parsed():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[], configs=["traefik_dynamic_yml_v2"])],
        configs=[
            _FakeConfig(
                "traefik_dynamic_yml_v2",
                'http:\n  routers:\n    r:\n      rule: Host(`{{ env "H" }}`)\n',
            )
        ],
    )

    info = collector.collect_traefik(client)

    assert info.routers == []
    assert info.file_provider_error == "traefik_dynamic_yml_v2: templated — not evaluated"


def test_a_daemon_without_swarm_lists_no_configs_and_blames_nothing_on_them(tmp_path):
    """Swarm configs exist only on a Swarm daemon. Asking a Compose-only one
    records its "not a swarm manager" answer as an unreadable file provider,
    although the bind-mounted files it actually reads were read fine."""
    calls = []

    class _ComposeOnly(_FakeClient):
        @property
        def services(self):
            raise RuntimeError("This node is not a swarm manager")

        @property
        def configs(self):
            calls.append("configs")
            raise RuntimeError("This node is not a swarm manager")

    static_file = tmp_path / "traefik.yaml"
    static_file.write_text(STATIC_YAML)
    dynamic_file = tmp_path / "dynamic.yaml"
    dynamic_file.write_text(DYNAMIC_MIDDLEWARES)
    container = _FakeContainer("demo-traefik-1")
    container.attrs.update(
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(static_file),
                    "Destination": "/etc/traefik/traefik.yaml",
                },
                {
                    "Type": "bind",
                    "Source": str(dynamic_file),
                    "Destination": "/etc/traefik/dynamic/dynamic.yaml",
                },
            ],
        }
    )

    info = collector.collect_traefik(_ComposeOnly(containers=[container]), match=("demo-traefik",))

    assert calls == []
    assert info.file_provider_error is None
    assert "tools-auth" in info.middlewares


def test_an_empty_match_makes_no_docker_call_at_all():
    class _Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"Docker was asked for {name}")

    info = collector.collect_traefik(_Untouchable(), match=())

    assert info.reachable is False
    assert info.entrypoints == []
    assert info.routers == []


def test_more_than_one_file_provider_failure_is_counted_not_dropped(tmp_path):
    from rich.console import Console

    from terminal_status_panel.render.traefik import traefik_section

    static_file = tmp_path / "traefik.yaml"
    static_file.write_text(STATIC_YAML)
    dynamic_dir = tmp_path / "dynamic"
    dynamic_dir.mkdir()
    templated = 'http:\n  routers:\n    r:\n      rule: Host(`{{ env "H" }}`)\n'
    (dynamic_dir / "a.yml").write_text(templated)
    (dynamic_dir / "b.yml").write_text(templated)
    service = _SpecService(
        "demo_traefik",
        {
            "Args": ["--configFile=/etc/traefik/traefik.yaml"],
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(static_file),
                    "Target": "/etc/traefik/traefik.yaml",
                },
                {"Type": "bind", "Source": str(dynamic_dir), "Target": "/etc/traefik/dynamic"},
            ],
        },
    )

    info = collector.collect_traefik(_NodeClient(services=[service]), match=("demo_traefik",))

    assert info.file_provider_error == (
        f"{dynamic_dir / 'a.yml'}: templated — not evaluated (+1 more)"
    )
    console = Console(width=400, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(traefik_section(info, Config()))
    assert "templated — not evaluated (+1 more)" in capture.get()


# --------------------------------------------------------------------------- #
# Copilot round 2 (#41): configs are asked for where Swarm is active
# --------------------------------------------------------------------------- #

_NOT_A_MANAGER = (
    "This node is not a swarm manager. Worker nodes can't be used to view or modify"
    " cluster state. Please run this command on a manager node or promote the current"
    " node to a manager."
)


def _non_manager_client(state, calls):
    """A daemon whose services and configs listings answer "not a swarm manager"."""

    class _NonManager(_FakeClient):
        def info(self):
            calls.append("info")
            return {"Swarm": {"LocalNodeState": state}}

        @property
        def services(self):
            raise RuntimeError(_NOT_A_MANAGER)

        @property
        def configs(self):
            calls.append("configs")
            raise RuntimeError(_NOT_A_MANAGER)

    return _NonManager()


def test_a_daemon_where_swarm_is_inactive_is_not_asked_for_configs():
    calls = []

    info = collector.collect_traefik(_non_manager_client("inactive", calls))

    assert calls == ["info"]
    assert info.file_provider_error is None


def test_a_swarm_worker_reports_the_configs_it_cannot_list_as_0_12_2_did():
    calls = []

    info = collector.collect_traefik(_non_manager_client("active", calls))

    assert calls == ["info", "configs"]
    assert info.file_provider_error == f"RuntimeError: {_NOT_A_MANAGER}"


def test_a_manager_whose_services_listing_succeeded_is_not_asked_about_swarm():
    calls = []

    class _Manager(_FakeClient):
        def info(self):
            calls.append("info")
            return {"Swarm": {"LocalNodeState": "active"}}

    info = collector.collect_traefik(_Manager(services=[_FakeService("traefik_traefik", args=[])]))

    assert calls == []
    assert info.file_provider_error is None


# --------------------------------------------------------------------------- #
# Final review (0.13.0): a Swarm worker states nothing it did not look at
# --------------------------------------------------------------------------- #

_SWARM_TASK = "com.docker.swarm.service.name"


class _Worker(_FakeClient):
    """A Swarm worker: it cannot list services or configs, and its Traefik
    task container carries the Swarm label, as every task container does."""

    def __init__(self, state="active"):
        super().__init__(
            containers=[
                _FakeContainer("traefik_traefik.1.abc", {_SWARM_TASK: "traefik_traefik"}),
                _FakeContainer(
                    "myapp_api.1.def",
                    {_SWARM_TASK: "myapp_api", "traefik.http.routers.api.entrypoints": "https"},
                ),
                _FakeContainer(
                    "dev-web-1",
                    {
                        "traefik.http.routers.web.entrypoints": "https",
                        "traefik.http.routers.web.rule": "PathPrefix(`/web`)",
                        "traefik.http.services.web.loadbalancer.server.port": "8000",
                    },
                ),
            ]
        )
        self.state = state
        self.calls = []

    def info(self):
        self.calls.append("info")
        if self.state is None:
            raise RuntimeError("info unavailable")
        return {"Swarm": {"LocalNodeState": self.state, "NodeID": "swarm01-wrk-02"}}

    @property
    def services(self):
        raise RuntimeError(_NOT_A_MANAGER)

    @property
    def configs(self):
        self.calls.append("configs")
        raise RuntimeError(_NOT_A_MANAGER)


def test_a_swarm_worker_collects_exactly_what_0_12_2_collected():
    """The worker could not look at any service, so it names no reason of its
    own: every field is what 0.12.2 produced for the same daemon."""
    from terminal_status_panel.model import TraefikServiceRef

    worker = _Worker()

    info = collector.collect_traefik(worker)

    not_a_manager = f"RuntimeError: {_NOT_A_MANAGER}"
    assert info.entrypoints == []
    assert info.ping_entrypoint is None
    assert info.routers == [
        TraefikRouter(
            name="web",
            entrypoints=["https"],
            rule="PathPrefix(`/web`)",
            service="web",
            source="swarm",
            origin="dev-web-1",
        )
    ]
    assert info.middlewares == {}
    assert info.services == {
        "web": TraefikServiceRef(name="web", port=8000, docker_service="dev-web-1")
    }
    assert info.file_provider_error == not_a_manager
    assert info.service_error == not_a_manager
    assert info.container_error is None
    assert info.error is None
    assert info.static_problem is None
    # One answer from `docker info` serves every question asked of it.
    assert worker.calls == ["info", "configs"]


#: 0.12.2's TRAEFIK WIRING for `_Worker`, rendered from the 0.12.2 tree.
_WORKER_0_12_2 = {
    80: [
        "TRAEFIK WIRING " + "─" * 65,
        "⚠️  no entrypoints found — the tree cannot be drawn, the routers below could not",
        "be placed",
        "",
        "⚠️  file provider unreadable: RuntimeError: This node is not a swarm manager.",
        "Worker nodes can't be used to view or modify cluster state. Please run this",
        "command on a manager node or promote the current node to a manager. — routers",
        "defined there are missing",
        "",
        "⚠️  Swarm service labels unreadable: RuntimeError: This node is not a swarm",
        "manager. Worker nodes can't be used to view or modify cluster state. Please run",
        "this command on a manager node or promote the current node to a manager. —",
        "routers declared by Swarm services are missing",
        "",
        "ORPHANED ROUTERS",
        "  ⚠️  web        entrypoint `https` — no entrypoint could be read   [dev-web-1]",
        "     PathPrefix(`/web`)",
        "     └─ → web :8000  ✗ no such service",
    ],
    215: [
        "TRAEFIK WIRING " + "─" * 200,
        "⚠️  no entrypoints found — the tree cannot be drawn, the routers below could not be placed",
        "",
        "⚠️  file provider unreadable: RuntimeError: This node is not a swarm manager. Worker"
        " nodes can't be used to view or modify cluster state. Please run this command on a"
        " manager node or promote the current node to a",
        "manager. — routers defined there are missing",
        "",
        "⚠️  Swarm service labels unreadable: RuntimeError: This node is not a swarm manager."
        " Worker nodes can't be used to view or modify cluster state. Please run this command"
        " on a manager node or promote the current node",
        "to a manager. — routers declared by Swarm services are missing",
        "",
        "ORPHANED ROUTERS",
        "  ⚠️  web        entrypoint `https` — no entrypoint could be read   [dev-web-1]",
        "     PathPrefix(`/web`)",
        "     └─ → web :8000  ✗ no such service",
    ],
}


@pytest.mark.parametrize("width", [80, 215])
def test_a_swarm_worker_renders_0_12_2_s_banner(width):
    from rich.console import Console

    from terminal_status_panel.model import SwarmInfo
    from terminal_status_panel.render.traefik import traefik_section

    info = collector.collect_traefik(_Worker())
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(traefik_section(info, Config(), SwarmInfo(reachable=True, enabled=True)))

    assert [line.rstrip() for line in capture.get().splitlines()] == _WORKER_0_12_2[width]


@pytest.mark.parametrize("state", [None, "pending", "locked"], ids=["no-info", "pending", "locked"])
def test_a_swarm_state_other_than_inactive_after_a_failed_listing_claims_no_match(state):
    """`docker info` failed, or reports a state that may still hold services:
    whether a Traefik service exists is unknown."""
    info = collector.collect_traefik(_Worker(state=state))

    assert info.static_problem is None


def test_a_host_without_swarm_and_without_traefik_names_the_pattern():
    """Swarm is not active, so there are no services; no container matches."""
    info = collector.collect_traefik(_Worker(state="inactive"))

    assert info.static_problem == (
        "no Traefik service or container matches traefik.match (traefik_traefik)"
    )


# --------------------------------------------------------------------------- #
# The reason may only name what was actually looked at
# --------------------------------------------------------------------------- #


class _ServicesEmptyContainersFail(_FakeClient):
    """Every service listed and none matched, while the container listing failed.

    Reachable, because one of the two listings answered -- so the collector
    carries on and has to say something about a host where containers were
    never seen.
    """

    def __init__(self):
        super().__init__(services=[])

    @property
    def containers(self):
        class _Failing:
            def list(self, *a, **k):
                raise TimeoutError("Read timed out.")

        return _Failing()


def test_a_failed_container_listing_is_not_reported_as_no_container_matching():
    """Claiming no container matches would report an unread list as an empty one."""
    info = collector.collect_traefik(_ServicesEmptyContainersFail())

    assert info.static_problem == (
        "no Traefik service matches traefik.match (traefik_traefik); "
        "containers could not be listed: TimeoutError: Read timed out."
    )


def test_both_listings_failing_stops_before_any_reason_is_formed():
    """Nothing was read at all. That is the combined listing error, and no claim
    about what does or does not match belongs beside it -- which is also why the
    reason above may assume the services were read whenever the containers were
    not."""

    class _BothFail(_ServicesEmptyContainersFail):
        @property
        def services(self):
            class _Failing:
                def list(self, *a, **k):
                    raise TimeoutError("Read timed out.")

            return _Failing()

    info = collector.collect_traefik(_BothFail())

    assert info.static_problem is None
    assert info.error == (
        "services: TimeoutError: Read timed out.; containers: TimeoutError: Read timed out."
    )


# --------------------------------------------------------------------------- #
# Final review (0.13.0): a failed configs listing is one note, not one per config
# --------------------------------------------------------------------------- #


class _ConfigsTimeOut(_NodeClient):
    @property
    def configs(self):
        class _Failing:
            def list(self, *a, **k):
                raise TimeoutError("Read timed out.")

        return _Failing()


def _flags_and_configs_traefik():
    """The CLI flags + Docker-config shape of 0.12.2, with two dynamic configs."""
    return _SpecService(
        "traefik_traefik",
        {
            "Args": [
                "--ping.entryPoint=ping",
                "--providers.file.directory=/dynamic/",
                "--entrypoints.ping.address=:8080",
                "--entrypoints.https.address=:443",
            ],
            "Configs": [
                {"ConfigName": "traefik_dynamic_yml_v3", "File": {"Name": "/dynamic/00-base.yml"}},
                {"ConfigName": "traefik_dynamic_extra_v2", "File": {"Name": "/dynamic/extra.yml"}},
                {"ConfigName": "traefik_rootca_v1", "File": {"Name": "/certs/rootca.pem"}},
            ],
        },
    )


def test_a_failed_configs_listing_in_the_0_12_2_shape_is_its_single_note():
    info = collector.collect_traefik(_ConfigsTimeOut(services=[_flags_and_configs_traefik()]))

    assert [ep.name for ep in info.entrypoints] == ["ping", "https"]
    assert info.file_provider_error == "TimeoutError: Read timed out."
    assert info.file_provider_notes == ["TimeoutError: Read timed out."]


def test_a_config_missing_from_a_successful_listing_is_named():
    client = _NodeClient(
        services=[_flags_and_configs_traefik()],
        configs=[_FakeConfig("traefik_dynamic_yml_v3", PING_ROUTER)],
    )

    info = collector.collect_traefik(client)

    assert [r.name for r in info.routers] == ["ping-router"]
    assert info.file_provider_error == "traefik_dynamic_extra_v2: config not found"


def test_a_config_served_static_file_behind_a_failed_listing_says_the_listing_failed():
    service = _SpecService(
        "traefik_traefik",
        {
            "Args": ["--configFile=/etc/traefik/traefik.yml"],
            "Configs": [
                {"ConfigName": "traefik_static_v1", "File": {"Name": "/etc/traefik/traefik.yml"}}
            ],
        },
    )

    info = collector.collect_traefik(_ConfigsTimeOut(services=[service]))

    assert info.entrypoints == []
    assert info.static_problem == (
        "entrypoints are configured in /etc/traefik/traefik.yml,"
        " traefik_static_v1: Docker configs could not be listed"
    )
    assert info.file_provider_error == "TimeoutError: Read timed out."
