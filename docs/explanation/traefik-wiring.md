# About the Traefik wiring view

`status-traefik` (and the `traefik` section inside `status-full`) reads
Traefik's entrypoint → router → middleware → service wiring straight from the
Docker API: the entrypoints from Traefik's own static configuration — a file,
its command-line flags, or its environment, whichever Traefik itself would
read (see *Where the static configuration comes from* below) — the
routers/middlewares/services from every Swarm service's `traefik.http.*`
labels **and** from every plain or Compose container's — a Traefik router can
be declared on a service, a `docker compose` container, or a bare
`docker run` container, and the panel reads all three the same way — and the
file-provider routers from every file Traefik's file provider reads, Docker
configs and, on the node the Traefik task runs on, bind-mounted host files
alike — the `api` and `ping-router` entries live only there. A container that
is itself a Swarm task is skipped: its labels are its service's own, already
read once from the services list, and reading them again from the container
list would double-count every Swarm-hosted router. No client certificate is
needed, and no change to the Traefik deployment — reading a bind-mounted host
file uses only the Docker socket already required for everything else.

**Selected by the file provider's own path, once one is known; by name
otherwise.** Once the static configuration names `providers.file.directory`
or `.filename`, that path decides exactly which files qualify — see *Where
the static configuration comes from* below. Only when no provider path could
be determined does the panel fall back to 0.12.2's original rule: Swarm keeps
every generation of a config — `traefik_dynamic_yml_v1` through `_v4` may all
still exist — but only the ones named in the service spec are the ones
Traefik has loaded. Selecting by name instead showed `ping-router` four times
on every entrypoint and turned entrypoints that were removed two revisions
ago into orphaned-router findings. Where the Traefik service cannot be found
at all, no generation is guessed: the file-provider routers are reported as
missing, with the reason named, rather than shown as if they were current.

## Labels from containers, not only Swarm services

A `traefik.http.*` label works the same wherever it is declared: on a Swarm
service, on a `docker compose` container, or on a bare `docker run`
container. The panel reads all three.

A router coming from a container carries two different names, and the panel
keeps them apart on purpose. Its **origin** is the container's own name,
exactly as `docker ps` shows it, so a human can trace the router back to
what declared it — but the tree only ever shows it for a router that lands
in **ORPHANED ROUTERS**, in brackets after the finding, `[course-statistics-db]`
for example. A correctly-wired router — the common case — appears in the
normal per-entrypoint tree with no origin shown at all; `_router_lines`,
which renders that tree, never reads `router.origin`. Brackets are therefore
a property of the orphan listing, not a mark of where a router came from in
general, and their absence next to a healthy router does not mean the origin
was not read. Its **target**, the name matched against Docker to produce the
`✅ 1/1`-style verdict shown on every router regardless of where it landed,
is a different string for a Compose container: Compose sets
`com.docker.compose.service` to the *service* name (`db`), not the
container's own name (`course-statistics-db`), and `db` is also what
`collectors/docker.py` calls that container everywhere else in the panel.
Matching a router's target against the container's own name instead would
render a false `✗ no such service` for a target that is running and
correctly wired — origin and target answer "who declared this?" and "what
does it point at?", and only one of those two questions is "the container's
own name". `compose_identity()` in `collectors/_labels.py` computes the
target name once, and both the Docker collector and the Traefik collector
call it, so the two cannot silently drift apart. A container with no Compose
label — a bare `docker run` — has no second name: origin and target are the
same string.

**A known ambiguity, not fixed here.** A container's target name is unique
only *within* the Compose project (or stack) that started it — the panel has
no notion of a project-qualified identity. Two unrelated Compose projects
that each define a service called `db` both produce a target named `db`, and
a router pointing at `db` matches both: the verdict sums their replica
counts into one number, observed live as `✅ 2/2` where a single,
correctly-wired container should have read `✅ 1/1`. An inflated count for a
common service name (`db`, `web`, `api`, …) is this ambiguity showing up, not
the panel double-counting a healthy service or Traefik being misconfigured.
It predates this work and lives in `collectors/docker.py`'s choice to key a
container's identity by service name alone, not by (project, service) —
fixing it would mean deciding how a router should express *which* project's
`db` it means, which is a design question for another day.

