from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import PanelData, SwarmInfo, TroubleEntry
from terminal_status_panel.model.host import EventCount, HostState, HostStateRead
from terminal_status_panel.render.trouble import trouble_section


def _render(data):
    console = Console(width=120, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(trouble_section(data, Config()))
    return capture.get()


def test_nothing_to_report_renders_nothing():
    assert _render(PanelData()).strip() == ""


def test_host_and_container_rows_share_one_block():
    swarm = SwarmInfo(
        reachable=True,
        trouble=[TroubleEntry(name="app_web", fails=3, cause="exit 1", source="container")],
    )
    host = HostStateRead(
        state=HostState(written_at=1.0, collector="x", events={"oom_kill": EventCount(1, 1.0)}),
        age_s=1.0,
    )
    out = _render(PanelData(swarm=swarm, host=host))
    assert out.count("TROUBLE") == 1 and "app_web" in out and "host: OOM kill" in out


def test_host_row_alone_renders_without_docker():
    host = HostStateRead(
        state=HostState(written_at=1.0, collector="x", events={"unit_failed": EventCount(1, 1.0)}),
        age_s=1.0,
    )
    assert "host: unit failed" in _render(PanelData(host=host))


def _failed_journal(**events):
    return HostStateRead(
        state=HostState(
            written_at=1.0, collector="x", events=events, errors={"events": "no-permission"}
        ),
        age_s=1.0,
    )


def test_failed_journal_read_with_zero_counts_still_renders_trouble():
    out = _render(PanelData(host=_failed_journal(oom_kill=EventCount(0, None))))
    assert "TROUBLE" in out
    assert "⚠️ host events: journal not refreshed (no-permission)" in out


def test_journal_warning_appears_once_beside_the_rows():
    out = _render(PanelData(host=_failed_journal(oom_kill=EventCount(2, 1.0))))
    assert "host: OOM kill" in out and out.count("journal not refreshed") == 1


def test_no_journal_warning_when_host_events_are_off():
    console = Console(width=120, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(
            trouble_section(PanelData(host=_failed_journal()), Config(trouble_host_events=()))
        )
    assert capture.get().strip() == ""


def test_trouble_only_layout_shows_the_journal_warning():
    from terminal_status_panel.render.layout import build_layout

    console = Console(width=120, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(
            build_layout(PanelData(host=_failed_journal()), Config(), sections=("trouble",))
        )
    assert "host events: journal not refreshed (no-permission)" in capture.get()
