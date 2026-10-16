import json
import os
import threading

import pytest

from terminal_status_panel.collectors.hoststate import host_trouble, read_host_state
from terminal_status_panel.model.host import EventCount, HostState, HostStateRead


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


def test_host_events_become_trouble_rows_and_zero_counts_do_not():
    read = HostStateRead(
        state=HostState(
            written_at=1000.0,
            collector="x",
            events={"oom_kill": EventCount(2, 900.0), "vrrp_state": EventCount(0, None)},
        ),
        age_s=1.0,
    )
    rows = host_trouble(read, now=1000.0, enabled=("oom_kill", "vrrp_state"))
    assert [(r.name, r.fails, r.source, r.severity) for r in rows] == [
        ("host: OOM kill", 2, "host", "dead")
    ]
    assert rows[0].cause == "last 1m ago"


def test_disabled_kind_is_not_reported():
    read = HostStateRead(
        state=HostState(written_at=1.0, collector="x", events={"oom_kill": EventCount(1, 1.0)}),
        age_s=1.0,
    )
    assert host_trouble(read, now=2.0, enabled=()) == []


def test_no_state_means_no_rows():
    assert host_trouble(HostStateRead(problem="missing"), now=1.0, enabled=("oom_kill",)) == []


def test_stale_state_marks_every_row_with_its_age():
    state = HostState(
        written_at=0.0,
        collector="x",
        events={"oom_kill": EventCount(1, 0.0), "unit_failed": EventCount(1, None)},
    )
    read = HostStateRead(state=state, age_s=300.0)
    rows = host_trouble(read, now=300.0, enabled=("oom_kill", "unit_failed"))
    assert rows[0].cause == "last 5m ago (state 5 min old)"
    assert rows[1].cause == "(state 5 min old)"


# -- the reader never blocks and never lets a bad field through --


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_fifo_at_the_state_path_is_unreadable_and_does_not_block(tmp_path):
    path = tmp_path / "host.json"
    os.mkfifo(path)
    result = {}
    reader = threading.Thread(
        target=lambda: result.update(read=read_host_state(str(path), now=1.0)), daemon=True
    )
    reader.start()
    reader.join(timeout=2.0)
    if reader.is_alive():  # free the blocked open() so the thread can end
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(timeout=2.0)
        pytest.fail("read_host_state blocked on a FIFO")
    assert result["read"].problem == "unreadable"


def test_directory_at_the_state_path_is_unreadable(tmp_path):
    (tmp_path / "host.json").mkdir()
    assert read_host_state(str(tmp_path / "host.json"), now=1.0).problem == "unreadable"


def test_oversized_state_file_is_unreadable(tmp_path):
    body = {"schema": 1, "written_at": 0.0, "collector": "x" * (64 * 1024)}
    assert read_host_state(_write(tmp_path, json.dumps(body)), now=1.0).problem == "unreadable"


def test_state_file_just_under_the_limit_is_read(tmp_path):
    body = {"schema": 1, "written_at": 0.0, "collector": "x" * (60 * 1024)}
    assert read_host_state(_write(tmp_path, json.dumps(body)), now=1.0).problem is None


def test_wrong_field_type_is_unreadable_and_trouble_still_renders(tmp_path):
    body = {
        "schema": 1,
        "written_at": 0.0,
        "collector": "x",
        "events": {"oom_kill": {"count": "3", "last": 0.0}},
    }
    read = read_host_state(_write(tmp_path, json.dumps(body)), now=1.0)
    assert read.state is None and read.problem == "unreadable"
    assert host_trouble(read, now=1.0, enabled=("oom_kill",)) == []


def test_negative_infinity_in_an_event_is_unreadable(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(
        '{"schema": 1, "written_at": 1000.0, "collector": "x",'
        ' "events": {"oom_kill": {"count": 1, "last": -Infinity}}}'
    )
    assert read_host_state(str(path), now=1000.0).problem == "unreadable"


def test_rows_from_a_failed_journal_read_say_they_are_not_refreshed():
    state = HostState(
        written_at=1000.0,
        collector="x",
        events={"oom_kill": EventCount(1, 940.0), "unit_failed": EventCount(1, None)},
        errors={"events": "timeout"},
    )
    rows = host_trouble(
        HostStateRead(state=state, age_s=1.0), now=1000.0, enabled=("oom_kill", "unit_failed")
    )
    assert [r.cause for r in rows] == ["last 1m ago (not refreshed)", "(not refreshed)"]
