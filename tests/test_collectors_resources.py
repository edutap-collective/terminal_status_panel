from types import SimpleNamespace

import pytest

from terminal_status_panel.collectors import resources
from terminal_status_panel.collectors import resources as resources_collector
from terminal_status_panel.model import ResourceUsage


@pytest.fixture
def base_mocks(monkeypatch):
    monkeypatch.setattr(
        resources.psutil, "virtual_memory",
        lambda: SimpleNamespace(total=32_000_000_000, used=20_400_000_000, percent=64.0),
    )
    monkeypatch.setattr(
        resources.psutil, "swap_memory",
        lambda: SimpleNamespace(total=8_000_000_000, used=600_000_000, percent=8.0),
    )
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", lambda: (1.0, 0.7, 0.4))
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        resources.psutil, "cpu_percent",
        lambda interval=None, percpu=False: [10.0, 20.0, 30.0, 40.0] if percpu else 25.0,
    )


def test_memory_and_swap(base_mocks):
    res = resources.collect_resources()
    assert isinstance(res, ResourceUsage)
    assert res.mem_percent == 64.0
    assert res.swap_used == 600_000_000
    assert res.load_avg == (1.0, 0.7, 0.4)
    assert res.cpu_count == 4


def test_cpu_total_and_per_core(base_mocks):
    res = resources.collect_resources()
    assert res.cpu_per_core == [10.0, 20.0, 30.0, 40.0]
    assert res.cpu_percent == 25.0  # mean of per-core samples


def test_filesystem_filtering(base_mocks, monkeypatch):
    parts = [
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        SimpleNamespace(device="overlay", mountpoint="/var/lib/docker", fstype="overlay"),
    ]
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        resources.psutil, "disk_usage",
        lambda p: SimpleNamespace(total=230_000_000_000, used=210_000_000_000, percent=91.0),
    )
    res = resources.collect_resources()
    assert [fs.mountpoint for fs in res.filesystems] == ["/"]
    assert res.filesystems[0].percent == 91.0


def test_degrades_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(resources.psutil, "virtual_memory", boom)
    monkeypatch.setattr(resources.psutil, "swap_memory", boom)
    monkeypatch.setattr(resources.psutil, "disk_partitions", lambda all=False: [])
    monkeypatch.setattr(resources.psutil, "getloadavg", boom)
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(resources.psutil, "cpu_percent", boom)
    res = resources.collect_resources()
    assert res.mem_percent is None
    assert res.filesystems == []
    assert res.cpu_per_core == []
    assert res.cpu_percent is None


class _Part:
    def __init__(self, mountpoint, fstype="apfs", device="/dev/disk1"):
        self.mountpoint = mountpoint
        self.fstype = fstype
        self.device = device
        self.opts = "rw"


class _Usage:
    def __init__(self, total, used, percent):
        self.total = total
        self.used = used
        self.percent = percent


def _fake_disks(monkeypatch, partitions, usage_by_mount):
    monkeypatch.setattr(
        resources_collector.psutil, "disk_partitions", lambda all=False: partitions
    )
    monkeypatch.setattr(
        resources_collector.psutil, "disk_usage", lambda mp: usage_by_mount[mp]
    )


def test_darwin_root_adopts_the_data_volumes_figures(monkeypatch):
    """On APFS, / and /System/Volumes/Data are one container. Data tells the truth."""
    monkeypatch.setattr(resources_collector.platform, "system", lambda: "Darwin")
    _fake_disks(
        monkeypatch,
        [_Part("/"), _Part("/System/Volumes/Data")],
        {
            "/": _Usage(994_662_584_320, 12_562_452_480, 26.0),
            "/System/Volumes/Data": _Usage(994_662_584_320, 905_679_106_048, 96.0),
        },
    )

    result = resources_collector._collect_filesystems([])

    assert [fs.mountpoint for fs in result] == ["/"]
    assert result[0].percent == 96.0
    assert result[0].used == 905_679_106_048


def test_the_merge_does_nothing_when_the_data_volume_is_absent(monkeypatch):
    monkeypatch.setattr(resources_collector.platform, "system", lambda: "Darwin")
    _fake_disks(monkeypatch, [_Part("/")], {"/": _Usage(100, 50, 50.0)})

    result = resources_collector._collect_filesystems([])

    assert [fs.mountpoint for fs in result] == ["/"]
    assert result[0].percent == 50.0


def test_the_merge_does_not_run_off_darwin(monkeypatch):
    monkeypatch.setattr(resources_collector.platform, "system", lambda: "Linux")
    _fake_disks(
        monkeypatch,
        [_Part("/", fstype="ext4"), _Part("/System/Volumes/Data", fstype="ext4")],
        {
            "/": _Usage(100, 26, 26.0),
            "/System/Volumes/Data": _Usage(100, 96, 96.0),
        },
    )

    result = resources_collector._collect_filesystems([])

    assert [fs.mountpoint for fs in result] == ["/", "/System/Volumes/Data"]


def test_ignored_prefixes_are_dropped(monkeypatch):
    monkeypatch.setattr(resources_collector.platform, "system", lambda: "Linux")
    _fake_disks(
        monkeypatch,
        [
            _Part("/", fstype="ext4"),
            _Part("/Library/Developer/CoreSimulator/Volumes/iOS_23C54"),
            _Part("/Volumes/Data2"),
        ],
        {
            "/": _Usage(100, 50, 50.0),
            "/Library/Developer/CoreSimulator/Volumes/iOS_23C54": _Usage(10, 9, 97.0),
            "/Volumes/Data2": _Usage(200, 1, 1.0),
        },
    )

    result = resources_collector._collect_filesystems(
        ["/Library/Developer/CoreSimulator/"]
    )

    assert [fs.mountpoint for fs in result] == ["/", "/Volumes/Data2"]


def test_the_merge_runs_before_the_ignore_list(monkeypatch):
    """Filtering first would discard the Data volume before anyone read it."""
    monkeypatch.setattr(resources_collector.platform, "system", lambda: "Darwin")
    _fake_disks(
        monkeypatch,
        [_Part("/"), _Part("/System/Volumes/Data")],
        {
            "/": _Usage(100, 26, 26.0),
            "/System/Volumes/Data": _Usage(100, 96, 96.0),
        },
    )

    result = resources_collector._collect_filesystems(["/System/Volumes/"])

    assert [fs.mountpoint for fs in result] == ["/"]
    assert result[0].percent == 96.0
