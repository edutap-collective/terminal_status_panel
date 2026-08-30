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

## [Unreleased]

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

[Unreleased]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/edutap-collective/terminal_status_panel/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/edutap-collective/terminal_status_panel/releases/tag/v0.9.0
