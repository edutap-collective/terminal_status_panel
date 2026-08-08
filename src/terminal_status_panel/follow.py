"""Keep the panel on screen and refresh it.

The decisions follow mode makes are separated from the loop that makes them, so
each can be tested without a terminal: which interval to use, how long to wait
after a pass, and how much of the panel fits.
"""

from __future__ import annotations

import sys
import time

from rich.console import Group
from rich.control import Control
from rich.text import Text

from .config import Config
from .render.layout import build_layout

#: No panel has anything new to say more often than this, and the cheapest of
#: them takes half a second to collect. An interval below it is raised rather
#: than refused: the flag's intent is clear, only its value is unreasonable.
MIN_INTERVAL = 1.0


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


def crop(lines: list[str], height: int) -> tuple[list[str], int]:
    """The lines that fit above the status row, and the count left out.

    Rich does not crop for us -- ``Console.render_lines`` returns all twenty
    lines of a twenty-line renderable on a ten-line console -- so the cut is
    ours to make, and therefore ours to get right.
    """
    if height <= 0:
        return [], 0
    room = height - 1
    if len(lines) <= room:
        return list(lines), 0
    return list(lines[:room]), len(lines) - room


def status_line(hidden: int, interval: float, width: int,
                error: str | None = None) -> str:
    """The bottom row: what is out of sight, the cadence, and how to stop.

    Preserves the stop hint even when space is tight. The error message is
    the only unbounded part; if necessary it is truncated or omitted to
    ensure the hint stays visible.
    """
    stop_hint = "Ctrl-C to stop"
    interval_str = f"every {interval:g}s"

    # Build full row with all components.
    parts = []
    if hidden > 0:
        parts.append(f"↓ {hidden} more lines")
    parts.append(interval_str)
    if error:
        parts.append(error)
    parts.append(stop_hint)

    result = " · ".join(parts)
    if len(result) <= width:
        return result

    # Row is too long. Check if the minimal row (interval + hint) fits.
    parts_minimal = [interval_str, stop_hint]
    result_minimal = " · ".join(parts_minimal)
    if len(result_minimal) > width:
        # Minimal doesn't fit; just return hint truncated to width.
        return stop_hint[:width]

    # Minimal fits. Try removing error.
    if error:
        parts_no_error = []
        if hidden > 0:
            parts_no_error.append(f"↓ {hidden} more lines")
        parts_no_error.append(interval_str)
        parts_no_error.append(stop_hint)

        result_no_error = " · ".join(parts_no_error)
        if len(result_no_error) <= width:
            return result_no_error

        # Try to fit truncated error.
        if hidden > 0:
            hidden_str = f"↓ {hidden} more lines"
            # Calculate space available for error between interval and hint.
            required = len(" · ".join([hidden_str, interval_str, stop_hint]))
            available = width - required - 3  # -3 for the " · " separator.
            if available > 0:
                truncated = error[:available]
                return " · ".join([hidden_str, interval_str, truncated,
                                   stop_hint])[:width]

        # Try truncated error without hidden.
        required = len(" · ".join([interval_str, stop_hint]))
        available = width - required - 3
        if available > 0:
            truncated = error[:available]
            return " · ".join([interval_str, truncated, stop_hint])[:width]

        # No space for truncated error; return without it.
        return result_no_error[:width]

    # No error but too long. Try removing hidden.
    if hidden > 0:
        parts_no_hidden = [interval_str, stop_hint]
        result_no_hidden = " · ".join(parts_no_hidden)
        if len(result_no_hidden) <= width:
            return result_no_hidden

    # Should not reach here (minimal fits), but fallback to hint.
    return stop_hint[:width]


def run_follow(cfg: Config, sections: tuple[str, ...], *,
               width: int | None, no_color: bool,
               interval: float | None) -> int:
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
                started = time.monotonic()
                error: str | None = None
                try:
                    # Re-read the size every pass, so resizing the window
                    # mid-run is picked up. resolve_width runs once otherwise.
                    console.width = cli.resolve_width(width, cfg)
                    data = cli.collect_all(cfg, sections)
                    rendered = [
                        "".join(segment.text for segment in line).rstrip()
                        for line in console.render_lines(
                            build_layout(data, cfg, sections), pad=False)
                    ]
                except Exception as exc:  # reported in the status line, not raised
                    # A pass that fails is a fact to show, not a reason to
                    # stop. The next one may well succeed.
                    rendered = []
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = time.monotonic() - started
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
                lines = kept + [status_line(hidden, cadence, console.width, error)]

                # `console.print` does not reposition the cursor between passes,
                # so a frame shorter than the last leaves the previous one's tail
                # on screen underneath it rather than replacing it. Homing the
                # cursor first, then updating through `Screen` -- which pads
                # every line to the full width and fills to the full height --
                # is what makes each pass actually overwrite the last, the way
                # `top` does.
                console.control(Control.home())
                screen.update(Group(*[Text(line) for line in lines]))
                time.sleep(delay)
    except KeyboardInterrupt:
        return 0
