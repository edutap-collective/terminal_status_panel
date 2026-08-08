import pytest

from terminal_status_panel import follow
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


def test_a_pass_slower_than_the_interval_still_waits(monkeypatch):
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
