import subprocess

from terminal_status_panel.collector import apt
from terminal_status_panel.model.host import UpdateCounts

SIM = """Reading package lists...
Inst libfoo1 [1.0-1] (1.0-2 Debian-Security:13/stable-security [amd64])
Inst bar [2.0] (2.1 Debian:13.8/stable [all])
Conf libfoo1 (1.0-2 Debian-Security:13/stable-security [amd64])
"""


def _no_run():
    raise AssertionError("runner must not be called")


def test_counts_inst_lines_and_security():
    assert apt.parse_simulation(SIM) == (2, 1)


def test_non_security_inst_lines_count_as_available_only():
    assert apt.parse_simulation("Inst bar [2.0] (2.1 Debian:13.8/stable [all])\n") == (1, 0)


def test_recent_result_is_reused():
    prev = UpdateCounts(1000.0, 4, 2, "apt-simulate")
    counts, error = apt.collect_updates(prev, now=1000.0 + 600, runner=_no_run)
    assert counts == prev and error is None


def test_old_result_is_refreshed():
    counts, error = apt.collect_updates(
        UpdateCounts(0.0, 9, 9, "apt-simulate"), now=4000.0, runner=lambda: (0, SIM)
    )
    assert (counts.available, counts.security, counts.checked_at) == (2, 1, 4000.0)


def test_apt_missing_is_not_installed():
    def boom():
        raise FileNotFoundError("apt-get")

    assert apt.collect_updates(None, now=1.0, runner=boom) == (None, "not-installed")


def test_failed_exit_keeps_previous_and_its_timestamp():
    prev = UpdateCounts(0.0, 9, 9, "apt-simulate")
    counts, error = apt.collect_updates(prev, now=4000.0, runner=lambda: (100, ""))
    assert counts is prev and error == "apt-failed"


def test_failed_exit_without_previous_reports_no_counts():
    assert apt.collect_updates(None, now=4000.0, runner=lambda: (100, "")) == (None, "apt-failed")


def test_timeout_keeps_previous_and_reports_timeout():
    prev = UpdateCounts(0.0, 9, 9, "apt-simulate")

    def slow():
        raise subprocess.TimeoutExpired(cmd="apt-get", timeout=20.0)

    counts, error = apt.collect_updates(prev, now=4000.0, runner=slow)
    assert counts is prev and error == "timeout"


def test_permission_error_is_apt_failed_not_not_installed():
    def denied():
        raise PermissionError("apt-get")

    assert apt.collect_updates(None, now=1.0, runner=denied) == (None, "apt-failed")
