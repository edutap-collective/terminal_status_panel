# Diagnose an empty panel

A value in the config file that cannot be read does not stop the panel: the
built-in default is used instead, and the panel renders. What it does *not* do
is guess — `thresholds.memory.warning = "soon"` is not a threshold, and the
file is not silently treated as if it said something else.

`--debug` prints what was skipped and why:

```console
$ status-full --debug --config /etc/terminal-status-panel/config.toml
config: thresholds.memory.warning: expected a number (found 'soon', using 75.0)
config: docker.show_image: expected true or false (found 'maybe', using True)
```

With a clean file it says so, rather than printing nothing — silence would not
distinguish "no problems" from "the flag did nothing".

Two readings are worth knowing about because they are easy to write by
accident:

- **A quoted boolean is read as written.** `show_image = "false"` means false.
  Python's own `bool("false")` is `True`, so this used to mean the opposite of
  what it said. TOML has a real boolean and `show_image = false` remains the
  right way to write it; the quoted form is forgiven, not preferred.
- **A value nobody can have meant falls back rather than being honoured.** A
  `width` below 20 leaves no room for one column of content, and a `timeout` of
  0 aborts every call before it starts. These are reported, not clamped: a typo
  clamped to the nearest legal value disappears behind plausible behaviour.

If the panel is empty because something raised rather than because of the
config, `--debug` names the stage and the exception:

```console
$ status-full --debug
failed while collecting the data: PermissionError: [Errno 13] /var/run/docker.sock
```

Colours are always **forced on** (unless `--no-color`), because at MOTD
generation time there is no TTY to auto-detect a colour terminal.
