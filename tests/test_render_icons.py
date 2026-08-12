from terminal_status_panel.render import health, icons, panels


def test_every_status_glyph_is_defined_once():
    assert (icons.OK, icons.WARN, icons.DEAD) == ("✅", "⚠️", "💀")
    assert (icons.UNKNOWN, icons.TRUNCATED, icons.FAILED) == ("·", "…", "✗")


def test_both_renderers_use_the_shared_glyphs():
    """A second copy is how the container-name patterns diverged once already."""
    assert (panels._OK, panels._WARN, panels._DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.OK, health.WARN, health.DEAD) == (icons.OK, icons.WARN, icons.DEAD)
    assert (health.UNKNOWN, health.TRUNCATED, health.FAILED) == (
        icons.UNKNOWN,
        icons.TRUNCATED,
        icons.FAILED,
    )
