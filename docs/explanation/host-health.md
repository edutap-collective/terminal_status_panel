# About host health

On a host without Docker the panel used to have one thing to say: the
**SYSTEM STATUS** block. The failures that matter on such a host are host-level
— memory exhausted and a process killed for it, a web server out of process
slots after a reload, a VRRP address that moves back and forth between two
nodes, a systemd unit that failed during the night — and none of them showed up
at login.

The **HEALTH** section now opens with a **THIS HOST** block that answers
"is this machine healthy right now?" with or without Docker, and the
**TROUBLE** section answers "did anything break here in the last twelve
hours?" (see {doc}`the TROUBLE block <the-trouble-block>`).

```text
HEALTH
  THIS HOST
    ✅ VRRP     vi_1 MASTER · 191/200   vi_2 BACKUP · 196/190 · lead −6
    ✅ Apache   112 req/s · slots 19 % · workers 7 %
    ✅ systemd  0 failed
    state 12 s old
  CLUSTERS
    ...
```

The icons are the panel's usual ones (see {doc}`/reference/icon-vocabulary`).
The examples on this page use neutral names (`node-a`, `vi_1`,
`example.net`).

## Two sources

THIS HOST draws on two kinds of source, and the split follows one rule: **the
login path never runs a privileged command and never waits for the collector.**

1. **Read directly by the panel**, because it needs no privilege.
   `systemctl list-units --failed` for the `systemd` kind, and
   `/proc/pressure/{cpu,memory,io}` for the PSI row of the server section.
2. **Read from a state file**, because the data needs a privilege or a history
   the login user does not have. `/run/terminal-status-panel/host.json` is
   written every 30 seconds by `status-collect` and holds VRRP state, Apache
   request rates, Debian update counts and the journal events. Reading it is
   one `open()`; the panel never starts the collector and never blocks on it.

The `vrrp` and `apache` kinds and the host rows of TROUBLE are therefore only
as current as the collector's last run (see *Freshness* below).

## Why a collector

Three things cannot be done at login:

- **Privileges.** keepalived runs as root, and its state is reachable only
  through a signal and a file only root may create. The systemd journal needs
  membership of the `systemd-journal` group. A login user has neither.
- **Windows.** A request rate is the difference between two readings divided by
  the time between them. The status page's own `ReqPerSec` is an average since
  the server started, which barely moves on a server that has been up for
  weeks. Only something that runs repeatedly can hold the previous reading.
- **History.** "An OOM kill in the last twelve hours" means reading the
  journal. The collector reads it **incrementally** from a saved cursor, so a
  run reads the last 30 seconds, not twelve hours; only the first run, one
  that lost its cursor, or one after `[trouble] host_events` changed reads the
  whole window. A whole-window read replaces the saved timestamps instead of
  adding to them, so nothing counts twice.

So the work moves into a systemd timer that runs `status-collect` every
30 seconds as a one-shot service. It never runs as a daemon.
{doc}`Install the collector </how-to/install-the-collector>` shows how to set
it up.

## What the collector may see

The service runs as a dynamic user and is hardened accordingly. The unit that
`install-panel --collector` writes contains:

| Setting | Effect |
|---------|--------|
| `DynamicUser=yes`, `User=status-collector` | No account is created on the host; systemd allocates one for each run. |
| `SupplementaryGroups=systemd-journal` | The only extra group: the journal is readable, nothing else is. |
| `NoNewPrivileges=yes`, `RestrictSUIDSGID=yes` | The process and its children cannot gain privileges. |
| `ProtectSystem=strict`, `ProtectHome=yes` | The file system is read-only except for the service's own directories; home directories are invisible. |
| `PrivateDevices=yes`, `ProtectKernelTunables=yes`, `ProtectControlGroups=yes` | No device nodes, no writes to kernel tunables or cgroups. |
| `RuntimeDirectory=terminal-status-panel` (`0755`, preserved) | Where `host.json` and the keepalived copy live. |
| `StateDirectory=terminal-status-panel` | Where the collector keeps what it must remember between runs (journal cursor, Apache totals). |

`ProtectHome=yes` has a practical consequence: the service cannot run a
`status-collect` that lives under `/home`, `/root` or `/run/user`.
`install-panel --collector` refuses such an interpreter; see
{doc}`the how-to </how-to/install-the-collector>`.

### The one privileged step

The unit has exactly one `ExecStartPre=-+` line, which runs the helper
`/usr/lib/terminal-status-panel/keepalived-dump`. The two prefixes mean:

- `+` runs only that command with full privileges; `User=` and the file-system
  restrictions do not apply to it (see
  [systemd.service(5)](https://manpages.debian.org/trixie/systemd/systemd.service.5.en.html)).
- `-` means a failing helper never stops the run. If keepalived is not
  installed, not running, or the dump does not appear, the helper exits quietly
  and the collector records the VRRP block as absent.

The helper asks keepalived for its JSON dump with the signal
`keepalived --signum=JSON` reports and waits up to one second for
`/tmp/keepalived.json` to be complete. keepalived may write it in several
parts, so the dump counts as complete once its size is the same on two polls
0.1 seconds apart. A dump that has not settled within the second is not
copied; it stays in `/tmp` as keepalived left it, and the next run removes it
before it signals keepalived again. Because it runs as root, it trusts nothing that a
user could have placed there: the pid file must name a process whose name is
`keepalived`, and the dump must be a regular file, not a symbolic link, owned
by root.

### The dump holds a secret

keepalived writes the VRRP authentication password into the dump (`auth_data`)
when one is configured. The design treats the file accordingly:

- The helper copies it in a freshly made directory under `/run` that only root
  can enter, with mode `0600`, never into the collector's runtime directory
  (where it could be swapped for a symbolic link or a device node). Only the
  finished file is moved into place, owned by the collector's user, and the
  original in `/tmp` is removed.
- The collector reads **five fields only** per instance: the instance name,
  the state, `base_priority`, `effective_priority` and `master_priority`. No
  other field is copied anywhere.
- The collector **deletes the copy after reading it**, whether or not it
  parsed. If deleting fails, the VRRP block is still reported and its error
  code is `cleanup`, so a copy that stayed on disk is a visible finding rather
  than a silent one.
- The copy **never outlives a run**. `status-collect` deletes it at the end of
  every run, with `vrrp` in `health.enabled` or not, and also when reading it
  failed in an unforeseen way; a copy it cannot delete is reported as `cleanup`.
  For a run in which `status-collect` never started, the unit's
  `ExecStopPost=-/bin/rm -f %t/terminal-status-panel/keepalived.json` removes
  it. That line runs as the collector's own user, without `+`: the runtime
  directory is the collector's, so no privilege is needed to delete in it.

### Assumptions about the host

These are requirements the design relies on, not properties the package
establishes:

- **`fs.protected_hardlinks` and `fs.protected_regular` are on.** They are the
  defaults of systemd distributions. The helper checks the dump before it
  reads it, but the dump sits in a world-writable directory, and these two
  kernel settings are what keeps another user from interfering between the
  check and the read.
- **keepalived does not run with `PrivateTmp=yes`.** The helper looks for the
  dump in the host's `/tmp`. A unit with private temporary directories writes
  it into a different one, which the helper does not follow, and the VRRP
  block then stays empty.

### Measured on Debian 13

What follows is what we measured on **2026-10-16, on Debian 13 with
keepalived 2.3.3**, in two containers running a single VRRP instance. It
describes what the platform did on that day, not what it guarantees:

- The JSON dump is built in (`--enable-json`), and `keepalived --signum=JSON`
  prints the signal number.
- The dump is format v1: a top-level array with one `{"data": …, "stats": …}`
  object per instance (v2, an object with a `vrrp` key, is also accepted).
- `state` is a number: `1` is BACKUP and `2` is MASTER. The other numbers the
  collector names (`0` INIT, `3` FAULT, `97` DELETED, `98` STOP) follow
  keepalived's header file and were **not** measured.
- A BACKUP reports the priority the master advertises as `master_priority`; a
  MASTER reports `0`. That is why the *lead* is shown only for a BACKUP (see
  below).
- Sending the signal did not change the node's addresses, and both processes
  kept running with the same process id.
- The packaged unit file sets neither `PrivateTmp=` nor `ProtectSystem=`. A
  local drop-in could still set them.

Everything else on this page is behaviour of the code.

## What the state file contains, and what it never contains

`/run/terminal-status-panel/host.json` is world-readable (`0644`) on purpose,
so that any login can read it. It is written to a temporary file in the same
directory and renamed into place, so a reader never sees half a file.

It holds **numbers, states, counts and timestamps — never log text, file
paths, client addresses or any field of the keepalived dump beyond the five
above.** The journal events are reduced to a count and the time of the last
one before they get anywhere near the file; the message text is not stored.
The private files in the state directory (`journal.json`, `apache.json`) hold
the journal cursor, event timestamps and the last Apache totals, nothing more.

The schema is listed in {ref}`the configuration reference <state-file-schema>`; in short,
one block per source, each optional:

- A source that is disabled or does not apply leaves its block `null`.
- A source that fails sets a short error code under `errors` (for example
  `timeout`, `no-permission`, `not-installed`, `parse`, `unreachable`,
  `apt-failed`, `cleanup`, `internal`) and leaves the other blocks intact.
  One failing source never empties the others.

A panel reads only schema 1. A file of another schema is shown as
"host state unavailable (unknown schema)", and keys the panel does not know are
ignored, so an older panel can read a newer collector's file.

## Where the figures come from

**VRRP.** From keepalived's dump, as above: name, state, base and effective
priority. The *lead* — the BACKUP's effective priority minus the priority the
master advertises — is shown only for a BACKUP whose dump carries both
numbers. A MASTER never hears the backups' priorities, so the panel never
claims a lead it cannot see. A lead of `0` or more is red (this backup would
win), a lead within `health.vrrp.lead_warning` of the master is yellow. A
state of FAULT, STOP or DELETED is red.

**Apache.** `GET <url>?auto` on mod_status. The request rate is the difference
in `Total Accesses` between two runs divided by the time between them; after a
restart (the total went down) or without a previous run there is no rate, and
the line shows `—`. *Slots* is the share of scoreboard slots that are not open
(`.`), *workers* the busy share of them, and *finishing* the count of `G`
slots — a reload's old generation still holding slots. The kind is *not
applicable* until `[health.apache] url` is set.

**Debian updates.** Debian has no `apt-check`. On Ubuntu `apt-check` stays the
first choice; where it is absent the UPDATES block uses the collector's count
instead. The collector counts the `Inst` lines of a **simulated upgrade**
(`apt-get -s dist-upgrade`, at most once an hour), and the security updates
are the ones whose origin names a `-security` suite. The simulation downloads
nothing and takes no lock; the collector never runs `apt-get update`, so
refreshing the package lists remains the system's job. A failed or timed-out
simulation keeps the previous answer and its timestamp: a broken apt is never
reported as "0 updates". UPDATES then adds `last check failed (<code>)` in
yellow, and counts whose last successful check is more than two hours old get
a dim `checked 3 h ago` (two hourly checks missed). Neither note appears with
`apt-check`, which is asked on the spot. Where no source exists the block reads
`n/a (no update source on this system)`.

**Host events** for TROUBLE. Each pattern is bound to its **source**, never to
text alone — a free-text search counts every `sudo journalctl -g …` as a hit.

| Kind | Source | Message |
|------|--------|---------|
| `oom_kill` | kernel transport | `Killed process` |
| `vrrp_state` | identifier `Keepalived_vrrp` | `Entering (MASTER\|BACKUP\|FAULT) STATE` |
| `apache_slots_full` | unit `apache2.service` or `httpd.service` | `AH03490` |
| `unit_failed` | PID 1 | `Failed with result` |
| `apache_reload` | PID 1 | `Reloading apache2.service` / `httpd.service` |

Entries older than twelve hours drop out.

## Docker-less hosts

The coupling "no Docker client, no health checks" is gone. The host kinds run
whether or not Docker exists, and so does `glusterfs`, which never needed it.
The Docker-bound kinds (`postgres`, `mongodb`, `kafka`, `rustfs`) are another
matter:

- On a host **without a Docker endpoint** (`DOCKER_HOST` unset and no
  `/var/run/docker.sock`), `status-full` leaves out the DOCKER INFOS and
  TRAEFIK WIRING sections.
- On a host **with** an endpoint whose daemon cannot be reached, nothing is
  hidden: the Docker-bound kinds show `⬜ not checked (no Docker client)`, and
  the Docker section keeps its own warning. A broken Docker on a Swarm node
  must stay visible.

The top-level `sections` key overrides the default section list; see
{doc}`the configuration reference </reference/configuration>`.

## Freshness

A figure that is old must not look current. The block ends with
`state <age> old`, and the age turns yellow once the file is **older than
120 seconds** — four collector periods. A failed journal read adds
`journal: not refreshed (<code>)` above it, since a fresh file can carry old
event counts. The same note is
attached to the host rows of TROUBLE (`(state 3 min old)`).

If the file is missing, THIS HOST says so instead of showing nothing:
`host state unavailable (collector not running?)`; a file that cannot be parsed
reads `host state unreadable`. *Not measured* must never look like *quiet*.
A state file without a VRRP block whose collector reported an error for it
(the dump could not be read or parsed) reads `no data (<code>)`, for example
`no data (parse)`. A dump that was read and lists no instances reads
`n/a here`.

That line appears only where the figure is expected. `vrrp` shows it only when
keepalived's pid file `/run/keepalived.pid` exists, and `apache` only when
`[health.apache] url` is set; otherwise the kind reads `n/a here`. Most nodes
of a cluster run neither keepalived nor the collector, and a red line on every
one of them would teach the reader to ignore it.

The reader never waits for the file. It opens it without blocking, accepts a
regular file of at most 64 KiB only, and checks the type of every field it
reads (a count is an integer, a timestamp a finite number, a state a string;
`NaN` and `Infinity`, which JSON parsers accept, are refused). Anything
else reads `host state unreadable`; the rest of the panel renders as usual.
