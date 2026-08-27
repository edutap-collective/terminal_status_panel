# Regenerate the OS logos

Logos are **pre-rendered** from real PNGs into half-block ANSI (`▀` with
fore/background colours) and bundled under
`src/terminal_status_panel/render/logos/*.ans`. They are plain ANSI, so they
render in MOTD and over SSH without any image protocol or runtime dependency.
The correct logo is chosen automatically — by platform first, then by
detected distribution, then Tux — as described under
{doc}`Platform behaviour </explanation/platform-behaviour>`.

To regenerate them (dev only, needs Pillow — `pip install -e '.[dev]'`), drop
source PNGs into `assets/logos/` and run:

```bash
python tools/generate_logos.py
```
