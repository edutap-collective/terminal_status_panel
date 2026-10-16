import json

import pytest

from terminal_status_panel.collector import apache

STATUS = """Total Accesses: 1000
BusyWorkers: 7
IdleWorkers: 93
Scoreboard: ____W___..GG....
"""


def test_parse_status():
    data = apache.parse_status(STATUS)
    assert data["total"] == 1000 and data["busy"] == 7 and data["scoreboard"].count(".") == 6


def test_parse_status_tolerates_missing_optional_fields():
    data = apache.parse_status("Total Accesses: 5\n")
    assert data == {"total": 5, "busy": None, "idle": None, "scoreboard": ""}


def test_parse_status_without_total_accesses_is_a_key_error():
    with pytest.raises(KeyError):
        apache.parse_status("BusyWorkers: 1\n")


def test_rate_from_two_runs(tmp_path):
    prev = tmp_path / "apache.json"
    prev.write_text(json.dumps({"total": 700, "at": 970.0}))
    state, error = apache.collect_apache(
        "http://localhost/server-status", prev, now=1000.0, fetch=lambda url, timeout: STATUS
    )
    assert error is None and state.requests_per_s == 10.0
    assert state.slots_finishing == 2 and round(state.slots_busy, 3) == round(1 - 6 / 16, 3)
    assert json.loads(prev.read_text()) == {"total": 1000, "at": 1000.0}


def test_restart_gives_no_rate(tmp_path):
    prev = tmp_path / "apache.json"
    prev.write_text(json.dumps({"total": 5000, "at": 970.0}))
    state, _ = apache.collect_apache(
        "http://x", prev, now=1000.0, fetch=lambda url, timeout: STATUS
    )
    assert state.requests_per_s is None


def test_unreachable_is_an_error_code(tmp_path):
    def boom(url, timeout):
        raise OSError("refused")

    state, error = apache.collect_apache("http://x", tmp_path / "a.json", now=1.0, fetch=boom)
    assert state is None and error == "unreachable"


def test_missing_total_accesses_is_a_parse_error(tmp_path):
    state, error = apache.collect_apache(
        "http://x", tmp_path / "a.json", now=1.0, fetch=lambda url, timeout: "BusyWorkers: 1\n"
    )
    assert state is None and error == "parse"
