from rich.console import Console

from terminal_status_panel.config import Config
from terminal_status_panel.model import (
    ClusterService,
    FilesystemUsage,
    HealthInfo,
    ResourceUsage,
    ServiceStatus,
    ServiceTask,
    SwarmInfo,
    SwarmNode,
    SystemInfo,
    UpdateInfo,
)
from terminal_status_panel.render import icons, panels


def _text(renderable, width=100) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def test_system_overview_shows_fields():
    info = SystemInfo(hostname="srv01", os_name="Debian", os_version="12",
                      kernel="6.1.0", uptime_seconds=90000, user="root",
                      ip_addresses=["10.0.0.5"])
    out = _text(panels.system_overview(info))
    assert "SYSTEM OVERVIEW" in out
    assert "srv01" in out
    assert "Debian" in out
    assert "10.0.0.5" in out


def test_system_overview_handles_none():
    out = _text(panels.system_overview(None))
    assert "not available" in out


def test_memory_panel_renders_bars():
    res = ResourceUsage(
        mem_total=32_000_000_000, mem_used=20_400_000_000, mem_percent=64.0,
        swap_total=8_000_000_000, swap_used=600_000_000, swap_percent=8.0,
    )
    out = _text(panels.memory_panel(res, Config()))
    assert "RAM" in out
    assert "SWAP" in out
    assert "64" in out   # percent shown
    assert "█" in out    # bar drawn


def test_load_panel_shows_per_core():
    res = ResourceUsage(
        load_avg=(1.0, 0.7, 0.4), cpu_count=4,
        cpu_percent=12.3, cpu_per_core=[8.1, 11.4, 6.2, 9.3],
    )
    out = _text(panels.load_panel(res, Config()))
    assert "SYSTEM LOAD" in out
    assert "Load Average" in out
    assert "per core" in out
    assert "Core 1" in out
    assert "Core 4" in out
    assert "12.3%" in out


def test_filesystem_panel_is_a_table():
    res = ResourceUsage(filesystems=[
        FilesystemUsage("/", 230_000_000_000, 210_000_000_000, 91.0),
        FilesystemUsage("/data", 500_000_000_000, 120_000_000_000, 24.0),
    ])
    out = _text(panels.filesystem_panel(res))
    assert "FILESYSTEM USAGE" in out
    assert "Size" in out
    assert "Use%" in out
    assert "/data" in out
    assert "91%" in out


def test_updates_panel_lists_counts():
    out = _text(panels.updates_panel(UpdateInfo(supported=True, available=12,
                                                security=5, standard=7)))
    assert "Available updates" in out
    assert "Security updates" in out
    assert "12" in out
    assert "5" in out


def test_updates_panel_unsupported():
    out = _text(panels.updates_panel(UpdateInfo(supported=False)))
    assert "n/a" in out.lower()


