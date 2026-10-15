import pytest

from terminal_status_panel.model.host import (
    SCHEMA_VERSION,
    ApacheState,
    EventCount,
    HostState,
    UpdateCounts,
    VrrpInstance,
    VrrpState,
)


def _state():
    return HostState(
        written_at=1000.0,
        collector="0.14.0",
        vrrp=VrrpState(
            source="keepalived-json",
            instances=[
                VrrpInstance("vi_1", "MASTER", 200, 191, 191),
                VrrpInstance("vi_2", "BACKUP", 190, 188, 196),
            ],
        ),
        apache=ApacheState(30.0, 112.4, 0.19, 0.07, 0),
        updates=UpdateCounts(900.0, 3, 1, "apt-simulate"),
        events={"vrrp_state": EventCount(4, 990.0), "oom_kill": EventCount(0, None)},
        errors={"apache": None, "vrrp": None, "events": None, "updates": None},
    )


def test_round_trip_keeps_every_field():
    state = _state()
    assert HostState.from_dict(state.to_dict()) == state


def test_schema_is_written():
    assert _state().to_dict()["schema"] == SCHEMA_VERSION


def test_unknown_schema_is_refused():
    data = _state().to_dict()
    data["schema"] = 2
    with pytest.raises(ValueError, match="schema"):
        HostState.from_dict(data)


def test_missing_blocks_are_none():
    state = HostState.from_dict({"schema": 1, "written_at": 1.0, "collector": "x"})
    assert state.vrrp is None and state.apache is None and state.updates is None
    assert state.events == {}


def test_holds_and_lead():
    master = VrrpInstance("vi_1", "MASTER", 200, 191, 191)
    backup = VrrpInstance("vi_2", "BACKUP", 190, 188, 196)
    assert master.holds and master.lead is None  # a master never learns the backups
    assert not backup.holds and backup.lead == -8
