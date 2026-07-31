from pathlib import Path

from terminal_status_panel import install


def test_resolve_target_global_bash():
    kind, path = install.resolve_target("global", "bash")
    assert kind == "file"
    assert path == Path("/etc/profile.d/zz-terminal-status-panel.sh")


def test_resolve_target_global_zsh():
    kind, path = install.resolve_target("global", "zsh")
    assert kind == "block"
    assert path == Path("/etc/zsh/zprofile")


def test_resolve_target_user_prefers_bash_profile(tmp_path):
    (tmp_path / ".bash_profile").write_text("# hi\n")
    kind, path = install.resolve_target("user", "bash", home=tmp_path)
    assert kind == "block"
    assert path == tmp_path / ".bash_profile"


def test_resolve_target_user_zsh(tmp_path):
    kind, path = install.resolve_target("user", "zsh", home=tmp_path)
    assert path == tmp_path / ".zprofile"


def test_install_block_is_idempotent_and_uninstallable(tmp_path):
    rc = tmp_path / ".profile"
    rc.write_text("export FOO=1\n")

    install.apply("block", rc, ("full",))
    text = rc.read_text()
    assert "export FOO=1" in text          # preserved existing content
    assert "status-full" in text
    assert text.count(install._BEGIN) == 1

    # Re-installing replaces the block, not appends a second one.
    install.apply("block", rc, ("server", "docker"))
    text = rc.read_text()
    assert text.count(install._BEGIN) == 1
    assert "status-server" in text and "status-docker" in text
    assert "status-full" not in text

    # Uninstall removes the block but keeps the rest.
    install.apply("block", rc, ("full",), uninstall=True)
    text = rc.read_text()
    assert install._BEGIN not in text
    assert "export FOO=1" in text


def test_install_global_file(tmp_path):
    target = tmp_path / "profile.d" / "zz-terminal-status-panel.sh"
    install.apply("file", target, ("full",))
    content = target.read_text()
    assert "status-full" in content
    assert "case $- in *i*)" in content
    install.apply("file", target, ("full",), uninstall=True)
    assert not target.exists()


def test_dry_run_writes_nothing(tmp_path):
    rc = tmp_path / ".profile"
    msgs = install.apply("block", rc, ("full",), dry_run=True)
    assert not rc.exists()
    assert msgs and "managed block" in msgs[0]


def test_main_user_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    rc = cli_home_profile = tmp_path / ".profile"
    rc.write_text("")
    rc_arg = install.main(["--scope", "user", "--panel", "docker"])
    assert rc_arg == 0
    assert "status-docker" in cli_home_profile.read_text()
    assert "login shells" in capsys.readouterr().out


def test_main_user_install_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    rc = cli_home_profile = tmp_path / ".profile"
    rc.write_text("")
    rc_arg = install.main(["--scope", "user", "--panel", "health"])
    assert rc_arg == 0
    assert "status-health" in cli_home_profile.read_text()
    assert "login shells" in capsys.readouterr().out
