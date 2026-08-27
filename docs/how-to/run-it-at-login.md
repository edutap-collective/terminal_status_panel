# Run the panel at login

**Recommended: run it from `profile.d` (the login shell), not from
`update-motd.d`.** This is the setup we use in production, and the reasoning is
explained below. Use *one* method only — running both prints the panel twice.

## Why profile.d and not update-motd.d?

`update-motd.d` looks like the natural home for a login banner, but it cannot
render at the viewer's terminal width:

- `pam_motd` runs the `update-motd.d` scripts **during PAM session setup, before
  the login shell starts**. At that moment the script has **no controlling TTY**
  and the terminal size is not available; `COLUMNS`/`LINES` are only set later,
  by the interactive shell.
- **SSH does not change this.** SSH knows the client's window size (from its
  `pty-req`), but it does not pass it to the MOTD scripts. So whether you connect
  by SSH or by a VM console, the result is the same.
- The output is also typically **cached** (`/run/motd.dynamic`) and shown to
  every subsequent login regardless of their window — a single fixed rendering.

The net effect: `update-motd.d` always renders at a **fixed** width (the config
`width`, default 80). For our environment that is exactly wrong:

- We reach every server **only over SSH** — never a VM/VMware console — so a
  real terminal with a known size is always present *at the shell*, just not at
  MOTD-generation time.
- We work from **MacBooks and 4K displays**, where an 80-column banner is either
  cramped or wastes most of the screen. We want the panel to fill whatever
  window the login happens in.

A `profile.d` snippet runs **inside the interactive login shell**, where stdout
*is* the SSH pty and its (SSH-negotiated) size is available. The tool then
auto-detects and uses the **full current terminal width** on every login — wide
on a 4K display, snug in a small MacBook window — with no fixed value to
maintain. That flexibility is why we chose it.

## Install with `install-panel`

The `install-panel` command writes the login snippet for you — system-wide or
per-user — and is idempotent (safe to re-run) and reversible.

```bash
# System-wide, all users (writes /etc/profile.d/zz-terminal-status-panel.sh):
sudo install-panel --scope global

# Per user, no root needed (managed block in ~/.profile or ~/.zprofile):
install-panel --scope user

# Pick which panel(s) to show — e.g. Docker + cluster health on a Swarm node:
sudo install-panel --scope global --panel docker --panel health
# …or any other combination as separate commands:
install-panel --scope user --panel server --panel docker

# Preview without writing, then remove again:
install-panel --scope user --dry-run
install-panel --scope user --uninstall
```

A Swarm node is the natural case for `--panel docker --panel health`
together: the Docker Swarm block and the clustered-services health block
answer different questions (what's scheduled vs. what's actually healthy)
and both only make sense where the Docker socket is available. Installed
alone, `--panel docker` collects no health at all, so the **Working** cell of
every clustered service falls back to Docker's own measurement — `⬜` only for
a row Docker itself measured clean (fully staffed or scaled to zero), still
`💀`/`⚠️` when Docker measured it dead or degraded — honest, but the column
only earns its cluster icons with `--panel health` beside it.

Options:

| Option | Values | Default | Meaning |
|--------|--------|---------|---------|
| `--scope` | `global` \| `user` | `user` | `/etc/profile.d` (needs root) vs. your own login profile. |
| `--panel` | `full` \| `server` \| `docker` \| `health` \| `traefik` | `full` | Which command to run; repeatable. |
| `--shell` | `auto` \| `bash` \| `zsh` | `auto` | Target profile; `zsh` uses `zprofile` (zsh does not read `/etc/profile`). |
| `--uninstall` | — | — | Remove a previous install. |
| `--dry-run` | — | — | Show what would change, write nothing. |

Global vs. user gives you flexibility: roll it out for everyone via
`/etc/profile.d`, or let individual users opt in (or override the global one)
from their own profile. The snippet only runs for **login** shells (SSH logins,
`bash -l`) and only when interactive — it renders once at login; resizing the
window afterwards re-renders on the next login.

To avoid a duplicate static banner, make sure no `update-motd.d` hook is
installed and, if present, empty `/etc/motd` (and optionally set `PrintMotd no`
in `/etc/ssh/sshd_config`).

## Fallback: update-motd.d (fixed width, not recommended here)

If you must use the classic MOTD mechanism, drop a one-line hook
(`exec status-full`) into `/etc/update-motd.d/` and set a fixed wide `width` in
the config — accepting that it will **not** adapt to each login's window. For a
mix of 4K and laptop screens that is the wrong trade-off; prefer `install-panel`.
