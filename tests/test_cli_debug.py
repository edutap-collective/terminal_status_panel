"""Tests for `--debug`, the opt-in diagnostic channel.

`main` wraps everything in `except Exception: pass` and returns 0. That is
right for a login shell -- a status panel must never keep someone out -- but
until now there was no way to ask *why* a panel came up empty, and the answer
was not recoverable after the fact. `--debug` keeps every one of those
properties and adds a channel that has to be asked for.
"""

from __future__ import annotations

import pytest

from terminal_status_panel import cli


def _write(tmp_path, body: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# The contract that must not change
# --------------------------------------------------------------------------- #


def test_debug_still_exits_zero_on_an_unexpected_error(monkeypatch, capsys):
    """Diagnostics are not a strict mode. The login must not fail either way."""

    def boom(*args, **kwargs):
        raise RuntimeError("the docker socket melted")

    monkeypatch.setattr(cli, "collect_all", boom)

    assert cli.main(["--debug"]) == 0


def test_without_debug_an_unexpected_error_stays_silent(monkeypatch, capsys):
    """The default is unchanged: stderr at login is noise in front of the prompt."""

    def boom(*args, **kwargs):
        raise RuntimeError("the docker socket melted")

    monkeypatch.setattr(cli, "collect_all", boom)

    assert cli.main([]) == 0
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- #
# What --debug actually says
# --------------------------------------------------------------------------- #


def test_debug_names_the_exception_type_and_message(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("the docker socket melted")

    monkeypatch.setattr(cli, "collect_all", boom)

    cli.main(["--debug"])

    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "the docker socket melted" in err


def test_debug_names_the_stage_that_failed(monkeypatch, capsys):
    """Which step broke matters more than the traceback for a first look."""

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(cli, "collect_all", boom)

    cli.main(["--debug"])

    assert "collecting" in capsys.readouterr().err


def test_debug_reports_config_problems_even_when_nothing_raised(tmp_path, capsys):
    """The common case: the panel renders, but not the way someone configured it."""
    path = _write(tmp_path, '[thresholds.memory]\nwarning = "soon"\n')

    assert cli.main(["--debug", "--config", path, "--sections", "server"]) == 0

    err = capsys.readouterr().err
    assert "thresholds.memory.warning" in err
    assert "soon" in err


def test_a_clean_run_says_so_rather_than_printing_nothing(tmp_path, capsys):
    """Silence would be ambiguous: did it find nothing, or did --debug not work?"""
    path = _write(tmp_path, "[thresholds.memory]\nwarning = 60\n")

    cli.main(["--debug", "--config", path, "--sections", "server"])

    assert "no problems" in capsys.readouterr().err.lower()


def test_config_problems_are_reported_before_the_panel_is_collected(tmp_path, capsys, monkeypatch):
    """A config problem is often the cause of the crash that follows it."""
    path = _write(tmp_path, '[docker]\ntimeout = "soon"\n')

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(cli, "collect_all", boom)
    cli.main(["--debug", "--config", path])

    err = capsys.readouterr().err
    assert err.index("docker.timeout") < err.index("RuntimeError")


# --------------------------------------------------------------------------- #
# The environment variable, for a login shell that cannot pass arguments
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_the_environment_variable_turns_diagnostics_on(monkeypatch, capsys, value):
    monkeypatch.setenv("TERMINAL_STATUS_PANEL_DEBUG", value)

    def boom(*args, **kwargs):
        raise RuntimeError("from the environment")

    monkeypatch.setattr(cli, "collect_all", boom)
    assert cli.main([]) == 0

    assert "from the environment" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_the_environment_variable_off_keeps_the_default(monkeypatch, capsys, value):
    monkeypatch.setenv("TERMINAL_STATUS_PANEL_DEBUG", value)

    def boom(*args, **kwargs):
        raise RuntimeError("should not be printed")

    monkeypatch.setattr(cli, "collect_all", boom)
    cli.main([])

    assert capsys.readouterr().err == ""


def test_the_flag_wins_over_an_unset_environment(monkeypatch, capsys):
    monkeypatch.delenv("TERMINAL_STATUS_PANEL_DEBUG", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("still printed")

    monkeypatch.setattr(cli, "collect_all", boom)
    cli.main(["--debug"])

    assert "still printed" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# The panel itself is unaffected
# --------------------------------------------------------------------------- #


def test_debug_does_not_write_diagnostics_into_the_panel(tmp_path, capsys):
    """stdout stays the panel. Diagnostics go to stderr so a pipe is unaffected."""
    path = _write(tmp_path, '[thresholds.memory]\nwarning = "soon"\n')

    cli.main(["--debug", "--config", path, "--sections", "server"])

    captured = capsys.readouterr()
    assert "thresholds.memory.warning" not in captured.out
    assert "thresholds.memory.warning" in captured.err
