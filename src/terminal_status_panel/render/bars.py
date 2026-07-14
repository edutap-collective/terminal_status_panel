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
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def filled_cells(percent: float, width: int) -> int:
    cells = round(percent / 100 * width)
    return max(0, min(width, cells))


def render_bar(percent: float, status: str, width: int = 18) -> Text:
    filled = filled_cells(percent, width)
    body = _FILLED * filled + _EMPTY * (width - filled)
    return Text(body, style=STATUS_COLORS.get(status, "white"))


def format_bytes(n: int | None) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"
