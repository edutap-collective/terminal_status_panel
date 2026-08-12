"""The panel's status vocabulary, in one place.

Every glyph means one thing and only one thing. ``…`` and ``✗`` are not
interchangeable: a check that ran out of budget says nothing about the service,
a check that failed says a great deal. ``·`` is reserved for what was not
observable at all.
"""

OK = "✅"        # measured healthy
WARN = "⚠️"      # degraded, but serving
DEAD = "💀"      # measured broken
UNKNOWN = "·"    # not observable
JOB = "⏰"       # a scheduled job, resting between runs — measured healthy
TRUNCATED = "…"  # ran out of budget
FAILED = "✗"     # the check itself failed
