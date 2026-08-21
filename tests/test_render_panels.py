from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ClusterService,
    FilesystemUsage,
    HealthInfo,
    ProcessInfo,
    ProcessSnapshot,
    ResourceUsage,
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    SwarmNode,
    SystemInfo,
    UpdateInfo,
)
from terminal_status_panel.render import icons, panels
from terminal_status_panel.render.panels import system_status


def _text(renderable, width=100) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_system_overview_shows_fields():
    info = SystemInfo(
        hostname="srv01",
        os_name="Debian",
        os_version="12",
        kernel="6.1.0",
        uptime_seconds=90000,
        user="root",
        ip_addresses=["10.0.0.5"],
    )
    out = _text(panels.system_overview(info))
    assert "SYSTEM OVERVIEW" in out
    assert "srv01" in out
    assert "Debian" in out
    assert "10.0.0.5" in out


def test_system_overview_handles_none():
    out = _text(panels.system_overview(None))
    assert "not available" in out


def test_missing_os_identity_is_named_not_hidden():
    info = SystemInfo(
        hostname="host",
        os_name=None,
        os_version=None,
        kernel="Linux 6.1.0",
        uptime_seconds=60,
        user="root",
    )
    out = _text(panels.system_overview(info))
    assert "OS identity unavailable" in out


def test_memory_panel_renders_bars():
    res = ResourceUsage(
        mem_total=32_000_000_000,
        mem_used=20_400_000_000,
        mem_percent=64.0,
        swap_total=8_000_000_000,
        swap_used=600_000_000,
        swap_percent=8.0,
    )
    out = _text(panels.memory_panel(res, Config()))
    assert "RAM" in out
    assert "SWAP" in out
    assert "64" in out  # percent shown
    assert "█" in out  # bar drawn


def test_load_panel_shows_per_core():
    res = ResourceUsage(
        load_avg=(1.0, 0.7, 0.4),
        cpu_count=4,
        cpu_percent=12.3,
        cpu_per_core=[8.1, 11.4, 6.2, 9.3],
    )
    out = _text(panels.load_panel(res, Config()))
    assert "SYSTEM LOAD" in out
    assert "Load Average" in out
    assert "per core" in out
    assert "Core 1" in out
    assert "Core 4" in out
    assert "12.3%" in out


def test_filesystem_panel_is_a_table():
    res = ResourceUsage(
        filesystems=[
            FilesystemUsage("/", 230_000_000_000, 210_000_000_000, 91.0),
            FilesystemUsage("/data", 500_000_000_000, 120_000_000_000, 24.0),
        ]
    )
    out = _text(panels.filesystem_panel(res))
    assert "FILESYSTEM USAGE" in out
    assert "Size" in out
    assert "Use%" in out
    assert "/data" in out
    assert "91%" in out


def test_updates_panel_lists_counts():
    out = _text(
        panels.updates_panel(UpdateInfo(supported=True, available=12, security=5, standard=7))
    )
    assert "Available updates" in out
    assert "Security updates" in out
    assert "12" in out
    assert "5" in out


def test_updates_panel_unsupported():
    out = _text(panels.updates_panel(UpdateInfo(supported=False)))
    assert "n/a" in out.lower()


