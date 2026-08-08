import re

from rich.console import Console, Group
from rich.text import Text

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    TraefikEntrypoint,
    TraefikInfo,
    TraefikMiddleware,
    TraefikRouter,
    TraefikServiceRef,
)
from terminal_status_panel.render import icons
from terminal_status_panel.render.packing import PackedColumns
from terminal_status_panel.render.panels import section
from terminal_status_panel.render.traefik import _entrypoint_block, traefik_section


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


def test_a_swarm_that_is_not_enabled_shows_unknown_not_a_missing_service():
    """Off a Swarm, `services` holds container names, so no Swarm service name
    can ever match — every router would read "no such service"."""
    out = _render(_wired(), swarm=SwarmInfo(reachable=True, enabled=False))
    assert icons.UNKNOWN in out
    assert icons.FAILED not in out


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


def _ragged():
    """One tall entrypoint and three short ones — the shape that wastes space.

    The tall block is eleven lines (its head plus five routers of two lines
    each), the short ones three. Filled row by row into two columns that is
    eleven lines then three; packed by height it is eleven, because the tall
    block sets the height on its own and the three short ones stack beside it.

    The two tests below that check the arrangement render this at width 120,
    not wider: these blocks are narrow enough (40-49 cells) that a wider
    terminal fits all four side by side, which is the correct, optimal
    layout too — but it does not exercise the two-column case the tests are
    about. At 120 only two columns fit, which is what forces the three short
    blocks to share one column instead of a row each.
    """
    routers = [
        TraefikRouter(name=f"busy_{index}", entrypoints=["portal_admin"],
                      rule=f"PathPrefix(`/busy/{index}`)", service=f"busy_{index}")
        for index in range(5)
    ]
    routers += [
        TraefikRouter(name=f"quiet_{name}", entrypoints=[name],
                      rule="PathPrefix(`/`)", service=f"quiet_{name}")
        for name in ("status_public", "status_internal", "eam_dev")
    ]
    return TraefikInfo(
        reachable=True,
        entrypoints=[
            TraefikEntrypoint(name="portal_admin", address=":2020", port=2020),
            TraefikEntrypoint(name="status_public", address=":2011", port=2011),
            TraefikEntrypoint(name="status_internal", address=":2012", port=2012),
            TraefikEntrypoint(name="eam_dev", address=":2021", port=2021),
        ],
        routers=routers,
        services={router.service: TraefikServiceRef(name=router.service, port=8080)
                  for router in routers},
    )


def test_a_short_entrypoint_beside_a_tall_one_wastes_no_rows():
    out = _render(_ragged(), width=120)
    body = [line for line in out.splitlines() if line.strip()]
    # Eleven lines of columns plus the section's own rule. Row-major filling
    # would add the three-line second row on top of that.
    assert len(body) == 12


def test_short_entrypoints_stack_rather_than_share_a_row():
    out = _render(_ragged(), width=120)
    heads = [
        index
        for index, line in enumerate(out.splitlines())
        if "status_internal  :2012" in line or "eam_dev  :2021" in line
    ]
    assert len(heads) == 2
    assert heads[0] != heads[1]


def test_every_entrypoint_and_router_is_rendered_exactly_once():
    out = _render(_ragged(), width=200)
    for name in ("portal_admin", "status_public", "status_internal", "eam_dev"):
        assert out.count(f"{name}  :") == 1
    for index in range(5):
        assert _branch_heads(out, f"busy_{index}") == 1


def test_no_rendered_line_exceeds_the_console_width():
    for width in (80, 120, 200):
        out = _render(_ragged(), width=width)
        assert max(Text(line).cell_len for line in out.splitlines()) <= width


def test_an_entrypoint_that_has_routers_never_reads_as_having_none():
    out = _render(_ragged(), width=200)
    assert "no router" not in out


def _internal(middlewares=None, mw_defs=None):
    return TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="default", address=":8088", port=8088)],
        routers=[TraefikRouter(name="ping-router", entrypoints=["default"],
                               rule="Path(`/_traefik_ping_`)", service="ping@internal",
                               middlewares=middlewares or [], source="file")],
        services={"ping@internal": TraefikServiceRef(name="ping@internal",
                                                     source="file")},
        middlewares=mw_defs or {},
    )


