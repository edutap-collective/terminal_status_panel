"""The panel's status vocabulary, in one place.

Every glyph means one thing and only one thing. ``…`` and ``✗`` are not
interchangeable: a check that ran out of budget says nothing about the service,
a check that failed says a great deal. ``⬜`` is reserved for what was not
observable at all.

All of them occupy two terminal cells, and that is a requirement rather than a
coincidence. A column mixing a one-cell glyph with a two-cell one steps left
and right down the block, which is what ``·`` -- a single cell against ``✅``'s
two -- did to every cluster member list. An empty square also carries the
meaning better than a dot: it reads as a space left blank, not as a small
state of its own.

``⬜`` and ``⏸️`` are the pair worth keeping apart, and they were one glyph
until 0.12. ``⬜`` is an absence of knowledge: nobody asked, or the answer did
not arrive. ``⏸️`` is knowledge -- a service scaled to zero replicas was
measured, and what was measured is that somebody decided it should run
nothing. Rendering that as unmeasured hid a decision behind a shrug, and it
made ``⬜`` unreadable by meaning too many things at once.

The same rule sorts the rest: a glyph in this vocabulary describes what was
*found*, never how the panel feels about it. A yellow line reporting a
measured finding takes ``⚠️`` even where nothing is broken yet.

Note that ``·`` still appears elsewhere in the panel as a *separator* -- in the
follow-mode status line and the Swarm summary. That is a different use of the
same character and has nothing to do with this vocabulary.
"""

OK = "✅"  # measured healthy
WARN = "⚠️"  # degraded, but serving
DEAD = "💀"  # measured broken
UNKNOWN = "⬜"  # not observable
JOB = "⏰"  # a scheduled job, resting between runs — measured healthy
PAUSED = "⏸️"  # measured, and deliberately running nothing
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"  # the check itself failed
