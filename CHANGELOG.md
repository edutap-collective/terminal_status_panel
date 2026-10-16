# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with one qualification: **there is no public Python API before 1.0**, so the
compatibility promise covers the console scripts and their configuration file,
not anything importable. See
[Requirements and stability](docs/reference/stability.md).

This file starts at 0.10.0. Earlier releases are described by their git tags
and commit messages; reconstructing entries for them after the fact would be
guesswork dressed as a record.

## [0.14.0] - unreleased

### Added

- Host health without Docker. HEALTH opens with a **THIS HOST** block and three
  new kinds: `systemd` (failed units, via `systemctl`), `vrrp` (instance, state,
  effective and base priority, and the *lead* of a BACKUP where it is known) and
  `apache` (request rate, slots, workers and finishing slots from mod_status;
  *not applicable* until `[health.apache] url` is set). The DNS checks move into
  THIS HOST. `glusterfs` and the peers stay in the cluster blocks. See
  [About host health](docs/explanation/host-health.md).
- `status-collect` and `install-panel --collector`: a hardened systemd timer
  (every 30 s, a dynamic user) that writes `/run/terminal-status-panel/host.json`
  with the data a login cannot read itself. The panel never runs a privileged
  command and never waits for the collector. A missing or old state file is
  shown (`host state unavailable (collector not running?)`,
  `state 3 min old` in yellow past 120 s), never read as quiet. Where
  keepalived runs and the dump could not be read, VRRP reads
  `no data (<code>)`. `install-panel --collector` prints `failed` and exits 1
  when a `systemctl` step fails, and a re-run activates a timer that is not
  running even when the files already match (`activated`). See
  [Install the collector](docs/how-to/install-the-collector.md) and the
  [state file schema](docs/reference/configuration.md).
- A PSI row under the load average (`cpu 4 % · 9 %`, from
  `/proc/pressure`), coloured by `[thresholds.psi]` (defaults 10 and 25).
  Omitted where the kernel has no PSI.
- Debian updates. Where `apt-check` is absent, UPDATES shows the collector's
  count from a simulated upgrade (`apt-get -s dist-upgrade`, at most hourly).
  A failed check keeps the previous counts and says so
  (`last check failed (<code>)`); counts older than two hours carry
  `checked <n> h ago`.
- `status-trouble`, and TROUBLE as its own section with **host events** from the
  journal (`oom_kill`, `vrrp_state`, `apache_slots_full`, `unit_failed`,
  `apache_reload`), each bound to its source, never to text alone.
  `[trouble] host_events` selects the kinds; changing it makes the collector
  read the whole window again. After a failed journal read the rows carry
  `(not refreshed)` and THIS HOST shows `journal: not refreshed (<code>)`.
- Configuration: `sections` (the default section list of `status-full`),
  `[thresholds.psi]`, `[health.apache]`, `[health.vrrp]`, `[health.systemd]`,
  `[trouble] host_events`, and `health.timeout.systemd`. `health.enabled` now
  defaults to all eight kinds. `sections` and `[trouble] host_events` report
  invalid entries under `--debug` and keep a repeated name once.
- DOCKER INFOS opens with a legend naming every glyph the section can show,
  the row markers `📌` and `⚑` included. It wraps between entries, never
  inside one. Where the clusters were not probed — `status-docker` alone
  collects no health — the `⬜` entry says "cluster not checked" and names
  `status-health`: a clustered row read `⬜ 5/5` there with nothing on screen
  to explain it. `docker.legend = false` removes the line.

### Changed

- The HEALTH section is no longer called CLUSTER HEALTH; it has the blocks
  THIS HOST, CLUSTERS and PEERS.
- TROUBLE moved out of DOCKER INFOS into its own section. `status-docker`
  renders it directly above DOCKER INFOS; `status-full` renders it once.
- The section order of `status-full` is server, health, trouble, docker,
  traefik (it was server, docker, health, traefik).
- Without a Docker endpoint (`DOCKER_HOST` unset and no `/var/run/docker.sock`)
  `status-full` leaves out DOCKER INFOS and TRAEFIK WIRING. Where the endpoint
  exists but the daemon does not answer, the warning stays and the Docker-bound
  health kinds read `not checked (no Docker client)`.
