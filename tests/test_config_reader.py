"""Tests for the typed config readers and the problems they record.

Every value in the config file used to be converted in one of two ways: some
wrapped in `try`, some not. `thresholds.memory.warning = "soon"` raised
`ValueError` out of `load_config`, `main` swallowed it, and the login shell
printed nothing at all -- no panel, no reason. These tests pin the other
behaviour: a malformed value falls back, the panel renders, and the file says
what it could not use.
"""

from __future__ import annotations

import pytest

from terminal_status_panel.config import (
    DEFAULT_KAFKA_COMMAND_CONFIG,
    DEFAULT_MONGODB_MATCH,
    DEFAULT_POSTGRES_MATCH,
    DEFAULT_RUSTFS_MATCH,
    Config,
    KafkaProbe,
    PostgresProbe,
    RustfsProbe,
    load_config,
)


def _write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# The regression: a bad threshold used to blank the whole panel
# --------------------------------------------------------------------------- #


def test_an_unparseable_threshold_falls_back_instead_of_raising(tmp_path):
    path = _write(tmp_path, '[thresholds.memory]\nwarning = "soon"\n')

    cfg = load_config(path)

    assert cfg.thresholds.memory_warning == Config().thresholds.memory_warning
    assert any("thresholds.memory.warning" == p.key for p in cfg.problems)


def test_the_problem_names_what_was_found_and_what_was_used(tmp_path):
    path = _write(tmp_path, '[thresholds.memory]\nwarning = "soon"\n')

    problem = next(p for p in load_config(path).problems if p.key == "thresholds.memory.warning")

    assert "soon" in problem.found
    assert "75" in problem.used


def test_a_valid_file_records_no_problems(tmp_path):
    path = _write(tmp_path, "[thresholds.memory]\nwarning = 60\ncritical = 80\n")

    cfg = load_config(path)

    assert cfg.problems == []
    assert cfg.thresholds.memory_warning == 60.0


def test_a_missing_file_records_no_problems():
    """Absent is not malformed. The defaults are the documented behaviour."""
    assert load_config("/nonexistent/config.toml").problems == []


def test_a_broken_file_records_one_problem(tmp_path):
    path = _write(tmp_path, "this is not = = toml\n")

    cfg = load_config(path)

    assert len(cfg.problems) == 1
    assert cfg.problems[0].key == str(path)


# --------------------------------------------------------------------------- #
# bool: the surprising one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("written", ["false", "no", "off", "0"])
def test_a_string_that_reads_as_false_is_not_true(tmp_path, written):
    """`bool("false")` is `True`, which is the opposite of what was written.

    TOML has a real boolean and `show_image = false` is the correct way to
    write this. But quoting it is an easy slip, and silently turning it into
    the opposite of what it says is the worst possible reading.
    """
    path = _write(tmp_path, f'[docker]\nshow_image = "{written}"\n')

    cfg = load_config(path)

    assert cfg.show_image is False


@pytest.mark.parametrize("written", ["true", "yes", "on", "1"])
def test_a_string_that_reads_as_true_is_true(tmp_path, written):
    path = _write(tmp_path, f'[docker]\nshow_image = "{written}"\n')

    assert load_config(path).show_image is True


def test_a_real_toml_boolean_is_taken_as_written(tmp_path):
    path = _write(tmp_path, "[docker]\nshow_image = false\n")

    cfg = load_config(path)

    assert cfg.show_image is False
    assert cfg.problems == []


def test_a_string_that_means_nothing_falls_back_and_is_reported(tmp_path):
    path = _write(tmp_path, '[docker]\nshow_image = "maybe"\n')

    cfg = load_config(path)

    assert cfg.show_image is True  # the default
    assert any(p.key == "docker.show_image" for p in cfg.problems)


# --------------------------------------------------------------------------- #
# Ranges
# --------------------------------------------------------------------------- #


def test_a_negative_width_falls_back_rather_than_rendering_nothing(tmp_path):
    path = _write(tmp_path, "width = -10\n")

    cfg = load_config(path)

    assert cfg.width == Config().width
    assert any(p.key == "width" for p in cfg.problems)


def test_a_negative_top_processes_means_zero_rather_than_a_fallback(tmp_path):
    """Documented behaviour, and different from the width case above.

    README says a negative `top_processes` means 0. That is a clamp, not a
    misreading, so it is applied silently and records no problem.
    """
    path = _write(tmp_path, "[resources]\ntop_processes = -3\n")

    cfg = load_config(path)

    assert cfg.top_processes == 0
    assert cfg.problems == []


def test_a_zero_docker_timeout_falls_back(tmp_path):
    """Every Docker call would abort instantly; that cannot be what was meant."""
    path = _write(tmp_path, "[docker]\ntimeout = 0\n")

    cfg = load_config(path)

    assert cfg.docker_timeout == Config().docker_timeout
    assert any(p.key == "docker.timeout" for p in cfg.problems)


