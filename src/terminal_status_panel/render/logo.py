"""Load pre-rendered half-block ANSI OS logos, picked by distribution name.

The ``.ans`` files under ``logos/`` are generated offline from real PNG logos
by ``tools/generate_logos.py``. At runtime we only read the text and hand it to
Rich — no image decoding and no extra dependency.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from .. import platform_defaults

_LOGO_DIR = Path(__file__).parent / "logos"

#: Checked in order against the lower-cased distribution name. Order matters:
#: "opensuse" contains "suse", so the specific key has to come first.
_DISTRO_KEYS: tuple[tuple[str, str], ...] = (
    ("ubuntu", "ubuntu"),
    ("debian", "debian"),
    ("opensuse", "opensuse"),
    ("sles", "suse"),
    ("suse", "suse"),
    ("red hat", "rhel"),
    ("redhat", "rhel"),
    ("rhel", "rhel"),
    ("rocky", "rocky"),
    ("alma", "alma"),
    ("centos", "centos"),
    ("fedora", "fedora"),
)

#: Every key the distribution table can yield, plus the generic one. Tux is a
#: true statement about each of them, so a key listed here whose own artwork is
#: not bundled may borrow the penguin. Nothing else may: for macOS and the BSDs
#: the penguin would be a lie, and there an absent logo is the correct answer.
_LINUX_KEYS: frozenset[str] = frozenset({logo for _, logo in _DISTRO_KEYS} | {"linux"})


def _logo_name(os_name: str | None) -> str:
    """Pick the logo stem: platform first, distribution second, Tux last.

    The platform decides first because it is the stronger fact -- a Mac is a Mac
    whatever string ``distro`` managed to produce. Tux is the final fallback
    only for systems where it is true, which the platform mapping guarantees by
    claiming every non-Linux system it knows.
    """
    key = platform_defaults.logo_key()
    if key is not None:
        return key
    name = (os_name or "").lower()
    for needle, logo in _DISTRO_KEYS:
        if needle in name:
            return logo
    return "linux"


def os_logo_by_key(key: str) -> Text:
    """Return the pre-rendered logo stored under *key*, or empty Text."""
    try:
        path = _LOGO_DIR / f"{key}.ans"
        return Text.from_ansi(path.read_text(encoding="utf-8").rstrip("\n"))
    except Exception:
        return Text("")


def logo_for_key(key: str) -> Text:
    """The logo actually shown for *key*.

    Its own artwork, or Tux for a Linux distribution whose mark could not be
    licensed.

    Several distribution marks are deliberately not bundled (see
    ``assets/logos/SOURCES.md``). Letting those keys render nothing would drop
    the logo column entirely on a Fedora or RHEL host, which is worse than the
    generic answer -- and the generic answer is not a guess: Tux is a true
    statement about any Linux distribution. It is a lie about macOS and the
    BSDs, so keys outside ``_LINUX_KEYS`` keep whatever they have, including
    nothing.
    """
    logo = os_logo_by_key(key)
    if logo.plain or key not in _LINUX_KEYS:
        return logo
    return os_logo_by_key("linux")


def os_logo(os_name: str | None) -> Text:
    """Return the pre-rendered logo for *os_name*, or empty Text if missing."""
    return logo_for_key(_logo_name(os_name))
