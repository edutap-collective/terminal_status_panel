import json
import subprocess

from terminal_status_panel.collector import journal
from terminal_status_panel.model.host import HOST_EVENT_KINDS


def _line(**fields):
    return json.dumps(
        {
            "__CURSOR": fields.pop("cursor", "c1"),
            "__REALTIME_TIMESTAMP": str(int(fields.pop("at", 1000.0) * 1e6)),
            **fields,
        }
    )


def test_counts_bound_to_source_and_ignores_sudo(tmp_path):
    out = "\n".join(
        [
            _line(
                cursor="c1",
                _TRANSPORT="kernel",
                MESSAGE="Out of memory: Killed process 42 (worker)",
            ),
            _line(
                cursor="c2",
                SYSLOG_IDENTIFIER="sudo",
                MESSAGE="node-a : COMMAND=/usr/bin/journalctl -g Killed process",
            ),
            _line(
                cursor="c3",
                SYSLOG_IDENTIFIER="Keepalived_vrrp",
                MESSAGE="(vi_1) Entering MASTER STATE",
            ),
            _line(
                cursor="c4",
                _SYSTEMD_UNIT="apache2.service",
                MESSAGE="[mpm_event:error] AH03490: scoreboard is full",
            ),
            _line(cursor="c5", _PID="1", MESSAGE="foo.service: Failed with result 'exit-code'."),
            _line(
                cursor="c6",
                _PID="1",
                MESSAGE="Reloading apache2.service - The Apache HTTP Server...",
            ),
        ]
    )
    events, error = journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, out),
    )
    assert error is None
    assert {k: v.count for k, v in events.items()} == {
        "oom_kill": 1,
        "vrrp_state": 1,
        "apache_slots_full": 1,
        "unit_failed": 1,
        "apache_reload": 1,
    }
    assert json.loads((tmp_path / "j.json").read_text())["cursor"] == "c6"


def test_second_run_reads_after_cursor_and_keeps_counts(tmp_path):
    first = _line(cursor="c1", _TRANSPORT="kernel", MESSAGE="Killed process 1", at=1000.0)
    journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, first),
    )
    seen = {}

    def runner(args, timeout):
        seen["args"] = args
        return 0, ""

    events, _ = journal.collect_events(
        tmp_path / "j.json", now=1030.0, enabled=journal.KINDS, runner=runner
    )
    assert "--after-cursor=c1" in seen["args"] and events["oom_kill"].count == 1


def test_window_expires_old_events(tmp_path):
    first = _line(cursor="c1", _TRANSPORT="kernel", MESSAGE="Killed process 1", at=0.0)
    journal.collect_events(
        tmp_path / "j.json", now=0.0, enabled=journal.KINDS, runner=lambda args, timeout: (0, first)
    )
    events, _ = journal.collect_events(
        tmp_path / "j.json",
        now=13 * 3600.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, ""),
    )
    assert events["oom_kill"].count == 0


def test_lost_cursor_falls_back_to_the_window(tmp_path):
    (tmp_path / "j.json").write_text(json.dumps({"cursor": "gone", "events": {}}))
    calls = []

    def runner(args, timeout):
        calls.append(args)
        return (1, "") if any(a.startswith("--after-cursor") for a in args) else (0, "")

    _, error = journal.collect_events(
        tmp_path / "j.json", now=100.0, enabled=journal.KINDS, runner=runner
    )
    assert error is None and any(a.startswith("--since=") for a in calls[-1])


def test_lost_cursor_window_replaces_saved_counts(tmp_path):
    saved = {"cursor": "gone", "events": {"oom_kill": [1000.0]}}
    (tmp_path / "j.json").write_text(json.dumps(saved))
    window = _line(cursor="c9", _TRANSPORT="kernel", MESSAGE="Killed process 7", at=1000.0)

    def runner(args, timeout):
        return (1, "") if any(a.startswith("--after-cursor") for a in args) else (0, window)

    events, _ = journal.collect_events(
        tmp_path / "j.json", now=1100.0, enabled=journal.KINDS, runner=runner
    )
    assert events["oom_kill"].count == 1


def test_unwritable_state_still_returns_counts(tmp_path):
    out = _line(cursor="c1", _TRANSPORT="kernel", MESSAGE="Killed process 1")
    events, error = journal.collect_events(
        tmp_path / "missing" / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, out),
    )
    assert events["oom_kill"].count == 1 and error is None


