import unicodedata

import pytest
from rich.cells import cell_len

from terminal_status_panel.render import health, icons, panels


def test_every_status_glyph_is_defined_once():
    assert (icons.OK, icons.WARN, icons.DEAD) == ("✅", "⚠ ", "💀")
    assert (icons.UNKNOWN, icons.TRUNCATED, icons.FAILED) == ("⬜", "…", "✗")
    assert icons.PAUSED == "💤"


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


def _cells_by_unicode(text: str) -> int:
    """The width a terminal following the Unicode tables advances the cursor by.

    East Asian Width `W` and `F` are two cells; everything else is one. This
    deliberately knows nothing about variation selectors -- see below.
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


@pytest.mark.parametrize("glyph", COLUMN_GLYPHS, ids=COLUMN_GLYPHS)
def test_every_column_glyph_is_wide_by_unicode_not_by_opinion(glyph):
    """Rich's measurement is not the terminal's, and the two must not differ.

    `⚠️` and `⏸️` were a text-presentation character (East Asian Width N)
    followed by U+FE0F, the emoji variation selector. That sequence has no
    agreed width: rich 15 counts two cells and pads for two, while a terminal
    following wcwidth advances the cursor by one and draws the glyph over the
    padding. The space after the icon vanished and every column to its right
    stepped one cell left -- on exactly the warning and paused rows, so the
    ragged lines were the ones a reader was meant to look at.

    Two constructions are safe: a single code point of width `W`, or a
    one-cell character padded to two inside the value (`"⚠ "`). Both add up
    to two by the Unicode tables alone, with nothing to disagree about -- so
    the first assertion rejects every character that has no cell of its own:
    the whole variation-selector range (U+FE00..U+FE0F and the supplementary
    block, all category Mn) and the zero-width joiner that builds emoji
    sequences (U+200D, category Cf), not just the one selector that bit.
    The second rejects East Asian Width `A`: ambiguous characters are one
    cell in most terminals and two in a CJK locale that treats ambiguous
    width as wide, which is the same disagreement by another route.
    `cell_len` above still guards the layout rich produces; this test guards
    that the terminal will agree with it.
    """
    invisible = [c for c in glyph if unicodedata.category(c) in ("Mn", "Me", "Cf")]
    assert not invisible, (
        f"{glyph!r} carries {[f'U+{ord(c):04X}' for c in invisible]}: a mark or format "
        f"character has no cell of its own, and a variation selector or zero-width "
        f"joiner makes the width ambiguous between rich and the terminal. Use a wide "
        f"code point or pad a narrow one."
    )
    ambiguous = [c for c in glyph if unicodedata.east_asian_width(c) == "A"]
    assert not ambiguous, (
        f"{glyph!r} carries {[f'U+{ord(c):04X}' for c in ambiguous]} of East Asian Width "
        f"'A': one cell in most terminals, two in a CJK locale that treats ambiguous "
        f"width as wide. Use a code point of width W, N or Na."
    )
    assert _cells_by_unicode(glyph) == 2, (
        f"{glyph!r} is {_cells_by_unicode(glyph)} cells by East Asian Width; "
        f"a status column needs 2 in every terminal, not only in rich."
    )


def test_the_separator_dot_is_not_the_unknown_glyph():
    """`·` still appears in the panel, and it means something else.

    The follow-mode status line and the Swarm summary join their parts with
    ` · `. That is punctuation, not vocabulary, and a reader who greps for the
    old marker should not be led back to it.
    """
    assert icons.UNKNOWN != "·"
