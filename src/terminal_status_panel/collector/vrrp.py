"""VRRP state from keepalived's own JSON dump.

keepalived (root) writes the dump on SIGRTMIN+2 and the collector reads it.
The dump carries the VRRP authentication password (``auth_data``) when one is
set, so exactly five fields are read and the file is deleted afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model.host import VrrpInstance, VrrpState

#: keepalived's VRRP state numbers. 1 and 2 are measured (tests/fixtures/host);
#: the others follow keepalived's vrrp.h and are not measured here.
_STATES = {0: "INIT", 1: "BACKUP", 2: "MASTER", 3: "FAULT", 97: "DELETED", 98: "STOP"}


def _int(value: object) -> int | None:
    """The value when it is an integer, else None (bool is not a priority)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_keepalived_json(text: str) -> list[VrrpInstance]:
    """The instances of a v1 (top-level array) or v2 (``{"vrrp": [...]}``) dump.

    Raises ValueError when the text is not JSON or not shaped like a dump.
    """
    data = json.loads(text)
    if isinstance(data, dict):
        if "vrrp" not in data:
            raise ValueError("v2 dump without a vrrp list")
        entries = data["vrrp"]
    else:
        entries = data
    if not isinstance(entries, list):
        raise ValueError("unexpected dump shape")
    instances = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("dump entry must be an object")
        fields = entry.get("data", {})
        if not isinstance(fields, dict):
            raise ValueError("dump entry data must be an object")
        name = fields.get("iname")
        if not isinstance(name, str):
            continue
        instances.append(
            VrrpInstance(
                name=name,
                state=_STATES.get(fields.get("state"), "UNKNOWN"),
                base_priority=_int(fields.get("base_priority")),
                effective_priority=_int(fields.get("effective_priority")),
                master_priority=_int(fields.get("master_priority")),
            )
        )
    return instances


def _remove_dump(dump_path: Path) -> str | None:
    """None when the dump is gone; ``cleanup`` when it could not be removed.

    A dump that stays on disk holds the VRRP password, so the failure is
    reported rather than swallowed.
    """
    try:
        dump_path.unlink(missing_ok=True)
    except OSError:
        return "cleanup"
    return None


def collect_vrrp(dump_path: Path) -> tuple[VrrpState | None, str | None]:
    """The VRRP block and an error code (``None`` when fine).

    The dump is deleted whether or not it parses. When it cannot be deleted,
    the parsed state is still returned, with error code ``cleanup``.
    """
    try:
        text = dump_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "not-installed"
    except OSError:
        return None, _remove_dump(dump_path) or "no-permission"
    except ValueError:  # UnicodeDecodeError: not text, so not a dump we can read
        return None, _remove_dump(dump_path) or "parse"
    try:
        instances = parse_keepalived_json(text)
    except ValueError:
        return None, _remove_dump(dump_path) or "parse"
    state = VrrpState(source="keepalived-json", instances=instances)
    return state, _remove_dump(dump_path)
