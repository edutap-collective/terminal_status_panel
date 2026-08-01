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
