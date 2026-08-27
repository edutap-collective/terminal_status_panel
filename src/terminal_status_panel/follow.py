"""Keep the panel on screen and refresh it.

The decisions follow mode makes are separated from the loop that makes them, so
each can be tested without a terminal: which interval to use, how long to wait
after a pass, and how much of the panel fits.
"""

from __future__ import annotations

import sys
import time
from itertools import chain

from rich.control import Control
from rich.segment import Segment, Segments

from .config import Config
from .render.layout import build_layout

#: No panel has anything new to say more often than this, and the cheapest of
#: them takes half a second to collect. An interval below it is raised rather
#: than refused: the flag's intent is clear, only its value is unreasonable.
MIN_INTERVAL = 1.0


def _monotonic() -> float:
    """The loop's clock, as a seam a test can take over.

    Called rather than using ``time.monotonic`` directly so that a test can
    dictate how long a pass "took" by replacing *this* function. Replacing
    ``time.monotonic`` itself would swap the clock for the whole process --
    including the daemon threads in ``budget`` -- and whichever of them called
    it first would consume the value this loop was meant to read.
    """
    return time.monotonic()


def _sleep(seconds: float) -> None:
    """The loop's wait, as a seam for the same reason as ``_monotonic``."""
    time.sleep(seconds)


def default_interval(sections: tuple[str, ...], cfg: Config) -> float:
    """The refresh interval for these sections.

    A rule rather than a table: whatever carries the health section carries its
    cost. Stated this way it is also right for `--sections docker,health`,
    where no per-command default could have been.
    """
    if "health" in sections:
        return cfg.follow_health_interval
    return cfg.follow_interval


def next_delay(interval: float, elapsed: float) -> float:
    """How long to wait after a pass that took *elapsed* seconds.

    Ordinarily the remainder of the interval. The floor is the second term: a
    pass that overran -- a hung Docker socket, a slow cluster -- waits at least
    as long again rather than lapping itself, so the panel can never take more
    than half the wall clock whatever it is told to do.
    """
    return max(interval - elapsed, elapsed)


def crop(lines: list[list[Segment]], height: int) -> tuple[list[list[Segment]], int]:
    """The lines that fit above the status row, and the count left out.

    Rich does not crop for us -- ``Console.render_lines`` returns all twenty
    lines of a twenty-line renderable on a ten-line console -- so the cut is
    ours to make, and therefore ours to get right. Each line is the list of
    segments Rich rendered it as, not a plain string: the cut is a slice of
    that list, so every segment keeps the style it was rendered with, and the
    colour a line was given survives the crop untouched.
    """
    if height <= 0:
        return [], 0
    room = height - 1
    if len(lines) <= room:
        return list(lines), 0
    return list(lines[:room]), len(lines) - room


def _row(*parts: str | None) -> str:
    """Join the parts that exist. A part is omitted, not rendered empty."""
    return " · ".join(part for part in parts if part)


def status_line(hidden: int, interval: float, width: int, error: str | None = None) -> str:
    """The bottom row: what is out of sight, the cadence, and how to stop.

    A list of candidate rows in order of preference, returning the first that
    fits. The stop hint is in every one of them and survives to the last:
    without it a reader who does not know the panel cannot leave it. The error
    is the only unbounded part, so it is the one that gets shortened, and the
    hidden-line count is dropped before the cadence because "how often does
    this refresh" is the more useful of the two once space is scarce.
    """
    stop_hint = "Ctrl-C to stop"
    interval_str = f"every {interval:g}s"
    hidden_str = f"↓ {hidden} more lines" if hidden > 0 else None

    full = _row(hidden_str, interval_str, error, stop_hint)
    if len(full) <= width:
        return full

    minimal = _row(interval_str, stop_hint)
    if len(minimal) > width:
        # Not even the cadence and the hint fit. The hint is the part that has
        # to survive, cut to whatever there is.
        return stop_hint[:width]

    if not error:
        # The only part left to drop is the hidden-line count, and what remains
        # is `minimal`, which was just measured as fitting.
        return minimal

    without_error = _row(hidden_str, interval_str, stop_hint)
    if len(without_error) <= width:
        return without_error

    # Keep the error, shortened, for as long as any of it survives -- a
    # truncated reason still names the failure, where dropping it says nothing
    # happened. Tried with the hidden-line count first, then without it.
    for prefix in ([hidden_str] if hidden_str else []) + [None]:
        kept = _row(prefix, interval_str, stop_hint)
        available = width - len(kept) - 3  # -3 for the " · " before the error
        if available > 0:
            return _row(prefix, interval_str, error[:available], stop_hint)[:width]

    return without_error[:width]