- Health no longer requires a Docker client: host kinds and `glusterfs` run
  without one.
- Where there is no update source, UPDATES reads `n/a (no update source on this
  system)` (it read `n/a (not a Debian/Ubuntu system)`).

### Security

- The collector runs as a dynamic user (`status-collector`) with the file system
  read-only, home directories hidden, no device access and no new privileges.
  Exactly one step runs privileged (`ExecStartPre=-+`): the helper that asks
  keepalived for its JSON dump.
- The keepalived dump contains the VRRP password. The helper refuses a dump
  that is not a regular root-owned file with a single link, waits until its
  size is stable (two polls 0.1 s apart, within about a second; an unfinished
  dump is not copied), copies it in a
  directory only root can enter, as mode `0600`, and removes the original; the
  collector reads five fields (name, state, three priorities). The copy never
  outlives a run: `status-collect` deletes it on every run, `vrrp` enabled or
  not and whatever the VRRP source did, and the unit's `ExecStopPost=` removes
  it when `status-collect` never started. The state file never holds log
  text, paths or client addresses.
- The panel reads the state file without blocking (a FIFO in its place cannot
  hang a login), only as a regular file of at most 64 KiB, and refuses fields
  of the wrong type, and non-finite numbers (`NaN`, `Infinity`), as
  `host state unreadable` rather than failing the whole panel.
- `install-panel --collector --uninstall` stops the timer and then the
  service, waiting for a running collection, before it deletes any file.
- `install-panel --collector --dry-run` is refused (exit 1): the collector
  install has no dry run, and the flag must not be taken for one.
- `install-panel --collector` refuses an interpreter under `/home`, `/root` or
  `/run/user`, which the hardened service could not run from.

### Fixed

- The banner no longer says that no container matches on a host where the
  container listing failed: an unread list is not an empty one. Where the
  services were listed and the containers could not be, the reason names the
  services alone and says why the other half is missing.

## [0.13.0] - 2026-09-12

### Added

- `[health.postgres]`, `[health.mongodb]`, `[health.kafka]`, `[health.rustfs]`:
  per-kind `match` patterns, `postgres.mode = "standalone"` (`pg_isready`),
  `kafka.command_config` (`""` omits it) and `rustfs.scheme`. Every default is
  the behaviour of 0.12.2. Unknown keys and tables under `[health]` are
  reported by `--debug`.
- `traefik.match`: the Traefik workload is found by configurable,
  case-insensitive name patterns, as a Swarm service or as a plain container.
  An empty list reads nothing on the host: no Docker call, no API cross-check.
- Traefik's static configuration is read from the source Traefik itself uses —
  a file (`--configFile` or a default location), else the command-line flags,
  else `TRAEFIK_*` variables — and the file provider by the path that
  configuration names. Files come from Docker configs or, on the node the
  Traefik task runs on, from bind mounts. YAML and TOML. A bind-mounted file
  is opened component by component, never through a symlink below the bind
  source. A provider directory yields at most 64 files — its walk stops once
  a 65th is known — and scans at most 10,000 directory entries; either limit
  leaves a note.

### Changed

- `docker.infra_ui_services` now keeps admin UIs out of every cluster health
  match — the probe's container search and the crash-loop check as well as the
  DOCKER INFOS join — and the join reads the configured list instead of the
  built-in one.
- When no entrypoints can be read, the banner names the reason instead of
  "no entrypoints found" wherever the panel knows it. A Swarm worker, which
  cannot list services, keeps the old banner.
- A Traefik configured by command-line flags with a file-provider directory
  has its dynamic configs chosen by mount path now, as Traefik chooses them:
  a config mounted under that directory with a `.yml`, `.yaml` or `.toml`
  file name is read whatever the config is called; a `*traefik_dynamic*`
  config mounted elsewhere, or without such an extension, is no longer read;
  and the files are read in path order, which decides between two files
  declaring the same name. A config under the provider directory, or
  serving as Traefik's static configuration file, that the service mounts
  but a successful configs listing does not contain is reported as
  `<name>: config not found`; a mounted config outside the directory, or
  one taken by the config-generation rule instead, is skipped silently. A
  listing that failed stays one note.
- A Docker config read under the config-generation rule that contains `{{` is
  noted as templated and no longer parsed, as on the file-provider path.