**A second, different collision: one Traefik service name, two declarations.**
The ambiguity above is about two *containers* sharing a target name, and it
shows up as an inflated replica count. This one is about two *label sources*
declaring the same Traefik service name, and it shows up as a verdict for the
wrong thing entirely. `collect_traefik` reads Swarm services first and
containers second, and the container pass ends with `info.services.update()`,
so where both declare `traefik.http.services.web.*` the container's
declaration replaces the Swarm one:

```
Swarm service portal_web        declares routers.web + services.web
Compose container dev-web-1     declares routers.web + services.web
→ services["web"].docker_service becomes "web" (was "portal_web")
```

The router declared by `portal_web` now has its verdict computed from the
Compose container. If `portal_web` is scaled 0/3 and dead while the container
runs, that router renders a green `✅ 1/1` — a healthy verdict for a dead
service, measured on something unrelated to it. **Where two label sources
declare the same Traefik service name, the panel shows one verdict and does
not tell you there was a conflict.** The underlying situation is a genuine
name conflict between two label sources, and what the panel *should* say
about it — report both targets, flag the collision, prefer the Swarm
declaration — is a design question rather than a patch, so it is documented
here instead of fixed. Until it is answered, a router whose verdict looks
implausible is worth checking for a second declaration of its service name.

**A paused standalone container reads as missing.** A paused container is
still listed by `containers.list()`, so its labels are read and its router
appears in the tree — but `collectors/docker.py` counts a container with no
Compose project only while it is running or restarting, so it never becomes a
`ServiceStatus` for the verdict to match against. The router's target then
renders a red `✗ no such service` for a container the panel itself just read
labels off. Un-pausing it restores both halves; a *Compose* container is
unaffected, since it stays in its group and shows the shortfall instead.

The panel renders one branch per entrypoint (in declaration order — see
*Layout and order* below), each listing
its routers (dimmed when they come from the file provider), their
middlewares, and the Docker service or container each one points at —
cross-checked against the same Swarm service **and container** data the
DOCKER INFOS section uses, through the same `service_verdict`, so one target
never gets two verdicts. A target that matches neither a service nor a
container, on a daemon that actually answered, still reads `✗ no such
service`, in red. That
data is collected whenever the `traefik` section runs, including for a bare
`status-traefik`; the DOCKER INFOS block itself is *not* rendered as a side
effect. When the Docker daemon gives no answer at all — no client, or an
unreachable or non-Swarm daemon — the service line shows a neutral `⬜`
rather than claiming the service is missing, since nothing was measured. A
router naming no
entrypoint is attached to every entrypoint by Traefik itself, so it appears
under all of them; an entrypoint with no attached router reads `— no
router`, which is a finding (a published port nothing serves), not an
absence.

## Layout and order

The entrypoint branches are packed into as many columns as the terminal
allows, balanced by height rather than filled row by row: `rich.Columns`,
which CLUSTER HEALTH still uses, fills a grid row by row, so a row is as tall
as its tallest cell and a three-line branch beside a twenty-line one leaves
seventeen blank lines behind it. The packer used here fills column by column
instead, putting the tallest branches each in a column of their own and
stacking the short ones together, so the whole layout is only as tall as its
fullest column. There is no fixed "three columns at 190, one at 60" to name,
because the column count now falls out of which branches land in which
column on the actual terminal width, not out of a uniform column width the
way `Columns` computed it. The orphaned-router block stays full width below
the columns: its lines are the longest in the section, and it is what you
read first. Each entrypoint's head line carries the worst verdict among its
routers, so a wall of branches still says at a glance which one to open.

Entrypoints appear **in the order Traefik's static configuration declares
them** — a file, command-line arguments, or environment variables, whichever
source applies (see *Where the static configuration comes from* below) — not
by port. A deployment's automation may list its shared entrypoints first —
`dashboard`, `ping`, `default`, `https`, say — before appending its own
per-vhost ones, and that grouping is more useful than the numeric order,
which would put `https` (443) first and `dashboard` (8082) last and scatter
what belongs together.

## Folding endpoints that claim nothing

