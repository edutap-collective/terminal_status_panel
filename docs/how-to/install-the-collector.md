# Install the collector

The collector is the small service that writes the host state the panel's
**THIS HOST** block and the host rows of **TROUBLE** read. Without it the panel
still works, the `systemd` kind and the PSI row included, but VRRP, Apache,
Debian updates and host events say `host state unavailable (collector not
running?)`. See {doc}`About host health </explanation/host-health>` for what it
does and why it is built the way it is.

## Install it

The collector is installed from the Python environment that holds the panel,
and that environment must be readable by the service. The unit runs with
`ProtectHome=yes`, so a package installed under `/home`, `/root` or `/run/user`
(a `pip install --user`, a virtualenv in a home directory) cannot be started by
it. `install-panel --collector` refuses such an interpreter and says so.

Use a virtualenv under `/opt`, installed with pip:

```bash
sudo python3 -m venv /opt/terminal-status-panel
sudo /opt/terminal-status-panel/bin/pip install terminal-status-panel
sudo /opt/terminal-status-panel/bin/install-panel --collector
```

`install-panel --collector` needs root. It writes the service and timer into
`/etc/systemd/system`, the keepalived helper into
`/usr/lib/terminal-status-panel`, and enables the timer. The unit's `ExecStart`
points at the `status-collect` next to the interpreter you ran it with, so it
always calls the installed package's own collector.

It prints one word:

| Output | Meaning |
|--------|---------|
| `written` | A file was new or changed; systemd was reloaded and the timer enabled. |
| `activated` | The installed files already match, but the timer was not enabled and active; systemd was reloaded and the timer enabled. |
| `unchanged` | The installed files already match and the timer runs; nothing was touched. |
| `failed` | A `systemctl` step failed (its own message is on standard error); the exit status is 1. |

Running it again is safe, and configuration management can use the word to
report a change truthfully. After `failed`, fix the cause and run it again:
it retries the activation even though the files are already in place.

## Check that it runs

```bash
systemctl list-timers terminal-status-panel-collect.timer
cat /run/terminal-status-panel/host.json
```

The timer fires 30 seconds after boot and 30 seconds after each run. The file
appears after the first run; `written_at` is a Unix timestamp that should keep
moving. If the file is missing, look at the service:

```bash
systemctl status terminal-status-panel-collect.service
journalctl -u terminal-status-panel-collect.service
```

The collector prints at most one line, `status-collect: cannot write <path>`,
and no log text or addresses.

## Configure what it collects

The collector reads the same configuration file as the panel,
`/etc/terminal-status-panel/config.toml`.

- **VRRP** is on by default and needs nothing else; a host without keepalived
  simply has no VRRP line. To switch it off, take `vrrp` out of
  `health.enabled`.
- **Apache** is collected only when mod_status answers at the URL you give:

  ```toml
  [health.apache]
  url = "http://localhost/server-status"
  ```

  The collector appends `?auto` itself. The URL should be a local one.
- **Host events** for TROUBLE can be narrowed with `[trouble] host_events`.

All keys are in {doc}`the configuration reference </reference/configuration>`.

## Remove it

```bash
sudo /opt/terminal-status-panel/bin/install-panel --collector --uninstall
```

This stops and disables the timer, then stops the service and waits for a
collection that is still running, removes the units, the keepalived helper and
the contents of the runtime directory (the state file and any leftover copy of
the keepalived dump), and prints `removed`, or `nothing to remove`. If
stopping fails while the unit files are still installed, it prints `failed`,
removes nothing and exits 1; a unit that is already gone is not an error. It **leaves
the systemd state directory** `/var/lib/terminal-status-panel` (journal cursor
and Apache totals) alone; remove it by hand if you want it gone.

## Requirements on the host

Two assumptions the keepalived step relies on are listed in
{doc}`About host health </explanation/host-health>`: `fs.protected_hardlinks`
and `fs.protected_regular` enabled (the defaults of systemd distributions), and
keepalived not running with `PrivateTmp=yes`.
