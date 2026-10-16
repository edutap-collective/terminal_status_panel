from types import SimpleNamespace

from terminal_status_panel.collectors import updates
from terminal_status_panel.collectors.hoststate import HostStateRead
from terminal_status_panel.model import UpdateInfo
from terminal_status_panel.model.host import HostState, UpdateCounts


def test_parses_apt_check_output(monkeypatch):
    monkeypatch.setattr(
        updates.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stderr="12;5", stdout=""),
    )
    info = updates.collect_updates()
    assert isinstance(info, UpdateInfo)
    assert info.supported is True
    assert info.available == 12
    assert info.security == 5
    assert info.standard == 7


def test_degrades_when_helper_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(updates.APT_CHECK)

    monkeypatch.setattr(updates.subprocess, "run", boom)
    info = updates.collect_updates()
    assert info.supported is False
    assert info.available is None


def test_falls_back_to_the_collector_counts(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(updates.APT_CHECK)

    monkeypatch.setattr(updates.subprocess, "run", boom)
    host = HostStateRead(
        state=HostState(
            written_at=1.0,
            collector="x",
            updates=UpdateCounts(1.0, 3, 1, "apt-simulate"),
        ),
        age_s=5.0,
    )
    info = updates.collect_updates(host=host)
    assert (info.supported, info.available, info.security, info.standard) == (True, 3, 1, 2)


def test_apt_check_still_wins(monkeypatch):
    monkeypatch.setattr(
        updates.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stderr="5;0", stdout=""),
    )
    host = HostStateRead(
        state=HostState(
            written_at=1.0,
            collector="x",
            updates=UpdateCounts(1.0, 9, 9, "apt-simulate"),
        ),
        age_s=5.0,
    )
    assert updates.collect_updates(host=host).available == 5


def _apt_check_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(updates.APT_CHECK)

    monkeypatch.setattr(updates.subprocess, "run", boom)


def test_fallback_carries_the_check_time_and_the_error(monkeypatch):
    _apt_check_missing(monkeypatch)
    host = HostStateRead(
        state=HostState(
            written_at=9000.0,
            collector="x",
            updates=UpdateCounts(1000.0, 0, 0, "apt-simulate"),
            errors={"updates": "timeout"},
        ),
        age_s=5.0,
    )
    info = updates.collect_updates(host=host)
    assert (info.checked_at, info.problem) == (1000.0, "timeout")


def test_apt_check_has_no_check_time_or_problem(monkeypatch):
    monkeypatch.setattr(
        updates.subprocess, "run", lambda *a, **k: SimpleNamespace(stderr="1;0", stdout="")
    )
    info = updates.collect_updates()
    assert (info.checked_at, info.problem) == (None, None)
