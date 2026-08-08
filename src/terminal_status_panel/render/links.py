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

#: Where a regular expression stops being a literal path. Everything from the
#: first of these onwards is a pattern, and guessing what it matches is exactly
#: the guessing this module exists to avoid.
_REGEX_META = frozenset("([.*+?{|\\$")


def path_from_rule(rule: str | None) -> str | None:
    """The single path this rule matches, or ``None``.

    Deliberately not a rule parser. Traefik's grammar has ``||``, ``&&`` and
    negation, and a rule using them has no single path to link to -- so
    counting matchers answers the question without understanding the grammar,
    and answers it the same way for every case this cannot handle.
    """
    if not rule:
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
            return literal[:index] or None
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
