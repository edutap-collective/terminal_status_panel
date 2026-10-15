from types import SimpleNamespace

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


def test_missing_state_is_an_error_line_not_quiet():
    check = host.check_vrrp(HostStateRead(problem="missing"), VrrpProbe())
    assert check.status == "error" and "collector not running" in check.summary
