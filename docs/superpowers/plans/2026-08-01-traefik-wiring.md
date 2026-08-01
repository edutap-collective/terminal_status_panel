# Traefik Wiring Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `status-traefik` command that shows the entrypoint → router → middleware → service wiring, read from the Docker API, with an optional comparison against Traefik's runtime view.

**Architecture:** Four small pure parsers (entrypoint args, service labels, file-provider configs, and the Traefik API response) feed one assembly function that joins them and detects routers whose entrypoint does not exist. A tree renderer draws one branch per entrypoint plus an orphan block. Nothing existing changes except the section registry.

**Tech Stack:** Python 3.11+, `rich`, `docker` SDK, `httpx` (new, for the optional API path), `pytest`.

## Global Constraints

- **The panel never claims something it did not measure.** A gap in coverage renders neither as a clean bill of health nor as nothing at all. The orphan block exists because a tree keyed by entrypoint would silently drop a router naming a non-existent entrypoint.
- **The entrypoint prefix is matched case-insensitively.** The four default entrypoints are declared `--entrypoints.…`, the five vhost entrypoints `--entryPoints.…`. A case-sensitive parser drops exactly the five the tool exists to explain.
- **Icon vocabulary** from `render/icons.py`: `✅` measured healthy · `⚠️` degraded · `💀` measured broken · `·` not observable · `✗` check failed. No locally defined glyphs.
- The service line reuses `service_verdict` from `render/verdict.py` — no second verdict logic.
- The Docker path always runs; the Traefik API path is **off by default** and activated only by a `[traefik]` config block naming certificate, key and CA.
- `status-traefik` is its own command and is **not** part of `status-full`.
- The collector never raises; `cli.main()` returns 0 unconditionally.
- Python 3.11+, line length 100, ruff `select = ["E", "F", "I"]`. Do **not** run `ruff format`.
- Code, comments and identifiers in English; user-facing panel strings in English.
- Run tests with `.venv/bin/python -m pytest`; lint with `uvx ruff@0.16 check src tests`.
- Baseline: **301 tests pass** on `main`, suite fully green.

## File Structure

| File | Responsibility |
|---|---|
| `src/terminal_status_panel/model.py` | **Modify.** Five dataclasses plus `PanelData.traefik`. |
| `src/terminal_status_panel/collectors/traefik_parse.py` | **Create.** Four pure parsers. No Docker, no network — the whole reason the fixtures can drive them directly. |
| `src/terminal_status_panel/collectors/traefik.py` | **Create.** Reads the Docker API, calls the parsers, joins, detects orphans. |
| `src/terminal_status_panel/render/traefik.py` | **Create.** The tree and the orphan block. |
| `src/terminal_status_panel/config.py` | **Modify.** A `[traefik]` block for the optional API path. |
| `src/terminal_status_panel/render/layout.py`, `cli.py`, `pyproject.toml` | **Modify.** Register the section and the console script. |
| `README.md` | **Modify.** The section, the command, the config, and the intent-versus-runtime limit. |

---

### Task 1: Data model

**Files:**
- Modify: `src/terminal_status_panel/model.py` (append after `HealthInfo`, before `PanelData`; extend `PanelData`)
- Test: `tests/test_model.py` (append)

**Interfaces:**
- Produces: `TraefikEntrypoint`, `TraefikMiddleware`, `TraefikServiceRef`, `TraefikRouter`, `TraefikInfo`, and `PanelData.traefik`. Every later task depends on these exact field names.

All aggregate fields default to empty so a failed collector degrades instead of raising — the convention every other dataclass in this file follows.

Two fields carry deliberate tri-state or provenance meaning:
- `TraefikRouter.rejected` is `bool | None`. `None` means the Traefik API was not consulted, which is the default and must never render as "accepted".
- `TraefikRouter.source` records whether a router came from Swarm labels or the file provider, because the internal ones render differently.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model.py  (append)
from terminal_status_panel.model import (
    TraefikEntrypoint,
    TraefikInfo,
    TraefikMiddleware,
    TraefikRouter,
    TraefikServiceRef,
)


def test_traefik_entrypoint_carries_name_and_address():
    ep = TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020)
    assert (ep.name, ep.address, ep.port) == ("portalmgmt", ":2020", 2020)


def test_a_router_defaults_to_unconsulted_rather_than_accepted():
    """rejected is None until the Traefik API was actually asked."""
    router = TraefikRouter(name="kafbat-ui")
    assert router.rejected is None
    assert router.source == "swarm"
    assert router.entrypoints == []
    assert router.middlewares == []


def test_a_service_reference_may_be_internal():
    ref = TraefikServiceRef(name="api@internal", internal=True)
    assert ref.internal is True
    assert ref.port is None


def test_a_middleware_keeps_its_kind_and_detail():
    mw = TraefikMiddleware(name="image_api_stripprefix", kind="stripprefix",
                           detail="prefixes=/wallet/image-api")
    assert mw.kind == "stripprefix"


def test_traefik_info_defaults_are_empty_and_unconsulted():
    info = TraefikInfo()
    assert info.reachable is False
    assert info.entrypoints == []
    assert info.routers == []
    assert info.middlewares == {}
    assert info.services == {}
    assert info.api_consulted is False
    assert info.error is None


def test_panel_data_carries_traefik():
    from terminal_status_panel.model import PanelData

    assert PanelData().traefik is None
    info = TraefikInfo(reachable=True)
    assert PanelData(traefik=info).traefik.reachable is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'TraefikEntrypoint'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/model.py  (insert after HealthInfo)


@dataclass
class TraefikEntrypoint:
    """A port Traefik listens on, as declared in its static configuration."""

    name: str
    address: str  # ":2020"
    port: int | None = None


@dataclass
class TraefikMiddleware:
    name: str
    kind: str | None = None  # stripprefix, headers, …
    detail: str | None = None  # the first configured key, for display


@dataclass
class TraefikServiceRef:
    """What a router points at — a Docker service, or one of Traefik's own."""

    name: str
    port: int | None = None
    scheme: str | None = None
    internal: bool = False  # api@internal / ping@internal
    docker_service: str | None = None  # the Swarm service backing it, when known


@dataclass
class TraefikRouter:
    name: str
    entrypoints: list[str] = field(default_factory=list)
    rule: str | None = None
    middlewares: list[str] = field(default_factory=list)
    service: str | None = None
    tls: bool = False
    source: str = "swarm"  # swarm | file
    origin: str | None = None  # the Docker service or config it was read from
    # None means the Traefik API was never asked. It must not render as
    # "accepted": not consulted is not the same as confirmed.
    rejected: bool | None = None


@dataclass
class TraefikInfo:
    reachable: bool = False
    entrypoints: list[TraefikEntrypoint] = field(default_factory=list)
    routers: list[TraefikRouter] = field(default_factory=list)
    middlewares: dict[str, TraefikMiddleware] = field(default_factory=dict)
    services: dict[str, TraefikServiceRef] = field(default_factory=dict)
    api_consulted: bool = False
    error: str | None = None
