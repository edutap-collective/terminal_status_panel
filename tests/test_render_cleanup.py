"""The Objects row, the cleanup rules, and the Cleanup line under DOCKER INFOS."""

from rich.console import Console

from terminal_status_panel.config import CleanupThresholds, Config
from terminal_status_panel.model import (
    DockerDiskUsage,
    FilesystemUsage,
    ResourceUsage,
    SwarmInfo,
    SwarmNode,
)
from terminal_status_panel.render import panels
from terminal_status_panel.render.cleanup import cleanup_findings, disk_pressure

GIB = 2**30


def _text(renderable, width=220) -> str:
    console = Console(width=width, force_terminal=True, color_system="truecolor", record=True)
    console.print(renderable)
    return console.export_text()


def _disk(**kw) -> DockerDiskUsage:
    """A tidy node: nothing here should raise a finding."""
    defaults = dict(
        node="srv-01",
        root_dir="/var/lib/docker",
        used=5 * GIB,
        reclaimable=GIB // 10,
        images=4 * GIB,
        images_reclaimable=GIB // 10,
        volumes_total=4,
        volumes_unused=1,
        containers_total=8,
        containers_running=6,
        containers_stopped=0,
        containers_swarm_history=2,
        images_total=10,
        images_unused=2,
        images_dangling=1,
        networks=7,
    )
    defaults.update(kw)
    return DockerDiskUsage(**defaults)


def _fs(percent, mountpoint="/"):
    return FilesystemUsage(mountpoint=mountpoint, total=100, used=int(percent), percent=percent)


def _section(disk, resources=None, cfg=None) -> str:
    swarm = SwarmInfo(
        reachable=True,
        enabled=True,
        node_role="manager",
        node_count=1,
        nodes=[
            SwarmNode(
                "srv-01",
                reachable=True,
                role="manager",
                leader=True,
                state="ready",
                availability="active",
            )
        ],
        disk=disk,
    )
    return _text(panels.services_section(swarm, cfg or Config(), resources=resources))


def _row(out: str, label: str) -> str | None:
    return next((line for line in out.splitlines() if line.strip().startswith(label)), None)


def _severities(disk, resources=None, cleanup=None):
    cfg = Config(docker_cleanup=cleanup or CleanupThresholds())
    return {f.subject: f.severity for f in cleanup_findings(disk, resources, cfg)}


# --- the rules ---------------------------------------------------------------


def test_a_tidy_node_has_no_findings():
    assert _severities(_disk()) == {}


def test_unused_volumes_ten_times_the_used_ones_are_critical():
    """Measured on a production node, 2026-11-18: 2,175 unused volumes beside 3 in use."""
    assert _severities(_disk(volumes_total=2178, volumes_unused=2175)) == {"volumes": "critical"}


def test_more_unused_than_used_volumes_is_a_warning():
    """Measured on a production node, 2026-11-18: 16 unused beside 9 in use, and growing."""
    assert _severities(_disk(volumes_total=25, volumes_unused=16)) == {"volumes": "warning"}


def test_a_handful_of_unused_volumes_is_not_worth_a_word():
    """A fresh development machine with five leftover volumes and none in use."""
    assert _severities(_disk(volumes_total=5, volumes_unused=5)) == {}


def test_the_ratio_holds_with_no_volume_in_use():
    assert _severities(_disk(volumes_total=12, volumes_unused=12)) == {"volumes": "critical"}


def test_swarm_task_history_is_never_a_finding():
    """Swarm prunes its own task history; only the operator's leftovers count."""
    assert _severities(_disk(containers_stopped=0, containers_swarm_history=40)) == {}
    assert _severities(_disk(containers_stopped=12)) == {"containers": "warning"}


def test_dangling_images_are_a_warning():
    assert _severities(_disk(images_dangling=10)) == {"images": "warning"}


def test_many_unused_images_are_a_warning():
    assert _severities(_disk(images_total=40, images_unused=25)) == {"images": "warning"}


def test_unused_images_outnumbered_by_used_ones_are_not_a_finding():
    assert _severities(_disk(images_total=60, images_unused=25)) == {}


def test_a_full_docker_disk_with_reclaimable_bytes_follows_the_filesystem_thresholds():
    heap = dict(reclaimable=8 * GIB, images_reclaimable=8 * GIB)
    assert _severities(_disk(**heap), ResourceUsage(filesystems=[_fs(85.0)])) == {"disk": "warning"}
    assert _severities(_disk(**heap), ResourceUsage(filesystems=[_fs(93.0)])) == {
        "disk": "critical"
    }


def test_a_full_disk_with_nothing_to_reclaim_is_no_cleanup_finding():
    """Cleaning up would not help; the disk line's colour still says it is full."""
    assert _severities(_disk(reclaimable=0), ResourceUsage(filesystems=[_fs(95.0)])) == {}


