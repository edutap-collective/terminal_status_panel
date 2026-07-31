import pytest

from terminal_status_panel import cli
from terminal_status_panel.config import Config


def test_main_exits_zero_and_prints(capsys):
    rc = cli.main(["--width", "100"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SYSTEM OVERVIEW" in out
    assert "SYSTEM LOAD" in out
    assert "DOCKER" in out


def test_main_never_raises_even_if_collection_breaks(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    # Break one collector entirely; main must still exit 0.
    monkeypatch.setattr(cli, "collect_resources", boom)
    rc = cli.main([])
    assert rc == 0


def test_width_flag_overrides(capsys):
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


@pytest.fixture
def isolated_cli(monkeypatch):
    """Keep the CLI tests off the real Docker socket and the real system.

    ``collect_all`` builds a Docker client for the health section; without this
    the unit tests would talk to whatever daemon happens to run on the machine.
    """
    monkeypatch.setattr(cli, "_docker_client", lambda cfg: None)
    monkeypatch.setattr(cli, "collect_system", lambda: None)
    monkeypatch.setattr(cli, "collect_resources", lambda: None)
    monkeypatch.setattr(cli, "collect_updates", lambda timeout=None: None)
    return monkeypatch


def test_health_main_returns_zero(isolated_cli):
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: None)
    assert cli.health_main([]) == 0


def test_collect_all_skips_health_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_health", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []


def test_collect_all_calls_health_when_selected(isolated_cli):
    called = []

    def fake(cfg, peer_names, client=None, resolve_fqdn=None):
        called.append((peer_names, resolve_fqdn))
        return None

    isolated_cli.setattr(cli, "collect_health", fake)
    cli.collect_all(Config(), sections=("health",))
    assert len(called) == 1


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
