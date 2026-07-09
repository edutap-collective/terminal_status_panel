from rich.text import Text

from lmu.terminal_status_panel.render import bars


def test_classify_thresholds():
    assert bars.classify(50, 75, 90) == "ok"
    assert bars.classify(80, 75, 90) == "warning"
    assert bars.classify(95, 75, 90) == "critical"
    assert bars.classify(90, 75, 90) == "critical"  # boundary is inclusive


def test_filled_cells_proportional_and_clamped():
    assert bars.filled_cells(50, 20) == 10
    assert bars.filled_cells(0, 20) == 0
    assert bars.filled_cells(100, 20) == 20
    assert bars.filled_cells(150, 20) == 20  # clamped


def test_render_bar_is_styled_text():
    bar = bars.render_bar(50.0, "critical", width=10)
    assert isinstance(bar, Text)
    assert bar.plain.count("█") == 5
    assert bar.plain.count("░") == 5
    assert bar.style == "red"


def test_format_bytes():
    assert bars.format_bytes(None) == "n/a"
    assert bars.format_bytes(0) == "0.0 B"
    assert bars.format_bytes(20_400_000_000).endswith("GB")