- The file-provider warning counts further failures, `(+N more)`, instead of
  showing only the first.

### Fixed

- On a host where Swarm is not active, the file provider is no longer
  reported unreadable because Docker configs — a Swarm-only object — could
  not be listed; they are not asked for there. A Swarm node lists them as
  before, a worker included.

## [0.12.2] - 2026-09-10

### Changed

- The warning icon is the emoji warning sign again, with a pad cell inside
  the value: `icons.WARN` is `"⚠️ "`. The text after it sits one cell further
  right than after `✅`; that is the price of the pad and it is intended.

### Fixed

- 0.12.1's diagnosis was wrong, and its documentation with it. Measured on
  2026-09-10 in iTerm2 and in VS Code's terminal: both advance the cursor by
  two cells for `⚠️`, exactly as rich 15 counts — the columns were never out
  of line. What vanished was the space after the icon, painted over by VS
  Code's terminal, which draws the emoji glyph wider than its two cells. The
  pad cell absorbs that; iTerm2 shows it as a second space. The docstring,
  the reference page and the tests no longer claim a cursor-advance
  mismatch, and the bare text sign — small and monochrome in most fonts —
  is gone again.

## [0.12.1] - 2026-09-08

### Changed

- `💤` for a service **deliberately running nothing** replaces `⏸️`, for the
  reason under *Fixed* below: the pause sign only existed as a text character
  plus the emoji variation selector, and that sequence has no agreed width.
- The icon vocabulary states its three rules in one place: one icon, one
  meaning; two cells in every terminal; and shape before colour — an icon
  must stay unambiguous for a reader who cannot see colour, or the colour of
  its row. See [Icon vocabulary](docs/reference/icon-vocabulary.md).

### Fixed

- Warning and paused rows were one cell out: the space after `⚠️` and `⏸️`
  vanished and every column to its right stepped left, on exactly the rows a
  reader was meant to look at. Both glyphs were a text-presentation character
  plus U+FE0F, the emoji variation selector — a sequence rich pads as two
  cells while a terminal following wcwidth advances by one and draws the
  glyph over the padding. The warning icon is now the bare warning sign with
  its second cell padded into the value (`"⚠ "`), which adds up to two by the
  Unicode tables alone; a test pins that property for the whole column
  vocabulary instead of trusting rich's `cell_len`.

## [0.12.0] - 2026-08-30

### Added

- `⏸️` for a service **deliberately running nothing** — scaled to zero
  replicas. That is a measurement and a decision, and it rendered as `⬜`
  until now, which claimed nothing had been observed.

### Fixed

- The WireGuard *mixed endpoint families* line carries `⚠️` instead of `⬜`.
  It reports a measured finding on an already-yellow row; the unmeasured
  marker contradicted both the colour and the sentence.

  Both of these were wrong before `·` became `⬜` in 0.10. `·` read as a
  neutral bullet, so the contradiction was invisible; giving the marker a
  meaning made it visible. Three remaining uses of `⬜` — a job with no run
  history, a cluster with no quorum reading, a check that never ran — are
  genuinely unmeasured and unchanged. There is now one test per meaning, so
  the five cases cannot quietly collapse into one glyph again.

## [0.11.0] - 2026-08-28

### Added

- The panel's own version in the footer, beside the timestamp. Read from the
  installed distribution metadata; a checkout that was never installed reports
  `dev` rather than a number matching no release.
- A **MANAGED** block under UPDATES, naming the tool that configures the
  machine, optionally linking the repository its configuration lives in, and
  carrying one line of local detail. Configured under `[managed]` and rendered
  only where `managed.by` is set, so nothing changes for an installation that
  has not asked for it.

  It exists to make one fact hard to miss at a login prompt: a change made
  here by hand is gone at the next run. That rule is known and forgotten, so
  the panel states it where people are already looking, set heavier than the
  rows beside it. Beside a rendered OS logo it costs no vertical space at all,
  because the left column is the taller one.

## [0.10.0] - 2026-08-27

### Added

- `--debug`, and `TERMINAL_STATUS_PANEL_DEBUG=1`, report configuration values
  that could not be used and the stage any unexpected error happened in. The
  command still exits 0 and still renders whatever it can; diagnostics go to
  standard error so a panel piped into an MOTD file is unaffected.
