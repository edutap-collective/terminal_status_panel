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
    assert out.count("everywhere") >= 2
    assert "ORPHANED" not in out


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
