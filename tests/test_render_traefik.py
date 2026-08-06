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


def _branch_heads(out: str, router: str) -> int:
    """How many entrypoint branches the router heads.

    Counting bare name occurrences cannot tell one branch from two: a router
    under a *single* entrypoint already prints its name twice, on the ``└─
    router`` line and on the ``└─ → service`` line below it. Only the branch
    head is counted, and by substring rather than per line — the entrypoints
    render side by side, so one line can carry two branches.
    """
    return out.count(f"\u2514\u2500 {router}")


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


def test_entrypoints_keep_the_order_the_collector_gives_them():
    """Declaration order, not port order: the parser preserves the arguments'
    own grouping and the renderer must not undo it."""
    out = _render(_wired(), width=60)
    assert out.index(":2006") < out.index(":2020") < out.index(":443")


def test_a_router_on_two_entrypoints_appears_under_both():
    out = _render(_wired())
    assert _branch_heads(out, "kafbat-ui") == 2


def test_an_entrypoint_without_a_router_says_so():
    out = _render(_wired())
    https_line = [ln for ln in out.splitlines() if "https" in ln][0]
    assert "no router" in https_line


def test_an_orphaned_router_gets_its_own_block_naming_the_entrypoint():
    info = _wired()
    info.routers.append(TraefikRouter(
        name="image_api", entrypoints=["websecure"],
        rule="Host(`www.example.net`)", service="image_api",
        origin="mystack_image_api"))
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
    # Once under the entrypoint that exists, once in the orphan block.
    assert _branch_heads(out, "half") == 1
    orphan_line = [ln for ln in out.splitlines() if "nosuch" in ln][0]
    assert "half" in orphan_line


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


def test_swarm_none_shows_unknown_not_missing():
    """swarm=None means nobody looked at Docker: claiming the service does not
    exist would be an unmeasured verdict, exactly the defect this project is
    closing. It must render the neutral dot, never the failed-check icon."""
    out = _render(_wired(), swarm=None)
    assert icons.UNKNOWN in out
    assert icons.FAILED not in out


def test_a_router_naming_no_entrypoint_appears_under_every_entrypoint():
    """Traefik attaches an entrypoint-less router to all of them (confirmed by
    collectors.traefik.unknown_entrypoints, which never calls it an orphan).
    A tree keyed by entrypoint must not drop it just because it names none."""
    info = TraefikInfo(
        reachable=True,
        entrypoints=[
            TraefikEntrypoint(name="portalmgmt", address=":2020", port=2020),
            TraefikEntrypoint(name="kafbat", address=":2006", port=2006),
        ],
        routers=[TraefikRouter(name="everywhere", entrypoints=[], service="everywhere")],
    )
    out = _render(info)
    assert _branch_heads(out, "everywhere") == 2
    assert "ORPHANED" not in out


def test_without_entrypoints_the_routers_are_still_shown_under_a_warning():
    """Not finding the entrypoints is a coverage gap, not an empty panel.

    It happens whenever the Traefik service is not matched by
    TRAEFIK_SERVICE_PATTERNS, or its entrypoints come from static YAML rather
    than from Args. Dropping every router in that state is the very blind spot
    the orphan block exists to close.
    """
    info = TraefikInfo(
        reachable=True,
        entrypoints=[],
        routers=[
            TraefikRouter(name="alpha", entrypoints=["portalmgmt"], service="alpha"),
            TraefikRouter(name="beta", entrypoints=["kafbat"], service="beta"),
        ],
    )
    out = _render(info)
    assert icons.WARN in out
    assert "alpha" in out
    assert "beta" in out


def test_without_entrypoints_no_entrypoint_is_called_nonexistent():
    """With nothing read, "entrypoint `x` does not exist" would be a claim
    about something that was never measured."""
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="alpha", entrypoints=["portalmgmt"], service="alpha")],
    )
    out = _render(info)
    assert "does not exist" not in out
    assert icons.FAILED not in out


def test_without_entrypoints_even_an_entrypoint_less_router_is_shown():
    """A router naming no entrypoint is attached to all of them — and with none
    read there is no branch for it either, so it is the one router the orphan
    block would still drop."""
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="everywhere", service="everywhere")],
    )
    assert "everywhere" in _render(info)


def test_an_unreachable_swarm_shows_unknown_not_a_missing_service():
    """`reachable=False` means collect_docker never got an answer: the service
    list is empty because nothing was measured, not because the service is
    gone."""
    out = _render(_wired(), swarm=SwarmInfo(reachable=False))
    assert icons.UNKNOWN in out
    assert icons.FAILED not in out


