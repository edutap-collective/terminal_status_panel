from pathlib import Path

import pytest

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
    assert "export FOO=1" in text  # preserved existing content
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


def test_install_panel_can_wire_up_traefik(tmp_path, monkeypatch):
    """install.py derives --panel choices from PANELS; a missing entry made
    --panel health unusable once already."""
    from terminal_status_panel import install

    assert install.PANELS["traefik"] == "status-traefik"
    assert install.PANELS["trouble"] == "status-trouble"


TIMER = "terminal-status-panel-collect.timer"
SERVICE = "terminal-status-panel-collect.service"


def _ok(args):
    return 0


def _recorder(failing=()):
    """A systemctl stand-in: records each call, fails those whose first words match."""
    calls = []

    def systemctl(args):
        calls.append(args)
        return 1 if any(args[: len(f)] == list(f) for f in failing) else 0

    return systemctl, calls


def test_collector_install_is_idempotent_and_reports_it(tmp_path):
    systemctl, calls = _recorder()
    first = install.apply_collector(tmp_path, "/opt/tsp/bin", uninstall=False, systemctl=systemctl)
    second = install.apply_collector(tmp_path, "/opt/tsp/bin", uninstall=False, systemctl=systemctl)
    assert (first, second) == ("written", "unchanged")
    assert (tmp_path / "etc/systemd/system/terminal-status-panel-collect.service").exists()
    assert (tmp_path / "usr/lib/terminal-status-panel/keepalived-dump").stat().st_mode & 0o111
    assert calls == [
        ["daemon-reload"],
        ["enable", "--now", TIMER],
        ["is-enabled", "--quiet", TIMER],
        ["is-active", "--quiet", TIMER],
    ]


@pytest.mark.parametrize("failing", [("daemon-reload",), ("enable",)])
def test_collector_install_fails_when_systemctl_fails(tmp_path, failing):
    systemctl, _ = _recorder(failing=[failing])
    assert install.apply_collector(tmp_path, "/x", False, systemctl) == "failed"


def test_unchanged_files_retry_activation_of_an_inactive_timer(tmp_path):
    install.apply_collector(tmp_path, "/x", False, _ok)
    systemctl, calls = _recorder(failing=[("is-active",)])
    assert install.apply_collector(tmp_path, "/x", False, systemctl) == "activated"
    assert calls[-1] == ["enable", "--now", TIMER]


def test_failed_retry_of_activation_is_reported(tmp_path):
    install.apply_collector(tmp_path, "/x", False, _ok)
    systemctl, _ = _recorder(failing=[("is-enabled",), ("enable",)])
    assert install.apply_collector(tmp_path, "/x", False, systemctl) == "failed"


def test_uninstall_stops_the_service_before_deleting_files(tmp_path):
    install.apply_collector(tmp_path, "/x", False, _ok)
    unit = tmp_path / "etc/systemd/system" / SERVICE
    seen = []

    def systemctl(args):
        seen.append((args, unit.exists()))
        return 0

    assert install.apply_collector(tmp_path, "/x", True, systemctl) == "removed"
    assert seen == [
        (["disable", "--now", TIMER], True),
        (["stop", SERVICE], True),
        (["daemon-reload"], False),
    ]


@pytest.mark.parametrize("failing", [("disable",), ("stop",)])
def test_uninstall_failure_keeps_the_files_and_is_reported(tmp_path, failing):
    install.apply_collector(tmp_path, "/x", False, _ok)
    systemctl, _ = _recorder(failing=[failing])
    assert install.apply_collector(tmp_path, "/x", True, systemctl) == "failed"
    assert (tmp_path / "etc/systemd/system" / TIMER).exists()


def test_uninstall_of_absent_units_ignores_systemctl_errors(tmp_path):
    systemctl, _ = _recorder(failing=[("disable",), ("stop",)])
    assert install.apply_collector(tmp_path, "/x", True, systemctl) == "nothing to remove"


def test_uninstall_without_systemctl_still_removes_the_files(tmp_path):
    install.apply_collector(tmp_path, "/x", False, _ok)
    assert install.apply_collector(tmp_path, "/x", True, lambda args: 127) == "removed"
    assert not (tmp_path / "etc/systemd/system" / TIMER).exists()
    assert not (tmp_path / "usr/lib/terminal-status-panel").exists()


def test_collector_cli_exits_1_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(install, "check_collector_bin_dir", lambda bin_dir: None)
    monkeypatch.setattr(install, "apply_collector", lambda root, bin_dir, uninstall: "failed")
    assert install.main(["--collector"]) == 1
    assert capsys.readouterr().out.strip() == "failed"