```

Extend `PanelData` with `traefik: TraefikInfo | None = None`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_model.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/model.py tests/test_model.py
git commit -m "feat: add the Traefik wiring data model"
```

---

### Task 2: Parse entrypoints from the service arguments

**Files:**
- Create: `src/terminal_status_panel/collectors/traefik_parse.py`
- Test: `tests/test_collectors_traefik_parse.py`

**Interfaces:**
- Consumes: `TraefikEntrypoint` (Task 1).
- Produces: `parse_entrypoints(args: list[str]) -> list[TraefikEntrypoint]`.

The static entrypoints live in the Traefik service's command arguments. **The prefix must be matched case-insensitively** — the recorded arguments below carry both spellings, because different parts of the Ansible role build them.

Sort by port so the rendered tree has a stable, meaningful order.

- [ ] **Step 1: Write the failing tests**

The fixture is the verbatim argument list recorded from `lmzvd06-ccc-01`.

```python
# tests/test_collectors_traefik_parse.py
from terminal_status_panel.collectors import traefik_parse as parse

ARGS = [
    "--ping=true",
    "--ping.entryPoint=ping",
    "--providers.swarm.endpoint=http://sockproxy:2375",
    "--providers.swarm.exposedbydefault=false",
    "--providers.file.directory=/dynamic/",
    "--api.dashboard=true",
    "--api.basePath=/traefik",
    "--entrypoints.dashboard.address=:8082",
    "--entrypoints.dashboard.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entrypoints.ping.address=:8080",
    "--entrypoints.default.address=:8088",
    "--entrypoints.default.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entrypoints.https.address=:443",
    "--accessLog.filePath=/log/access.log",
    "--log.level=INFO",
    "--global.checknewversion=false",
    "--entryPoints.login_lmu_de.address=:2009",
    "--entryPoints.login_lmu_de.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.portalmgmt.address=:2020",
    "--entryPoints.portalmgmt.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.www_portal_uni_muenchen_de.address=:2010",
    "--entryPoints.www_portal_uni_muenchen_de.forwardedHeaders.trustedIPs=0.0.0.0/0",
    "--entryPoints.db-ui.address=:2008",
    "--entryPoints.kafbat.address=:2006",
]


def test_both_spellings_of_the_prefix_are_found():
    """The defaults use 'entrypoints', the vhost ones 'entryPoints'. A
    case-sensitive parser drops exactly the five that matter."""
    names = {ep.name for ep in parse.parse_entrypoints(ARGS)}
    assert names == {
        "dashboard", "ping", "default", "https",
        "login_lmu_de", "portalmgmt", "www_portal_uni_muenchen_de",
        "db-ui", "kafbat",
    }


def test_ports_are_parsed_from_the_address():
    by_name = {ep.name: ep for ep in parse.parse_entrypoints(ARGS)}
    assert by_name["portalmgmt"].port == 2020
    assert by_name["portalmgmt"].address == ":2020"
    assert by_name["https"].port == 443


def test_entrypoints_are_ordered_by_port():
    ports = [ep.port for ep in parse.parse_entrypoints(ARGS)]
    assert ports == sorted(ports)


def test_non_address_arguments_do_not_create_entrypoints():
    """forwardedHeaders and the like must not be mistaken for a declaration."""
    eps = parse.parse_entrypoints(ARGS)
    assert len(eps) == 9


def test_an_address_with_a_host_still_yields_its_port():
    eps = parse.parse_entrypoints(["--entryPoints.x.address=127.0.0.1:9000"])
    assert (eps[0].name, eps[0].port) == ("x", 9000)


def test_an_unparsable_address_keeps_the_entrypoint_without_a_port():
    eps = parse.parse_entrypoints(["--entryPoints.x.address=notaport"])
    assert (eps[0].name, eps[0].port) == ("x", None)


def test_no_arguments_yield_no_entrypoints():
    assert parse.parse_entrypoints([]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.traefik_parse'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/collectors/traefik_parse.py
"""Pure parsers for Traefik's wiring.

No Docker, no network, no Rich — every function here turns recorded text into
model objects, which is what lets the tests drive them with the real fixtures
captured from a production cluster.
"""

from __future__ import annotations

import re

from ..model import TraefikEntrypoint

# The four default entrypoints are declared '--entrypoints.…' and the five
# vhost ones '--entryPoints.…', because different parts of the Ansible role
# build them. Case-sensitivity here silently drops five of nine.
_ENTRYPOINT_ADDRESS = re.compile(
    r"^--entrypoints\.(?P<name>[^.]+)\.address=(?P<address>.+)$", re.IGNORECASE
)


def _port_of(address: str) -> int | None:
    _, _, tail = address.rpartition(":")
    try:
        return int(tail)
    except ValueError:
        return None


def parse_entrypoints(args: list[str]) -> list[TraefikEntrypoint]:
    """Entrypoints from the Traefik service's command arguments, by port."""
    found: list[TraefikEntrypoint] = []
    for arg in args or []:
        match = _ENTRYPOINT_ADDRESS.match(arg.strip())
        if not match:
            continue
        address = match.group("address")
        found.append(
            TraefikEntrypoint(
                name=match.group("name"), address=address, port=_port_of(address)
            )
        )
    # A port-less entrypoint sorts last rather than crashing the comparison.
    found.sort(key=lambda ep: (ep.port is None, ep.port or 0, ep.name))
    return found
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/collectors/traefik_parse.py \
        tests/test_collectors_traefik_parse.py
git commit -m "feat: parse Traefik entrypoints from the service arguments"
```

---

### Task 3: Parse routers, middlewares and services from labels

**Files:**
- Modify: `src/terminal_status_panel/collectors/traefik_parse.py`
- Test: `tests/test_collectors_traefik_parse.py` (append)

**Interfaces:**
- Consumes: `TraefikRouter`, `TraefikMiddleware`, `TraefikServiceRef` (Task 1).
- Produces: `parse_labels(labels: dict[str, str], origin: str) -> tuple[list[TraefikRouter], dict[str, TraefikMiddleware], dict[str, TraefikServiceRef]]`.

Traefik's dynamic configuration arrives as flat dotted labels on the Docker service. The three shapes that matter:

```
traefik.http.routers.<name>.entrypoints  = portalmgmt,kafbat   ← comma-separated
traefik.http.routers.<name>.rule         = PathPrefix(`/…`)
traefik.http.routers.<name>.middlewares  = a,b
traefik.http.routers.<name>.service      = <name>
traefik.http.routers.<name>.tls          = true
traefik.http.middlewares.<name>.<kind>.<key> = <value>
traefik.http.services.<name>.loadbalancer.server.port   = 8080
traefik.http.services.<name>.loadbalancer.server.scheme = http
```

`origin` is the Docker service the labels came from. It goes on routers and
service references, both of which display it. Middlewares carry no origin: they
render only as `⇢ name (kind)` beneath their router, whose origin is already on
screen — a field nothing shows would be scaffolding for a display that does not
exist.

