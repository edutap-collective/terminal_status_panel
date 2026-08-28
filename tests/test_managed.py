"""Tests for the MANAGED block and the version in the footer.

The block exists to make one fact impossible to miss at login: this machine is
configured by a tool, and a change made here by hand is gone at the next run.
That is a rule people know and forget, so the panel says it where they are
already looking.

It renders only where it is configured. A panel with no `[managed]` block is
unchanged, which is every installation that has not asked for this.
"""

from __future__ import annotations

import importlib.metadata

import pytest
from rich.console import Console

from terminal_status_panel.config import Config, ManagedConfig, load_config
from terminal_status_panel.model import PanelData, SystemInfo
from terminal_status_panel.render import panels
from terminal_status_panel.render.layout import build_layout


def _write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _text(renderable, width: int = 100) -> str:
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_without_a_managed_block_nothing_is_configured(tmp_path):
    path = _write(tmp_path, "width = 100\n")

    assert load_config(path).managed.by is None


def test_the_tool_name_is_read(tmp_path):
    path = _write(tmp_path, '[managed]\nby = "Ansible"\n')

    assert load_config(path).managed.by == "Ansible"


def test_the_repository_and_detail_are_read(tmp_path):
    path = _write(
        tmp_path,
        "[managed]\n"
        'by = "Ansible"\n'
        'repository = "https://gitlab.example.de/group/ansible-app-server"\n'
        'detail = "no local changes"\n',
    )

    managed = load_config(path).managed

    assert managed.repository == "https://gitlab.example.de/group/ansible-app-server"
    assert managed.detail == "no local changes"


@pytest.mark.parametrize(
    "url",
    [
        "gitlab.example.de/group/repo",  # no scheme
        "ftp://gitlab.example.de/group/repo",  # not a web address
        "https://",  # a scheme with nothing to reach
        "not a url at all",
    ],
    ids=["no-scheme", "wrong-scheme", "no-host", "nonsense"],
)
def test_an_unusable_repository_is_dropped_and_reported(tmp_path, url):
    """Same rule as `traefik.links`, and for the same reason.

    A link that goes somewhere plausible but wrong is worse than no link,
    because the reader cannot tell until they click it.
    """
    path = _write(tmp_path, f'[managed]\nby = "Ansible"\nrepository = "{url}"\n')

    cfg = load_config(path)

    assert cfg.managed.repository is None
    assert cfg.managed.by == "Ansible", "one bad value must not take the block down"
    assert any(p.key == "managed.repository" for p in cfg.problems)


def test_a_repository_without_a_tool_name_shows_nothing(tmp_path):
    """`by` is what turns the block on. A repository alone says nothing."""
    path = _write(tmp_path, '[managed]\nrepository = "https://gitlab.example.de/g/r"\n')

    cfg = load_config(path)

    assert cfg.managed.by is None
    assert panels.managed_panel(cfg.managed) is None


@pytest.mark.parametrize("key", ["by", "repository", "detail"])
def test_a_wrong_type_falls_back_and_is_reported(tmp_path, key):
    path = _write(tmp_path, f"[managed]\n{key} = 42\n")

    cfg = load_config(path)

    assert any(p.key == f"managed.{key}" for p in cfg.problems)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_an_unconfigured_panel_renders_no_managed_block():
    """`None`, not an empty group.

    A `Group` with no children is still a truthy object, so a caller writing
    `if managed_panel(...)` would stack a separator above nothing. The absence
    has to be representable.
    """
    assert panels.managed_panel(ManagedConfig()) is None


def test_the_block_names_the_tool_in_upper_case():
    out = _text(panels.managed_panel(ManagedConfig(by="Ansible")))

    assert "MANAGED" in out
    assert "ANSIBLE" in out


def test_the_repository_renders_as_its_last_path_segment():
    """A GitLab URL is some sixty characters and would break the column."""
    managed = ManagedConfig(
        by="Ansible", repository="https://gitlab.example.de/LMU-Dez-VI/Ref-VI.5/ansible-app-server"
    )

    out = _text(panels.managed_panel(managed))

    assert "ansible-app-server" in out
    assert "gitlab.example.de" not in out, "the full URL belongs in the link, not on screen"


def test_the_detail_line_is_rendered():
    out = _text(panels.managed_panel(ManagedConfig(by="Ansible", detail="no local changes")))

    assert "no local changes" in out


def test_the_block_appears_in_the_panel_under_updates():
    cfg = Config(managed=ManagedConfig(by="Ansible"))
    data = PanelData(system=SystemInfo(hostname="host"))

    out = _text(build_layout(data, cfg, sections=("server",)), width=120)

    assert "MANAGED" in out
    assert out.index("UPDATES") < out.index("MANAGED"), "it belongs under UPDATES, not above"


def test_a_panel_without_the_block_costs_no_extra_line():
    """Not merely "no heading" -- not one line taller either.

    The first version returned an empty `Group`, which is truthy, so the
    caller stacked a separator above nothing and every panel that had
    configured nothing grew by a line. Asserting only on the heading did not
    catch it.

    Measured where the right column is the taller one: with no system info the
    left column is two rows, so a stray blank line on the right shows up in
    the total. Beside a rendered OS logo it would not, which is exactly why
    the obvious version of this test passed.
    """
    data = PanelData()  # no system info: the left column is short

    plain = _text(build_layout(data, Config(), sections=("server",)), width=120)
    with_block = _text(
        build_layout(data, Config(managed=ManagedConfig(by="Ansible")), sections=("server",)),
        width=120,
    )

    assert "MANAGED" not in plain
    assert "MANAGED" in with_block
    assert len(with_block.splitlines()) > len(plain.splitlines())


def test_the_block_is_free_beside_the_os_logo():
    """On a real host it costs no height at all, which is why it can be there.

    The left column carries a pre-rendered OS logo and is the taller of the
    two; the MANAGED block fits in space the layout was already spending.
    """
    data = PanelData(system=SystemInfo(hostname="host"))

    plain = _text(build_layout(data, Config(), sections=("server",)), width=120)
    with_block = _text(
        build_layout(
            data,
            Config(managed=ManagedConfig(by="Ansible", detail="no local changes")),
            sections=("server",),
        ),
        width=120,
    )

    assert "MANAGED" in with_block
    assert len(with_block.splitlines()) == len(plain.splitlines())


# --------------------------------------------------------------------------- #
# The version in the footer
# --------------------------------------------------------------------------- #


def test_the_footer_carries_the_installed_version():
    data = PanelData(system=SystemInfo(hostname="host"))

    out = _text(build_layout(data, Config(), sections=("server",)), width=120)

    assert f"v{importlib.metadata.version('terminal-status-panel')}" in out


def test_the_footer_still_carries_the_timestamp():
    """The version is added beside it, not in place of it."""
    data = PanelData(system=SystemInfo(hostname="host"))

    assert "Last check:" in _text(build_layout(data, Config(), sections=("server",)), width=120)


def test_an_uninstalled_package_reports_dev_rather_than_a_made_up_number(monkeypatch):
    """Running from a checkout is a real case, and inventing a version is not ok."""

    def missing(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)
    from terminal_status_panel.render import layout

    assert layout.panel_version() == "dev"
