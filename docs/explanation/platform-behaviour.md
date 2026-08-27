# About platform behaviour

The panel is written for Linux servers but also runs correctly on a
developer's Mac, on FreeBSD, and on the RHEL and SUSE families. This section
is where those differences are collected.

- **Identity.** macOS reads `ProductName` and `ProductVersion` from
  `/System/Library/CoreServices/SystemVersion.plist`, the file both macOS
  itself and `platform.mac_ver()` read; every other system uses
  [`distro`](https://pypi.org/project/distro/), which covers Debian, Ubuntu,
  the RHEL family, the SUSE family and FreeBSD. There is deliberately no
  fallback chain — a system that cannot be identified reports `n/a (OS
  identity unavailable)` rather than inventing a coarser answer. The kernel
  row always names its system as well as its release, e.g. `Darwin 25.5.0` or
  `Linux 6.1.0-18-amd64`, because the release number alone is ambiguous
  between platforms.
- **Filesystems on macOS.** `/` and `/System/Volumes/Data` are two mounts of
  one APFS container; the panel reports the data volume's totals under `/`
  and drops the duplicate data-volume row. Without this merge, `/` reads as a
  reassuring 26 % used on a machine that is in fact 98 % full. The
  `resources.ignore_mountpoints` config key (see above) then hides the
  remaining system volumes and simulator runtimes that would otherwise
  outnumber the real filesystems roughly seven to one.
- **Swap on macOS.** The swap warning threshold defaults to 80 % there,
  rather than the 1 % used everywhere else, because macOS allocates swap
  continuously as a matter of design — the Linux default would warn on every
  healthy Mac.
- **Logos.** The logo is chosen by platform first, distribution second: a Mac
  is a Mac whatever string `distro` produces, so platform identity always
  wins when it applies. Where no platform claims the system, the panel falls
  back to matching the distribution name, and finally to Tux — a true
  statement about the kernel for any Linux distribution without its own
  bundled mark — the RHEL and SUSE families are exactly that case, since their
  marks could not be licensed for redistribution. Systems Tux would
  *misdescribe* never borrow it: macOS gets its product name rendered as block
  lettering rather than an Apple emblem, since no Apple artwork is
  redistributed, OpenBSD and NetBSD get the BSD daemon, and FreeBSD shows no
  logo at all, its own wordmark being illegible at this size. Every bundled
  mark's provenance and licence are recorded in
  [`assets/logos/SOURCES.md`](https://github.com/edutap-collective/terminal_status_panel/blob/main/assets/logos/SOURCES.md).
- **Containers.** Plain `docker run` containers and Docker Compose projects
  now appear alongside Swarm services, not just services from an active
  Swarm. They are grouped by their Compose project into their own `COMPOSE
  PROJECTS` block (mirroring the `SWARM STACKS` block above it); a container
  with no Compose project lands under `Standalone containers`, as does a Swarm
  service created outside any stack — both are standalone, and neither is
  given a project heading it does not belong to. A container
  that exited cleanly (exit code `0`) is treated as finished work and is
  omitted, in both blocks. Beyond a clean exit, the two kinds are **not**
  treated alike: a **Compose** container that exits with a non-zero code
  stays visible and shows as a shortfall against its group, the same way a
  stopped Swarm task would; a **standalone** container has no group to fall
  short against, so it is shown only while it is `running` or `restarting`
  — once it exits, at any exit code, it disappears rather than lingering.
  Without that difference, every one-off `docker run` left behind on a
  development machine would accumulate in the panel forever.
