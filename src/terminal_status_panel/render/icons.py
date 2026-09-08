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
measured finding takes ``⚠`` even where nothing is broken yet.

**Two cells, everywhere.** All of them occupy two terminal cells, and that is
a requirement rather than a coincidence. A column mixing a one-cell glyph with
a two-cell one steps left and right down the block, which is what ``·`` -- a
single cell against ``✅``'s two -- did to every cluster member list. Two
cells means two cells in *every* terminal, and only two constructions deliver
that: a single code point whose East Asian Width is ``W`` (``✅`` ``💀`` ``⬜``
``⏰`` ``💤``), or a one-cell character with the second cell padded into the
value itself -- ``WARN`` is ``"⚠ "``, warning sign plus space. What does not
deliver it is a text-presentation character followed by U+FE0F, the emoji
variation selector, which is what ``⚠️`` and ``⏸️`` were until 0.13. That
sequence has no agreed width: rich counts two cells and pads for two, while a
terminal following wcwidth advances the cursor by one and draws the glyph
over the padding. The space after the icon vanished, and every column to its
right stepped one cell left on exactly the rows a reader was meant to look at.

**Shape, not colour.** A glyph must stay unambiguous for a reader who cannot
see colour, and without the colour of its row. ``✅`` and ``💀`` are a tick and
a skull before they are green and grey; ``⚠`` is a triangle before it is
yellow. A coloured dot is not a glyph in this vocabulary -- ``🟡`` beside
``🟢`` is one shape twice. This is also why the warning sign stayed a bare
text-presentation character instead of being swapped for a wide emoji: it is
*the* warning shape, and it may well render small and monochrome. That is
fine; the shape is the message.

Note that ``·`` still appears elsewhere in the panel as a *separator* -- in the
follow-mode status line and the Swarm summary. That is a different use of the
same character and has nothing to do with this vocabulary.
"""

OK = "✅"  # measured healthy
WARN = "⚠ "  # degraded, but serving -- one cell plus its pad, see above
DEAD = "💀"  # measured broken
UNKNOWN = "⬜"  # not observable
JOB = "⏰"  # a scheduled job, resting between runs — measured healthy
PAUSED = "💤"  # measured, and deliberately running nothing
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"  # the check itself failed
