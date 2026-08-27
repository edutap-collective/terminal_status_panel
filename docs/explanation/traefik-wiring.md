# About the Traefik wiring view

`status-traefik` (and the `traefik` section inside `status-full`) reads
Traefik's entrypoint → router → middleware → service wiring straight from the
Docker API: the entrypoints from the Traefik service's own command arguments,
the routers/middlewares/services from every Swarm service's `traefik.http.*`
labels **and** from every plain or Compose container's — a Traefik router can
be declared on a service, a `docker compose` container, or a bare
`docker run` container, and the panel reads all three the same way — and the
file-provider routers from the mounted Docker configs (`traefik_dynamic*`) —
the `api` and `ping-router` entries live only there. A container that is
itself a Swarm task is skipped: its labels are its service's own, already
read once from the services list, and reading them again from the container
list would double-count every Swarm-hosted router. No credentials beyond the
Docker socket are needed, and no change to the Traefik deployment.

**Only the config generations the Traefik service actually mounts are read.**
Swarm keeps every generation of a config — `traefik_dynamic_yml_v1` through
`_v4` may all still exist — but only the ones named in the service spec are
the ones Traefik has loaded. Selecting by name instead showed `ping-router`
four times on every entrypoint and turned entrypoints that were removed two
revisions ago into orphaned-router findings. Where the Traefik service cannot
be found at all, no generation is guessed: the file-provider routers are
reported as missing, with the reason named, rather than shown as if they were
current.

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

The panel renders one branch per entrypoint (sorted by port), each listing
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

Entrypoints appear **in the order the Traefik service's arguments declare
them**, not by port. The Ansible role lists the four every cluster has —
`dashboard`, `ping`, `default`, `https` — before appending this cluster's
per-vhost ones, and that grouping is more useful than the numeric order, which
would put `https` (443) first and `dashboard` (8082) last and scatter the four.

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

`--ping.entryPoint=ping` makes Traefik answer `/ping` on that entrypoint
itself, with no router involved. It is read from the same arguments, and that
entrypoint reads `— Traefik's own health check` instead of `— no router`, so
the one port that is *meant* to carry nothing does not read as a finding.
Every other empty entrypoint still does — an internal `https :443` with
nothing routed to it genuinely has nothing behind it.

## Services the file provider declares

A router can point at a service defined in the dynamic YAML rather than in
Swarm — `account-api` → `account-api-placeholder` →
`http://user-account.internal` is the live example. Those services are read
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
accepted it. The real case on this cluster: the `image_api` router's label
names the entrypoint `websecure` (Traefik's own common naming convention for
a TLS entrypoint), but that cluster's nine entrypoints are named `dashboard`,
`ping`, `default`, `https`, `login_example_net`, `portalmgmt`,
`www_example_net`, `db-ui` and `kafbat` — no `websecure` among them, so the
router is wired to a port that plainly doesn't exist. Since a
tree keyed by entrypoint has no branch to put such a router under, it would
otherwise vanish from the panel silently. Instead it gets its own
**ORPHANED ROUTERS** block, listing the router, the entrypoint name(s) it
refers to that do not exist, its rule, and the service it would have pointed
at.

When the Docker configs backing the file provider could not be listed at
all, a `file provider unreadable: …` warning appears above the tree — a
partial-read failure, distinct from the routers simply being empty. Because
`api` and `ping-router` live only in the file provider, this warning is the
signal that their absence below is a read failure, not a finding.

## What is still not read when Traefik itself runs as a container

Reading labels from containers (above) means a router can now be *declared*
anywhere. Two other things this collector reads, it still reads only from
the Traefik **Swarm service**, and a host where Traefik runs as a container
rather than a Swarm service loses both — though not in the same way, as the
two paragraphs below show: one announces the gap loudly, the other can stay
completely silent about it.

**Entrypoints.** `--entryPoints.*` is read from the Traefik service's own
`TaskTemplate.ContainerSpec.Args`, a piece of the Docker API that only a
Swarm *service* carries. A container-hosted Traefik has no such spec, so
`info.entrypoints` comes back empty — and the section says so plainly: a
yellow `⚠️ no entrypoints found — the tree cannot be drawn, the routers
below could not be placed` banner opens the section whenever this happens,
so the *cause* is never quiet. What the banner does not spell out is its
second-order effect: with no entrypoint list, every router — however it was
declared — falls into the **ORPHANED ROUTERS** block instead of the tree,
where it reads, in yellow, ``⚠️ … entrypoint `https` — no entrypoint could
be read``, never the red ``✗ … entrypoint `https` does not exist`` a router
with a genuinely missing entrypoint gets: with no entrypoint list to check
against, the code cannot tell "not on this one" from "nothing was read" and
declines to accuse. So while the section is loud about not having read the
entrypoints, it says nothing further about whether any router points at one
that does not exist — that specific check simply does not run, for any
router, on such a host. **A container-hosted Traefik with no red orphan
findings has not been shown clean; the check that would have found a
problem never ran.**

**The file provider.** This collector reaches `traefik.yml` only through a
Docker Config mounted into a Swarm service; it never reads a container's
filesystem or its bind mounts. A container-hosted Traefik's `traefik.yml`,
mounted the ordinary Compose way, is invisible to the Docker API entirely —
there is no path to it. Concretely: if no `traefik_dynamic*` Docker config
exists at all, which is the normal state for a deployment that never created
one, the section prints **no warning whatsoever**; `api@internal` and the
ping router are simply absent from the tree, exactly as they would be for a
Swarm deployment that genuinely declares no file provider. The two cases are
not distinguishable from the panel's output. Only when a *stale*
`traefik_dynamic*` config survives from an earlier, Swarm-based deployment
of the same Traefik does the `file provider unreadable: traefik service not
found, so which config generations are mounted cannot be determined` warning
above fire — because in that case the collector can see a config, just not
one it can still tie to a live service.

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
