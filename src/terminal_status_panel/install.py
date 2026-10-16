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
import stat
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

# panel name -> console script it runs
PANELS = {
    "full": "status-full",
    "server": "status-server",
    "docker": "status-docker",
    "health": "status-health",
    "traefik": "status-traefik",
    "trouble": "status-trouble",
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
    """Return (kind, path) for where the snippet belongs.

    ``kind`` is 'file' (a dedicated file, overwritten) or 'block' (a managed
    block inside an existing rc file).
    """
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


UNIT_DIR = Path("etc/systemd/system")
LIB_DIR = Path("usr/lib/terminal-status-panel")
RUN_DIR = Path("run/terminal-status-panel")
WORK_ROOT = Path("run")
_WORK_PREFIX = "terminal-status-panel-dump."
_SERVICE = "terminal-status-panel-collect.service"
_TIMER = "terminal-status-panel-collect.timer"
_BIN_DIR_PATTERN = re.compile(r"^/[A-Za-z0-9._+/-]+$")
# ProtectHome=yes hides these from the service, so an ExecStart there never runs.
_HIDDEN_FROM_SERVICE = ("/home", "/root", "/run/user")


def _data(name: str) -> str:
    return resources.files("terminal_status_panel.data").joinpath(name).read_text()


def render_collector_units(bin_dir: str) -> dict[str, str]:
    """The two unit files (file name -> content), with *bin_dir* filled in.

    Raises ``ValueError`` for a *bin_dir* that is not a plain absolute path,
    so nothing can be injected into the unit.
    """
    if not _BIN_DIR_PATTERN.fullmatch(bin_dir):
        raise ValueError(f"unsafe bin directory for a unit file: {bin_dir!r}")
    return {
        _SERVICE: _data(_SERVICE + ".in").replace("{bin_dir}", bin_dir.rstrip("/")),
        _TIMER: _data(_TIMER),
    }


def check_collector_bin_dir(bin_dir: str) -> None:
    """Refuse a *bin_dir* the hardened service could not run from."""
    render_collector_units(bin_dir)
    for candidate in (bin_dir, str(Path(bin_dir).resolve())):
        for hidden in _HIDDEN_FROM_SERVICE:
            if candidate == hidden or candidate.startswith(hidden + "/"):
                raise ValueError(
                    f"{bin_dir} is hidden from the service (ProtectHome=yes); install the "
                    "package into a system location, e.g. a virtualenv under "
                    "/opt/terminal-status-panel (pip install terminal-status-panel), "
                    "and run install-panel from there"
                )
    if not (Path(bin_dir) / "status-collect").is_file():
        raise ValueError(f"{bin_dir}/status-collect does not exist")


def _systemctl(args: list[str]) -> int:
    """Run ``systemctl`` with *args*; its exit code (127 when it is missing)."""
    import subprocess

    try:
        return subprocess.run(["systemctl", *args], check=False).returncode  # noqa: S603, S607
    except FileNotFoundError:
        return 127


def _write_atomically(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    temporary.chmod(mode)
    os.replace(temporary, path)


def _stop_units(root: Path, systemctl: Callable[[list[str]], int]) -> bool:
    """Stop the timer, then the service, and wait for it; False on a real failure.

    Stopping the timer does not stop a collection it already started, and
    ``systemctl stop`` returns once the service has stopped -- so nothing is
    deleted under a running collector or dump helper. A unit whose file is
    already gone cannot be stopped and need not be: its failure is ignored.
    """
    for args, unit in (
        (["disable", "--now", _TIMER], _TIMER),
        (["stop", _SERVICE], _SERVICE),
    ):
        if systemctl(args) != 0 and (root / UNIT_DIR / unit).exists():
            return False
    return True


def _remove_collector(root: Path, systemctl: Callable[[list[str]], int]) -> str:
    if not _stop_units(root, systemctl):
        return "failed"
    run_dir = root / RUN_DIR
    doomed = [
        root / UNIT_DIR / _SERVICE,
        root / UNIT_DIR / _TIMER,
        root / LIB_DIR / "keepalived-dump",
    ]
    doomed += [run_dir / "host.json", run_dir / "keepalived.json"]
    if run_dir.is_dir():
        doomed += run_dir.glob(".keepalived.*")
    leftover_dirs = list((root / WORK_ROOT).glob(f"{_WORK_PREFIX}*"))
    for directory in leftover_dirs:
        doomed.append(directory / "keepalived.json")
    removed = False
    for path in doomed:
        if path.is_symlink() or path.exists():
            path.unlink()
            removed = True
    for directory in [*leftover_dirs, root / LIB_DIR, run_dir]:
        try:
            directory.rmdir()
        except OSError:
            pass
    if removed and systemctl(["daemon-reload"]) != 0:
        return "failed"
    return "removed" if removed else "nothing to remove"


def _timer_running(systemctl: Callable[[list[str]], int]) -> bool:
    return (
        systemctl(["is-enabled", "--quiet", _TIMER]) == 0
        and systemctl(["is-active", "--quiet", _TIMER]) == 0
    )


def _activate(systemctl: Callable[[list[str]], int]) -> bool:
    return systemctl(["daemon-reload"]) == 0 and systemctl(["enable", "--now", _TIMER]) == 0


def apply_collector(
    root: Path,
    bin_dir: str,
    uninstall: bool,
    systemctl: Callable[[list[str]], int] = _systemctl,
) -> str:
    """Install or remove the collector under *root*; report what happened.

    Returns ``"written"``, ``"activated"`` (files unchanged, timer started
    again), ``"unchanged"``, ``"removed"``, ``"nothing to remove"`` or
    ``"failed"`` (a systemctl call failed; ``main`` exits 1). Uninstall
    ignores *bin_dir*; it also clears the runtime directory (state file,
    keepalived copy, leftovers) but leaves the systemd StateDirectory
    (``/var/lib/terminal-status-panel``) alone. *systemctl* takes the
    arguments and returns the exit code.
    """
    if uninstall:
        return _remove_collector(root, systemctl)
    script = root / LIB_DIR / "keepalived-dump"
    modes = {script: 0o755}
    texts = {script: _data("keepalived-dump")}
    for name, text in render_collector_units(bin_dir).items():
        path = root / UNIT_DIR / name
        modes[path] = 0o644
        texts[path] = text

    changed = False
    for path, text in texts.items():
        if path.exists() and path.read_text() == text:
            if stat.S_IMODE(path.stat().st_mode) != modes[path]:
                path.chmod(modes[path])
                changed = True
            continue
        _write_atomically(path, text, modes[path])
        changed = True
    if not changed:
        # Files in place but the timer off -- an earlier activation failed, or
        # someone stopped it: a re-run activates instead of reporting success.
        if _timer_running(systemctl):
            return "unchanged"
        return "activated" if _activate(systemctl) else "failed"
    return "written" if _activate(systemctl) else "failed"


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``install-panel`` command."""
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
    parser.add_argument(
        "--collector",
        action="store_true",
        help="install the host state collector (systemd timer); needs root",
    )
    parser.add_argument("--dry-run", action="store_true", help="show actions, write nothing")
    args = parser.parse_args(argv)

    if args.collector:
        if args.dry_run:
            # Refused rather than half-honoured: the collector install writes
            # units and calls systemctl, and has no dry run of its own.
            print("error: --collector does not support --dry-run", file=sys.stderr)
            return 1
        bin_dir = str(Path(sys.executable).parent)
        try:
            if not args.uninstall:
                check_collector_bin_dir(bin_dir)
            result = apply_collector(Path("/"), bin_dir, args.uninstall)
            print(result)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        except PermissionError as error:
            print(f"error: permission denied ({error}). Re-run with sudo.", file=sys.stderr)
            return 1
        return 1 if result == "failed" else 0

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
