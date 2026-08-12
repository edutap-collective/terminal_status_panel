"""``install-panel`` — wire the status panel into a login shell.

Writes a tiny snippet that runs the chosen command(s) on interactive login,
where the shell owns a real TTY so the panel uses the full terminal width
(unlike ``update-motd.d``, which is generated without a terminal). Supports a
system-wide install (``/etc/profile.d``) or a per-user one (``~/.profile`` /
``~/.zprofile``), is idempotent, and can uninstall itself again.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# panel name -> console script it runs
PANELS = {
    "full": "status-full",
    "server": "status-server",
    "docker": "status-docker",
    "health": "status-health",
    "traefik": "status-traefik",
}

_BEGIN = "# >>> terminal-status-panel >>>"
_END = "# <<< terminal-status-panel <<<"


def _commands(panels: tuple[str, ...]) -> list[str]:
    return [PANELS[p] for p in panels]


def render_profile_file(panels: tuple[str, ...]) -> str:
    """Content of a dedicated ``/etc/profile.d`` file (fully managed)."""
    lines = [
        "# terminal-status-panel login banner (managed by 'install-panel')",
        "# Runs on interactive login shells, at full terminal width.",
        "case $- in *i*) ;; *) return ;; esac",
    ]
    lines += [f"command -v {c} >/dev/null 2>&1 && {c}" for c in _commands(panels)]
    return "\n".join(lines) + "\n"


def render_managed_block(panels: tuple[str, ...]) -> str:
    """A marker-delimited block inserted into an existing rc file."""
    inner = " ; ".join(f"command -v {c} >/dev/null 2>&1 && {c}" for c in _commands(panels))
    return f"{_BEGIN}\ncase $- in *i*) {inner} ;; esac\n{_END}\n"


def strip_block(text: str) -> str:
    """Remove a previously inserted managed block (idempotent)."""
    pattern = re.compile(re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?", re.DOTALL)
    return pattern.sub("", text)


def _detect_shell() -> str:
    return "zsh" if os.path.basename(os.environ.get("SHELL", "")) == "zsh" else "bash"


def resolve_target(scope: str, shell: str, home: Path | None = None) -> tuple[str, Path]:
    """Return (kind, path). kind is 'file' (dedicated, overwritten) or 'block'
    (a managed block inside an existing rc file)."""
    home = home or Path.home()
    if shell == "auto":
        shell = _detect_shell() if scope == "user" else "bash"

    if scope == "global":
        if shell == "zsh":
            return "block", Path("/etc/zsh/zprofile")
        return "file", Path("/etc/profile.d/zz-terminal-status-panel.sh")

    if shell == "zsh":
        return "block", home / ".zprofile"
    bash_profile = home / ".bash_profile"
    return "block", (bash_profile if bash_profile.exists() else home / ".profile")


def apply(
    kind: str, path: Path, panels: tuple[str, ...], uninstall: bool = False, dry_run: bool = False
) -> list[str]:
    """Perform the install/uninstall; return human-readable action messages."""
    messages: list[str] = []

    if uninstall:
        if kind == "file":
            if path.exists():
                messages.append(f"remove {path}")
                if not dry_run:
                    path.unlink()
            else:
                messages.append(f"nothing to remove at {path}")
        else:
            if path.exists():
                new = strip_block(path.read_text())
                messages.append(f"remove managed block from {path}")
                if not dry_run:
                    path.write_text(new)
            else:
                messages.append(f"nothing to remove in {path}")
        return messages

    if kind == "file":
        messages.append(f"write {path}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_profile_file(panels))
            path.chmod(0o644)
    else:
        existing = path.read_text() if path.exists() else ""
        stripped = strip_block(existing).rstrip("\n")
        block = render_managed_block(panels)
        new = (stripped + "\n\n" if stripped else "") + block
        messages.append(f"update managed block in {path}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new)
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install-panel",
        description="Install the status panel into a login shell (recommended over "
        "update-motd.d, so it uses the full terminal width).",
    )
    parser.add_argument(
        "--scope",
        choices=["global", "user"],
        default="user",
        help="system-wide (/etc/profile.d) or per-user (default: user)",
    )
    parser.add_argument(
        "--panel",
        choices=list(PANELS),
        action="append",
        dest="panels",
        help="which command to run; repeatable (default: full)",
    )
    parser.add_argument(
        "--shell",
        choices=["auto", "bash", "zsh"],
        default="auto",
        help="target shell profile (default: auto-detect)",
    )
    parser.add_argument("--uninstall", action="store_true", help="remove a previous install")
    parser.add_argument("--dry-run", action="store_true", help="show actions, write nothing")
    args = parser.parse_args(argv)

    panels = tuple(args.panels or ["full"])
    kind, path = resolve_target(args.scope, args.shell)
    try:
        messages = apply(kind, path, panels, args.uninstall, args.dry_run)
    except PermissionError:
        print(
            f"error: permission denied writing {path}. Re-run with sudo for --scope global.",
            file=sys.stderr,
        )
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    for message in messages:
        print(prefix + message)
    if not args.uninstall and not args.dry_run:
        print(
            f"Installed [{', '.join(panels)}] for {args.scope} login shells — "
            f"open a new login session to see it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