A router pointing at one of Traefik's own `@internal` endpoints — the `ping`
router that answers `/ping` is the everyday example — has nothing to report
on its service line: nothing about `@internal` was ever measured, so there is
no verdict to show, only the target's name. Rather than spend a whole row on
that name, it is folded onto the router's own line instead:

```
  └─ ping-router        Path(`/_traefik_ping_`)  → ping@internal
```

Nothing is hidden and no verdict is dropped, because there was no verdict to
drop in the first place; a router carrying a middleware, or pointing at a
real service, keeps its service line on its own row exactly as before. The
fold does cost the branch some width, though, and a wider branch can push a
column over the terminal's width and cost the whole section a column back —
paying several lines on screen to save one. So the panel builds both the
folded and the unfolded form of every branch, packs each independently, and
draws whichever one actually packs to fewer lines. On a shape of six
entrypoints that share nothing but one `ping` router, folding unconditionally
would cost a column at a terminal width of 120 (14 lines instead of 11);
packing both and choosing avoids that.

## Entrypoints that are supposed to look empty

`ping.entryPoint` (`--ping.entryPoint=ping` as a flag) makes Traefik answer
`/ping` on that entrypoint itself, with no router involved. It is read from
the same static configuration as the entrypoints — the file, the flags or
the `TRAEFIK_*` variables, whichever Traefik uses — and that entrypoint reads
`— Traefik's own health check` instead of `— no router`, so
the one port that is *meant* to carry nothing does not read as a finding.
Every other empty entrypoint still does — an internal `https :443` with
nothing routed to it genuinely has nothing behind it.

## Services the file provider declares

A router can point at a service defined in the dynamic configuration rather
than in Swarm — for example `myapp-api` → `myapp-api-placeholder` →
`http://api.example.net`. Those services are read
along with the routers, and the upstream URL is shown in place of a Docker
verdict, with a `⬜`: nothing about that target was measured. Matching them
against Swarm service names instead reported `✗ no such service` for something
that was never supposed to be a Swarm service.

## Clickable entrypoints and routers

When `[traefik.links]` names a base URL for an entrypoint, that entrypoint's
head becomes a clickable link, and so does the name of every router on it
whose rule names exactly one path. Nothing else in the block is ever
clickable — the service line names a container and a port inside the
cluster, an address no browser reaches, linked or not.

```toml
[traefik.links]
login_example_de = "https://login.example.de"
portal_dept_uni_example_de = "https://portal.dept.uni-example.de"
```