Note two real quirks the fixtures carry: a router's `entrypoints` may name several, and a router's `service` key is often absent — Traefik then defaults the service name to the router's own name.

- [ ] **Step 1: Write the failing tests**

Fixtures are the verbatim labels of two live services.

```python
# tests/test_collectors_traefik_parse.py  (append)

KAFBAT_LABELS = {
    "traefik.enable": "true",
    "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt,kafbat",
    "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/portale/kafka-ui`)",
    "traefik.http.routers.kafbat-ui.tls": "true",
    "traefik.http.services.kafbat-ui.loadbalancer.server.port": "8080",
    "traefik.http.services.kafbat-ui.loadbalancer.server.scheme": "http",
    "traefik.swarm.network": "kafbat-ui",
}

IMAGE_API_LABELS = {
    "traefik.docker.network": "traefik-public",
    "traefik.enable": "true",
    "traefik.http.middlewares.image_api_stripprefix.stripprefix.prefixes":
        "/wallet/image-api",
    "traefik.http.routers.image_api.entrypoints": "websecure",
    "traefik.http.routers.image_api.middlewares": "image_api_stripprefix",
    "traefik.http.routers.image_api.rule":
        "Host(`www.portal.uni-muenchen.de`) && PathPrefix(`/wallet/image-api`)",
    "traefik.http.routers.image_api.tls": "true",
    "traefik.http.services.image_api.loadbalancer.server.port": "8090",
}


def test_a_router_on_several_entrypoints_keeps_all_of_them():
    routers, _, _ = parse.parse_labels(KAFBAT_LABELS, origin="kafbat-ui_kafbat-ui")
    assert len(routers) == 1
    assert routers[0].entrypoints == ["portalmgmt", "kafbat"]
    assert routers[0].rule == "PathPrefix(`/portale/kafka-ui`)"
    assert routers[0].tls is True
    assert routers[0].origin == "kafbat-ui_kafbat-ui"
    assert routers[0].source == "swarm"


def test_a_router_without_a_service_key_defaults_to_its_own_name():
    routers, _, _ = parse.parse_labels(KAFBAT_LABELS, origin="x")
    assert routers[0].service == "kafbat-ui"


def test_service_port_and_scheme_are_parsed():
    _, _, services = parse.parse_labels(KAFBAT_LABELS, origin="kafbat-ui_kafbat-ui")
    assert services["kafbat-ui"].port == 8080
    assert services["kafbat-ui"].scheme == "http"
    assert services["kafbat-ui"].docker_service == "kafbat-ui_kafbat-ui"


def test_a_middleware_keeps_its_kind_and_first_key():
    _, middlewares, _ = parse.parse_labels(IMAGE_API_LABELS, origin="x")
    mw = middlewares["image_api_stripprefix"]
    assert mw.kind == "stripprefix"
    assert "prefixes" in mw.detail
    assert "/wallet/image-api" in mw.detail


def test_a_router_keeps_its_middleware_references():
    routers, _, _ = parse.parse_labels(IMAGE_API_LABELS, origin="x")
    assert routers[0].middlewares == ["image_api_stripprefix"]


def test_labels_that_are_not_traefik_are_ignored():
    routers, middlewares, services = parse.parse_labels(
        {"com.docker.stack.namespace": "kafka", "lmu.service.description": "x"},
        origin="x",
    )
    assert (routers, middlewares, services) == ([], {}, {})


def test_a_service_without_a_port_still_appears():
    _, _, services = parse.parse_labels(
        {"traefik.http.services.plain.loadbalancer.server.scheme": "https"},
        origin="x",
    )
    assert services["plain"].scheme == "https"
    assert services["plain"].port is None


