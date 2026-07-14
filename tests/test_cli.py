from terminal_status_panel import cli


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
