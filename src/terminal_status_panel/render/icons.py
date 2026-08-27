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

Note that ``·`` still appears elsewhere in the panel as a *separator* -- in the
follow-mode status line and the Swarm summary. That is a different use of the
same character and has nothing to do with this vocabulary.
"""

OK = "✅"  # measured healthy
WARN = "⚠️"  # degraded, but serving
DEAD = "💀"  # measured broken
UNKNOWN = "⬜"  # not observable
JOB = "⏰"  # a scheduled job, resting between runs — measured healthy
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"  # the check itself failed
