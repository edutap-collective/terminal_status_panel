import json
import stat
from dataclasses import replace

import pytest

from terminal_status_panel.collector import run as runmod
from terminal_status_panel.config import ApacheProbe, Config, HealthConfig
from terminal_status_panel.model.host import EventCount, HostState, UpdateCounts

APACHE_URL = "http://localhost/server-status"


def _config() -> Config:
    """Defaults, but with the Apache probe switched on: its URL is off by default."""
    health = replace(HealthConfig(), apache=ApacheProbe(url=APACHE_URL))
    return replace(Config(), health=health)


def _sources(**overrides):
    defaults = {
        "vrrp": lambda dump: (None, "parse"),
        "apache": lambda url, prev, now: (None, "unreachable"),
        "updates": lambda prev, now: (None, "not-installed"),
        "events": lambda path, now, enabled: ({"oom_kill": EventCount(1, 1.0)}, None),
    }
    defaults.update(overrides)
    return runmod.Sources(**defaults)


def _raise(*_args):
    raise OverflowError("corrupt totals")


def test_write_state_is_atomic_and_world_readable(tmp_path):
    path = tmp_path / "host.json"
    runmod.write_state(HostState(written_at=1.0, collector="x"), path)
    assert json.loads(path.read_text())["schema"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert not list(tmp_path.glob("*.tmp"))


def test_write_state_failure_leaves_no_temp_file(tmp_path):
    missing = tmp_path / "gone" / "host.json"
    with pytest.raises(OSError):
        runmod.write_state(HostState(written_at=1.0, collector="x"), missing)
    assert not list(tmp_path.glob("**/*.tmp"))


def test_one_failing_source_leaves_the_others(tmp_path):
    state = runmod.run(_config(), tmp_path, tmp_path, now=10.0, sources=_sources())
    assert state.events["oom_kill"].count == 1
    assert state.errors == {
        "vrrp": "parse",
        "apache": "unreachable",
        "updates": "not-installed",
        "events": None,
    }
    written = json.loads((tmp_path / "host.json").read_text())
    assert written["events"]["oom_kill"]["count"] == 1


def test_exception_in_a_source_is_recorded_as_internal_and_state_still_lands(tmp_path):
    sources = _sources(updates=_raise)
    state = runmod.run(_config(), tmp_path, tmp_path, now=10.0, sources=sources)
    assert state.updates is None
    assert state.errors["updates"] == "internal"
    assert state.events["oom_kill"].count == 1
    assert state.errors["events"] is None
    assert json.loads((tmp_path / "host.json").read_text())["errors"]["updates"] == "internal"


def test_exception_in_every_source_still_writes_the_file(tmp_path):
    sources = _sources(vrrp=_raise, apache=_raise, updates=_raise, events=_raise)
    runmod.run(_config(), tmp_path, tmp_path, now=10.0, sources=sources)
    errors = json.loads((tmp_path / "host.json").read_text())["errors"]
    assert errors == {
        "vrrp": "internal",
        "apache": "internal",
        "updates": "internal",
        "events": "internal",
    }


def test_corrupt_previous_state_does_not_prevent_the_write(tmp_path):
    (tmp_path / "host.json").write_text(
        json.dumps({"schema": 1, "written_at": 1.0, "collector": "x", "updates": {"bogus": 1}})
    )
    state = runmod.run(_config(), tmp_path, tmp_path, now=10.0, sources=_sources())
    assert state.written_at == 10.0
    assert (tmp_path / "host.json").exists()


def test_previous_update_counts_are_handed_to_the_updates_source(tmp_path):
    previous = UpdateCounts(checked_at=5.0, available=3, security=1, source="apt-simulate")
    (tmp_path / "host.json").write_text(
        json.dumps(HostState(written_at=5.0, collector="x", updates=previous).to_dict())
    )
    seen = {}

    def updates(prev, now):
        seen["prev"] = prev
        return prev, None

    runmod.run(_config(), tmp_path, tmp_path, now=10.0, sources=_sources(updates=updates))
    assert seen["prev"] == previous


def test_status_collect_exits_zero_when_state_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, "load_config", lambda path=None: _config())
    stub = _sources()
    monkeypatch.setattr(runmod, "Sources", lambda: stub)
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    assert runmod.main([]) == 0
    assert (tmp_path / "host.json").exists()


def test_status_collect_exits_one_with_one_stderr_line_when_write_fails(
    tmp_path, monkeypatch, capsys
):
    target_dir = tmp_path / "missing"
    monkeypatch.setattr(runmod, "load_config", lambda path=None: _config())
    stub = _sources()
    monkeypatch.setattr(runmod, "Sources", lambda: stub)
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(target_dir))
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    assert runmod.main([]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert str(target_dir / "host.json") in err
    assert "No such file" not in err
