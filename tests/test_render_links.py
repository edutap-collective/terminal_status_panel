from terminal_status_panel.render.links import link_for, path_from_rule


def test_a_path_prefix_yields_its_prefix():
    assert path_from_rule("PathPrefix(`/account`)") == "/account"


def test_an_exact_path_yields_itself():
    assert path_from_rule("Path(`/health`)") == "/health"


def test_a_regexp_yields_its_literal_head():
    """Everything up to the first metacharacter is a path; the rest is pattern."""
    assert path_from_rule("PathRegexp(`^/portal/app(?:/.*)?$`)") == "/portal/app"


def test_a_regexp_that_starts_with_a_pattern_yields_nothing():
    assert path_from_rule("PathRegexp(`^(?:/a|/b)$`)") is None


def test_two_paths_in_one_rule_yield_nothing():
    """One link cannot represent two, so the honest answer is none."""
    assert path_from_rule("PathPrefix(`/a`) || PathPrefix(`/b`)") is None


def test_a_rule_with_no_path_yields_nothing():
    assert path_from_rule("Headers(`X-Test`, `yes`)") is None


def test_no_rule_yields_nothing():
    assert path_from_rule(None) is None
    assert path_from_rule("") is None


def test_a_path_prefix_is_not_read_as_a_path_matcher():
    """`Path` is a prefix of `PathPrefix`; a careless alternation reads every
    PathPrefix( as a Path matcher whose argument happens to start with
    `Prefix(`, and then a single-path rule looks like two."""
    assert path_from_rule("PathPrefix(`/portal`)") == "/portal"
    assert path_from_rule("PathRegexp(`^/portal`)") == "/portal"


def test_a_base_and_a_path_join_into_a_url():
    assert link_for("https://login.example.de", "/account") == \
        "https://login.example.de/account"


def test_a_trailing_slash_on_the_base_changes_nothing():
    assert link_for("https://login.example.de/", "/account") == \
        link_for("https://login.example.de", "/account")


def test_no_path_yields_the_base_itself():
    assert link_for("https://login.example.de/", None) == "https://login.example.de"


def test_no_base_yields_no_link():
    assert link_for(None, "/account") is None
    assert link_for("", "/account") is None


def test_a_path_without_a_leading_slash_still_joins_cleanly():
    assert link_for("https://login.example.de", "account") == \
        "https://login.example.de/account"


def test_a_negated_rule_yields_nothing():
    """A negation names the one path the router does *not* serve.

    Counting matchers cannot see it -- the count is still one -- so this is
    the case where the count alone would hand a reader a link to precisely
    the address the rule excludes.
    """
    assert path_from_rule("!PathPrefix(`/health`)") is None


def test_a_negation_anywhere_in_the_rule_yields_nothing():
    assert path_from_rule("Method(`GET`) && !Path(`/metrics`)") is None


def test_an_exclamation_mark_inside_a_path_is_not_a_negation():
    """The grammar is outside the backticks; the argument is not grammar."""
    assert path_from_rule("PathPrefix(`/a!b`)") == "/a!b"