def test_routers_come_back_in_a_stable_order():
    labels = {
        "traefik.http.routers.zebra.rule": "Path(`/z`)",
        "traefik.http.routers.alpha.rule": "Path(`/a`)",
    }
    routers, _, _ = parse.parse_labels(labels, origin="x")
    assert [r.name for r in routers] == ["alpha", "zebra"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -k parse_labels -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_labels'`. Check which tests that `-k` actually selects and run any it misses separately, so the RED evidence covers all of them.

- [ ] **Step 3: Write the implementation**

Append to `traefik_parse.py` (add `from ..model import TraefikMiddleware, TraefikRouter, TraefikServiceRef` to the existing import):

```python
_ROUTER = re.compile(r"^traefik\.http\.routers\.(?P<name>[^.]+)\.(?P<key>.+)$")
_MIDDLEWARE = re.compile(
    r"^traefik\.http\.middlewares\.(?P<name>[^.]+)\.(?P<kind>[^.]+)\.(?P<key>.+)$"
)
_SERVICE = re.compile(r"^traefik\.http\.services\.(?P<name>[^.]+)\.(?P<key>.+)$")


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_labels(labels: dict[str, str], origin: str):
    """Routers, middlewares and services from one Docker service's labels."""
    routers: dict[str, TraefikRouter] = {}
    middlewares: dict[str, TraefikMiddleware] = {}
    services: dict[str, TraefikServiceRef] = {}

    for key, value in sorted((labels or {}).items()):
        match = _ROUTER.match(key)
        if match:
            router = routers.setdefault(
                match.group("name"),
                TraefikRouter(name=match.group("name"), origin=origin),
            )
            field = match.group("key")
            if field == "entrypoints":
                router.entrypoints = _csv(value)
            elif field == "rule":
                router.rule = value
            elif field == "middlewares":
                router.middlewares = _csv(value)
            elif field == "service":
                router.service = value
            elif field == "tls":
                router.tls = value.lower() == "true"
            continue

        match = _MIDDLEWARE.match(key)
        if match:
            name = match.group("name")
            existing = middlewares.get(name)
            if existing is None:
                middlewares[name] = TraefikMiddleware(
                    name=name,
                    kind=match.group("kind"),
                    detail=f"{match.group('key')}={value}",
                )
            continue

        match = _SERVICE.match(key)
        if match:
            name = match.group("name")
            service = services.setdefault(
                name, TraefikServiceRef(name=name, docker_service=origin)
            )
            field = match.group("key")
            if field.endswith("server.port"):
                try:
                    service.port = int(value)
                except ValueError:
                    service.port = None
            elif field.endswith("server.scheme"):
                service.scheme = value

    # Traefik defaults a router's service to the router's own name.
    for router in routers.values():
        if router.service is None:
            router.service = router.name

    return [routers[name] for name in sorted(routers)], middlewares, services
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/collectors/traefik_parse.py \
        tests/test_collectors_traefik_parse.py
git commit -m "feat: parse Traefik routers, middlewares and services from labels"
```

---

### Task 4: Parse the file-provider configuration

**Files:**
- Modify: `src/terminal_status_panel/collectors/traefik_parse.py`
- Test: `tests/test_collectors_traefik_parse.py` (append)

**Interfaces:**
- Produces: `parse_dynamic_yaml(text: str, origin: str) -> tuple[list[TraefikRouter], dict[str, TraefikMiddleware]]`.

Some routers never appear as labels: the `api` and `ping-router` entries live in a YAML file mounted from a Docker config. Without them the dashboard entrypoint looks empty and the `/_traefik_ping_` path that every webfe health check relies on is invisible.

Routers from this source get `source="file"` so the renderer can dim them.

The package already depends on `dnspython`; YAML parsing needs **PyYAML**, which `rich` does not bring. Add it to `dependencies` in `pyproject.toml` in this task and install it into the venv (`uv pip install pyyaml`).

- [ ] **Step 1: Write the failing tests**

The fixture is the decoded `traefik_dynamic_yml_v2` config from the live cluster.

```python
# tests/test_collectors_traefik_parse.py  (append)

DYNAMIC_YML = """\
http:
    routers:
        api:
            entrypoints: dashboard
            rule: PathPrefix(`/traefik`)
            service: api@internal
            tls: 'true'
        ping-router:
            entryPoints:
            - login_lmu_de
            - portalmgmt
            - www_portal_uni_muenchen_de
            - db-ui
            - kafbat
            - default
            rule: Path(`/_traefik_ping_`)
            service: ping@internal
            tls: 'true'
    serversTransports:
        dummy: {}
tls:
    certificates: []
    options:
        default:
            clientAuth:
                caFiles:
                - /certs/client_ca.pem
                clientAuthType: RequireAndVerifyClientCert
            sniStrict: false
"""


def test_file_routers_are_marked_as_coming_from_the_file_provider():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="traefik_dynamic_yml_v2")
    assert {r.name for r in routers} == {"api", "ping-router"}
    assert all(r.source == "file" for r in routers)
    assert all(r.origin == "traefik_dynamic_yml_v2" for r in routers)


def test_a_single_entrypoint_string_becomes_a_list():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    api = [r for r in routers if r.name == "api"][0]
    assert api.entrypoints == ["dashboard"]
    assert api.service == "api@internal"


def test_the_capitalised_entrypoints_key_is_also_read():
    """The file provider accepts entryPoints as well as entrypoints, and this
    fixture uses both — one per router."""
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    ping = [r for r in routers if r.name == "ping-router"][0]
    assert ping.entrypoints == [
        "login_lmu_de", "portalmgmt", "www_portal_uni_muenchen_de",
        "db-ui", "kafbat", "default",
    ]


def test_a_quoted_tls_string_counts_as_true():
    routers, _ = parse.parse_dynamic_yaml(DYNAMIC_YML, origin="x")
    assert all(r.tls for r in routers)


def test_malformed_yaml_yields_nothing_rather_than_raising():
    assert parse.parse_dynamic_yaml("http: [unclosed", origin="x") == ([], {})


def test_yaml_without_an_http_section_yields_nothing():
    assert parse.parse_dynamic_yaml("tls:\n  stores: {}\n", origin="x") == ([], {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -k dynamic -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_dynamic_yaml'`

- [ ] **Step 3: Write the implementation**

Add `"pyyaml"` to `dependencies` in `pyproject.toml`, then append to `traefik_parse.py`:

```python
def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def parse_dynamic_yaml(text: str, origin: str):
    """Routers and middlewares from a file-provider config.

    The api and ping-router entries live only here. Without them the dashboard
    entrypoint looks empty and the /_traefik_ping_ path every webfe health
    check depends on is invisible.
    """
    import yaml

    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        return [], {}
    if not isinstance(data, dict):
        return [], {}
    http = data.get("http") or {}
    if not isinstance(http, dict):
        return [], {}

    routers: list[TraefikRouter] = []
    for name, spec in sorted((http.get("routers") or {}).items()):
        if not isinstance(spec, dict):
            continue
        # The file provider accepts either spelling of the key.
        entrypoints = spec.get("entrypoints", spec.get("entryPoints"))
        routers.append(
            TraefikRouter(
                name=str(name),
                entrypoints=_as_list(entrypoints),
                rule=spec.get("rule"),
                middlewares=_as_list(spec.get("middlewares")),
                service=spec.get("service"),
                tls=str(spec.get("tls", "")).lower() == "true",
                source="file",
                origin=origin,
            )
        )

    middlewares: dict[str, TraefikMiddleware] = {}
    for name, spec in sorted((http.get("middlewares") or {}).items()):
        kind = next(iter(spec), None) if isinstance(spec, dict) else None
        middlewares[str(name)] = TraefikMiddleware(name=str(name), kind=kind)

    return routers, middlewares
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik_parse.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/collectors/traefik_parse.py pyproject.toml \
        tests/test_collectors_traefik_parse.py
git commit -m "feat: parse the Traefik file-provider configuration"
```

---

### Task 5: Assemble the wiring and find orphans

**Files:**
- Create: `src/terminal_status_panel/collectors/traefik.py`
- Test: `tests/test_collectors_traefik.py`

**Interfaces:**
- Consumes: all three parsers (Tasks 2–4), `TraefikInfo`.
- Produces: `collect_traefik(client, timeout: float = 5.0) -> TraefikInfo` and `unknown_entrypoints(router, known: set[str]) -> list[str]`.

This reads the Docker API — the Traefik service's arguments, every service's labels, and the `traefik_dynamic_*` config objects — and joins them. It never raises: any failure yields `TraefikInfo(error=…)`.

**`unknown_entrypoints` is the orphan detector and the reason this task exists as its own reviewable unit.** A router naming an entrypoint that does not exist belongs under no branch of a tree keyed by entrypoint and would vanish. `image_api` on the live cluster is exactly that case: it names `websecure`, which `lrz_cc` does not have.

A router may name several entrypoints, some known and some not. It renders under each known one *and* appears in the orphan block listing only the unknown ones — reporting it in one place and hiding it from the other would be a half-truth.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collectors_traefik.py
from terminal_status_panel.collectors import traefik as collector
from terminal_status_panel.model import TraefikRouter


class _FakeService:
    def __init__(self, name, labels=None, args=None):
        self.name = name
        spec = {"Labels": labels or {}}
        if args is not None:
            spec["TaskTemplate"] = {"ContainerSpec": {"Args": args}}
        self.attrs = {"Spec": spec}


class _FakeConfig:
    def __init__(self, name, data):
        import base64

        self.name = name
        self.attrs = {"Spec": {"Data": base64.b64encode(data.encode()).decode()}}


class _FakeClient:
    def __init__(self, services=None, configs=None):
        self._services = services or []
        self._configs = configs or []

    class _Coll:
        def __init__(self, items):
            self._items = items

        def list(self, *a, **k):
            return self._items

    @property
    def services(self):
        return self._Coll(self._services)

    @property
    def configs(self):
        return self._Coll(self._configs)


def test_unknown_entrypoints_reports_only_the_missing_ones():
    router = TraefikRouter(name="r", entrypoints=["portalmgmt", "websecure"])
    assert collector.unknown_entrypoints(router, {"portalmgmt"}) == ["websecure"]


def test_a_router_with_only_known_entrypoints_has_no_orphans():
    router = TraefikRouter(name="r", entrypoints=["portalmgmt"])
    assert collector.unknown_entrypoints(router, {"portalmgmt"}) == []


def test_a_router_with_no_entrypoints_is_not_an_orphan():
    """No entrypoint named means Traefik attaches it to all of them."""
    assert collector.unknown_entrypoints(TraefikRouter(name="r"), {"a"}) == []


def test_collect_reads_entrypoints_from_the_traefik_service():
    client = _FakeClient(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
    ])
    info = collector.collect_traefik(client)
    assert info.reachable is True
    assert [ep.name for ep in info.entrypoints] == ["portalmgmt"]


