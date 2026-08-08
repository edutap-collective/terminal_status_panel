import pytest

from terminal_status_panel import cli, follow
from terminal_status_panel.config import Config


def test_sections_without_health_refresh_every_five_seconds():
    assert follow.default_interval(("server", "docker"), Config()) == 5.0


def test_any_selection_containing_health_refreshes_every_twenty():
    """The rule is about the section, not about the command.

    status-health and status-full both carry it, and so does an explicit
    --sections docker,health -- which is why this is a rule and not a table.
    """
    assert follow.default_interval(("health",), Config()) == 20.0
    assert follow.default_interval(("server", "docker", "health", "traefik"),
                                   Config()) == 20.0
    assert follow.default_interval(("docker", "health"), Config()) == 20.0


def test_the_config_overrides_both_defaults():
    cfg = Config(follow_interval=2.0, follow_health_interval=60.0)
    assert follow.default_interval(("server",), cfg) == 2.0
    assert follow.default_interval(("health",), cfg) == 60.0


def test_the_ordinary_delay_is_the_interval_less_the_work():
    assert follow.next_delay(20.0, 3.7) == pytest.approx(16.3)


def test_a_pass_slower_than_the_interval_still_waits():
    """The floor. Never more than half the wall clock, whatever it is told.

    A hung Docker socket must not turn the loop into a busy one.
    """
    assert follow.next_delay(5.0, 8.0) == 8.0


def test_instant_work_waits_the_whole_interval():
    assert follow.next_delay(5.0, 0.0) == 5.0


def test_content_taller_than_the_screen_is_cropped_and_counted():
    lines = [f"line {i}" for i in range(20)]
    kept, hidden = follow.crop(lines, height=10)
    assert kept == lines[:9]      # one row belongs to the status line
    assert hidden == 11


def test_content_that_fits_hides_nothing():
    lines = ["a", "b", "c"]
    kept, hidden = follow.crop(lines, height=10)
    assert kept == lines
    assert hidden == 0


def test_a_screen_with_room_for_only_the_status_line():
    kept, hidden = follow.crop(["a", "b"], height=1)
    assert kept == []
    assert hidden == 2


def test_no_screen_reports_nothing():
    assert follow.crop(["a", "b"], height=0) == ([], 0)


def test_the_status_line_names_the_interval_and_how_to_stop():
    line = follow.status_line(hidden=0, interval=20.0, width=80)
    assert "every 20s" in line
    assert "Ctrl-C" in line


def test_the_status_line_counts_what_is_hidden():
    assert "82 more lines" in follow.status_line(hidden=82, interval=5.0, width=80)


def test_the_status_line_carries_an_error():
    line = follow.status_line(hidden=0, interval=5.0, width=80, error="docker gone")
    assert "docker gone" in line


def test_the_status_line_never_exceeds_the_width():
    """A status row that wraps pushes the panel's first line off the screen."""
    line = follow.status_line(hidden=999, interval=5.0, width=20,
                              error="a very long failure message indeed")
    assert len(line) <= 20


def test_the_stop_hint_survives_a_long_error_in_a_narrow_row():
    """The row's whole purpose is telling you how to leave it.

    The error is the one unbounded part, so it is what gives way -- not the
    hint a reader needs precisely when something has gone wrong.
    """
    line = follow.status_line(hidden=999, interval=5.0, width=40,
                              error="a very long failure message indeed")
    assert "Ctrl-C" in line
    assert len(line) <= 40


def test_the_stop_hint_in_a_width_too_narrow_for_the_full_message():
    """When the width is too narrow even for the hint, truncate the hint itself
    rather than omitting it entirely.

    The user needs it more when errors occur and content doesn't fit.
    """
    line = follow.status_line(hidden=0, interval=5.0, width=10)
    assert line == "Ctrl-C to "
    assert len(line) <= 10


class _StopAfter:
    """A sleep that counts, then interrupts — bounding the loop and exercising
    the interrupt path in one, without the suite ever actually sleeping."""

    def __init__(self, passes: int) -> None:
        self.passes = passes
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self.passes:
            raise KeyboardInterrupt


def test_without_a_terminal_it_renders_once_and_returns(monkeypatch, capsys):
    """A loop inside a pipe is a trap, and this panel is also generated into
    an MOTD."""
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: False)
    # `collect_resources` (interval-based `psutil.cpu_percent`) and
    # `collect_processes` (its CPU sampling window) call the real `time.sleep`
    # -- the very name this test patches below, since `follow`, `resources`
    # and `processes` all share the one `time` module object. Left real,
    # those samples would land in `slept` too, though they have nothing to
    # do with what this test is checking.
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)
    slept = []
    monkeypatch.setattr(follow.time, "sleep", slept.append)
    assert follow.run_follow(Config(), ("server",), width=100,
                             no_color=True, interval=None) == 0
    assert slept == []
    assert "SYSTEM" in capsys.readouterr().out


def test_an_interrupt_ends_the_loop_cleanly(monkeypatch):
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(follow.time, "sleep", _StopAfter(1))
    assert follow.run_follow(Config(), ("server",), width=100,
                             no_color=True, interval=5.0) == 0


def test_a_failing_pass_does_not_end_the_loop(monkeypatch):
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    stopper = _StopAfter(3)
    monkeypatch.setattr(follow.time, "sleep", stopper)

    def boom(*args, **kwargs):
        raise RuntimeError("collector fell over")

    # Patched on `cli`, not on `follow`: run_follow reaches the collectors
    # through the `cli` module object, which is what breaks the import cycle.
    monkeypatch.setattr(cli, "collect_all", boom)
    assert follow.run_follow(Config(), ("server",), width=100,
                             no_color=True, interval=5.0) == 0
    assert len(stopper.delays) == 3, "the loop kept going after the failure"


def test_an_interval_below_the_floor_is_raised(monkeypatch):
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    # Same reasoning as above: without this, the first recorded "sleep" would
    # be `collect_resources`'s real 0.15s CPU sample, not the loop's own
    # delay -- and `_StopAfter(1)` would interrupt before the loop ever
    # reaches its own `time.sleep(delay)` call.
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)
    stopper = _StopAfter(1)
    monkeypatch.setattr(follow.time, "sleep", stopper)
    follow.run_follow(Config(), ("server",), width=100, no_color=True,
                      interval=0.1)
    # Not `>= MIN_INTERVAL`: `next_delay` subtracts the pass's real elapsed
    # time from the chosen interval, so the delay is always a little under
    # MIN_INTERVAL for any pass that takes measurable time -- which any real
    # collect-and-render pass does (confirmed: even two back-to-back
    # `time.monotonic()` calls with nothing between them differ). What this
    # test can actually show is that the floor was applied at all: a delay
    # near the 1s floor, not near the 0.1s that was asked for.
    assert stopper.delays[0] > 0.5, "the interval floor was not applied"
