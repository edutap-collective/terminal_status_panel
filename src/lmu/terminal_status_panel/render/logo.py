"""Small ASCII OS logos, picked by distribution name."""

from __future__ import annotations

from rich.text import Text

_DEBIAN = """\
   ,---.
  /    _\\
 |  ,-" |
 |  |  _/
  \\ `-"'
   `---'"""

_UBUNTU = """\
    _
  _(_)_
 (_) (_)
   (_)
 (_) (_)
  """

_TUX = """\
   .--.
  |o_o |
  |:_/ |
 //   \\ \\
(|     | )
 '\\_   _/'
  `---'"""


def _art_and_color(os_name: str | None) -> tuple[str, str]:
    name = (os_name or "").lower()
    if "ubuntu" in name:
        return _UBUNTU, "bright_red"
    if "debian" in name:
        return _DEBIAN, "red"
    return _TUX, "bright_yellow"


def os_logo(os_name: str | None) -> Text:
    """Return a styled ASCII logo for the given OS name."""
    art, color = _art_and_color(os_name)
    return Text(art, style=color)