def test_a_swarm_that_is_not_enabled_but_holds_no_matching_container_is_reported_missing():
    """`enabled=False` no longer means nothing can match: containers live in
    their own field now, searched alongside services. With neither holding the
    target, a reachable daemon really did not find it, and saying so is
    correct rather than premature."""
    out = _render(_wired(), swarm=SwarmInfo(reachable=True, enabled=False))
    assert icons.FAILED in out
    assert "no such service" in out


def test_a_router_pointing_at_a_compose_container_is_confirmed():
    """A Compose host has no Swarm services; its targets live in `containers`."""
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="web", service="web", entrypoints=["https"])],
        services={"web": TraefikServiceRef(name="web", docker_service="web")},
    )
    swarm = SwarmInfo(
        reachable=True, enabled=False,
        containers=[ServiceStatus(name="web", running_replicas=1, desired_replicas=1)],
    )
    assert "no such service" not in _render(info, swarm)


def test_a_router_pointing_nowhere_is_still_reported_missing():
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="web", service="web", entrypoints=["https"])],
        services={"web": TraefikServiceRef(name="web", docker_service="web")},
    )
    swarm = SwarmInfo(
        reachable=True, enabled=False,
        containers=[ServiceStatus(name="something-else", running_replicas=1,
                                  desired_replicas=1)],
    )
    assert "no such service" in _render(info, swarm)


def test_nothing_is_claimed_when_docker_was_not_reached():
    """Unmeasured is not the same as missing."""
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="web", service="web", entrypoints=["https"])],
        services={"web": TraefikServiceRef(name="web", docker_service="web")},
    )
    assert "no such service" not in _render(info, SwarmInfo(reachable=False))
    assert "no such service" not in _render(info, None)


def test_a_swarm_service_target_still_matches():
    """The pre-existing path must not regress."""
    info = TraefikInfo(
        reachable=True,
        routers=[TraefikRouter(name="api", service="api", entrypoints=["https"])],
        services={"api": TraefikServiceRef(name="api", docker_service="api")},
    )
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_count=1,
        services=[ServiceStatus(name="api", running_replicas=1, desired_replicas=1)],
    )
    assert "no such service" not in _render(info, swarm)


