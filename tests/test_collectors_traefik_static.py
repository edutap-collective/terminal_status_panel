"""Traefik's static configuration, as Traefik itself would read it."""

import pytest

from terminal_status_panel.collectors import traefik_static as static

YAML_DOC = """\
api:
  dashboard: true
entryPoints:
  http:
    address: ":80"
  https:
    address: ":443"
ping:
  entryPoint: http
providers:
  swarm:
    exposedByDefault: false
  file:
    directory: /etc/traefik/dynamic
    watch: true
"""

TOML_DOC = """\
[entryPoints.web]
address = ":8080"

[providers.file]
filename = "/etc/traefik/dynamic.toml"
"""


def test_a_yaml_document_yields_its_entrypoints_in_order():
    config = static.parse_static_document(YAML_DOC, "yaml")

    assert [(ep.name, ep.address, ep.port) for ep in config.entrypoints] == [
        ("http", ":80", 80),
        ("https", ":443", 443),
    ]


def test_a_yaml_document_yields_ping_and_the_provider_directory():
    config = static.parse_static_document(YAML_DOC, "yaml")

    assert config.ping_entrypoint == "http"
    assert config.file_provider == static.FileProvider(directory="/etc/traefik/dynamic")


def test_a_toml_document_is_read_the_same_way():
    config = static.parse_static_document(TOML_DOC, "toml")

    assert [ep.name for ep in config.entrypoints] == ["web"]
    assert config.file_provider.path == "/etc/traefik/dynamic.toml"


def test_keys_are_matched_case_insensitively_and_names_keep_their_case():
    doc = "ENTRYPOINTS:\n  WebSecure:\n    ADDRESS: ':443'\n"

    config = static.parse_static_document(doc, "yaml")

    assert [ep.name for ep in config.entrypoints] == ["WebSecure"]


def test_directory_wins_over_filename_as_in_traefik():
    doc = "providers:\n  file:\n    filename: /a.yml\n    directory: /d\n"

    assert static.parse_static_document(doc, "yaml").file_provider.path == "/d"


def test_an_invalid_document_raises_with_the_parser_s_reason():
    with pytest.raises(ValueError, match="TOMLDecodeError"):
        static.parse_static_document("this = = toml", "toml")


def test_a_document_of_the_wrong_shape_yields_nothing_rather_than_raising():
    config = static.parse_static_document("- just\n- a list\n", "yaml")

    assert config.entrypoints == []
    assert config.file_provider is None


def test_cli_flags_yield_entrypoints_ping_and_the_provider():
    config = static.parse_static_args(
        [
            "--entrypoints.https.address=:443",
            "--entryPoints.internal.address=:8090",
            "--ping.entryPoint=ping",
            "--providers.file.directory=/dynamic/",
        ]
    )

    assert [ep.name for ep in config.entrypoints] == ["https", "internal"]
    assert config.ping_entrypoint == "ping"
    assert config.file_provider == static.FileProvider(directory="/dynamic/")


def test_environment_variables_yield_lower_cased_entrypoints():
    config = static.parse_static_env(
        {
            "TRAEFIK_ENTRYPOINTS_WEB_ADDRESS": ":80",
            "TRAEFIK_PROVIDERS_FILE_FILENAME": "/dyn.yml",
            "PATH": "/usr/bin",
        }
    )

    assert [(ep.name, ep.port) for ep in config.entrypoints] == [("web", 80)]
    assert config.file_provider == static.FileProvider(filename="/dyn.yml")


def test_static_env_is_detected_only_by_the_traefik_prefix():
    assert static.has_static_env({"TRAEFIK_LOG_LEVEL": "INFO"}) is True
    assert static.has_static_env({"PATH": "/usr/bin"}) is False


@pytest.mark.parametrize("flag", ["--configFile", "--configfile"])
def test_the_config_file_flag_is_read_in_both_spellings_traefik_honours(flag):
    result = static.config_file_arg([f"{flag}=/etc/traefik/traefik.yaml"])
    assert result == "/etc/traefik/traefik.yaml"


def test_a_spelling_traefik_ignores_is_ignored_here_too():
    assert static.config_file_arg(["--ConfigFile=/etc/traefik/traefik.yaml"]) is None


def test_the_candidates_follow_traefik_s_order():
    paths = static.candidate_paths(
        ["--configFile=/cfg/t.yml"], {"HOME": "/root", "XDG_CONFIG_HOME": "/xdg"}, "/work"
    )

    assert paths == [
        "/cfg/t.yml",
        "/etc/traefik/traefik.toml",
        "/etc/traefik/traefik.yaml",
        "/etc/traefik/traefik.yml",
        "/xdg/traefik.toml",
        "/xdg/traefik.yaml",
        "/xdg/traefik.yml",
        "/root/.config/traefik.toml",
        "/root/.config/traefik.yaml",
        "/root/.config/traefik.yml",
        "/work/traefik.toml",
        "/work/traefik.yaml",
        "/work/traefik.yml",
    ]


def test_an_unset_variable_expands_to_nothing_as_os_expandenv_does():
    paths = static.candidate_paths([], {}, None)

    assert "/traefik.toml" in paths  # $XDG_CONFIG_HOME/traefik with XDG unset
    assert "/.config/traefik.yml" in paths  # $HOME/.config/traefik with HOME unset
    assert not any(path.startswith("./") for path in paths)  # no working dir declared


def test_flags_beside_the_config_file_are_counted():
    args = ["--configFile=/t.yml", "--entrypoints.x.address=:1", "--log.level=DEBUG"]

    assert static.ignored_flags(args) == 2
    assert static.ignored_flags(["--configFile=/t.yml"]) == 0


def test_a_relative_config_file_is_anchored_to_the_declared_working_directory():
    paths = static.candidate_paths(["--configFile=conf/traefik.yaml"], {}, "/etc/traefik")

    assert paths[0] == "/etc/traefik/conf/traefik.yaml"


def test_a_relative_config_file_stays_relative_without_a_working_directory():
    """Left for the caller to report: guessing a directory would invent a location."""
    assert static.candidate_paths(["--configFile=traefik.yaml"], {}, None)[0] == "traefik.yaml"