def test_services_section_merges_per_node_replicas():
    N1, N2, N3 = "lmzvd06-ccc-01", "lmzvd06-ccn-01", "lmzvd06-ccn-02"
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode(N1, reachable=True, role="manager", leader=True),
               SwarmNode(N2, reachable=True, role="worker"),
               SwarmNode(N3, reachable=False, role="worker", state="down")],
        services=[
            # One kafka service per node — must collapse to a single "kafka" row.
            ServiceStatus(f"kafka_kafka-{N1}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus(f"kafka_kafka-{N2}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N2, "running")]),
            ServiceStatus(f"kafka_kafka-{N3}", 1, 1, stack="kafka", description="Kafka broker",
                          tasks=[ServiceTask(N3, "failed")]),
            # PostgreSQL: per-node pg replicas + a distinct monitor service.
            ServiceStatus(f"PostgreSQL-18_pg-{N1}", 1, 1, stack="PostgreSQL-18",
                          description="PG", tasks=[ServiceTask(N1, "running")]),
            ServiceStatus(f"PostgreSQL-18_pg-{N2}", 1, 1, stack="PostgreSQL-18",
                          description="PG", tasks=[ServiceTask(N2, "running")]),
            ServiceStatus("PostgreSQL-18_pg-monitor", 1, 1, stack="PostgreSQL-18",
                          description="PG monitor", tasks=[ServiceTask(N3, "running")]),
            # traefik: two distinct services.
            ServiceStatus("traefik_sockproxy", 1, 1, stack="traefik",
                          description="socket proxy", tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("traefik_traefik", 3, 3, stack="traefik", description="ingress",
                          tasks=[ServiceTask(N1, "running"), ServiceTask(N2, "running"),
                                 ServiceTask(N3, "running")]),
            ServiceStatus("eduTAP_web", 1, 1, stack="eduTAP", description="eduTAP frontend",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("registry", 1, 1, description="Docker registry",
                          tasks=[ServiceTask(N2, "running")]),
            ServiceStatus("watchtower", 1, 1, description="Auto-update",
                          tasks=[ServiceTask(N3, "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "DOCKER INFOS" in out
    assert "Infrastruktur" in out and "Service" in out and "Container (ohne Stack)" in out
    assert "Description" in out
    assert "ccc-01" in out  # short node header

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
    # registry -> Infrastruktur, watchtower -> Container, eduTAP -> Service.
    assert "registry" in out and "Docker registry" in out
    assert "watchtower" in out
    assert "eduTAP" in out
    # Status emojis present (running ✅, failed 💀 for kafka on down node).
    assert "✅" in out and "💀" in out


def test_services_section_unreachable():
    out = _text(panels.services_section(SwarmInfo(reachable=False), Config()))
    assert "not reachable" in out.lower()


def _mixed_availability_swarm() -> SwarmInfo:
    """One active, one drained, one down node — no services."""
    return SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[
            SwarmNode("srv-01", reachable=True, role="manager", leader=True,
                      state="ready", availability="active"),
            SwarmNode("srv-02", reachable=True, role="worker",
                      state="ready", availability="drain"),
            SwarmNode("srv-03", reachable=False, role="worker",
                      state="down", availability="active"),
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
        reachable=True, enabled=True, node_role="manager", node_count=2,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active"),
               SwarmNode("srv-02", reachable=True, state="ready", availability="active")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "2 nodes  ·" in out
    assert "drain" not in out and "down" not in out


def test_node_with_empty_availability_falls_back_to_unavailable():
    # SwarmNode(..., availability="") is not operational, because
    # "" not in (None, "active") — this pins the same fallback _node_capacity
    # already applies, so both read "unavailable" instead of a blank label.
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
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
        reachable=True, enabled=True, node_role="manager", node_count=2,
        nodes=[SwarmNode(N1, reachable=True, state="ready", availability="active"),
               SwarmNode(N2, reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("kafka_kafka", 1, 1, stack="kafka", description="Broker",
                          tasks=[ServiceTask(N1, "running")]),
            # A UI living inside a real stack must leave that stack.
            ServiceStatus("kafka_kafbat-ui", 1, 1, stack="kafka", description="Kafka UI",
                          tasks=[ServiceTask(N2, "running")]),
            # A UI deployed as its own stack.
            ServiceStatus("cloudbeaver_cloudbeaver", 1, 1, stack="cloudbeaver",
                          description="SQL UI", tasks=[ServiceTask(N1, "running")]),
            # A UI running as a standalone container.
            ServiceStatus("mongo-express", 1, 1, description="Mongo UI",
                          tasks=[ServiceTask(N1, "running")]),
            ServiceStatus("eduTAP_web", 1, 1, stack="eduTAP", description="frontend",
                          tasks=[ServiceTask(N1, "running")]),
            # A real infrastructure stack that sorts alphabetically before
            # 'infra-uis' — the pseudo stack must still come first.
            ServiceStatus("elasticsearch_es", 1, 1, stack="elasticsearch",
                          description="Search", tasks=[ServiceTask(N1, "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)

    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastruktur"))
    uis_at = _line_index(out, lambda ln: ln.strip().startswith("infra-uis"))
    kafka_at = _line_index(out, lambda ln: ln.strip().startswith("kafka"))
    service_at = _line_index(out, lambda ln: ln.strip().startswith("Service"))
    container_at = _line_index(out, lambda ln: ln.strip().startswith("Container (ohne"))
    es_at = _line_index(out, lambda ln: ln.strip().startswith("elasticsearch"))

    # The pseudo stack heads the Infrastruktur block.
    assert infra_at < uis_at < kafka_at < service_at
    assert uis_at < es_at  # pseudo stack is hoisted, not sorted alphabetically
    # All three UI shapes ended up inside it.
    for ui in ("kafbat-ui", "cloudbeaver", "mongo-express"):
        assert uis_at < _line_index(out, lambda ln, ui=ui: ui in ln) < kafka_at
    # Stack prefixes are stripped on the sub-rows.
    assert "kafka_kafbat-ui" not in out
    assert "cloudbeaver_cloudbeaver" not in out
    # Unrelated services keep their block.
    assert service_at < _line_index(out, lambda ln: "eduTAP" in ln) < container_at
    # mongo-express must appear exactly once: under infra-uis, not left behind
    # as a row in the "Container (ohne Stack)" matrix too (_line_index above
    # only finds the FIRST match, so it would miss a stray leftover row).
    assert out.count("mongo-express") == 1


def test_single_infra_ui_keeps_its_own_name():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("mongo-express", 1, 1, description="Mongo UI",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    # Not collapsed to a single row labelled 'infra-uis' — the UI stays named.
    assert "infra-uis" in out
    assert "mongo-express" in out


def test_infra_ui_services_win_over_infrastructure_stacks():
    """A stack named after a UI service can still match an infrastructure key
    by substring — 'kafka-ui' (DEFAULT_INFRA_UI_SERVICES) contains 'kafka'
    (DEFAULT_INFRASTRUCTURE_STACKS), so is_infra("kafka-ui") is also true. The
    UI list is applied first and wins: the service is rendered as a sub-row of
    the 'infra-uis' pseudo stack instead of becoming its own top-level
    infrastructure stack."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("kafka-ui_kafka-ui", 1, 1, stack="kafka-ui",
                          description="Kafka UI", tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("mongodb_mongodb", 1, 1, stack="mongodb",
                          tasks=[ServiceTask("srv-01", "running")]),
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
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("kafka-ui_kafka-ui", 1, 1, stack="kafka-ui",
                          description="Kafka UI", tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("kafka-ui_agent", 1, 1, stack="kafka-ui",
                          description="Kafka UI sidecar", tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "kafka-ui/agent" in out
    # The UI row itself stays plain, not qualified as 'kafka-ui/kafka-ui'.
    lines = out.splitlines()
    ui_line = next(ln for ln in lines if ln.strip().startswith("kafka-ui") and
                   "kafka-ui/agent" not in ln)
    assert "kafka-ui/kafka-ui" not in ui_line


def test_no_infra_uis_row_without_matching_services():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka", 1, 1, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "infra-uis" not in out


def test_the_matrix_has_a_working_column():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("app_web", 3, 3, stack="app",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "Working" in out
    assert f"{icons.OK} 3/3" in out


def test_a_service_wanting_replicas_and_having_none_is_marked_dead():
    """Nine such rows render blank today — the outage is invisible."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("edutap_admin_backend", 0, 3, stack="edutap")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.DEAD} 0/3" in out


def test_a_service_scaled_to_zero_is_not_marked_dead():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("app_paused", 0, 0, stack="app")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.UNKNOWN} 0/0" in out
    assert f"{icons.DEAD} 0/0" not in out


def test_the_kafka_row_follows_the_cluster_verdict_not_the_replicas():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka-srv-01", 5, 5, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
    )
    health = HealthInfo(clusters_probed=True,
                        clusters=[ClusterService(kind="kafka", quorum_ok=False)])
    out = _text(panels.services_section(swarm, Config(), health), width=170)
    assert f"{icons.DEAD} 5/5" in out


def test_without_health_a_clustered_service_is_not_observable():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[ServiceStatus("kafka_kafka-srv-01", 5, 5, stack="kafka",
                                tasks=[ServiceTask("srv-01", "running")])],
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
    return SwarmInfo(reachable=True, enabled=True, node_role="manager", node_count=5,
                     nodes=nodes, services=services)


def test_a_global_service_counts_the_tasks_swarm_scheduled():
    """Swarm takes a global service's task off a drained node, so counting
    against every node would claim a degradation nobody measured — while
    ``docker service ls`` reads a contented 4/4."""
    traefik = ServiceStatus(
        "traefik_traefik", 4, None, stack="traefik",
        tasks=[ServiceTask(f"srv-0{i}", "running") for i in range(1, 5)],
    )
    out = _text(panels.services_section(
        _five_node_swarm_with_one_drained([traefik]), Config()), width=170)
    assert f"{icons.OK} 4/4" in out
    assert f"{icons.WARN} 4/5" not in out


def test_a_global_service_missing_a_task_is_still_degraded():
    traefik = ServiceStatus(
        "traefik_traefik", 3, None, stack="traefik",
        tasks=[ServiceTask(f"srv-0{i}", "running") for i in range(1, 4)]
             + [ServiceTask("srv-04", "failed")],
    )
    out = _text(panels.services_section(
        _five_node_swarm_with_one_drained([traefik]), Config()), width=170)
    assert f"{icons.WARN} 3/4" in out


def test_a_global_row_without_tasks_falls_back_to_the_reported_node_count():
    """``_node_map`` swallows a failed node listing, so an empty node list next
    to a non-zero node_count is reachable — and '/0' would be a lie."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=4, nodes=[],
        services=[ServiceStatus("traefik_traefik", 4, None, stack="traefik")],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert f"{icons.OK} 4/4" in out
    assert "4/0" not in out


def _tasks(*states):
    """One ServiceStatus holding tasks on node 'srv-01' with the given states."""
    return [ServiceStatus("svc", sum(s == "running" for s in states), len(states),
                          tasks=[ServiceTask("srv-01", s) for s in states])]


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


NODES = ["lmzvd06-ccc-01", "lmzvd06-ccn-01"]


def test_an_ordinal_suffix_is_stripped():
    assert (
        panels._base_service_name("edutap_production_heidi_connector_1", NODES)
        == "edutap_production_heidi_connector"
    )


def test_ordinal_stripping_survives_more_than_one_digit():
    assert panels._base_service_name("stack_worker_12", NODES) == "stack_worker"


def test_a_node_suffix_is_still_stripped():
    assert panels._base_service_name("kafka_kafka-lmzvd06-ccc-01", NODES) == "kafka_kafka"


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

    The stack also runs another service (as edutap_production does with
    thirteen, in production) so it renders as a stack header plus sub-rows
    rather than collapsing to a single stack-named row — that single-service
    collapse is a distinct, pre-existing case with its own tests."""
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=3,
        nodes=[SwarmNode(n, reachable=True, state="ready", availability="active")
               for n in ("srv-01", "srv-02", "srv-03")],
        services=[
            ServiceStatus(f"edutap_heidi_connector_{i}", 1, 1, stack="edutap",
                          tasks=[ServiceTask(f"srv-0{i}", "running")])
            for i in (1, 2, 3)
        ] + [
            ServiceStatus("edutap_web", 1, 1, stack="edutap",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    assert "heidi_connector" in out
    assert "heidi_connector_1" not in out
    assert f"{icons.OK} 3/3" in out
    matches = [ln for ln in out.splitlines() if ln.strip().startswith("heidi_connector")]
    assert len(matches) == 1


def test_bugsink_is_infrastructure():
    swarm = SwarmInfo(
        reachable=True, enabled=True, node_role="manager", node_count=1,
        nodes=[SwarmNode("srv-01", reachable=True, state="ready", availability="active")],
        services=[
            ServiceStatus("bugsink_bugsink", 1, 1, stack="bugsink",
                          description="Bugsink (Fehler-Tracker)",
                          tasks=[ServiceTask("srv-01", "running")]),
            ServiceStatus("app_web", 1, 1, stack="app",
                          tasks=[ServiceTask("srv-01", "running")]),
        ],
    )
    out = _text(panels.services_section(swarm, Config()), width=170)
    infra_at = _line_index(out, lambda ln: ln.strip().startswith("Infrastruktur"))
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
            SwarmNode(name=node_name, reachable=reachable,
                      state="ready" if reachable else "down", availability="active"),
        ],
    )


def _peer(name, ok, **kw):
    return PeerReachability(name=name, method="wireguard", ok=ok, **kw)


def test_down_node_without_handshake_points_at_the_network():
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", False, detail="never",
                     rx_bytes=0, tx_bytes=174080)],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg: no handshake" in output


def test_down_node_with_healthy_tunnel_points_at_docker():
    swarm = _swarm_with("node-c", reachable=False)
    health = HealthInfo(
        peers=[_peer("wg-node-c.example.net", True, detail="0:10",
                     rx_bytes=1024, tx_bytes=2048)],
        peers_probed=True,
    )
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg: ok" in output


def test_healthy_node_gets_no_annotation():
    """The line is crowded. The note appears only where it explains something."""
    swarm = _swarm_with("node-c", reachable=True)
    health = HealthInfo(peers=[_peer("wg-node-c.example.net", True, detail="0:10")],
                        peers_probed=True)
    output = _text(panels.services_section(swarm, Config(), health))
    assert "wg:" not in output


def test_down_node_without_matching_peer_is_not_annotated():
    """No peer data, no claim about the tunnel."""
    swarm = _swarm_with("node-c", reachable=False)
    output = _text(panels.services_section(swarm, Config(), HealthInfo(peers_probed=True)))
    assert "wg:" not in output