def test_services_section_merges_per_node_replicas():
    N1, N2, N3 = "swarm01-mgr-01", "swarm01-wrk-01", "swarm01-wrk-02"
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=3,
        nodes=[
            SwarmNode(N1, reachable=True, role="manager", leader=True),
            SwarmNode(N2, reachable=True, role="worker"),
            SwarmNode(N3, reachable=False, role="worker", state="down"),
        ],
        services=[
            # One kafka service per node — must collapse to a single "kafka" row.
            ServiceStatus(
                f"kafka_kafka-{N1}",
                1,
                1,
                stack="kafka",
                description="Kafka broker",
                tasks=[ServiceTask(N1, "running")],
            ),
            ServiceStatus(
                f"kafka_kafka-{N2}",
                1,
                1,
                stack="kafka",
                description="Kafka broker",
                tasks=[ServiceTask(N2, "running")],
            ),
            ServiceStatus(
                f"kafka_kafka-{N3}",
                1,
                1,
                stack="kafka",
                description="Kafka broker",
                tasks=[ServiceTask(N3, "failed")],
            ),
            # PostgreSQL: per-node pg replicas + a distinct monitor service.
            ServiceStatus(
                f"PostgreSQL-18_pg-{N1}",
                1,
                1,
                stack="PostgreSQL-18",
                description="PG",
                tasks=[ServiceTask(N1, "running")],
            ),
            ServiceStatus(
                f"PostgreSQL-18_pg-{N2}",
                1,
                1,
                stack="PostgreSQL-18",
                description="PG",
                tasks=[ServiceTask(N2, "running")],
            ),
            ServiceStatus(
                "PostgreSQL-18_pg-monitor",
                1,
                1,
                stack="PostgreSQL-18",
                description="PG monitor",
                tasks=[ServiceTask(N3, "running")],
            ),
            # traefik: two distinct services.
            ServiceStatus(
                "traefik_sockproxy",
                1,
                1,
                stack="traefik",
                description="socket proxy",
                tasks=[ServiceTask(N1, "running")],
            ),
            ServiceStatus(
                "traefik_traefik",
                3,
                3,
                stack="traefik",
                description="ingress",
                tasks=[
                    ServiceTask(N1, "running"),
                    ServiceTask(N2, "running"),
                    ServiceTask(N3, "running"),
                ],
            ),
            ServiceStatus(
                "eduTAP_web",
                1,
                1,
                stack="eduTAP",
                description="eduTAP frontend",
                tasks=[ServiceTask(N1, "running")],
            ),
            ServiceStatus(
                "registry", 1, 1, description="Docker registry", tasks=[ServiceTask(N2, "running")]
            ),
            ServiceStatus(
                "watchtower", 1, 1, description="Auto-update", tasks=[ServiceTask(N3, "running")]
            ),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "DOCKER INFOS" in out
    assert "Infrastructure" in out and "Service" in out and "Standalone containers" in out
    assert "Description" in out
    assert "mgr-01" in out  # short node header

    # kafka collapses to ONE row; per-node service names are gone.
    assert "kafka" in out
    assert f"kafka_kafka-{N1}" not in out
    # PostgreSQL: merged 'pg' row + distinct 'pg-monitor', stack prefix stripped.
    assert "PostgreSQL-18" in out
    assert "pg-monitor" in out
    assert f"PostgreSQL-18_pg-{N1}" not in out
    # traefik sub-rows without stack prefix.
    assert "sockproxy" in out
    assert "traefik_sockproxy" not in out
    # registry -> Infrastructure, watchtower -> Standalone containers, eduTAP -> Service.
    assert "registry" in out and "Docker registry" in out
    assert "watchtower" in out
    assert "eduTAP" in out
    # Status emojis present (running ✅, failed 💀 for kafka on down node).
    assert "✅" in out and "💀" in out


def test_services_section_unreachable():
    out = _text(panels.services_section(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()


def _swarm_with_origins(services=(), containers=(), nodes=None) -> SwarmInfo:
    """A Swarm carrying both Swarm services and plain/Compose containers.

    Named distinctly from the ``_swarm_with(node_name, reachable)`` helper
    further down this file, which builds a two-node Swarm for the WireGuard
    tunnel tests -- same short name, different shape, would otherwise shadow
    each other as module-level definitions.
    """
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        services=list(services),
        containers=list(containers),
        nodes=list(
            nodes
            or [
                SwarmNode(
                    name="node-01",
                    reachable=True,
                    role="manager",
                    leader=True,
                    state="ready",
                    availability="active",
                )
            ]
        ),
    )


def _origin_status(name, stack=None, running=1, desired=1, node="node-01") -> ServiceStatus:
    return ServiceStatus(
        name=name,
        running_replicas=running,
        desired_replicas=desired,
        stack=stack,
        tasks=[ServiceTask(node=node, state="running")] * running,
    )


def test_swarm_services_and_compose_projects_get_separate_blocks():
    swarm = _swarm_with_origins(
        services=[_origin_status("api", stack="backend")],
        containers=[_origin_status("web", stack="portal")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "SWARM STACKS" in out
    assert "COMPOSE PROJECTS" in out
    swarm_at = out.index("SWARM STACKS")
    compose_at = out.index("COMPOSE PROJECTS")
    assert swarm_at < compose_at


def test_an_empty_swarm_block_is_omitted_entirely():
    """A wall of dashes is what the explicit layout must not degenerate into."""
    swarm = _swarm_with_origins(containers=[_origin_status("web", stack="portal")])
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "SWARM STACKS" not in out
    assert "COMPOSE PROJECTS" in out


def test_an_empty_compose_block_is_omitted_entirely():
    swarm = _swarm_with_origins(services=[_origin_status("api", stack="backend")])
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "COMPOSE PROJECTS" not in out
    assert "SWARM STACKS" in out


def test_an_empty_category_inside_a_present_block_keeps_its_placeholder():
    """ "Swarm is running but has no infrastructure" is a statement."""
    swarm = _swarm_with_origins(services=[_origin_status("api", stack="backend")])
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "Infrastructure" in out
    assert "—" in out


def test_stackless_entries_from_both_origins_share_one_table():
    swarm = _swarm_with_origins(
        services=[_origin_status("lonely-service")],
        containers=[_origin_status("lonely-container")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "Standalone containers" in out
    assert "lonely-service" in out
    assert "lonely-container" in out


def test_an_infrastructure_shaped_stackless_entry_claims_no_project():
    """`docker run -d redis` on a host with no Compose project at all.

    ``redis`` and ``registry`` match ``infrastructure_stacks``, which used to
    file them under this origin's Infrastructure table -- and so under a
    "COMPOSE PROJECTS" / "SWARM STACKS" heading naming a project that does not
    exist. The earlier fixtures were called ``lonely-*`` and matched no infra
    keyword, which is why they never saw it. Being infrastructure-shaped does
    not give an entry a project.
    """
    swarm = _swarm_with_origins(
        services=[_origin_status("registry")],
        containers=[_origin_status("redis")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "SWARM STACKS" not in out
    assert "COMPOSE PROJECTS" not in out
    assert "Infrastructure" not in out
    standalone_at = out.index("Standalone containers")
    assert out.index("redis") > standalone_at
    assert out.index("registry") > standalone_at


def test_the_summary_counts_containers_without_calling_them_services():
    swarm = _swarm_with_origins(
        containers=[_origin_status("web", stack="portal"), _origin_status("db", stack="portal")]
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "0 services" in out
    assert "2 containers" in out


def _mixed_availability_swarm() -> SwarmInfo:
    """One active, one drained, one down node — no services."""
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=3,
        nodes=[
            SwarmNode(
                "srv-01",
                reachable=True,
                role="manager",
                leader=True,
                state="ready",
                availability="active",
            ),
            SwarmNode("srv-02", reachable=True, role="worker", state="ready", availability="drain"),
            SwarmNode(
                "srv-03", reachable=False, role="worker", state="down", availability="active"
            ),
        ],
    )


def test_drained_node_is_not_rendered_as_healthy():
    out = _text(panels.services_section(_mixed_availability_swarm(), Config()), width=170)
    nodes_line = next(line for line in out.splitlines() if "srv-02" in line)
    assert "⚠" in nodes_line and "drain" in nodes_line
    assert "srv-02 ✅" not in nodes_line
    # The healthy and the dead node keep their existing markers.
    assert "srv-01 ✅" in nodes_line
    assert "💀" in nodes_line and "down" in nodes_line


def test_swarm_summary_counts_unavailable_nodes():
    out = _text(panels.services_section(_mixed_availability_swarm(), Config()), width=170)
    assert "3 nodes (1 drain, 1 down)" in out


def test_swarm_summary_omits_capacity_note_when_all_nodes_are_active():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=2,
        nodes=[
            SwarmNode("srv-01", reachable=True, state="ready", availability="active"),
            SwarmNode("srv-02", reachable=True, state="ready", availability="active"),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "2 nodes  ·" in out
    assert "drain" not in out and "down" not in out


def test_node_with_empty_availability_falls_back_to_unavailable():
    # SwarmNode(..., availability="") is not operational, because
    # "" not in (None, "active") — this pins the same fallback _node_capacity
    # already applies, so both read "unavailable" instead of a blank label.
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-09", reachable=True, state="ready", availability="")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    nodes_line = next(line for line in out.splitlines() if "srv-09" in line)
    assert "⚠" in nodes_line and "unavailable" in nodes_line
    assert "(1 unavailable)" in out


def _line_index(out: str, predicate) -> int:
    lines = out.splitlines()
    return next(i for i, line in enumerate(lines) if predicate(line))


def test_infra_uis_are_grouped_into_a_pseudo_stack():
    N1, N2 = "srv-01", "srv-02"
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=2,
        nodes=[
            SwarmNode(N1, reachable=True, state="ready", availability="active"),
            SwarmNode(N2, reachable=True, state="ready", availability="active"),
        ],
        services=[
            ServiceStatus(
                "kafka_kafka",
                1,
                1,
                stack="kafka",
                description="Broker",
                tasks=[ServiceTask(N1, "running")],
            ),
            # A UI living inside a real stack must leave that stack.
            ServiceStatus(
                "kafka_kafbat-ui",
                1,
                1,
                stack="kafka",
                description="Kafka UI",
                tasks=[ServiceTask(N2, "running")],
            ),
            # A UI deployed as its own stack.
            ServiceStatus(
                "cloudbeaver_cloudbeaver",
                1,
                1,
                stack="cloudbeaver",
                description="SQL UI",
                tasks=[ServiceTask(N1, "running")],
            ),
            # A UI deployed as its own single-service Compose stack -- the
            # normal shape a Compose UI takes, and the case this hoisting
            # must cover. (A genuinely stackless container is covered
            # separately by test_a_stackless_admin_ui_claims_no_project,
            # which must NOT be hoisted here.)
            ServiceStatus(
                "mongo-express",
                1,
                1,
                stack="mongo-express",
                description="Mongo UI",
                tasks=[ServiceTask(N1, "running")],
            ),
            ServiceStatus(
                "eduTAP_web",
                1,
                1,
                stack="eduTAP",
                description="frontend",
                tasks=[ServiceTask(N1, "running")],
            ),
            # A real infrastructure stack that sorts alphabetically before
            # 'infra-uis' — the pseudo stack must still come first.
            ServiceStatus(
                "elasticsearch_es",
                1,
                1,
                stack="elasticsearch",
                description="Search",
                tasks=[ServiceTask(N1, "running")],
            ),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastructure"))
    uis_at = _line_index(out, lambda ln: ln.strip().startswith("infra-uis"))
    kafka_at = _line_index(out, lambda ln: ln.strip().startswith("kafka"))
    service_at = _line_index(out, lambda ln: ln.strip().startswith("Service"))
    es_at = _line_index(out, lambda ln: ln.strip().startswith("elasticsearch"))

    # The pseudo stack heads the Infrastructure block.
    assert infra_at < uis_at < kafka_at < service_at
    assert uis_at < es_at  # pseudo stack is hoisted, not sorted alphabetically
    # All three UI shapes ended up inside it.
    for ui in ("kafbat-ui", "cloudbeaver", "mongo-express"):
        assert uis_at < _line_index(out, lambda ln, ui=ui: ui in ln) < kafka_at
    # Stack prefixes are stripped on the sub-rows.
    assert "kafka_kafbat-ui" not in out
    assert "cloudbeaver_cloudbeaver" not in out
    # Unrelated services keep their block. Every service in this fixture has a
    # stack, so there is no stackless leftover and no "Standalone containers"
    # table at all -- eduTAP only needs to land after the Service header.
    assert service_at < _line_index(out, lambda ln: "eduTAP" in ln)
    assert "Standalone containers" not in out
    # mongo-express must appear exactly once: under infra-uis, not left behind
    # as a row in the "Standalone containers" matrix too (_line_index above
    # only finds the FIRST match, so it would miss a stray leftover row).
    assert out.count("mongo-express") == 1


def test_single_infra_ui_keeps_its_own_name():
    """The Compose case: a UI deployed as its own single-service stack."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "mongo-express",
                1,
                1,
                stack="mongo-express",
                description="Mongo UI",
                tasks=[ServiceTask("srv-01", "running")],
            )
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    # Not collapsed to a single row labelled 'infra-uis' — the UI stays named.
    assert "infra-uis" in out
    assert "mongo-express" in out


def test_a_stackless_admin_ui_claims_no_project():
    """A bare `docker run -d adminer` on a host with no Compose projects.

    `adminer` matches DEFAULT_INFRA_UI_SERVICES, which used to be enough to
    pull it into `_split_infra_uis` before the stackless split happened --
    landing it under "COMPOSE PROJECTS / Infrastructure / infra-uis", a
    project that does not exist. Being UI-shaped does not give a stackless
    entry a project either; it belongs in the same remainder table as any
    other stackless container.
    """
    swarm = _swarm_with_origins(containers=[_origin_status("adminer")])
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "COMPOSE PROJECTS" not in out
    assert "SWARM STACKS" not in out
    assert "Infrastructure" not in out
    assert "infra-uis" not in out
    assert "Standalone containers" in out
    assert "adminer" in out


def test_infra_ui_services_win_over_infrastructure_stacks():
    """A stack named after a UI service can still match an infrastructure key
    by substring — 'kafka-ui' (DEFAULT_INFRA_UI_SERVICES) contains 'kafka'
    (DEFAULT_INFRASTRUCTURE_STACKS), so is_infra("kafka-ui") is also true. The
    UI list is applied first and wins: the service is rendered as a sub-row of
    the 'infra-uis' pseudo stack instead of becoming its own top-level
    infrastructure stack."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "kafka-ui_kafka-ui",
                1,
                1,
                stack="kafka-ui",
                description="Kafka UI",
                tasks=[ServiceTask("srv-01", "running")],
            ),
            ServiceStatus(
                "mongodb_mongodb", 1, 1, stack="mongodb", tasks=[ServiceTask("srv-01", "running")]
            ),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    uis_at = _line_index(out, lambda ln: ln.strip().startswith("infra-uis"))
    # Rendered as a sub-row of the pseudo stack, not as a top-level infra row.
    assert uis_at < _line_index(out, lambda ln: ln.strip().startswith("kafka-ui"))
    assert _line_index(out, lambda ln: ln.strip().startswith("kafka-ui")) < _line_index(
        out, lambda ln: ln.strip().startswith("mongodb")
    )


def test_ui_sidecar_keeps_its_stack_for_attribution():
    """A service pulled into 'infra-uis' only because its STACK name matched a
    UI key (a sidecar such as 'kafka-ui_agent', whose stack 'kafka-ui' matches
    the 'kafka-ui' UI key) keeps 'stack/service' as its label — the UI itself
    still renders under its plain, unqualified name."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "kafka-ui_kafka-ui",
                1,
                1,
                stack="kafka-ui",
                description="Kafka UI",
                tasks=[ServiceTask("srv-01", "running")],
            ),
            ServiceStatus(
                "kafka-ui_agent",
                1,
                1,
                stack="kafka-ui",
                description="Kafka UI sidecar",
                tasks=[ServiceTask("srv-01", "running")],
            ),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "kafka-ui/agent" in out
    # The UI row itself stays plain, not qualified as 'kafka-ui/kafka-ui'.
    lines = out.splitlines()
    ui_line = next(
        ln for ln in lines if ln.strip().startswith("kafka-ui") and "kafka-ui/agent" not in ln
    )
    assert "kafka-ui/kafka-ui" not in ui_line


def test_no_infra_uis_row_without_matching_services():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "kafka_kafka", 1, 1, stack="kafka", tasks=[ServiceTask("srv-01", "running")]
            )
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "infra-uis" not in out


def test_the_matrix_has_a_working_column():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("app_web", 3, 3, stack="app", tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "Working" in out
    assert f"{icons.OK} 3/3" in out


def test_a_service_wanting_replicas_and_having_none_is_marked_dead():
    """Nine such rows render blank today — the outage is invisible."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("mystack_admin_backend", 0, 3, stack="mystack")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.DEAD} 0/3" in out


def test_a_service_scaled_to_zero_is_not_marked_dead():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("app_paused", 0, 0, stack="app")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.UNKNOWN} 0/0" in out
    assert f"{icons.DEAD} 0/0" not in out


def test_the_kafka_row_follows_the_cluster_verdict_not_the_replicas():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "kafka_kafka-srv-01", 5, 5, stack="kafka", tasks=[ServiceTask("srv-01", "running")]
            )
        ],
    )
    health = HealthInfo(
        clusters_probed=True, clusters=[ClusterService(kind="kafka", quorum_ok=False)]
    )
    out = _text(panels.services_section(swarm, Config(), health), width=170)
    assert f"{icons.DEAD} 5/5" in out


def test_without_health_a_clustered_service_is_not_observable():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "kafka_kafka-srv-01", 5, 5, stack="kafka", tasks=[ServiceTask("srv-01", "running")]
            )
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.UNKNOWN} 5/5" in out
    assert f"{icons.OK} 5/5" not in out


def _five_node_swarm_with_one_drained(services) -> SwarmInfo:
    nodes = [
        SwarmNode(f"srv-0{i}", reachable=True, state="ready", availability="active")
        for i in range(1, 5)
    ]
    nodes.append(SwarmNode("srv-05", reachable=True, state="ready", availability="drain"))
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=5,
        nodes=nodes,
        services=services,
    )


def test_a_global_service_counts_the_tasks_swarm_scheduled():
    """Swarm takes a global service's task off a drained node, so counting
    against every node would claim a degradation nobody measured — while
    ``docker service ls`` reads a contented 4/4."""
    traefik = ServiceStatus(
        "traefik_traefik",
        4,
        None,
        stack="traefik",
        tasks=[ServiceTask(f"srv-0{i}", "running") for i in range(1, 5)],
    )
    out = _text(
        panels.services_section(_five_node_swarm_with_one_drained([traefik]), Config()), width=170
    )
    assert f"{icons.OK} 4/4" in out
    assert f"{icons.WARN} 4/5" not in out


def test_a_global_service_missing_a_task_is_still_degraded():
    traefik = ServiceStatus(
        "traefik_traefik",
        3,
        None,
        stack="traefik",
        tasks=[ServiceTask(f"srv-0{i}", "running") for i in range(1, 4)]
        + [ServiceTask("srv-04", "failed")],
    )
    out = _text(
        panels.services_section(_five_node_swarm_with_one_drained([traefik]), Config()), width=170
    )
    assert f"{icons.WARN} 3/4" in out


def test_a_global_row_without_tasks_falls_back_to_the_reported_node_count():
    """``_node_map`` swallows a failed node listing, so an empty node list next
    to a non-zero node_count is reachable — and '/0' would be a lie."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=4,
        nodes=[],
        services=[ServiceStatus("traefik_traefik", 4, None, stack="traefik")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.OK} 4/4" in out
    assert "4/0" not in out


def _tasks(*states):
    """One ServiceStatus holding tasks on node 'srv-01' with the given states."""
    return [
        ServiceStatus(
            "svc",
            sum(s == "running" for s in states),
            len(states),
            tasks=[ServiceTask("srv-01", s) for s in states],
        )
    ]


def test_a_single_running_task_still_renders_the_bare_glyph():
    assert panels._node_cell(_tasks("running"), "srv-01").plain == panels._OK


def test_a_single_failed_task_still_renders_the_bare_glyph():
    assert panels._node_cell(_tasks("failed"), "srv-01").plain == panels._DEAD


def test_an_empty_node_cell_stays_blank():
    assert panels._node_cell(_tasks("running"), "other-node").plain == " "


def test_several_running_tasks_show_their_count():
    assert panels._node_cell(_tasks("running", "running"), "srv-01").plain == f"{panels._OK}2"
    assert (
        panels._node_cell(_tasks("running", "running", "running"), "srv-01").plain
        == f"{panels._OK}3"
    )


def test_a_partially_staffed_node_is_degraded_not_broken():
    cell = panels._node_cell(_tasks("running", "failed"), "srv-01")
    assert cell.plain == f"{panels._WARN}1/2"


def test_a_node_where_nothing_runs_is_broken_with_its_count():
    cell = panels._node_cell(_tasks("failed", "failed"), "srv-01")
    assert cell.plain == f"{panels._DEAD}0/2"


def test_tasks_of_several_services_in_one_row_are_counted_together():
    """A row bundles services — after ordinal grouping, two instances of one
    service can share a node."""
    services = [
        ServiceStatus("svc_1", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
        ServiceStatus("svc_2", 1, 1, tasks=[ServiceTask("srv-01", "running")]),
    ]
    assert panels._node_cell(services, "srv-01").plain == f"{panels._OK}2"


NODES = ["swarm01-mgr-01", "swarm01-wrk-01"]


def test_an_ordinal_suffix_is_stripped():
    assert panels._base_service_name("mystack_connector_1", NODES) == "mystack_connector"


def test_ordinal_stripping_survives_more_than_one_digit():
    assert panels._base_service_name("stack_worker_12", NODES) == "stack_worker"


def test_a_node_suffix_is_still_stripped():
    assert panels._base_service_name("kafka_kafka-swarm01-mgr-01", NODES) == "kafka_kafka"


def test_a_hyphen_before_digits_is_left_alone():
    """PostgreSQL-18_PostgreSQL-18 must not become PostgreSQL-18_PostgreSQL —
    '_' is what Swarm puts between a stack and its service, '-' is not."""
    assert (
        panels._base_service_name("PostgreSQL-18_PostgreSQL-18", NODES)
        == "PostgreSQL-18_PostgreSQL-18"
    )


def test_a_name_without_a_suffix_is_untouched():
    assert panels._base_service_name("traefik_sockproxy", NODES) == "traefik_sockproxy"


def test_a_name_that_is_only_an_ordinal_is_left_alone():
    assert panels._base_service_name("_1", NODES) == "_1"


def test_ordinal_instances_collapse_into_one_row():
    """Three pinned instances render as one sub-row summing their replicas.

    The stack deliberately runs a second, unrelated service as well, so it
    renders as a stack header plus sub-rows rather than collapsing to a single
    stack-named row — that single-service collapse is a distinct, pre-existing
    case with its own tests."""
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=3,
        nodes=[
            SwarmNode(n, reachable=True, state="ready", availability="active")
            for n in ("srv-01", "srv-02", "srv-03")
        ],
        services=[
            ServiceStatus(
                f"mystack_connector_{i}",
                1,
                1,
                stack="mystack",
                tasks=[ServiceTask(f"srv-0{i}", "running")],
            )
            for i in (1, 2, 3)
        ]
        + [
            ServiceStatus(
                "mystack_web", 1, 1, stack="mystack", tasks=[ServiceTask("srv-01", "running")]
            ),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "connector" in out
    assert "connector_1" not in out
    assert f"{icons.OK} 3/3" in out
    matches = [ln for ln in out.splitlines() if ln.strip().startswith("connector")]
    assert len(matches) == 1


def test_bugsink_is_infrastructure():
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus(
                "bugsink_bugsink",
                1,
                1,
                stack="bugsink",
                description="Bugsink (error tracker)",
                tasks=[ServiceTask("srv-01", "running")],
            ),
            ServiceStatus("app_web", 1, 1, stack="app", tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastructure"))
    service_at = _line_index(out, lambda ln: ln.strip().startswith("Service"))
    bugsink_at = _line_index(out, lambda ln: ln.strip().startswith("bugsink"))
    assert infra_at < bugsink_at < service_at


def test_a_single_starting_task_renders_as_degraded_not_dead():
    """It is on its way up. 💀 would say we measured it broken."""
    services = [ServiceStatus("s", 0, 1, tasks=[ServiceTask("srv-01", "preparing")])]
    assert "⚠️" in panels._node_cell(services, "srv-01").plain


def test_a_single_failed_task_still_renders_as_dead():
    services = [ServiceStatus("s", 0, 1, tasks=[ServiceTask("srv-01", "failed")])]
    assert "💀" in panels._node_cell(services, "srv-01").plain


# --- Swarm nodes held against the WireGuard tunnel --------------------------
#
# A down node with a healthy tunnel means Docker; one without a handshake means
# the network. Pairing them saves the reader a jump between two sections.

from terminal_status_panel.model import PeerReachability  # noqa: E402


def _swarm_with(node_name, reachable):
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        nodes=[
            SwarmNode(name="node-a", reachable=True, state="ready", availability="active"),
            SwarmNode(
                name=node_name,
                reachable=reachable,
                state="ready" if reachable else "down",
                availability="active",
            ),
        ],
    )


def _peer(name, ok, **kw):
    return PeerReachability(name=name, method="wireguard", ok=ok, **kw)


def test_down_node_without_handshake_points_at_the_network():
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", False, detail="never", rx_bytes=0, tx_bytes=174080)],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg: no handshake" in output


def test_down_node_with_healthy_tunnel_points_at_docker():
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", True, detail="0:10", rx_bytes=1024, tx_bytes=2048)],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg: ok" in output


def test_healthy_node_gets_no_annotation():
    """The line is crowded. The note appears only where it explains something."""
    swarm = _swarm_with("node-c", reachable=True)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", True, detail="0:10")], peers_probed=True
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg:" not in output


def test_down_node_without_matching_peer_is_not_annotated():
    """No peer data, no claim about the tunnel."""
    swarm = _swarm_with("node-c", reachable=False)
    output = _text(panels.services_section(swarm, Config(), HealthInfo(peers_probed=True)))
    assert "wg:" not in output


def test_tcp_fallback_peer_makes_no_tunnel_claim():
    """The TCP fallback probes port 2377. Its "ok" says nothing about the
    tunnel, so it must not surface as "(wg: ok)"."""
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[PeerReachability(name="node-c", method="tcp", ok=True, detail="tcp/2377")],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg:" not in output


def test_stale_handshake_is_not_reported_as_none():
    """peer.ok is False for a stale handshake too — but "no handshake" would be
    the wrong word for one that merely aged out."""
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", False, detail="5:00", rx_bytes=1024, tx_bytes=2048)],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "no handshake" not in output
    assert "5:00" in output


def test_long_address_lists_are_capped_with_a_visible_remainder():
    info = SystemInfo(
        hostname="host",
        os_name="Debian GNU/Linux 12",
        kernel="Linux 6.1.0",
        uptime_seconds=60,
        user="root",
        ip_addresses=[f"10.0.0.{n}" for n in range(101, 113)],
    )
    out = _text(panels.system_overview(info), width=200)
    assert "10.0.0.108" in out
    assert "10.0.0.109" not in out
    assert "(+4 more)" in out


def test_short_address_lists_show_no_remainder():
    info = SystemInfo(
        hostname="host",
        os_name="Debian GNU/Linux 12",
        kernel="Linux 6.1.0",
        uptime_seconds=60,
        user="root",
        ip_addresses=["10.0.0.1", "10.0.0.2"],
    )
    out = _text(panels.system_overview(info), width=200)
    assert "more)" not in out


def test_long_mount_paths_keep_their_distinguishing_tail():
    path = "/Library/Developer/CoreSimulator/Volumes/iOS_23C54"
    shortened = panels._short_mount(path, 15)
    assert len(shortened) == 15
    assert shortened.startswith("…")
    assert shortened.endswith("iOS_23C54")


def test_short_mount_paths_are_left_alone():
    assert panels._short_mount("/", 15) == "/"
    assert panels._short_mount("/Volumes/Data2", 15) == "/Volumes/Data2"


def test_short_mount_handles_width_one():
    """Edge case: width=1 should return just the ellipsis, not the full path."""
    assert panels._short_mount("/Volumes/Data2", 1) == "…"
    assert len(panels._short_mount("/Volumes/Data2", 1)) == 1


def _fs_regression_resource() -> ResourceUsage:
    return ResourceUsage(
        mem_total=32_000_000_000,
        mem_used=20_400_000_000,
        mem_percent=64.0,
        swap_total=8_000_000_000,
        swap_used=600_000_000,
        swap_percent=8.0,
        filesystems=[
            # total=950.0 GB, used=850.0 GB, avail=total-used=100.0 GB
            FilesystemUsage("/", 1_020_054_732_800, 912_680_550_400, 89.0),
            # total=500.0 GB, used=125.0 GB, avail=total-used=375.0 GB
            FilesystemUsage("/Volumes/Data2", 536_870_912_000, 134_217_728_000, 25.0),
        ],
    )


def test_filesystem_numeric_columns_not_truncated_in_system_status():
    """Regression test: mount column must not squeeze numeric columns.

    The filesystem table shares a narrow pane with memory; a fixed width mount
    column was starving Size, Used, Avail to a few characters each. The fix
    uses max_width instead of width. This test exercises the real constraint
    by rendering through system_status (which applies the ratio-based split)
    at a narrow width, and checking that numeric values are not truncated.

    The fixture values are deliberately chosen to format to five-character
    figures (three integer digits, a dot, one decimal digit, e.g. "950.0")
    plus a unit. Short values like "1.8 TB" survive even a squeezed column
    and would not discriminate between the fixed and broken layouts; the
    original defect only mangled wider figures, e.g. "926.4" rendered as
    "926…" in a four-character column.
    """
    # Render at width 100, which puts the filesystem table in a narrow space
    # -- narrow enough to leave no room for a usage bar (see the width-80/215
    # cases below), wide enough that Size/Used/Avail still fit on one line.
    out = _text(panels.system_status(_fs_regression_resource(), Config()), width=100)

    # Every numeric figure must appear intact, not cut mid-number. Each
    # assertion is pinned individually -- an any() over alternatives would
    # pass even if most of the expected values were missing.
    assert "950.0 GB" in out
    assert "850.0 GB" in out
    assert "100.0 GB" in out
    assert "500.0 GB" in out
    assert "125.0 GB" in out
    assert "375.0 GB" in out

    # Column headers alone are not proof: the original defect left them
    # intact, which is precisely why it slipped through.
    assert "/" in out
    assert "/Volumes/Data2" in out


def test_filesystem_numeric_columns_survive_a_narrow_terminal():
    """Regression test: restoring the usage bar must not re-break narrow terminals.

    The usage bar removed by an earlier task was restored to flex with the
    available width (see ``_FilesystemBody``) rather than claim a fixed
    share of it. At a genuinely narrow terminal -- width 80, the fallback
    width used when a shell is not attached to a TTY, not just a "somewhat
    narrow" 100 -- there is no room left for a bar at all once the numeric
    columns and the mount column have what they need, so the bar must
    disappear instead of taking space from them.
    """
    out = _text(panels.system_status(_fs_regression_resource(), Config()), width=80)
    fs_block = out[out.index("FILESYSTEM USAGE") :]

    # No mid-number cut, e.g. "926…": the fixed columns render exactly as
    # they did before the bar existed, wrapped onto extra lines rather than
    # truncated, because there is nothing left over for the bar to claim.
    assert "…" not in fs_block
    for fragment in ("950.0", "850.0", "100.0", "500.0", "125.0", "375.0", "GB"):
        assert fragment in fs_block
    assert "/Volumes/Data2" in fs_block


def test_filesystem_usage_bar_reappears_on_a_wide_terminal():
    """The usage bar an earlier task removed must come back once there is room.

    At the maintainer's actual terminal (215 columns) there are roughly 90
    columns unused to the right of the filesystem table; the bar should
    claim them rather than stay gone everywhere just because it must vanish
    at width 80. The numeric columns must still show their complete,
    single-line values -- the bar is an addition beside Use%, not something
    that gets to squeeze them.
    """
    out = _text(panels.system_status(_fs_regression_resource(), Config()), width=215)
    fs_block = out[out.index("FILESYSTEM USAGE") :]

    assert "950.0 GB" in fs_block
    assert "850.0 GB" in fs_block
    assert "100.0 GB" in fs_block
    assert "500.0 GB" in fs_block
    assert "125.0 GB" in fs_block
    assert "375.0 GB" in fs_block
    assert "/Volumes/Data2" in fs_block
    assert "Use%" in fs_block  # the percentage column stays, not replaced

    # The bar itself: filled cells for the 89%-full "/" and the 25%-full
    # "/Volumes/Data2" both present, and the fuller one has strictly more
    # filled cells than the emptier one.
    lines = [line for line in fs_block.splitlines() if "█" in line or "░" in line]
    assert len(lines) == 2
    filled_counts = [line.count("█") for line in lines]
    assert all(count > 0 for count in filled_counts)
    assert filled_counts[0] > filled_counts[1]


def test_filesystem_usage_bar_is_capped_to_the_memory_bar_width():
    """The filesystem bar must not outgrow the RAM/SWAP bars above it.

    At width 215 there are roughly 90 columns free to the right of the
    filesystem table -- flexing all the way into them makes the filesystem
    block visibly heavier than the MEMORY & SWAP block directly above it,
    even though both present a single measurement per row. The filesystem
    bar must stop growing once it reaches the memory bars' own width
    (``panels._MEMORY_BAR_WIDTH``, currently 44), continuing to flex only
    below that cap -- which the width-80 and width-100 tests above already
    cover.
    """
    out = _text(panels.system_status(_fs_regression_resource(), Config()), width=215)
    fs_block = out[out.index("FILESYSTEM USAGE") :]

    bar_lines = [line for line in fs_block.splitlines() if "█" in line or "░" in line]
    assert len(bar_lines) == 2
    for line in bar_lines:
        bar_width = line.count("█") + line.count("░")
        assert bar_width == panels._MEMORY_BAR_WIDTH


# --------------------------------------------------------------------------- #
# TOP CPU / TOP RAM row
# --------------------------------------------------------------------------- #


def _render_status(**kwargs) -> str:
    res = ResourceUsage(
        mem_total=8 * 1024**3,
        mem_used=1024**3,
        mem_percent=12.5,
        load_avg=(0.5, 0.4, 0.3),
        cpu_count=4,
        cpu_percent=10.0,
    )
    console = Console(width=200, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(system_status(res, Config(), **kwargs))
    return capture.get()


def _snapshot():
    return ProcessSnapshot(
        top_cpu=[
            ProcessInfo(
                pid=7372,
                name="pg_autoctl",
                cpu_percent=22.3,
                memory_percent=0.0,
                origin="container e23ce43dcbe0",
            ),
            ProcessInfo(
                pid=1920,
                name="glusterfsd",
                cpu_percent=19.1,
                memory_percent=0.1,
                origin="glusterd.service",
            ),
        ],
        top_memory=[
            ProcessInfo(
                pid=1079456,
                name="java",
                cpu_percent=0.0,
                memory_percent=7.0,
                origin="container efc841ba0f44",
            ),
        ],
        sampled=0.3,
    )


def test_the_column_labels_are_english():
    out = _render_status(processes=_snapshot())
    for label in ("TOP CPU", "TOP RAM", "%CPU", "%MEM", "PID", "PROCESS", "SERVICE"):
        assert label in out


def test_a_known_container_shows_its_service_name():
    out = _render_status(
        processes=_snapshot(), origins={"e23ce43dcbe0feef12bc0199df6bf45d": "pg_pg-monitor"}
    )
    assert "pg_pg-monitor" in out


def test_an_unknown_container_keeps_its_id():
    """The case that must not invent a name."""
    out = _render_status(processes=_snapshot(), origins={"something-else": "other"})
    assert "e23ce43dcbe0" in out
    assert "pg_pg-monitor" not in out


def test_without_docker_data_the_id_stands():
    out = _render_status(processes=_snapshot(), origins=None)
    assert "e23ce43dcbe0" in out


def test_a_systemd_unit_needs_no_resolving():
    out = _render_status(processes=_snapshot(), origins={})
    assert "glusterd.service" in out


def test_a_service_name_wider_than_its_column_is_truncated():
    """The design asked for this case explicitly and it was never pinned.

    47 characters is arbitrary only in how far past ``_SERVICE_WIDTH`` (22) it
    reaches -- the point is that the excess is cut with an ellipsis rather
    than left to push the row out of alignment.
    """
    long_name = "a" * 39 + ".service"  # a plain systemd unit, 47 characters
    snapshot = ProcessSnapshot(
        top_memory=[
            ProcessInfo(pid=4242, name="hog", memory_percent=3.0, origin=long_name),
        ]
    )
    out = _render_status(processes=snapshot)
    assert long_name not in out
    truncated = long_name[: panels._SERVICE_WIDTH - 1] + "…"
    assert truncated in out
    # The cut affects only the SERVICE cell -- PID and PROCESS on the same
    # row are unaffected, so the row stays aligned rather than collapsing.
    for line in out.splitlines():
        if truncated in line:
            assert "4242" in line and "hog" in line
            break
    else:
        raise AssertionError("truncated service name not found on its own row")


def test_a_service_name_exactly_at_the_column_width_is_not_truncated():
    exact_name = "b" * 14 + ".service"  # precisely _SERVICE_WIDTH characters
    assert len(exact_name) == panels._SERVICE_WIDTH
    snapshot = ProcessSnapshot(
        top_memory=[
            ProcessInfo(pid=99, name="proc", memory_percent=1.0, origin=exact_name),
        ]
    )
    out = _render_status(processes=snapshot)
    assert exact_name in out
    assert "…" not in out


def test_the_sampled_window_is_shown_in_the_heading():
    out = _render_status(processes=_snapshot())
    assert "TOP CPU (0.3s)" in out


def test_a_disabled_cpu_sample_shows_only_the_memory_list():
    snapshot = ProcessSnapshot(top_cpu=[], top_memory=_snapshot().top_memory, sampled=0.0)
    out = _render_status(processes=snapshot)
    assert "TOP RAM" in out
    assert "CPU sampling is off" in out


def test_no_process_data_leaves_the_section_as_it_was():
    """`None` means the row was never asked for -- every existing caller.

    The section then renders exactly as it did before this feature existed.
    """
    out = _render_status(processes=None)
    assert "TOP CPU" not in out
    assert "SYSTEM LOAD" in out


def test_an_empty_snapshot_says_so_rather_than_showing_nothing():
    """An empty snapshot means the collector was asked and came back
    empty-handed -- no psutil, no process table. That is a finding, and
    silently omitting the row would hide it."""
    out = _render_status(processes=ProcessSnapshot())
    assert "TOP CPU" in out
    assert "not available" in out


def test_the_memory_column_is_shown_in_bytes():
    snapshot = ProcessSnapshot(
        top_cpu=[ProcessInfo(pid=1, name="test", cpu_percent=0.0, memory_percent=0.0)],
        top_memory=[ProcessInfo(pid=1, name="app", memory_percent=7.0, memory_bytes=2 * 1024**3)],
        sampled=0.3,
    )
    out = _render_status(processes=snapshot)
    # Not `"MEM" in out`: `%MEM` already contains the substring "MEM", so that
    # assertion passes even without the new column. The header word list is
    # what actually distinguishes the two.
    header = next(line for line in out.splitlines() if "%CPU" in line and "PID" in line)
    assert "MEM" in header.split()
    assert "2.0 GB" in out


def test_an_absent_memory_figure_shows_the_dash_not_n_a():
    """`format_bytes(None)` returns "n/a"; the row already says absence with a
    dash, and one row should not carry two vocabularies for it.

    Rendered through `_process_table` directly rather than through
    `system_status`: side by side, the row this test cares about shares its
    physical output line with a row from the other table, and splitting that
    combined line would pick up the wrong table's cells.
    """
    table = panels._process_table(
        [ProcessInfo(pid=1, name="app", memory_percent=None, memory_bytes=None)],
        None,
    )
    out = _text(table, width=100)
    line = next(line for line in out.splitlines() if "app" in line)
    assert "n/a" not in line
    # Not `"—" in out`: the %CPU cell is also `None` and already prints a
    # dash, so that assertion would pass even if the MEM column alone fell
    # back to `format_bytes`'s "n/a". Pin the MEM cell itself -- the third
    # column, after %CPU and %MEM -- to a dash instead.
    cells = line.split()
    assert cells[:3] == ["—", "—", "—"]


def test_the_column_labels_are_english_and_in_order():
    """Split rather than searched: `%MEM` contains `MEM`, so `str.index` would
    find the wrong one and the order would pass however it was arranged."""
    snapshot = ProcessSnapshot(
        top_cpu=[ProcessInfo(pid=1, name="test", cpu_percent=0.0, memory_percent=0.0)],
        top_memory=[ProcessInfo(pid=1, name="app", memory_percent=7.0, memory_bytes=1024)],
        sampled=0.3,
    )
    out = _render_status(processes=snapshot)
    header = next(line for line in out.splitlines() if "%CPU" in line and "MEM" in line)
    # Two lists sit side by side, so the header carries both sets of labels.
    assert header.split()[:6] == ["%CPU", "%MEM", "MEM", "PID", "PROCESS", "SERVICE"]


# --------------------------------------------------------------------------- #
# TOP CPU / TOP RAM: side by side while both fit, stacked otherwise
# --------------------------------------------------------------------------- #
#
# `_render_status` above pins width=200, which is why the suite could not see
# this: at 80 -- `Config.width`'s default, and what `resolve_width` falls back
# to whenever stdout is not a TTY, the MOTD-generation path the README names --
# two six-column tables do not fit side by side, and letting Rich shrink them
# breaks the numeric columns instead of the columns this panel means to give
# way (`SERVICE`, `PROCESS`). These tests render at their own explicit widths.


def _render_status_at(width: int, **kwargs) -> str:
    res = ResourceUsage(
        mem_total=8 * 1024**3,
        mem_used=1024**3,
        mem_percent=12.5,
        load_avg=(0.5, 0.4, 0.3),
        cpu_count=4,
        cpu_percent=10.0,
    )
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(system_status(res, Config(), **kwargs))
    return capture.get()


def _wide_snapshot() -> ProcessSnapshot:
    """A realistic snapshot with figures wide enough to break at width 80: a
    four-digit and a seven-digit PID, and a `MEM` value in the hundreds of
    MB -- the exact numbers the review measured breaking (a size wrapping
    across two lines, a PID truncated to `1079…`)."""
    return ProcessSnapshot(
        top_cpu=[
            ProcessInfo(
                pid=3708,
                name="dockerd",
                cpu_percent=0.5,
                memory_percent=1.0,
                memory_bytes=int(128.5 * 1024**2),
                origin="containerd.service",
            ),
            ProcessInfo(
                pid=1920,
                name="glusterfsd",
                cpu_percent=19.1,
                memory_percent=0.1,
                memory_bytes=45 * 1024**2,
                origin="glusterd.service",
            ),
        ],
        top_memory=[
            ProcessInfo(
                pid=1079456,
                name="java",
                cpu_percent=0.0,
                memory_percent=7.0,
                memory_bytes=2 * 1024**3,
                origin="container efc841ba0f44",
            ),
        ],
        sampled=0.3,
    )


def test_process_lists_sit_side_by_side_when_they_fit():
    """At width 120 both tables fit at their natural width -- the behaviour
    unchanged from before this fix."""
    out = _render_status_at(120, processes=_wide_snapshot())
    header = next(line for line in out.splitlines() if "TOP CPU" in line)
    assert "TOP RAM" in header


def test_process_lists_stack_when_they_do_not_fit():
    """At width 80 the pair no longer fits side by side, so TOP RAM moves
    below TOP CPU instead of Rich shrinking the numeric columns.

    Before this fix, width 80 rendered ``128…`` with ``MB`` wrapped onto a
    second line, and the seven-digit PID `1079456` truncated -- a shortened
    number, which this panel must never produce. Every numeric cell here must
    stay whole and on one line.
    """
    out = _render_status_at(80, processes=_wide_snapshot())

    header = next(line for line in out.splitlines() if "TOP CPU" in line)
    assert "TOP RAM" not in header  # stacked, not side by side -- own heading

    assert "128.5 MB" in out  # not "128…" with "MB" wrapped onto its own line
    assert "1079456" in out  # not "1079…" or "456…"
    assert "456…" not in out
    assert not any(line.strip() == "MB" for line in out.splitlines())

    # Both headings still present, each introducing its own table.
    assert "TOP CPU" in out
    assert "TOP RAM" in out


# --------------------------------------------------------------------------- #
# Scheduled jobs
# --------------------------------------------------------------------------- #


def _job_service(schedule="0 5 * * *", description=None):
    return ServiceStatus(
        name="nightly",
        running_replicas=0,
        desired_replicas=1,
        job=True,
        schedule=schedule,
        description=description,
    )


def test_a_job_shows_its_schedule_next_to_its_description():
    """Without the schedule the row is unreadable: 'ok 12h' invites the
    question 'and when was it supposed to run?'"""
    services = [_job_service(description="STWM daily update")]

    assert panels._group_desc(services) == "0 5 * * * · STWM daily update"


def test_a_job_without_a_description_shows_the_schedule_alone():
    assert panels._group_desc([_job_service()]) == "0 5 * * *"


def test_a_job_without_a_schedule_keeps_its_description():
    services = [_job_service(schedule=None, description="migration")]

    assert panels._group_desc(services) == "migration"


def test_the_swarm_cronjob_controller_counts_as_infrastructure():
    """The thing that *drives* the jobs is infrastructure, not an application.

    It carries no data and serves no user; it scales other services up on a
    schedule. Filed under Services it would sit among the very jobs it
    triggers, where a reader asking "why did nothing run last night" would not
    think to look.
    """
    controller = _origin_status("app", stack="swarm-cronjob")

    infra, service, _ = panels._classify_origin([controller], Config(), ["node-01"])

    assert [name for name, _ in infra] == ["swarm-cronjob"]
    assert service == []


def test_the_controller_is_recognised_with_either_separator():
    """As a Swarm stack the name carries a hyphen; as a Compose project it is
    commonly an underscore. Both name the same controller."""
    for stack in ("swarm-cronjob", "swarm_cronjob"):
        entry = _origin_status("app", stack=stack)

        infra, service, _ = panels._classify_origin([entry], Config(), ["node-01"])

        assert [name for name, _ in infra] == [stack], stack
        assert service == [], stack


def test_an_application_stack_is_not_infrastructure():
    """The guard for the test above: this is what a non-match looks like."""
    infra, service, _ = panels._classify_origin(
        [_origin_status("app", stack="billing")], Config(), ["node-01"]
    )

    assert infra == []
    assert [name for name, _ in service] == ["billing"]


# --- the image column --------------------------------------------------------

_N1, _N2, _N3 = "swarm01-mgr-01", "swarm01-wrk-01", "swarm01-wrk-02"


def _image_swarm(*services) -> SwarmInfo:
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=3,
        nodes=[
            SwarmNode(_N1, reachable=True, role="manager", leader=True),
            SwarmNode(_N2, reachable=True, role="worker"),
            SwarmNode(_N3, reachable=True, role="worker"),
        ],
        services=list(services),
    )


def test_the_image_column_shows_repository_and_tag():
    """The registry and the namespace repeat on every row of a cluster that
    builds its own images -- they cost width and separate nothing."""
    swarm = _image_swarm(
        ServiceStatus(
            "shop_api",
            1,
            1,
            stack="shop",
            description="Shop API",
            tasks=[ServiceTask(_N1, "running")],
            image="gitlab.example.org:5005/group/project/api:2026-08-14_1206",
        )
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert "Image" in out
    assert "api:2026-08-14_1206" in out
    assert "gitlab.example.org" not in out


def test_a_registry_port_is_not_mistaken_for_a_tag():
    """`registry.example.org:5000/app` carries a colon that belongs to the host,
    not to a tag. Splitting on the last colon of the whole reference would
    render the row as `5000/app`."""
    swarm = _image_swarm(
        ServiceStatus(
            "shop_api",
            1,
            1,
            stack="shop",
            tasks=[ServiceTask(_N1, "running")],
            image="registry.example.org:5000/app",
        )
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert "5000" not in out


def test_replicas_on_the_same_image_report_it_once():
    swarm = _image_swarm(
        ServiceStatus(
            f"kafka_kafka-{_N1}",
            1,
            1,
            stack="kafka",
            tasks=[ServiceTask(_N1, "running")],
            image="apache/kafka:4.0.0",
        ),
        ServiceStatus(
            f"kafka_kafka-{_N2}",
            1,
            1,
            stack="kafka",
            tasks=[ServiceTask(_N2, "running")],
            image="apache/kafka:4.0.0",
        ),
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert out.count("kafka:4.0.0") == 1


def test_a_tag_that_differs_between_replicas_is_marked():
    """A rolling update that stalled leaves one node behind. The row still has
    to name an image, so it names the one most replicas run -- and the marker
    says the others do not."""
    swarm = _image_swarm(
        ServiceStatus(
            f"kafka_kafka-{_N1}",
            1,
            1,
            stack="kafka",
            tasks=[ServiceTask(_N1, "running")],
            image="apache/kafka:4.0.0",
        ),
        ServiceStatus(
            f"kafka_kafka-{_N2}",
            1,
            1,
            stack="kafka",
            tasks=[ServiceTask(_N2, "running")],
            image="apache/kafka:4.0.0",
        ),
        ServiceStatus(
            f"kafka_kafka-{_N3}",
            1,
            1,
            stack="kafka",
            tasks=[ServiceTask(_N3, "running")],
            image="apache/kafka:3.9.0",
        ),
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert f"kafka:{icons.WARN} 4.0.0" in out


def test_differing_repositories_mark_the_whole_reference():
    swarm = _image_swarm(
        ServiceStatus(
            f"web_proxy-{_N1}",
            1,
            1,
            stack="web",
            tasks=[ServiceTask(_N1, "running")],
            image="traefik:v3.3",
        ),
        ServiceStatus(
            f"web_proxy-{_N2}",
            1,
            1,
            stack="web",
            tasks=[ServiceTask(_N2, "running")],
            image="nginx:1.27",
        ),
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert f"{icons.WARN} traefik:v3.3" in out or f"{icons.WARN} nginx:1.27" in out


def test_a_service_without_an_image_leaves_the_cell_empty():
    swarm = _image_swarm(
        ServiceStatus(
            "shop_api",
            1,
            1,
            stack="shop",
            description="Shop API",
            tasks=[ServiceTask(_N1, "running")],
        )
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    assert "Image" in out and "Shop API" in out


def test_the_image_column_can_be_switched_off():
    """The column costs width, and on a narrow terminal with several node
    columns that is the width the description needs."""
    swarm = _image_swarm(
        ServiceStatus(
            "shop_api",
            1,
            1,
            stack="shop",
            description="Shop API",
            tasks=[ServiceTask(_N1, "running")],
            image="registry.example.org/api:2026-08-14_1206",
        )
    )
    out = _text(panels.services_section(swarm, Config(show_image=False)), width=170)

    assert "Image" not in out
    assert "2026-08-14_1206" not in out
    assert "Shop API" in out


# --- engine version and manager reachability -------------------------------


def _versioned_swarm(nodes, role="manager", local_version=None) -> SwarmInfo:
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role=role,
        node_count=len(nodes) or None,
        nodes=list(nodes),
        local_engine_version=local_version,
    )


def _mgr(name, version="28.5.2", reachable_by_managers=True, leader=False):
    return SwarmNode(
        name,
        reachable=True,
        role="manager",
        leader=leader,
        state="ready",
        availability="active",
        engine_version=version,
        reachable_by_managers=reachable_by_managers,
    )


def _wrk(name, version="28.5.2"):
    return SwarmNode(
        name,
        reachable=True,
        role="worker",
        state="ready",
        availability="active",
        engine_version=version,
    )


def _swarm_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.strip().startswith("Swarm"))


def _nodes_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.strip().startswith("Nodes"))


def test_one_engine_version_renders_once_and_not_per_node():
    swarm = _versioned_swarm([_mgr("srv-01", leader=True), _wrk("srv-02"), _wrk("srv-03")])
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "Docker 28.5.2" in _swarm_line(out)
    assert "28.5.2" not in _nodes_line(out)
    assert "versions" not in _swarm_line(out)


def test_a_deviating_engine_version_unfolds_only_that_node():
    swarm = _versioned_swarm(
        [_mgr("srv-01", leader=True), _wrk("srv-02", version="27.3.1"), _wrk("srv-03")]
    )
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "2 versions" in _swarm_line(out)
    nodes = _nodes_line(out)
    assert "27.3.1" in nodes
    # Only the deviating node carries a version; the others stay bare.
    assert nodes.count("28.5.2") == 0


def test_a_deviating_patch_level_warns_like_a_minor():
    swarm = _versioned_swarm([_mgr("srv-01", leader=True), _wrk("srv-02", version="28.5.1")])
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "2 versions" in _swarm_line(out)
    assert "28.5.1" in _nodes_line(out)


def test_a_worker_labels_its_version_as_local():
    swarm = _versioned_swarm([], role="worker", local_version="28.5.2")
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "Docker 28.5.2 (local)" in _swarm_line(out)


def test_a_node_without_a_version_does_not_invent_one():
    swarm = _versioned_swarm([_mgr("srv-01", version=None, leader=True)])
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "Docker" not in _swarm_line(out)


def test_an_unreachable_manager_is_marked_and_keeps_its_leader_suffix():
    swarm = _versioned_swarm(
        [
            _mgr("srv-01", leader=True, reachable_by_managers=False),
            _mgr("srv-02"),
            _mgr("srv-03"),
        ]
    )
    out = _text(panels.services_section(swarm, Config()), width=200)
    nodes = _nodes_line(out)
    assert "unreachable" in nodes
    assert "(leader)" in nodes


def test_the_quorum_line_appears_when_one_more_loss_would_lock_the_swarm():
    swarm = _versioned_swarm(
        [_mgr("srv-01", leader=True), _mgr("srv-02"), _mgr("srv-03", reachable_by_managers=False)]
    )
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "2/3 managers reachable" in out


def test_no_quorum_line_when_every_manager_is_reachable():
    swarm = _versioned_swarm([_mgr("srv-01", leader=True), _mgr("srv-02"), _mgr("srv-03")])
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "managers reachable" not in out


def test_the_quorum_line_counts_managers_not_nodes():
    """Five nodes, three of them managers -- the ratio must be over managers."""
    swarm = _versioned_swarm(
        [
            _mgr("srv-01", leader=True),
            _mgr("srv-02"),
            _mgr("srv-03", reachable_by_managers=False),
            _wrk("srv-04"),
            _wrk("srv-05"),
        ]
    )
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert "2/3 managers reachable" in out
    assert "4/5" not in out


# --- docker disk usage -----------------------------------------------------


def _disk(node="srv-01", root_dir="/var/lib/docker", **kw):
    from terminal_status_panel.model import DockerDiskUsage

    defaults = dict(
        node=node,
        root_dir=root_dir,
        used=43 * 2**30,
        reclaimable=28 * 2**30,
        images=20 * 2**30,
        build_cache=12 * 2**30,
        volumes=2 * 2**30,
        containers=2**28,
        volumes_unused=178,
        volumes_total=185,
    )
    defaults.update(kw)
    return DockerDiskUsage(**defaults)


def _disk_swarm(disk):
    return SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[_mgr("srv-01", leader=True)],
        disk=disk,
    )


def test_the_disk_line_names_the_node_it_describes():
    out = _text(panels.services_section(_disk_swarm(_disk()), Config()), width=200)
    line = next(line for line in out.splitlines() if line.strip().startswith("Disk"))
    assert "srv-01" in line


def test_the_disk_line_leads_with_what_can_be_reclaimed():
    out = _text(panels.services_section(_disk_swarm(_disk()), Config()), width=200)
    line = next(line for line in out.splitlines() if line.strip().startswith("Disk"))
    assert line.index("reclaimable") < line.index("images")
    assert "178/185 unused" in line


def test_a_missing_disk_reading_says_so_rather_than_vanishing():
    """A line that disappears reads as 'nothing to report', which would be false."""
    out = _text(panels.services_section(_disk_swarm(None), Config()), width=200)
    line = next(line for line in out.splitlines() if line.strip().startswith("Disk"))
    assert "n/a (timeout)" in line


def test_the_disk_line_renders_without_a_swarm():
    swarm = SwarmInfo(reachable=True, enabled=False, disk=_disk())
    out = _text(panels.services_section(swarm, Config()), width=200)
    assert any(line.strip().startswith("Disk") for line in out.splitlines())


def _fs(mountpoint, percent):
    from terminal_status_panel.model import FilesystemUsage

    return FilesystemUsage(mountpoint=mountpoint, total=100, used=int(percent), percent=percent)


def test_disk_colour_follows_real_pressure_not_the_reclaimable_size():
    from terminal_status_panel.model import ResourceUsage

    roomy = ResourceUsage(filesystems=[_fs("/", 22.0)])
    assert panels._disk_style(_disk(), roomy) is None

    tight = ResourceUsage(filesystems=[_fs("/", 91.0)])
    assert panels._disk_style(_disk(), tight) == "yellow"


def test_disk_colour_picks_the_mount_that_actually_holds_the_root_dir():
    """The longest matching mountpoint wins, not the first or the shortest."""
    from terminal_status_panel.model import ResourceUsage

    res = ResourceUsage(filesystems=[_fs("/", 95.0), _fs("/var/lib/docker", 30.0)])
    assert panels._disk_style(_disk(root_dir="/var/lib/docker"), res) is None


def test_disk_colour_stays_absent_when_nothing_was_measured():
    """No filesystem data is not evidence of comfort, but it is not a finding either."""
    assert panels._disk_style(_disk(), None) is None
