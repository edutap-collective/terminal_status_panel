"""Turn a Traefik rule and a configured base URL into a clickable address.

The base cannot be derived from anything Traefik knows. Its routers match on
path alone -- the reference cluster has no ``Host()`` rule at all -- so the
public hostname appears nowhere in the routing configuration. The entrypoint's
own name looks like a hostname with dots replaced by underscores, and is not:
in a name like ``portal_dept_uni_example_de`` one underscore stands for a dot
and the next for a hyphen, and nothing in the name says which. A link that
goes somewhere plausible but wrong is worse than no link, because the reader
cannot tell until they click it. The base therefore comes from configuration
and from nowhere else.
"""

from __future__ import annotations

import re

#: Traefik's three path matchers, with their backtick-quoted argument.
#: ``PathPrefix`` and ``PathRegexp`` are tried before ``Path`` because ``Path``
#: is a prefix of both: read the other way round, every ``PathPrefix(`` parses
#: as a ``Path`` matcher whose argument begins ``Prefix(``, and a rule naming
#: one path starts to look like a rule naming two.
_MATCHER = re.compile(r"(PathPrefix|PathRegexp|Path)\(`([^`]*)`\)")

#: Backtick-quoted argument, for detecting negations in grammar (not path content).
_ARGUMENT = re.compile(r"`[^`]*`")

#: Where a regular expression stops being a literal path. Everything from the
#: first of these onwards is a pattern, and guessing what it matches is exactly
#: the guessing this module exists to avoid.
_REGEX_META = frozenset("([.*+?{|\\$")

#: Tails -- everything from the first metacharacter onward -- that leave the
#: literal head in front of them a genuine prefix. `PathRegexp(`^/foo\.bar$`)`
#: reads as the literal head `/foo` followed by the tail `\.bar$`: that tail is
#: not here, because it narrows the match to a different, longer address, and
#: `/foo` is not a sub-case of `/foo.bar` -- it is a different address the
#: router does not serve. Only a tail that adds nothing but more of the same
#: path -- the end of the string, or an optional `/...` continuation -- keeps
#: the head a prefix. Each shape is listed both anchored (a trailing `$`, the
#: common case) and unanchored, because the grammar does not require the
#: anchor even though most rules carry one.
_OPTIONAL_TAILS = frozenset({
    "$",                      # nothing follows the head at all
    "(?:/.*)?$", "(?:/.*)?",  # an optional, non-capturing `/...` continuation
    "(/.*)?$", "(/.*)?",      # the same, capturing
    "/?$", "/?",              # an optional trailing slash
    ".*$", ".*",              # anything may follow -- still a prefix
})


def path_from_rule(rule: str | None) -> str | None:
    """The single path this rule matches, or ``None``.

    Deliberately not a rule parser. Traefik's grammar has ``||``, ``&&`` and
    negation, and a rule using them has no single path to link to -- so
    counting matchers answers the question without understanding the grammar,
    and answers it the same way for every case this cannot handle.
    """
    if not rule:
        return None

    # Strip backtick-quoted arguments, which are content not grammar. A negation
    # in the grammar (outside backticks) means the one matcher names a path the
    # router does *not* serve, so the honest answer is None.
    grammar = _ARGUMENT.sub("", rule)
    if "!" in grammar:
        return None

    matches = _MATCHER.findall(rule)
    if len(matches) != 1:
        return None
    kind, value = matches[0]
    if kind != "PathRegexp":
        return value or None
    literal = value.removeprefix("^")
    for index, char in enumerate(literal):
        if char in _REGEX_META:
            head = literal[:index]
            tail = literal[index:]
            if not head or tail not in _OPTIONAL_TAILS:
                return None
            return head
    return literal or None


def link_for(base: str | None, path: str | None) -> str | None:
    """*base* joined with *path*, or ``None`` when there is no base.

    Returns the base alone when *path* is ``None`` -- the host is known even
    where the sub-path is not, which is why an entrypoint head can be clickable
    while the router below it is not.
    """
    if not base:
        return None
    root = base.rstrip("/")
    if not root:
        return None
    if not path:
        return root
    return f"{root}/{path.lstrip('/')}"
