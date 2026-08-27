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

    ``collect_updates`` shells out to apt-check for the same reason.
    ``collect_processes`` reaches nothing off the machine either, but it is
    stubbed too: it sleeps for ``cfg.process_sample`` — 0.3 s by default — to
    sample CPU, and walks the whole process table on every call, so leaving it
    real would tax every server-section test with that sleep and scan. The
    purely local collectors that read state instantly (system, resources) are
    left alone.

    A test that wants a specific ``collect_health`` simply overrides it again.
    """
    monkeypatch.setattr(cli, "_docker_client", lambda cfg: None)
    monkeypatch.setattr(cli, "collect_health", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_updates", lambda timeout=None: None)
    monkeypatch.setattr(cli, "collect_processes", lambda sample, **kw: None)
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
    have succeeded, whatever health.timeout.kafka said.

    The slowest enabled probe is MongoDB since its per-member fan-out; the
    invariant is that the socket outlives whichever one that is, not that it
    is any particular check.
    """
    cfg = Config()
    assert cfg.docker_timeout == 1.5
    assert cli._health_socket_timeout(cfg) == max(cfg.health.timeouts.values()) == 6.0


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
    assert client.api.timeout == cli._health_socket_timeout(cfg) == 6.0


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


def test_the_traefik_section_collects_the_swarm_data_too(isolated_cli):
    """Without it every service line renders `⬜`: the tree would never show a
    real verdict from the command the README documents as the way to run it,
    and `service_verdict` — reused so there is no second verdict logic — would
    be dead in that path."""
    from terminal_status_panel.model import ServiceStatus, SwarmInfo, TraefikInfo

    swarm = SwarmInfo(
        reachable=True, enabled=True, services=[ServiceStatus("kafbat-ui_kafbat-ui", 1, 1)]
    )
    isolated_cli.setattr(cli, "collect_docker", lambda *a, **k: swarm)
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: TraefikInfo())
    data = cli.collect_all(Config(), sections=("traefik",))
    assert data.swarm is swarm


def test_the_traefik_section_does_not_render_docker_infos(isolated_cli, capsys):
    """Only the data is fetched — the DOCKER INFOS block stays out of the
    traefik-only panel."""
    from terminal_status_panel.model import SwarmInfo, TraefikInfo

    isolated_cli.setattr(
        cli, "collect_docker", lambda *a, **k: SwarmInfo(reachable=True, enabled=True)
    )
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: TraefikInfo())
    assert cli.traefik_main(["--width", "100"]) == 0
    out = capsys.readouterr().out
    assert "TRAEFIK WIRING" in out
    assert "DOCKER INFOS" not in out


def test_a_traefik_only_panel_shows_a_real_verdict_not_a_dot(isolated_cli, capsys):
    from terminal_status_panel.model import (
        ServiceStatus,
        SwarmInfo,
        TraefikEntrypoint,
        TraefikInfo,
        TraefikRouter,
        TraefikServiceRef,
    )

    swarm = SwarmInfo(
        reachable=True, enabled=True, services=[ServiceStatus("kafbat-ui_kafbat-ui", 1, 1)]
    )
    info = TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020)],
        routers=[TraefikRouter(name="kafbat-ui", entrypoints=["portalmgmt"], service="kafbat-ui")],
        services={
            "kafbat-ui": TraefikServiceRef(name="kafbat-ui", docker_service="kafbat-ui_kafbat-ui")
        },
    )
    isolated_cli.setattr(cli, "collect_docker", lambda *a, **k: swarm)
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: info)
    assert cli.traefik_main(["--width", "100"]) == 0
    assert "1/1" in capsys.readouterr().out


def test_collect_all_skips_traefik_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []


def test_processes_are_collected_only_for_the_server_section(isolated_cli, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "collect_processes", lambda sample, **kw: calls.append(sample) or None)
    cli.collect_all(Config(), ("docker",))
    assert calls == []
    cli.collect_all(Config(), ("server",))
    assert calls == [Config().process_sample]