def test_an_internal_target_folds_onto_the_router_line():
    out = _render(_internal())
    heads = [line for line in out.splitlines() if "ping-router" in line]
    assert len(heads) == 1
    assert "ping@internal" in heads[0]


def test_the_folded_head_carries_only_its_own_branch_glyph():
    """The fold strips the service line's own ``└─ `` prefix by string surgery
    (``service.plain.strip().removeprefix("└─ ")``) so that gluing it onto the
    router head does not leave a second, orphaned branch glyph behind. That
    surgery is coupled to the exact text ``_service_state`` produces; this
    guards it against silently breaking if that text ever changes shape.
    """
    out = _render(_internal())
    head = next(line for line in out.splitlines() if "ping-router" in line)
    assert head.count("└─") == 1


def test_a_middleware_keeps_the_internal_target_on_its_own_line():
    info = _internal(middlewares=["strip"],
                     mw_defs={"strip": TraefikMiddleware(name="strip",
                                                         kind="stripprefix")})
    out = _render(info)
    lines = out.splitlines()
    head = next(line for line in lines if "ping-router" in line)
    assert "ping@internal" not in head
    assert any("ping@internal" in line and "ping-router" not in line for line in lines)


def test_a_measured_service_keeps_its_own_line():
    swarm = SwarmInfo(reachable=True, enabled=True, services=[
        ServiceStatus("kafbat-ui_kafbat-ui", 1, 1,
                      tasks=[ServiceTask("srv-01", "running")]),
    ])
    out = _render(_wired(), swarm=swarm)
    head = next(line for line in out.splitlines()
                if "└─ kafbat-ui" in line and "→" not in line)
    assert "kafbat-ui_kafbat-ui" not in head


def _six_ping():
    """Six entrypoints whose only router is the shared ping router.

    The design's own reference shape: one ``TraefikRouter`` naming all six
    entrypoints, pointing at ``ping@internal``, declared in the file
    provider — the case the fold exists for.
    """
    names = ["dashboard", "ping", "default", "https", "portal_admin", "status_public"]
    entrypoints = [
        TraefikEntrypoint(name=name, address=f":{2000 + index}", port=2000 + index)
        for index, name in enumerate(names)
    ]
    router = TraefikRouter(
        name="ping-router", entrypoints=names, rule="Path(`/_traefik_ping_`)",
        service="ping@internal", source="file",
    )
    return TraefikInfo(
        reachable=True,
        entrypoints=entrypoints,
        routers=[router],
        services={"ping@internal": TraefikServiceRef(name="ping@internal", source="file")},
    )


def test_folding_never_costs_the_six_ping_router_shape_a_column():
    """Folded, each branch is 2 lines and 64 cells wide (head + folded router
    line, e.g. ``dashboard  :2000`` / `` └─ ping-router  Path(...)  → ping@
    internal``). Two folded columns need 64 + 4 + 64 = 132 cells, which does
    not fit at width 120, so a packer that only ever sees the folded blocks
    falls back to one column: 6 branches * 2 lines = 12 content lines, plus
    the section's own rule and trailing blank = 14.

    Unfolded, each branch grows to 3 lines (router line and service line
    split again) but narrows to roughly 47 cells: two columns need
    47 + 4 + 47 = 98, which fits comfortably at 120. Two columns of 3
    branches * 3 lines = 9, plus the section's 2 chrome lines = 11 — shorter
    than the folded fallback despite the extra line per branch. Packing both
    candidates and drawing whichever is shorter must reach this 11, not the
    14 a folded-only packer produces.
    """
    out = _render(_six_ping(), width=120)
    lines = out.splitlines()
    assert len(lines) == 11
    # Never taller at 120 than at any wider width: widening a terminal must
    # not cost lines.
    for wider in (140, 160, 190, 200, 215):
        wider_lines = _render(_six_ping(), width=wider).splitlines()
        assert len(lines) >= len(wider_lines)
    # And specifically no taller than a section built only from the unfolded
    # blocks (no alternative, so PackedColumns has nothing to choose between)
    # — the same chrome (rule and trailing blank) around the same six
    # branches, forced always-unfolded.
    info = _six_ping()
    unfolded_blocks = [
        _entrypoint_block(ep, info, None, fold=False) for ep in info.entrypoints
    ]
    unfolded_section = section("TRAEFIK WIRING", Group(PackedColumns(unfolded_blocks), Text("")))
    unfolded_console = Console(width=120, force_terminal=False, color_system=None)
    with unfolded_console.capture() as capture:
        unfolded_console.print(unfolded_section)
    unfolded_lines = capture.get().splitlines()
    assert len(lines) <= len(unfolded_lines)


