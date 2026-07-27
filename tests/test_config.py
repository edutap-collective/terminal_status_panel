from terminal_status_panel.config import load_config


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