def test_collect_joins_labels_from_every_service():
    client = _FakeClient(services=[
        _FakeService("traefik_traefik", args=["--entryPoints.portalmgmt.address=:2020"]),
        _FakeService("kafbat-ui_kafbat-ui", labels={
            "traefik.http.routers.kafbat-ui.entrypoints": "portalmgmt",
            "traefik.http.routers.kafbat-ui.rule": "PathPrefix(`/x`)",
            "traefik.http.services.kafbat-ui.loadbalancer.server.port": "8080",
        }),
    ])
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["kafbat-ui"]
    assert info.services["kafbat-ui"].port == 8080


def test_collect_reads_the_file_provider_configs():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("traefik_dynamic_yml_v2", (
            "http:\n  routers:\n    api:\n      entrypoints: dashboard\n"
            "      rule: PathPrefix(`/traefik`)\n      service: api@internal\n"
        ))],
    )
    info = collector.collect_traefik(client)
    assert [r.name for r in info.routers] == ["api"]
    assert info.routers[0].source == "file"


def test_configs_that_are_not_traefik_dynamic_are_ignored():
    client = _FakeClient(
        services=[_FakeService("traefik_traefik", args=[])],
        configs=[_FakeConfig("ca-certificates.crt_v1", "not yaml at all: [")],
    )
    assert collector.collect_traefik(client).routers == []


def test_a_docker_failure_is_reported_as_an_error_not_a_crash():
    class _Broken:
        @property
        def services(self):
            raise RuntimeError("socket gone")

    info = collector.collect_traefik(_Broken())
    assert info.reachable is False
    assert "socket gone" in info.error


def test_no_traefik_service_yields_no_entrypoints_but_does_not_fail():
    info = collector.collect_traefik(_FakeClient(services=[]))
    assert info.reachable is True
    assert info.entrypoints == []
    assert info.error is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.collectors.traefik'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/collectors/traefik.py
"""Read Traefik's wiring from the Docker API.

Everything the dashboard shows is derivable here: the entrypoints from the
Traefik service's own arguments, the routers and services from the labels of
every Swarm service, and the file-provider routers from the mounted Docker
configs. No client certificate, no change to the Traefik deployment.

What this cannot see is Traefik's runtime opinion — a rule that failed to
parse still appears here as configured. The optional API path answers that.
"""

from __future__ import annotations

import base64

from ..model import TraefikInfo, TraefikRouter
from .traefik_parse import parse_dynamic_yaml, parse_entrypoints, parse_labels

TRAEFIK_SERVICE_PATTERNS = ("traefik_traefik",)
DYNAMIC_CONFIG_PREFIX = "traefik_dynamic"


def unknown_entrypoints(router: TraefikRouter, known: set[str]) -> list[str]:
    """Entrypoints a router names that do not exist.

    A router with no entrypoint named is attached to all of them by Traefik,
    so it is never an orphan.
    """
    return [name for name in router.entrypoints if name not in known]


def _args_of(service) -> list[str]:
    spec = (getattr(service, "attrs", {}) or {}).get("Spec", {})
    container = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
    return list(container.get("Args") or [])


def _labels_of(service) -> dict:
    return dict(((getattr(service, "attrs", {}) or {}).get("Spec", {})).get("Labels") or {})


def _config_text(config) -> str:
    data = ((getattr(config, "attrs", {}) or {}).get("Spec", {})).get("Data") or ""
    try:
        return base64.b64decode(data).decode("utf-8", "replace")
    except Exception:
        return ""