def test_a_global_service_uses_the_reported_node_count_like_docker_infos():
    """`_node_map` swallows a failed node listing, so an empty node list beside
    a non-zero count is normal. Counting only the list renders `· 0/0` here and
    `💀 0/3` in DOCKER INFOS — two verdicts for one service."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_count=3, nodes=[],
        services=[ServiceStatus("kafbat-ui_kafbat-ui", 0, None)],
    )
    out = _render(_wired(), swarm=swarm)
    assert f"{icons.DEAD} 0/3" in out


def test_a_router_traefik_rejected_carries_a_marker():
    info = _wired()
    info.api_consulted = True
    info.routers[0].rejected = True
    out = _render(info)
    router_line = [ln for ln in out.splitlines() if "└─ kafbat-ui" in ln][0]
    assert icons.DEAD in router_line
    assert "rejected" in router_line


def test_a_router_traefik_accepted_gets_no_second_checkmark():
    """The tree already reads as configured-and-accepted; a marker for the
    normal case would be noise on every line."""
    info = _wired()
    info.api_consulted = True
    info.routers[0].rejected = False
    assert _render(info) == _render(_wired())


def test_an_unconsulted_router_renders_exactly_as_before():
    """`rejected is None` means nobody asked Traefik — nothing may be implied
    in either direction."""
    info = _wired()
    info.routers[0].rejected = None
    assert _render(info) == _render(_wired())


def test_a_stale_rejected_flag_without_a_consultation_implies_nothing():
    info = _wired()
    info.api_consulted = False
    info.routers[0].rejected = True
    assert _render(info) == _render(_wired())


def test_a_rejected_router_is_not_reported_when_traefik_gave_no_status():
    """End to end over the two halves of item 7: an unreadable spec must not
    reach the tree as a measured failure."""
    from terminal_status_panel.collectors.traefik import mark_rejected
    from terminal_status_panel.collectors.traefik_parse import parse_api_rawdata

    info = _wired()
    mark_rejected(info, parse_api_rawdata({"routers": {"kafbat-ui@swarm": {}}}))
    assert info.routers[0].rejected is False
    assert "rejected" not in _render(info)


def test_file_provider_error_is_shown_as_a_warning_above_the_tree():
    info = _wired()
    info.file_provider_error = "ConnectionError: configs.list() failed"
    out = _render(info)
    lines = out.splitlines()
    warning_idx = next(i for i, ln in enumerate(lines) if "file provider unreadable" in ln)
    assert "ConnectionError: configs.list() failed" in lines[warning_idx]
    # The tree still renders beneath the warning.
    tree_idx = next(i for i, ln in enumerate(lines) if "kafbat-ui" in ln)
    assert tree_idx > warning_idx


def test_the_entrypoints_flow_into_columns_on_a_wide_terminal():
    """Stacked vertically they run to some seventy lines on cluster-a while two
    thirds of the terminal stay empty — the same arrangement CLUSTER HEALTH
    uses."""
    wide = _render(_wired(), width=200)
    narrow = _render(_wired(), width=40)
    assert len(wide.splitlines()) < len(narrow.splitlines())
    # Side by side, one line carries two entrypoint heads.
    assert any("kafbat" in line and "portalmgmt" in line for line in wide.splitlines())


def test_an_entrypoint_head_carries_the_worst_verdict_below_it():
    """A wall of branches has to say at a glance which one to read first."""
    info = _wired()
    info.routers.append(TraefikRouter(
        name="broken", entrypoints=["portalmgmt"], service="gone"))
    swarm = SwarmInfo(reachable=True, enabled=True, services=[
        ServiceStatus("kafbat-ui_kafbat-ui", 1, 1,
                      tasks=[ServiceTask("srv-01", "running")]),
    ])
    out = _render(info, swarm=swarm, width=60)
    heads = [ln for ln in out.splitlines() if ":2020" in ln]
    assert icons.FAILED in heads[0]


def test_the_ping_entrypoint_is_not_reported_as_an_unserved_port():
    """`--ping.entryPoint=ping` makes Traefik answer /ping there itself. It is
    the one entrypoint that is supposed to look empty."""
    info = _wired()
    info.entrypoints.append(TraefikEntrypoint(name="ping", address=":8080", port=8080))
    info.ping_entrypoint = "ping"
    out = _render(info, width=60)
    ping_line = [ln for ln in out.splitlines() if ln.startswith("ping  :8080")][0]
    assert "health check" in ping_line
    assert "no router" not in ping_line


def test_an_entrypoint_that_is_not_the_ping_one_still_reads_as_a_finding():
    info = _wired()
    info.ping_entrypoint = "ping"
    out = _render(info, width=60)
    https_line = [ln for ln in out.splitlines() if ":443" in ln][0]
    assert "no router" in https_line


def test_a_file_provider_service_is_not_reported_as_a_missing_docker_service():
    """`account-api-placeholder` is declared in the file provider and never in
    Swarm. Matching it against Swarm service names reports a service missing
    that was never supposed to be there."""
    info = TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="login_example_net", address=":2009",
                                       port=2009)],
        routers=[TraefikRouter(name="account-api", entrypoints=["login_example_net"],
                               rule="PathPrefix(`/api`)",
                               service="account-api-placeholder", source="file")],
        services={"account-api-placeholder": TraefikServiceRef(
            name="account-api-placeholder", source="file",
            upstreams=["http://user-account.internal"])},
    )
    out = _render(info, swarm=SwarmInfo(reachable=True, enabled=True, services=[]),
                  width=100)
    assert "no such service" not in out
    assert "http://user-account.internal" in out
    assert icons.UNKNOWN in out


def test_an_orphaned_router_names_the_service_carrying_the_label():
    """The block says which router and which entrypoint, but the label lives on
    a Docker service — without its name you cannot go and fix it."""
    info = _wired()
    info.routers.append(TraefikRouter(
        name="image_api", entrypoints=["websecure"],
        rule="Host(`www.example.net`)", service="image_api",
        origin="mystack_image_api"))
    out = _render(info, width=120)
    assert "mystack_image_api" in out


def test_an_orphaned_router_without_an_origin_says_nothing_extra():
    """`origin` is optional. An absent one must not render as an empty pair of
    brackets pretending to be an answer."""
    info = _wired()
    info.routers.append(TraefikRouter(name="nameless", entrypoints=["websecure"]))
    out = _render(info, width=120)
    assert "()" not in out


def test_a_router_naming_an_unknown_middleware_is_marked():
    """A typo in a middleware reference otherwise reads exactly like a
    reference that resolved."""
    info = _wired()
    info.routers[0].middlewares = ["image_api_stripprefix"]
    out = _render(info, width=120)
    assert "no such middleware" in out
    assert icons.FAILED in out


def test_a_middleware_reference_with_a_provider_suffix_still_resolves():
    """Traefik writes `name@provider` when a router references a middleware
    from another provider; the name before the @ is the one we parsed."""
    from terminal_status_panel.model import TraefikMiddleware

    info = _wired()
    info.middlewares["strip"] = TraefikMiddleware(name="strip", kind="stripprefix")
    info.routers[0].middlewares = ["strip@swarm"]
    out = _render(info, width=120)
    assert "no such middleware" not in out
    assert "stripprefix" in out
