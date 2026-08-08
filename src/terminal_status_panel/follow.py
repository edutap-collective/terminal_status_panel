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

    Truncated to *width* rather than wrapped. A status row that wrapped would
    take two of the screen's rows while claiming one, and the panel's first
    line would scroll off the top.
    """
    parts = []
    if hidden > 0:
        parts.append(f"↓ {hidden} more lines")
    parts.append(f"every {interval:g}s")
    if error:
        parts.append(error)
    parts.append("Ctrl-C to stop")
    line = " · ".join(parts)
    return line[:width]
