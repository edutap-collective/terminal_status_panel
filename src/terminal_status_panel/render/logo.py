"""Load pre-rendered half-block ANSI OS logos, picked by distribution name.

The ``.ans`` files under ``logos/`` are generated offline from real PNG logos
by ``tools/generate_logos.py``. At runtime we only read the text and hand it to
Rich — no image decoding and no extra dependency.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

_LOGO_DIR = Path(__file__).parent / "logos"


def _logo_name(os_name: str | None) -> str:
    name = (os_name or "").lower()
    if "ubuntu" in name:
        return "ubuntu"
    if "debian" in name:
        return "debian"
    return "linux"  # generic fallback (Tux)


def os_logo(os_name: str | None) -> Text:
    """Return the pre-rendered logo for *os_name*, or empty Text if missing."""
    try:
        path = _LOGO_DIR / f"{_logo_name(os_name)}.ans"
        return Text.from_ansi(path.read_text(encoding="utf-8").rstrip("\n"))
    except Exception:
        return Text("")
