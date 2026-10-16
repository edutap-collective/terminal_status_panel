from types import SimpleNamespace

import pytest

from terminal_status_panel.collectors import host
from terminal_status_panel.collectors.hoststate import HostStateRead
from terminal_status_panel.config import ApacheProbe, VrrpProbe
from terminal_status_panel.model.host import ApacheState, HostState, VrrpInstance, VrrpState


def _read(**blocks):
    return HostStateRead(state=HostState(written_at=1.0, collector="x", **blocks), age_s=5.0)


def test_systemd_ok(monkeypatch):
    monkeypatch.setattr(
        host.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    check = host.probe_systemd(1.0, ())
    assert (check.status, check.summary) == ("ok", "0 failed")


def test_systemd_names_failed_units_and_honours_ignore(monkeypatch):
    out = "foo.service loaded failed failed Foo\nbar.timer loaded failed failed Bar\n"
    monkeypatch.setattr(
        host.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=out)
    )
    check = host.probe_systemd(1.0, ("bar.timer",))
    assert (
        check.status == "crit" and check.summary == "1 failed" and check.details == ["foo.service"]
    )


def test_systemd_absent_is_not_applicable(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(host.subprocess, "run", boom)
    assert host.probe_systemd(1.0, ()).status == "na"


def test_vrrp_without_data_is_not_applicable():
    assert host.check_vrrp(_read(), VrrpProbe()).status == "na"


def test_vrrp_backup_close_to_takeover_warns():
    read = _read(vrrp=VrrpState("keepalived-json", [VrrpInstance("vi_1", "BACKUP", 190, 188, 191)]))
    check = host.check_vrrp(read, VrrpProbe(lead_warning=5, lead_critical=0))
    assert check.status == "warn" and "lead −3" in check.summary


def test_vrrp_fault_is_critical():
    read = _read(vrrp=VrrpState("keepalived-json", [VrrpInstance("vi_1", "FAULT", 190, 0, None)]))
    assert host.check_vrrp(read, VrrpProbe()).status == "crit"


def test_vrrp_odd_values_do_not_crash():
    odd = VrrpInstance("vi_1", "BACKUP", "high", None, "x")  # type: ignore[arg-type]
    read = _read(vrrp=VrrpState("keepalived-json", [odd]))
    assert host.check_vrrp(read, VrrpProbe()).status == "ok"


def test_apache_thresholds():
    read = _read(apache=ApacheState(30.0, 450.0, 0.19, 0.07, 0))
    check = host.check_apache(
        read,
        ApacheProbe(
            url="http://localhost/server-status", requests_warning=400, requests_critical=500
        ),
    )
    assert check.status == "warn" and check.summary.startswith("450 req/s")


def test_apache_without_rate_shows_dash():
    read = _read(apache=ApacheState(30.0, None, 0.1, 0.05, 0))
    check = host.check_apache(read, ApacheProbe(url="http://x"))
    assert check.summary.startswith("— req/s")


def test_apache_not_configured_is_not_applicable():
    assert host.check_apache(_read(), ApacheProbe()).status == "na"


@pytest.fixture
def keepalived_pid(tmp_path, monkeypatch):
    """Point the keepalived pid file into tmp_path; absent until the test writes it."""
    path = tmp_path / "keepalived.pid"
    monkeypatch.setattr(host, "KEEPALIVED_PIDFILE", str(path))
    return path


def test_missing_state_is_an_error_line_where_keepalived_runs(keepalived_pid):
    keepalived_pid.write_text("1\n")
    check = host.check_vrrp(HostStateRead(problem="missing"), VrrpProbe())
    assert check.status == "error" and "collector not running" in check.summary


@pytest.mark.parametrize("problem", ["missing", "unreadable", "unknown schema"])
def test_no_state_without_keepalived_is_not_applicable(keepalived_pid, problem):
    check = host.check_vrrp(HostStateRead(problem=problem), VrrpProbe())
    assert (check.status, check.summary) == ("na", "n/a here")


def test_unreadable_state_is_an_error_line_where_keepalived_runs(keepalived_pid):
    keepalived_pid.write_text("1\n")
    check = host.check_vrrp(HostStateRead(problem="unreadable"), VrrpProbe())
    assert (check.status, check.summary) == ("error", "host state unreadable")


def test_missing_state_without_apache_url_is_not_applicable():
    check = host.check_apache(HostStateRead(problem="missing"), ApacheProbe())
    assert check.status == "na"


def test_missing_state_with_apache_url_is_an_error_line():
    check = host.check_apache(HostStateRead(problem="missing"), ApacheProbe(url="http://x"))
    assert check.status == "error" and "collector not running" in check.summary


def _run_result(returncode, stdout="", stderr=""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_systemd_not_booted_is_not_applicable(monkeypatch):
    err = "System has not been booted with systemd as init system (PID 1). Can't operate."
    monkeypatch.setattr(host.subprocess, "run", _run_result(1, stderr=err))
    assert host.probe_systemd(1.0, ()).status == "na"


def test_systemd_other_failure_is_an_error(monkeypatch):
    err = "Failed to connect to bus: Permission denied\nmore"
    monkeypatch.setattr(host.subprocess, "run", _run_result(1, stderr=err))
    check = host.probe_systemd(1.0, ())
    assert (
        check.status == "error" and check.summary == "Failed to connect to bus: Permission denied"
    )


def test_vrrp_backup_with_non_negative_lead_is_critical():
    read = _read(vrrp=VrrpState("keepalived-json", [VrrpInstance("vi_1", "BACKUP", 190, 191, 190)]))
    assert host.check_vrrp(read, VrrpProbe()).status == "crit"


def test_apache_requests_at_critical_is_critical():
    read = _read(apache=ApacheState(30.0, 600.0, 0.1, 0.05, 0))
    probe = ApacheProbe(url="http://x", requests_warning=400, requests_critical=500)
    assert host.check_apache(read, probe).status == "crit"


def test_apache_slots_band_is_critical_with_defaults():
    read = _read(apache=ApacheState(30.0, 10.0, 0.96, 0.05, 0))
    assert host.check_apache(read, ApacheProbe(url="http://x")).status == "crit"
