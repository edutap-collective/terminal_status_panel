import pytest
from rich.cells import cell_len

from terminal_status_panel.render import health, icons, panels


def test_every_status_glyph_is_defined_once():
    assert (icons.OK, icons.WARN, icons.DEAD) == ("✅", "⚠️", "💀")
    assert (icons.UNKNOWN, icons.TRUNCATED, icons.FAILED) == ("⬜", "…", "✗")
    assert icons.PAUSED == "⏸️"


def test_both_renderers_use_the_shared_glyphs():
    """A second copy is how the container-name patterns diverged once already."""
    assert (panels._OK, panels._WARN, panels._DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.OK, health.WARN, health.DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.UNKNOWN, health.TRUNCATED, health.FAILED) == (
        icons.UNKNOWN,
        icons.TRUNCATED,
        icons.FAILED,
    )


#: The status glyphs, as they appear beside one another in a column. `…` and
#: `✗` are excluded: they are used inline in a sentence, never as a column of
#: their own, so their width is not part of this invariant.
COLUMN_GLYPHS = [icons.OK, icons.WARN, icons.DEAD, icons.JOB, icons.UNKNOWN, icons.PAUSED]


@pytest.mark.parametrize("glyph", COLUMN_GLYPHS, ids=COLUMN_GLYPHS)
def test_every_column_glyph_occupies_two_cells(glyph):
    """A column mixing widths steps left and right down the block.

    This is not a style preference. Rich lays a column out by cell width, so a
    one-cell glyph beside a two-cell one shifts every following column on that
    row. `UNKNOWN` was `·` -- one cell against `✅`'s two -- through 0.9, and
    every cluster member list was ragged because of it.

    It also depends on rich, which is why `rich>=15.0` is a floor. Up to 14.1
    `cell_len("⚠️")` is 1 where a terminal draws two cells, so warning rows
    were one column out with any older release. The minimum-dependency job is
    what found that; the version matrix cannot, because it always resolves to
    a rich that agrees.
    """
    assert cell_len(glyph) == 2, (
        f"{glyph!r} is {cell_len(glyph)} cells wide; a status column needs 2. "
        f"Pick a glyph with East Asian Wide width, or pad it deliberately."
    )


def test_the_separator_dot_is_not_the_unknown_glyph():
    """`·` still appears in the panel, and it means something else.

    The follow-mode status line and the Swarm summary join their parts with
    ` · `. That is punctuation, not vocabulary, and a reader who greps for the
    old marker should not be led back to it.
    """
    assert icons.UNKNOWN != "·"
