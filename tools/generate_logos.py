#!/usr/bin/env python3
"""Pre-render PNG logos to half-block ANSI art bundled with the package.

Dev-only tool (needs Pillow). Each output ``.ans`` file is plain ANSI that the
runtime loads with ``rich.text.Text.from_ansi`` — no image decoding, no runtime
dependency, and it survives MOTD caching / SSH like any other coloured text.

Usage:
    python tools/generate_logos.py

Each terminal cell renders two vertical pixels via ``▀`` (upper half block):
the glyph's foreground colours the top pixel, its background the bottom pixel.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "assets" / "logos"
OUT = REPO / "src" / "lmu" / "terminal_status_panel" / "render" / "logos"

# (source file, output name, target width in character cells)
LOGOS = [
    ("debian.png", "debian", 18),
    ("ubuntu.png", "ubuntu", 18),
    ("tux.png", "linux", 18),
]

_UPPER = "▀"
_ALPHA_CUTOFF = 128


def _cell(top, bottom) -> str:
    tr, tg, tb, ta = top
    br, bg, bb, ba = bottom
    top_on = ta >= _ALPHA_CUTOFF
    bot_on = ba >= _ALPHA_CUTOFF
    if not top_on and not bot_on:
        return " "
    if top_on and bot_on:
        return f"\x1b[38;2;{tr};{tg};{tb}m\x1b[48;2;{br};{bg};{bb}m{_UPPER}\x1b[0m"
    if top_on:
        return f"\x1b[38;2;{tr};{tg};{tb}m{_UPPER}\x1b[0m"
    # only bottom pixel: colour the lower half block
    return f"\x1b[38;2;{br};{bg};{bb}m▄\x1b[0m"


def render(path: Path, cols: int) -> str:
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    target_w = cols
    target_h = max(2, round(h / w * target_w))
    if target_h % 2:
        target_h += 1
    img = img.resize((target_w, target_h), Image.LANCZOS)
    px = img.load()

    lines = []
    for y in range(0, target_h, 2):
        row = []
        for x in range(target_w):
            top = px[x, y]
            bottom = px[x, y + 1] if y + 1 < target_h else (0, 0, 0, 0)
            row.append(_cell(top, bottom))
        lines.append("".join(row).rstrip())
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for source, name, cols in LOGOS:
        src = SRC / source
        if not src.exists():
            print(f"skip {name}: {src} missing")
            continue
        ansi = render(src, cols)
        (OUT / f"{name}.ans").write_text(ansi, encoding="utf-8")
        print(f"wrote {name}.ans ({len(ansi)} bytes)")


if __name__ == "__main__":
    main()
