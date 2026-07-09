from lmu.terminal_status_panel import cli


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
