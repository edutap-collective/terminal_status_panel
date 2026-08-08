"""Keep the panel on screen and refresh it.

The decisions follow mode makes are separated from the loop that makes them, so
each can be tested without a terminal: which interval to use, how long to wait
after a pass, and how much of the panel fits.
"""

from __future__ import annotations

from .config import Config

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
