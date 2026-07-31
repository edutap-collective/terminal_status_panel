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
    path = _write(tmp_path, """
[health]
budget = 8.5

[health.timeout]
kafka = 6.0
""")
    health = load_config(path).health
    assert health.budget == 8.5
    assert health.timeouts["kafka"] == 6.0
    # untouched keys keep their defaults
    assert health.timeouts["postgres"] == 1.5


def test_enabled_kinds_can_be_narrowed(tmp_path):
    path = _write(tmp_path, """
[health]
enabled = ["postgres", "glusterfs"]
""")
    assert load_config(path).health.enabled == ["postgres", "glusterfs"]


def test_dns_expectations_are_parsed(tmp_path):
    path = _write(tmp_path, """
[[health.dns.expect]]
name = "login.lmu.de"
addresses = ["10.9.9.9"]

[[health.dns.expect]]
name = "www.portal.uni-muenchen.de"
""")
    expectations = load_config(path).health.dns_expect
    assert [e.name for e in expectations] == ["login.lmu.de", "www.portal.uni-muenchen.de"]
    assert expectations[0].addresses == ["10.9.9.9"]
    assert expectations[1].addresses == []


def test_a_broken_health_block_falls_back_to_defaults_instead_of_raising(tmp_path):
    path = _write(tmp_path, """
[health]
budget = "not a number"
""")
    assert load_config(path).health.budget == 5.0