def test_collector_install_sets_modes_and_repairs_them(tmp_path):
    install.apply_collector(tmp_path, "/x", uninstall=False, systemctl=_ok)
    script = tmp_path / "usr/lib/terminal-status-panel/keepalived-dump"
    unit = tmp_path / "etc/systemd/system/terminal-status-panel-collect.timer"
    assert (script.stat().st_mode & 0o777, unit.stat().st_mode & 0o777) == (0o755, 0o644)
    script.chmod(0o666)
    assert install.apply_collector(tmp_path, "/x", False, _ok) == "written"
    assert script.stat().st_mode & 0o777 == 0o755
    assert install.apply_collector(tmp_path, "/x", False, _ok) == "unchanged"


def test_collector_uninstall_removes_everything_it_made(tmp_path):
    install.apply_collector(tmp_path, "/x", uninstall=False, systemctl=_ok)
    run = tmp_path / "run/terminal-status-panel"
    run.mkdir(parents=True)
    for name in ("host.json", "keepalived.json", ".keepalived.abc123"):
        (run / name).write_text("{}")
    result = install.apply_collector(tmp_path, "/x", uninstall=True, systemctl=_ok)
    assert result == "removed"
    assert not (tmp_path / "etc/systemd/system/terminal-status-panel-collect.timer").exists()
    assert not (tmp_path / "etc/systemd/system/terminal-status-panel-collect.service").exists()
    assert not (tmp_path / "usr/lib/terminal-status-panel").exists()
    assert not run.exists()
    again = install.apply_collector(tmp_path, "/x", uninstall=True, systemctl=_ok)
    assert again == "nothing to remove"


def test_collector_cli_reports_permission_error(monkeypatch, capsys):
    def deny(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(install, "check_collector_bin_dir", lambda bin_dir: None)
    monkeypatch.setattr(install, "apply_collector", deny)
    assert install.main(["--collector"]) == 1
    assert "permission denied" in capsys.readouterr().err


def test_collector_cli_prints_result(monkeypatch, capsys):
    monkeypatch.setattr(install, "check_collector_bin_dir", lambda bin_dir: None)
    monkeypatch.setattr(install, "apply_collector", lambda root, bin_dir, uninstall: "written")
    assert install.main(["--collector"]) == 0
    assert capsys.readouterr().out.strip() == "written"


def test_collector_cli_refuses_an_interpreter_the_service_cannot_reach(monkeypatch, capsys):
    monkeypatch.setattr(install.sys, "executable", "/home/someone/venv/bin/python")
    assert install.main(["--collector"]) == 1
    assert "ProtectHome" in capsys.readouterr().err


def test_check_bin_dir_requires_status_collect(tmp_path):
    with pytest.raises(ValueError, match="status-collect"):
        install.check_collector_bin_dir(str(tmp_path))
    (tmp_path / "status-collect").write_text("")
    install.check_collector_bin_dir(str(tmp_path))


def test_uninstall_does_not_need_a_valid_bin_dir(tmp_path):
    install.apply_collector(tmp_path, "/x", uninstall=False, systemctl=_ok)
    result = install.apply_collector(tmp_path, "bad dir\n", True, _ok)
    assert result == "removed"


def test_uninstall_removes_leftover_dump_directories(tmp_path):
    leftover = tmp_path / "run/terminal-status-panel-dump.AbC123"
    leftover.mkdir(parents=True)
    (leftover / "keepalived.json").write_text("{}")
    other = tmp_path / "run/unrelated"
    other.mkdir()
    assert install.apply_collector(tmp_path, "/x", True, _ok) == "removed"
    assert not leftover.exists()
    assert other.exists()


def test_check_bin_dir_refuses_hidden_paths_after_resolving(tmp_path):
    with pytest.raises(ValueError, match="ProtectHome"):
        install.check_collector_bin_dir("/usr/../root/bin")
    link = tmp_path / "bin"
    link.symlink_to(tmp_path)
    (tmp_path / "status-collect").write_text("")
    install.check_collector_bin_dir(str(link))  # resolves to a plain tmp dir


@pytest.mark.parametrize("extra", [[], ["--uninstall"]])
def test_collector_dry_run_is_refused_and_touches_nothing(monkeypatch, capsys, extra):
    def touched(*args, **kwargs):
        raise AssertionError("apply_collector must not run under --dry-run")

    monkeypatch.setattr(install, "check_collector_bin_dir", lambda bin_dir: None)
    monkeypatch.setattr(install, "apply_collector", touched)
    assert install.main(["--collector", "--dry-run", *extra]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1 and "--dry-run" in captured.err
