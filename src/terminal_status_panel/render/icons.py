"""The panel's status vocabulary, in one place.

Every glyph means one thing and only one thing. ``…`` and ``✗`` are not
interchangeable: a check that ran out of budget says nothing about the service,
a check that failed says a great deal. ``⬜`` is reserved for what was not
observable at all.

Three rules hold for every entry. The tests pin the two that can be measured.

**One glyph, one meaning.** ``⬜`` and ``💤`` are the pair worth keeping
apart, and they were one glyph until 0.12. ``⬜`` is an absence of knowledge:
nobody asked, or the answer did not arrive. ``💤`` is knowledge -- a service
scaled to zero replicas was measured, and what was measured is that somebody
decided it should run nothing. Rendering that as unmeasured hid a decision
behind a shrug, and it made ``⬜`` unreadable by meaning too many things at
once. The same rule sorts the rest: a glyph in this vocabulary describes what
was *found*, never how the panel feels about it. A yellow line reporting a
measured finding takes ``⚠️`` even where nothing is broken yet.

**One width per glyph, in every terminal.** A column mixing widths steps
left and right down the block, which is what ``·`` -- a single cell against
``✅``'s two -- did to every cluster member list. So every glyph has one width
that rich and the terminal agree on: two cells for a single code point of
East Asian Width ``W`` (``✅`` ``💀`` ``⬜`` ``⏰`` ``💤``), and three for the
warning sign, because ``WARN`` is ``"⚠️ "`` -- emoji plus a pad cell inside
the value. The pad is there for a measured reason. VS Code's terminal draws
the emoji glyph wider than its two cells and paints over the space that
follows it, so ``⚠️ 2/5`` read as ``⚠️2/5`` there; the pad cell absorbs the
overdraw, and iTerm2, which draws the glyph within its cells, shows it as a
second space. Measured 2026-09-10 in both; both advance the cursor by two
cells for the sequence, exactly as rich 15 counts it.

0.12.1 got this wrong. It diagnosed a cursor-advance mismatch -- rich
counting two cells where a terminal advances one -- and replaced the emoji
with the bare text-presentation sign, which most fonts draw small and
monochrome. The columns had never been out; only the space had been painted
over. Keeping the emoji and adding the pad fixes what was actually broken.

**Shape, not colour.** A glyph must stay unambiguous for a reader who cannot
see colour, and without the colour of its row. ``✅`` and ``💀`` are a tick and
a skull before they are green and grey; ``⚠️`` is a triangle before it is
yellow. A coloured dot is not a glyph in this vocabulary -- ``🟡`` beside
``🟢`` is one shape twice. This is why the warning sign stayed the warning
sign rather than becoming an amber light: it is *the* warning shape.

Note that ``·`` still appears elsewhere in the panel as a *separator* -- in the
follow-mode status line and the Swarm summary. That is a different use of the
same character and has nothing to do with this vocabulary.
"""

OK = "✅"  # measured healthy
WARN = "⚠️ "  # degraded, but serving -- emoji plus its pad cell, see above
DEAD = "💀"  # measured broken
UNKNOWN = "⬜"  # not observable
JOB = "⏰"  # a scheduled job, resting between runs — measured healthy
PAUSED = "💤"  # measured, and deliberately running nothing
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"  # the check itself failed