def test_an_unmeasurable_process_collection_still_yields_an_empty_snapshot(
    isolated_cli,
):
    """``collect_processes`` returning ``None`` -- nothing at all could be
    read -- must not leak through as ``None`` on ``PanelData.processes``: that
    would read as "never asked" to the renderer, when the row was in fact
    asked for and came back empty-handed."""
    from terminal_status_panel.model import ProcessSnapshot

    data = cli.collect_all(Config(), ("server",))
    assert data.processes == ProcessSnapshot()


def test_the_follow_flags_are_accepted_by_every_command():
    for prog in (
        "status-full",
        "status-server",
        "status-docker",
        "status-health",
        "status-traefik",
    ):
        args = cli._parse_args(["-f", "--interval", "7"], prog)
        assert args.follow is True
        assert args.interval == 7.0


def test_follow_hands_off_to_the_loop(isolated_cli, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_follow", lambda cfg, sections, **kw: seen.update(kw) or 0)
    assert cli.main(["--follow", "--interval", "9", "--no-color"]) == 0
    assert seen["interval"] == 9.0


def test_without_follow_nothing_loops(isolated_cli, monkeypatch):
    monkeypatch.setattr(cli, "run_follow", lambda *a, **k: pytest.fail("must not be called"))
    assert cli.main(["--no-color", "--width", "100"]) == 0


def test_the_processes_flag_overrides_the_configured_count():
    cfg = Config(top_processes=5)
    assert cli.resolve_top_processes(12, cfg) == 12
    assert cli.resolve_top_processes(None, cfg) == 5


def test_a_negative_flag_is_zero():
    assert cli.resolve_top_processes(-2, Config(top_processes=5)) == 0


def test_an_explicit_zero_flag_silences_the_block_for_one_run():
    """Guards the falsy-zero trap: the guard must be `is not None`, not
    truthiness.

    A user with `top_processes = 12` configured, typing `--processes 0` to
    silence the block for a single run, must get `0` back -- not `12`. The
    obvious "simplification" `if arg_processes:` would treat `0` the same as
    "not given" and silently fall back to the configured value, exactly the
    trap `follow.py` documents for `--interval 0` (see the comment on
    `run_follow`'s `requested = ...` line).
    """
    assert cli.resolve_top_processes(0, Config(top_processes=12)) == 0


def test_the_processes_flag_is_accepted_by_every_command():
    for prog in (
        "status-full",
        "status-server",
        "status-docker",
        "status-health",
        "status-traefik",
    ):
        assert cli._parse_args(["--processes", "9"], prog).processes == 9


def test_zero_rows_skips_the_collection_entirely(isolated_cli, monkeypatch):
    """The switch is worth having because it removes the 0.3 s sampling window
    from the login path, not merely because it hides a table."""
    calls = []
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: calls.append(a) or None)
    data = cli.collect_all(Config(top_processes=0), ("server",))
    assert calls == []
    assert data.processes is None


def test_zero_rows_is_not_reported_as_a_failed_collection(isolated_cli, monkeypatch):
    """`None` means never asked; an empty snapshot means asked and got nothing,
    which the section reports as `not available`. A setting is not a failure."""
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)
    assert cli.collect_all(Config(top_processes=0), ("server",)).processes is None


def test_the_resolved_count_reaches_the_collector(isolated_cli, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "collect_processes", lambda sample, limit=5: seen.update(limit=limit) or None
    )
    cli.collect_all(Config(top_processes=9), ("server",))
    assert seen["limit"] == 9


def test_the_flag_survives_into_follow_mode(isolated_cli, monkeypatch):
    """Follow mode collects once per pass and never sees argv.

    The resolved count is written onto the config for exactly this reason: a
    flag that held for the first frame and silently reverted on the second
    would be worse than no flag, because nothing would say it had.
    """
    limits = []
    monkeypatch.setattr(
        cli, "collect_processes", lambda sample, limit=5: limits.append(limit) or None
    )
    monkeypatch.setattr(
        cli,
        "run_follow",
        lambda cfg, sections, **kw: (
            [cli.collect_all(cfg, sections), cli.collect_all(cfg, sections)] and 0
        ),
    )
    cli.main(["--sections", "server", "--processes", "7", "--follow", "--no-color"])
    assert limits == [7, 7]