def test_thresholds_come_from_the_config():
    cleanup = CleanupThresholds(unused_volumes=50)
    assert _severities(_disk(volumes_total=25, volumes_unused=16), cleanup=cleanup) == {}


def test_unknown_counts_raise_no_finding():
    """A count nobody took is not evidence of a mess."""
    disk = _disk(
        containers_stopped=None, images_dangling=None, images_unused=None, images_total=None
    )
    assert _severities(disk) == {}


def test_a_failed_reading_raises_no_finding():
    assert _severities(_disk(error="timeout", volumes_total=2178, volumes_unused=2175)) == {}


def test_disk_pressure_follows_the_filesystem_thresholds():
    cfg = Config()
    assert disk_pressure(_disk(), ResourceUsage(filesystems=[_fs(50.0)]), cfg.thresholds) is None
    assert disk_pressure(_disk(), ResourceUsage(filesystems=[_fs(85.0)]), cfg.thresholds) == (
        "warning"
    )
    assert disk_pressure(_disk(), ResourceUsage(filesystems=[_fs(95.0)]), cfg.thresholds) == (
        "critical"
    )


# --- the rendering -----------------------------------------------------------


def test_the_objects_row_carries_every_count():
    row = _row(_section(_disk()), "Objects")
    assert row is not None
    assert "6 running" in row
    assert "2 swarm history" in row
    assert "images 10 (2 unused, 1 dangling)" in row
    assert "volumes 4 (1 unused" in row
    assert "networks 7" in row


def test_the_objects_row_names_anonymous_volumes():
    row = _row(_section(_disk(volumes_anonymous=3)), "Objects")
    assert "3 anonymous" in row


def test_unknown_counts_are_left_out_of_the_objects_row():
    row = _row(_section(_disk(containers_total=None, networks=None)), "Objects")
    assert "containers" not in row
    assert "networks" not in row
    assert "images 10" in row


def test_the_disk_row_names_what_each_category_can_give_back():
    row = _row(_section(_disk(images_reclaimable=3 * GIB)), "Disk")
    assert "images 4.0 GB (↺ 3.0 GB)" in row


def test_no_cleanup_line_on_a_tidy_node():
    assert _row(_section(_disk()), "Cleanup") is None


def test_a_critical_finding_says_cleanup_is_overdue():
    row = _row(_section(_disk(volumes_total=2178, volumes_unused=2175)), "Cleanup")
    assert row is not None
    assert "overdue" in row
    assert "2175 unused volumes" in row
    assert "725×" in row


def test_a_warning_says_cleanup_is_due():
    row = _row(_section(_disk(containers_stopped=12)), "Cleanup")
    assert row is not None
    assert "due" in row and "overdue" not in row
    assert "12 stopped containers" in row


def test_the_worst_finding_leads_the_cleanup_line():
    disk = _disk(containers_stopped=12, volumes_total=2178, volumes_unused=2175)
    row = _row(_section(disk), "Cleanup")
    assert row.index("volumes") < row.index("containers")


def test_the_cleanup_line_renders_without_a_swarm():
    swarm = SwarmInfo(
        reachable=True, enabled=False, disk=_disk(volumes_total=2178, volumes_unused=2175)
    )
    out = _text(panels.services_section(swarm, Config()))
    assert _row(out, "Objects") is not None
    assert _row(out, "Cleanup") is not None


def test_a_failed_reading_renders_neither_objects_nor_cleanup():
    """The Disk line already says n/a and why; repeating it adds nothing."""
    out = _section(_disk(error="timeout"))
    assert _row(out, "Objects") is None
    assert _row(out, "Cleanup") is None


def test_unknown_volume_counts_raise_no_finding_and_are_left_out():
    disk = _disk(volumes_total=None, volumes_unused=None, volumes_anonymous=None)
    assert _severities(disk) == {}
    assert "volumes" not in _row(_section(disk), "Objects")


def test_a_known_total_with_unknown_unused_shows_only_the_total():
    row = _row(_section(_disk(volumes_total=6, volumes_unused=None)), "Objects")
    assert "volumes 6" in row
    assert "unused" not in row.split("volumes 6")[1].split("·")[0]


def test_disk_pressure_bands_are_inclusive_like_the_disk_bars():
    thresholds = Config().thresholds
    at_critical = ResourceUsage(filesystems=[_fs(90.0)])
    at_warning = ResourceUsage(filesystems=[_fs(80.0)])
    assert disk_pressure(_disk(), at_critical, thresholds) == "critical"
    assert disk_pressure(_disk(), at_warning, thresholds) == "warning"


def test_disk_pressure_follows_configured_thresholds():
    from terminal_status_panel.config import Thresholds

    thresholds = Thresholds(filesystem_warning=70.0, filesystem_critical=75.0)
    assert disk_pressure(_disk(), ResourceUsage(filesystems=[_fs(85.0)]), thresholds) == (
        "critical"
    )
