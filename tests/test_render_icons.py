import unicodedata

import pytest
from rich.cells import cell_len

from terminal_status_panel.render import health, icons, panels


def test_every_status_glyph_is_defined_once():
    assert (icons.OK, icons.WARN, icons.DEAD) == ("✅", "⚠️ ", "💀")
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


#: The status glyphs that are a single wide code point. `…` and `✗` are
#: excluded: they are used inline in a sentence, never as a column of their
#: own. `WARN` is excluded because it is deliberately not one code point --
#: see `test_the_warning_sign_is_the_emoji_with_a_pad_cell`.
WIDE_GLYPHS = [icons.OK, icons.DEAD, icons.JOB, icons.UNKNOWN, icons.PAUSED]


@pytest.mark.parametrize("glyph", WIDE_GLYPHS, ids=WIDE_GLYPHS)
def test_every_wide_glyph_occupies_two_cells(glyph):
    """A column mixing widths steps left and right down the block.

    This is not a style preference. Rich lays a column out by cell width, so a
    one-cell glyph beside a two-cell one shifts every following column on that
    row. `UNKNOWN` was `·` -- one cell against `✅`'s two -- through 0.9, and
    every cluster member list was ragged because of it.
    """
    assert cell_len(glyph) == 2, (
        f"{glyph!r} is {cell_len(glyph)} cells wide; a status column needs 2. "
        f"Pick a glyph with East Asian Wide width."
    )


def _cells_by_unicode(text: str) -> int:
    """The width a terminal following the Unicode tables advances the cursor by.

    East Asian Width `W` and `F` are two cells; everything else is one.
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


@pytest.mark.parametrize("glyph", WIDE_GLYPHS, ids=WIDE_GLYPHS)
def test_every_wide_glyph_is_wide_by_unicode_not_by_opinion(glyph):
    """Rich's measurement is not the terminal's, and the two must not differ.

    A single code point of East Asian Width `W` is two cells by the Unicode
    tables alone, so there is nothing for rich and a terminal to disagree
    about. The assertions reject what would reopen that disagreement: marks
    and format characters, which have no cell of their own (variation
    selectors, the zero-width joiner), and East Asian Width `A`, which is one
    cell in most terminals and two in a CJK locale that treats ambiguous
    width as wide. `cell_len` above guards the layout rich produces; this
    test guards that the terminal will agree with it.
    """
    invisible = [c for c in glyph if unicodedata.category(c) in ("Mn", "Me", "Cf")]
    assert not invisible, (
        f"{glyph!r} carries {[f'U+{ord(c):04X}' for c in invisible]}: a mark or format "
        f"character has no cell of its own. Use a wide code point."
    )
    ambiguous = [c for c in glyph if unicodedata.east_asian_width(c) == "A"]
    assert not ambiguous, (
        f"{glyph!r} carries {[f'U+{ord(c):04X}' for c in ambiguous]} of East Asian Width "
        f"'A': one cell in most terminals, two in a CJK locale that treats ambiguous "
        f"width as wide. Use a code point of width W or F."
    )
    assert _cells_by_unicode(glyph) == 2, (
        f"{glyph!r} is {_cells_by_unicode(glyph)} cells by East Asian Width; "
        f"a status column needs 2 in every terminal, not only in rich."
    )


def test_the_warning_sign_is_the_emoji_with_a_pad_cell():
    """`WARN` is the one glyph built from a sequence, and the pad is the point.

    There is no warning triangle with default emoji presentation in Unicode:
    U+26A0 is a text character, and only the variation selector U+FE0F turns
    it into the emoji. rich 15 counts that sequence as two cells, and so do
    the terminals this panel is read in -- measured 2026-09-10 in iTerm2 and
    in VS Code's terminal, both advancing the cursor by two. What VS Code's
    terminal also does is draw the glyph wider than its two cells, painting
    over the space that follows it, so `⚠️ 2/5` read as `⚠️2/5`. The pad cell
    inside the value absorbs that overdraw; iTerm2 shows it as a second
    space, which is the accepted price.

    0.12.1 replaced the emoji with the bare text sign on a wrong diagnosis
    (a cursor-advance mismatch that the measurement did not confirm) and got
    a small monochrome triangle for it. The shape is the message, and the
    emoji carries it at full size.
    """
    assert icons.WARN == "\u26a0\ufe0f ", "warning sign, emoji presentation, one pad cell"
    assert cell_len(icons.WARN) == 3, "two cells for the emoji as rich counts it, one pad"
    assert icons.WARN[-1] == " ", "the pad is a plain space, so it takes the row's style"


def test_the_separator_dot_is_not_the_unknown_glyph():
    """`·` still appears in the panel, and it means something else.

    The follow-mode status line and the Swarm summary join their parts with
    ` · `. That is punctuation, not vocabulary, and a reader who greps for the
    old marker should not be led back to it.
    """
    assert icons.UNKNOWN != "·"
