"""Bar-chart rendering helpers with threshold-based coloring."""

from __future__ import annotations

from rich.text import Text

STATUS_COLORS: dict[str, str] = {
    "ok": "green",
    "warning": "yellow",
    "critical": "red",
}

_FILLED = "█"
_EMPTY = "░"


def classify(value: float, warning: float, critical: float) -> str:
    """Which band *value* falls into: ok, warning or critical."""
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def filled_cells(percent: float, width: int) -> int:
    """How many cells of a *width*-wide bar *percent* fills, clamped to it."""
    cells = round(percent / 100 * width)
    return max(0, min(width, cells))


def render_bar(percent: float, status: str, width: int = 18) -> Text:
    """One usage bar, coloured by *status*."""
    filled = filled_cells(percent, width)
    body = _FILLED * filled + _EMPTY * (width - filled)
    return Text(body, style=STATUS_COLORS.get(status, "white"))


def format_bytes(n: int | None) -> str:
    """A byte count in the largest unit that keeps it readable.

    ``None`` renders as "n/a": no measurement was taken, which is not zero.
    """
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"
