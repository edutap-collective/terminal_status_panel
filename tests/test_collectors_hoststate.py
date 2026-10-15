import json

from terminal_status_panel.collectors.hoststate import read_host_state


def test_missing_file_is_reported_not_quiet(tmp_path):
    read = read_host_state(str(tmp_path / "host.json"), now=10.0)
    assert read.state is None and read.problem == "missing"


def test_age_is_measured_from_written_at(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"schema": 1, "written_at": 100.0, "collector": "x"}))
    read = read_host_state(str(path), now=130.0)
    assert read.problem is None and read.age_s == 30.0 and not read.stale


def test_old_state_is_stale(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"schema": 1, "written_at": 0.0, "collector": "x"}))
    assert read_host_state(str(path), now=121.0).stale


def test_unknown_schema_is_its_own_problem(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"schema": 9, "written_at": 0.0, "collector": "x"}))
    read = read_host_state(str(path), now=1.0)
    assert read.state is None and read.problem == "unknown schema"


def test_garbage_is_unreadable(tmp_path):
    path = tmp_path / "host.json"
    path.write_text("{not json")
    assert read_host_state(str(path), now=1.0).problem == "unreadable"