def collect_traefik(client, timeout: float = 5.0) -> TraefikInfo:
    """The wiring as configured. Never raises."""
    info = TraefikInfo()
    try:
        services = client.services.list()
    except Exception as exc:
        return TraefikInfo(error=f"{type(exc).__name__}: {exc}")
    info.reachable = True

    for service in services:
        name = getattr(service, "name", "") or ""
        if any(pattern in name for pattern in TRAEFIK_SERVICE_PATTERNS):
            info.entrypoints = parse_entrypoints(_args_of(service))
        routers, middlewares, refs = parse_labels(_labels_of(service), origin=name)
        info.routers.extend(routers)
        info.middlewares.update(middlewares)
        info.services.update(refs)

    try:
        configs = client.configs.list()
    except Exception:
        configs = []  # the file provider is optional; the labels still stand
    for config in configs:
        name = getattr(config, "name", "") or ""
        if DYNAMIC_CONFIG_PREFIX not in name:
            continue
        routers, middlewares = parse_dynamic_yaml(_config_text(config), origin=name)
        info.routers.extend(routers)
        info.middlewares.update(middlewares)

    info.routers.sort(key=lambda r: (r.source != "swarm", r.name))
    return info
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_collectors_traefik.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/collectors/traefik.py tests/test_collectors_traefik.py
git commit -m "feat: assemble the Traefik wiring from the Docker API"
```

---

### Task 6: Render the wiring

**Files:**
- Create: `src/terminal_status_panel/render/traefik.py`
- Test: `tests/test_render_traefik.py`

**Interfaces:**
- Consumes: `TraefikInfo` and friends; `unknown_entrypoints` (Task 5); `icons`; `service_verdict` from `render/verdict.py`; `section`/`_subhead` conventions from `render/panels.py`.
- Produces: `traefik_section(info: TraefikInfo | None, cfg: Config, swarm: SwarmInfo | None = None) -> RenderableType`.

One branch per entrypoint, ordered by port, then the orphan block.

Three rules that carry meaning, not taste:

- **An entrypoint with no router gets a line saying so.** A published port nothing serves is a finding, not an absence.
- **Internal routers render dimmed and last** within their entrypoint. `ping-router` is attached to six of the nine and would otherwise dominate every branch.
- **The orphan block lists routers whose entrypoint does not exist**, naming the entrypoint they ask for. Without it the tree drops them silently — the defect this project has spent three branches closing.

Where the router's service maps to a Docker service, the line reuses `service_verdict` so the icon-and-count vocabulary matches the DOCKER INFOS matrix. Where it does not — `api@internal`, `ping@internal` — no verdict is shown, because none was measured.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_traefik.py
from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    TraefikEntrypoint,
    TraefikInfo,
    TraefikRouter,
    TraefikServiceRef,
)
from terminal_status_panel.render import icons
from terminal_status_panel.render.traefik import traefik_section


def _render(info, swarm=None, width=120):
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(traefik_section(info, Config(), swarm))
    return capture.get()


def _wired():
    return TraefikInfo(
        reachable=True,
        entrypoints=[
            TraefikEntrypoint(name="kafbat", address=":2006", port=2006),
            TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020),
            TraefikEntrypoint(name="https", address=":443", port=443),
        ],
        routers=[
            TraefikRouter(name="kafbat-ui", entrypoints=["portalmgmt", "kafbat"],
                          rule="PathPrefix(`/portale/kafka-ui`)", service="kafbat-ui",
                          origin="kafbat-ui_kafbat-ui"),
        ],
        services={"kafbat-ui": TraefikServiceRef(
            name="kafbat-ui", port=8080, scheme="http",
            docker_service="kafbat-ui_kafbat-ui")},
    )


def test_missing_info_renders_a_placeholder_not_a_crash():
    assert "TRAEFIK" in _render(None)


def test_an_error_is_shown_with_its_message():
    out = _render(TraefikInfo(error="socket gone"))
    assert icons.FAILED in out
    assert "socket gone" in out


def test_entrypoints_appear_with_their_port():
    out = _render(_wired())
    assert "portalmgmt" in out
    assert "2020" in out


def test_entrypoints_are_ordered_by_port():
    out = _render(_wired())
    assert out.index(":443") < out.index(":2006") < out.index(":2020")


def test_a_router_on_two_entrypoints_appears_under_both():
    out = _render(_wired())
    assert out.count("kafbat-ui") >= 2


def test_an_entrypoint_without_a_router_says_so():
    out = _render(_wired())
    https_line = [ln for ln in out.splitlines() if "https" in ln][0]
    assert "no router" in https_line


def test_an_orphaned_router_gets_its_own_block_naming_the_entrypoint():
    info = _wired()
    info.routers.append(TraefikRouter(
        name="image_api", entrypoints=["websecure"],
        rule="Host(`www.portal.uni-muenchen.de`)", service="image_api",
        origin="edutap_production_image_api"))
    out = _render(info)
    assert "ORPHANED" in out
    assert "image_api" in out
    assert "websecure" in out


def test_an_orphaned_router_is_not_silently_dropped():
    """The whole reason the block exists: a tree keyed by entrypoint has no
    branch for a router naming an entrypoint that does not exist."""
    info = TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020)],
        routers=[TraefikRouter(name="lost", entrypoints=["nosuch"], service="lost")],
    )
    assert "lost" in _render(info)


def test_a_router_on_a_known_and_an_unknown_entrypoint_appears_in_both_places():
    info = TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020)],
        routers=[TraefikRouter(name="half", entrypoints=["portalmgmt", "nosuch"],
                               service="half")],
    )
    out = _render(info)
    assert out.count("half") >= 2
    assert "nosuch" in out


def test_internal_routers_are_shown_but_dimmed_last():
    info = _wired()
    info.routers.append(TraefikRouter(name="ping-router", entrypoints=["portalmgmt"],
                                      rule="Path(`/_traefik_ping_`)",
                                      service="ping@internal", source="file"))
    out = _render(info)
    assert "ping-router" in out
    # The application router leads its entrypoint's branch.
    assert out.index("kafbat-ui") < out.index("ping-router")


def test_a_service_backed_by_docker_shows_the_shared_verdict():
    swarm = SwarmInfo(reachable=True, enabled=True, services=[
        ServiceStatus("kafbat-ui_kafbat-ui", 1, 1,
                      tasks=[ServiceTask("srv-01", "running")]),
    ])
    out = _render(_wired(), swarm=swarm)
    assert f"{icons.OK} 1/1" in out


def test_a_router_pointing_at_a_missing_service_is_marked():
    info = _wired()
    info.routers[0].service = "gone"
    out = _render(info, swarm=SwarmInfo(reachable=True, enabled=True, services=[]))
    assert icons.FAILED in out


def test_narrow_width_still_renders():
    assert "TRAEFIK" in _render(_wired(), width=60)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_traefik.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terminal_status_panel.render.traefik'`

- [ ] **Step 3: Write the implementation**

```python
# src/terminal_status_panel/render/traefik.py
"""Render Traefik's wiring: entrypoint → router → middleware → service.

One branch per entrypoint, ordered by port, then a block for routers whose
entrypoint does not exist. That block is not symmetry: a tree keyed by
entrypoint has no branch for such a router, so without it the panel would drop
it silently — and the cluster has one today.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from ..collectors.traefik import unknown_entrypoints
from ..config import Config
from ..model import SwarmInfo, TraefikInfo, TraefikRouter
from . import icons
from .panels import _subhead, section
from .verdict import service_verdict

_INTERNAL_SUFFIX = "@internal"


def _service_line(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Text:
    name = router.service or router.name
    ref = info.services.get(name)
    line = Text(f"     └─ → {name}")
    if ref and ref.scheme:
        line.append(f"  {ref.scheme}")
    if ref and ref.port:
        line.append(f" :{ref.port}")
    if name.endswith(_INTERNAL_SUFFIX):
        # Traefik's own endpoint: nothing was measured, so nothing is claimed.
        return line
    docker_name = ref.docker_service if ref else None
    matching = [
        s for s in (swarm.services if swarm else []) if s.name == docker_name
    ]
    if not matching:
        line.append(f"  {icons.FAILED} no such service", style="red")
        return line
    line.append("  ")
    line.append_text(service_verdict(matching, node_count=len(swarm.nodes if swarm else [])))
    return line


def _router_lines(router: TraefikRouter, info: TraefikInfo,
                  swarm: SwarmInfo | None) -> Group:
    style = "dim" if router.source == "file" else ""
    head = Text(f"  └─ {router.name}", style=style)
    if router.rule:
        head.append(f"        {router.rule}", style="dim")
    parts: list[RenderableType] = [head]
    for name in router.middlewares:
        mw = info.middlewares.get(name)
        kind = f" ({mw.kind})" if mw and mw.kind else ""
        parts.append(Text(f"     ├─ ⇢ {name}{kind}", style="dim"))
    parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def _entrypoint_block(entrypoint, info: TraefikInfo, swarm: SwarmInfo | None) -> Group:
    head = Text(f"{entrypoint.name}  {entrypoint.address}", style="bold cyan")
    attached = [r for r in info.routers if entrypoint.name in r.entrypoints]
    if not attached:
        # A published port nothing serves is a finding, not an absence.
        head.append("   — no router", style="dim")
        return Group(head)
    # Internal routers last: ping-router hangs on six of nine entrypoints.
    attached.sort(key=lambda r: (r.source != "swarm", r.name))
    return Group(head, *[_router_lines(r, info, swarm) for r in attached])


def _orphan_block(info: TraefikInfo, swarm: SwarmInfo | None) -> Group | None:
    known = {ep.name for ep in info.entrypoints}
    orphans = [(r, unknown_entrypoints(r, known)) for r in info.routers]
    orphans = [(r, missing) for r, missing in orphans if missing]
    if not orphans:
        return None
    parts: list[RenderableType] = [_subhead("ORPHANED ROUTERS")]
    for router, missing in orphans:
        named = ", ".join(f"`{name}`" for name in missing)
        parts.append(
            Text(f"  {icons.FAILED} {router.name}        entrypoint {named} does not exist",
                 style="red")
        )
        if router.rule:
            parts.append(Text(f"     {router.rule}", style="dim"))
        parts.append(_service_line(router, info, swarm))
    return Group(*parts)


def traefik_section(info: TraefikInfo | None, cfg: Config,
                    swarm: SwarmInfo | None = None) -> RenderableType:
    """The TRAEFIK WIRING block."""
    data = info or TraefikInfo()
    if data.error:
        return section("TRAEFIK WIRING",
                       Text(f"{icons.FAILED} {data.error}", style="red"))
    if not data.reachable:
        return section("TRAEFIK WIRING", Text("not checked", style="dim"))
    if not data.entrypoints:
        return section("TRAEFIK WIRING", Text("no entrypoints found", style="dim"))

    parts: list[RenderableType] = []
    for entrypoint in data.entrypoints:
        parts.append(_entrypoint_block(entrypoint, data, swarm))
        parts.append(Text(""))
    orphans = _orphan_block(data, swarm)
    if orphans is not None:
        parts.append(orphans)
    return section("TRAEFIK WIRING", Group(*parts))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_render_traefik.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/traefik.py tests/test_render_traefik.py
git commit -m "feat: render the Traefik wiring as a tree per entrypoint"
```

