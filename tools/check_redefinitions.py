#!/usr/bin/env python3
"""Fail when a module or class defines the same name twice.

Ruff already carries a rule for this -- F811, redefined-while-unused -- and it
is enabled here via ``select = ["F"]``. It does not catch the case this script
exists for. F811 only fires while the *first* binding is still unused, so a
copy-pasted block whose names are called somewhere between the two copies
reads as legitimate to the rule and passes clean.

That is not hypothetical. Five functions in ``collectors/docker.py`` stood
twice, byte for byte identical, for three weeks: ``_container_groups`` and
friends were called at line 691 and redefined at 964, and ruff, ty, 883 tests
and a release all went green. The first 169 lines were dead -- an edit to them
would have changed nothing while looking in review exactly like an edit that
did.

Stdlib only and no import of the package under test: this has to run in the
FreeBSD CI job, which installs test dependencies by name, and it has to keep
working when the package itself does not.

Usage:
    python tools/check_redefinitions.py [path ...]      # default: src tests tools
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

DEFAULT_ROOTS = ("src", "tests", "tools")

Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _is_legitimate_redefinition(node: Definition) -> bool:
    """True for the decorators whose whole purpose is to bind a name twice.

    ``@overload`` declares signatures for a name implemented once below them.
    ``@x.setter`` / ``.getter`` / ``.deleter`` extend a property of the same
    name, and ``@x.register`` adds a ``singledispatch`` implementation. All
    three are the language working as intended, not a pasted block.
    """
    for decorator in node.decorator_list:
        # `@overload` and `@typing.overload`
        if isinstance(decorator, ast.Name) and decorator.id == "overload":
            return True
        if isinstance(decorator, ast.Attribute):
            if decorator.attr in {"overload", "setter", "getter", "deleter", "register"}:
                return True
    return False


def _scopes(tree: ast.Module) -> Iterator[tuple[str, list[ast.stmt]]]:
    """Yield every scope whose direct children share one namespace.

    The module body and each class body, and nothing else. Definitions nested
    in an ``if`` or a ``try`` are deliberately out of scope: a name bound once
    per branch is the ordinary way to write a platform fallback, and flagging
    it would train people to ignore this check.
    """
    yield "module", tree.body
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield f"class {node.name}", node.body


def find_redefinitions(path: Path) -> list[str]:
    """Report every name defined more than once in one namespace of one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: cannot parse: {exc.msg}"]

    problems: list[str] = []
    for scope, body in _scopes(tree):
        seen: dict[str, list[int]] = defaultdict(list)
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if _is_legitimate_redefinition(node):
                    continue
                seen[node.name].append(node.lineno)
        for name, lines in sorted(seen.items()):
            if len(lines) > 1:
                first, *rest = lines
                repeats = ", ".join(str(line) for line in rest)
                problems.append(
                    f"{path}:{first}: `{name}` is defined again at line {repeats} "
                    f"in the same {scope} scope -- only the last one has any effect"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Scan the given paths (or the default roots) and report on stderr."""
    args = argv if argv is not None else sys.argv[1:]
    roots = [Path(arg) for arg in args] or [Path(root) for root in DEFAULT_ROOTS]

    problems: list[str] = []
    for root in roots:
        if not root.exists():
            print(f"no such path: {root}", file=sys.stderr)
            return 2
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for file in files:
            problems.extend(find_redefinitions(file))

    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
