from terminal_status_panel.config import DEFAULT_HEALTH_KINDS, load_config


def _write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return str(path)


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.width == 80
    assert cfg.docker_timeout == 1.5
    assert cfg.critical_services == []
    assert cfg.thresholds.memory_critical == 90.0
    assert cfg.thresholds.load_warning == 0.8


def test_toml_overrides_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "width = 100",
                "[docker]",
                "timeout = 3.0",
                "[services]",
                'critical = ["postgres", "kafka"]',
                "[thresholds.memory]",
                "warning = 60",
                "critical = 85",
            ]
        )
    )
    cfg = load_config(path)
    assert cfg.width == 100
    assert cfg.docker_timeout == 3.0
    assert cfg.critical_services == ["postgres", "kafka"]
    assert cfg.thresholds.memory_warning == 60.0
    assert cfg.thresholds.memory_critical == 85.0
    # untouched thresholds keep defaults
    assert cfg.thresholds.filesystem_critical == 90.0


def test_infra_ui_services_default(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert "kafbat-ui" in cfg.infra_ui_services
    assert "cloudbeaver" in cfg.infra_ui_services
    assert "mongo-express" in cfg.infra_ui_services


def test_infra_ui_services_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[docker]\ninfra_ui_services = ["cloudbeaver", "my-own-ui"]\n')
    cfg = load_config(path)
    assert cfg.infra_ui_services == ["cloudbeaver", "my-own-ui"]
    # An unrelated option keeps its default.
    assert cfg.docker_timeout == 1.5


def test_health_defaults_without_a_config_file():
    health = load_config("/nonexistent/config.toml").health
    assert health.budget == 5.0
    assert health.enabled == list(DEFAULT_HEALTH_KINDS)
    assert health.timeouts["kafka"] == 4.0
    assert health.timeouts["postgres"] == 1.5
    assert health.dns_expect == []


def test_health_budget_and_timeouts_are_overridable(tmp_path):
    path = _write(
        tmp_path,
        """
[health]
budget = 8.5

[health.timeout]
kafka = 6.0
""",
    )
    health = load_config(path).health
    assert health.budget == 8.5
    assert health.timeouts["kafka"] == 6.0
    # untouched keys keep their defaults
    assert health.timeouts["postgres"] == 1.5


def test_enabled_kinds_can_be_narrowed(tmp_path):
    path = _write(
        tmp_path,
        """
[health]
enabled = ["postgres", "glusterfs"]
""",
    )
    assert load_config(path).health.enabled == ["postgres", "glusterfs"]


def test_dns_expectations_are_parsed(tmp_path):
    path = _write(
        tmp_path,
        """
[[health.dns.expect]]
name = "login.example.net"
addresses = ["10.9.9.9"]

[[health.dns.expect]]
name = "www.example.net"
""",
    )
    expectations = load_config(path).health.dns_expect
    assert [e.name for e in expectations] == ["login.example.net", "www.example.net"]
    assert expectations[0].addresses == ["10.9.9.9"]
    assert expectations[1].addresses == []


def test_a_broken_health_block_falls_back_to_defaults_instead_of_raising(tmp_path):
    path = _write(
        tmp_path,
        """
[health]
budget = "not a number"
""",
    )
    assert load_config(path).health.budget == 5.0


def test_traefik_api_is_off_by_default():
    cfg = load_config("/nonexistent/config.toml")
    assert cfg.traefik.url is None
    assert cfg.traefik.cert is None


def test_traefik_api_can_be_configured(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[traefik]\n"
        'url = "https://localhost:8082/traefik/api/rawdata"\n'
        'cert = "/etc/ssl/panel.pem"\n'
        'key = "/etc/ssl/panel.key"\n'
        'ca = "/etc/ssl/webfe-ca.pem"\n'
    )
    cfg = load_config(str(path))
    assert cfg.traefik.url.endswith("/rawdata")
    assert cfg.traefik.cert == "/etc/ssl/panel.pem"


def test_ignore_mountpoints_defaults_to_the_platform_list(monkeypatch, tmp_path):
    from terminal_status_panel import platform_defaults

    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.ignore_mountpoints == [
        "/System/Volumes/",
        "/Library/Developer/CoreSimulator/",
    ]
    assert cfg.thresholds.swap_warning == 80.0


def test_ignore_mountpoints_from_the_resources_block(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[resources]\nignore_mountpoints = ["/mnt/backup/", "/srv/scratch/"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.ignore_mountpoints == ["/mnt/backup/", "/srv/scratch/"]


def test_an_explicitly_empty_ignore_list_hides_nothing(monkeypatch, tmp_path):
    """Presence, not truthiness: [] means "hide nothing", not "use defaults"."""
    from terminal_status_panel import platform_defaults

    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    path = tmp_path / "config.toml"
    path.write_text("[resources]\nignore_mountpoints = []\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.ignore_mountpoints == []


# --- a bare scalar in a list-shaped key must not be split into characters ---


def test_ignore_mountpoints_scalar_string_becomes_a_one_element_list(tmp_path):
    """A bare string is valid TOML for this key and a plausible authoring
    mistake. Splitting it with list("/System/Volumes/") used to yield
    ['/', 'S', 'y', ...], and the lone "/" element prefix-matches every
    mountpoint -- blanking the whole filesystem table with no error."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[resources]\nignore_mountpoints = "/System/Volumes/"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.ignore_mountpoints == ["/System/Volumes/"]
    # The regression this guards against: a lone "/" hides every mountpoint.
    assert "/" not in cfg.ignore_mountpoints


def test_ignore_mountpoints_wrong_type_falls_back_to_the_platform_default(monkeypatch, tmp_path):
    """Neither a list nor a string -- e.g. a stray number -- is not the typo
    this helper forgives; it falls back to the default instead of raising."""
    from terminal_status_panel import platform_defaults

    monkeypatch.setattr(platform_defaults.platform, "system", lambda: "Darwin")
    path = tmp_path / "config.toml"
    path.write_text("[resources]\nignore_mountpoints = 42\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.ignore_mountpoints == [
        "/System/Volumes/",
        "/Library/Developer/CoreSimulator/",
    ]


def test_critical_services_scalar_string_becomes_a_one_element_list(tmp_path):
    """The same list(...) pitfall applies to critical_services, just with a
    milder failure: it silently drops every character of the one intended
    service name into its own (bogus) critical-service entry."""
    path = tmp_path / "config.toml"
    path.write_text('[services]\ncritical = "postgres"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.critical_services == ["postgres"]


def test_the_process_sample_window_defaults_to_three_tenths(tmp_path):
    assert load_config(tmp_path / "missing.toml").process_sample == 0.3


def test_the_process_sample_window_is_configurable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[resources]\nprocess_sample = 1.0\n")
    assert load_config(path).process_sample == 1.0


def test_a_malformed_process_sample_falls_back(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[resources]\nprocess_sample = "soon"\n')
    assert load_config(path).process_sample == 0.3


def test_the_row_count_defaults_to_five(tmp_path):
    assert load_config(tmp_path / "missing.toml").top_processes == 5


def test_the_row_count_is_configurable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[resources]\ntop_processes = 12\n")
    assert load_config(path).top_processes == 12


def test_a_malformed_row_count_falls_back(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[resources]\ntop_processes = "lots"\n')
    assert load_config(path).top_processes == 5


def test_a_negative_row_count_is_zero(tmp_path):
    """Below none is none. Treating it as a fallback to five would turn a
    clumsy way of saying "off" into the opposite."""
    path = tmp_path / "c.toml"
    path.write_text("[resources]\ntop_processes = -3\n")
    assert load_config(path).top_processes == 0


def test_the_follow_intervals_have_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.follow_interval == 5.0
    assert cfg.follow_health_interval == 20.0


def test_the_follow_intervals_are_configurable(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[follow]\ninterval = 2.5\nhealth_interval = 45\n")
    cfg = load_config(path)
    assert cfg.follow_interval == 2.5
    assert cfg.follow_health_interval == 45.0


def test_malformed_follow_intervals_fall_back(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[follow]\ninterval = "soon"\nhealth_interval = []\n')
    cfg = load_config(path)
    assert cfg.follow_interval == 5.0
    assert cfg.follow_health_interval == 20.0


def test_no_link_table_yields_no_links(tmp_path):
    assert load_config(tmp_path / "missing.toml").traefik.links == {}


def test_the_link_table_is_read(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        "[traefik.links]\n"
        'login_example_de = "https://login.example.de"\n'
        'portal_dept_uni_example_de = "https://portal.dept.uni-example.de"\n'
    )
    links = load_config(path).traefik.links
    assert links["login_example_de"] == "https://login.example.de"
    assert links["portal_dept_uni_example_de"] == "https://portal.dept.uni-example.de"


def test_a_trailing_slash_is_removed_from_a_base(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[traefik.links]\nlogin_example_de = "https://login.example.de/"\n')
    assert load_config(path).traefik.links["login_example_de"] == "https://login.example.de"


def test_a_value_that_is_not_an_http_url_is_dropped(tmp_path):
    """Dropped rather than rejected: this file must never fail a login, and an
    entrypoint with no usable base simply gets no links."""
    path = tmp_path / "c.toml"
    path.write_text(
        "[traefik.links]\n"
        'a = "login.example.de"\n'
        'b = "ftp://login.example.de"\n'
        "c = 42\n"
        'd = "https://ok.example.de"\n'
    )
    assert load_config(path).traefik.links == {"d": "https://ok.example.de"}


def test_a_malformed_link_table_leaves_the_rest_of_the_config_intact(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[traefik]\nlinks = "not a table"\nurl = "https://api.example.de"\n')
    cfg = load_config(path)
    assert cfg.traefik.links == {}
    assert cfg.traefik.url == "https://api.example.de"


def test_a_base_with_no_host_is_dropped(tmp_path):
    """A scheme with nothing to reach is not a base anything can be joined
    onto -- rendering it would produce a bare `http://something` fragment,
    not a URL."""
    path = tmp_path / "c.toml"
    path.write_text(
        '[traefik.links]\nbare_scheme = "http://:8080"\nok = "https://login.example.de"\n'
    )
    assert load_config(path).traefik.links == {"ok": "https://login.example.de"}


def test_a_base_carrying_a_query_or_fragment_is_dropped(tmp_path):
    """`link_for` only ever appends a path, so a query or fragment on the base
    would land inside the joined URL instead of where it was written:
    `https://x.de?q=1` + `/a` -> `https://x.de?q=1/a`."""
    path = tmp_path / "c.toml"
    path.write_text(
        "[traefik.links]\n"
        'query = "https://x.example.de?q=1"\n'
        'fragment = "https://x.example.de#top"\n'
        'ok = "https://login.example.de"\n'
    )
    assert load_config(path).traefik.links == {"ok": "https://login.example.de"}
