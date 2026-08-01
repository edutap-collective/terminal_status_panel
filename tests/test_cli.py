import pytest

from terminal_status_panel import cli
from terminal_status_panel.config import Config


@pytest.fixture
def isolated_cli(monkeypatch):
    """Keep the CLI tests off the real Docker socket and the real system.

    ``collect_all`` builds a Docker client for the health section, and the
    health section itself runs ``sudo -n wg``, ``sudo -n gluster`` and real DNS
    lookups. Without this, a unit test would talk to whatever daemon, sudo rule
    and resolver happen to exist on the machine — invisible on a developer's
    macOS box, up to a full health budget per test on a Debian CI runner.

    ``collect_updates`` shells out to apt-check for the same reason. The purely
    local collectors (system, resources) are left alone: they read /proc and
    psutil, so they are fast and cannot reach off the machine.

    A test that wants a specific ``collect_health`` simply overrides it again.
    """
    monkeypatch.setattr(cli, "_docker_client", lambda cfg: None)
    monkeypatch.setattr(cli, "collect_health", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_updates", lambda timeout=None: None)
    return monkeypatch


def test_main_exits_zero_and_prints(isolated_cli, capsys):
    rc = cli.main(["--width", "100"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SYSTEM OVERVIEW" in out
    assert "SYSTEM LOAD" in out
    assert "DOCKER" in out


def test_main_never_raises_even_if_collection_breaks(isolated_cli, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    # Break one collector entirely; main must still exit 0.
    isolated_cli.setattr(cli, "collect_resources", boom)
    rc = cli.main([])
    assert rc == 0


def test_width_flag_overrides(isolated_cli, capsys):
    rc = cli.main(["--width", "40"])
    assert rc == 0
    # Narrow width still produces output without crashing.
    assert capsys.readouterr().out.strip() != ""


def test_server_entrypoint_renders_only_server(capsys):
    rc = cli.server_main(["--width", "100"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SYSTEM OVERVIEW" in out
    assert "SYSTEM STATUS" in out
    assert "DOCKER INFOS" not in out


def test_docker_entrypoint_renders_only_docker(capsys):
    rc = cli.docker_main(["--width", "100"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOCKER INFOS" in out
    assert "SYSTEM OVERVIEW" not in out


def test_docker_section_skips_system_collectors(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "collect_system", lambda *a, **k: called.append("system"))
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: called.append("res"))
    monkeypatch.setattr(cli, "collect_updates", lambda *a, **k: called.append("upd"))
    monkeypatch.setattr(cli, "collect_docker", lambda *a, **k: called.append("docker"))
    cli.collect_all(cli.load_config(None), sections=("docker",))
    assert called == ["docker"]  # only the docker collector ran


def test_sections_flag_selects(capsys):
    rc = cli.main(["--width", "100", "--sections", "docker"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOCKER INFOS" in out
    assert "SYSTEM OVERVIEW" not in out


def test_health_main_returns_zero(isolated_cli):
    assert cli.health_main([]) == 0


def test_collect_all_skips_health_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []


def test_collect_all_calls_health_when_selected(isolated_cli):
    called = []

    def fake(cfg, peer_names, client=None, resolve_fqdn=None, resolve_peer_names=None):
        called.append((peer_names, resolve_fqdn))
        return None

    isolated_cli.setattr(cli, "collect_health", fake)
    cli.collect_all(Config(), sections=("health",))
    assert len(called) == 1


def test_the_health_client_socket_outlives_the_slowest_enabled_probe():
    """docker-py bounds an exec by the client's socket timeout. With
    docker.timeout = 1.5 the Kafka probe (~2.6 s of JVM startup) could never
    have succeeded, whatever health.timeout.kafka said."""
    cfg = Config()
    assert cfg.docker_timeout == 1.5
    assert cli._health_socket_timeout(cfg) == cfg.health.timeouts["kafka"] == 4.0


def test_the_health_client_never_drops_below_the_docker_timeout():
    cfg = Config()
    cfg.docker_timeout = 9.0
    assert cli._health_socket_timeout(cfg) == 9.0


def test_the_health_client_ignores_the_timeouts_of_disabled_kinds():
    cfg = Config()
    cfg.health.enabled = ["postgres"]
    assert cli._health_socket_timeout(cfg) == 1.5  # postgres, not kafka


def test_the_health_client_is_built_at_the_docker_timeout(monkeypatch):
    """docker.from_env() negotiates the API version with the daemon while it is
    constructed — on the main thread, before the budget. That request must stay
    bounded by docker.timeout; only the budgeted probe requests get the larger
    health timeout."""
    import sys
    import types

    built = {}

    class _Api:
        timeout = None

    class _Client:
        api = _Api()

    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda **kwargs: built.update(kwargs) or _Client()
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    cfg = Config()
    client = cli._docker_client(cfg)
    assert built["timeout"] == cfg.docker_timeout == 1.5
    assert client.api.timeout == cli._health_socket_timeout(cfg) == 4.0


def test_collect_all_never_reaches_the_docker_daemon_before_the_budget(isolated_cli):
    """The Swarm node list used to be fetched here, on the main thread, over the
    health client — so a hung daemon delayed the login by the largest per-kind
    timeout instead of by docker.timeout. It is handed over as a callable and
    called inside the budget instead."""
    reached = []

    class _Client:
        @property
        def nodes(self):
            reached.append(True)
            raise AssertionError("the node list must be fetched inside the budget")

    captured = {}

    def fake(cfg, peer_names, client=None, resolve_fqdn=None, resolve_peer_names=None):
        captured["resolve_peer_names"] = resolve_peer_names
        return None

    isolated_cli.setattr(cli, "_docker_client", lambda cfg: _Client())
    isolated_cli.setattr(cli, "collect_health", fake)
    cli.collect_all(Config(), sections=("health",))

    assert reached == []
    # …and it really is the node list that was handed over, not a stub.
    with pytest.raises(AssertionError):
        captured["resolve_peer_names"]()


def test_collect_all_never_resolves_the_fqdn_itself(isolated_cli):
    """socket.getfqdn() does a forward *and* a reverse lookup through NSS and
    blocks for tens of seconds with a broken resolver — the very fault the DNS
    check diagnoses. It must therefore happen inside the budgeted DNS task, not
    in the main thread ahead of it."""
    import socket

    resolved = []
    isolated_cli.setattr(socket, "getfqdn", lambda *a: resolved.append(True) or "x")
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: None)
    cli.collect_all(Config(), sections=("health",))
    assert resolved == []


def test_main_never_propagates_a_collector_explosion(isolated_cli):
    def boom(*a, **k):
        raise RuntimeError("kaputt")

    isolated_cli.setattr(cli, "collect_health", boom)
    assert cli.main(["--sections", "health"]) == 0


def test_traefik_main_returns_zero(isolated_cli):
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: None)
    assert cli.traefik_main([]) == 0


def test_collect_all_skips_traefik_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []
