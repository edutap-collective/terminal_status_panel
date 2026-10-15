# keepalived dump measurements

Measured 2026-10-16 (gemessen: what the platform did on that day, not a guarantee).

Setup: two `debian:trixie` containers (no systemd), keepalived from the
distribution, unicast VRRP, one instance `vi_1` (VRID 51, priorities 200 and
190, virtual address 192.0.2.10/32 on eth0), started with
`keepalived -D -f /etc/keepalived/keepalived.conf`.

- Version: Keepalived v2.3.3 (03/30,2025); build options include
  `--enable-json` and the config option `JSON`.
- `keepalived --signum=JSON` prints `36` (exit code 0), i.e. SIGRTMIN+2 in this
  environment (dump with `kill -36 <pid>`).
- Dump format: **v1** - a top-level JSON array of
  `{"data": {...}, "stats": {...}}`, one element per VRRP instance. Not the
  `{"vrrp": [...]}` object.
- File: `/tmp/keepalived.json`, mode 0600, owner root. The pid file is
  `/run/keepalived.pid` and holds the parent process pid (the dump signal goes
  to that pid).
- `data.state`: 1 = BACKUP, 2 = MASTER.
- Unit file `/usr/lib/systemd/system/keepalived.service` (package): only
  `ExecStart=/usr/sbin/keepalived --dont-fork $DAEMON_ARGS` matched; neither
  `PrivateTmp=` nor `ProtectSystem=` appears. Not measured on a running
  systemd host (no systemd in the containers), so a drop-in could still set it.
- Side effects of the signal: `ip -4 addr` identical before and after on both
  nodes, both processes kept running, same pid. Log output was **not**
  measured (no syslog/journal in the containers).
- `master_priority`: the BACKUP reports `master_priority: 200`, equal to the
  MASTER's `effective_priority: 200`. The MASTER itself reports
  `master_priority: 0`. So `master_priority` is only meaningful on a BACKUP.
- Other differences: MASTER has `vipset: true` and the vip string ends in
  `scope global set`; BACKUP has `vipset: false` and no `set` suffix.
  MASTER counts `advert_sent`, BACKUP counts `advert_rcvd`.

Redaction: the authentication fields were removed; names, addresses and
interface are neutral (`vi_1`, 192.0.2.x, eth0).

## Decision (Step 6)

JSON is built in, so the JSON parser is implemented (no text-dump parser).
`PrivateTmp` is not set in the packaged unit, so the helper script reads
`/tmp/keepalived.json` directly. Because a local drop-in could still enable
`PrivateTmp`, the script may fall back to
`/proc/$(cat /run/keepalived.pid)/root/tmp/keepalived.json`.
