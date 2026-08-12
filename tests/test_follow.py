import io
import re
import threading
import time

import pytest
from rich.console import Console
from rich.segment import Segment

from terminal_status_panel import cli, follow
from terminal_status_panel.config import Config


def _line(text: str) -> list[Segment]:
    """A one-segment line, standing in for what `Console.render_lines` returns.

    `crop` only ever slices the outer list, so a single plain segment per line
    is enough to pin its behaviour without dragging in a real render.
    """
    return [Segment(text)]


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """The written stream with every control/colour sequence removed.

    Used to read the plain text of a captured frame; `_count_sgr` below reads
    the same stream for the sequences this one throws away.
    """
    return _ANSI.sub("", text)


def _count_sgr(text: str) -> int:
    """How many colour/style (SGR) sequences a captured frame carries.

    Distinct from `_ANSI`, which also matches cursor-control sequences such as
    the home command -- those exist whether or not the frame has any colour in
    it, so counting them would not tell colour and plain text apart.
    """
    return len(re.findall(r"\x1b\[[0-9;]*m", text))


def test_sections_without_health_refresh_every_five_seconds():
    assert follow.default_interval(("server", "docker"), Config()) == 5.0


def test_any_selection_containing_health_refreshes_every_twenty():
    """The rule is about the section, not about the command.

    status-health and status-full both carry it, and so does an explicit
    --sections docker,health -- which is why this is a rule and not a table.
    """
    assert follow.default_interval(("health",), Config()) == 20.0
    assert follow.default_interval(("server", "docker", "health", "traefik"), Config()) == 20.0
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
    lines = [_line(f"line {i}") for i in range(20)]
    kept, hidden = follow.crop(lines, height=10)
    assert kept == lines[:9]  # one row belongs to the status line
    assert hidden == 11


def test_content_that_fits_hides_nothing():
    lines = [_line("a"), _line("b"), _line("c")]
    kept, hidden = follow.crop(lines, height=10)
    assert kept == lines
    assert hidden == 0


def test_a_screen_with_room_for_only_the_status_line():
    kept, hidden = follow.crop([_line("a"), _line("b")], height=1)
    assert kept == []
    assert hidden == 2


def test_no_screen_reports_nothing():
    assert follow.crop([_line("a"), _line("b")], height=0) == ([], 0)


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
    line = follow.status_line(
        hidden=999, interval=5.0, width=20, error="a very long failure message indeed"
    )
    assert len(line) <= 20


def test_the_stop_hint_survives_a_long_error_in_a_narrow_row():
    """The row's whole purpose is telling you how to leave it.

    The error is the one unbounded part, so it is what gives way -- not the
    hint a reader needs precisely when something has gone wrong.
    """
    line = follow.status_line(
        hidden=999, interval=5.0, width=40, error="a very long failure message indeed"
    )
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
    monkeypatch.setattr(follow, "_sleep", slept.append)
    assert follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=None) == 0
    assert slept == []
    assert "SYSTEM" in capsys.readouterr().out


def test_an_interrupt_ends_the_loop_cleanly(monkeypatch):
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(follow, "_sleep", _StopAfter(1))
    assert follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=5.0) == 0


def test_a_failing_pass_does_not_end_the_loop(monkeypatch):
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    stopper = _StopAfter(3)
    monkeypatch.setattr(follow, "_sleep", stopper)

    def boom(*args, **kwargs):
        raise RuntimeError("collector fell over")

    # Patched on `cli`, not on `follow`: run_follow reaches the collectors
    # through the `cli` module object, which is what breaks the import cycle.
    monkeypatch.setattr(cli, "collect_all", boom)
    assert follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=5.0) == 0
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
    monkeypatch.setattr(follow, "_sleep", stopper)
    follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=0.1)
    # Not `>= MIN_INTERVAL`: `next_delay` subtracts the pass's real elapsed
    # time from the chosen interval, so the delay is always a little under
    # MIN_INTERVAL for any pass that takes measurable time -- which any real
    # collect-and-render pass does (confirmed: even two back-to-back
    # `time.monotonic()` calls with nothing between them differ). What this
    # test can actually show is that the floor was applied at all: a delay
    # near the 1s floor, not near the 0.1s that was asked for.
    assert stopper.delays[0] > follow.MIN_INTERVAL / 2, "the interval floor was not applied"