# --------------------------------------------------------------------------- #
# Every documented key survives a wrong type
# --------------------------------------------------------------------------- #


WRONG_TYPES = [
    ("width", 'width = "wide"'),
    ("docker.timeout", '[docker]\ntimeout = "soon"'),
    ("docker.df_timeout", '[docker]\ndf_timeout = "soon"'),
    ("docker.show_image", "[docker]\nshow_image = 12.5"),
    ("docker.description_label", "[docker]\ndescription_label = 7"),
    ("docker.group_label", "[docker]\ngroup_label = 7"),
    ("resources.process_sample", '[resources]\nprocess_sample = "a while"'),
    ("resources.top_processes", '[resources]\ntop_processes = "five"'),
    ("thresholds.memory.warning", '[thresholds.memory]\nwarning = "soon"'),
    ("thresholds.memory.critical", '[thresholds.memory]\ncritical = "soon"'),
    ("thresholds.swap.warning", '[thresholds.swap]\nwarning = "soon"'),
    ("thresholds.filesystem.warning", '[thresholds.filesystem]\nwarning = "soon"'),
    ("thresholds.filesystem.critical", '[thresholds.filesystem]\ncritical = "soon"'),
    ("thresholds.load.warning", '[thresholds.load]\nwarning = "soon"'),
    ("thresholds.load.critical", '[thresholds.load]\ncritical = "soon"'),
    ("follow.interval", '[follow]\ninterval = "often"'),
    ("follow.health_interval", '[follow]\nhealth_interval = "often"'),
    ("health.budget", '[health]\nbudget = "a while"'),
]


@pytest.mark.parametrize("key,body", WRONG_TYPES, ids=[key for key, _ in WRONG_TYPES])
def test_every_documented_key_survives_a_wrong_type(tmp_path, key, body):
    """No key may raise, and each must say which one it was."""
    path = _write(tmp_path, body + "\n")

    cfg = load_config(path)

    assert isinstance(cfg, Config)
    assert any(p.key == key for p in cfg.problems), (
        f"{key} fell back without recording why -- a silent fallback is what "
        f"this whole change exists to remove. Recorded: {[p.key for p in cfg.problems]}"
    )


@pytest.mark.parametrize("key,body", WRONG_TYPES, ids=[key for key, _ in WRONG_TYPES])
def test_a_wrong_type_leaves_every_other_value_at_its_default(tmp_path, key, body):
    """One bad key must not take the rest of the file down with it."""
    path = _write(tmp_path, body + "\n")

    cfg = load_config(path)
    defaults = Config()

    assert cfg.docker_timeout == defaults.docker_timeout or key == "docker.timeout"
    assert cfg.width == defaults.width or key == "width"
    assert cfg.health.budget == defaults.health.budget or key == "health.budget"


def test_several_bad_values_are_all_reported(tmp_path):
    path = _write(
        tmp_path,
        'width = "wide"\n[docker]\ntimeout = "soon"\n[thresholds.memory]\nwarning = "soon"\n',
    )

    keys = {p.key for p in load_config(path).problems}

    assert keys == {"width", "docker.timeout", "thresholds.memory.warning"}


def test_a_health_timeout_with_a_wrong_type_is_reported_by_its_own_name(tmp_path):
    path = _write(tmp_path, '[health.timeout]\nkafka = "slow"\n')

    cfg = load_config(path)

    assert any(p.key == "health.timeout.kafka" for p in cfg.problems)
    assert cfg.health.timeouts["kafka"] == Config().health.timeouts["kafka"]


# --------------------------------------------------------------------------- #
# [health.<kind>]: per-kind probe settings
# --------------------------------------------------------------------------- #


def test_without_kind_tables_every_probe_keeps_the_old_behaviour():
    """The acceptance bar: no new key, no change."""
    health = Config().health
    assert health.postgres == PostgresProbe(match=("_pg-",), mode="pg_auto_failover")
    assert health.mongodb.match == ("mongodb",)
    assert health.kafka == KafkaProbe(match=("kafka_kafka-",), command_config="/client.properties")
    assert health.rustfs == RustfsProbe(match=("rustfs_rustfs",), scheme="https")


def test_every_kind_table_is_read(tmp_path):
    path = _write(
        tmp_path,
        "[health.postgres]\n"
        'match = ["demo_postgres"]\n'
        'mode = "standalone"\n'
        "[health.mongodb]\n"
        'match = ["demo_mongo"]\n'
        "[health.kafka]\n"
        'match = ["demo_kafka"]\n'
        'command_config = ""\n'
        "[health.rustfs]\n"
        'match = ["demo_rustfs"]\n'
        'scheme = "http"\n',
    )

    cfg = load_config(path)

    assert cfg.problems == []
    assert cfg.health.postgres == PostgresProbe(match=("demo_postgres",), mode="standalone")
    assert cfg.health.mongodb.match == ("demo_mongo",)
    assert cfg.health.kafka == KafkaProbe(match=("demo_kafka",), command_config="")
    assert cfg.health.rustfs == RustfsProbe(match=("demo_rustfs",), scheme="http")


