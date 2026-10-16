from terminal_status_panel.collector import apt
from terminal_status_panel.model.host import UpdateCounts

SIM = """Reading package lists...
Inst libfoo1 [1.0-1] (1.0-2 Debian-Security:13/stable-security [amd64])
Inst bar [2.0] (2.1 Debian:13.8/stable [all])
Conf libfoo1 (1.0-2 Debian-Security:13/stable-security [amd64])
"""


def test_counts_inst_lines_and_security():
    assert apt.parse_simulation(SIM) == (2, 1)


def test_recent_result_is_reused():
    prev = UpdateCounts(1000.0, 4, 2, "apt-simulate")
    counts, error = apt.collect_updates(
        prev, now=1000.0 + 600, runner=lambda: (_ for _ in ()).throw(AssertionError)
    )
    assert counts == prev and error is None


def test_old_result_is_refreshed():
    counts, error = apt.collect_updates(
        UpdateCounts(0.0, 9, 9, "apt-simulate"), now=4000.0, runner=lambda: SIM
    )
    assert (counts.available, counts.security, counts.checked_at) == (2, 1, 4000.0)


def test_apt_missing_is_not_installed():
    def boom():
        raise FileNotFoundError("apt-get")

    assert apt.collect_updates(None, now=1.0, runner=boom) == (None, "not-installed")
