"""Tests for the duplicate-definition guard in ``tools/check_redefinitions.py``.

The tool is not part of the package, so it is loaded from its path rather than
imported. It runs in CI, and a guard nobody tests is a guard that quietly stops
guarding.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "check_redefinitions.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_redefinitions", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_tool()


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_a_name_defined_twice_in_one_module_is_reported(tmp_path):
    path = _write(
        tmp_path,
        "def collect():\n    return 1\n\n\ndef collect():\n    return 2\n",
    )
    problems = checker.find_redefinitions(path)
    assert len(problems) == 1
    assert "`collect`" in problems[0]
    assert "line 5" in problems[0]


def test_a_name_used_between_its_two_definitions_is_still_reported(tmp_path):
    """The exact shape ruff's F811 misses, and the reason this tool exists.

    F811 fires only while the first binding is unused. Call the name in
    between -- as ``collectors/docker.py`` did -- and the rule stays silent
    while the first definition is just as dead.
    """
    path = _write(
        tmp_path,
        "def helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return helper()\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n",
    )
    problems = checker.find_redefinitions(path)
    assert len(problems) == 1
    assert "`helper`" in problems[0]


def test_a_module_with_distinct_names_is_clean(tmp_path):
    path = _write(
        tmp_path,
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n\n\nclass Three:\n    pass\n",
    )
    assert checker.find_redefinitions(path) == []


def test_overloaded_signatures_are_not_a_redefinition(tmp_path):
    path = _write(
        tmp_path,
        "from typing import overload\n"
        "\n"
        "\n"
        "@overload\n"
        "def read(x: int) -> int: ...\n"
        "@overload\n"
        "def read(x: str) -> str: ...\n"
        "def read(x):\n"
        "    return x\n",
    )
    assert checker.find_redefinitions(path) == []


def test_a_property_and_its_setter_are_not_a_redefinition(tmp_path):
    path = _write(
        tmp_path,
        "class Panel:\n"
        "    @property\n"
        "    def width(self):\n"
        "        return self._width\n"
        "\n"
        "    @width.setter\n"
        "    def width(self, value):\n"
        "        self._width = value\n",
    )
    assert checker.find_redefinitions(path) == []


def test_a_method_defined_twice_in_one_class_is_reported(tmp_path):
    path = _write(
        tmp_path,
        "class Panel:\n"
        "    def render(self):\n"
        "        return 1\n"
        "\n"
        "    def render(self):\n"
        "        return 2\n",
    )
    problems = checker.find_redefinitions(path)
    assert len(problems) == 1
    assert "class Panel" in problems[0]


def test_the_same_name_in_two_branches_is_left_alone(tmp_path):
    """A platform fallback binds one name once per branch, and that is correct."""
    path = _write(
        tmp_path,
        "import sys\n"
        "\n"
        "if sys.platform == 'darwin':\n"
        "\n"
        "    def mountpoints():\n"
        "        return ['/']\n"
        "\n"
        "else:\n"
        "\n"
        "    def mountpoints():\n"
        "        return ['/', '/boot']\n",
    )
    assert checker.find_redefinitions(path) == []


def test_the_same_name_in_two_different_classes_is_left_alone(tmp_path):
    path = _write(
        tmp_path,
        "class A:\n    def render(self):\n        return 1\n"
        "\n"
        "\nclass B:\n    def render(self):\n        return 2\n",
    )
    assert checker.find_redefinitions(path) == []


def test_an_unparseable_file_is_reported_rather_than_skipped(tmp_path):
    path = _write(tmp_path, "def broken(:\n")
    problems = checker.find_redefinitions(path)
    assert len(problems) == 1
    assert "cannot parse" in problems[0]


def test_the_exit_code_reports_findings(tmp_path, capsys):
    _write(tmp_path, "def once():\n    pass\n\n\ndef once():\n    pass\n")
    assert checker.main([str(tmp_path)]) == 1
    assert "`once`" in capsys.readouterr().err


def test_a_missing_path_is_an_error_not_a_pass(tmp_path):
    assert checker.main([str(tmp_path / "nowhere")]) == 2


def test_this_repository_defines_nothing_twice():
    """The regression guard for the duplicated block in ``collectors/docker.py``.

    Five functions stood there twice, identical, through a release. This is the
    assertion that would have gone red.
    """
    problems: list[str] = []
    for root in ("src", "tests", "tools"):
        for file in sorted((REPO / root).rglob("*.py")):
            problems.extend(checker.find_redefinitions(file))
    assert problems == []


@pytest.mark.parametrize("root", ["src", "tests", "tools"])
def test_every_scanned_root_exists(root):
    """The default roots are a hardcoded tuple; a rename must not silence it."""
    assert (REPO / root).is_dir()
    assert root in checker.DEFAULT_ROOTS
