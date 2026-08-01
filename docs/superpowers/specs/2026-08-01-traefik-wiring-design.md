# Traefik Wiring Viewer — Design

**Date:** 2026-08-01
**Status:** Approved (design) — pending spec review
**Package:** `terminal_status_panel`

## Purpose

Traefik's own dashboard shows how a request travels: entrypoint → router →
middleware → service. Reaching it from an app server means going out through a
webfe, past Shibboleth and MFA, to look at wiring that is defined on that very
server. This adds a fourth panel section that answers the same question from
the terminal you are already sitting in.

The immediate value is not curiosity. Gathering the fixtures for this design
turned up a real misconfiguration on `lrz_cc`: the router `image_api` on
`edutap_production_image_api` names the entrypoint `websecure`, which does not
exist on that cluster — its nine entrypoints are `dashboard`, `ping`,
`default`, `https`, `login_lmu_de`, `portalmgmt`,
`www_portal_uni_muenchen_de`, `db-ui` and `kafbat`. The same service still
carries `traefik.docker.network` rather than `traefik.swarm.network`. It would
receive no traffic even if it were running, and nothing in the existing panel
says so.

## Scope

A new section `traefik`, reachable as `status-traefik`. It is **not** part of
`status-full`: nine entrypoints and their routers would bury the login banner,
and this is a tool you reach for while debugging, not a status line.

## Where the wiring comes from

Everything needed is in the Docker API — no client certificate, no change to
the `traefik` Ansible role.

| Level | Source | Shape |
|---|---|---|
| Entrypoints | args of the `traefik_traefik` service | `--entryPoints.<name>.address=:<port>` |
| Routers, middlewares, services | `traefik.*` labels on Docker services | `traefik.http.routers.<n>.rule`, … |
| File-provider routers, TLS options, transports | Docker configs named `traefik_dynamic_*` | base64 in `Spec.Data`, then YAML |

### The entrypoint prefix is read case-insensitively

This is not a detail. The four default entrypoints are declared as
`--entrypoints.…` (lowercase) and the five vhost entrypoints as
`--entryPoints.…` (capital P), because they are built by different parts of the
Ansible role. A case-sensitive parser drops `login_lmu_de`, `portalmgmt`,
`www_portal_uni_muenchen_de`, `db-ui` and `kafbat` — precisely the five the
tool exists to explain. A test pins both spellings.

## Two sources, one seam

The Docker path is the base and always runs.

The Traefik API path is activated by a `[traefik]` config block naming the
client certificate, key and CA, and is **off by default**. When on, the viewer
reads `/traefik/api/rawdata` and compares: a router present in the labels but
absent from Traefik's runtime view is marked **rejected**. That is exactly the
class of fault the Docker path cannot see — a rule that failed to parse, a
reference Traefik refused.

The certificate does not exist on the app servers today: the dashboard router
requires one signed by the webfe CA, and the `traefik` role only issues client
certificates signed by the app-server TinyCA, for Traefik→service mTLS. The API
path is therefore dormant until the role is extended. It is built now, against
recorded responses, so that extension is the only remaining work.

## Rendering

A tree per entrypoint, ordered by port:

```
TRAEFIK WIRING

portalmgmt  :2020
  └─ kafbat-ui        PathPrefix(`/portale/kafka-ui`)
     └─ → kafbat-ui           http :8080   ✅ 1/1
  └─ db-ui            PathPrefix(`/portale/db-ui`)
     └─ → cloudbeaver         http :8978   ✅ 1/1

login_lmu_de  :2009
  └─ konto-spa        PathPrefix(`/konto`)
     └─ → konto-web           http :80     ✅ 2/2

https  :443   — no router
```

Internal routers — `api` and `ping-router`, which come from the file provider
and point at `api@internal` / `ping@internal` — render **dimmed and last**
inside their entrypoint. `ping-router` is attached to six entrypoints and would
otherwise dominate every view.

An entrypoint with no router gets its own line rather than being omitted. A
published port that nothing serves is a finding, not an absence.

The service line reuses `service_verdict`, so the icon-and-count vocabulary is
the one the DOCKER INFOS matrix already uses. No second verdict logic.

### Orphaned routers

A tree keyed by entrypoint has one structural blind spot: a router naming an
entrypoint that does not exist belongs under no branch and would vanish. That
is the defect class this project has spent three branches closing — a gap
rendering as nothing at all.

Such routers therefore get their own block below the tree, naming the entrypoint
they ask for:

```
ORPHANED ROUTERS
  ✗ image_api        entrypoint `websecure` does not exist
     Host(`www.portal.uni-muenchen.de`) && PathPrefix(`/wallet/image-api`)
     └─ → image_api           http :8090   💀 0/3
```

This is not a hypothetical section built for symmetry: it has one real
occupant today.

## Honest limits

- **The Docker path shows intent, not runtime.** A label with a typo appears as
  configured even though Traefik rejected it. The API path is the answer, and
  the README says so rather than leaving the reader to assume otherwise.
- A router pointing at a service that does not exist renders `✗`.
- The viewer reports the wiring of the node it runs on. Traefik runs on every
  node with identical configuration, so that is the same everywhere — but it is
  read locally, not cluster-wide.

## Testing

Fixtures are the real recorded data, not invented examples:

- the entrypoint args in **both** spellings, from the live service definition;
- `kafbat-ui`'s labels — one router on two entrypoints, which is the case that
  breaks a naive one-router-one-entrypoint model;
- `image_api`'s labels — the only middleware in the cluster, and the orphaned
  router;
- the decoded `traefik_dynamic_yml_v2` config, for the file-provider routers.

Named cases beyond the happy path: an entrypoint with no router; a router on
several entrypoints appearing under each; a router whose entrypoint does not
exist appearing exactly once in the orphan block and nowhere else; a router
whose service is missing; and the API comparison marking a router Traefik
rejected.

## Out of scope

The measurement layer. No changes to the existing collectors, no change to the
`traefik` Ansible role, and no attempt to reach the Traefik API without the
certificate that does not yet exist.
