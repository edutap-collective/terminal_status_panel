"""Tests for parsing Traefik entrypoint configuration from service arguments."""

from terminal_status_panel.collectors import traefik_parse as parse

ARGS = [
    "--ping=true",
    "--ping.entryPoint=ping",
    "--providers.swarm.endpoint=http://sockproxy:2375",
    "--providers.swarm.exposedbydefault=false",
    "--providers.file.directory=/dynamic/",
    "--api.dashboard=true",
    "--api.basePath=/traefik",
    "--entrypoints.dashboard.address=:8082",
    "--entrypoints.dashboard.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entrypoints.ping.address=:8080",
    "--entrypoints.default.address=:8088",
    "--entrypoints.default.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entrypoints.https.address=:443",
    "--accessLog.filePath=/log/access.log",
    "--log.level=INFO",
    "--global.checknewversion=false",
    "--entryPoints.login_lmu_de.address=:2009",
    "--entryPoints.login_lmu_de.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.portalmgmt.address=:2020",
    "--entryPoints.portalmgmt.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.www_portal_uni_muenchen_de.address=:2010",
    "--entryPoints.www_portal_uni_muenchen_de.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.db-ui.address=:2008",
    "--entryPoints.kafbat.address=:2006",
]


def test_both_spellings_of_the_prefix_are_found():
    """The defaults use 'entrypoints', the vhost ones 'entryPoints'. A
    case-sensitive parser drops exactly the five that matter."""
    names = {ep.name for ep in parse.parse_entrypoints(ARGS)}
    assert names == {
        "dashboard", "ping", "default", "https",
        "login_lmu_de", "portalmgmt", "www_portal_uni_muenchen_de",
        "db-ui", "kafbat",
    }


def test_ports_are_parsed_from_the_address():
    by_name = {ep.name: ep for ep in parse.parse_entrypoints(ARGS)}
    assert by_name["portalmgmt"].port == 2020
    assert by_name["portalmgmt"].address == ":2020"
    assert by_name["https"].port == 443


def test_entrypoints_are_ordered_by_port():
    ports = [ep.port for ep in parse.parse_entrypoints(ARGS)]
    assert ports == sorted(ports)


def test_non_address_arguments_do_not_create_entrypoints():
    """forwardedHeaders and the like must not be mistaken for a declaration."""
    eps = parse.parse_entrypoints(ARGS)
    assert len(eps) == 9


def test_an_address_with_a_host_still_yields_its_port():
    eps = parse.parse_entrypoints(["--entryPoints.x.address=127.0.0.1:9000"])
    assert (eps[0].name, eps[0].port) == ("x", 9000)


def test_an_unparsable_address_keeps_the_entrypoint_without_a_port():
    eps = parse.parse_entrypoints(["--entryPoints.x.address=notaport"])
    assert (eps[0].name, eps[0].port) == ("x", None)


def test_no_arguments_yield_no_entrypoints():
    assert parse.parse_entrypoints([]) == []


KAFBAT_LABELS = {
    "traefik.enable": "true",
    "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt,kafbat",
    "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/portale/kafka-ui`)",
    "traefik.http.routers.kafbat-ui.tls": "true",
    "traefik.http.services.kafbat-ui.loadbalancer.server.port": "8080",
    "traefik.http.services.kafbat-ui.loadbalancer.server.scheme": "http",
    "traefik.swarm.network": "kafbat-ui",
}

IMAGE_API_LABELS = {
    "traefik.docker.network": "traefik-public",
    "traefik.enable": "true",
    "traefik.http.middlewares.image_api_stripprefix.stripprefix.prefixes":
        "/wallet/image-api",
    "traefik.http.routers.image_api.entrypoints": "websecure",
    "traefik.http.routers.image_api.middlewares": "image_api_stripprefix",
    "traefik.http.routers.image_api.rule":
        "Host(`www.portal.uni-muenchen.de`) && PathPrefix(`/wallet/image-api`)",
    "traefik.http.routers.image_api.tls": "true",
    "traefik.http.services.image_api.loadbalancer.server.port": "8090",
}


def test_a_router_on_several_entrypoints_keeps_all_of_them():
    routers, _, _ = parse.parse_labels(KAFBAT_LABELS, origin="kafbat-ui_kafbat-ui")
    assert len(routers) == 1
    assert routers[0].entrypoints == ["portalmgmt", "kafbat"]
    assert routers[0].rule == "PathPrefix(`/portale/kafka-ui`)"
    assert routers[0].tls is True
    assert routers[0].origin == "kafbat-ui_kafbat-ui"
    assert routers[0].source == "swarm"