---

### Task 7: The optional Traefik API path

**Files:**
- Modify: `src/terminal_status_panel/config.py`, `src/terminal_status_panel/collectors/traefik_parse.py`, `src/terminal_status_panel/collectors/traefik.py`
- Modify: `pyproject.toml` (add `httpx`)
- Test: `tests/test_collectors_traefik_parse.py`, `tests/test_collectors_traefik.py`, `tests/test_config.py` (append to each)

**Interfaces:**
- Produces: `TraefikApiConfig(url, cert, key, ca)` and `Config.traefik`; `parse_api_rawdata(payload: dict) -> set[str]`; `mark_rejected(info, accepted: set[str]) -> None`.

The Docker path shows what Traefik was *told*. A rule that failed to parse, or a reference Traefik refused, still appears there as configured. Traefik's own API is the only source for its runtime opinion.

**This path is off by default and dormant today.** The dashboard router requires a client certificate signed by the webfe CA; the `traefik` Ansible role issues client certificates signed by the app-server TinyCA, for Traefik→service mTLS. Until that role is extended there is no certificate to configure. It is built now, against a recorded response, so the role change is the only remaining work.

`rejected` stays `None` unless the API was actually consulted. Not consulted must never render as accepted — that is the same rule the health section's `clusters_probed` enforces.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py  (append)
def test_traefik_api_is_off_by_default():
    cfg = load_config("/nonexistent/config.toml")
    assert cfg.traefik.url is None
    assert cfg.traefik.cert is None


def test_traefik_api_can_be_configured(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[traefik]\n"
        'url = "https://localhost:8082/traefik/api/rawdata"\n'
        'cert = "/etc/ssl/panel.pem"\n'
        'key = "/etc/ssl/panel.key"\n'
        'ca = "/etc/ssl/webfe-ca.pem"\n'
    )
    cfg = load_config(str(path))
    assert cfg.traefik.url.endswith("/rawdata")
    assert cfg.traefik.cert == "/etc/ssl/panel.pem"
```

```python
# tests/test_collectors_traefik_parse.py  (append)

RAWDATA = {
    "routers": {
        "kafbat-ui@swarm": {"entryPoints": ["portalmgmt"], "status": "enabled"},
        "api@internal": {"status": "enabled"},
        "broken@swarm": {"status": "disabled", "error": ["bad rule"]},
    }
}


def test_only_enabled_routers_count_as_accepted():
    accepted = parse.parse_api_rawdata(RAWDATA)
    assert "kafbat-ui" in accepted
    assert "api" in accepted
    assert "broken" not in accepted


def test_the_provider_suffix_is_stripped():
    assert parse.parse_api_rawdata(RAWDATA) >= {"kafbat-ui", "api"}


def test_a_payload_without_routers_yields_nothing():
    assert parse.parse_api_rawdata({}) == set()
```

```python
# tests/test_collectors_traefik.py  (append)

def test_rejected_stays_none_when_the_api_was_not_consulted():
    info = collector.collect_traefik(_FakeClient(services=[]))
    assert info.api_consulted is False
    assert all(r.rejected is None for r in info.routers)


def test_mark_rejected_flags_routers_traefik_never_accepted():
    from terminal_status_panel.model import TraefikInfo

    info = TraefikInfo(routers=[
        TraefikRouter(name="kept"), TraefikRouter(name="dropped"),
    ])
    collector.mark_rejected(info, {"kept"})
    by_name = {r.name: r for r in info.routers}
    assert by_name["kept"].rejected is False
    assert by_name["dropped"].rejected is True
    assert info.api_consulted is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_collectors_traefik_parse.py tests/test_collectors_traefik.py -k "traefik" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'traefik'` and the two missing functions.

- [ ] **Step 3: Write the implementation**

Add `"httpx"` to `dependencies` in `pyproject.toml`.

In `config.py`:

```python
@dataclass
class TraefikApiConfig:
    """The optional runtime cross-check. Off unless a URL and cert are given.

    Dormant today: the dashboard router requires a client certificate signed by
    the webfe CA, and the Ansible role issues only app-server TinyCA ones.
    """

    url: str | None = None
    cert: str | None = None
    key: str | None = None
    ca: str | None = None
```

Extend `Config` with `traefik: TraefikApiConfig = field(default_factory=TraefikApiConfig)` and parse it in `load_config`:

```python
    traefik_section = _section(data, "traefik")
    traefik = TraefikApiConfig(
        url=traefik_section.get("url") or None,
        cert=traefik_section.get("cert") or None,
        key=traefik_section.get("key") or None,
        ca=traefik_section.get("ca") or None,
    )
```

In `traefik_parse.py`:

```python
def parse_api_rawdata(payload: dict) -> set[str]:
    """Router names Traefik actually accepted, from /api/rawdata.

    Names carry a provider suffix there (kafbat-ui@swarm); the labels do not,
    so it is stripped for comparison.
    """
    accepted: set[str] = set()
    for name, spec in ((payload or {}).get("routers") or {}).items():
        if isinstance(spec, dict) and spec.get("status") != "enabled":
            continue
        accepted.add(str(name).split("@", 1)[0])
    return accepted
```

In `traefik.py`:

```python
def mark_rejected(info: TraefikInfo, accepted: set[str]) -> None:
    """Flag routers Traefik never accepted. Only call after really asking it."""
    info.api_consulted = True
    for router in info.routers:
        router.rejected = router.name not in accepted


