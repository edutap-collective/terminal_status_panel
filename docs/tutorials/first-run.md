# Run the panel for the first time

In this tutorial you install the panel, run it once by hand, and read what it
tells you about the machine you are on. By the end you will know which of the
five commands to reach for, and what the panel's four status icons mean.

You need Python 3.11 or newer and a terminal. Docker is optional — without it
the Docker block says so and the rest of the panel is unaffected, which is
itself worth seeing.

## Install it into a throwaway environment

Nothing here touches your login shell yet.

```shell
python3 -m venv /tmp/panel-tutorial
/tmp/panel-tutorial/bin/pip install terminal-status-panel
```

## Look at the system alone

Start with the smallest command, so there is less on screen to read:

```shell
/tmp/panel-tutorial/bin/status-server
```

You should see two blocks. **SYSTEM OVERVIEW** on the left carries the
operating system, the kernel, how long the machine has been up and its
addresses, beside an OS logo. **SYSTEM STATUS** below it carries load, CPU per
core, memory, swap and a filesystem table, and ends with the processes ranked
by CPU and by memory.

Notice that the panel filled the whole width of your terminal. Make the window
narrower and run it again — the blocks reflow, and the process table drops
columns rather than wrapping them.

## Add Docker

```shell
/tmp/panel-tutorial/bin/status-docker
```

If a Docker daemon is running, the **DOCKER INFOS** block appears with one row
per service. If none is running, you get a single line saying so. Both are
correct answers, and the difference between them is the panel's whole design:
it distinguishes what it measured from what it could not.

## Read the icons

Whatever the two commands showed you, the icons mean one thing each:

| Icon | Meaning |
|------|---------|
| ✅ | measured healthy |
| ⚠️ | degraded, but serving |
| 💀 | measured broken |
| ⬜ | not measured |

`⬜` is the one worth remembering. It never means "fine" and never means
"broken" — it means nobody asked, or the answer did not arrive in time. A
status panel that guessed instead would be worse than one that says nothing.

## See everything at once

```shell
/tmp/panel-tutorial/bin/status-full
```

This is the command a login shell runs. On a machine with clustered services it
adds **CLUSTER HEALTH** and **TRAEFIK WIRING**; on a plain host those sections
say they found nothing to check, and the panel is shorter.

Run it once more with the diagnostic channel open:

```shell
/tmp/panel-tutorial/bin/status-full --debug
```

The panel is unchanged. On standard error you now get a line per configuration
value that could not be used, or `config: no problems found` — and, if anything
raised, which stage it happened in.

## Clean up

```shell
rm -rf /tmp/panel-tutorial
```

You have run all three commands, seen how the panel treats an absent Docker
daemon, and read its status vocabulary.

To put it in front of your own login shell, follow
{doc}`running it at login </how-to/run-it-at-login>`. To change what it shows,
the {doc}`configuration reference </reference/configuration>` lists every key.
