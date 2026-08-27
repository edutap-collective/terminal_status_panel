# About the render width

The width is resolved in this order (first match wins):

1. **`--width N`** — an explicit flag always wins.
2. **The current terminal width** — used automatically when standard output is
   a real terminal (TTY), i.e. when you run the command interactively or from a
   shell-login hook. This is what gives you the *full screen width*.
3. **`width` from the config** (default **80**) — the fallback when there is no
   TTY, e.g. when `update-motd.d` pre-generates the cached MOTD.

> The panel is designed for wide terminals. Narrow widths still render but wrap.