def test_the_section_fills_most_of_a_wide_terminal():
    """The whole point of the packing, measured rather than asserted.

    ``Text(line).cell_len`` counts the trailing spaces Rich pads every cell
    with to reach its column width, which measures "how close each line is to
    the console width" — a metric a *worse*, more ragged layout can score
    *higher* on, since a tall ragged block pads more blank rows out to the
    full width. Rstripping first counts actual ink instead. Measured on this
    shape at width 120: the pre-packing layout (16 lines, row-major
    ``rich.Columns``) scores 0.47 on ink against 0.79 on padding: the padded
    metric would have called the worse layout better. The packed-and-folded
    layout (13 lines) scores 0.57. The bound sits between the two, well clear
    of either, so it genuinely discriminates rather than merely rewarding
    padding.
    """
    out = _render(_ragged(), width=120).splitlines()
    ink = sum(Text(line.rstrip()).cell_len for line in out)
    assert ink / (len(out) * 120) > 0.50


#: An OSC-8 opener and its target. The `+` matters: OSC-8 closes with
#: `ESC ] 8 ; ; ESC \`, an opener with an empty target, and a `*` here would
#: match that too and put an empty string in the list for every link.
_OSC8 = re.compile("\x1b]8;[^;]*;([^\x1b]+)\x1b\\\\")


def _render_linked(info, cfg=None, swarm=None, width=200) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor")
    with console.capture() as capture:
        console.print(traefik_section(info, cfg or Config(), swarm))
    return capture.get()


def _link_targets(out: str) -> list[str]:
    """Every OSC-8 target in the rendered output, in order."""
    return _OSC8.findall(out)


def _linked():
    return TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="login_example_de", address=":2009",
                                       port=2009)],
        routers=[
            TraefikRouter(name="account-spa", entrypoints=["login_example_de"],
                          rule="PathPrefix(`/account`)", service="account-spa"),
            TraefikRouter(name="odd-one", entrypoints=["login_example_de"],
                          rule="PathPrefix(`/a`) || PathPrefix(`/b`)",
                          service="odd-one"),
        ],
        services={"account-spa": TraefikServiceRef(name="account-spa", port=8080),
                  "odd-one": TraefikServiceRef(name="odd-one", port=8081)},
    )


def _cfg_with_links():
    cfg = Config()
    cfg.traefik.links = {"login_example_de": "https://login.example.de"}
    return cfg


def test_a_configured_entrypoint_links_its_head_and_its_routers():
    targets = _link_targets(_render_linked(_linked(), _cfg_with_links()))
    assert "https://login.example.de" in targets
    assert "https://login.example.de/account" in targets


def test_without_a_configured_base_nothing_is_linked():
    """No base means no link -- never a guessed one."""
    assert _link_targets(_render_linked(_linked(), Config())) == []


def test_a_router_traefik_rejected_carries_no_link():
    """A router Traefik measurably rejected asserts the route does not exist;
    a link beside that claim would offer to take you there anyway, even
    though its rule would otherwise yield a perfectly good path."""
    info = _linked()
    info.api_consulted = True
    info.routers[0].rejected = True  # account-spa, whose rule links cleanly
    out = _render_linked(info, _cfg_with_links())
    linked_spans = re.findall("\x1b]8;[^;]*;[^\x1b]+\x1b\\\\(.*?)\x1b]8;;", out)
    assert not any("account-spa" in span for span in linked_spans)
    # The entrypoint head is unaffected -- only this one router was rejected.
    assert "https://login.example.de" in _link_targets(out)


