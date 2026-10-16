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


def test_unknown_keys_inside_blocks_are_ignored():
    data = _state().to_dict()
    data["apache"]["extra"] = 1
    data["vrrp"]["instances"][0]["extra"] = 1
    assert HostState.from_dict(data) == _state()


def test_non_finite_written_at_is_refused():
    data = _state().to_dict()
    data["written_at"] = float("nan")
    with pytest.raises(ValueError):
        HostState.from_dict(data)


def _with(path: tuple, value):
    """The test state as a dict, with the field at *path* replaced by *value*."""
    data = _state().to_dict()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("written_at",), "1000.0"),
        (("written_at",), True),
        (("collector",), 14),
        (("events_window_s",), 1.5),
        (("events_window_s",), "43200"),
        (("events", "vrrp_state", "count"), "3"),
        (("events", "vrrp_state", "count"), True),
        (("events", "vrrp_state", "last"), "990"),
        (("vrrp", "source"), 1),
        (("vrrp", "instances", 0, "name"), 1),
        (("vrrp", "instances", 0, "state"), None),
        (("vrrp", "instances", 0, "base_priority"), "200"),
        (("vrrp", "instances", 0, "effective_priority"), 191.0),
        (("vrrp", "instances", 0, "master_priority"), False),
        (("apache", "window_s"), "30"),
        (("apache", "requests_per_s"), "112.4"),
        (("apache", "slots_finishing"), 0.5),
        (("updates", "available"), "3"),
        (("updates", "source"), None),
        (("errors", "vrrp"), 5),
        (("errors",), ["parse"]),
    ],
)
def test_wrong_field_types_are_refused(path, value):
    with pytest.raises(ValueError):
        HostState.from_dict(_with(path, value))


def test_integers_are_accepted_where_floats_are_expected():
    data = _with(("written_at",), 1000)
    data["apache"]["requests_per_s"] = 112
    state = HostState.from_dict(data)
    assert state.written_at == 1000.0 and state.apache.requests_per_s == 112


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("events", "vrrp_state", "last"), float("-inf")),
        (("apache", "requests_per_s"), float("nan")),
        (("apache", "window_s"), float("inf")),
        (("updates", "checked_at"), float("inf")),
        (("updates", "checked_at"), 10**400),
    ],
)
def test_non_finite_floats_in_blocks_are_refused(path, value):
    with pytest.raises(ValueError):
        HostState.from_dict(_with(path, value))