def test_state_write_value_error_still_returns_counts(tmp_path, monkeypatch):
    def refuse(path, payload):
        raise ValueError("cannot encode")

    monkeypatch.setattr(journal, "_write_atomic", refuse)
    out = _line(cursor="c1", _TRANSPORT="kernel", MESSAGE="Killed process 1")
    events, error = journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, out),
    )
    assert events["oom_kill"].count == 1 and error is None


def test_non_numeric_timestamp_is_skipped(tmp_path):
    bad = json.dumps(
        {
            "__CURSOR": "c1",
            "__REALTIME_TIMESTAMP": "soon",
            "_TRANSPORT": "kernel",
            "MESSAGE": "Killed process 1",
        }
    )
    good = _line(cursor="c2", _TRANSPORT="kernel", MESSAGE="Killed process 2", at=1000.0)
    events, error = journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, bad + "\n" + good),
    )
    assert events["oom_kill"].count == 1 and error is None


def test_state_file_holds_no_message_text(tmp_path):
    out = _line(cursor="c1", _TRANSPORT="kernel", MESSAGE="Killed process 42 (worker)", at=1000.0)
    journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, out),
    )
    written = (tmp_path / "j.json").read_text()
    assert "MESSAGE" not in written and "worker" not in written and "Killed" not in written


def test_kinds_match_the_host_model():
    assert journal.KINDS == HOST_EVENT_KINDS


def test_lost_cursor_is_dropped_when_the_window_is_empty(tmp_path):
    (tmp_path / "j.json").write_text(json.dumps({"cursor": "gone", "events": {}}))

    def runner(args, timeout):
        return (1, "") if any(a.startswith("--after-cursor") for a in args) else (0, "")

    journal.collect_events(tmp_path / "j.json", now=100.0, enabled=journal.KINDS, runner=runner)
    assert json.loads((tmp_path / "j.json").read_text())["cursor"] is None


def test_binary_message_does_not_raise_or_match(tmp_path):
    binary = json.dumps(
        {
            "__CURSOR": "c1",
            "__REALTIME_TIMESTAMP": "1000000000",
            "_TRANSPORT": "kernel",
            "MESSAGE": [75, 76],
        }
    )
    events, error = journal.collect_events(
        tmp_path / "j.json",
        now=1000.0,
        enabled=journal.KINDS,
        runner=lambda args, timeout: (0, binary),
    )
    assert events["oom_kill"].count == 0 and error is None


def test_every_kind_source_is_in_the_server_side_filter():
    args = journal._filter_args()
    assert set(journal.SOURCES) == set(journal.KINDS)
    for kind in journal.KINDS:
        for match in journal.SOURCES[kind]:
            assert match in args


def test_server_side_filter_joins_matches_with_plus():
    args = journal._filter_args()
    assert "+" in args and args[0] != "+" and args[-1] != "+"
    assert "SYSLOG_IDENTIFIER=Keepalived_vrrp" in args


def test_window_read_uses_the_long_timeout_and_incremental_the_short(tmp_path):
    timeouts = []

    def runner(args, timeout):
        timeouts.append((args[0].startswith("--after-cursor"), timeout))
        return 0, ""

    (tmp_path / "j.json").write_text(json.dumps({"cursor": "c1", "events": {}}))
    journal.collect_events(tmp_path / "j.json", now=100.0, enabled=journal.KINDS, runner=runner)
    (tmp_path / "k.json").write_text("{}")
    journal.collect_events(tmp_path / "k.json", now=100.0, enabled=journal.KINDS, runner=runner)
    assert timeouts == [(True, journal.INCREMENTAL_TIMEOUT), (False, journal.WINDOW_TIMEOUT)]


def test_window_timeout_returns_last_known_counts(tmp_path):
    saved = {"cursor": None, "events": {"oom_kill": [1000.0]}}
    (tmp_path / "j.json").write_text(json.dumps(saved))

    def slow(args, timeout):
        raise subprocess.TimeoutExpired(cmd="journalctl", timeout=timeout)

    events, error = journal.collect_events(
        tmp_path / "j.json", now=1100.0, enabled=journal.KINDS, runner=slow
    )
    assert error == "timeout" and events["oom_kill"].count == 1


def test_failed_read_keeps_last_known_counts(tmp_path):
    saved = {"cursor": "c1", "events": {"oom_kill": [1000.0]}}
    (tmp_path / "j.json").write_text(json.dumps(saved))
    events, error = journal.collect_events(
        tmp_path / "j.json", now=1100.0, enabled=journal.KINDS, runner=lambda args, timeout: (1, "")
    )
    assert error == "no-permission" and events["oom_kill"].count == 1
    assert json.loads((tmp_path / "j.json").read_text()) == saved