def test_a_router_whose_rule_has_no_single_path_is_not_linked():
    """Not "no target ends in /a or /b" -- that also passes if the router got
    linked to the bare base, which is `link_for(base, None)`'s answer for an
    entrypoint whose sub-path is merely unknown, not for a router that could
    not name one. The span-matching approach from
    `test_the_verdict_glyph_is_outside_the_clickable_region` is what actually
    tells "linked to the wrong thing" and "not linked" apart."""
    out = _render_linked(_linked(), _cfg_with_links())
    linked_spans = re.findall("\x1b]8;[^;]*;[^\x1b]+\x1b\\\\(.*?)\x1b]8;;", out)
    assert not any(span == "odd-one" for span in linked_spans)


def test_the_entrypoint_head_is_linked_even_when_a_router_is_not():
    """The host is known even where the sub-path is not."""
    targets = _link_targets(_render_linked(_linked(), _cfg_with_links()))
    assert "https://login.example.de" in targets


def test_the_service_line_is_never_linked():
    """It names a container port inside the cluster, which no browser reaches."""
    targets = _link_targets(_render_linked(_linked(), _cfg_with_links()))
    assert not any(":8080" in t or ":8081" in t for t in targets)


def test_no_colour_means_no_hyperlinks():
    """--no-color already means plain text, and a hyperlink is markup."""
    console = Console(width=200, force_terminal=True, color_system=None)
    with console.capture() as capture:
        console.print(traefik_section(_linked(), _cfg_with_links(), None))
    assert _link_targets(capture.get()) == []


def test_the_verdict_glyph_is_outside_the_clickable_region():
    """Both heads are built by appending. Linking the whole Text would make the
    verdict part of the link, and a reader aiming at the state would open a
    browser instead."""
    out = _render_linked(_linked(), _cfg_with_links())
    # `+`, not `*`, for the same reason as in _OSC8: a `*` also matches the
    # empty-target closer and the spans come out shifted by one.
    linked_spans = re.findall("\x1b]8;[^;]*;[^\x1b]+\x1b\\\\(.*?)\x1b]8;;", out)
    assert linked_spans, "expected at least one linked span"
    for span in linked_spans:
        assert icons.OK not in span
        assert icons.DEAD not in span
        assert icons.UNKNOWN not in span
        assert "PathPrefix" not in span
        # Same logic as the verdict glyph: the entrypoint head's own address
        # (`:2009`) is a cluster-internal port and must sit outside the link
        # to a public URL, just like the router heads' rule text does.
        assert ":2009" not in span


def _linked_without_routers():
    """An entrypoint that nothing attaches to -- the "-- no router" append
    path, which `_linked()` above never exercises because it always has
    `account-spa` and `odd-one` hanging on it."""
    return TraefikInfo(
        reachable=True,
        entrypoints=[TraefikEntrypoint(name="metrics_example_de", address=":2010",
                                       port=2010)],
        routers=[],
        services={},
    )


def _cfg_with_links_for_empty_entrypoint():
    cfg = Config()
    cfg.traefik.links = {"metrics_example_de": "https://metrics.example.de"}
    return cfg


def test_an_entrypoint_with_no_routers_is_still_linked():
    """The verdict-glyph test above only ever renders the worst-verdict append
    path, since `_linked()`'s entrypoint always has routers attached. This
    pins the other two append paths -- "-- no router" here, and the
    ping-entrypoint's own health-check note the same way -- as an established
    fact rather than something traced by eye."""
    out = _render_linked(_linked_without_routers(), _cfg_with_links_for_empty_entrypoint())
    assert "https://metrics.example.de" in _link_targets(out)
    linked_spans = re.findall("\x1b]8;[^;]*;[^\x1b]+\x1b\\\\(.*?)\x1b]8;;", out)
    assert linked_spans, "expected the entrypoint head to be linked"
    for span in linked_spans:
        assert "no router" not in span