def test_a_router_without_a_service_key_defaults_to_its_own_name():
    routers, _, _ = parse.parse_labels(KAFBAT_LABELS, origin="x")
    assert routers[0].service == "kafbat-ui"


def test_service_port_and_scheme_are_parsed():
    _, _, services = parse.parse_labels(KAFBAT_LABELS, origin="kafbat-ui_kafbat-ui")
    assert services["kafbat-ui"].port == 8080
    assert services["kafbat-ui"].scheme == "http"
    assert services["kafbat-ui"].docker_service == "kafbat-ui_kafbat-ui"


def test_a_middleware_keeps_its_kind_and_first_key():
    _, middlewares, _ = parse.parse_labels(IMAGE_API_LABELS, origin="x")
    mw = middlewares["image_api_stripprefix"]
    assert mw.kind == "stripprefix"
    assert "prefixes" in mw.detail
    assert "/wallet/image-api" in mw.detail


def test_a_router_keeps_its_middleware_references():
    routers, _, _ = parse.parse_labels(IMAGE_API_LABELS, origin="x")
    assert routers[0].middlewares == ["image_api_stripprefix"]


def test_labels_that_are_not_traefik_are_ignored():
    routers, middlewares, services = parse.parse_labels(
        {"com.docker.stack.namespace": "kafka", "lmu.service.description": "x"},
        origin="x",
    )
    assert (routers, middlewares, services) == ([], {}, {})


def test_a_service_without_a_port_still_appears():
    _, _, services = parse.parse_labels(
        {"traefik.http.services.plain.loadbalancer.server.scheme": "https"},
        origin="x",
    )
    assert services["plain"].scheme == "https"
    assert services["plain"].port is None


def test_routers_come_back_in_a_stable_order():
    labels = {
        "traefik.http.routers.zebra.rule": "Path(`/z`)",
        "traefik.http.routers.alpha.rule": "Path(`/a`)",
    }
    routers, _, _ = parse.parse_labels(labels, origin="x")
    assert [r.name for r in routers] == ["alpha", "zebra"]


DYNAMIC_YML = """\
http:
    routers:
        api:
            entrypoints: dashboard
            rule: PathPrefix(`/traefik`)
            service: api@internal
            tls: 'true'
        ping-router:
            entryPoints:
            - login_lmu_de
            - portalmgmt
            - www_portal_uni_muenchen_de
            - db-ui
            - kafbat
            - default
            rule: Path(`/_traefik_ping_`)
            service: ping@internal
            tls: 'true'
    serversTransports:
        dummy: {}
tls:
    certificates: []
    options:
        default:
            clientAuth:
                caFiles:
                - /certs/client_ca.pem
                clientAuthType: RequireAndVerifyClientCert
            sniStrict: false
"""


def test_file_routers_are_marked_as_coming_from_the_file_provider():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="traefik_dynamic_yml_v2")
    assert {r.name for r in routers} == {"api", "ping-router"}
    assert all(r.source == "file" for r in routers)
    assert all(r.origin == "traefik_dynamic_yml_v2" for r in routers)


def test_a_single_entrypoint_string_becomes_a_list():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    api = [r for r in routers if r.name == "api"][0]
    assert api.entrypoints == ["dashboard"]
    assert api.service == "api@internal"


def test_the_capitalised_entrypoints_key_is_also_read():
    """The file provider accepts entryPoints as well as entrypoints, and this
    fixture uses both — one per router."""
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    ping = [r for r in routers if r.name == "ping-router"][0]
    assert ping.entrypoints == [
        "login_lmu_de", "portalmgmt", "www_portal_uni_muenchen_de",
        "db-ui", "kafbat", "default",
    ]


def test_a_quoted_tls_string_counts_as_true():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    assert all(r.tls for r in routers)


def test_malformed_yaml_yields_nothing_rather_than_raising():
    assert parse.parse_dynamic_yaml("http: [unclosed", origin="x") == ([], {})


def test_yaml_without_an_http_section_yields_nothing():
    assert parse.parse_dynamic_yaml("tls:\n  stores: {}\n", origin="x") == ([], {})