def test_an_explicit_zero_interval_is_floored_not_ignored(monkeypatch):
    """Zero is a value the user typed, not the absence of one.

    A truthiness test cannot tell them apart, and mistaking an explicit zero
    for silence turns the fastest interval the user can ask for into the
    slowest one the section carries.
    """
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)

    # A stand-in for a pass, not a real one: the health section's own
    # collectors reach a real Docker socket, which this test has no business
    # doing just to read off a delay. Failing fast keeps `elapsed` near zero,
    # so the recorded delay is `chosen` itself -- exactly what is under test
    # here, and nothing about the render.
    def boom(*args, **kwargs):
        raise RuntimeError("collector stand-in")

    monkeypatch.setattr(cli, "collect_all", boom)
    stopper = _StopAfter(1)
    monkeypatch.setattr(follow, "_sleep", stopper)
    follow.run_follow(Config(), ("health",), width=100, no_color=True, interval=0.0)
    # An ignored zero would fall back to the 20s health default; a floored
    # zero lands near MIN_INTERVAL instead, the same shape the floor test
    # above checks for.
    assert stopper.delays[0] < 5.0, "explicit --interval 0 was not floored"


def test_each_pass_redraws_the_whole_screen(monkeypatch):
    """A frame shorter than the last must erase it, not scroll it away.

    Without homing the cursor and padding to the full height, a short panel
    leaves the previous frame's tail on screen underneath it.
    """
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)

    # A real `Console`, forced into terminal mode so it emits control codes,
    # writing to a buffer instead of the real screen -- the seam this test
    # needs to inspect the raw stream `run_follow` actually produces.
    buffer = io.StringIO()
    capturing = Console(file=buffer, force_terminal=True, width=100, height=24)
    monkeypatch.setattr(cli, "build_console", lambda width, no_color: capturing)

    stopper = _StopAfter(2)
    monkeypatch.setattr(follow, "_sleep", stopper)

    follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=5.0)

    # One home sequence comes from entering the alternate screen itself; one
    # more per pass is what actually erases the previous frame. Two passes
    # ran, so three in total -- a count stuck at one would mean later passes
    # scrolled underneath the first instead of overwriting it.
    assert buffer.getvalue().count("\x1b[H") == 3


def _run_one_pass_and_capture(monkeypatch, *, interval: float, monotonic_calls: list[float]) -> str:
    """A single pass of `run_follow`, with a controlled elapsed time.

    `started`/`elapsed` inside the loop both come from `follow._monotonic`,
    called exactly twice per pass with the stubbed collectors below doing
    nothing measurable in between -- so handing it a fixed two-value sequence
    is what lets a test dictate exactly how long a pass "took" without an
    actual sleep. Returns the frame's plain text, ANSI sequences stripped.

    The module's own seams are replaced, never `time.monotonic` and
    `time.sleep` themselves. Those are process-wide, and the daemon threads in
    `budget` call both: a straggler left over from an earlier test would land
    its own `sleep` in `stopper.delays`, or eat a value out of this iterator,
    and the assertions would then be measuring that thread instead of this
    loop. Observed in CI as `delay == 0.1` where 19.38 was expected.
    """
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)

    calls = iter(monotonic_calls)
    monkeypatch.setattr(follow, "_monotonic", lambda: next(calls))

    buffer = io.StringIO()
    capturing = Console(file=buffer, force_terminal=True, width=100, height=24)
    monkeypatch.setattr(cli, "build_console", lambda width, no_color: capturing)

    stopper = _StopAfter(1)
    monkeypatch.setattr(follow, "_sleep", stopper)

    follow.run_follow(Config(), ("server",), width=100, no_color=True, interval=interval)
    return _strip_ansi(buffer.getvalue()), stopper.delays[0]


def test_an_ordinary_pass_reports_the_configured_interval(monkeypatch):
    """Finding 1: the status line must show the cadence, `elapsed + delay` --
    not `delay`, the remaining wait, which an earlier version of this loop
    passed to it directly.

    On an ordinary pass the two happen to share a value only sometimes: here
    interval=20s and the pass takes 0.62s, so `delay` is 19.38s while the
    cadence is the full 20s the flag actually asked for. Reporting `delay`
    would have shown "every 19.38s" -- a number that changes on every single
    pass depending on how long collection took, never the interval itself.
    """
    plain, delay = _run_one_pass_and_capture(
        monkeypatch, interval=20.0, monotonic_calls=[0.0, 0.62]
    )
    assert "every 20s" in plain, plain
    assert delay == pytest.approx(19.38)


def test_a_pass_that_overruns_reports_the_doubled_cadence_not_the_delay(monkeypatch):
    """The floor case -- the one case the floor exists for, and the one the
    original bug got wrong by a factor of two.

    An 8s pass against a 5s interval trips `next_delay`'s floor: the loop
    waits another 8s rather than lapping itself, so the cadence it is
    actually keeping is elapsed + delay = 16s. The bug reported `delay` alone
    (8s, read as "the interval"), which was neither the 5s configured nor the
    16s actually being kept.
    """
    plain, delay = _run_one_pass_and_capture(monkeypatch, interval=5.0, monotonic_calls=[0.0, 8.0])
    assert "every 16s" in plain, plain
    assert "every 5s" not in plain
    assert "every 8s" not in plain
    assert delay == 8.0