def fetch_accepted(cfg) -> set[str] | None:
    """Ask Traefik what it accepted, or None when not configured or reachable."""
    api = getattr(cfg, "traefik", None)
    if not api or not api.url or not api.cert:
        return None
    try:
        import httpx

        response = httpx.get(
            api.url,
            cert=(api.cert, api.key) if api.key else api.cert,
            verify=api.ca or True,
            timeout=5.0,
        )
        response.raise_for_status()
        return parse_api_rawdata(response.json())
    except Exception:
        # Unreachable is not the same as "rejected everything": leave the
        # routers unconsulted rather than marking them all rejected.
        return None
```

Import `parse_api_rawdata` alongside the other parsers.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/config.py src/terminal_status_panel/collectors/traefik.py \
        src/terminal_status_panel/collectors/traefik_parse.py pyproject.toml tests/
git commit -m "feat: optionally cross-check the wiring against Traefik's runtime view"
```

---

### Task 8: Wire the section in, and document it

**Files:**
- Modify: `src/terminal_status_panel/render/layout.py`, `src/terminal_status_panel/cli.py`, `pyproject.toml`, `src/terminal_status_panel/install.py`
- Modify: `README.md`
- Test: `tests/test_render_layout.py`, `tests/test_cli.py`, `tests/test_install.py` (append)

**Interfaces:**
- Consumes: `traefik_section` (Task 6), `collect_traefik`/`fetch_accepted`/`mark_rejected` (Tasks 5, 7).
- Produces: `"traefik"` in `layout.SECTIONS`, `cli.traefik_main`, the `status-traefik` console script, and `"traefik"` in `install.py`'s `PANELS`.

**The section must not join `status-full`'s default.** Nine entrypoints and their routers would bury the login banner. `SECTIONS` lists it so `--sections traefik` works and the layout can build it; the default tuple passed by `main()` stays `("server", "docker", "health")`.

Read how `health` was wired — same shape, one section along.

`install.py` derives its `--panel` choices from `PANELS`, so adding the entry is all that is needed there. A previous task in this repo found that gap the hard way; do not repeat it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_layout.py  (append)
def test_traefik_is_a_known_section():
    assert "traefik" in SECTIONS


def test_traefik_is_not_in_the_default_full_panel():
    """Nine entrypoints would bury the login banner."""
    from terminal_status_panel import cli

    assert "traefik" not in cli.DEFAULT_SECTIONS
```

```python
# tests/test_cli.py  (append)
def test_traefik_main_returns_zero(isolated_cli):
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: None)
    assert cli.traefik_main([]) == 0


def test_collect_all_skips_traefik_when_not_selected(isolated_cli):
    called = []
    isolated_cli.setattr(cli, "collect_traefik", lambda *a, **k: called.append(True))
    cli.collect_all(Config(), sections=("server",))
    assert called == []
```

```python
# tests/test_install.py  (append)
def test_install_panel_can_wire_up_traefik(tmp_path, monkeypatch):
    """install.py derives --panel choices from PANELS; a missing entry made
    --panel health unusable once already."""
    from terminal_status_panel import install

    assert install.PANELS["traefik"] == "status-traefik"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_layout.py tests/test_cli.py tests/test_install.py -k traefik -v`
Expected: FAIL — `'traefik' not in SECTIONS`, `KeyError: 'traefik'`.

- [ ] **Step 3: Write the implementation**

In `layout.py`, import `traefik_section`, add `"traefik"` to `SECTIONS`, and register a builder:

```python
def traefik_block(data: PanelData, cfg: Config) -> RenderableType:
    """The TRAEFIK WIRING block."""
    return traefik_section(data.traefik, cfg, data.swarm)
```

In `cli.py`, introduce an explicit default so the full panel's contents are stated in one place rather than implied by `SECTIONS`:

```python
DEFAULT_SECTIONS: tuple[str, ...] = ("server", "docker", "health")
```

Use it as `main`'s default instead of `SECTIONS`, gate collection on `"traefik" in sections`, and add:

```python
def traefik_main(argv: list[str] | None = None) -> int:
    """Entry point for ``status-traefik`` — the wiring viewer only."""
    return main(argv, sections=("traefik",), prog="status-traefik")
```

The traefik branch of `collect_all` mirrors the health one: build the client, call `collect_traefik`, then — only when configured — `fetch_accepted` and `mark_rejected`.

Register `status-traefik = "terminal_status_panel.cli:traefik_main"` in `pyproject.toml` and `"traefik": "status-traefik"` in `install.py`'s `PANELS`.

- [ ] **Step 4: Document it in `README.md`**

Add the section to the intro list, `status-traefik` to the commands table and the usage synopsis, and the `[traefik]` block to the configuration reference.

State the limit plainly, in the same place as the feature: the viewer shows the wiring **as configured**. A label with a typo appears here even though Traefik rejected it. The optional `[traefik]` cross-check is the answer, and it needs a client certificate signed by the webfe CA — which the app servers do not have today, because the Ansible role issues only app-server TinyCA certificates for Traefik→service mTLS. Say that rather than letting a reader configure something that cannot work.

Mention the orphan block and what it is for, using the real case: a router naming an entrypoint that does not exist would otherwise vanish from a tree keyed by entrypoint.

- [ ] **Step 5: Run the full suite, lint and commit**

```bash
.venv/bin/python -m pytest -q
uvx ruff@0.16 check src tests
git add src/terminal_status_panel/render/layout.py src/terminal_status_panel/cli.py \
        src/terminal_status_panel/install.py pyproject.toml README.md tests/
git commit -m "feat: wire the Traefik section into the CLI and document it"
```

---

## Self-Review

**Spec coverage.** The three Docker sources → Tasks 2, 3, 4. Assembly and orphan detection → Task 5. The tree, the no-router line, dimmed internal routers and the orphan block → Task 6. The optional API path with its dormancy stated → Task 7. Section registration, `status-traefik`, `install.py`, and the intent-versus-runtime limit in the README → Task 8. The model underpinning all of it → Task 1.

**Type consistency.** `parse_entrypoints(args) -> list[TraefikEntrypoint]`, `parse_labels(labels, origin) -> tuple[list, dict, dict]`, `parse_dynamic_yaml(text, origin) -> tuple[list, dict]` and `parse_api_rawdata(payload) -> set[str]` are defined in Tasks 2–4 and 7 and called with exactly those signatures in Task 5 and 7. `unknown_entrypoints(router, known)` is defined in Task 5 and used in Task 6. `traefik_section(info, cfg, swarm)` is defined in Task 6 and called in Task 8.

**Two new dependencies**, both in the tasks that need them: `pyyaml` in Task 4, `httpx` in Task 7. Neither is optional at import time in the modules that use them, so both must be real dependencies rather than extras.

**One risk worth naming.** Task 6's renderer reaches into `render/panels.py` for `section` and `_subhead`, the second of which is private. That is the established pattern — `render/health.py` defines its own `_subhead` rather than importing it. The implementer should follow whichever `health.py` does and say which, rather than inventing a third arrangement.