def run_follow(
    cfg: Config,
    sections: tuple[str, ...],
    *,
    width: int | None,
    no_color: bool,
    interval: float | None,
) -> int:
    """Render the panel on the alternate screen until interrupted.

    Always returns 0. A status panel must never fail a login shell, and follow
    mode is the same program with a loop around it.
    """
    from . import cli  # module object, not its names -- see the note above

    # Not `interval or default_interval(...)`: 0.0 is falsy, so `or` cannot
    # tell an explicit --interval 0 apart from no --interval at all, and
    # would silently replace the fastest cadence a user can ask for with
    # whichever section default is slowest. `None` is the one value argparse
    # gives this parameter to mean "not given" -- everything else, including
    # zero, is a value someone typed and is owed the floor below, not a
    # fallback.
    requested = interval if interval is not None else default_interval(sections, cfg)
    chosen = max(requested, MIN_INTERVAL)
    console = cli.build_console(cli.resolve_width(width, cfg), no_color)

    if not sys.stdout.isatty():
        # A loop inside a pipe is a trap, and this panel's other job is to be
        # generated into an MOTD. One frame, then out.
        console.print(build_layout(cli.collect_all(cfg, sections), cfg, sections))
        return 0

    try:
        with console.screen() as screen:
            while True:
                started = _monotonic()
                error: str | None = None
                try:
                    # Re-read the size every pass, so resizing the window
                    # mid-run is picked up. resolve_width runs once otherwise.
                    console.width = cli.resolve_width(width, cfg)
                    data = cli.collect_all(cfg, sections)
                    # A list of segment lines, not joined text: joining threw
                    # every segment's style away, which is why an earlier
                    # version of this loop rendered the whole panel in grey.
                    # `pad=False` means no line carries trailing blank
                    # segments, so the `.rstrip()` an earlier version needed
                    # on the joined string has nothing left to do here.
                    rendered = console.render_lines(build_layout(data, cfg, sections), pad=False)
                except Exception as exc:  # reported in the status line, not raised
                    # A pass that fails is a fact to show, not a reason to
                    # stop. The next one may well succeed.
                    rendered = []
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = _monotonic() - started
                delay = next_delay(chosen, elapsed)
                # `delay` is only the *remaining* wait, not the cadence: on an
                # ordinary pass elapsed + delay is `chosen` again, but once the
                # floor in `next_delay` fires, delay equals elapsed and the two
                # together are 2 * elapsed -- double `chosen`, not `chosen`
                # itself. The status line must show that doubled number, since
                # it is the cadence this loop is actually keeping; showing
                # `delay` alone would report a value that is never the
                # interval and drifts on every pass.
                cadence = elapsed + delay
                kept, hidden = crop(rendered, console.size.height)
                status = [Segment(status_line(hidden, cadence, console.width, error))]
                lines = [*kept, status]

                # `console.print` does not reposition the cursor between passes,
                # so a frame shorter than the last leaves the previous one's tail
                # on screen underneath it rather than replacing it. Homing the
                # cursor first, then updating through `Screen` -- which pads
                # every line to the full width and fills to the full height --
                # is what makes each pass actually overwrite the last, the way
                # `top` does. `render_lines` splits on line boundaries but
                # drops the newline itself, so each line's segments need one
                # put back before they are flattened into a single sequence.
                console.control(Control.home())
                screen.update(
                    Segments(chain.from_iterable((*line, Segment.line()) for line in lines))
                )
                _sleep(delay)
    except KeyboardInterrupt:
        return 0