def test_follow_mode_keeps_the_panels_colour(monkeypatch):
    """Finding 2: joining segment text and re-rendering it plain threw every
    style away -- thresholds, bars, rules and the OS logo all rendered grey in
    the one mode built to watch a cluster change state.

    Compares the follow-path frame against a one-shot render of the same
    layout at the same width: both must carry SGR (colour/style) sequences,
    and neither count should be anywhere near zero.
    """
    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "collect_resources", lambda *a, **k: None)
    monkeypatch.setattr(cli, "collect_processes", lambda *a, **k: None)

    buffer = io.StringIO()
    capturing = Console(
        file=buffer, force_terminal=True, width=100, height=30, color_system="standard"
    )
    monkeypatch.setattr(cli, "build_console", lambda width, no_color: capturing)

    stopper = _StopAfter(1)
    monkeypatch.setattr(follow, "_sleep", stopper)

    follow.run_follow(Config(), ("server",), width=100, no_color=False, interval=5.0)
    follow_sgr = _count_sgr(buffer.getvalue())

    one_shot = Console(width=100, force_terminal=True, color_system="standard")
    with one_shot.capture() as capture:
        from terminal_status_panel.render.layout import build_layout

        one_shot.print(build_layout(cli.collect_all(Config(), ("server",)), Config(), ("server",)))
    one_shot_sgr = _count_sgr(capture.get())

    assert follow_sgr > 0, "follow mode's frame carries no colour at all"
    # Not an exact match -- the status line and screen padding add a little
    # noise -- but the two paths render the same layout, so the counts must
    # land in the same order of magnitude, not a bare few against over a
    # hundred.
    assert follow_sgr > one_shot_sgr / 2


def test_follow_mode_keeps_the_panels_hyperlinks(monkeypatch):
    """The frame is carried as segments, so a link in a style survives it.

    Before 0.5.0's colour fix the loop flattened each frame to plain strings,
    and this feature would have worked once and gone silent here — which is
    why it is worth a test of its own rather than an assumption.
    """
    from terminal_status_panel.model import (
        PanelData,
        TraefikEntrypoint,
        TraefikInfo,
        TraefikRouter,
        TraefikServiceRef,
    )

    info = TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="login_example_de", address=":2009", port=2009)],
        routers=[
            TraefikRouter(
                name="account-spa",
                entrypoints=["login_example_de"],
                rule="PathPrefix(`/account`)",
                service="account-spa",
            )
        ],
        services={"account-spa": TraefikServiceRef(name="account-spa", port=8080)},
    )
    cfg = Config()
    cfg.traefik.links = {"login_example_de": "https://login.example.de"}

    monkeypatch.setattr(follow.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "collect_all", lambda *a, **k: PanelData(traefik=info))

    buffer = io.StringIO()
    capturing = Console(
        file=buffer, force_terminal=True, width=100, height=30, color_system="standard"
    )
    monkeypatch.setattr(cli, "build_console", lambda width, no_color: capturing)
    monkeypatch.setattr(follow, "_sleep", _StopAfter(1))

    follow.run_follow(cfg, ("traefik",), width=100, no_color=False, interval=5.0)

    assert "\x1b]8;" in buffer.getvalue(), "the frame carries no hyperlink"


def test_a_stray_thread_cannot_hijack_the_loop_measurements(monkeypatch):
    """The regression the seams exist for.

    An earlier version replaced `time.sleep` and `time.monotonic` themselves,
    which are process-wide. The daemon threads in `budget` call both, so a
    straggler left over from an earlier test could land its own `sleep` in the
    captured delays -- and did, in CI, where this assertion read 0.1 instead
    of 19.38. Patching the module's seams leaves such a thread on the real
    clock, where it belongs.
    """
    started = threading.Event()

    def straggler():
        started.wait(timeout=5)
        time.sleep(0.1)  # the real one: it must not reach the assertions

    thread = threading.Thread(target=straggler, daemon=True, name="check-leftover")
    thread.start()
    started.set()

    plain, delay = _run_one_pass_and_capture(
        monkeypatch, interval=20.0, monotonic_calls=[0.0, 0.62]
    )

    assert "every 20s" in plain, plain
    assert delay == pytest.approx(19.38)
    thread.join(timeout=5)
