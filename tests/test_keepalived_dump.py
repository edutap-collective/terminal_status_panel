"""Run the root-side dump helper under ``sh`` with its paths redirected."""

import os
import shutil
import signal
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

import pytest

SIGNAL = signal.SIGUSR1.value
GNU_STAT = sys.platform.startswith("linux")
needs_gnu = pytest.mark.skipif(
    not (GNU_STAT and shutil.which("chown")), reason="needs GNU stat/chown (Linux)"
)


@pytest.fixture
def env(tmp_path):
    """Paths and a fake ``keepalived`` that prints a signal number."""
    run = tmp_path / "run"
    run.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    fake = tmp_path / "keepalived"
    fake.write_text(f"#!/bin/sh\necho {SIGNAL}\n")
    fake.chmod(0o755)
    (tmp_path / "proc").mkdir()
    return {
        "TSP_PIDFILE": str(tmp_path / "keepalived.pid"),
        "TSP_DUMP": str(tmp_path / "keepalived-dump.json"),
        "TSP_RUNDIR": str(run),
        "TSP_WORKROOT": str(work),
        "TSP_OWNER": str(os.getuid()),
        "TSP_PROC": str(tmp_path / "proc"),
        "TSP_KEEPALIVED": str(fake),
        "PATH": os.environ["PATH"],
    }


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "keepalived-dump"
    path.write_text(
        resources.files("terminal_status_panel.data").joinpath("keepalived-dump").read_text()
    )
    return path


def start_target(env, tmp_path, comm="keepalived"):
    """A shell that writes the dump when signalled; /proc/<pid>/comm is faked."""
    target = tmp_path / "target.sh"
    target.write_text(
        f"#!/bin/sh\ntrap 'echo {{}} > \"{env['TSP_DUMP']}\"' USR1\nwhile :; do sleep 0.05; done\n"
    )
    process = subprocess.Popen(["sh", str(target)])  # noqa: S603, S607
    time.sleep(0.3)  # let the trap be installed
    (Path(env["TSP_PROC"]) / str(process.pid)).mkdir()
    (Path(env["TSP_PROC"]) / str(process.pid) / "comm").write_text(comm + "\n")
    Path(env["TSP_PIDFILE"]).write_text(f"{process.pid}\n")
    return process


def run(script, env):
    return subprocess.run(["sh", str(script)], env=env, check=False, timeout=30)  # noqa: S603, S607


@pytest.fixture
def target_factory(tmp_path):
    started = []

    def factory(env, **kwargs):
        process = start_target(env, tmp_path, **kwargs)
        started.append(process)
        return process

    yield factory
    for process in started:
        process.kill()
        process.wait()


def test_non_numeric_pid_signals_nothing(script, env, tmp_path, target_factory):
    process = target_factory(env)
    Path(env["TSP_PIDFILE"]).write_text("-1\n")
    assert run(script, env).returncode == 0
    time.sleep(0.2)
    assert process.poll() is None
    assert not Path(env["TSP_DUMP"]).exists()


def test_pid_that_is_not_keepalived_signals_nothing(script, env, tmp_path, target_factory):
    process = target_factory(env, comm="sleep")
    assert run(script, env).returncode == 0
    time.sleep(0.2)
    assert process.poll() is None
    assert not Path(env["TSP_DUMP"]).exists()


@needs_gnu
def test_foreign_owned_dump_is_ignored(script, env, tmp_path, target_factory):
    target_factory(env)
    env["TSP_OWNER"] = str(os.getuid() + 1)
    assert run(script, env).returncode == 0
    assert not (Path(env["TSP_RUNDIR"]) / "keepalived.json").exists()
    assert list(Path(env["TSP_RUNDIR"]).iterdir()) == []


@needs_gnu
def test_symlinked_dump_is_ignored(script, env, tmp_path, target_factory):
    # The target replaces the dump with a symlink to a file we own.
    other = tmp_path / "other.json"
    other.write_text("{}")
    target = tmp_path / "linker.sh"
    trap = f"ln -sf '{other}' '{env['TSP_DUMP']}'"
    body = ["#!/bin/sh", f'trap "{trap}" USR1', "while :; do sleep 0.05; done"]
    target.write_text("\n".join(body) + "\n")
    process = subprocess.Popen(["sh", str(target)])  # noqa: S603, S607
    try:
        time.sleep(0.3)
        (Path(env["TSP_PROC"]) / str(process.pid)).mkdir()
        (Path(env["TSP_PROC"]) / str(process.pid) / "comm").write_text("keepalived\n")
        Path(env["TSP_PIDFILE"]).write_text(f"{process.pid}\n")
        assert run(script, env).returncode == 0
    finally:
        process.kill()
        process.wait()
    assert list(Path(env["TSP_RUNDIR"]).iterdir()) == []


@needs_gnu
def test_happy_path_hands_over_a_private_copy(script, env, tmp_path, target_factory):
    target_factory(env)
    assert run(script, env).returncode == 0
    dest = Path(env["TSP_RUNDIR"]) / "keepalived.json"
    assert dest.read_text() == "{}\n"
    assert dest.stat().st_mode & 0o777 == 0o600
    assert not Path(env["TSP_DUMP"]).exists()
    assert [p.name for p in Path(env["TSP_RUNDIR"]).iterdir()] == ["keepalived.json"]
    assert list(Path(env["TSP_WORKROOT"]).iterdir()) == []


@needs_gnu
def test_planted_directory_symlink_does_not_capture_the_copy(script, env, tmp_path, target_factory):
    victim = tmp_path / "victim"
    victim.mkdir()
    dest = Path(env["TSP_RUNDIR"]) / "keepalived.json"
    dest.symlink_to(victim, target_is_directory=True)
    target_factory(env)
    assert run(script, env).returncode == 0
    assert list(victim.iterdir()) == []
    assert dest.is_file() and not dest.is_symlink()
    assert list(Path(env["TSP_WORKROOT"]).iterdir()) == []


@needs_gnu
def test_failing_copy_leaves_no_work_directory(script, env, tmp_path, target_factory):
    target_factory(env)
    env["TSP_RUNDIR"] = str(tmp_path / "missing")  # chown --reference fails
    assert run(script, env).returncode == 0
    assert list(Path(env["TSP_WORKROOT"]).iterdir()) == []
