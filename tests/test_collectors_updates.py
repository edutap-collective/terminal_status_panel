from types import SimpleNamespace

from lmu.terminal_status_panel.collectors import updates
from lmu.terminal_status_panel.model import UpdateInfo


def test_parses_apt_check_output(monkeypatch):
    monkeypatch.setattr(
        updates.subprocess, "run",
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