- The MongoDB check measures **every replica-set member**, not just the primary
  and the node answering. `hello` is permitted before authentication, so this
  needs no credentials. Members not reached before the deadline stay unmeasured,
  which is the display the whole set had before.
- Arbiters and passive members appear in the MongoDB member list. `hello`
  reports them in their own fields rather than in `hosts`, so a set that has
  them was rendering a row short.
- Documentation moved into `docs/` as a Sphinx site organised by Diataxis, with
  `make docs`, `make docs-linkcheck` and `make docs-live`. The README keeps a
  quick start and links into it, at 92 lines instead of 1733.
- A first-run tutorial.
- This changelog.
- An explicit statement, in `__init__.py` and in the documentation, that there
  is **no public Python API before 1.0**. The console scripts and the
  configuration file are the supported surface; anything importable may change
  in any release.

### Changed

- **The not-observable marker is now `⬜` instead of `·`.** `·` occupies one
  terminal cell where the other status glyphs occupy two, so every unmeasured
  row sat one column out. `·` remains in use as a separator in the follow-mode
  status line and the Swarm summary, which is unrelated.
- `health.timeout.mongodb` 2.5 → 6.0 and `health.budget` 5.0 → 8.0, to fit the
  member fan-out. Measured: a healthy pass takes 1.95–2.01 s, still below
  Kafka's ~2.6 s, so a working cluster sees no change in how long the section
  takes. The higher budget is spent only where something is broken.
- The Traefik cross-check uses `httpx2` instead of `httpx`.
- Every configuration value is read through one typed parser. A malformed value
  falls back to its default and is reported, instead of raising out of
  `load_config` and leaving the login shell with no panel and no reason.
- A quoted boolean is read as written. `show_image = "false"` means false;
  Python's `bool("false")` is `True`, so it used to mean the opposite.
- A configuration value nobody can have meant — a `width` below 20, a timeout of
  0, a non-finite number — falls back and is reported rather than being honoured.
- Runtime dependencies carry tested lower bounds: `rich>=15.0`, `docker>=6.0`,
  `psutil>=5.9.8`, `distro>=1.5`, `dnspython>=2.0`, `pyyaml>=6.0.1`,
  `httpx2>=2.12`. Each was verified by running the suite at exactly that
  version on Python 3.11 and 3.14.

### Fixed

- Five functions in `collectors/docker.py` were defined twice, identically. The
  first set was dead from the day it was written; an edit to it had no effect.
- The straggler registry no longer grows without bound under `--follow`. Threads
  that finished after their deadline are dropped rather than held until the
  process exits — which for a follow-mode panel never happens.
- `cli.py` documented `status-full` as excluding the Traefik section, and the
  comment beside `DEFAULT_SECTIONS` described a summary rendering that does not
  exist. Both had been wrong for three releases, and the claim had spread into
  a test docstring.
- `traefik.ca` left unset uses the system trust store, and the README said the
  opposite. The library's own default never applied here: the cross-check
  requires a client certificate, so the request always carried an explicitly
  built `SSLContext`.
- Render tests no longer depend on the ambient `TERM`.

### Internal

- A CI check rejects duplicate top-level definitions. Ruff's `F811` does not
  catch the case that motivated it: a name used between its two definitions
  never looks unused to the rule.
- A CI job installs the declared dependency floors and runs the suite against
  them on Python 3.11 and 3.14, and asserts that the versions it installed are
  the ones declared. It found the `rich` incompatibility above, which the
  regular matrix cannot see.
- Status glyphs are asserted to occupy two terminal cells.
- No function in the package is above McCabe 10 any more; nine were over it,
  the worst at 21. `C901` is now part of the lint rule set with that limit, so
  it is a floor that holds rather than a number in a review. `status_line` was
  rewritten rather than split and checked against the original over 21000
  input combinations.
- `model.py` is a package split by domain — system, docker, health, traefik —
  re-exported so no caller or test changed.

## [0.9.0] and earlier

See the git history and the release tags.

[Unreleased]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.13.0...HEAD
[0.14.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.12.2...v0.13.0
[0.12.2]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/edutap-collective/terminal_status_panel/releases/tag/v0.9.0