One entry per **entrypoint name**, not per router: every router hanging off
an entrypoint shares that entrypoint's base, only the path differs. The key
is the entrypoint's own name, exactly as Traefik reports it; the value must
start with `http://` or `https://` — anything else (a bare hostname, a
non-string, a typo'd scheme) is silently dropped, and that entrypoint is
left with no links at all rather than a broken one.

**The base cannot be derived — it has to be configured.** Traefik's routers
match on path alone: the reference cluster has no `Host()` rule at all, so
no hostname appears anywhere in the routing configuration this panel reads.
And the entrypoint's own name is not a hostname with underscores standing in
for dots, even though it can look like one: in a name such as
`portal_dept_uni_example_de`, one underscore is a dot and the next is a
hyphen, and nothing in the name itself says which is which —
`portal.dept.uni-example.de` is only recoverable by checking DNS, not by
substitution. A link that goes somewhere plausible but wrong is worse than
no link, because the reader cannot tell which until they click it. That is
why an entrypoint absent from `[traefik.links]` gets no links, rather than a
guessed one.

A router whose rule names more than one path — an alternation such as
``PathPrefix(`/a`) || PathPrefix(`/b`)`` — or negates a path —
``!Path(`/health`)``, which names the one path the router does *not* serve —
keeps no link of its own; there is no single sub-path left to join onto the
base. Its entrypoint's head stays clickable regardless: the host is known
even where the sub-path is not.

`--no-color` suppresses these hyperlinks along with every other colour, the
escape hatch for a terminal that renders OSC-8 hyperlinks badly.

## What "as configured" means, and its limit

Everything above is read from *configuration* — labels and YAML — never from
Traefik's own runtime state. **A router with a typo'd rule, or naming an
entrypoint that does not exist, still appears here exactly as declared**,
because nothing in this reading path asks Traefik whether it actually
accepted it. For example, a router whose label names the entrypoint
`websecure` (Traefik's own common naming convention for a TLS entrypoint), on
a deployment whose entrypoints are `dashboard`, `ping`, `https`,
`app_example_net` and `www_example_net` — no `websecure` among them — is
wired to a port that plainly doesn't exist. Since a
tree keyed by entrypoint has no branch to put such a router under, it would
otherwise vanish from the panel silently. Instead it gets its own
**ORPHANED ROUTERS** block, listing the router, the entrypoint name(s) it
refers to that do not exist, its rule, and the service it would have pointed
at.

When the file provider could not be read in full, a `file provider
unreadable: …` warning appears above the tree. The reasons include, for
example, Docker configs that could not be listed; a file under it that could
not be read, parsed or evaluated; a relative provider path with no declared
working directory; a Traefik service that was not found, so which config
generations are live is unknown; and a listing that stopped at its file or
directory-entry limit. It is a partial-read failure, distinct from the
routers simply being empty. It
names the first failure and counts the others, `(+N more)`, rather than
dropping them; `TraefikInfo.file_provider_notes` holds every one. Because
`api` and `ping-router` live only in the file provider, this warning is the
signal that their absence below is a read failure, not a finding.

## Where the static configuration comes from

Traefik takes its static configuration from exactly one of three sources, and
uses only the first that yields something: a file, then its command-line
flags, then its `TRAEFIK_*` environment variables
([configuration overview](https://doc.traefik.io/traefik/getting-started/configuration-overview/);
the search order lives in
[`cmd/traefik/traefik.go`](https://github.com/traefik/traefik/blob/master/cmd/traefik/traefik.go),
the first-success logic in paerser's `cli/commands.go`, read against Traefik
v3.7.13 and paerser v0.2.2, the version Traefik pins). The panel mirrors that
search so its tree matches what Traefik actually reads — mixing flags into a
tree built from a file, or the reverse, would show a configuration Traefik
itself ignores.

**The file.** The first candidate that exists wins: the `--configFile` value,
then `/etc/traefik/traefik`, `$XDG_CONFIG_HOME/traefik`,
`$HOME/.config/traefik`, `./traefik`, each tried with `.toml`, `.yaml`, `.yml`
in that order
([`loader_file.go` L66-68](https://github.com/traefik/traefik/blob/v3.7.13/pkg/cli/loader_file.go#L66-L68),
[paerser `file_finder.go` L37-49](https://github.com/traefik/paerser/blob/v0.2.2/cli/file_finder.go#L37-L49)).
`$XDG_CONFIG_HOME` and `$HOME` are expanded from the workload's own declared
environment — a `HOME` the container engine injects at run time is invisible
from here — and `./` only resolves against a declared working directory;
without one, a relative `--configFile` is reported as such rather than
guessed at. A `--configFile` that does not exist is skipped silently by
Traefik itself, not treated as an error
([`file_finder.go` L23-26](https://github.com/traefik/paerser/blob/v0.2.2/cli/file_finder.go#L23-L26)),
and the panel does the same.

**Only what is mounted is visible — and "present" means something different
for each mount shape.** A Docker config sitting exactly at the candidate, and
a bind mount whose own target is exactly the candidate (a single file
bind-mounted there), both count as present outright, with no node or
existence check performed at this point: whether either can actually be read
is a question for the read itself, and an unreadable one is reported as such,
not silently treated as absent. A bind mount whose target is a *directory*
that merely covers the candidate is the one shape checked up front, using the
same path resolution a read uses: present only when the Traefik task runs on
this node and the file genuinely exists there. Genuinely missing under such a
directory, a candidate is passed over exactly as Traefik passes over it — a
missing `--configFile` under a checkable directory mount included; on another
node, on a volume, on a tmpfs, or behind a check that fails outright, it is
undecidable rather than absent, which the next paragraph covers.

**An unreadable file never falls back to the flags.** A covering mount that
cannot be checked from here — a bind mount whose Traefik task runs on another
node, a named volume, a tmpfs, or a check that fails outright — makes the
search undecidable, not absent: the panel says so and draws no tree at all,
rather than falling back to a configuration Traefik may not be using. A
`--configFile` that no checkable mount backs is the same kind of gap: it may
be baked into the image, in which case Traefik reads it, or simply absent, in
which case Traefik moves on to its default locations and then the flags —
nothing readable from here can tell those two apart.

**Bind mounts, read only on the Traefik task's node.** A Docker config is
readable through the API from any node; a bind mount is a host path, readable
only where a running Traefik task actually is (a Swarm service's own
`Service.tasks`, checked against `docker info`'s node ID; always, for a plain
container). Reading one reads a host file as the panel's own login user,
resolved component by component below the bind source the way the kernel
would resolve it inside the container — an absolute symlink is never
followed, no step may climb back out of the bind source, symlink loops are
bounded. The file is then opened one path component at a time from the bind
source, none of them through a symlink, so a component swapped for a link
after that check fails to open rather than being followed. Only a plain
regular file is read, at most 1 MiB.

**The file provider, once its path is known.** `providers.file.directory` or
`.filename` — read from the same source as the entrypoints, `directory`
winning when both are set — selects the files: for a directory, everything
under it, recursively, from Docker configs and bind mounts alike, matched by
`.toml`, `.yaml` or `.yml` case-insensitively — taken as Go's `filepath.Ext`
takes it, so a file named just `.yml` counts too — hidden files included,
because Traefik's own directory walk does not skip them
([`file.go` L406-427](https://github.com/traefik/traefik/blob/v3.7.13/pkg/provider/file/file.go#L406-L427)).
A file that a more specific mount also covers belongs to that mount, and is
read once. A directory yields at most 64 files: once more are known the walk
stops, a note says the rest are not read, and the 64 read are the first by
path among those found. The walk also stops after 10,000 directory entries,
with a note naming the directory it stopped in. Those bounds are counts of
entries and files, not of time, and each file is opened without blocking,
so a FIFO cannot hold the login. A filesystem that hangs is a different
matter: a network or FUSE mount behind a bind mount can still block a
directory listing or a read at login, because the Traefik section runs
outside the health checks' time budget. A volume, tmpfs or other mount
nested inside the provider directory is named in a note — Traefik reads the
files in it, the panel cannot — rather than left out as if it were not
there. The same goes for such a mount holding the provider directory
itself: its note stands first, even when configs or bind mounts below it
were listed, because that listing is then partial. A walked `.yml`,
`.yaml` or `.toml` entry that is not listed says why — not a regular file,
or the system's reason when it cannot even be checked, such as a dangling
link or a permission problem. An entry without one of those extensions is
skipped silently: it is not one that Traefik's file provider would read either.
Each file is capped at 1 MiB, the same limit a bind-mounted static file
has. A file containing `{{` is noted as templated and not evaluated —
Traefik runs every dynamic file through Go's `text/template`
([`file.go` L125-156](https://github.com/traefik/traefik/blob/v3.7.13/pkg/provider/file/file.go#L125-L156)),
which the panel cannot do without presenting a guess as configuration.

**Without a known provider path**, dynamic-file reading falls back to the rule
0.12.2 already used: Docker configs named `*traefik_dynamic*` that the
service actually mounts — the file-provider path just described applies only
once the static configuration is known and names one. The same `{{` check
applies to those configs.

### When the tree cannot be drawn at all

Every state below ends the section's banner the same way — `… — the tree
cannot be drawn, the routers below could not be placed` — and draws no
entrypoint branches at all: every router that was still read falls into the
ORPHANED ROUTERS block instead, in yellow rather than red, because with no
entrypoint list to check against, the code cannot tell "not on this one" from
"nothing was read" and declines to accuse.

| State | Reason stated in the banner | Where it is produced |
|---|---|---|
| No Traefik service or container matches `traefik.match` — where the containers were listed, and either the services were too or `docker info` reports Swarm inactive on this node | `no Traefik service or container matches traefik.match (<patterns>)` | `collect_traefik`, `collectors/traefik.py` |
| No service matches and the container listing failed — the containers are an unread list, not an empty one, so only the services are spoken for | `no Traefik service matches traefik.match (<patterns>); containers could not be listed: <reason>` | `collect_traefik`, `collectors/traefik.py` |
| A relative `--configFile` with no declared working directory | `--configFile=<path> is relative and the container's working directory is not declared — not read` | `_find_static_file`, `collectors/traefik.py` |
| `--configFile` not backed by any checkable mount | `--configFile=<path> is not mounted — it may be part of the image, or absent (then Traefik falls back to its default locations and flags)` | `_find_static_file`, `collectors/traefik.py` |
| A candidate's presence cannot be decided — a bind mount on another node, a volume, a tmpfs, or a check that failed | `<reason> — whether it holds <name(s)> cannot be checked from here` | `_find_static_file`, using `presence()` in `collectors/traefik_mounts.py` |
| The located file cannot be read — a bind mount on another node, a volume, an unsupported mount kind, Docker configs that could not be listed, an OS error | `entrypoints are configured in <path>, <reason>` | `_read_static_file`, `collectors/traefik.py` |
| The file's text is not valid YAML/TOML | `<path>: <YAMLError/TOMLDecodeError …>` | `_read_static_file`, `collectors/traefik.py` |
| The file parses cleanly but declares no entrypoints | `<path> declares no entrypoints` | `_read_static_file`, `collectors/traefik.py` |
| No file, no command-line flags, no `TRAEFIK_*` variables at all | `no static configuration found — no file, no flags, no TRAEFIK_ variables` | `_read_static`, `collectors/traefik.py` |
| Something the collector's own guard did not foresee | `Traefik's configuration could not be read: <ExceptionType>: <message>` | `_absorb_traefik`, `collectors/traefik.py` |

The old wording, `no entrypoints found`, still appears in two cases where
the panel has no more specific reason it could back up:

- The workload's command-line flags or `TRAEFIK_*` variables are read
  successfully but simply declare no entrypoints — `parse_static_args` and
  `parse_static_env` report that as an empty result, not as a problem.
- No Traefik was found, but the Swarm services could not be listed and
  `docker info` did not report Swarm inactive — it reported it active, or
  another state, or could not be asked. A Swarm worker is the everyday
  case: it can never list services, and its Traefik
  task container carries the Swarm label and is skipped like every task
  container. Whether a service matches `traefik.match` was never looked at,
  so the panel does not claim that none does — it shows the banner 0.12.2
  showed.

A file found beside other command-line flags does draw a tree; Traefik
ignores those flags, and the panel notes that in one dim line above the tree
(`static configuration from <path>; Traefik ignores N other command-line
flags`) rather than treating it as a reason not to draw.

Two further, narrower gaps, one on each side of the container-label read: a
`containers.list()` call that fails is recorded on `TraefikInfo.container_error`,
and a `services.list()` call that fails is recorded on the symmetric
`TraefikInfo.service_error` — both distinct from `error`, which is reserved
for the case where *neither* listing could be read and there is genuinely
nothing to show. Either one failing alone degrades rather than aborts: the
labels the other listing did read still stand, and the panel renders a dim
notice above the tree naming which side failed — `container labels
unreadable: …` or `Swarm service labels unreadable: …` — so a Docker
permission or connectivity problem never degrades silently to "labels from
the other source only."

The service-listing notice has one deliberate exception: it stays silent
when Swarm is not active on the node running the panel. A `services.list()`
call failing with "this node is not a swarm manager" is not a Docker problem
at all on a Compose-only host — it is the expected, permanent answer on
every single run there, and a warning that fires every time trains the
reader to stop reading it. The section only shows the notice when Swarm
reports itself active and the services listing still failed — a Swarm
manager or worker that genuinely could not be queried, which is worth a
line precisely because it is not supposed to happen.

For the same reason, Docker configs are asked for only where Swarm is
active. When the services listing succeeded, they are listed as before. When
it failed, the panel asks `docker info` once: where Swarm is active — a
worker, which can never list services, or a manager whose listing failed —
configs are listed exactly as 0.12.2 listed them, and a failure there reads
`file provider unreadable: …` as it always has. Where Swarm is not active,
or `docker info` itself fails, configs are not asked for at all: they exist
only on a Swarm daemon, and on a Compose-only host the "not a swarm manager"
answer would otherwise read as an unreadable file provider — although the
bind-mounted files Traefik actually reads there were read fine. On a Swarm
daemon, then, configs are listed as before.
