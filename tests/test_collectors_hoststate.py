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


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "host.json"
    path.write_text(text)
    return str(path)


def test_json_that_is_not_an_object_is_unreadable(tmp_path):
    for text in ("[]", "null", "5", '"text"'):
        read = read_host_state(_write(tmp_path, text), now=1.0)
        assert read.state is None and read.problem == "unreadable", text


def test_wrong_shaped_blocks_are_unreadable(tmp_path):
    cases = {
        "events_scalar": {"events": 5},
        "events_list": {"events": [1]},
        "vrrp_scalar": {"vrrp": 5},
        "entry_scalar": {"events": {"oom_kill": 5}},
    }
    for name, extra in cases.items():
        body = {"schema": 1, "written_at": 0.0, "collector": "x", **extra}
        read = read_host_state(_write(tmp_path, json.dumps(body)), now=1.0)
        assert read.state is None and read.problem == "unreadable", name


def test_infinite_window_is_unreadable(tmp_path):
    text = '{"schema": 1, "written_at": 0.0, "collector": "x", "events_window_s": 1e999}'
    assert read_host_state(_write(tmp_path, text), now=1.0).problem == "unreadable"


def test_deeply_nested_json_is_unreadable(tmp_path):
    assert read_host_state(_write(tmp_path, "[" * 100_000), now=1.0).problem == "unreadable"


def test_missing_written_at_is_unreadable(tmp_path):
    text = '{"schema": 1, "collector": "x"}'
    assert read_host_state(_write(tmp_path, text), now=1.0).problem == "unreadable"


def test_non_finite_written_at_is_unreadable(tmp_path):
    text = '{"schema": 1, "written_at": NaN, "collector": "x"}'
    assert read_host_state(_write(tmp_path, text), now=1.0).problem == "unreadable"


def test_non_numeric_written_at_is_not_mistaken_for_schema(tmp_path):
    text = '{"schema": 1, "written_at": "schema", "collector": "x"}'
    assert read_host_state(_write(tmp_path, text), now=1.0).problem == "unreadable"


def test_unknown_keys_inside_blocks_are_ignored(tmp_path):
    body = {
        "schema": 1,
        "written_at": 100.0,
        "collector": "x",
        "vrrp": {
            "source": "keepalived-json",
            "instances": [{"name": "vi_1", "state": "MASTER", "extra": 1}],
        },
        "apache": {"window_s": 30.0, "extra": 1},
        "events": {"oom_kill": {"count": 0, "last": None, "extra": 1}},
    }
    read = read_host_state(_write(tmp_path, json.dumps(body)), now=130.0)
    assert read.problem is None
    assert read.state.vrrp.instances[0].name == "vi_1"
    assert read.state.apache.window_s == 30.0
    assert read.state.events["oom_kill"].count == 0