def test_match_replaces_the_default_rather_than_extending_it(tmp_path):
    path = _write(tmp_path, '[health.kafka]\nmatch = ["demo_kafka"]\n')

    assert load_config(path).health.kafka.match == ("demo_kafka",)


def test_a_bare_string_match_is_one_pattern(tmp_path):
    path = _write(tmp_path, '[health.rustfs]\nmatch = "demo_rustfs"\n')

    cfg = load_config(path)

    assert cfg.health.rustfs.match == ("demo_rustfs",)
    assert cfg.problems == []


def test_an_empty_match_list_means_no_member_here(tmp_path):
    path = _write(tmp_path, "[health.mongodb]\nmatch = []\n")

    cfg = load_config(path)

    assert cfg.health.mongodb.match == ()
    assert cfg.problems == []


def test_an_empty_string_pattern_is_dropped_and_reported(tmp_path):
    """The empty substring is in every name: it would hand the verdict to
    whichever container the daemon lists first."""
    path = _write(tmp_path, '[health.postgres]\nmatch = ["", "  ", "demo_postgres"]\n')

    cfg = load_config(path)

    assert cfg.health.postgres.match == ("demo_postgres",)
    assert [p.key for p in cfg.problems] == ["health.postgres.match", "health.postgres.match"]


def test_a_match_that_is_not_a_list_falls_back(tmp_path):
    path = _write(tmp_path, "[health.postgres]\nmatch = 5\n")

    cfg = load_config(path)

    assert cfg.health.postgres.match == DEFAULT_POSTGRES_MATCH
    assert any(p.key == "health.postgres.match" for p in cfg.problems)


def test_an_unknown_mode_falls_back_and_is_reported(tmp_path):
    path = _write(tmp_path, '[health.postgres]\nmode = "patroni"\n')

    cfg = load_config(path)

    assert cfg.health.postgres.mode == "pg_auto_failover"
    problem = next(p for p in cfg.problems if p.key == "health.postgres.mode")
    assert "standalone" in problem.reason


def test_an_unknown_scheme_falls_back_and_is_reported(tmp_path):
    path = _write(tmp_path, '[health.rustfs]\nscheme = "ftp"\n')

    cfg = load_config(path)

    assert cfg.health.rustfs.scheme == "https"
    assert any(p.key == "health.rustfs.scheme" for p in cfg.problems)


def test_a_non_string_command_config_falls_back(tmp_path):
    path = _write(tmp_path, "[health.kafka]\ncommand_config = false\n")

    cfg = load_config(path)

    assert cfg.health.kafka.command_config == DEFAULT_KAFKA_COMMAND_CONFIG
    assert any(p.key == "health.kafka.command_config" for p in cfg.problems)


def test_an_unknown_key_in_a_kind_table_is_reported(tmp_path):
    path = _write(tmp_path, '[health.postgres]\nmach = ["demo_postgres"]\n')

    cfg = load_config(path)

    assert cfg.health.postgres.match == DEFAULT_POSTGRES_MATCH
    assert any(p.key == "health.postgres.mach" and p.reason == "unknown key" for p in cfg.problems)


def test_a_misspelt_kind_table_is_reported(tmp_path):
    path = _write(tmp_path, '[health.postgress]\nmatch = ["demo_postgres"]\n')

    cfg = load_config(path)

    assert any(p.key == "health.postgress" and p.reason == "unknown table" for p in cfg.problems)


def test_the_known_health_subtables_are_not_reported(tmp_path):
    path = _write(
        tmp_path,
        "[health.timeout]\nkafka = 7.0\n"
        '[[health.dns.expect]]\nname = "login.example.net"\n'
        '[health.rustfs]\nscheme = "http"\n',
    )

    assert load_config(path).problems == []


def test_an_unknown_scalar_key_directly_under_health_is_reported(tmp_path):
    path = _write(tmp_path, "[health]\nprobe_nmae = true\n")

    cfg = load_config(path)

    assert any(p.key == "health.probe_nmae" and p.reason == "unknown key" for p in cfg.problems)


def test_the_known_health_scalar_keys_are_not_reported(tmp_path):
    path = _write(tmp_path, '[health]\nbudget = 5.0\nenabled = ["postgres"]\n')

    assert load_config(path).problems == []


def test_rustfs_and_mongodb_defaults_are_exported_unchanged():
    assert DEFAULT_RUSTFS_MATCH == ("rustfs_rustfs",)
    assert DEFAULT_MONGODB_MATCH == ("mongodb",)
